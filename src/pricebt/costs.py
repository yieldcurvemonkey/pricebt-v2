"""Transaction-cost models (Constant / Scaled / Aggregate).

A cost is scaled by a MEASURE of the traded instrument (`notional`, `dv01`, ...): the engine supplies `CostContext.measure(name)` from the position's bindings
and derived measures, so no cost model inspects an instrument. The cash-accrual models live in `pricebt.accrual` (re-exported here: the registries, the loader and the tests import them from `pricebt.costs`).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import pandas as pd

from .accrual import CallableCashAccrual, CashAccrualModel, ConstantCashAccrual, SeriesCashAccrual  # noqa: F401  (re-exported)
from .errors import ConfigError


@dataclass(frozen=True)
class CostContext:
    ts: pd.Timestamp
    quantity: float
    measure: Optional[Callable[[str], float]] = None  # per-UNIT measure of the traded instrument at ts (holder-positive)
    additional_scaling: float = 1.0


@dataclass(frozen=True)
class CostBreakdown:
    fixed: float = 0.0
    scaled: float = 0.0

    @property
    def total(self) -> float:
        return self.fixed + self.scaled


class CostModel(ABC):
    @abstractmethod
    def breakdown(self, ctx: CostContext) -> CostBreakdown: ...

    def cost(self, ctx: CostContext) -> float:
        return self.breakdown(ctx).total


@dataclass(frozen=True)
class ConstantCost(CostModel):
    """A fixed cost per order leg (not scaled by size), gs ConstantTransactionModel."""

    cost_per_leg: float = 0.0

    def breakdown(self, ctx: CostContext) -> CostBreakdown:
        return CostBreakdown(fixed=abs(self.cost_per_leg))


@dataclass(frozen=True)
class ScaledCost(CostModel):
    """level * |size_measure * quantity * additional_scaling|. `scaling_type` names a measure of the instrument ('notional', 'dv01', ...); a leading
    'measure:' is accepted and ignored."""

    scaling_type: str = "notional"
    scaling_level: float = 1e-4

    def breakdown(self, ctx: CostContext) -> CostBreakdown:
        name = self.scaling_type.split(":", 1)[1] if self.scaling_type.startswith("measure:") else self.scaling_type
        if ctx.measure is None:
            raise ConfigError("ScaledCost needs CostContext.measure", code="COST")
        unit = float(ctx.measure(name))
        return CostBreakdown(scaled=self.scaling_level * abs(unit * ctx.quantity * ctx.additional_scaling))


@dataclass(frozen=True)
class AggregateCost(CostModel):
    models: Sequence[CostModel] = ()
    aggregate: str = "sum"

    def breakdown(self, ctx: CostContext) -> CostBreakdown:
        parts = [m.breakdown(ctx) for m in self.models]
        if not parts:
            return CostBreakdown()
        if self.aggregate == "sum":
            return CostBreakdown(sum(p.fixed for p in parts), sum(p.scaled for p in parts))
        pick = max if self.aggregate == "max" else min if self.aggregate == "min" else None
        if pick is None:
            raise ConfigError(f"unknown aggregate {self.aggregate!r}", code="COST")
        return pick(parts, key=lambda p: p.total)
