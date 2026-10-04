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
    warnings: list[str] = field(default_factory=list)


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

    # 以位移首点为应变零点（预载可能非零，力不归零，应力基于绝对力）
    load = channels.load_n
    strain = (disp - disp[0]) / basis_m
    stress = load / area0

    # 单调化保护：位移回退（卸载段）不参与材料曲线，给出警告并截断到最大应变点之后
    warnings: list[str] = []
    peak_disp_idx = int(np.argmax(disp))
    if peak_disp_idx != len(disp) - 1:
        warnings.append(
            f"检测到位移在第 {peak_disp_idx} 点后回退（卸载或引伸计摘除），"
            "材料曲线仅保留单调加载段"
        )
        strain = strain[: peak_disp_idx + 1]
        stress = stress[: peak_disp_idx + 1]

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
        warnings=warnings,
    )
