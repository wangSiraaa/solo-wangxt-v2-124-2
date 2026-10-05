"""计算内核单元测试：三个核对案例 + 颈缩 + 排除点原因 + 机器柔度修正。"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.mechanics.analysis import run_analysis
from app.mechanics.compliance import (
    MachineCompliance,
    apply_compliance_correction,
    parse_compliance,
)
from app.mechanics.curves import Channels, CurveError, SpecimenInfo, build_curve
from app.mechanics.elasticity import fit_modulus
from app.mechanics.synthetic import (
    case_clear_yield,
    case_linear_elastic,
    case_no_clear_yield,
    synthetic_machine_compliance,
)
from app.schemas import ExcludedPoint


ROUND_SPEC = SpecimenInfo(
    geometry="round", nominal_diameter_mm=10.0,
    gauge_length_mm=50.0, parallel_length_mm=80.0,
    extensometer_gauge_length_mm=50.0,
    final_diameter_mm=6.0, final_gauge_length_mm=58.0,
)


def _channels(data) -> Channels:
    return Channels(
        load_n=data.load_n,
        crosshead_m=data.crosshead_m,
        extensometer_m=data.extensometer_m,
    )


def test_case_a_linear_elastic_recovers_E_and_reports_no_yield():
    """合成线弹性：模量必须正确恢复，0.2% 偏移法不得给出屈服值。"""
    data = case_linear_elastic(E_pa=200e9, eps_end=0.005)
    result = run_analysis(_channels(data), ROUND_SPEC, "extensometer", "MPa",
                          0.0005, 0.004, [], run_id=1)
    E_mpa = result.elastic_fit.modulus_in_output_unit
    assert E_mpa == pytest.approx(200_000, rel=1e-3)
    assert result.elastic_fit.r_squared > 0.9999
    assert result.yield_result.found is False
    assert "0.2% 偏移" in result.yield_result.reason
    assert result.yield_result.proof_stress is None
    # 线弹性无颈缩峰值（末端即最大），真实应力在整段有效
    assert all(p.true_stress_valid for p in result.curve)


def test_case_a_crosshead_channel_differs_from_extensometer():
    """夹具位移含机器柔度，与引伸计读数不是一列，混用会导致模量偏低。"""
    data = case_linear_elastic(E_pa=200e9)
    assert not np.allclose(data.crosshead_m, data.extensometer_m)

    ext = run_analysis(_channels(data), ROUND_SPEC, "extensometer", "MPa",
                       0.0005, 0.004, [], run_id=1)
    cross = run_analysis(_channels(data), ROUND_SPEC, "crosshead", "MPa",
                         0.0005, 0.004, [], run_id=2)
    assert ext.elastic_fit.modulus_in_output_unit == pytest.approx(200_000, rel=1e-3)
    # 夹具位移更大（含柔度）-> 算得模量系统性偏低
    assert cross.elastic_fit.modulus_in_output_unit < 0.9 * 200_000
    assert "柔度" in cross.provenance["strain_basis"]


def test_case_b_no_clear_yield_flags_unclear():
    """无清晰屈服：仍可得到条件 Rp0.2，但必须标记 yield_unclear。"""
    data = case_no_clear_yield()
    result = run_analysis(_channels(data), ROUND_SPEC, "extensometer", "MPa",
                          0.0001, 0.0009, [], run_id=3)
    assert result.yield_result.found is True
    assert result.yield_result.yield_unclear is True
    assert "无明显屈服平台" in result.yield_result.reason
    assert result.yield_result.offset_strain == pytest.approx(0.002)


def test_clear_yield_reference_case_full_pipeline():
    """双折线参考案例：E、Rp0.2≈400MPa、A、Z、颈缩后 true 应力缺失。"""
    data = case_clear_yield()
    result = run_analysis(_channels(data), ROUND_SPEC, "extensometer", "MPa",
                          0.0005, 0.0018, [], run_id=4)
    assert result.elastic_fit.modulus_in_output_unit == pytest.approx(200_000, rel=2e-3)
    assert result.yield_result.found is True
    assert result.yield_result.yield_unclear is False
    assert result.yield_result.proof_stress == pytest.approx(400, rel=0.03)
    f = result.fracture
    assert f.elongation_percent == pytest.approx(16.0)
    assert f.reduction_of_area_percent == pytest.approx(
        (10.0**2 - 6.0**2) / 10.0**2 * 100)
    # 颈缩后真实应力必须为 None，不得给出换算值
    neck = result.necking_index
    assert neck is not None
    assert result.curve[neck].true_stress_valid is True
    assert result.curve[-1].true_stress is None
    assert result.curve[-1].true_stress_valid is False
    assert any("颈缩" in w for w in result.warnings)


def test_missing_diameter_raises_explicit_error():
    """尺寸缺失：应力无法计算，明确报错而非隐式假设面积。"""
    data = case_linear_elastic()
    spec = SpecimenInfo(geometry="round", nominal_diameter_mm=None,
                        extensometer_gauge_length_mm=50.0)
    with pytest.raises(CurveError, match="初始直径"):
        build_curve(_channels(data), spec, "extensometer")


def test_missing_final_measurements_leave_metrics_null():
    """断后尺寸缺失：A/Z 为 None 且 missing_inputs 说明原因。"""
    data = case_clear_yield()
    spec = SpecimenInfo(
        geometry="round", nominal_diameter_mm=10.0,
        gauge_length_mm=50.0, parallel_length_mm=80.0,
        extensometer_gauge_length_mm=50.0,
        final_diameter_mm=None, final_gauge_length_mm=None,
    )
    result = run_analysis(_channels(data), spec, "extensometer", "MPa",
                          0.0005, 0.0018, [], run_id=5)
    assert result.fracture.elongation_percent is None
    assert result.fracture.reduction_of_area_percent is None
    joined = " ".join(result.fracture.missing_inputs)
    assert "断后标距" in joined and "断后直径" in joined
    assert result.fracture.data_complete is False


def test_excluded_points_need_reason_and_affect_fit():
    """人工排除点带原因；注入离群点后排除可恢复模量，残差结构可见。"""
    data = case_linear_elastic()
    ch = _channels(data)
    ch.load_n[100] *= 1.25  # 单点毛刺
    bad = run_analysis(ch, ROUND_SPEC, "extensometer", "MPa",
                       0.0005, 0.004, [], run_id=6)
    assert bad.elastic_fit.r_squared < 0.999

    good = run_analysis(ch, ROUND_SPEC, "extensometer", "MPa",
                        0.0005, 0.004,
                        [ExcludedPoint(index=100, reason="载荷毛刺：该点载荷突增25%，疑干扰")],
                        run_id=7)
    assert good.elastic_fit.r_squared > 0.9999
    assert good.elastic_fit.n_excluded == 1
    assert good.elastic_fit.excluded[0].reason
    assert good.provenance["excluded_points"][0]["index"] == 100


def test_interval_sensitivity_reported():
    data = case_no_clear_yield()
    result = run_analysis(_channels(data), ROUND_SPEC, "extensometer", "MPa",
                          0.0001, 0.0009, [], run_id=8)
    labels = [s.label for s in result.interval_sensitivity]
    assert "收缩 25%" in labels and "上限上移 25%" in labels
    for s in result.interval_sensitivity:
        assert s.n_points >= 3


# ---- 机器柔度修正 ----

SYN_AREA = math.pi * (10.0e-3) ** 2 / 4


def test_compliance_correction_recovers_reference_modulus():
    """验收：用注入柔度的校准值逐点扣除后，E 恢复到合成参考 200 GPa。"""
    data = case_linear_elastic(E_pa=200e9)
    c_si, c_mm_kn, unit = synthetic_machine_compliance(SYN_AREA)
    assert data.compliance_m_per_n == pytest.approx(c_si)

    uncorrected = run_analysis(_channels(data), ROUND_SPEC, "crosshead", "MPa",
                               0.0005, 0.004, [], run_id=20)
    assert uncorrected.elastic_fit.modulus_in_output_unit < 0.9 * 200_000

    corrected = run_analysis(
        _channels(data), ROUND_SPEC, "crosshead", "MPa",
        None, None, [], run_id=21,
        machine_compliance=MachineCompliance(c_mm_kn, unit),
    )
    E = corrected.elastic_fit.modulus_in_output_unit
    assert E == pytest.approx(200_000, rel=1e-3)
    assert corrected.elastic_fit.r_squared > 0.9999
    # 所有点物理有效；独立修正曲线 + 未修正对照曲线同时给出
    assert corrected.compliance_correction is not None
    assert corrected.compliance_correction.n_nonphysical_points == 0
    assert corrected.reference_curve is not None
    assert all(p.corrected and p.physically_valid for p in corrected.curve)
    assert all(not p.corrected for p in corrected.reference_curve)
    assert corrected.provenance["machine_compliance_correction"]["enabled"] is True
    assert corrected.provenance["machine_compliance_correction"]["raw_signals_modified"] is False


def test_compliance_unit_conversions_equivalent():
    """m/N、mm/N、mm/kN 三种校准单位等价换算，模量结果一致。"""
    data = case_linear_elastic(E_pa=200e9)
    c_si, c_mm_kn, _ = synthetic_machine_compliance(SYN_AREA)
    results = []
    for coef, unit in [(c_si, "m/N"), (c_si * 1e3, "mm/N"), (c_mm_kn, "mm/kN")]:
        r = run_analysis(_channels(data), ROUND_SPEC, "crosshead", "MPa",
                         None, None, [], run_id=30,
                         machine_compliance=MachineCompliance(coef, unit))
        results.append(r.elastic_fit.modulus_in_output_unit)
    for e in results[1:]:
        assert e == pytest.approx(results[0], rel=1e-9)
    assert results[0] == pytest.approx(200_000, rel=1e-3)


def test_correction_off_restores_original_result():
    """验收：关闭修正时字段为 None，且结果与原夹具位移分析完全一致。"""
    data = case_linear_elastic(E_pa=200e9)
    r = run_analysis(_channels(data), ROUND_SPEC, "crosshead", "MPa",
                     0.0005, 0.004, [], run_id=40)
    assert r.compliance_correction is None
    assert r.reference_curve is None
    assert r.provenance["machine_compliance_correction"] == {"enabled": False}
    assert r.elastic_fit.modulus_in_output_unit < 0.9 * 200_000
    assert all(not p.corrected and p.physically_valid for p in r.curve)


def test_raw_signals_unchanged_after_correction():
    """修正只发生在分析副本：Channels 与修正信息中的机器变形可以解释差值。"""
    data = case_linear_elastic(E_pa=200e9)
    ch = _channels(data)
    original = ch.crosshead_m.copy()
    c_si, c_mm_kn, unit = synthetic_machine_compliance(SYN_AREA)
    corrected = run_analysis(ch, ROUND_SPEC, "crosshead", "MPa",
                             None, None, [], run_id=50,
                             machine_compliance=MachineCompliance(c_mm_kn, unit))
    assert np.array_equal(ch.crosshead_m, original)
    # 修正位移 + 机器变形 = 原始夹具位移（首点对齐）
    p0 = corrected.curve[0]
    for p in corrected.curve[::40]:
        expected = (p.strain * 80e-3) + (p.machine_deformation_m - p0.machine_deformation_m)
        actual = original[p.index] - original[0]
        assert expected == pytest.approx(actual, rel=1e-9)


def test_nonphysical_points_marked_and_block_fit():
    """验收：载荷毛刺导致修正位移回退 -> 标记非物理并阻止错误拟合。"""
    data = case_linear_elastic(E_pa=200e9)
    c_si, c_mm_kn, unit = synthetic_machine_compliance(SYN_AREA)
    ch = _channels(data)
    ch.load_n[120:123] *= 1.8  # 载荷尖峰：F·C 扣除后修正位移回退

    # 选择覆盖毛刺点的修正后应变窗口 -> 拒绝拟合，原因包含索引
    with pytest.raises(CurveError, match="非物理点"):
        run_analysis(ch, ROUND_SPEC, "crosshead", "MPa",
                     0.0, 0.0002, [], run_id=60,
                     machine_compliance=MachineCompliance(c_mm_kn, unit))

    # 跳过毛刺点之后的窗口仍可拟合出正确模量（非物理点仅存在于该段）
    good = run_analysis(ch, ROUND_SPEC, "crosshead", "MPa",
                        0.0005, 0.004, [], run_id=61,
                        machine_compliance=MachineCompliance(c_mm_kn, unit))
    assert good.elastic_fit.modulus_in_output_unit == pytest.approx(200_000, rel=1e-3)
    bad_points = [p for p in good.curve if not p.physically_valid]
    assert {p.index for p in bad_points} == {120, 121, 122}
    assert all("回退" in (p.invalid_reason or "") for p in bad_points)
    assert any("非物理点" in w for w in good.warnings)


def test_excessive_compliance_all_nonphysical_rejected():
    """柔度系数过大：所有修正位移为负 -> 直接拒绝，不生成伪曲线。"""
    data = case_linear_elastic(E_pa=200e9)
    _, c_mm_kn, unit = synthetic_machine_compliance(SYN_AREA)
    with pytest.raises(CurveError, match="无法构建修正应变曲线"):
        run_analysis(_channels(data), ROUND_SPEC, "crosshead", "MPa",
                     None, None, [], run_id=70,
                     machine_compliance=MachineCompliance(c_mm_kn * 3, unit))


def test_missing_or_unknown_compliance_unit_rejected():
    """验收：缺少校准单位或单位未知时给出原因，不保存伪结果。"""
    with pytest.raises(ValueError, match="校准单位"):
        parse_compliance(0.006, None)
    with pytest.raises(ValueError, match="校准单位"):
        parse_compliance(0.006, "")
    with pytest.raises(ValueError, match="柔度系数"):
        parse_compliance(None, "mm/kN")
    with pytest.raises(ValueError, match="未知的机器柔度"):
        MachineCompliance(1.0, "kN/mm").to_si()
    with pytest.raises(ValueError, match="未知的机器柔度"):
        apply_compliance_correction(
            np.array([0.0, 1e-5]), np.array([0.0, 1e3]),
            MachineCompliance(1.0, "kN/mm"),
        )


def test_compliance_with_extensometer_source_rejected():
    """柔度修正是夹具通道的机器变形，与引伸计组合没有物理意义。"""
    data = case_linear_elastic(E_pa=200e9)
    _, c_mm_kn, unit = synthetic_machine_compliance(SYN_AREA)
    with pytest.raises(CurveError, match="只能作用于夹具位移"):
        run_analysis(_channels(data), ROUND_SPEC, "extensometer", "MPa",
                     0.0005, 0.004, [], run_id=80,
                     machine_compliance=MachineCompliance(c_mm_kn, unit))


def test_corrected_clear_yield_full_pipeline():
    """明确屈服案例：修正后曲线参与屈服/强度计算，Rp0.2 仍≈400MPa。"""
    data = case_clear_yield()
    c_si, c_mm_kn, unit = synthetic_machine_compliance(SYN_AREA)
    result = run_analysis(_channels(data), ROUND_SPEC, "crosshead", "MPa",
                          0.0005, 0.0018, [], run_id=90,
                          machine_compliance=MachineCompliance(c_mm_kn, unit))
    assert result.elastic_fit.modulus_in_output_unit == pytest.approx(200_000, rel=2e-3)
    assert result.yield_result.found is True
    assert result.yield_result.proof_stress == pytest.approx(400, rel=0.03)
    assert result.compliance_correction.n_nonphysical_points == 0
