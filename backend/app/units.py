"""单位定义与换算。

系统内部统一使用 SI：长度 m，力 N，应力 Pa，应变无量纲（mm/mm）。
输入/输出层使用带单位的字符串，避免“0.2% 偏移”落到错误量纲上。
"""
from __future__ import annotations

from enum import Enum


class LengthUnit(str, Enum):
    M = "m"
    MM = "mm"


class ForceUnit(str, Enum):
    N = "N"
    KN = "kN"


class StressUnit(str, Enum):
    PA = "Pa"
    MPA = "MPa"
    GPA = "GPa"


_LENGTH_TO_M = {LengthUnit.M: 1.0, LengthUnit.MM: 1e-3}
_FORCE_TO_N = {ForceUnit.N: 1.0, ForceUnit.KN: 1e3}
_STRESS_TO_PA = {StressUnit.PA: 1.0, StressUnit.MPA: 1e6, StressUnit.GPA: 1e9}


def length_to_m(value: float, unit: str | LengthUnit) -> float:
    return value * _LENGTH_TO_M[LengthUnit(unit)]


def force_to_n(value: float, unit: str | ForceUnit) -> float:
    return value * _FORCE_TO_N[ForceUnit(unit)]


def stress_from_pa(value_pa: float, unit: str | StressUnit) -> float:
    return value_pa / _STRESS_TO_PA[StressUnit(unit)]


# 0.2% 偏移即 0.002 mm/mm；显式常量，禁止使用无单位魔数
OFFSET_YIELD_STRAIN = 0.002  # 0.2%
