"""The value contract: `Valuation` (what one mark returns) and `MarkContext` (everything a bound callable may see at a mark).

pricebt never looks inside a pricable: a bound callable (see `contracts.binding`) returns a `Valuation`, a tuple `(pv[, cash[, financing]])` or a bare number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

import pandas as pd

from .pricer import Pricer


@dataclass(frozen=True)
class Valuation:
    """One mark of ONE unit of an instrument. `pv` is the full economic mark; `cash`/`financing` are flows realised in [prev_ts, ts)."""

    ts: pd.Timestamp
    pv: float
    cash: float = 0.0
    financing: float = 0.0
    meta: Mapping[str, Any] = field(default_factory=dict)


def as_valuation(x: Any, ts: pd.Timestamp) -> Valuation:
    if isinstance(x, Valuation):
        return x
    if isinstance(x, tuple) and len(x) >= 1:
        pv = float(x[0])
        return Valuation(ts, pv, float(x[1]) if len(x) > 1 else 0.0, float(x[2]) if len(x) > 2 else 0.0)
    return Valuation(ts, float(getattr(x, "real", x)))


@dataclass(frozen=True)
class MarkContext:
    """Everything a bound callable may reference with `@ctx...`. The instrument is never handed the backtest."""

    ts: pd.Timestamp
    pricer: Pricer
    pricers: Mapping[str, Pricer]
    prev_ts: Optional[pd.Timestamp]
    prev_pricer: Optional[Pricer]
    entry_ts: pd.Timestamp
    entry_pricer: Pricer
    state: Dict[str, Any]  # per-position scratch owned by the engine, persisted between marks
    cache: Dict[Any, Any]  # per-(position, ts) scratch shared by value, layers and measures
    params: Mapping[str, Any] = field(default_factory=dict)  # config-supplied per-instrument parameters

    @classmethod
    def standalone(cls, pricer: Pricer, ts: Optional[pd.Timestamp] = None, prev: Optional[Pricer] = None, **params: Any) -> "MarkContext":
        """A context outside a backtest (notebooks, tests): entry == prev == given pricers."""
        t = ts or pricer.ts
        return cls(t, pricer, {"primary": pricer}, prev.ts if prev else None, prev, (prev or pricer).ts, prev or pricer, {}, {}, params)
