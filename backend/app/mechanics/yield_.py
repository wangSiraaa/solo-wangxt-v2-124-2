"""0.2% 偏移法条件屈服强度 Rp0.2。

偏移线：σ = E·(ε − 0.002)，应变单位 mm/mm（0.2% 即无量纲 0.002），
应力内部为 Pa、按用户单位输出。

几何关系：曲线在弹性段始终位于偏移线上方（偏移线在 ε<0.002 时为负）；
发生塑性流动后曲线斜率低于 E，曲线自上而下穿越偏移线，交点即 Rp0.2。
无交点（纯线弹性/断裂过早/合成数据）时 found=False，不插值猜测。
"""
from __future__ import annotations

import numpy as np

from .curves import StressStrainCurve
from ..schemas import FitResult, YieldResult
from ..units import OFFSET_YIELD_STRAIN, stress_from_pa, StressUnit


def _first_intersection(strain: np.ndarray, stress: np.ndarray,
                        slope: float, intercept: float) -> tuple[int, float, float] | None:
    """曲线自上方穿越偏移线（diff: >=0 -> <0）的首个线段交点。"""
    diff = stress - (slope * strain + intercept)
    for i in range(len(diff) - 1):
        if diff[i] >= 0.0 > diff[i + 1]:
            f0, f1 = diff[i], diff[i + 1]
            t = -f0 / (f1 - f0)
            eps = strain[i] + t * (strain[i + 1] - strain[i])
            sig = slope * eps + intercept
            return i, float(sig), float(eps)
    return None


def _first_intersection_valid(strain: np.ndarray, stress: np.ndarray,
                              valid: np.ndarray | None,
                              slope: float, intercept: float,
                              search_from: int) -> tuple[float, float] | None:
    """寻找首个自上方穿越点；非物理点使曲线断开，绝不跨间隙插值。"""
    diff = stress - (slope * strain + intercept)
    for i in range(search_from, len(diff) - 1):
        if valid is not None and not (valid[i] and valid[i + 1]):
            continue
        if diff[i] >= 0.0 > diff[i + 1]:
            f0, f1 = diff[i], diff[i + 1]
            t = -f0 / (f1 - f0)
            eps = strain[i] + t * (strain[i + 1] - strain[i])
            sig = slope * eps + intercept
            return float(sig), float(eps)
    return None


def proof_stress_offset(curve: StressStrainCurve, fit: FitResult,
                        stress_unit: str = "MPa",
                        offset: float = OFFSET_YIELD_STRAIN) -> YieldResult:
    stress_unit_enum = StressUnit(stress_unit)
    E = fit.slope_pa
    # 弹性拟合上限之后寻找交点（初段噪声不可能产生真实屈服交点）
    search_from = max(0, int(np.searchsorted(curve.strain, fit.strain_max, side="left")) - 1)
    # 柔度修正后的非物理点不参与屈服判定：曲线在这些点断开，绝不跨间隙插值
    hit = _first_intersection_valid(
        curve.strain, curve.engineering_stress, curve.point_valid,
        E, -E * offset, search_from,
    )
    # 无交点原因判定使用最后一个物理有效点
    valid = curve.valid_mask()
    last_valid = int(np.where(valid)[0][-1])
    if hit is None:
        diff_end = float(
            curve.engineering_stress[last_valid]
            - E * (curve.strain[last_valid] - offset)
        )
        if diff_end >= 0:
            reason = (
                "工程应力-应变曲线在整个记录范围内始终位于 0.2% 偏移线上方，"
                f"二者无交点（末有效点应变 {curve.strain[last_valid]:.4f}）："
                "材料保持线弹性或在出现塑性流动前终止，0.2% 偏移法无法给出条件屈服强度"
            )
        else:
            reason = ("曲线在记录末端低于 0.2% 偏移线但未检测到自上方的穿越，"
                      "请检查弹性区间拟合与数据噪声")
        return YieldResult(
            found=False, offset_strain=offset, proof_stress=None,
            stress_unit=stress_unit_enum.value, at_strain=None, reason=reason,
            yield_unclear=False,
        )

    sig_pa, eps = hit

    # “无清晰屈服”判定：交点附近割线模量仍较高（连续过渡、无屈服平台）。
    # 交点前后窗口局部刚度 / E > 0.6 视为无明显屈服；只取连续物理有效点。
    i0 = int(np.searchsorted(curve.strain, eps, side="left"))
    lo, hi = max(0, i0 - 3), min(len(curve.strain), i0 + 4)
    win_idx = np.array(
        [i for i in range(lo, hi) if valid[i]], dtype=int
    )
    xs, ys = curve.strain[win_idx], curve.engineering_stress[win_idx]
    unclear = False
    if len(xs) >= 3:
        local_slope = np.polyfit(xs, ys, 1)[0]
        unclear = bool(local_slope / E > 0.6)

    reason = "0.2% 偏移线（σ=E(ε−0.002)，ε 单位 mm/mm）与工程应力-应变曲线交点（线性插值）"
    if unclear:
        reason += "；交点附近无明显屈服平台，该值仅为条件屈服强度 Rp0.2，结果对弹性区间选择敏感"

    return YieldResult(
        found=True,
        offset_strain=offset,
        proof_stress=float(stress_from_pa(sig_pa, stress_unit_enum)),
        stress_unit=stress_unit_enum.value,
        at_strain=float(eps),
        reason=reason,
        yield_unclear=unclear,
    )
