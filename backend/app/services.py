"""ORM <-> 计算内核 的转换服务。"""
from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from .mechanics.curves import Channels, SpecimenInfo
from .models import Analysis, RawSignal, Report, Specimen, TestRun
from .units import ForceUnit, LengthUnit, force_to_n, length_to_m


def specimen_to_info(s: Specimen) -> SpecimenInfo:
    return SpecimenInfo(
        geometry=s.geometry,
        nominal_diameter_mm=s.nominal_diameter_mm,
        nominal_width_mm=s.nominal_width_mm,
        nominal_thickness_mm=s.nominal_thickness_mm,
        gauge_length_mm=s.gauge_length_mm,
        parallel_length_mm=s.parallel_length_mm,
        # 引伸计标距来自设备参数（channels_from_run 中填入）
        extensometer_gauge_length_mm=None,
        final_diameter_mm=s.final_diameter_mm,
        final_gauge_length_mm=s.final_gauge_length_mm,
    )


def channels_from_run(run: TestRun) -> tuple[Channels, SpecimenInfo]:
    info = specimen_to_info(run.specimen)
    le = (run.equipment or {}).get("extensometer_gauge_length_mm")
    if le is not None:
        info.extensometer_gauge_length_mm = float(le)

    by_kind: dict[str, np.ndarray] = {}
    for ch in run.channels:
        # 使用入库时固化的 SI 副本；原始录入值保留在 values/unit 列不动
        by_kind[ch.kind] = np.asarray(ch.si_values, dtype=float)
    if "load" not in by_kind:
        raise ValueError("原始信号缺少 load 通道")
    return Channels(
        load_n=by_kind["load"],
        crosshead_m=by_kind.get("crosshead_displacement"),
        extensometer_m=by_kind.get("extensometer_displacement"),
    ), info


def convert_channel_to_si(kind: str, values: list[float], unit: str) -> list[float]:
    if kind == "load":
        return [force_to_n(v, ForceUnit(unit)) for v in values]
    return [length_to_m(v, LengthUnit(unit)) for v in values]


def mark_previous_analyses_not_latest(db: Session, run_id: int) -> None:
    olds = db.scalars(select(Analysis).where(Analysis.run_id == run_id)).all()
    for a in olds:
        a.is_latest = False


def persist_analysis(db: Session, run_id: int, params: dict, result: dict) -> Analysis:
    mark_previous_analyses_not_latest(db, run_id)
    analysis = Analysis(run_id=run_id, params=params, result=result, is_latest=True)
    db.add(analysis)
    db.flush()
    return analysis


def persist_report(db: Session, analysis_id: int, title: str, markdown: str) -> Report:
    report = Report(analysis_id=analysis_id, title=title, markdown=markdown)
    db.add(report)
    db.flush()
    return report


def get_run_or_none(db: Session, run_id: int) -> TestRun | None:
    return db.get(TestRun, run_id)
