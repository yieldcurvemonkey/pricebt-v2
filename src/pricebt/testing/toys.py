"""Pure-python toy market and instruments with closed-form values: the agnosticism proof and the workhorse of the core tests.

Market: a single scalar RATE (percent) plus a vol (bp/day-normal) evolving deterministically. The toy stack is an adapter like any other: each toy ships a
`Kit` (factory + default binding block) and `toy_template(obj)` wraps a ready-made object with its default bindings; nothing is resolved by name.
"""
from __future__ import annotations

import datetime as dt
import functools
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

import pandas as pd

from ..contracts.schema import SchemaRegistry
from ..contracts.spec import Built, Kit, TradeTemplate
from ..errors import MarketDataUnavailable
from ..pricable import MarkContext, Valuation
from ..pricer import PricerBase, SnapshotIndex, TimeMapping

_N = NormalDist()
DAY = 86400.0


class ToyPricer(PricerBase):
    """Holds `rate` (percent), `vol` (normal vol, bp per sqrt-year) and `funding` (percent)."""

    LOOKUPS = ("rate_at", "calendar_advance", "level")

    def __init__(self, ts: pd.Timestamp, rate: float, vol: float = 80.0, funding: float = 4.0, extra: Optional[Mapping[str, float]] = None):
        self.ts = ts
        self.reference_date = ts.date()
        self.rate = float(rate)
        self.vol = float(vol)
        self.funding = float(funding)
        self.extra = dict(extra or {})

    def level(self, name: str = "rate") -> float:
        return {"rate": self.rate, "vol": self.vol, "funding": self.funding, **self.extra}[name]

    def rate_at(self) -> float:
        return self.rate

    def calendar_advance(self, d: dt.date, tenor: str) -> dt.date:
        n = int(tenor[:-1])
        return d + dt.timedelta(days=n * {"d": 1, "w": 7, "m": 30, "y": 365}[tenor[-1].lower()])

    def describe(self) -> Mapping[str, Any]:
        return {"type": "ToyPricer", "ts": str(self.ts), "rate": self.rate}


def default_path(ts: pd.Timestamp) -> Dict[str, float]:
    """Deterministic smooth-but-nontrivial path: level 4% +/- oscillation + slow drift (percent)."""
    days = ts.value / (DAY * 1e9)
    return {"rate": 4.0 + 0.5 * math.sin(days / 9.0) + 0.002 * (days % 400) - 0.4 * math.sin(days / 1.3) * 0.1, "vol": 80.0 + 10.0 * math.sin(days / 23.0)}


class ToyMDP:
    """MarketDataProvider over a callable path or a DataFrame indexed by tz-aware stamps (columns rate[, vol, funding])."""

    def __init__(self, path: Optional[Callable[[pd.Timestamp], Mapping[str, float]]] = None, frame: Optional[pd.DataFrame] = None, mapping: Optional[TimeMapping] = None,
                 fail_at: Sequence[pd.Timestamp] = ()):
        self.path = path or default_path
        self.frame = frame
        self.mapping = mapping or TimeMapping("asof", pd.Timedelta(days=5))
        self.fail_at = {pd.Timestamp(x).value for x in fail_at}
        self._idx = SnapshotIndex(list(frame.index)) if frame is not None else None
        self.calls = 0

    def get_pricer(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> ToyPricer:
        self.calls += 1
        ts = pd.Timestamp(ts)
        if ts.value in self.fail_at:
            raise MarketDataUnavailable(ts, request, "scripted failure")
        if self.frame is not None:
            k = self._idx.select(ts, self.mapping)
            if k is None:
                raise MarketDataUnavailable(ts, request, "no snapshot")
            row = self.frame.iloc[k]
            return ToyPricer(self.frame.index[k], row["rate"], row.get("vol", 80.0), row.get("funding", 4.0))
        p = self.path(ts)
        return ToyPricer(ts, p["rate"], p.get("vol", 80.0), p.get("funding", 4.0))

    def available_timestamps(self, start: pd.Timestamp, end: pd.Timestamp):
        if self._idx is None:
            raise NotImplementedError
        return list(self._idx.within(start, end))


def years_between(a: pd.Timestamp, b: pd.Timestamp) -> float:
    return (b - a).total_seconds() / (365.25 * DAY)


@dataclass
class ToyForward:
    """Linear rate forward. pv = notional * (rate - strike) [percent points]; dv01 = notional*0.01 per bp; carry accrues `carry_bp_per_day` bp/day as CASH.

    Layers: carry (cash accrued), delta (dv01 * d rate in bp), convexity (0). engine `unexplained` = 0 by construction.
    """

    strike: float
    notional: float = 1.0
    carry_bp_per_day: float = 0.0
    asset_class: str = "toy_forward"

    def value(self, ctx: MarkContext) -> Valuation:
        px = ctx.pricer.level("rate")
        cash = 0.0
        if ctx.prev_ts is not None:
            days = (ctx.ts - ctx.prev_ts).total_seconds() / DAY
            cash = self.notional * self.carry_bp_per_day * 0.01 * days
        return Valuation(ctx.ts, self.notional * (px - self.strike), cash=cash)

    def dv01(self, ctx: MarkContext) -> float:
        return self.notional * 0.01

    def convexity(self, ctx: MarkContext) -> float:
        return 0.0

    def value_carry(self, ctx: MarkContext) -> float:
        return self.value(ctx).cash

    def value_delta(self, ctx: MarkContext) -> float:
        return self.notional * (ctx.pricer.level("rate") - ctx.prev_pricer.level("rate"))

    def value_convexity(self, ctx: MarkContext) -> float:
        return 0.0


@dataclass
class ToyZero:
    """Zero-coupon bond on a flat yield: pv = notional * exp(-y*T), T shrinking with time (natural roll/carry). Financing at pricer.funding on prior mark."""

    maturity: pd.Timestamp
    notional: float = 100.0
    asset_class: str = "toy_zero"

    def _pv(self, ts: pd.Timestamp, y_pct: float) -> float:
        T = max(years_between(ts, self.maturity), 0.0)
        return self.notional * math.exp(-y_pct / 100.0 * T)

    def value(self, ctx: MarkContext) -> Valuation:
        pv = self._pv(ctx.ts, ctx.pricer.level("rate"))
        fin = 0.0
        if ctx.prev_ts is not None:
            prev = self._pv(ctx.prev_ts, ctx.prev_pricer.level("rate"))
            fin = -prev * ctx.prev_pricer.level("funding") / 100.0 * years_between(ctx.prev_ts, ctx.ts)
        return Valuation(ctx.ts, pv, financing=fin)

    def dv01(self, ctx: MarkContext) -> float:
        T = years_between(ctx.ts, self.maturity)
        return -self._pv(ctx.ts, ctx.pricer.level("rate")) * T * 1e-4

    def convexity(self, ctx: MarkContext) -> float:
        T = years_between(ctx.ts, self.maturity)
        return self._pv(ctx.ts, ctx.pricer.level("rate")) * T * T * 1e-8

    def value_roll(self, ctx: MarkContext) -> float:
        """Time passes, yield unchanged."""
        y0 = ctx.prev_pricer.level("rate")
        return self._pv(ctx.ts, y0) - self._pv(ctx.prev_ts, y0)

    def value_delta(self, ctx: MarkContext) -> float:
        y0, y1 = ctx.prev_pricer.level("rate"), ctx.pricer.level("rate")
        T = years_between(ctx.ts, self.maturity)
        return -self._pv(ctx.ts, y0) * T * (y1 - y0) / 100.0

    def value_convexity(self, ctx: MarkContext) -> float:
        y0, y1 = ctx.prev_pricer.level("rate"), ctx.pricer.level("rate")
        T = years_between(ctx.ts, self.maturity)
        return 0.5 * self._pv(ctx.ts, y0) * T * T * ((y1 - y0) / 100.0) ** 2


@dataclass
class ToyOption:
    """Bachelier call/put on the rate (percent). pv = notional * price(F, K, vol, T) in percent points per unit; closed-form dv01 (delta per bp), gamma, vega.

    kind 'payer' = call on rate. `vol` from pricer.vol (bp). Long only when quantity > 0.
    """

    strike: float
    expiry: pd.Timestamp
    kind: str = "payer"
    notional: float = 1.0
    asset_class: str = "toy_option"

    def _greeks(self, ts: pd.Timestamp, F: float, volbp: float) -> Dict[str, float]:
        T = max(years_between(ts, self.expiry), 0.0)
        sgn = 1.0 if self.kind == "payer" else -1.0
        sig = volbp / 100.0  # percent per sqrt-year
        if T <= 0.0:
            intrinsic = max(sgn * (F - self.strike), 0.0)
            return {"pv": intrinsic, "delta": sgn * (1.0 if intrinsic > 0 else 0.0), "gamma": 0.0, "vega": 0.0, "theta": 0.0}
        s = sig * math.sqrt(T)
        d = sgn * (F - self.strike) / s
        pv = sgn * (F - self.strike) * _N.cdf(d) + s * _N.pdf(d)
        delta = sgn * _N.cdf(d)
        gamma = _N.pdf(d) / s
        vega = math.sqrt(T) * _N.pdf(d) / 100.0  # per +1 bp of vol -> percent pts... scaled per 1bp
        theta = -0.5 * sig * sig * gamma / 365.25
        return {"pv": pv, "delta": delta, "gamma": gamma, "vega": vega, "theta": theta}

    def value(self, ctx: MarkContext) -> Valuation:
        g = self._greeks(ctx.ts, ctx.pricer.level("rate"), ctx.pricer.level("vol"))
        cash = 0.0
        if ctx.prev_ts is not None and ctx.prev_ts < self.expiry <= ctx.ts:
            cash = 0.0  # cash-settled at expiry: pv already equals intrinsic; nothing extra to sweep
        return Valuation(ctx.ts, self.notional * g["pv"], cash=cash)

    def dv01(self, ctx: MarkContext) -> float:
        return self.notional * self._greeks(ctx.ts, ctx.pricer.level("rate"), ctx.pricer.level("vol"))["delta"] * 0.01

    def convexity(self, ctx: MarkContext) -> float:
        return self.notional * self._greeks(ctx.ts, ctx.pricer.level("rate"), ctx.pricer.level("vol"))["gamma"] * 1e-4

    def vega(self, ctx: MarkContext) -> float:
        return self.notional * self._greeks(ctx.ts, ctx.pricer.level("rate"), ctx.pricer.level("vol"))["vega"]

    def value_delta(self, ctx: MarkContext) -> float:
        g = self._greeks(ctx.prev_ts, ctx.prev_pricer.level("rate"), ctx.prev_pricer.level("vol"))
        return self.notional * g["delta"] * (ctx.pricer.level("rate") - ctx.prev_pricer.level("rate"))

    def value_convexity(self, ctx: MarkContext) -> float:
        g = self._greeks(ctx.prev_ts, ctx.prev_pricer.level("rate"), ctx.prev_pricer.level("vol"))
        d = ctx.pricer.level("rate") - ctx.prev_pricer.level("rate")
        return 0.5 * self.notional * g["gamma"] * d * d


@dataclass
class ToyFuture:
    """Exchange-traded future: pv = 0 always; variation margin is CASH: notional * (price - prev_price). Entry price = the mark at entry."""

    multiplier: float = 1.0
    asset_class: str = "toy_future"

    def value(self, ctx: MarkContext) -> Valuation:
        vm = 0.0
        if ctx.prev_pricer is not None:
            vm = self.multiplier * (ctx.pricer.level("rate") - ctx.prev_pricer.level("rate"))
        return Valuation(ctx.ts, 0.0, cash=vm)

    def dv01(self, ctx: MarkContext) -> float:
        return self.multiplier * 0.01

    def convexity(self, ctx: MarkContext) -> float:
        return 0.0


# ------------------------------------------------------------------------------ the toy stack as an adapter: schema, default bindings, kits
_U = "currency, per unit, over the interval"
TOY_SCHEMA: Mapping[str, Any] = {
    "schema_version": 1, "asset_class": "toy", "extends": "generic", "doc": "The toy market's instruments: every standard name optional, so a toy binds what it offers.",
    "measures": {
        "dv01": {"returns": "float", "unit": "currency per +1bp", "sign": "holder", "doc": "Toy dollar delta."},
        "gamma": {"returns": "float", "unit": "currency per bp^2", "sign": "holder", "doc": "Toy gamma."},
        "vega": {"returns": "float", "unit": "currency per vol point", "sign": "holder", "doc": "Toy vega."},
        "theta": {"returns": "float", "unit": "currency per day", "sign": "holder", "doc": "Toy theta."},
        "rate": {"returns": "float", "unit": "percent", "doc": "The toy market rate."},
        "delta_ladder": {"returns": "dict[str, float]", "unit": "currency per +1bp per bucket", "sign": "holder", "doc": "Toy bucketed delta."},
    },
    "layers": {n: {"id": f"toy.{n}", "version": 1, "returns": "float", "unit": _U, "doc": f"Toy {n} layer."} for n in ("carry", "roll", "delta", "convexity")},
}


def _m(name: str) -> Dict[str, Any]:
    return {"target": {"method": name}, "kwargs": {"ctx": "@ctx"}}


_NOTIONAL: Mapping[str, Any] = {"target": {"attribute": "notional"}}
_RATE: Mapping[str, Any] = {"target": {"pricer_method": "level"}, "kwargs": {"name": "rate"}}  # the toy's own market rate: the pricer's `rate` level, in percent
FORWARD_BIND: Mapping[str, Any] = {"notional": _NOTIONAL, "rate": _RATE, "value": _m("value"), "dv01": _m("dv01"), "gamma": _m("convexity"), "carry": _m("value_carry"), "delta": _m("value_delta"), "convexity": _m("value_convexity")}
ZERO_BIND: Mapping[str, Any] = {"notional": _NOTIONAL, "rate": _RATE, "value": _m("value"), "dv01": _m("dv01"), "gamma": _m("convexity"), "roll": _m("value_roll"), "delta": _m("value_delta"), "convexity": _m("value_convexity")}
OPTION_BIND: Mapping[str, Any] = {"notional": _NOTIONAL, "rate": _RATE, "value": _m("value"), "dv01": _m("dv01"), "gamma": _m("convexity"), "vega": _m("vega"), "delta": _m("value_delta"), "convexity": _m("value_convexity")}
FUTURE_BIND: Mapping[str, Any] = {"value": _m("value"), "dv01": _m("dv01"), "gamma": _m("convexity")}


@functools.lru_cache(maxsize=None)
def toy_schemas() -> SchemaRegistry:
    """The shipped schemas plus the toy extension schema (built once; a pure function of constants)."""
    return SchemaRegistry.default().extended(TOY_SCHEMA, "toy")


_DEFAULT_BIND = ((ToyForward, FORWARD_BIND), (ToyZero, ZERO_BIND), (ToyOption, OPTION_BIND), (ToyFuture, FUTURE_BIND))


def toy_template(obj: Any, *, name: Optional[str] = None, bind: Optional[Mapping[str, Any]] = None, layers: Sequence[str] = (), extra: Optional[Mapping[str, str]] = None,
                 resolved: Sequence[str] = (), pricer: str = "primary", pricers: Optional[Mapping[str, str]] = None, params: Optional[Mapping[str, Any]] = None) -> TradeTemplate:
    """A ready-made toy (or a subclass of one) as a TradeTemplate with the toy stack's default bindings; `bind` adds or overrides names, `extra` declares
    extension names (kind 'measure' or 'layer'), `resolved` exposes attributes as resolved terms (for `trade_duration` by name)."""
    default = next((b for cls, b in _DEFAULT_BIND if isinstance(obj, cls)), {})
    return TradeTemplate.wrap(obj, bind={**default, **dict(bind or {})}, schemas=toy_schemas(), name=name, asset_class="toy", schema=TOY_SCHEMA, layers=layers, extra=extra,
                              resolved=resolved, pricer=pricer, pricers=pricers, params=params)


class _ToyFactory:
    """Kit factory: the instrument's definition parameters arrive in the `conventions` block (a toy has no terms); timestamps are coerced and localised."""

    def __init__(self, cls: type):
        self.cls = cls

    def __call__(self, pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
        kw = dict(conventions)
        for k in ("maturity", "expiry"):
            if k in kw:
                t = pd.Timestamp(kw[k])
                kw[k] = t if t.tzinfo is not None else t.tz_localize(pd.Timestamp(ts).tz)
        return Built(self.cls(**kw), {})


def _kit(cls: type, bind: Mapping[str, Any]) -> Kit:
    return Kit(factory=_ToyFactory(cls), asset_class="toy", default_bind=bind, cls=cls, schema=TOY_SCHEMA, doc=f"toy {cls.__name__}")


forward = _kit(ToyForward, FORWARD_BIND)
zero = _kit(ToyZero, ZERO_BIND)
option = _kit(ToyOption, OPTION_BIND)
future = _kit(ToyFuture, FUTURE_BIND)
