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
