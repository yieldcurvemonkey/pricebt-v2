"""The reference stack (spec A4): a dependency-free adapter written from first principles, used as the KNOWN ANSWER of the tie-out harness and as a third,
independent leg next to any pricing library. It needs numpy and pricebt only.

What it prices, from a plain-data `MarketSnapshot` (`pricebt.snapshot`), exactly as the shared conventions block says (the block a library adapter gets):

* an overnight-indexed swap (`swap` kit): schedule, business-day arithmetic, daily-compounded floating leg over published fixings, PV on a log-linear
  discount curve, par rate, `dv01`, `gamma`, the bucketed `delta_ladder` on a bootstrapped par-swap risk curve, and the four P&L layers by scenario curves;
* a fixed-coupon bond (`bond` kit): schedule, yield <-> price under the treasury rule (simple interest in the first fractional period, then compounded), risk,
  convexity, coupon sweep, repo financing and the yield-space layers.

Every number is a closed form or a bootstrap over the snapshot: with the shipped definitions each formula can be re-derived by hand (see `tests/test_refstack.py`,
which does exactly that, and the harness self-test X5c). This module is an adapter that happens to need no library, so it may name the conventions its shared
block names (guard `convention_allow`); it registers nothing, holds no global state and imports no pricing library.

    instruments: {swap_ref: {factory: "pricebt.testing.refstack:swap", conventions: {...}}}
    market: {pricers: {primary: {mdp: ..., wrap: "pricebt.testing.refstack:wrap"}}}
"""
from __future__ import annotations

import bisect
import calendar as _calendar
import datetime as dt
import math
import re
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from ..contracts.schema import SchemaRegistry
from ..contracts.spec import Built, Kit, Stack
from ..errors import ConfigError, MarketDataUnavailable
from ..pricable import MarkContext, Valuation
from ..pricer import PricerBase
from ..snapshot import SnapshotPricer
from ..timeutil import Calendar

DEFAULT_TENORS: Tuple[str, ...] = ("3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y")
LADDER_BUMP = 0.5e-4  # decimal, per pillar, central
PARALLEL_BUMP = 1.0e-4  # decimal, all pillars, central (gamma)
_TAU_DAYS = 365.0  # the zero rate of the layer scenarios: continuously compounded, actual days over 365
_H = 0.1  # fraction of the realised move used for the directional derivatives of the layers

# The shared conventions blocks WITHOUT `calendar`: a calendar is a NAME in the snapshot's data (the provider owns it), so the caller adds it,
# for example `{**USD_SOFR_OIS_CONVENTIONS, "calendar": "<a calendar of the snapshot>"}`.
USD_SOFR_OIS_CONVENTIONS: Mapping[str, Any] = {
    "spot_lag_days": 2, "day_count": "act360", "frequency": "annual", "business_day_convention": "modified_following", "payment_lag_days": 2,
    "compounding": "daily_compounded", "stub": "short_front", "end_of_month": False, "fixing_lag_days": 1, "time_accrual": "lump",
}
UST_CONVENTIONS: Mapping[str, Any] = {
    "settlement_lag_days": 0, "day_count": "actact_icma", "frequency": "semiannual", "business_day_convention": "unadjusted",
    "payment_business_day_convention": "following", "payment_lag_days": 0, "stub": "short_front", "end_of_month": True, "compounding": "semiannual",
    "yield_convention": "treasury", "ex_dividend_days": 0, "financing_day_count": "act360", "time_accrual": "lump",
}

_FREQ_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
_BASIS = {"act360": 360.0, "act365f": 365.0}
_SWAP_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "spot_lag_days": "*", "payment_lag_days": "*", "day_count": ("act360", "act365f"), "frequency": tuple(_FREQ_MONTHS),
    "business_day_convention": ("unadjusted", "following", "modified_following", "preceding"), "compounding": ("daily_compounded",), "stub": ("short_front",),
    "end_of_month": (False,), "fixing_lag_days": (1,), "time_accrual": ("lump",),
}
_BOND_SUPPORTED: Dict[str, Any] = {
    "calendar": "*", "settlement_lag_days": (0,), "day_count": ("actact_icma",), "frequency": ("semiannual",), "business_day_convention": ("unadjusted",),
    "payment_business_day_convention": ("following",), "payment_lag_days": (0,), "stub": ("short_front",), "end_of_month": (True,), "compounding": ("semiannual",),
    "yield_convention": ("treasury",), "ex_dividend_days": (0,), "financing_day_count": ("act360",), "time_accrual": ("lump",),
}
_SCHEMAS = SchemaRegistry.default()


def _conventions(asset_class: str, raw: Mapping[str, Any], supported: Mapping[str, Any]) -> Dict[str, Any]:
    """The shared block, validated by the schema vocabulary (unknown key, wrong type, unknown token, missing key) and then against what this stack honours."""
    block = _SCHEMAS.get(asset_class).check_conventions(raw, require_all=True)
    for key, v in block.items():
        ok = supported[key]
        if ok != "*" and v not in ok:
            raise ConfigError(f"convention {key}={v!r} is in the vocabulary but the reference stack does not implement it; it implements {list(ok)}", code="CFG-CONVENTION-UNSUPPORTED")
    return block


# ------------------------------------------------------------------------------ dates
def add_months(d: dt.date, n: int, roll: int) -> dt.date:
    """`d` plus `n` months on day-of-month `roll`, clamped to the month's last day."""
    y, m0 = divmod(d.year * 12 + d.month - 1 + n, 12)
    m = m0 + 1
    return dt.date(y, m, min(roll, _calendar.monthrange(y, m)[1]))


def tenor_months(tenor: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*([YyMm])\s*", str(tenor))
    if not m:
        raise ConfigError(f"tenor {tenor!r}: whole months or years only (for example '18M', '10Y')", code="TENOR")
    return int(m.group(1)) * (12 if m.group(2) in "Yy" else 1)


def _unadjusted_dates(effective: dt.date, end: dt.date, roll: int, step: int) -> Tuple[dt.date, ...]:
    """Backward from `end` in `step`-month strides on day `roll`, stopping before `effective`: a short front stub falls out."""
    dates, k = [end], 1
    while True:
        prev = add_months(end, -step * k, roll)
        if prev <= effective:
            break
        dates.append(prev)
        k += 1
    dates.append(effective)
    return tuple(reversed(dates))


@dataclass(frozen=True)
class Schedule:
    """Accrual periods of a swap: `unadjusted` dates and, per period, the adjusted start / end, the payment date and the accrual fraction."""

    unadjusted: Tuple[dt.date, ...]
    starts: Tuple[dt.date, ...]
    ends: Tuple[dt.date, ...]
    pays: Tuple[dt.date, ...]
    taus: Tuple[float, ...]


def build_schedule(effective: dt.date, maturity: Any, cal: Calendar, *, freq_months: int, bdc: str, pay_lag: int, basis: float) -> Schedule:
    """Swap schedule. `maturity` is a tenor ('10Y', '18M': end = effective + n months on the effective day-of-month, short front stub) or a date (the unadjusted end is
    the effective's day in the maturity's month when that adjusts to the date and is a whole number of periods away, else the date itself with its own roll)."""
    roll = effective.day
    if isinstance(maturity, str):
        end = add_months(effective, tenor_months(maturity), roll)
        r = roll
    else:
        md = maturity.date() if isinstance(maturity, dt.datetime) else maturity
        cand = add_months(dt.date(md.year, md.month, 1), 0, roll)
        months = (md.year - effective.year) * 12 + md.month - effective.month
        if cal.adjust(cand, bdc) == md and months % freq_months == 0:
            end, r = cand, roll
        else:
            end, r = md, md.day
    unadj = _unadjusted_dates(effective, end, r, freq_months)
    adj = [cal.adjust(d, bdc) for d in unadj]
    starts, ends = tuple(adj[:-1]), tuple(adj[1:])
    pays = tuple(cal.add_business_days(e, pay_lag) for e in ends)
    return Schedule(unadj, starts, ends, pays, tuple((e - s).days / basis for s, e in zip(starts, ends)))


# ------------------------------------------------------------------------------ the curve
class RefCurve:
    """Discount factors at node dates, log-linear in calendar time between nodes and flat-forward beyond the last (spec D1 tag `log_linear_df`)."""

    def __init__(self, reference_date: dt.date, dates: Sequence[dt.date], dfs: Sequence[float]):
        if len(dates) < 2 or len(dates) != len(dfs):
            raise ConfigError("a curve needs >= 2 nodes and one discount factor per node", code="REFSTACK")
        x = np.array([d.toordinal() for d in dates], dtype=float)
        y = np.log(np.array(dfs, dtype=float))
        if dates[0] != reference_date or (np.diff(x) <= 0).any() or not np.isfinite(y).all():
            raise ConfigError("curve nodes must start at the reference date, ascend strictly and carry positive finite discount factors", code="REFSTACK")
        self.reference_date, self.dates, self.dfs = reference_date, tuple(dates), tuple(float(v) for v in dfs)
        self._x, self._y = x, y
        self._slope = float((y[-1] - y[-2]) / (x[-1] - x[-2]))

    def logdf(self, ordinals: Any) -> np.ndarray:
        xs = np.asarray(ordinals, dtype=float)
        if xs.size and xs.min() < self._x[0]:
            raise ConfigError(f"a discount factor was asked for a date before the curve's reference date {self.reference_date}", code="REFSTACK")
        out = np.interp(xs, self._x, self._y)
        return np.where(xs > self._x[-1], self._y[-1] + self._slope * (xs - self._x[-1]), out)

    def df(self, d: dt.date) -> float:
        return float(np.exp(self.logdf(np.array([d.toordinal()], dtype=float))[0]))

    def dfs_at(self, ordinals: Any) -> np.ndarray:
        return np.exp(self.logdf(ordinals))


# ------------------------------------------------------------------------------ the pricer
class RefPricer(PricerBase):
    """A snapshot as the reference stack sees it: the discount curve, published fixings (percent), calendars and the quote panel, all as plain attributes."""

    LOOKUPS = ()

    def __init__(self, sp: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None):
        snap = sp.snapshot
        self.snapshot, self.digest, self.ts, self.reference_date = snap, sp.digest, sp.ts, snap.reference_date
        cs = _pick(snap.curves, curve, "curve")
        fs = _pick(snap.fixings, fixings, "fixings")
        self.curve: Optional[RefCurve] = None if cs is None else RefCurve(cs.reference_date, cs.node_dates, cs.values)
        scale = 1.0 if fs is None or fs.unit == "percent" else 100.0
        self.fixings: Dict[dt.date, float] = {} if fs is None else {d: v * scale for d, v in zip(fs.dates, fs.values)}
        self.quotes = snap.quotes
        self._calendars: Dict[str, Calendar] = {}
        self._memo: Dict[Any, Any] = {}

    def calendar(self, name: str) -> Calendar:
        cal = self._calendars.get(name)
        if cal is None:
            cd = self.snapshot.calendars.get(name)
            if cd is None:
                raise ConfigError(f"the snapshot has no calendar {name!r}; it has {sorted(self.snapshot.calendars)}", code="CFG-CONVENTION")
            cal = self._calendars[name] = Calendar(cd.holidays, cd.weekmask, cd.name)
        return cal

    def memo(self, key: Any, fn: Callable[[], Any]) -> Any:
        """Per-snapshot cache of derived objects (risk curves, fixed parts of started periods): a pricer is immutable data, so this can never go stale."""
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def describe(self) -> Mapping[str, Any]:
        return {"type": type(self).__name__, "ts": str(self.ts), "reference_date": str(self.reference_date), "digest": self.digest, "n_nodes": 0 if self.curve is None else len(self.curve.dates),
                "n_fixings": len(self.fixings), "n_quotes": 0 if self.quotes is None else len(self.quotes.quotes)}

    def need_curve(self) -> RefCurve:
        if self.curve is None:
            raise MarketDataUnavailable(self.ts, {}, "this snapshot carries no discount curve")
        return self.curve


def _pick(section: Mapping[str, Any], name: Optional[str], what: str) -> Any:
    if name is not None:
        if name not in section:
            raise ConfigError(f"the snapshot has no {what} {name!r}; it has {sorted(section)}", code="WRAP")
        return section[name]
    if len(section) > 1:
        raise ConfigError(f"the snapshot holds several {what}s {sorted(section)}: name one", code="WRAP")
    return next(iter(section.values()), None)


_WRAPPED: "weakref.WeakKeyDictionary[SnapshotPricer, Dict[Tuple[Optional[str], Optional[str]], RefPricer]]" = weakref.WeakKeyDictionary()


def wrap(pricer: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None) -> RefPricer:
    """The reference pricer of a snapshot pricer, built once per snapshot pricer (`curve` / `fixings` name the entry when the snapshot holds several)."""
    memo = _WRAPPED.setdefault(pricer, {})
    key = (curve, fixings)
    if key not in memo:
        memo[key] = RefPricer(pricer, curve=curve, fixings=fixings)
    return memo[key]


def _ref(p: Any) -> RefPricer:
    if not isinstance(p, RefPricer):
        raise ConfigError(f"the market pricer is a {type(p).__name__}, not a RefPricer: set `wrap` on the pricer role to pricebt.testing.refstack:wrap", code="CFG-WRAP")
    return p


# ------------------------------------------------------------------------------ the swap
@dataclass(frozen=True)
class SwapConv:
    calendar: str
    spot_lag_days: int
    day_count: str
    frequency: str
    business_day_convention: str
    payment_lag_days: int

    @property
    def freq_months(self) -> int:
        return _FREQ_MONTHS[self.frequency]

    @property
    def basis(self) -> float:
        return _BASIS[self.day_count]


def swap_conventions(raw: Mapping[str, Any]) -> SwapConv:
    b = _conventions("swap", raw, _SWAP_SUPPORTED)
    return SwapConv(b["calendar"], b["spot_lag_days"], b["day_count"], b["frequency"], b["business_day_convention"], b["payment_lag_days"])


def _resolve_effective(spec: Any, ref: dt.date, cal: Calendar, conv: SwapConv) -> dt.date:
    spot = cal.add_business_days(ref, conv.spot_lag_days)
    if isinstance(spec, str):
        s = spec.strip().lower()
        if s == "spot":
            return spot
        m = re.fullmatch(r"(\d+)([ymwd])", s)
        if not m:
            raise ConfigError(f"effective {spec!r}: use 'spot', a forward tenor such as '1y', '6m', '2w' or a date", code="TERMS")
        n, unit = int(m.group(1)), m.group(2)
        if unit in "ym":
            fwd = add_months(spot, n * (12 if unit == "y" else 1), spot.day)
        else:
            fwd = spot + dt.timedelta(days=n * (7 if unit == "w" else 1))
        return cal.adjust(fwd, conv.business_day_convention)
    return cal.adjust(spec.date() if isinstance(spec, dt.datetime) else spec, conv.business_day_convention)


class RefSwap:
    """One unit = one swap of `notional` (unsigned); `sign` +1 pays fixed (positive dv01). Plain data: dates, a percent rate, validated conventions."""

    asset_class = "swap"

    def __init__(self, *, effective: dt.date, schedule: Schedule, sign: int, notional: float, fixed_rate: float, conv: SwapConv):
        if sign not in (1, -1):
            raise ConfigError(f"sign must be +1 (pay fixed) or -1 (receive fixed), got {sign!r}", code="SWAP")
        if not notional > 0:
            raise ConfigError("notional is unsigned and must be > 0 (direction is `side`)", code="SWAP")
        self.effective, self.schedule, self.sign, self.notional, self.fixed_rate, self.conv = effective, schedule, int(sign), float(notional), float(fixed_rate), conv
        self.maturity = schedule.ends[-1]
        self._a = np.array([d.toordinal() for d in schedule.starts], dtype=float)
        self._e = np.array([d.toordinal() for d in schedule.ends], dtype=float)
        self._p = np.array([d.toordinal() for d in schedule.pays], dtype=float)
        self._tau = np.array(schedule.taus, dtype=float)

    @property
    def notional_amount(self) -> float:
        return self.notional

    # ---- state
    def started(self, ref: dt.date) -> bool:
        return self.effective < ref

    def matured(self, ref: dt.date) -> bool:
        return self.schedule.pays[-1] < ref

    # ---- the floating leg over fixings
    def _check_fixings(self, p: RefPricer, ref: dt.date) -> None:
        """Every business day from the effective date to the day before `ref` needs a published fixing, else the swap cannot be marked (no silent proxying)."""
        def check() -> None:
            cal = p.calendar(self.conv.calendar)
            first = cal.following(self.effective)
            last = cal.add_business_days(ref, -1)
            if last < first:
                return
            missing = [d for d in cal.business_days(first, last) if d not in p.fixings]
            if missing:
                raise MarketDataUnavailable(p.ts, {}, f"{len(missing)} overnight fixings missing between {first} and {last}; first {missing[0]}")

        if not p.fixings:
            raise MarketDataUnavailable(p.ts, {}, f"swap started {self.effective} but the pricer carries no fixings")
        p.memo(("fixings_ok", id(p.fixings), self.conv.calendar, self.effective, ref), check)

    def _fixed_part(self, p: RefPricer, i: int, ref: dt.date) -> Tuple[float, dt.date]:
        """(compounded growth over the business days of period `i` that are before `ref`, the first day not yet fixed): the observed part of the coupon."""
        def compute() -> Tuple[float, dt.date]:
            cal = p.calendar(self.conv.calendar)
            a, e = self.schedule.starts[i], self.schedule.ends[i]
            stop = min(e, ref)
            g, d = 1.0, a
            while d < stop:
                nxt = min(cal.add_business_days(d, 1), e)
                g *= 1.0 + p.fixings[d] / 100.0 * (nxt - d).days / self.conv.basis
                d = nxt
            return g, d

        return p.memo(("fixed_part", id(self), i, ref), compute)

    def _growth(self, p: RefPricer, curve: RefCurve, ref: dt.date) -> np.ndarray:
        """Compounded growth of every floating period as of `ref`: observed fixings up to `ref`, the curve's forward beyond."""
        ref_o = float(ref.toordinal())
        g = np.ones(len(self._a))
        fut = self._a >= ref_o
        if fut.any():
            g[fut] = curve.dfs_at(self._a[fut]) / curve.dfs_at(self._e[fut])
        started = np.nonzero(~fut)[0]
        if started.size:
            self._check_fixings(p, ref)
        for i in started:
            gf, d = self._fixed_part(p, int(i), ref)
            g[i] = gf if self._e[i] <= ref_o else gf * curve.df(d) / curve.df(self.schedule.ends[int(i)])
        return g

    def _amounts(self, p: RefPricer, curve: RefCurve, ref: dt.date) -> np.ndarray:
        """Holder-signed amount of each period's exchange (floating received minus fixed paid for a payer)."""
        return self.sign * self.notional * (self._growth(p, curve, ref) - 1.0 - self.fixed_rate / 100.0 * self._tau)

    def npv(self, p: RefPricer, curve: RefCurve, ref: dt.date) -> float:
        """PV at `ref` of the flows paid on or after it (a flow paid ON `ref` is inside the value and swept as cash the next day)."""
        if self.matured(ref):
            return 0.0
        live = self._p >= ref.toordinal()
        return float((self._amounts(p, curve, ref)[live] * curve.dfs_at(self._p[live])).sum())

    def paid_between(self, p: RefPricer, curve: RefCurve, ref: dt.date, t0: dt.date) -> float:
        """Holder-signed amounts of the flows paid in [t0, ref) (known amounts: their periods ended before `ref`)."""
        window = (self._p >= t0.toordinal()) & (self._p < ref.toordinal())
        return float(self._amounts(p, curve, ref)[window].sum()) if window.any() else 0.0

    def par_rate(self, p: RefPricer, curve: RefCurve, ref: dt.date) -> float:
        """The fixed rate (percent) that makes the remaining swap worth zero."""
        live = self._p >= ref.toordinal()
        df_p = curve.dfs_at(self._p[live])
        float_leg = (self._growth(p, curve, ref)[live] - 1.0) * df_p
        return float(100.0 * float_leg.sum() / (self._tau[live] * df_p).sum())

    def annuity_dv01(self, curve: RefCurve, ref: dt.date) -> float:
        """PV of one basis point on the fixed leg, holder-signed: the analytic dv01 of a swap that has not started."""
        live = self._p >= ref.toordinal()
        return float(self.sign * self.notional * (self._tau[live] * curve.dfs_at(self._p[live])).sum() * 1e-4)

    # ---- value
    def value(self, *, ctx: MarkContext) -> Valuation:
        p, prev = _ref(ctx.pricer), ctx.prev_pricer
        ref, curve = p.reference_date, p.need_curve()
        cash = 0.0
        if prev is not None and prev.reference_date < ref:
            cash = self.paid_between(p, curve, ref, prev.reference_date)
        return Valuation(ctx.ts, self.npv(p, curve, ref), cash, 0.0, {"reference_date": ref})

    # ---- measures
    def rate(self, *, ctx: MarkContext) -> float:
        p = _ref(ctx.pricer)
        return float("nan") if self.matured(p.reference_date) else self.par_rate(p, p.need_curve(), p.reference_date)

    def dv01(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        p = _ref(ctx.pricer)
        ref = p.reference_date
        if self.matured(ref):
            return 0.0
        if self.started(ref):  # the analytic figure keeps the whole accrued coupon, which no longer moves with rates: the market dv01 is the ladder's sum
            return float(sum(self.delta_ladder(ctx=ctx, tenors=tenors).values()))
        return self.annuity_dv01(p.need_curve(), ref)

    def delta_ladder(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> Dict[str, float]:
        p = _ref(ctx.pricer)
        rc = _risk_curves(p, self.conv, tenors)
        key = ("ref_swap_ladder", id(self), rc.tenors)
        if key in ctx.cache:
            return dict(ctx.cache[key])
        ref = p.reference_date
        if self.matured(ref):
            out = {t: 0.0 for t in rc.tenors}
        else:
            out = {t: (self.npv(p, up, ref) - self.npv(p, dn, ref)) / (2.0 * LADDER_BUMP / 1e-4) for t, up, dn in zip(rc.tenors, rc.ups, rc.dns)}
        ctx.cache[key] = out
        return dict(out)

    def gamma(self, *, ctx: MarkContext, tenors: Sequence[str] = DEFAULT_TENORS) -> float:
        p = _ref(ctx.pricer)
        ref = p.reference_date
        if self.matured(ref):
            return 0.0
        rc = _risk_curves(p, self.conv, tenors)
        return (self.npv(p, rc.parallel_up, ref) + self.npv(p, rc.parallel_dn, ref) - 2.0 * self.npv(p, rc.base, ref)) / (PARALLEL_BUMP / 1e-4) ** 2

    def _zero_shift(self, ctx: MarkContext) -> Tuple[float, float]:
        p = _ref(ctx.pricer)
        key = ("ref_swap_zero", id(self))
        if key in ctx.cache:
            return ctx.cache[key]
        ref = p.reference_date
        if self.matured(ref):
            out = (0.0, 0.0)
        else:
            curve = p.need_curve()
            v0 = self.npv(p, curve, ref)
            v = {bp: self.npv(p, _zero_shifted(curve, bp * 1e-4), ref) for bp in (0.5, -0.5, 1.0, -1.0)}
            out = ((v[0.5] - v[-0.5]) / 1.0, v[1.0] - 2.0 * v0 + v[-1.0])
        ctx.cache[key] = out
        return out

    def dv01_zero(self, *, ctx: MarkContext) -> float:
        """PV change for +1bp of the continuously-compounded ZERO rates of the dense curve (central difference of full revaluations)."""
        return self._zero_shift(ctx)[0]

    def gamma_zero(self, *, ctx: MarkContext) -> float:
        """d2 PV / dbp^2 for the same parallel zero-rate move."""
        return self._zero_shift(ctx)[1]

    # ---- layers
    def decomposition(self, ctx: MarkContext) -> Dict[str, float]:
        """carry, roll, delta, convexity and `residual` (what the engine reports as `unexplained`), per unit, plus V0, V1 and the cash paid in the interval."""
        key = ("ref_swap_decomposition", id(self))
        if key in ctx.cache:
            return ctx.cache[key]
        p1, p0 = _ref(ctx.pricer), ctx.prev_pricer
        if p0 is None:
            raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
        p0 = _ref(p0)
        t0, t1 = p0.reference_date, p1.reference_date
        if t1 < t0:
            raise ValueError(f"the layers need t1 >= t0, got {t0} -> {t1}")
        if self.schedule.pays[-1] < t0:  # nothing left at t0: nothing moves
            out = {k: 0.0 for k in ("carry", "roll", "delta", "convexity", "residual", "V0", "V1", "cash")}
            ctx.cache[key] = out
            return out
        c0, c1 = p0.need_curve(), p1.need_curve()
        v0 = self.npv(p0, c0, t0)
        # carry: the t0 world's flows valued at t1 (flows paid in [t0, t1) at face)
        amt0 = self._amounts(p0, c0, t0)
        pay_o = self._p
        d1 = c0.df(t1)
        live0 = pay_o >= t0.toordinal()
        fwd = np.ones(len(pay_o))
        future = pay_o >= t1.toordinal()  # a discount factor is asked only for dates on or after the curve's reference date (np.where evaluates both branches)
        fwd[future] = c0.dfs_at(pay_o[future]) / d1
        x_fwd = float((amt0[live0] * fwd[live0]).sum())
        v1 = self.npv(p1, c1, t1)
        cash = self.paid_between(p1, c1, t1, t0)
        # roll: the curve unchanged in TENOR space, anchored at t1 (DF(t1 + tau) = DF0(t0 + tau)), with the realised fixings, plus the cash paid
        n = (t1 - t0).days
        nodes = [(t1, 1.0)] + [(d + dt.timedelta(days=n), v) for d, v in zip(c0.dates, c0.dfs) if d > t0]
        c_roll = RefCurve(t1, [d for d, _ in nodes], [v for _, v in nodes])
        x_roll = self.npv(p1, c_roll, t1) + cash
        # delta and convexity: directional derivatives along the realised move of the zero rates at the t1 curve's nodes, from the rolled curve resampled there
        dates1 = c1.dates
        base_dfs = [float(v) for v in c_roll.dfs_at(np.array([d.toordinal() for d in dates1], dtype=float))]
        tau = np.array([(d - t1).days / _TAU_DAYS for d in dates1[1:]])
        z1 = -np.log(np.array(c1.dfs[1:])) / tau
        zb = -np.log(np.array(base_dfs[1:])) / tau
        dz = z1 - zb

        def v_eps(eps: float) -> float:
            dfs = [1.0] + list(np.array(base_dfs[1:]) * np.exp(-eps * dz * tau))
            return self.npv(p1, RefCurve(t1, dates1, dfs), t1)

        v_base = v_eps(0.0)
        first = second = 0.0
        d_a, d_b = [(v_eps(h) - v_eps(-h)) / (2 * h) for h in (_H, _H / 2)]
        s_a, s_b = [(v_eps(h) + v_eps(-h) - 2.0 * v_base) / (2 * h * h) for h in (_H, _H / 2)]
        first = (4.0 * d_b - d_a) / 3.0  # Richardson: the directional derivatives are smooth, so this is exact to ~1e-10
        second = (4.0 * s_b - s_a) / 3.0
        out = {
            "carry": x_fwd - v0, "roll": x_roll - x_fwd, "delta": (v_base + cash - x_roll) + first, "convexity": second,
            "residual": (v1 - v_base) - first - second, "V0": v0, "V1": v1, "cash": cash,
        }
        ctx.cache[key] = out
        return out

    def carry(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx)["carry"]

    def roll(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx)["roll"]

    def delta(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx)["delta"]

    def convexity(self, *, ctx: MarkContext) -> float:
        return self.decomposition(ctx)["convexity"]


def _zero_shifted(curve: RefCurve, shift: float) -> RefCurve:
    """The curve with every continuously-compounded zero rate moved by `shift` (decimal)."""
    ref = curve.reference_date
    return RefCurve(ref, curve.dates, [v * math.exp(-shift * (d - ref).days / _TAU_DAYS) for d, v in zip(curve.dates, curve.dfs)])


# ------------------------------------------------------------------------------ the par-swap risk curve (delta ladder, dv01 of a started swap, gamma)
@dataclass(frozen=True)
class RiskCurves:
    tenors: Tuple[str, ...]
    terms: Tuple[dt.date, ...]
    pars: Tuple[float, ...]  # decimals
    base: RefCurve
    ups: Tuple[RefCurve, ...]
    dns: Tuple[RefCurve, ...]
    parallel_up: RefCurve
    parallel_dn: RefCurve


def clean_tenors(tenors: Sequence[str]) -> Tuple[str, ...]:
    out = tuple(str(t).strip().upper() for t in tenors)
    if not out:
        raise ConfigError("a delta ladder needs at least one tenor", code="LADDER")
    for t in out:
        tenor_months(t)
    if len(set(out)) != len(out):
        raise ConfigError(f"ladder tenors {list(out)} contain duplicates", code="LADDER")
    return out


def _pillar_pv(sched_arrays: Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], curve: RefCurve, par: float) -> float:
    """PV of a spot-start pillar swap (fixed at `par`, unit notional, payer) on `curve`: nothing has started, so every floating period grows by DF(start)/DF(end)."""
    a, e, p, tau = sched_arrays
    return float(((curve.dfs_at(a) / curve.dfs_at(e) - 1.0 - par * tau) * curve.dfs_at(p)).sum())


def _newton_node(f: Callable[[float], float], x0: float) -> Optional[float]:
    """Newton's method with a finite-difference slope from the seed `x0`; None when it does not converge (the caller then bisects)."""
    x, fx = x0, f(x0)
    for _ in range(30):
        d = (f(x + 1e-7) - fx) / 1e-7
        if not math.isfinite(d) or d == 0.0:
            return None
        nx = x - fx / d
        if not math.isfinite(nx) or abs(nx - x0) > 1.0:
            return None
        if abs(nx - x) < 1e-15:
            return nx
        x, fx = nx, f(nx)
    return None


def _bisect_node(f: Callable[[float], float], x0: float) -> float:
    """The root of the (monotone) `f` near `x0`, by bracketing then bisection to machine precision."""
    lo, hi = x0 - 0.05, x0 + 0.05
    flo, fhi = f(lo), f(hi)
    k = 0
    while flo * fhi > 0 and k < 40:
        lo, hi = lo - 0.05 * 2 ** k, hi + 0.05 * 2 ** k
        flo, fhi = f(lo), f(hi)
        k += 1
    if flo * fhi > 0:
        raise ConfigError("the risk curve bootstrap could not bracket a discount factor; the snapshot's curve may be degenerate", code="LADDER")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if fm == 0.0 or hi - lo < 1e-15:
            return mid
        if fm * flo > 0:
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _solve_node(f: Callable[[float], float], x0: float) -> float:
    """The root of the (monotone) `f` near `x0`: Newton from the seed (4-6 evaluations), bisection if that fails."""
    root = _newton_node(f, x0)
    return root if root is not None else _bisect_node(f, x0)


def _bootstrap(ref: dt.date, terms: Sequence[dt.date], pillars: Sequence[Tuple[np.ndarray, ...]], pars: Sequence[float], seed_dfs: Sequence[float], *, keep: Optional[Sequence[float]] = None) -> RefCurve:
    """Node discount factors at the pillar terminations, solved one pillar at a time so each spot-start par swap reprices at its par rate. `keep` supplies already
    solved log-discount-factors for the first pillars (a bumped pillar changes only the nodes from itself onward)."""
    dates = [ref] + list(terms)
    logs = [0.0] + list(keep or [])
    for k in range(len(keep or []), len(terms)):
        def f(x: float, k: int = k) -> float:
            return _pillar_pv(pillars[k], RefCurve(ref, dates[: k + 2], [math.exp(v) for v in (logs + [x])]), pars[k])

        logs.append(_solve_node(f, math.log(seed_dfs[k])))
    return RefCurve(ref, dates, [math.exp(v) for v in logs])


def _risk_curves(p: RefPricer, conv: SwapConv, tenors: Sequence[str]) -> RiskCurves:
    t = clean_tenors(tenors)
    return p.memo(("risk", conv, t), lambda: _build_risk_curves(p, conv, t))


def _build_risk_curves(p: RefPricer, conv: SwapConv, tenors: Tuple[str, ...]) -> RiskCurves:
    ref, dense, cal = p.reference_date, p.need_curve(), p.calendar(conv.calendar)
    spot = cal.add_business_days(ref, conv.spot_lag_days)
    pillars, terms, pars = [], [], []
    for tenor in tenors:
        s = build_schedule(spot, tenor, cal, freq_months=conv.freq_months, bdc=conv.business_day_convention, pay_lag=conv.payment_lag_days, basis=conv.basis)
        arr = (np.array([d.toordinal() for d in s.starts], dtype=float), np.array([d.toordinal() for d in s.ends], dtype=float),
               np.array([d.toordinal() for d in s.pays], dtype=float), np.array(s.taus, dtype=float))
        float_leg = (dense.dfs_at(arr[0]) / dense.dfs_at(arr[1]) - 1.0) * dense.dfs_at(arr[2])
        pars.append(float(float_leg.sum() / (arr[3] * dense.dfs_at(arr[2])).sum()))
        pillars.append(arr)
        terms.append(s.ends[-1])
    if len(set(terms)) != len(terms) or list(terms) != sorted(terms):
        raise ConfigError(f"ladder tenors {list(tenors)} map to duplicate or unordered maturities; use distinct, increasing tenors", code="LADDER")
    seed = [dense.df(d) for d in terms]
    base = _bootstrap(ref, terms, pillars, pars, seed)
    base_logs = [math.log(v) for v in base.dfs[1:]]
    ups, dns = [], []
    for j in range(len(tenors)):
        for sgn, bag in ((+1, ups), (-1, dns)):
            bumped = list(pars)
            bumped[j] += sgn * LADDER_BUMP
            bag.append(_bootstrap(ref, terms, pillars, bumped, seed, keep=base_logs[:j]))
    par_up = _bootstrap(ref, terms, pillars, [x + PARALLEL_BUMP for x in pars], seed)
    par_dn = _bootstrap(ref, terms, pillars, [x - PARALLEL_BUMP for x in pars], seed)
    return RiskCurves(tenors, tuple(terms), tuple(pars), base, tuple(ups), tuple(dns), par_up, par_dn)


# ------------------------------------------------------------------------------ the swap factory and kit
def _bind(method: str, **extra: Any) -> Dict[str, Any]:
    return {"target": {"method": method}, "kwargs": {"ctx": "@ctx", **extra}}


SWAP_BIND: Dict[str, Any] = {
    "value": _bind("value"),
    "dv01": _bind("dv01", tenors=list(DEFAULT_TENORS)),
    "gamma": _bind("gamma", tenors=list(DEFAULT_TENORS)),
    "rate": _bind("rate"),
    "delta_ladder": {**_bind("delta_ladder", tenors=list(DEFAULT_TENORS)), "keys": list(DEFAULT_TENORS), "reduce": "dict_of_floats"},
    "carry": _bind("carry"),
    "roll": _bind("roll"),
    "delta": _bind("delta"),
    "convexity": _bind("convexity"),
    "dv01_zero": _bind("dv01_zero"),
    "gamma_zero": _bind("gamma_zero"),
}


def swap_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    """`(pricer, ts, *, terms, conventions) -> Built`: resolves the dates and `par` on the snapshot and returns what it resolved (spec T4)."""
    p = _ref(pricer)
    conv = swap_conventions(conventions)
    cal = p.calendar(conv.calendar)
    extras = dict(terms.get("extras") or {})
    unknown = sorted(set(extras) - {"par_spread_bp"})
    if unknown:
        raise ConfigError(f"the reference stack does not understand the swap extras {unknown}; it understands ['par_spread_bp']", code="CFG-EXTRAS")
    eff = _resolve_effective(terms.get("effective", "spot"), p.reference_date, cal, conv)
    sched = build_schedule(eff, terms["maturity"], cal, freq_months=conv.freq_months, bdc=conv.business_day_convention, pay_lag=conv.payment_lag_days, basis=conv.basis)
    rate = terms.get("fixed_rate", "par")
    sign = int(terms["direction"])
    if isinstance(rate, str):
        if rate != "par":
            raise ConfigError(f"fixed_rate {rate!r}: a number (percent) or the token 'par'", code="TERMS")
        probe = RefSwap(effective=eff, schedule=sched, sign=sign, notional=1.0, fixed_rate=0.0, conv=conv)
        rate = probe.par_rate(p, p.need_curve(), p.reference_date) + float(extras.get("par_spread_bp", 0.0)) / 100.0
    swap = RefSwap(effective=eff, schedule=sched, sign=sign, notional=float(terms["notional"]), fixed_rate=float(rate), conv=conv)
    return Built(swap, {"effective": eff, "maturity": swap.maturity, "fixed_rate": float(rate), "notional": swap.notional})


swap = Kit(factory=swap_factory, asset_class="swap", default_bind=SWAP_BIND, cls=RefSwap, extra={"dv01_zero": "measure", "gamma_zero": "measure"},
           doc="Reference-stack overnight-indexed swap: closed-form schedule, PV, par rate, ladder and layers from a snapshot (module docstring).")


# ------------------------------------------------------------------------------ the bond
@dataclass(frozen=True)
class BondConv:
    calendar: str


def bond_conventions(raw: Mapping[str, Any]) -> BondConv:
    return BondConv(_conventions("bond", raw, _BOND_SUPPORTED)["calendar"])


_ALIAS = re.compile(r"(CT|OOO|OO|O)(\d+)")
MAX_FIXING_AGE_DAYS = 10  # a repo rate read from the newest published fixing must not be older than this
_FD_H = 0.01  # percentage points: the step of the finite differences behind risk and convexity


def resolve_security(p: RefPricer, token: str) -> Any:
    """A security id, or an alias (`CT10`, `O10`, `OO10`, `OOO10`: the on-the-run ranking is DATA in the snapshot's quote set), to its reference data."""
    qs = p.quotes
    if qs is None:
        raise MarketDataUnavailable(p.ts, {"bond": token}, "this snapshot carries no bond quotes")
    tok = str(token).strip()
    for cand in (tok, tok.upper()):
        if cand in qs.securities:
            return qs.securities[cand]
    m = _ALIAS.fullmatch(tok.upper())
    if m:
        hit = qs.aliases.get(f"{m.group(1)}{int(m.group(2))}")
        if hit is None:
            raise MarketDataUnavailable(p.ts, {"bond": token}, f"no {token} in the reference table on {p.reference_date}")
        return qs.securities[hit]
    if len(tok) == 9 and tok.isalnum():
        raise MarketDataUnavailable(p.ts, {"bond": token}, f"cusip {tok.upper()} not in the reference table")
    raise ConfigError(f"unknown bond token {token!r}; use a security id, CT<n>, O<n>, OO<n> or OOO<n>", code="UNIVERSE")


class RefBond:
    """One unit = `notional` face of one fixed-coupon bond (a zero-coupon bond is coupon 0). `sign` +1 is long (negative dv01). Plain data; the schedule is built once.

    Conventions (the shared block): semiannual coupons on unadjusted dates, generated backwards from the maturity on the maturity's day-of-month (month-end stays
    month-end), short front stub from the issue date; a payment falling on a non-business day is paid on the next business day; ACT/ACT ICMA accrual (a short first
    period accrues over its NOTIONAL regular period); the price/yield rule is the treasury one (simple interest over the first fractional period, then compounded
    semiannually); settlement is the reference date; a coupon whose accrual ends ON the settlement date is not in the price (the seller keeps it: accrued is 0)."""

    asset_class = "bond"
    GC_FROM_PRICER = "pricer"

    def __init__(self, *, security: Any, sign: int, notional: float, repo: Optional[Mapping[str, Any]], cal: Calendar):
        if sign not in (1, -1):
            raise ConfigError(f"sign must be +1 (long) or -1 (short), got {sign!r}", code="BOND")
        if not notional > 0:
            raise ConfigError("notional (face) is unsigned and must be > 0 (direction is `side`)", code="BOND")
        self.security, self.sign, self.notional = security, int(sign), float(notional)
        self.cusip, self.coupon = security.id, float(security.coupon)
        self.repo = dict(repo or {})
        unknown = sorted(set(self.repo) - {"gc_rate", "specialness_bps", "haircut"})
        if unknown:
            raise ConfigError(f"unknown repo keys {unknown}; the reference stack understands gc_rate, specialness_bps, haircut", code="CFG-EXTRAS")
        gc = self.repo.get("gc_rate")
        if isinstance(gc, str) and gc != self.GC_FROM_PRICER:
            raise ConfigError(f"repo gc_rate must be a percent number or {self.GC_FROM_PRICER!r}, got {gc!r}", code="BOND")
        mat, issue = security.maturity_date, security.issue_date
        roll = 31 if (mat + dt.timedelta(days=1)).month != mat.month else mat.day  # month-end maturities keep month-end coupons
        unadj = _unadjusted_dates(issue, mat, roll, 6)
        self.starts, self.ends = unadj[:-1], unadj[1:]
        self.ref_starts = tuple(add_months(e, -6, roll) for e in self.ends)  # the notional regular period of each coupon
        n = len(self.ends)
        self.amounts = tuple(self.coupon / 2.0 * (e - a).days / (e - r).days + (100.0 if i == n - 1 else 0.0)
                             for i, (a, e, r) in enumerate(zip(self.starts, self.ends, self.ref_starts)))
        self.pays = tuple(cal.following(e) for e in self.ends)
        self.last_payment = self.pays[-1]
        self._k = self.sign * self.notional / 100.0

    @property
    def notional_amount(self) -> float:
        return self.notional

    # ---- the price / yield rule
    def _first_open(self, settle: dt.date) -> int:
        """Index of the period `settle` is in: the first whose accrual end is strictly after it."""
        if settle < self.starts[0]:
            raise ConfigError(f"{self.cusip}: settlement {settle} is before the issue date {self.starts[0]}", code="BOND")
        return bisect.bisect_right(self.ends, settle)

    def dirty(self, y: float, settle: dt.date) -> float:
        """Dirty price per 100 face at yield `y` (percent, semiannual): the flows discounted period by period, the first fractional period at simple interest."""
        k = self._first_open(settle)
        w = (self.ends[k] - settle).days / (self.ends[k] - self.ref_starts[k]).days
        price, disc = 0.0, 1.0
        for j in range(k, len(self.ends)):
            disc /= 1.0 + y / 200.0 * (w if j == k else 1.0)
            price += self.amounts[j] * disc
        return price

    def accrued(self, settle: dt.date) -> float:
        """Accrued interest per 100 face: 0 on a coupon date (market convention), else the coupon share of the notional period elapsed."""
        k = self._first_open(settle)
        if settle == self.starts[k]:
            return 0.0
        return self.coupon / 2.0 * (settle - self.starts[k]).days / (self.ends[k] - self.ref_starts[k]).days

    def yield_of_dirty(self, dirty: float, settle: dt.date) -> float:
        lo, hi = -50.0, 200.0
        flo, fhi = self.dirty(lo, settle) - dirty, self.dirty(hi, settle) - dirty
        if flo * fhi > 0:
            raise ConfigError(f"{self.cusip}: no yield between {lo}% and {hi}% reproduces the price {dirty}", code="BOND")
        for _ in range(300):
            mid = 0.5 * (lo + hi)
            fm = self.dirty(mid, settle) - dirty
            if fm == 0.0 or hi - lo < 1e-14:
                return mid
            if fm * flo > 0:
                lo, flo = mid, fm
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def _derivs(self, y: float, settle: dt.date) -> Tuple[float, float]:
        """(dP/dy, d2P/dy2) of the dirty price per percentage point: fourth-order central differences (error ~1e-10 relative)."""
        h = _FD_H
        f = {i: self.dirty(y + i * h, settle) for i in (-2, -1, 0, 1, 2)}
        return (-f[2] + 8 * f[1] - 8 * f[-1] + f[-2]) / (12 * h), (-f[2] + 16 * f[1] - 30 * f[0] + 16 * f[-1] - f[-2]) / (12 * h * h)

    # ---- the mark
    def priced(self, ref: dt.date) -> bool:
        """True while a quote is needed: strictly before the last payment date."""
        return ref < self.last_payment

    def mark(self, p: RefPricer) -> Dict[str, float]:
        """quote -> ytm -> dirty at ONE settlement (the reference date). On or after the last payment there is nothing left to price."""
        def compute() -> Dict[str, float]:
            settle = p.reference_date
            if not self.priced(settle):
                return {"ytm": float("nan"), "dirty": 0.0, "accrued": float("nan"), "clean": float("nan")}
            q = p.quotes.quotes.get(self.cusip) if p.quotes is not None else None
            if q is None:
                raise MarketDataUnavailable(p.ts, {"cusip": self.cusip}, f"no quote for {self.cusip} at {p.ts}")
            acc = self.accrued(settle)
            y = float(q.ytm) if q.ytm is not None else self.yield_of_dirty(float(q.clean) + acc, settle)
            d = self.dirty(y, settle)
            return {"ytm": y, "dirty": d, "accrued": acc, "clean": d - acc}

        return p.memo(("bond_mark", self.cusip), compute)

    def _due(self, ref: dt.date) -> float:
        """Holder-signed flows paid ON `ref`: the price at a coupon-date settlement excludes them, so the value adds them back (a flow paid on D is inside V(D))."""
        return self._k * sum(a for a, d in zip(self.amounts, self.pays) if d == ref)

    def _cash(self, t0: dt.date, t1: dt.date) -> float:
        return self._k * sum(a for a, d in zip(self.amounts, self.pays) if t0 <= d < t1)

    def _mv(self, dirty: float) -> float:
        return self._k * dirty

    def pv(self, p: RefPricer) -> float:
        return self._mv(self.mark(p)["dirty"]) + self._due(p.reference_date)

    # ---- financing
    def repo_rate(self, p: RefPricer) -> float:
        gc = self.repo.get("gc_rate")
        if gc is None:
            return 0.0
        if isinstance(gc, str):
            ref = p.reference_date
            older = [d for d in p.fixings if d < ref]
            if not older:
                raise MarketDataUnavailable(p.ts, {}, f"repo gc_rate 'pricer': no published fixing before {ref}")
            newest = max(older)
            if (ref - newest).days > MAX_FIXING_AGE_DAYS:
                raise MarketDataUnavailable(p.ts, {}, f"repo gc_rate 'pricer': newest fixing {newest} is {(ref - newest).days} days before {ref} (stale)")
            gc = p.fixings[newest]
        return float(gc) - float(self.repo.get("specialness_bps", 0.0)) / 100.0

    def financing(self, prev: RefPricer, ts: Any, prev_ts: Any) -> float:
        """Per-unit funding over (prev_ts, ts]: a long pays, a short earns, on the previous dirty value net of haircut, ACT/360 on elapsed seconds."""
        if self.repo.get("gc_rate") is None or prev.reference_date > self.last_payment:
            return 0.0
        seconds = (pd.Timestamp(ts) - pd.Timestamp(prev_ts)).total_seconds()
        if seconds < 0:
            raise ValueError("financing needs prev_ts <= ts")
        financed = self.notional / 100.0 * self.mark(prev)["dirty"] * (1.0 - float(self.repo.get("haircut", 0.0)))
        return -self.sign * financed * self.repo_rate(prev) / 100.0 * seconds / (360.0 * 86400.0)

    # ---- value and measures
    def value(self, *, ctx: MarkContext) -> Valuation:
        p, prev = _ref(ctx.pricer), ctx.prev_pricer
        m = self.mark(p)
        cash = fin = 0.0
        if prev is not None:
            t0, t1 = prev.reference_date, p.reference_date
            if t0 < t1:
                cash = self._cash(t0, t1)
            fin = self.financing(prev, ctx.ts, ctx.prev_ts)
        return Valuation(ctx.ts, self._mv(m["dirty"]) + self._due(p.reference_date), cash, fin, {k: m[k] for k in ("ytm", "clean", "dirty", "accrued")})

    def _state(self, ctx: MarkContext) -> Dict[str, float]:
        p = _ref(ctx.pricer)
        if not self.priced(p.reference_date):
            raise ConfigError(f"{self.cusip} is on/after its last payment date; no quote-based measures", code="BOND")
        return self.mark(p)

    def ytm(self, *, ctx: MarkContext) -> float:
        return self._state(ctx)["ytm"]

    def accrued_interest(self, *, ctx: MarkContext) -> float:
        return self._state(ctx)["accrued"]

    def duration(self, *, ctx: MarkContext) -> float:
        """Modified duration in years."""
        s = self._state(ctx)
        return -self._derivs(s["ytm"], _ref(ctx.pricer).reference_date)[0] * 100.0 / s["dirty"]  # -(dP/dy with y in decimals) / P

    def dv01(self, *, ctx: MarkContext) -> float:
        """Currency P&L for a +1bp move in the bond's yield (negative for a long)."""
        s = self._state(ctx)
        return self._k * self._derivs(s["ytm"], _ref(ctx.pricer).reference_date)[0] / 100.0

    def gamma(self, *, ctx: MarkContext) -> float:
        """d2 PV / dbp^2 in currency."""
        s = self._state(ctx)
        return self._k * self._derivs(s["ytm"], _ref(ctx.pricer).reference_date)[1] * 1e-4

    # ---- layers
    def _terms(self, ctx: MarkContext) -> Dict[str, float]:
        key = ("ref_bond_terms", id(self))
        if key in ctx.cache:
            return ctx.cache[key]
        p, prev = _ref(ctx.pricer), ctx.prev_pricer
        if prev is None:
            raise ConfigError("layers need a previous pricer (ctx.prev_pricer)", code="LAYER")
        prev = _ref(prev)
        t0, t1 = prev.reference_date, p.reference_date
        if t0 > self.last_payment:
            out = dict.fromkeys(("carry", "roll", "delta", "convexity"), 0.0)
        else:
            m0 = self.mark(prev)
            fin = self.financing(prev, ctx.ts, ctx.prev_ts)
            cash = self._cash(t0, t1) if t0 < t1 else 0.0
            v0 = self._mv(m0["dirty"]) + self._due(t0)
            if not (self.priced(t0) and self.priced(t1)):
                out = {"carry": self.pv(p) + cash - v0 + fin, "roll": 0.0, "delta": 0.0, "convexity": 0.0}
            else:
                y0, y1 = m0["ytm"], self.mark(p)["ytm"]
                v_cy = self._mv(self.dirty(y0, t1)) + self._due(t1)
                d1, d2 = self._derivs(y0, t1)
                y_hold = y0 + self._pulldown(prev, p)
                out = {
                    "carry": cash + v_cy - v0 + fin,
                    "roll": self._k * d1 * (y_hold - y0),
                    "delta": self._k * d1 * (y1 - y_hold),
                    "convexity": 0.5 * self._k * d2 * (y1 - y0) ** 2,
                }
        ctx.cache[key] = out
        return out

    def _pulldown(self, prev: RefPricer, p: RefPricer) -> float:
        """Yield change (percent) from ageing down the snapshot's yield curve over the elapsed calendar time; 0 when the snapshot has no such curve."""
        yc = ytm_curve(prev)
        if yc is None:
            return 0.0
        ttm0 = (self.security.maturity_date - prev.reference_date).days / 365.25
        dt_years = (p.reference_date - prev.reference_date).days / 365.25
        return float(np.interp(max(ttm0 - dt_years, 1e-3), yc[0], yc[1]) - np.interp(ttm0, yc[0], yc[1]))

    def carry(self, *, ctx: MarkContext) -> float:
        return self._terms(ctx)["carry"]

    def roll(self, *, ctx: MarkContext) -> float:
        return self._terms(ctx)["roll"]

    def delta(self, *, ctx: MarkContext) -> float:
        return self._terms(ctx)["delta"]

    def convexity(self, *, ctx: MarkContext) -> float:
        return self._terms(ctx)["convexity"]


def ytm_curve(p: RefPricer) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """(ttm in years, ytm in percent) through the quoted rank-0 (`CT<n>`) bonds, else every quoted bond; None below two points (then the roll layer is 0)."""
    def compute() -> Optional[Tuple[np.ndarray, np.ndarray]]:
        qs = p.quotes
        if qs is None:
            return None
        ids = [c for a, c in qs.aliases.items() if a.startswith("CT")] or list(qs.quotes)
        pts: Dict[float, float] = {}
        for c in ids:
            q, sec = qs.quotes.get(c), qs.securities[c]
            b = RefBond(security=sec, sign=1, notional=100.0, repo=None, cal=Calendar())
            if q is None or not b.priced(p.reference_date):
                continue
            y = float(q.ytm) if q.ytm is not None else b.yield_of_dirty(float(q.clean) + b.accrued(p.reference_date), p.reference_date)
            pts[(sec.maturity_date - p.reference_date).days / 365.25] = y
        if len(pts) < 2:
            return None
        ks = sorted(pts)
        return np.array(ks), np.array([pts[k] for k in ks])

    return p.memo(("ytm_curve",), compute)


BOND_BIND: Dict[str, Any] = {name: _bind(method) for name, method in (
    ("value", "value"), ("dv01", "dv01"), ("gamma", "gamma"), ("rate", "ytm"), ("ytm", "ytm"), ("duration", "duration"), ("accrued", "accrued_interest"),
    ("carry", "carry"), ("roll", "roll"), ("delta", "delta"), ("convexity", "convexity"))}


def bond_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    p = _ref(pricer)
    conv = bond_conventions(conventions)
    sec = resolve_security(p, terms["security"])
    extras = dict(terms.get("extras") or {})
    unknown = sorted(set(extras) - {"repo"})
    if unknown:
        raise ConfigError(f"the reference stack does not understand the bond extras {unknown}; it understands ['repo']", code="CFG-EXTRAS")
    b = RefBond(security=sec, sign=int(terms["direction"]), notional=float(terms["notional"]), repo=extras.get("repo"), cal=p.calendar(conv.calendar))
    return Built(b, {"security": sec.id, "coupon": sec.coupon, "maturity": sec.maturity_date, "notional": b.notional})


bond = Kit(factory=bond_factory, asset_class="bond", default_bind=BOND_BIND, cls=RefBond,
           doc="Reference-stack fixed-coupon bond: schedule, treasury price/yield rule, risk, coupon sweep, repo financing and yield-space layers (module docstring).")


# ------------------------------------------------------------------------------ the stack (what PricebtSession.use(stack=...) takes)
STACK = Stack(
    name="refstack", wrap=wrap,  # no default instrument specs: a spec needs a calendar name, which is the snapshot's data (`PricebtSession(instruments={"swap:USD": {...}})`)
    accepts_gs={"floating_rate_option": ("USD-SOFR", "USD-SOFR-COMPOUND", "USD-SOFR-OIS", "SOFR")},
)
