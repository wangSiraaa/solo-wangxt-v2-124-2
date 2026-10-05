"""端到端 API 测试：入库 -> 分析 -> 报告，含 422 与原始信号保留。"""
from __future__ import annotations

from app.mechanics.synthetic import case_linear_elastic


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


def test_crosshead_compliance_correction_full_api(client):
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    run_id = r["run_id"]
    c_mm_per_kn = case_linear_elastic().machine_compliance_m_per_n * 1e6

    raw_before = client.get(f"/api/runs/{run_id}").json()["raw_values"]["crosshead_displacement"]
    payload = {
        "run_id": run_id, "strain_source": "crosshead",
        "stress_unit": "MPa", "strain_min": 0.0005, "strain_max": 0.004,
        "excluded_points": [],
        "machine_compliance": {
            "enabled": True, "coefficient": c_mm_per_kn, "unit": "mm/kN",
        },
    }
    res = client.post("/api/runs/analyze", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert 198_000 < body["elastic_fit"]["modulus_in_output_unit"] < 202_000
    assert body["compliance_correction"]["applied"] is True
    assert body["curve"][1]["uncorrected_strain"] is not None

    raw_after = client.get(f"/api/runs/{run_id}").json()["raw_values"]["crosshead_displacement"]
    assert raw_before == raw_after

    report = client.post(f"/api/runs/{run_id}/report", json=payload).json()
    assert "机器柔度修正" in report["markdown"]
    assert "mm/kN" in report["markdown"]


def test_missing_compliance_unit_returns_reason_and_does_not_analyze(client):
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    resp = client.post("/api/runs/analyze", json={
        "run_id": r["run_id"], "strain_source": "crosshead",
        "strain_min": 0.0005, "strain_max": 0.004,
        "machine_compliance": {"enabled": True, "coefficient": 6.1, "unit": None},
    })
    assert resp.status_code == 422
    detail = str(resp.json()["detail"])
    assert "校准单位" in detail


def test_negative_corrected_displacement_returns_reason_and_no_persistence(client):
    r = client.post("/api/demo/synthetic/linear_elastic").json()
    c_mm_per_kn = case_linear_elastic().machine_compliance_m_per_n * 1e6
    resp = client.post("/api/runs/analyze", json={
        "run_id": r["run_id"], "strain_source": "crosshead",
        "strain_min": 0.0005, "strain_max": 0.004,
        "machine_compliance": {
            "enabled": True, "coefficient": 2.5 * c_mm_per_kn, "unit": "mm/kN",
        },
    })
    assert resp.status_code == 422
    assert "修正后位移" in resp.json()["detail"]
    # 错误在创建 Analysis 前抛出，因此不应能取到编号 1 的伪分析结果
    assert client.get("/api/analyses/1").status_code == 404

    # 失败后仍可正常分析，证明请求没有污染该试验
    ok = client.post("/api/runs/analyze", json={
        "run_id": r["run_id"], "strain_source": "extensometer",
        "strain_min": 0.0005, "strain_max": 0.004,
    })
    assert ok.status_code == 200


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
