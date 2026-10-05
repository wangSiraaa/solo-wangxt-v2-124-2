"""机器柔度修正：按载荷逐点从夹具位移中扣除机器变形。

试验机机架、夹头与力传感器存在可测柔度 C（校准得到），加载时的机器
变形为 δ_machine = F·C。用夹具位移推算材料应变前应逐点扣除：

    δ_corrected[i] = δ_crosshead[i] − F[i]·C_SI

本模块**只在分析层工作**：Channels 中的原始夹具位移与数据库
raw_signals 均不回写，修正量另存为独立曲线。

扣除后出现非物理点（相对修正位移为负，或修正位移较上一有效点回退）
必须显式标记，绝不让其混入拟合。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..units import ComplianceUnit, compliance_to_m_per_n


@dataclass(frozen=True)
class MachineCompliance:
    """校准柔度系数（含单位）。C > 0；单位缺失/未知时拒绝分析。"""
    coefficient: float
    unit: str

    def to_si(self) -> float:
        try:
            return compliance_to_m_per_n(self.coefficient, self.unit)
        except ValueError as exc:
            raise ValueError(
                f"未知的机器柔度校准单位 {self.unit!r}，"
                f"支持的单位：{[u.value for u in ComplianceUnit]}"
            ) from exc


@dataclass
class CorrectedDisplacement:
    """逐点修正结果（长度与原始信号一致，索引一一对应）。"""
    corrected_m: np.ndarray          # 绝对修正位移（m，保留零点对齐前形态）
    relative_corrected_m: np.ndarray  # 相对首点的修正位移（用于应变）
    machine_deformation_m: np.ndarray  # 被扣除的机器变形 F·C（m）
    valid: np.ndarray                # bool：该点物理有效
    invalid_reasons: list[str | None]  # 逐点非物理原因（有效点为 None）
    n_invalid: int
    compliance_si: float             # 使用的 SI 柔度（m/N）


def parse_compliance(coefficient: float | None,
                     unit: str | None) -> MachineCompliance | None:
    """从请求参数解析柔度校准值；未启用返回 None。

    只给系数不给单位（或反之）必须报错，不允许使用隐式单位保存伪结果。
    """
    if coefficient is None and (unit is None or unit == ""):
        return None
    if coefficient is None:
        raise ValueError(
            f"启用机器柔度修正需要柔度系数，当前只提供了校准单位 {unit!r}，"
            "无法进行修正"
        )
    if unit is None or unit == "":
        raise ValueError(
            "启用机器柔度修正必须提供校准单位（m/N、mm/N 或 mm/kN），"
            "缺少单位的系数无法解释，拒绝保存可能错误的修正结果"
        )
    if not isinstance(coefficient, (int, float)) or not np.isfinite(coefficient) \
            or coefficient <= 0:
        raise ValueError(
            f"机器柔度系数必须为正的有限数值，收到 {coefficient!r}"
        )
    return MachineCompliance(float(coefficient), str(unit))


def apply_compliance_correction(crosshead_m: np.ndarray, load_n: np.ndarray,
                                compliance: MachineCompliance) -> CorrectedDisplacement:
    """逐点 δ_corr = δ_cross − F·C，并标记非物理点。

    判定规则：
    * relative_corrected < 0 —— 扣除量超过原始位移增量，柔度过大或数据异常；
    * 修正位移较“上一有效点”回退 —— 修正曲线失去加载单调性。
    首点（相对位移为 0）天然有效。
    """
    c_si = compliance.to_si()
    crosshead_m = np.asarray(crosshead_m, dtype=float)
    load_n = np.asarray(load_n, dtype=float)

    machine_def = load_n * c_si
    corrected = crosshead_m - machine_def
    relative = corrected - corrected[0]

    n = len(corrected)
    valid = np.ones(n, dtype=bool)
    reasons: list[str | None] = [None] * n
    tol = 1e-9 * max(float(np.max(np.abs(corrected))), 1.0)
    last_good = 0
    for i in range(1, n):
        local_reasons: list[str] = []
        if relative[i] < -tol:
            local_reasons.append(
                f"修正后相对位移为负（{relative[i]:.3e} m）：机器变形 F·C "
                f"={machine_def[i] - machine_def[0]:.3e} m 超过夹具位移增量，"
                "柔度系数过大或该点载荷/位移异常"
            )
        elif not np.isfinite(relative[i]):
            local_reasons.append("修正后位移为非有限值（NaN/Inf），原始信号异常")
        if i > 0 and relative[i] + tol < relative[last_good]:
            local_reasons.append(
                f"修正后位移在第 {i} 点回退（{relative[i]:.3e} m < 上一有效点 "
                f"{relative[last_good]:.3e} m）：修正曲线不再单调加载"
            )
        if local_reasons:
            valid[i] = False
            reasons[i] = "；".join(local_reasons)
        else:
            last_good = i

    return CorrectedDisplacement(
        corrected_m=corrected,
        relative_corrected_m=relative,
        machine_deformation_m=machine_def,
        valid=valid,
        invalid_reasons=reasons,
        n_invalid=int((~valid).sum()),
        compliance_si=c_si,
    )
