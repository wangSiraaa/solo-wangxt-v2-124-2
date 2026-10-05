"""分析编排：曲线 -> 弹性拟合 -> 区间敏感性 -> 屈服 -> 断后指标，
并汇总完整 provenance 与曲线点。

机器柔度修正为可选步骤：启用时主曲线为“按载荷逐点扣除 F·C”的修正曲线，
同时输出未修正的原始夹具位移对照曲线；原始信号永不回写。
"""
from __future__ import annotations

from .compliance import MachineCompliance
from .curves import (
    Channels,
    SpecimenInfo,
    StressStrainCurve,
    build_corrected_curve,
    build_curve,
    build_raw_reference_curve,
)
from .elasticity import fit_modulus, interval_sensitivity
from .fracture import fracture_metrics
from .yield_ import proof_stress_offset
from ..schemas import (
    AnalysisResult,
    ComplianceCorrectionInfo,
    CurvePoint,
    ExcludedPoint,
)
from ..units import stress_from_pa, StressUnit


def _curve_points(curve: StressStrainCurve, load_n: np.ndarray,
                  stress_unit: StressUnit) -> list[CurvePoint]:
    valid_mask = curve.valid_mask()
    reasons = curve.point_invalid_reasons
    points: list[CurvePoint] = []
    for i in range(len(curve.strain)):
        points.append(CurvePoint(
            index=i,
            strain=float(curve.strain[i]),
            engineering_stress=float(
                stress_from_pa(curve.engineering_stress[i], stress_unit)),
            true_stress=(float(stress_from_pa(curve.true_stress[i], stress_unit))
                         if curve.true_valid[i] else None),
            true_stress_valid=bool(curve.true_valid[i]),
            load_n=float(load_n[i]),
            source=curve.basis_description,
            corrected=curve.is_corrected,
            physically_valid=bool(valid_mask[i]),
            invalid_reason=(reasons[i] if curve.is_corrected else None),
            machine_deformation_m=(float(curve.machine_deformation_m[i])
                                   if curve.machine_deformation_m is not None else None),
        ))
    return points


def run_analysis(channels: Channels, specimen: SpecimenInfo,
                 strain_source: str, stress_unit: str,
                 strain_min: float | None, strain_max: float | None,
                 excluded: list[ExcludedPoint], run_id: int,
                 machine_compliance: MachineCompliance | None = None) -> AnalysisResult:
    stress_unit_enum = StressUnit(stress_unit)

    compliance_info: ComplianceCorrectionInfo | None = None
    reference_points: list[CurvePoint] | None = None

    if machine_compliance is not None:
        if strain_source != "crosshead":
            # 柔度是“载荷 -> 夹具位移”通道的机器变形，扣到引伸计上没有物理意义
            from .curves import CurveError
            raise CurveError(
                "机器柔度修正只能作用于夹具位移通道：当前应变来源为引伸计，"
                "其读数不含机器变形，按引伸计分析时请关闭修正"
            )
        curve = build_corrected_curve(channels, specimen, machine_compliance)
        # 未修正的原始夹具位移曲线仅用于对照显示（独立计算，原始信号不变）
        ref_curve = build_raw_reference_curve(channels, specimen)
        reference_points = _curve_points(
            ref_curve, channels.load_n[: len(ref_curve.strain)], stress_unit_enum)
        invalid_idx = [i for i, ok in enumerate(curve.valid_mask()) if not ok]
        compliance_info = ComplianceCorrectionInfo(
            coefficient=machine_compliance.coefficient,
            unit=machine_compliance.unit,
            coefficient_si_m_per_n=curve.compliance_si_m_per_n,
            correction_formula=(
                "δ_corrected[i] = δ_crosshead[i] − F[i]·C，"
                f"C = {machine_compliance.coefficient:g} {machine_compliance.unit}"
                f" = {curve.compliance_si_m_per_n:.6g} m/N；"
                "ε = (δ_corrected − δ_corrected[0]) / Lc"
            ),
            n_nonphysical_points=len(invalid_idx),
            nonphysical_indices=invalid_idx,
            nonphysical_reasons=[
                curve.point_invalid_reasons[i] or "非物理点" for i in invalid_idx
            ],
        )
        strain_source_used = "crosshead"
    else:
        curve = build_curve(channels, specimen, strain_source)
        strain_source_used = strain_source

    fit = fit_modulus(curve, strain_min, strain_max, excluded, stress_unit)
    sensitivity = interval_sensitivity(curve, fit, stress_unit)
    yld = proof_stress_offset(curve, fit, stress_unit)
    frac = fracture_metrics(curve, specimen, stress_unit)

    points = _curve_points(curve, channels.load_n[: len(curve.strain)], stress_unit_enum)

    provenance = {
        "run_id": run_id,
        "strain_source": strain_source_used,
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
    if machine_compliance is not None:
        provenance["machine_compliance_correction"] = {
            "enabled": True,
            "coefficient": machine_compliance.coefficient,
            "unit": machine_compliance.unit,
            "coefficient_si_m_per_n": curve.compliance_si_m_per_n,
            "formula": "δ_corr[i] = δ_cross[i] − F[i]·C",
            "n_nonphysical_points": compliance_info.n_nonphysical_points,
            "nonphysical_indices": compliance_info.nonphysical_indices,
            "raw_signals_modified": False,
            "note": "修正仅作用于分析副本；raw_signals 与未修正对照曲线均保留",
        }
    else:
        provenance["machine_compliance_correction"] = {"enabled": False}

    return AnalysisResult(
        run_id=run_id,
        strain_source_used=strain_source_used,
        stress_unit=stress_unit_enum.value,
        elastic_fit=fit,
        interval_sensitivity=sensitivity,
        yield_result=yld,
        fracture=frac,
        curve=points,
        necking_index=curve.necking_index,
        warnings=curve.warnings,
        provenance=provenance,
        compliance_correction=compliance_info,
        reference_curve=reference_points,
    )
