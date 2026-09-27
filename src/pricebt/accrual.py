"""Cash accrual: an interface `interest(balance, t0, t1) -> float` and the shipped implementations.

pricebt owns no day count and no compounding rule. `ConstantCashAccrual` and `SeriesCashAccrual` take the accrual BASIS (days per year) and the
compounding rule ('compound' | 'simple') as EXPLICIT parameters with no default (spec C1), so the convention is always stated by the caller; a callable
(`CallableCashAccrual`) lets a library or user function own it entirely. The gs_quant default (1 + r/365) is isolated in `pricebt.backtests`.
This is the one core module allowed to name a basis and a compounding rule (guard `convention_allow`).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from .errors import ConfigError


class CashAccrualModel(ABC):
    @abstractmethod
    def interest(self, balance: float, t0: pd.Timestamp, t1: pd.Timestamp) -> float: ...


def wall_days(t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    """Days between two stamps on the WALL CLOCK of `t0`'s zone (the zone is then dropped): a daily grid gives whole days, an intraday step stays proportional. Elapsed seconds would make a
    Friday-to-Monday weekend 71 hours across the spring-forward and 73 across the fall-back, so a book accrues a different number of days from one week to the next."""
    if t0.tzinfo is not None and t1.tzinfo is not None:
        t1 = t1.tz_convert(t0.tz)
    n0, n1 = (t.replace(tzinfo=None) for t in (t0, t1))
    return (n1 - n0).total_seconds() / 86400.0


def _accrue(balance: float, rate: float, basis_days: float, compounding: str, t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    days = wall_days(t0, t1)
    per_day = rate / basis_days
    if compounding == "compound":
        return balance * ((1.0 + per_day) ** days - 1.0)
    return balance * per_day * days


def _check_accrual(rate_name: str, basis_days: float, compounding: str) -> None:
    if not basis_days or basis_days <= 0:
        raise ConfigError(f"{rate_name}: basis_days must be a positive number of days per year (pricebt owns no day count)", code="ACCRUAL")
    if compounding not in ("compound", "simple"):
        raise ConfigError(f"{rate_name}: compounding must be 'compound' or 'simple', got {compounding!r}", code="ACCRUAL")


@dataclass(frozen=True)
class ConstantCashAccrual(CashAccrualModel):
    """A constant annual `rate` (decimal, 0.04 = 4%) over an explicit `basis_days` and `compounding` ('compound' | 'simple'). No default for either."""

    rate: float = 0.0
    basis_days: float = 0.0
    compounding: str = ""

    def __post_init__(self) -> None:
        _check_accrual("ConstantCashAccrual", self.basis_days, self.compounding)

    def interest(self, balance: float, t0: pd.Timestamp, t1: pd.Timestamp) -> float:
        return _accrue(balance, self.rate, self.basis_days, self.compounding, t0, t1)


@dataclass(frozen=True)
class SeriesCashAccrual(CashAccrualModel):
    """The rate in force at the interval start, read from user data: a date-indexed Series of annual decimal rates (as-of the local date of t0)."""

    rates: Any = None
    basis_days: float = 0.0
    compounding: str = ""

    def __post_init__(self) -> None:
        _check_accrual("SeriesCashAccrual", self.basis_days, self.compounding)
        s = pd.Series(self.rates, dtype=float)
        if s.empty:
            raise ConfigError("SeriesCashAccrual needs at least one rate", code="ACCRUAL")
        s.index = pd.DatetimeIndex(s.index).normalize().tz_localize(None) if pd.DatetimeIndex(s.index).tz is not None else pd.DatetimeIndex(s.index).normalize()
        object.__setattr__(self, "rates", s.sort_index())

    def interest(self, balance: float, t0: pd.Timestamp, t1: pd.Timestamp) -> float:
        day = pd.Timestamp(t0.date())
        s = self.rates
        j = s.index.searchsorted(day, side="right") - 1
        if j < 0:
            raise ConfigError(f"SeriesCashAccrual has no rate on or before {t0.date()}", code="ACCRUAL")
        return _accrue(balance, float(s.iloc[j]), self.basis_days, self.compounding, t0, t1)


@dataclass(frozen=True)
class CallableCashAccrual(CashAccrualModel):
    """`fn(balance, t0, t1) -> interest`: the caller (or a bound library function) owns the convention."""

    fn: Callable[[float, pd.Timestamp, pd.Timestamp], float] = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not callable(self.fn):
            raise ConfigError("CallableCashAccrual needs a callable fn(balance, t0, t1)", code="ACCRUAL")

    def interest(self, balance: float, t0: pd.Timestamp, t1: pd.Timestamp) -> float:
        return float(self.fn(balance, t0, t1))
