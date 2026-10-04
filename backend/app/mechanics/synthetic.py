"""合成试验信号，用于无试验机情况下的核对。

直接在“载荷-位移”空间生成，保证从信号到应力应变的全链路被检验，
而不是直接喂一条已知应力应变曲线自证。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class SyntheticRun:
    load_n: np.ndarray
    crosshead_m: np.ndarray
    extensometer_m: np.ndarray
    n: int


def _signals_from_strain(strain: np.ndarray, stress_pa: np.ndarray,
                         area_m2: float, le_m: float, lc_m: float,
                         compliance: float = 1.2) -> SyntheticRun:
    """由目标材料应变/应力反推三通道信号。

    夹具位移额外包含机器柔度位移 F*compliance_factor，
    因此夹具位移通道与引伸计通道必然不同——用于检验二者不可混列。
    """
    load = stress_pa * area_m2
    ext_disp = strain * le_m
    cross_disp = strain * lc_m + load / (area_m2 * 2e11) * compliance * lc_m
    return SyntheticRun(load_n=load, crosshead_m=cross_disp,
                        extensometer_m=ext_disp, n=len(strain))


def case_linear_elastic(E_pa: float = 200e9, d0_mm: float = 10.0,
                        le_mm: float = 50.0, lc_mm: float = 80.0,
                        eps_end: float = 0.005, n: int = 200) -> SyntheticRun:
    """案例 A：合成纯线弹性，加载至 eps_end 即终止（脆性/卸载）。

    应力应变曲线与 0.2% 偏移线平行，永远不相交 → 不应报出屈服强度。
    """
    area = math.pi * (d0_mm * 1e-3) ** 2 / 4
    eps = np.linspace(0.0, eps_end, n)
    sig = E_pa * eps
    return _signals_from_strain(eps, sig, area, le_mm * 1e-3, lc_mm * 1e-3)


def case_clear_yield(E_pa: float = 200e9, sigma_y_pa: float = 400e6,
                     d0_mm: float = 10.0, le_mm: float = 50.0,
                     lc_mm: float = 80.0, hardening_pa: float = 1.5e9,
                     eps_frac: float = 0.18, n: int = 600) -> SyntheticRun:
    """参考案例：弹性段 + 屈服拐点 + 温和线性硬化，颈缩在 80% 断裂应变处。

    硬化斜率 1.5 GPa 远小于 E；0.2% 偏移交点约 σy + 3 MPa。
    """
    area = math.pi * (d0_mm * 1e-3) ** 2 / 4
    eps = np.linspace(0.0, eps_frac, n)
    eps_y = sigma_y_pa / E_pa
    plastic = np.maximum(eps - eps_y, 0.0)
    sig = np.where(eps <= eps_y,
                   E_pa * eps,
                   sigma_y_pa + hardening_pa * plastic)
    # 人为制造峰值后下降（颈缩后工程应力跌落）
    peak_idx = int(0.8 * n)
    after = np.arange(n - peak_idx)
    sig[peak_idx:] = sig[peak_idx] * np.exp(-after / (n - peak_idx) * 1.2)
    return _signals_from_strain(eps, sig, area, le_mm * 1e-3, lc_mm * 1e-3)


def case_no_clear_yield(E_pa: float = 200e9, eps_kink: float = 0.001,
                        tangent_ratio: float = 0.75, d0_mm: float = 10.0,
                        le_mm: float = 50.0, lc_mm: float = 80.0,
                        eps_frac: float = 0.012, n: int = 300) -> SyntheticRun:
    """案例 B：无清晰屈服。

    0.001 应变前为线弹性，之后割线刚度仅小幅下降（tangent_ratio*E），
    与 0.2% 偏移线相交但交点附近无屈服平台 → yield_unclear=True。
    """
    area = math.pi * (d0_mm * 1e-3) ** 2 / 4
    eps = np.linspace(0.0, eps_frac, n)
    sig = np.where(
        eps <= eps_kink,
        E_pa * eps,
        E_pa * eps_kink + tangent_ratio * E_pa * (eps - eps_kink),
    )
    return _signals_from_strain(eps, sig, area, le_mm * 1e-3, lc_mm * 1e-3)
