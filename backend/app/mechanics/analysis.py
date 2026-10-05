"""分析编排：曲线 -> 弹性拟合 -> 区间敏感性 -> 屈服 -> 断后指标，
并汇总完整 provenance 与曲线点。
"""
from __future__ import annotations

from .curves import Channels, CurveError, SpecimenInfo, build_curve
from .elasticity import fit_modulus, interval_sensitivity
from .fracture import fracture_metrics
from .yield_ import proof_stress_offset
from ..schemas import (
    AnalysisResult,
    ComplianceCorrectionResult,
    CurvePoint,
    ExcludedPoint,
    MachineComplianceCorrection,
)
from ..units import compliance_to_m_per_n, stress_from_pa, StressUnit


def run_analysis(channels: Channels, specimen: SpecimenInfo,
                 strain_source: str, stress_unit: str,
                 strain_min: float | None, strain_max: float | None,
                 excluded: list[ExcludedPoint], run_id: int,
                 compliance: MachineComplianceCorrection | None = None) -> AnalysisResult:
    stress_unit_enum = StressUnit(stress_unit)
    source = strain_source
    compliance_si = None
    compliance_result = ComplianceCorrectionResult(
        enabled=False, applied=False,
        machine_displacement_rule="未启用机器柔度修正；使用原始通道生成材料曲线",
    )

    if compliance is not None and compliance.enabled:
        if compliance.coefficient is None or not compliance.unit:
            # schema 通常已拦截；这里保护直接调用内核的路径
            raise CurveError("机器柔度修正已启用，但必须同时提供校准系数和单位")
        compliance_si = compliance_to_m_per_n(compliance.coefficient, compliance.unit)
        compliance_result = ComplianceCorrectionResult(
            enabled=True,
            applied=False,
            coefficient=compliance.coefficient,
            unit=compliance.unit,
            coefficient_m_per_n=compliance_si,
            machine_displacement_rule="修正位移 = 相对夹具位移 - 柔度系数 × 相对载荷",
        )

    curve = build_curve(channels, specimen, source, compliance_m_per_n=compliance_si)
    compliance_result.applied = curve.compliance_applied
    compliance_result.nonphysical_indices = sorted(curve.nonphysical_reasons)
    compliance_result.nonphysical_reasons = {
        str(i): reason for i, reason in curve.nonphysical_reasons.items()
    }
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
            load_n=float(curve.engineering_stress[i] * curve.area0_m2),
            source=curve.basis_description,
            uncorrected_strain=(float(curve.uncorrected_strain[i])
                                if curve.uncorrected_strain is not None else None),
            physically_valid=bool(curve.physically_valid[i]) if curve.physically_valid is not None else True,
            nonphysical_reason=curve.nonphysical_reasons.get(i),
        )
        for i in range(len(curve.strain))
    ]

    provenance = {
        "run_id": run_id,
        "strain_source": source,
        "strain_basis": curve.basis_description,
        "strain_basis_m": curve.basis_m,
        "initial_area_m2": curve.area0_m2,
        "machine_compliance": compliance_result.model_dump(mode="json"),
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
        strain_source_used=source,
        stress_unit=stress_unit_enum.value,
        elastic_fit=fit,
        interval_sensitivity=sensitivity,
        yield_result=yld,
        fracture=frac,
        compliance_correction=compliance_result,
        curve=points,
        necking_index=curve.necking_index,
        warnings=curve.warnings,
        provenance=provenance,
    )
