"""Test doubles for the contracts tests: a swap-shaped object whose methods and parameters are named UNLIKE the schema (spec B5, Z3).

Nothing here imports pricebt internals beyond the public contract types, so these are the shape of a user's own library.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Mapping

import pandas as pd

from pricebt.contracts.spec import Built, Kit


class Weird:
    """A linear swap in somebody else's vocabulary. `dv01`, `value` and `gamma` exist as METHOD NAMES with the WRONG meaning so that any
    by-name resolution would silently pick them up; the right numbers live under names no schema knows."""

    def __init__(self, notional: float, direction: int, rate: float):
        self.calls: List[str] = []
        self.n, self.d, self.k = notional, direction, rate

    def zz_value(self, *, market: Any) -> float:
        self.calls.append("zz_value")
        return self.d * self.n * (market.level - self.k) * 1e-2

    def pp01(self, *, market: Any) -> float:
        self.calls.append("pp01")
        return self.d * self.n * 1e-4

    def curv(self, *, market: Any, bump_bp: float = 1.0) -> float:
        self.calls.append("curv")
        return 0.5 * bump_bp

    def parlevel(self, *, market: Any) -> float:
        self.calls.append("parlevel")
        return market.level

    def lay_a(self, *, market: Any) -> float:
        self.calls.append("lay_a")
        return 0.0

    def lay_b(self, *, market: Any) -> float:
        self.calls.append("lay_b")
        return 0.0

    def lad(self, *, market: Any, buckets: List[str]) -> "pd.Series":
        self.calls.append("lad")
        return pd.Series({b: self.d * self.n * 1e-4 / len(buckets) for b in buckets})

    # WRONG-MEANING same-name traps: a by-name resolver would call these.
    def value(self, *a: Any, **k: Any) -> float:
        raise AssertionError("value() must never be called: nothing binds the schema name `value` to it")

    def dv01(self, *a: Any, **k: Any) -> float:
        raise AssertionError("dv01() must never be called: nothing binds the schema name `dv01` to it")

    def gamma(self, *a: Any, **k: Any) -> float:
        raise AssertionError("gamma() must never be called: nothing binds the schema name `gamma` to it")

    def delta_ladder(self, *a: Any, **k: Any) -> float:
        raise AssertionError("delta_ladder() must never be called")


class Market:
    """A pricer-shaped object: only what the bindings reference."""

    def __init__(self, level: float = 4.0):
        self.level = level
        self.ts = pd.Timestamp("2024-01-02 17:00", tz="America/New_York")
        self.reference_date = self.ts.date()


def resolve_dates(terms: Mapping[str, Any], ts: Any) -> Dict[str, Any]:
    """The 'library' resolving relative dates: spot = trade date + 2 days, a tenor `nY` = n years later. Enough for a test double."""
    start = ts.date()
    out: Dict[str, Any] = dict(terms)
    eff = terms["effective"]
    if isinstance(eff, str):
        eff = start + dt.timedelta(days=2) if eff.lower() == "spot" else start.replace(year=start.year + int(eff[:-1]))
    out["effective"] = eff
    mat = terms["maturity"]
    if isinstance(mat, str):
        mat = eff.replace(year=eff.year + int(mat[:-1]))
    out["maturity"] = mat
    return out


def weird_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    resolved = resolve_dates(terms, ts)
    if resolved["fixed_rate"] == "par":
        resolved["fixed_rate"] = pricer.level  # the factory (the library) resolves `par`
    return Built(Weird(float(terms["notional"]), int(terms["direction"]), float(resolved["fixed_rate"])), resolved)


def as_valuation_fn(pv: float, ts: Any) -> Any:
    from pricebt.pricable import Valuation

    return Valuation(ts, pv)


def as_tuple_fn() -> Any:
    return (7.0, 0.0, 0.0)


def nan_fn() -> float:
    return float("nan")


def none_fn() -> None:
    return None


def scalar_fn() -> float:
    return 3.0


def short_ladder(tenors: List[str]) -> Dict[str, float]:
    return {"2Y": 1.0, "10Y": 2.0}


def long_ladder(tenors: List[str]) -> Dict[str, float]:
    return {"2Y": 1.0, "10Y": 2.0, "30Y": 3.0}


def unsorted_ladder(tenors: List[str]) -> Dict[str, float]:
    return {"10Y": 2.0, "2Y": 1.0}


def junk_ladder() -> Dict[str, float]:
    return {"2Y": 1.0, "front": 2.0}


BIND = {
    "value": {"target": {"method": "zz_value"}, "kwargs": {"market": "@pricer"}},
    "dv01": {"target": {"method": "pp01"}, "kwargs": {"market": "@pricer"}},
    "gamma": {"target": {"method": "curv"}, "kwargs": {"market": "@pricer", "bump_bp": 2.0}},
    "rate": {"target": {"method": "parlevel"}, "kwargs": {"market": "@pricer"}},
    "delta_ladder": {"target": {"method": "lad"}, "kwargs": {"market": "@pricer", "buckets": ["2Y", "10Y"]}, "keys": ["2Y", "10Y"], "reduce": "series_to_tenor_dict"},
    "carry": {"target": {"method": "lay_a"}, "kwargs": {"market": "@pricer"}},
    "roll": {"target": {"method": "lay_a"}, "kwargs": {"market": "@pricer"}},
    "delta": {"target": {"method": "lay_b"}, "kwargs": {"market": "@pricer"}},
    "convexity": {"target": {"method": "lay_b"}, "kwargs": {"market": "@pricer"}},
}

weird_kit = Kit(factory=weird_factory, asset_class="swap", default_bind=BIND, cls=Weird, doc="a swap in somebody else's vocabulary")
weird_kit_no_defaults = Kit(factory=weird_factory, asset_class="swap", default_bind={}, cls=Weird)
