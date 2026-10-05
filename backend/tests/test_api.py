"""端到端 API 测试：入库 -> 分析 -> 报告，含 422 与原始信号保留。"""
from __future__ import annotations


def test_demo_linear_elastic_full_flow(client):
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    run_id = r["run_id"]

    detail = client.get(f"/api/runs/{run_id}").json()
    kinds = {c["kind"] for c in detail["channels"]}
    assert kinds == {"load", "crosshead_displacement", "extensometer_displacement"}

    payload = {"run_id": run_id, "strain_source": "extensometer",
               "stress_unit": "MPa", "strain_min": 0.0005, "strain_max": 0.004,
               "excluded_points": []}
    res = client.post("/api/runs/analyze", json=payload).json()
    assert 198_000 < res["elastic_fit"]["modulus_in_output_unit"] < 202_000
    assert res["elastic_fit"]["r_squared"] > 0.9999
    assert res["yield_result"]["found"] is False
    assert res["yield_result"]["proof_stress"] is None
    assert res["provenance"]["strain_source"] == "extensometer"

    rep = client.post(f"/api/runs/{run_id}/report", json=payload).json()
    md = rep["markdown"]
    assert "弹性模量" in md and "0.2%" in md
    assert "未确定" in md and "区间影响" in md
    # 报告不允许只输出孤立数值：来源、方法、区间、点都必须出现
    assert "最小二乘" in md and "参与点" in md and "溯源" in md


def test_no_clear_yield_flags_unclear(client):
    r = client.post("/api/demo/synthetic/no_clear_yield").json()
    res = client.post("/api/runs/analyze", json={
        "run_id": r["run_id"], "strain_min": 0.0001, "strain_max": 0.0009,
    }).json()
    assert res["yield_result"]["found"] is True
    assert res["yield_result"]["yield_unclear"] is True


def test_missing_diameter_returns_422(client):
    r = client.post("/api/demo/synthetic/linear_elastic?omit_diameter=true").json()
    resp = client.post("/api/runs/analyze", json={
        "run_id": r["run_id"], "strain_min": 0.0005, "strain_max": 0.004,
    })
    assert resp.status_code == 422
    assert "初始直径" in resp.json()["detail"]


def test_mixed_channels_rejected_and_raw_signal_immutable(client):
    # 缺位移通道 -> 422
    s = client.post("/api/specimens", json={
        "code": "T1",
        "geometry": {"geometry": "round", "nominal_diameter_mm": 10.0,
                     "gauge_length_mm": 50.0},
    }).json()
    bad = client.post("/api/runs", json={
        "specimen_id": s["id"],
        "equipment": {"machine": "M", "extensometer_gauge_length_mm": 50.0},
        "channels": [
            {"kind": "load", "unit": "kN", "values": [0.0, 1.0, 2.0, 3.0, 4.0]},
        ],
    })
    assert bad.status_code == 422

    # 正常入库：kN/mm 单位转换 + 分析后原始值不变
    ok = client.post("/api/runs", json={
        "specimen_id": s["id"],
        "equipment": {"machine": "M", "extensometer_gauge_length_mm": 50.0},
        "channels": [
            {"kind": "load", "unit": "kN", "values": [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]},
            {"kind": "extensometer_displacement", "unit": "mm",
             "values": [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007]},
        ],
    })
    assert ok.status_code == 201
    run_id = ok.json()["run_id"]
    before = client.get(f"/api/runs/{run_id}").json()["raw_values"]["load"]

    client.post("/api/runs/analyze", json={
        "run_id": run_id, "strain_min": 0.0, "strain_max": 0.00014,
        "excluded_points": [{"index": 2, "reason": "测试排除"}],
    })
    after = client.get(f"/api/runs/{run_id}").json()["raw_values"]["load"]
    assert before == after == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]


def test_excluded_point_without_reason_rejected(client):
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    resp = client.post("/api/runs/analyze", json={
        "run_id": r["run_id"], "strain_min": 0.0005, "strain_max": 0.004,
        "excluded_points": [{"index": 10, "reason": ""}],
    })
    assert resp.status_code == 422


# ---- 机器柔度修正端到端 ----

def test_compliance_corrected_analysis_recovers_E_and_reports(client):
    """验收：合成已知柔度，夹具位移 + 正确校准值 -> E≈200 GPa 且可溯源。"""
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    run_id = r["run_id"]
    cal = r["machine_compliance"]

    # 未修正：夹具位移模量偏低
    raw = client.post("/api/runs/analyze", json={
        "run_id": run_id, "strain_source": "crosshead",
        "strain_min": 0.0005, "strain_max": 0.004,
    }).json()
    assert raw["elastic_fit"]["modulus_in_output_unit"] < 0.9 * 200_000
    assert raw["compliance_correction"] is None

    # 启用修正（mm/kN 单位）
    res = client.post("/api/runs/analyze", json={
        "run_id": run_id, "strain_source": "crosshead",
        "strain_min": 0.0005, "strain_max": 0.004,
        "machine_compliance": {"coefficient": cal["coefficient"], "unit": cal["unit"]},
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert 198_000 < body["elastic_fit"]["modulus_in_output_unit"] < 202_000
    cc = body["compliance_correction"]
    assert cc["coefficient"] == cal["coefficient"]
    assert cc["coefficient_si_m_per_n"] > 0
    assert cc["n_nonphysical_points"] == 0
    assert body["reference_curve"] is not None
    assert all(p["corrected"] for p in body["curve"])
    assert all(not p["corrected"] for p in body["reference_curve"])
    assert body["provenance"]["machine_compliance_correction"]["raw_signals_modified"] is False

    # 报告包含校准参数与修正公式
    rep = client.post(f"/api/runs/{run_id}/report", json={
        "run_id": run_id, "strain_source": "crosshead",
        "strain_min": 0.0005, "strain_max": 0.004,
        "machine_compliance": {"coefficient": cal["coefficient"], "unit": cal["unit"]},
    }).json()
    md = rep["markdown"]
    assert "机器柔度修正" in md and "δ_corr" in md
    assert cal["unit"] in md


def test_compliance_disabled_restores_original_and_raw_immutable(client):
    """验收：关闭修正恢复原结果，且原始信号不被任何分析回写。"""
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    run_id = r["run_id"]
    cal = r["machine_compliance"]
    payload = {"run_id": run_id, "strain_source": "crosshead",
               "strain_min": 0.0005, "strain_max": 0.004}
    before = client.get(f"/api/runs/{run_id}").json()
    # 先做一次修正分析
    client.post("/api/runs/analyze", json={
        **payload,
        "machine_compliance": {"coefficient": cal["coefficient"], "unit": cal["unit"]},
    })
    # 再关闭修正 -> 回到偏低的原始结果
    res = client.post("/api/runs/analyze", json=payload).json()
    assert res["compliance_correction"] is None
    assert res["elastic_fit"]["modulus_in_output_unit"] < 0.9 * 200_000
    after = client.get(f"/api/runs/{run_id}").json()
    assert before["raw_values"] == after["raw_values"]


def test_compliance_missing_unit_returns_422_with_reason_and_nothing_saved(client):
    """验收：缺少校准单位 -> 422 + 中文原因，且不产生任何分析记录。"""
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    run_id = r["run_id"]
    resp = client.post("/api/runs/analyze", json={
        "run_id": run_id, "strain_source": "crosshead",
        "strain_min": 0.0005, "strain_max": 0.004,
        "machine_compliance": {"coefficient": 0.006},
    })
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "校准单位" in detail

    # 未知单位同样拒绝
    bad_unit = client.post("/api/runs/analyze", json={
        "run_id": run_id, "strain_source": "crosshead",
        "strain_min": 0.0005, "strain_max": 0.004,
        "machine_compliance": {"coefficient": 0.006, "unit": "kN/mm"},
    })
    assert bad_unit.status_code == 422

    # 两次失败均未写入分析记录
    listing = client.get(f"/api/runs/{run_id}/analyses").json()
    assert listing["count"] == 0


def test_over_correction_negative_displacement_blocked_and_not_saved(client):
    """验收：过大柔度导致负修正位移 -> 422 说明原因，不保存伪结果。"""
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    run_id = r["run_id"]
    cal = r["machine_compliance"]
    resp = client.post("/api/runs/analyze", json={
        "run_id": run_id, "strain_source": "crosshead",
        "machine_compliance": {"coefficient": cal["coefficient"] * 3,
                               "unit": cal["unit"]},
    })
    assert resp.status_code == 422
    assert "非物理" in resp.json()["detail"]
    listing = client.get(f"/api/runs/{run_id}/analyses").json()
    assert listing["count"] == 0


def test_compliance_rejected_for_extensometer_source(client):
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    run_id = r["run_id"]
    cal = r["machine_compliance"]
    resp = client.post("/api/runs/analyze", json={
        "run_id": run_id, "strain_source": "extensometer",
        "strain_min": 0.0005, "strain_max": 0.004,
        "machine_compliance": {"coefficient": cal["coefficient"], "unit": cal["unit"]},
    })
    assert resp.status_code == 422
    assert "夹具位移" in resp.json()["detail"]
