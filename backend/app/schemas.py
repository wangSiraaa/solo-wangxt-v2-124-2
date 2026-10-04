"""Pydantic 请求/响应模型。"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---- 试样与试验 ----

class SpecimenGeometry(BaseModel):
    geometry: Literal["round", "rectangular"] = "round"
    # 圆截面试样：初始直径 d0；矩形：宽度 b0 与厚度 t0
    nominal_diameter_mm: float | None = Field(default=None, gt=0)
    nominal_width_mm: float | None = Field(default=None, gt=0)
    nominal_thickness_mm: float | None = Field(default=None, gt=0)
    gauge_length_mm: float | None = Field(default=None, gt=0, description="引伸计标距 L0，mm")
    parallel_length_mm: float | None = Field(default=None, gt=0, description="平行段长度，mm")


class SpecimenCreate(BaseModel):
    code: str = Field(..., description="试样编号")
    material: str | None = None
    geometry: SpecimenGeometry
    # 断后实测（试验后录入；缺失则 A、Z 无法计算）
    final_diameter_mm: float | None = Field(default=None, gt=0)
    final_gauge_length_mm: float | None = Field(default=None, gt=0)


class SpecimenOut(BaseModel):
    id: int
    code: str
    material: str | None = None
    geometry: str = "round"
    nominal_diameter_mm: float | None = None
    nominal_width_mm: float | None = None
    nominal_thickness_mm: float | None = None
    gauge_length_mm: float | None = None
    parallel_length_mm: float | None = None
    final_diameter_mm: float | None = None
    final_gauge_length_mm: float | None = None

    model_config = {"from_attributes": True}


class EquipmentParams(BaseModel):
    machine: str | None = None
    load_cell_capacity_n: float | None = Field(default=None, gt=0)
    extensometer_gauge_length_mm: float | None = Field(default=None, gt=0)
    sampling_rate_hz: float | None = Field(default=None, gt=0)


class ChannelIngest(BaseModel):
    """单个信号通道。

    夹具位移（crosshead）与引伸计（extensometer）必须分列，
    载荷单列，长度一致；任何合并都在计算阶段显式发生。
    """
    kind: Literal["load", "crosshead_displacement", "extensometer_displacement"]
    unit: str
    values: list[float]


class TestRunCreate(BaseModel):
    specimen_id: int
    equipment: EquipmentParams
    channels: list[ChannelIngest] = Field(..., min_length=2)
    # 计算方案：应变来源 + 输出应力单位，随试验记录保存
    strain_source: Literal["extensometer", "crosshead"] = "extensometer"
    stress_unit: str = "MPa"
    displacement_unit_for_curve: str = "mm"


# ---- 分析 ----

class ExcludedPoint(BaseModel):
    index: int = Field(..., ge=0)
    reason: str = Field(..., min_length=1, description="人工排除原因，不允许空")


class FitRequest(BaseModel):
    run_id: int
    strain_source: Literal["extensometer", "crosshead"] | None = None
    stress_unit: str = "MPa"
    # 用户选择的弹性区间（应变，mm/mm）；为空时由后端给出建议区间
    strain_min: float | None = None
    strain_max: float | None = None
    excluded_points: list[ExcludedPoint] = Field(default_factory=list)


class ResidualPoint(BaseModel):
    index: int
    strain: float
    stress: float
    fitted_stress: float
    residual: float


class FitResult(BaseModel):
    slope_pa: float
    modulus_in_output_unit: float
    modulus_unit: str
    intercept_pa: float
    strain_min: float
    strain_max: float
    n_points: int
    n_excluded: int
    r_squared: float
    rmse_pa: float
    max_abs_residual_pa: float
    residuals: list[ResidualPoint]
    excluded: list[ExcludedPoint]


class IntervalSensitivity(BaseModel):
    """区间影响：在用户区间周围缩放/平移时模量如何变化。"""
    label: str
    strain_min: float
    strain_max: float
    modulus_in_output_unit: float
    r_squared: float
    n_points: int


class YieldResult(BaseModel):
    found: bool
    method: str = "0.2% offset (EN 10002-1 / ASTM E8)"
    offset_strain: float = 0.002
    proof_stress: float | None = None
    stress_unit: str
    at_strain: float | None = None
    reason: str
    yield_unclear: bool = Field(description="无明显屈服/平台：仅条件屈服值，且残差/曲率判定不明确")


class FractureResult(BaseModel):
    engineering_fracture_strain: float | None
    elongation_percent: float | None = Field(default=None, description="断后伸长率 A，%（需断后标距）")
    reduction_of_area_percent: float | None = Field(default=None, description="断面收缩率 Z，%（需断后直径）")
    ultimate_tensile_strength: float
    stress_unit: str
    strain_at_uts: float
    data_complete: bool
    missing_inputs: list[str]


class CurvePoint(BaseModel):
    index: int
    strain: float
    engineering_stress: float | None
    true_stress: float | None = Field(description="颈缩后为 null，简单换算在颈缩后不再假装有效")
    true_stress_valid: bool
    load_n: float
    source: str


class AnalysisResult(BaseModel):
    run_id: int
    strain_source_used: str
    stress_unit: str
    elastic_fit: FitResult
    interval_sensitivity: list[IntervalSensitivity]
    yield_result: YieldResult
    fracture: FractureResult
    curve: list[CurvePoint]
    necking_index: int | None
    warnings: list[str]
    provenance: dict


class ReportOut(BaseModel):
    report_id: int
    run_id: int
    title: str
    markdown: str
    generated_at: str
