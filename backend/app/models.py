"""SQLAlchemy 模型。生产使用 PostgreSQL（JSONB），本地/测试使用 SQLite（JSON）。

原始信号按通道分行保存（load / crosshead / extensometer），
试样尺寸、设备参数、计算方案各自独立列/表，可全程溯源。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


JSONType = JSON().with_variant(JSONB, "postgresql")


class Specimen(Base):
    __tablename__ = "specimens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    material: Mapped[str | None] = mapped_column(String(128))
    # 试样尺寸（SI 由应用层处理，库内存录入单位 mm；单位在列名中显式）
    geometry: Mapped[str] = mapped_column(String(16), default="round")
    nominal_diameter_mm: Mapped[float | None] = mapped_column(Float)
    nominal_width_mm: Mapped[float | None] = mapped_column(Float)
    nominal_thickness_mm: Mapped[float | None] = mapped_column(Float)
    gauge_length_mm: Mapped[float | None] = mapped_column(Float)
    parallel_length_mm: Mapped[float | None] = mapped_column(Float)
    final_diameter_mm: Mapped[float | None] = mapped_column(Float)
    final_gauge_length_mm: Mapped[float | None] = mapped_column(Float)

    runs: Mapped[list["TestRun"]] = relationship(
        back_populates="specimen", cascade="all, delete-orphan"
    )


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    specimen_id: Mapped[int] = mapped_column(ForeignKey("specimens.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # 设备参数（载荷传感器量程、引伸计标距、采样率……）
    equipment: Mapped[dict] = mapped_column(JSONType, default=dict)
    # 计算方案：应变来源、应力单位
    calc_plan: Mapped[dict] = mapped_column(JSONType, default=dict)

    specimen: Mapped[Specimen] = relationship(back_populates="runs")
    channels: Mapped[list["RawSignal"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class RawSignal(Base):
    """原始信号：永不就地修改；人工排除发生在分析记录中，不回写原始数据。"""
    __tablename__ = "raw_signals"
    __table_args__ = (UniqueConstraint("run_id", "kind", name="uq_run_channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False,
                                     comment="load|crosshead_displacement|extensometer_displacement")
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    values: Mapped[list] = mapped_column(JSONType, nullable=False)
    si_values: Mapped[list] = mapped_column(JSONType, nullable=False,
                                           comment="换算到 SI(N/m) 后的不可变副本")

    run: Mapped[TestRun] = relationship(back_populates="channels")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id"), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    params: Mapped[dict] = mapped_column(JSONType, nullable=False,
                                        comment="弹性区间、排除点(含原因)、单位选择")
    result: Mapped[dict] = mapped_column(JSONType, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)

    run: Mapped[TestRun] = relationship(back_populates="analyses")
    reports: Mapped[list["Report"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256))
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    analysis: Mapped[Analysis] = relationship(back_populates="reports")
