"""HTTP 路由。"""
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..mechanics.analysis import run_analysis
from ..mechanics.compliance import parse_compliance
from ..mechanics.report import render_markdown
from ..mechanics.synthetic import (
    case_clear_yield,
    case_linear_elastic,
    case_no_clear_yield,
    synthetic_machine_compliance,
)
from ..models import Analysis, RawSignal, Report, Specimen, TestRun
from ..schemas import (
    AnalysisResult,
    FitRequest,
    ReportOut,
    SpecimenCreate,
    SpecimenOut,
    TestRunCreate,
)
from ..mechanics.curves import CurveError

router = APIRouter(prefix="/api")


# ---- 试样 ----

@router.post("/specimens", response_model=SpecimenOut, status_code=201)
def create_specimen(payload: SpecimenCreate, db: Session = Depends(get_db)) -> Specimen:
    if db.scalar(select(Specimen).where(Specimen.code == payload.code)):
        raise HTTPException(409, f"试样编号 {payload.code} 已存在")
    g = payload.geometry
    s = Specimen(
        code=payload.code, material=payload.material,
        geometry=g.geometry,
        nominal_diameter_mm=g.nominal_diameter_mm,
        nominal_width_mm=g.nominal_width_mm,
        nominal_thickness_mm=g.nominal_thickness_mm,
        gauge_length_mm=g.gauge_length_mm,
        parallel_length_mm=g.parallel_length_mm,
        final_diameter_mm=payload.final_diameter_mm,
        final_gauge_length_mm=payload.final_gauge_length_mm,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@router.get("/specimens", response_model=list[SpecimenOut])
def list_specimens(db: Session = Depends(get_db)) -> list[Specimen]:
    return list(db.scalars(select(Specimen).order_by(Specimen.id)).all())


# ---- 试验记录与原始信号 ----

@router.post("/runs", status_code=201)
def create_run(payload: TestRunCreate, db: Session = Depends(get_db)) -> dict:
    specimen = db.get(Specimen, payload.specimen_id)
    if specimen is None:
        raise HTTPException(404, "试样不存在")
    kinds = [c.kind for c in payload.channels]
    if "load" not in kinds:
        raise HTTPException(422, "必须包含 load 通道")
    if not any(k in kinds for k in ("crosshead_displacement", "extensometer_displacement")):
        raise HTTPException(422, "必须包含夹具位移或引伸计位移通道，且二者分列")
    lengths = {len(c.values) for c in payload.channels}
    if len(lengths) != 1:
        raise HTTPException(422, f"各通道采样点数不一致: {sorted(lengths)}")

    run = TestRun(
        specimen_id=specimen.id,
        equipment=payload.equipment.model_dump(exclude_none=True),
        calc_plan={"strain_source": payload.strain_source,
                   "stress_unit": payload.stress_unit,
                   "displacement_unit_for_curve": payload.displacement_unit_for_curve},
    )
    db.add(run)
    db.flush()
    for ch in payload.channels:
        db.add(RawSignal(
            run_id=run.id, kind=ch.kind, unit=ch.unit,
            point_count=len(ch.values), values=ch.values,
            si_values=services.convert_channel_to_si(ch.kind, ch.values, ch.unit),
        ))
    db.commit()
    return {"run_id": run.id, "specimen_id": specimen.id, "points": lengths.pop()}


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)) -> dict:
    run = services.get_run_or_none(db, run_id)
    if run is None:
        raise HTTPException(404, "试验记录不存在")
    return {
        "run_id": run.id,
        "specimen_code": run.specimen.code,
        "equipment": run.equipment,
        "calc_plan": run.calc_plan,
        "channels": [
            {"kind": c.kind, "unit": c.unit, "point_count": c.point_count}
            for c in run.channels
        ],
        "raw_values": {c.kind: c.values for c in run.channels},
    }


# ---- 分析 ----

def _do_analysis(db: Session, req: FitRequest) -> tuple[Analysis, AnalysisResult]:
    run = services.get_run_or_none(db, req.run_id)
    if run is None:
        raise HTTPException(404, "试验记录不存在")
    try:
        # 校准参数校验：系数/单位不成对或单位未知时直接给出中文原因，不保存伪结果
        try:
            compliance = parse_compliance(
                req.machine_compliance.coefficient if req.machine_compliance else None,
                req.machine_compliance.unit if req.machine_compliance else None,
            )
        except ValueError as exc:
            raise CurveError(str(exc)) from exc
        channels, info = services.channels_from_run(run)
        result = run_analysis(
            channels=channels,
            specimen=info,
            strain_source=req.strain_source or run.calc_plan.get("strain_source", "extensometer"),
            stress_unit=req.stress_unit,
            strain_min=req.strain_min,
            strain_max=req.strain_max,
            excluded=req.excluded_points,
            run_id=run.id,
            machine_compliance=compliance,
        )
    except CurveError as exc:
        #尺寸缺失、通道缺失、区间点数不足、柔度非物理等：明确 422 与原因，不返回伪造数值
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    params = {
        "strain_source": result.strain_source_used,
        "stress_unit": req.stress_unit,
        "strain_min": req.strain_min,
        "strain_max": req.strain_max,
        "excluded_points": [p.model_dump() for p in req.excluded_points],
        "machine_compliance": (req.machine_compliance.model_dump()
                               if req.machine_compliance is not None else None),
    }
    analysis = services.persist_analysis(db, run.id, params, result.model_dump(mode="json"))
    return analysis, result


@router.post("/runs/analyze", response_model=AnalysisResult)
def analyze(req: FitRequest, db: Session = Depends(get_db)) -> AnalysisResult:
    analysis, result = _do_analysis(db, req)
    db.commit()
    return result


@router.post("/runs/{run_id}/report", response_model=ReportOut)
def build_report(run_id: int, req: FitRequest, db: Session = Depends(get_db)) -> ReportOut:
    req.run_id = run_id
    analysis, result = _do_analysis(db, req)
    run = analysis.run
    md = render_markdown(result, run.specimen.code, run.equipment)
    report = services.persist_report(
        db, analysis.id,
        title=f"{run.specimen.code} 拉伸试验报告", markdown=md,
    )
    db.commit()
    db.refresh(report)
    return ReportOut(
        report_id=report.id, run_id=run_id, title=report.title,
        markdown=report.markdown,
        generated_at=report.generated_at.isoformat(),
    )


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int, db: Session = Depends(get_db)) -> dict:
    a = db.get(Analysis, analysis_id)
    if a is None:
        raise HTTPException(404, "分析记录不存在")
    return {"analysis_id": a.id, "run_id": a.run_id, "params": a.params,
            "result": a.result, "is_latest": a.is_latest}


@router.get("/runs/{run_id}/analyses")
def list_run_analyses(run_id: int, db: Session = Depends(get_db)) -> dict:
    """列出某次试验的全部分析记录（用于核对被拒绝的分析确实未入库）。"""
    run = services.get_run_or_none(db, run_id)
    if run is None:
        raise HTTPException(404, "试验记录不存在")
    rows = db.scalars(
        select(Analysis).where(Analysis.run_id == run_id).order_by(Analysis.id)
    ).all()
    return {"run_id": run_id, "count": len(rows),
            "analysis_ids": [a.id for a in rows]}


@router.get("/reports/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)) -> dict:
    r = db.get(Report, report_id)
    if r is None:
        raise HTTPException(404, "报告不存在")
    return {"report_id": r.id, "analysis_id": r.analysis_id,
            "title": r.title, "markdown": r.markdown,
            "generated_at": r.generated_at.isoformat()}


# ---- 合成案例演示/核对 ----

@router.post("/demo/synthetic/{case_name}", status_code=201)
def demo_synthetic(case_name: str, with_final_measurements: bool = True,
                   omit_diameter: bool = False, db: Session = Depends(get_db)) -> dict:
    """一键生成合成案例并入库：linear_elastic | no_clear_yield | clear_yield。

    omit_diameter=true 制造“尺寸缺失”案例（应力计算应 422）。
    """
    generators = {
        "linear_elastic": case_linear_elastic,
        "no_clear_yield": case_no_clear_yield,
        "clear_yield": case_clear_yield,
    }
    if case_name not in generators:
        raise HTTPException(404, f"未知案例 {case_name}")
    run_data = generators[case_name]()
    area_m2 = math.pi * (10.0e-3) ** 2 / 4
    c_si, c_mm_kn, c_unit = synthetic_machine_compliance(area_m2)

    g = dict(geometry="round",
             nominal_diameter_mm=None if omit_diameter else 10.0,
             nominal_width_mm=None, nominal_thickness_mm=None,
             gauge_length_mm=50.0, parallel_length_mm=80.0)
    s = Specimen(
        code=f"SYN-{case_name}", material="synthetic",
        final_diameter_mm=(6.0 if with_final_measurements else None),
        final_gauge_length_mm=(58.0 if with_final_measurements else None),
        **g,
    )
    if existing := db.scalar(select(Specimen).where(Specimen.code == s.code)):
        db.delete(existing)
        db.flush()
    db.add(s)
    db.flush()

    run = TestRun(
        specimen_id=s.id,
        equipment={"machine": "SYNTHETIC", "load_cell_capacity_n": 100000,
                   "extensometer_gauge_length_mm": 50.0, "sampling_rate_hz": 100,
                   # 校准得到的机器柔度（合成案例随数据提供）
                   "machine_compliance_coefficient": c_mm_kn,
                   "machine_compliance_unit": c_unit},
        calc_plan={"strain_source": "extensometer", "stress_unit": "MPa"},
    )
    db.add(run)
    db.flush()
    for kind, vals, unit in (
        ("load", list(run_data.load_n), "N"),
        ("crosshead_displacement", list(run_data.crosshead_m), "m"),
        ("extensometer_displacement", list(run_data.extensometer_m), "m"),
    ):
        db.add(RawSignal(run_id=run.id, kind=kind, unit=unit,
                         point_count=len(vals), values=vals,
                         si_values=services.convert_channel_to_si(kind, vals, unit)))
    db.commit()
    return {"run_id": run.id, "specimen_id": s.id, "case": case_name,
            "points": run_data.n, "diameter_omitted": omit_diameter,
            "machine_compliance": {
                "coefficient": c_mm_kn, "unit": c_unit,
                "coefficient_si_m_per_n": c_si,
            }}
