"""抗拉强度、断后伸长率 A、断面收缩率 Z 等断后指标。

缺失尺寸时指标为 None 并在 missing_inputs 中显式列出，
绝不使用面积=1 之类的隐式假设顶替。
"""
from __future__ import annotations

import numpy as np

from .curves import SpecimenInfo, StressStrainCurve
from ..schemas import FractureResult
from ..units import stress_from_pa, StressUnit


def fracture_metrics(curve: StressStrainCurve, specimen: SpecimenInfo,
                     stress_unit: str = "MPa") -> FractureResult:
    stress_unit_enum = StressUnit(stress_unit)
    valid = curve.valid_mask()
    stress_series = curve.engineering_stress
    strain_series = curve.strain
    # 修正曲线：非物理点（负修正位移/回退）不参与 Rm 与断裂应变
    if curve.point_valid is not None:
        stress_series = np.where(valid, curve.engineering_stress, -np.inf)
    uts_idx = int(np.argmax(stress_series))
    uts_pa = float(curve.engineering_stress[uts_idx])
    valid_idx = np.where(valid)[0]
    last_valid = int(valid_idx[-1])
    last_strain = float(curve.strain[last_valid])
    fracture_strain = last_strain if last_strain > 0 else None
    if curve.point_valid is not None and last_valid < len(curve.strain) - 1:
        # 末端存在被截断的非物理点：记录到 provenance 之外不额外造数
        fracture_strain = last_strain if last_strain > 0 else None

    missing: list[str] = []
    elongation_pct = None
    if specimen.final_gauge_length_mm is None:
        missing.append("final_gauge_length_mm（断后标距 Lu）：无法计算断后伸长率 A")
    else:
        l0_mm = specimen.gauge_length_mm
        if l0_mm is None:
            missing.append("gauge_length_mm（初始标距 L0）：无法计算断后伸长率 A")
        else:
            elongation_pct = (specimen.final_gauge_length_mm - l0_mm) / l0_mm * 100.0

    reduction_pct = None
    if specimen.final_diameter_mm is None:
        missing.append("final_diameter_mm（断后直径 du）：无法计算断面收缩率 Z")
    elif specimen.geometry != "round":
        missing.append("当前仅圆截面支持由断后直径计算 Z，矩形截面需录入断后截面积")
    else:
        try:
            a0 = specimen.initial_area_m2()
            au = specimen.final_area_m2()
            reduction_pct = (a0 - au) / a0 * 100.0
        except Exception as exc:  # 尺寸缺失已在 area 方法中报错
            missing.append(str(exc))

    return FractureResult(
        engineering_fracture_strain=fracture_strain,
        elongation_percent=elongation_pct,
        reduction_of_area_percent=reduction_pct,
        ultimate_tensile_strength=float(stress_from_pa(uts_pa, stress_unit_enum)),
        stress_unit=stress_unit_enum.value,
        strain_at_uts=float(curve.strain[uts_idx]),
        data_complete=len(missing) == 0,
        missing_inputs=missing,
    )
