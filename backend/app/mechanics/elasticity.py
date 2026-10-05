"""弹性区线性拟合：模量、残差与区间影响。"""
from __future__ import annotations

import numpy as np

from .curves import CurveError, StressStrainCurve
from ..schemas import (
    ExcludedPoint,
    FitResult,
    IntervalSensitivity,
    ResidualPoint,
)
from ..units import stress_from_pa, StressUnit


def _select_window(strain: np.ndarray, stress: np.ndarray,
                   strain_min: float, strain_max: float,
                   excluded: set[int],
                   valid_mask: np.ndarray | None = None) -> np.ndarray:
    mask = (strain >= strain_min) & (strain <= strain_max)
    mask &= stress > 0
    if valid_mask is not None:
        mask &= valid_mask
    for i in excluded:
        if 0 <= i < len(mask):
            mask[i] = False
    idx = np.where(mask)[0]
    if len(idx) < 3:
        raise CurveError(
            f"弹性区间 [{strain_min:.6g}, {strain_max:.6g}] 内有效点不足 3 个，无法拟合"
        )
    return idx


def _blocked_nonphysical(curve: StressStrainCurve,
                         strain_min: float, strain_max: float,
                         excluded: set[int]) -> list[int]:
    """区间内被柔度修正标记为非物理、且未被人工显式排除的点——必须阻止拟合。"""
    if curve.point_valid is None:
        return []
    in_window = np.where(
        (curve.strain >= strain_min) & (curve.strain <= strain_max)
    )[0]
    return [int(i) for i in in_window
            if not curve.point_valid[i] and int(i) not in excluded]


def suggest_elastic_window(strain: np.ndarray, stress: np.ndarray,
                           upper_fraction: float = 0.4,
                           valid_mask: np.ndarray | None = None) -> tuple[float, float]:
    """无用户选择时的建议区间：起点跳过初始机座间隙，上限取初段斜率稳定处。

    仅作建议，最终区间由用户确认。存在柔度修正非物理点时，只在物理有效点
    中选取建议区间。
    """
    if valid_mask is not None:
        good = np.where(valid_mask & (stress > 0))[0]
        if len(good) < 5:
            raise CurveError(
                "机器柔度修正后物理有效点不足 5 个，无法自动建议弹性区间："
                "请核对柔度系数与单位是否正确，拒绝在非物理数据上拟合"
            )
        first = int(good[0])
        strain = strain[first:]
        stress = stress[first:]
        valid_mask = valid_mask[first:]
    if len(strain) < 10:
        return float(strain[1]), float(strain[min(len(strain) - 1, 4)])
    # 初段割线模量，取其稳定窗口
    lo = max(1, int(0.02 * len(strain)))
    hi = max(lo + 5, int(upper_fraction * len(strain)))
    if valid_mask is not None:
        valid_idx = np.where(valid_mask)[0]
        valid_idx = valid_idx[valid_idx >= lo]
        if len(valid_idx) < 5:
            raise CurveError(
                "机器柔度修正后的物理有效段过短，无法自动建议弹性区间，"
                "请核对柔度校准值"
            )
        hi = min(hi, int(valid_idx[-1]))
        lo = max(lo, int(valid_idx[0]))
        if hi - lo < 4:
            lo, hi = int(valid_idx[0]), int(valid_idx[-1])
    seg = slice(lo, hi)
    slope = (stress[seg][-1] - stress[seg][0]) / (strain[seg][-1] - strain[seg][0] + 1e-30)
    # 逐点找首次明显偏离直线（应力低于模量*应变的 85%）的位置
    pred = slope * (strain - strain[lo]) + stress[lo]
    deviation_candidates = np.where(stress[lo:] < 0.85 * pred[lo:])[0]
    if valid_mask is not None:
        deviation_candidates = np.asarray(
            [d for d in deviation_candidates if valid_mask[lo + d]]
        )
    end = lo + deviation_candidates[0] if len(deviation_candidates) else hi
    end = max(lo + 5, min(end, len(strain) - 1))
    return float(strain[lo]), float(strain[end])


def fit_modulus(curve: StressStrainCurve,
                strain_min: float | None, strain_max: float | None,
                excluded: list[ExcludedPoint],
                stress_unit: str = "MPa") -> FitResult:
    stress_unit = StressUnit(stress_unit)
    excl_set = {p.index for p in excluded}

    if strain_min is None or strain_max is None:
        lo, hi = suggest_elastic_window(curve.strain, curve.engineering_stress,
                                        valid_mask=curve.valid_mask())
        strain_min = lo if strain_min is None else strain_min
        strain_max = hi if strain_max is None else strain_max
    if not strain_min < strain_max:
        raise CurveError("弹性区间无效：strain_min 必须小于 strain_max")

    # 修正后的非物理点落在所选区间内：显式阻止错误拟合（人工排除可覆盖）
    blocked = _blocked_nonphysical(curve, strain_min, strain_max, excl_set)
    if blocked:
        reasons = []
        for i in blocked[:3]:
            r = curve.point_invalid_reasons[i] if curve.point_invalid_reasons else None
            reasons.append(f"#{i}" + (f"（{r}）" if r else ""))
        raise CurveError(
            f"弹性区间 [{strain_min:.6g}, {strain_max:.6g}] 内含 "
            f"{len(blocked)} 个机器柔度修正后的非物理点（{', '.join(reasons)}"
            f"{'……' if len(blocked) > 3 else ''}），拒绝拟合："
            "请核对柔度系数/单位，或在确认后逐点人工排除并写明原因"
        )

    idx = _select_window(curve.strain, curve.engineering_stress,
                         strain_min, strain_max, excl_set,
                         valid_mask=curve.valid_mask())
    x = curve.strain[idx]
    y = curve.engineering_stress[idx]
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    resid = y - fitted
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    rmse = float(np.sqrt(ss_res / len(x)))

    residuals = [
        ResidualPoint(
            index=int(i),
            strain=float(curve.strain[i]),
            stress=float(stress_from_pa(curve.engineering_stress[i], stress_unit)),
            fitted_stress=float(stress_from_pa(slope * curve.strain[i] + intercept, stress_unit)),
            residual=float(stress_from_pa(curve.engineering_stress[i]
                                          - (slope * curve.strain[i] + intercept), stress_unit)),
        )
        for i in idx
    ]

    return FitResult(
        slope_pa=float(slope),
        modulus_in_output_unit=float(stress_from_pa(slope, stress_unit)),
        modulus_unit=str(stress_unit.value),
        intercept_pa=float(intercept),
        strain_min=float(strain_min),
        strain_max=float(strain_max),
        n_points=len(idx),
        n_excluded=len(excl_set),
        r_squared=r2,
        rmse_pa=rmse,
        max_abs_residual_pa=float(np.max(np.abs(resid))),
        residuals=residuals,
        excluded=excluded,
    )


def interval_sensitivity(curve: StressStrainCurve, fit: FitResult,
                         stress_unit: str = "MPa") -> list[IntervalSensitivity]:
    """区间影响分析：在用户确认区间周围收缩/扩展，观察模量漂移与 R²。"""
    stress_unit = StressUnit(stress_unit)
    span = fit.strain_max - fit.strain_min
    variants = [
        ("用户区间", fit.strain_min, fit.strain_max),
        ("收缩 25%", fit.strain_min + 0.125 * span, fit.strain_max - 0.125 * span),
        ("下限下移 25%", fit.strain_min - 0.25 * span, fit.strain_max),
        ("上限上移 25%", fit.strain_min, fit.strain_max + 0.25 * span),
    ]
    excl = {p.index for p in fit.excluded}
    out: list[IntervalSensitivity] = []
    for label, lo, hi in variants:
        try:
            # 敏感性区间若覆盖修正非物理点则跳过该变体，不在别的点集上偷算模量
            if _blocked_nonphysical(curve, lo, hi, excl):
                continue
            idx = _select_window(curve.strain, curve.engineering_stress, lo, hi,
                                 excl, valid_mask=curve.valid_mask())
            s, b = np.polyfit(curve.strain[idx], curve.engineering_stress[idx], 1)
            yhat = s * curve.strain[idx] + b
            y = curve.engineering_stress[idx]
            ss_res = float(np.sum((y - yhat) ** 2))
            ss_tot = float(np.sum((y - y.mean()) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
            out.append(IntervalSensitivity(
                label=label, strain_min=float(lo), strain_max=float(hi),
                modulus_in_output_unit=float(stress_from_pa(s, stress_unit)),
                r_squared=r2, n_points=len(idx),
            ))
        except CurveError:
            continue
    return out
