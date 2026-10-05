"""信号通道 -> 工程/真实 应力应变曲线。

约束（对应需求）：
* 载荷、夹具位移、引伸计读数各自独立通道，绝不混为一列；机器柔度修正仅派生新曲线；
* 颈缩后真实应力/应变的简单换算失效，直接标记 null/False，不伪造；
* 所有几何量与标距的选择都进入 provenance，报告可溯源。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..units import length_to_m


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
    uncorrected_strain: np.ndarray | None = None
    physically_valid: np.ndarray | None = None
    nonphysical_reasons: dict[int, str] = field(default_factory=dict)
    compliance_m_per_n: float | None = None
    compliance_applied: bool = False
    warnings: list[str] = field(default_factory=list)


def build_curve(channels: Channels, specimen: SpecimenInfo,
                strain_source: str,
                compliance_m_per_n: float | None = None) -> StressStrainCurve:
    area0 = specimen.initial_area_m2()
    basis_m, basis_desc = specimen.strain_basis_m(strain_source)

    if strain_source == "extensometer":
        if channels.extensometer_m is None:
            raise CurveError("计算方案选择引伸计应变，但该试验没有引伸计通道")
        source_disp_all = channels.extensometer_m
    else:
        if channels.crosshead_m is None:
            raise CurveError("计算方案选择夹具位移应变，但该试验没有夹具位移通道")
        source_disp_all = channels.crosshead_m

    load_all = channels.load_n

    # 以位移首点为应变零点（预载可能非零，力不归零，应力基于绝对力）。
    # 先按原始夹具位移判断卸载/回退；机器柔度修正只生成派生信号，绝不回写 raw_signals。
    warnings: list[str] = []
    raw_peak_disp_idx = int(np.argmax(source_disp_all))
    if raw_peak_disp_idx != len(source_disp_all) - 1:
        warnings.append(
            f"检测到原始位移在第 {raw_peak_disp_idx} 点后回退（卸载或引伸计摘除），"
            "材料曲线仅保留单调加载段"
        )
    raw_slice = slice(0, raw_peak_disp_idx + 1)
    disp_raw_branch = source_disp_all[raw_slice]
    load_raw_branch = load_all[raw_slice]

    active_disp = disp_raw_branch
    active_load = load_raw_branch
    uncorrected_strain: np.ndarray | None = None
    physically_valid: np.ndarray | None = None
    nonphysical_reasons: dict[int, str] = {}
    compliance_applied = False

    if compliance_m_per_n is not None:
        if strain_source != "crosshead":
            raise CurveError("机器柔度修正只能用于夹具位移通道，不能用于引伸计信号")

        # 预载/位移零点同时归零：δ_corr = (D-D0) - C(F-F0)
        disp_change = disp_raw_branch - disp_raw_branch[0]
        load_change = load_raw_branch - load_raw_branch[0]
        corrected_disp = disp_change - compliance_m_per_n * load_change
        scale_tol = 1e-12 * max(float(np.max(np.abs(disp_change))), 1.0)
        force_tol = 1e-12 * max(float(np.max(np.abs(load_change))), 1.0)

        negative_idx = np.flatnonzero(corrected_disp < -scale_tol)
        if len(negative_idx):
            i = int(negative_idx[0])
            raise CurveError(
                f"第 {i} 点修正后位移为 {corrected_disp[i]:.6g} m（<0）："
                "校准柔度过大、载荷/位移单位不匹配或该点不满足单调加载，"
                "已阻止柔度修正拟合，未保存分析结果"
            )

        d_disp = np.diff(corrected_disp)
        d_load = np.diff(load_change)
        reverse = np.flatnonzero((d_load > force_tol) & (d_disp < -scale_tol))
        if len(reverse):
            i = int(reverse[0]) + 1
            raise CurveError(
                f"第 {i} 点载荷增加但修正后位移减少："
                "机器柔度校准值或单位可能错误，已阻止非物理修正曲线参与拟合"
            )

        active_disp = corrected_disp
        active_load = load_change + load_raw_branch[0]
        compliance_applied = True
        corrected_peak = int(np.argmax(corrected_disp))
        if corrected_peak != len(corrected_disp) - 1:
            warnings.append(
                f"扣除机器柔度后，位移在第 {corrected_peak} 点达到峰值；"
                "仅使用此前的单调修正段参与材料曲线计算"
            )
            active_slice = slice(0, corrected_peak + 1)
            active_disp = corrected_disp[active_slice]
            active_load = load_raw_branch[active_slice]
        else:
            active_slice = slice(0, len(corrected_disp))

        # 原始夹具应变只作为对比曲线，不改变原始通道，也不参与修正后的拟合。
        uncorrected_full = disp_change / basis_m
        uncorrected_strain = uncorrected_full[active_slice]
        basis_desc = (
            f"平行段/试样长度近似的夹具位移，已逐点扣除机器变形 "
            f"F·C（C={compliance_m_per_n:.6g} m/N）"
        )
    else:
        active_slice = slice(0, len(disp_raw_branch))

    strain = (active_disp - active_disp[0]) / basis_m
    stress = active_load / area0
    physically_valid = np.ones(len(strain), dtype=bool)

    # 颈缩起点 = 最大工程应力（最大载荷）点；之后简单换算失效
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
    if necking_index < n - 1:
        warnings.append(
            f"第 {necking_index} 点（载荷峰值/UTS）后发生颈缩，"
            "真实应力-应变仅在均匀塑性变形段有效，颈缩后标记为缺失"
        )

    return StressStrainCurve(
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
        uncorrected_strain=uncorrected_strain,
        physically_valid=physically_valid,
        nonphysical_reasons=nonphysical_reasons,
        compliance_m_per_n=compliance_m_per_n if compliance_applied else None,
        compliance_applied=compliance_applied,
        warnings=warnings,
    )
