"""信号通道 -> 工程/真实 应力应变曲线。

约束（对应需求）：
* 载荷、夹具位移、引伸计读数各自独立通道，绝不混为一列；
* 颈缩后真实应力/应变的简单换算失效，直接标记 null/False，不伪造；
* 所有几何量与标距的选择都进入 provenance，报告可溯源。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..units import length_to_m
from .compliance import CorrectedDisplacement, MachineCompliance
from .compliance import apply_compliance_correction


class CurveError(ValueError):
    """曲线构建阶段的可向用户报告的错误（如尺寸缺失）。"""


@dataclass
class SpecimenInfo:
    geometry: str  # "round" | "rectangular"
    nominal_diameter_mm: float | None = None
    nominal_width_mm: float | None = None
    nominal_thickness_mm: float | None = None
    gauge_length_mm: float | None = None          # 试样标距 L0
    parallel_length_mm: float | None = None       # 平行段长度 Lc
    extensometer_gauge_length_mm: float | None = None  # 引伸计标距 Le
    final_diameter_mm: float | None = None
    final_gauge_length_mm: float | None = None

    def initial_area_m2(self) -> float:
        if self.geometry == "round":
            if self.nominal_diameter_mm is None:
                raise CurveError(
                    "缺少初始直径 d0：圆截面试样必须提供 nominal_diameter_mm，无法计算应力"
                )
            d = length_to_m(self.nominal_diameter_mm, "mm")
            return math.pi * d**2 / 4.0
        if self.nominal_width_mm is None or self.nominal_thickness_mm is None:
            raise CurveError(
                "缺少初始宽度 b0 或厚度 t0：矩形截面试样必须提供 nominal_width_mm 与 "
                "nominal_thickness_mm，无法计算应力"
            )
        b = length_to_m(self.nominal_width_mm, "mm")
        t = length_to_m(self.nominal_thickness_mm, "mm")
        return b * t

    def final_area_m2(self) -> float:
        if self.final_diameter_mm is None:
            raise CurveError("缺少断后直径 du，无法计算断面收缩率 Z")
        d = length_to_m(self.final_diameter_mm, "mm")
        return math.pi * d**2 / 4.0

    def strain_basis_m(self, source: str) -> tuple[float, str]:
        """返回 (标距, 说明)。引伸计优先 Le，夹具位移优先 Lc。"""
        if source == "extensometer":
            if self.extensometer_gauge_length_mm is not None:
                return length_to_m(self.extensometer_gauge_length_mm, "mm"), "引伸计标距 Le（设备参数）"
            if self.gauge_length_mm is not None:
                return length_to_m(self.gauge_length_mm, "mm"), "试样标距 L0（未提供引伸计标距，回退）"
            raise CurveError("缺少标距：无法将引伸计位移换算为应变")
        # 夹具位移：不是材料应变，只能用平行段长度近似
        if self.parallel_length_mm is not None:
            return length_to_m(self.parallel_length_mm, "mm"), "平行段长度 Lc（夹具位移近似，含机器柔度）"
        if self.gauge_length_mm is not None:
            return length_to_m(self.gauge_length_mm, "mm"), "试样标距 L0（夹具位移近似，含机器柔度）"
        raise CurveError("缺少标距：无法将夹具位移换算为应变")

    def corrected_strain_basis_m(self) -> tuple[float, str]:
        """夹具位移经机器柔度修正后的应变标距（仍为近似材料应变）。"""
        if self.parallel_length_mm is not None:
            return length_to_m(self.parallel_length_mm, "mm"), \
                "平行段长度 Lc（夹具位移已按载荷逐点扣除机器柔度 F·C）"
        if self.gauge_length_mm is not None:
            return length_to_m(self.gauge_length_mm, "mm"), \
                "试样标距 L0（夹具位移已按载荷逐点扣除机器柔度 F·C）"
        raise CurveError("缺少标距：无法将修正后夹具位移换算为应变")


@dataclass
class Channels:
    load_n: np.ndarray
    crosshead_m: np.ndarray | None = None
    extensometer_m: np.ndarray | None = None

    def __post_init__(self) -> None:
        n = len(self.load_n)
        if n < 5:
            raise CurveError("信号点数过少（<5），无法拟合")
        for name, arr in (("crosshead_m", self.crosshead_m),
                          ("extensometer_m", self.extensometer_m)):
            if arr is not None and len(arr) != n:
                raise CurveError(f"通道长度不一致：load={n}, {name}={len(arr)}")

    @classmethod
    def from_raw(cls, raw: dict[str, list[float]]) -> "Channels":
        """raw: kind -> SI 值列表（N / m，单位换算在 ingestion 层完成）。"""
        if "load" not in raw:
            raise CurveError("缺少 load 通道")
        cross = raw.get("crosshead_displacement")
        ext = raw.get("extensometer_displacement")
        if cross is None and ext is None:
            raise CurveError("至少需要一个位移通道（夹具位移或引伸计），二者不可混用")
        return cls(
            load_n=np.asarray(raw["load"], dtype=float),
            crosshead_m=np.asarray(cross, dtype=float) if cross is not None else None,
            extensometer_m=np.asarray(ext, dtype=float) if ext is not None else None,
        )


@dataclass
class StressStrainCurve:
    strain: np.ndarray                 # 工程应变（按所选来源）
    engineering_stress: np.ndarray     # Pa
    true_stress: np.ndarray            # Pa，颈缩后为 nan
    true_strain: np.ndarray            # 颈缩后为 nan
    true_valid: np.ndarray             # bool 掩码
    necking_index: int | None
    uts_index: int
    strain_source: str
    basis_description: str
    area0_m2: float
    basis_m: float
    warnings: list[str] = field(default_factory=list)
    # 机器柔度修正（仅修正曲线非 None）；point_valid 为 None 时视为全部有效
    compliance_applied: MachineCompliance | None = None
    compliance_si_m_per_n: float | None = None
    machine_deformation_m: np.ndarray | None = None
    point_valid: np.ndarray | None = None
    point_invalid_reasons: list[str | None] | None = None
    truncated_unloading: bool = False  # 原始曲线是否因位移回退被截断

    @property
    def is_corrected(self) -> bool:
        return self.compliance_applied is not None

    def valid_mask(self) -> np.ndarray:
        """逐点物理有效掩码；未做修正时全部为 True。"""
        if self.point_valid is None:
            return np.ones(len(self.strain), dtype=bool)
        return self.point_valid


def _assemble_curve(load: np.ndarray, disp: np.ndarray, area0: float,
                    basis_m: float, basis_desc: str, strain_source: str,
                    truncate_unloading: bool,
                    warnings: list[str],
                    uts_mask: np.ndarray | None = None
                    ) -> tuple[StressStrainCurve, int]:
    """由“已选定的位移通道”组装应力应变曲线。

    返回 (曲线, 截断长度)；调用方据此对齐其他全长数组（如机器变形）。
    uts_mask 非 None 时（修正曲线），UTS/颈缩索引只在物理有效点中选取。
    """
    # 以位移首点为应变零点（预载可能非零，力不归零，应力基于绝对力）
    strain = (disp - disp[0]) / basis_m
    stress = load / area0

    # 单调化保护：位移回退（卸载段）不参与材料曲线，给出警告并截断到最大应变点之后
    peak_disp_idx = int(np.argmax(disp))
    truncated = False
    if truncate_unloading and peak_disp_idx != len(disp) - 1:
        warnings.append(
            f"检测到位移在第 {peak_disp_idx} 点后回退（卸载或引伸计摘除），"
            "材料曲线仅保留单调加载段"
        )
        strain = strain[: peak_disp_idx + 1]
        stress = stress[: peak_disp_idx + 1]
        if uts_mask is not None:
            uts_mask = uts_mask[: peak_disp_idx + 1]
        truncated = True

    # 颈缩起点 = 最大工程应力（最大载荷）点；之后简单换算失效。
    # 修正曲线上的非物理点（载荷毛刺造成的假峰）不允许成为 UTS。
    if uts_mask is not None:
        uts_index = int(np.argmax(np.where(uts_mask, stress, -np.inf)))
    else:
        uts_index = int(np.argmax(stress))
    necking_index = uts_index
    n = len(stress)
    true_stress = np.full(n, np.nan)
    true_strain = np.full(n, np.nan)
    valid = np.zeros(n, dtype=bool)
    up_to = necking_index + 1
    true_stress[:up_to] = stress[:up_to] * (1.0 + strain[:up_to])
    true_strain[:up_to] = np.log(1.0 + strain[:up_to])
    valid[:up_to] = True
    if uts_mask is not None:
        # 修正曲线上的非物理点同样不给真实应力换算值
        valid &= uts_mask
    if necking_index < n - 1:
        warnings.append(
            f"第 {necking_index} 点（载荷峰值/UTS）后发生颈缩，"
            "真实应力-应变仅在均匀塑性变形段有效，颈缩后标记为缺失"
        )

    curve = StressStrainCurve(
        strain=strain,
        engineering_stress=stress,
        true_stress=true_stress,
        true_strain=true_strain,
        true_valid=valid,
        necking_index=necking_index,
        uts_index=uts_index,
        strain_source=strain_source,
        basis_description=basis_desc,
        area0_m2=area0,
        basis_m=basis_m,
        warnings=warnings,
        truncated_unloading=truncated,
    )
    return curve, len(strain)


def build_curve(channels: Channels, specimen: SpecimenInfo,
                strain_source: str) -> StressStrainCurve:
    area0 = specimen.initial_area_m2()
    basis_m, basis_desc = specimen.strain_basis_m(strain_source)

    if strain_source == "extensometer":
        if channels.extensometer_m is None:
            raise CurveError("计算方案选择引伸计应变，但该试验没有引伸计通道")
        disp = channels.extensometer_m
    else:
        if channels.crosshead_m is None:
            raise CurveError("计算方案选择夹具位移应变，但该试验没有夹具位移通道")
        disp = channels.crosshead_m

    warnings: list[str] = []
    curve, _ = _assemble_curve(
        channels.load_n, disp, area0, basis_m, basis_desc,
        strain_source, truncate_unloading=True, warnings=warnings,
    )
    return curve


def build_raw_reference_curve(channels: Channels, specimen: SpecimenInfo) -> StressStrainCurve:
    """柔度修正启用时，同时构建未经修正的夹具位移曲线（仅供对照显示）。

    不做卸载截断，保留全部采样点，以便与修正曲线逐点对照。
    """
    if channels.crosshead_m is None:
        raise CurveError("该试验没有夹具位移通道，无法提供修正前对照曲线")
    area0 = specimen.initial_area_m2()
    basis_m, basis_desc = specimen.strain_basis_m("crosshead")
    curve, _ = _assemble_curve(
        channels.load_n, channels.crosshead_m, area0, basis_m, basis_desc,
        "crosshead", truncate_unloading=False, warnings=[],
    )
    return curve


def build_corrected_curve(channels: Channels, specimen: SpecimenInfo,
                          compliance: MachineCompliance) -> StressStrainCurve:
    """按载荷逐点扣除机器变形 F·C，生成独立的修正应变曲线。

    修正只发生在本函数内：channels.crosshead_m 与数据库原始信号不变。
    非物理点不截断、不删除，而是在 point_valid/point_invalid_reasons 中
    逐点标记，供拟合阶段阻止错误使用、供前端显示原因。
    """
    if channels.crosshead_m is None:
        raise CurveError("计算方案选择机器柔度修正，但该试验没有夹具位移通道")
    area0 = specimen.initial_area_m2()
    basis_m, basis_desc = specimen.corrected_strain_basis_m()

    correction = apply_compliance_correction(
        channels.crosshead_m, channels.load_n, compliance
    )

    warnings: list[str] = []
    # 不做卸载截断：回退点本身就是需要暴露的非物理/卸载信号
    curve, used_len = _assemble_curve(
        channels.load_n, correction.relative_corrected_m, area0, basis_m,
        basis_desc, "crosshead_corrected",
        truncate_unloading=False, warnings=warnings,
        uts_mask=correction.valid,
    )
    curve.compliance_applied = compliance
    curve.compliance_si_m_per_n = correction.compliance_si
    curve.machine_deformation_m = correction.machine_deformation_m[:used_len].copy()
    curve.point_valid = correction.valid[:used_len].copy()
    curve.point_invalid_reasons = list(correction.invalid_reasons[:used_len])

    if correction.n_invalid:
        invalid_idx = np.where(~curve.point_valid)[0]
        preview = ", ".join(
            f"#{i}（{curve.point_invalid_reasons[i]}）" for i in invalid_idx[:3]
        )
        more = "……" if len(invalid_idx) > 3 else ""
        warnings.append(
            f"机器柔度修正后存在 {len(invalid_idx)} 个非物理点（索引 {preview}{more}），"
            "已逐点标记；这些点不参与弹性拟合，若落在所选弹性区间内将拒绝分析。"
            "请核对柔度系数与单位是否与校准证书一致"
        )
    if int(curve.point_valid.sum()) < 5:
        n_bad = int((~curve.point_valid).sum())
        raise CurveError(
            f"机器柔度修正后 {n_bad} 个采样点为非物理（修正位移为负或回退），"
            f"物理有效点仅剩 {int(curve.point_valid.sum())} 个，无法构建修正应变曲线："
            "柔度系数或校准单位很可能错误，拒绝生成伪结果"
        )
    return curve
