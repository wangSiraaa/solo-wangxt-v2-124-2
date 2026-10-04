"""分析编排：曲线 -> 弹性拟合 -> 区间敏感性 -> 屈服 -> 断后指标，
并汇总完整 provenance 与曲线点。
"""
from __future__ import annotations

from .curves import Channels, SpecimenInfo, build_curve
from .elasticity import fit_modulus, interval_sensitivity
from .fracture import fracture_metrics
from .yield_ import proof_stress_offset
from ..schemas import (
    AnalysisResult,
    CurvePoint,
    ExcludedPoint,
)
from ..units import stress_from_pa, StressUnit


def run_analysis(channels: Channels, specimen: SpecimenInfo,
                 strain_source: str, stress_unit: str,
                 strain_min: float | None, strain_max: float | None,
                 excluded: list[ExcludedPoint], run_id: int) -> AnalysisResult:
    stress_unit_enum = StressUnit(stress_unit)
    curve = build_curve(channels, specimen, strain_source)
    fit = fit_modulus(curve, strain_min, strain_max, excluded, stress_unit)
    sensitivity = interval_sensitivity(curve, fit, stress_unit)
    yld = proof_stress_offset(curve, fit, stress_unit)
    frac = fracture_metrics(curve, specimen, stress_unit)

    points = [
        CurvePoint(
            index=i,
            strain=float(curve.strain[i]),
            engineering_stress=float(stress_from_pa(curve.engineering_stress[i], stress_unit_enum)),
            true_stress=(float(stress_from_pa(curve.true_stress[i], stress_unit_enum))
                         if curve.true_valid[i] else None),
            true_stress_valid=bool(curve.true_valid[i]),
            load_n=float(channels.load_n[i]),
            source=curve.basis_description,
        )
        for i in range(len(curve.strain))
    ]

    provenance = {
        "run_id": run_id,
        "strain_source": strain_source,
        "strain_basis": curve.basis_description,
        "strain_basis_m": curve.basis_m,
        "initial_area_m2": curve.area0_m2,
        "stress_unit": stress_unit_enum.value,
        "elastic_window": [fit.strain_min, fit.strain_max],
        "fit_method": "普通最小二乘 sigma=E*eps+b（应力单位 Pa，应变 mm/mm）",
        "excluded_points": [p.model_dump() for p in excluded],
        "yield_method": yld.method,
        "offset_strain": yld.offset_strain,
        "necking_rule": "颈缩起点取最大工程应力点（载荷峰值），其后真实应力简单换算失效",
        "curve_points": len(points),
    }

    return AnalysisResult(
        run_id=run_id,
        strain_source_used=strain_source,
        stress_unit=stress_unit_enum.value,
        elastic_fit=fit,
        interval_sensitivity=sensitivity,
        yield_result=yld,
        fracture=frac,
        curve=points,
        necking_index=curve.necking_index,
        warnings=curve.warnings,
        provenance=provenance,
    )
