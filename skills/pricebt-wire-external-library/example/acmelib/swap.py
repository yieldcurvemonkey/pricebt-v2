"""acmelib swaps and the pricing / risk functions on them.

Foreign conventions on purpose (each is something a real bank library does):

* every number is from the RECEIVER of the fixed leg's point of view: `pv` is what the fixed receiver holds, `pv01` is the receiver's change for +1bp (negative), `gamma`
  and the ladder likewise. A swap has NO side; whoever pays fixed negates.
* rates are DECIMALS (0.0425); the swap's `fixed` too. Dates are ISO strings and tenors are lower-case ('10y').
* risk (`pv01`, `bucket_risk`, `gamma`) is returned for a notional of ONE MILLION whatever the swap's own notional is; `pv` and `cashflows` use the swap's notional.
* `bucket_risk` returns a pandas Series labelled '3m', '6m', ... plus an 'on' (overnight) bucket that pricebt has no name for.
* every valuation depends on the process-global valuation date (`acmelib.set_valuation_date`) and needs a `Market` whose curve is anchored at it.
* there is NO attribution: the building blocks for it are `pv(..., as_of=...)`, `cashflows`, `Market.shifted`, `Market.rolled` and `ZeroCurve.resampled`.

The arithmetic (schedules, discounting over fixings, the bootstrapped par-swap risk curve) is REUSED from `pricebt.testing.refstack`, so acmelib agrees with the
reference stack to round-off. A real library is independent and shows noise floors instead (README).
"""
from __future__ import annotations

import datetime as dt
import functools
import re
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import pandas as pd

from pricebt.errors import MarketDataUnavailable, PricebtError
from pricebt.testing import refstack as core  # the numerical core (README): schedule, PV over fixings, par rate, risk curves

from . import calendars as cal
from .curve import Market
from .errors import AcmeError, BadInput, CurveError, MissingFixing, SwapExpired
from .state import require_valuation_date

MM = 1_000_000.0  # risk is reported per one million of notional
_BASIS = {"A360": "act360", "A365": "act365f"}
_FREQ = {"1m": "monthly", "3m": "quarterly", "6m": "semiannual", "1y": "annual"}


def _translated(fn: Callable[..., Any]) -> Callable[..., Any]:
    """The reused arithmetic raises pricebt's errors; acmelib's callers must only ever see acmelib's."""

    @functools.wraps(fn)
    def inner(*a: Any, **k: Any) -> Any:
        try:
            return fn(*a, **k)
        except AcmeError:
            raise
        except MarketDataUnavailable as e:
            raise MissingFixing(e.reason) from e
        except PricebtError as e:
            raise AcmeError(str(e)) from e

    return inner


class Swap:
    """A fixed-vs-overnight swap CONTRACT (no side). `start` is an ISO date, `maturity` a lower-case tenor ('10y', '18m') or an ISO date, `fixed` a decimal."""

    @_translated
    def __init__(self, start: str, maturity: str, notional: float, fixed: float, *, calendar: str, basis: str = "A360", freq: str = "1y", bdc: str = "MF",
                 pay_lag: int = 2, spot_lag: int = 2):
        if basis not in _BASIS or freq not in _FREQ:
            raise BadInput(f"basis is one of {sorted(_BASIS)} and freq one of {sorted(_FREQ)}, got {basis!r} / {freq!r}")
        if not notional > 0:
            raise BadInput("notional must be positive")
        if bdc not in cal.BDC:
            raise BadInput(f"bdc is one of {sorted(cal.BDC)}, got {bdc!r}")
        eff = cal.parse_date(start)
        if re.fullmatch(r"\d+[ymwd]", str(maturity)):
            if cal.parse_tenor(maturity)[1] not in "ym":
                raise BadInput(f"swap maturities are year or month tenors, got {maturity!r}")
            mat: Any = maturity
        else:
            mat = cal.parse_date(maturity)
        conv = core.SwapConv(calendar, spot_lag, _BASIS[basis], _FREQ[freq], cal.BDC[bdc], pay_lag)
        sched = core.build_schedule(eff, mat, cal.calendar(calendar), freq_months=conv.freq_months, bdc=conv.business_day_convention, pay_lag=pay_lag, basis=conv.basis)
        self.calendar, self.notional, self.fixed = calendar, float(notional), float(fixed)
        self.start, self.end, self.last_payment = eff.isoformat(), sched.ends[-1].isoformat(), sched.pays[-1].isoformat()
        self._ref = core.RefSwap(effective=eff, schedule=sched, sign=-1, notional=float(notional), fixed_rate=100.0 * float(fixed), conv=conv)  # -1: the RECEIVER's swap
        self._mm = core.RefSwap(effective=eff, schedule=sched, sign=-1, notional=MM, fixed_rate=100.0 * float(fixed), conv=conv)  # the same, for risk

    def __repr__(self) -> str:
        return f"Swap({self.start} -> {self.end}, notional {self.notional:g}, fixed {self.fixed:.6f}, {self.calendar})"


class _Book:
    """What the reused arithmetic reads from a 'pricer', built from a Market at ONE valuation date: the curve, the fixings in percent, the calendars, a memo."""

    def __init__(self, market: Market, v: dt.date):
        self.reference_date, self.ts = v, pd.Timestamp(v, tz="UTC")
        self.curve = market.curve._curve()
        self.fixings = {cal.parse_date(k): 100.0 * x for k, x in market.fixings.items()}
        self._memo: Dict[Any, Any] = {}
        self._pinned: Dict[int, Any] = {}  # the arithmetic keys some memo entries by id(swap): keep every swap alive as long as this book, so an id is never recycled

    def calendar(self, name: str) -> Any:
        return cal.calendar(name)

    def need_curve(self) -> Any:
        return self.curve

    def memo(self, key: Any, fn: Callable[[], Any]) -> Any:
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]


def _view(swap: Swap, market: Market) -> Tuple[_Book, dt.date]:
    v = cal.parse_date(require_valuation_date())
    if market.curve.anchor != v.isoformat():
        raise CurveError(f"the curve is anchored at {market.curve.anchor} but the valuation date is {v.isoformat()}: set the valuation date to the curve's anchor")
    book = market._books.get(v)
    if book is None:
        book = market._books[v] = _Book(market, v)
    for o in (swap._ref, swap._mm):
        book._pinned[id(o)] = o
    return book, v


def _tenors(buckets: Sequence[str]) -> Tuple[str, ...]:
    for b in buckets:
        cal.parse_tenor(b)  # acmelib buckets are lower-case
    return tuple(b.upper() for b in buckets)  # the reused arithmetic's own spelling


@_translated
def pv(swap: Swap, market: Market, as_of: Optional[str] = None) -> float:
    """Value to the fixed RECEIVER of the flows paid on or after `as_of` (default: the valuation date), discounted to `as_of` on `market`. With `as_of` after the
    valuation date this is the FORWARD value: the market is not moved, only the date the flows are valued at (a flow paid before `as_of` is not in it)."""
    book, v = _view(swap, market)
    if as_of is None or cal.parse_date(as_of) == v:
        return swap._ref.npv(book, book.curve, v)
    a = cal.parse_date(as_of)
    if a < v:
        raise BadInput(f"as_of {as_of} is before the valuation date {v.isoformat()}")
    ref = swap._ref
    live = ref._p >= a.toordinal()
    return float((ref._amounts(book, book.curve, v)[live] * book.curve.dfs_at(ref._p[live])).sum() / book.curve.df(a))


@_translated
def cashflows(swap: Swap, market: Market, since: Optional[str] = None) -> pd.DataFrame:
    """The flows paid on or after `since` (default: the valuation date), receiver's sign, projected as of the valuation date: columns `pay_date` (ISO) and `amount`."""
    book, v = _view(swap, market)
    s = v if since is None else cal.parse_date(since)
    ref = swap._ref
    keep = ref._p >= s.toordinal()
    days = pd.Series([dt.date.fromordinal(int(o)).isoformat() for o in ref._p[keep]], dtype=object)  # explicit: an empty frame must still hold strings
    return pd.DataFrame({"pay_date": days, "amount": pd.Series(ref._amounts(book, book.curve, v)[keep], dtype=float)})


@_translated
def par_rate(swap: Swap, market: Market) -> float:
    """The fixed rate (decimal) that makes the remaining swap worth zero."""
    book, v = _view(swap, market)
    if swap._ref.matured(v):
        raise SwapExpired(f"the swap paid its last flow on {swap.last_payment}: it has no par rate on {v.isoformat()}")
    return swap._ref.par_rate(book, book.curve, v) / 100.0


@_translated
def pv01(swap: Swap, market: Market) -> float:
    """The receiver's change in value for +1bp of the fixed rate's annuity, per ONE MILLION notional (negative: a receiver loses when rates rise). Analytic; zero when expired."""
    book, v = _view(swap, market)
    return 0.0 if swap._mm.matured(v) else swap._mm.annuity_dv01(book.curve, v)


@_translated
def bucket_risk(swap: Swap, market: Market, buckets: Sequence[str]) -> pd.Series:
    """The receiver's change for +1bp of each bucket's par rate, per ONE MILLION notional: a Series labelled with the lower-case `buckets` PLUS an 'on' (overnight) bucket."""
    book, v = _view(swap, market)
    tenors = _tenors(buckets)
    if swap._mm.matured(v):
        vals = [0.0] * len(tenors)
    else:
        rc = core._risk_curves(book, swap._mm.conv, tenors)
        vals = [(swap._mm.npv(book, up, v) - swap._mm.npv(book, dn, v)) / (2.0 * core.LADDER_BUMP / 1e-4) for up, dn in zip(rc.ups, rc.dns)]
    return pd.Series([0.0, *vals], index=["on", *buckets], name="dv01_per_mm")


@_translated
def gamma(swap: Swap, market: Market, buckets: Sequence[str]) -> float:
    """The second difference of the receiver's value for a parallel +-1bp move of all `buckets`' par rates, per ONE MILLION notional and bp^2."""
    book, v = _view(swap, market)
    tenors = _tenors(buckets)
    if swap._mm.matured(v):
        return 0.0
    rc = core._risk_curves(book, swap._mm.conv, tenors)
    return (swap._mm.npv(book, rc.parallel_up, v) + swap._mm.npv(book, rc.parallel_dn, v) - 2.0 * swap._mm.npv(book, rc.base, v)) / (core.PARALLEL_BUMP / 1e-4) ** 2
