"""计算内核单元测试：三个核对案例 + 颈缩 + 排除点原因。"""
from __future__ import annotations

import numpy as np
import pytest

from app.mechanics.analysis import run_analysis
from app.mechanics.curves import Channels, CurveError, SpecimenInfo, build_curve
from app.mechanics.elasticity import fit_modulus
from app.mechanics.synthetic import (
    case_clear_yield,
    case_linear_elastic,
    case_no_clear_yield,
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
