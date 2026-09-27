"""SyntheticMarket: a deterministic, library-free market for tests, emitting plain-data snapshots (spec D8). Nelson-Siegel factor random walks give the
discount-factor node curves, the same walks give seeded overnight fixings, and a small panel of fixed-coupon bonds is quoted in YIELD (percent), so no bond
pricing rule lives here. Clearly synthetic: no calibration to any real market, no volatility process (options are out of scope).

The business-day calendar is DATA supplied by the caller (a `Calendar` or a snapshot `CalendarData`; default: weekends only), never a library's name.
Every `get_pricer(ts)` returns a `SnapshotPricer` (see `pricebt.snapshot`) stamped `ts`; a library adapter wraps it into that library's pricer.

Fixings follow a publication rule of this provider (a fixing dated d is public from one business day later at 08:00 local time; the newest required one is
proxied by the last public level before that), so a snapshot carries only fixings visible at its stamp and lists a proxied date in `proxied`.
"""
from __future__ import annotations

import bisect
import datetime as dt
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ..errors import ConfigError, MarketDataUnavailable
from ..snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, Quote, QuoteSet, Security, SnapshotPricer
from ..timeutil import Calendar

_NODE_YEARS = (1 / 52, 2 / 52, 1 / 12, 2 / 12, 3 / 12, 6 / 12, 9 / 12, 1, 1.5, 2, 3, 4, 5, 7, 10, 12, 15, 20, 25, 30)
DEFAULT_BONDS: Tuple[Tuple[float, dt.date, dt.date], ...] = (
    (4.500, dt.date(2023, 11, 15), dt.date(2027, 11, 15)),
    (3.875, dt.date(2024, 2, 15), dt.date(2029, 2, 15)),
    (4.250, dt.date(2024, 11, 15), dt.date(2034, 11, 15)),
    (4.125, dt.date(2022, 8, 15), dt.date(2032, 8, 15)),
    (3.500, dt.date(2020, 2, 15), dt.date(2050, 2, 15)),
)


def ns_zero(t_years: np.ndarray, b0: float, b1: float, b2: float, lam: float) -> np.ndarray:
    """Nelson-Siegel continuously-compounded zero rate in percent."""
    x = np.maximum(np.asarray(t_years, dtype=float), 1e-6) / lam
    f = (1.0 - np.exp(-x)) / x
    return b0 + b1 * f + b2 * (f - np.exp(-x))


def _calendar(c: Union[None, Calendar, CalendarData], name: str) -> Tuple[Calendar, CalendarData]:
    if c is None:
        c = Calendar.weekends_only()
    if isinstance(c, CalendarData):
        return Calendar(c.holidays, c.weekmask, c.name), c
    wk = "".join(str(int(x)) for x in c.weekmask)
    return c, CalendarData(name, tuple(sorted(c.holidays)), wk)


class SyntheticMarket:
    """Business-day snapshots for `[start, end]`, with `history_years` of earlier fixings so seasoned swaps can be marked.

    Factors (b0 level, b1 slope, b2 curvature) follow seeded mean-reverting walks. `intraday_bp` > 0 adds a deterministic, timestamp-seeded parallel noise so
    minute marks differ. `get_pricer(ts)` returns a snapshot stamped `ts` (fresh data); non-business days raise.
    """

    def __init__(
        self, start: str = "2024-01-02", end: str = "2025-12-31", *, calendar: Union[None, Calendar, CalendarData] = None, seed: int = 7, history_years: float = 3.0,
        tz: str = "America/New_York", level: float = 4.0, slope: float = -0.3, curvature: float = 0.3, lam: float = 2.0, daily_bp: Tuple[float, float, float] = (3.0, 2.0, 3.0),
        intraday_bp: float = 0.0, bonds: Sequence[Tuple[float, dt.date, dt.date]] = DEFAULT_BONDS, bond_spread_bp: float = 6.0, curve_name: str = "sofr",
        fixings_name: str = "USD-SOFR-1D", calendar_name: str = "usd_fed", publish_after_bdays: int = 1, publish_at: Union[str, dt.time] = "08:00", pre_publish: str = "proxy_last",
    ):
        if pre_publish not in ("proxy_last", "strict"):
            raise ConfigError(f"pre_publish must be proxy_last or strict, got {pre_publish!r}", code="SYNTH")
        self.tz, self.seed, self.lam, self.intraday_bp = tz, int(seed), float(lam), float(intraday_bp)
        self.curve_name, self.fixings_name = curve_name, fixings_name
        self.publish_after_bdays = int(publish_after_bdays)
        self.publish_at = publish_at if isinstance(publish_at, dt.time) else dt.time.fromisoformat(publish_at)
        self.pre_publish = pre_publish
        self._cal, self._cal_data = _calendar(calendar, calendar_name)
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        h0 = s - pd.Timedelta(days=int(365.25 * history_years))
        self._days: List[dt.date] = self._cal.business_days(self._cal.following(h0.date()), self._cal.preceding(e.date()))
        self._pos = {d: i for i, d in enumerate(self._days)}
        self.start, self.end = s.date(), e.date()
        rng = np.random.default_rng(self.seed)
        n = len(self._days)
        mu = np.array([level, slope, curvature])
        sd = np.array(daily_bp) / 100.0
        f = np.empty((n, 3))
        f[0] = mu
        for i in range(1, n):
            f[i] = f[i - 1] + 0.01 * (mu - f[i - 1]) + sd * rng.standard_normal(3)
        self._f = f
        self._fix = pd.Series(np.maximum(f[:, 0] + f[:, 1] + 0.005 * rng.standard_normal(n), 0.01), index=pd.DatetimeIndex([pd.Timestamp(d) for d in self._days]))
        self._fix_values = tuple(float(v) for v in self._fix.to_numpy())
        self.refs: Dict[str, Security] = {}
        for k, (cpn, iss, mat) in enumerate(bonds):
            cid = f"SYN{k:02d}{mat.year:04d}"
            self.refs[cid] = Security(cid, float(cpn), iss, mat, f"SYN {cpn:g} {mat:%b %y}")
        self._spread = {c: bond_spread_bp / 100.0 * ((k % 3) - 1) for k, c in enumerate(self.refs)}

    # ------------------------------------------------------------------ market model
    def factors(self, ref: dt.date, ts: Optional[pd.Timestamp] = None) -> np.ndarray:
        i = self._pos.get(ref)
        if i is None:
            raise MarketDataUnavailable(ts, {}, f"{ref} is not a business day inside the synthetic history {self._days[0]}..{self._days[-1]}")
        f = self._f[i].copy()
        if self.intraday_bp > 0.0 and ts is not None:
            t = pd.Timestamp(ts).tz_convert(self.tz)
            r = np.random.default_rng([self.seed, ref.toordinal(), t.hour * 60 + t.minute])
            f[0] += self.intraday_bp / 100.0 * r.standard_normal()
        return f

    def zero(self, ref: dt.date, t_years: Any, ts: Optional[pd.Timestamp] = None) -> np.ndarray:
        b0, b1, b2 = self.factors(ref, ts)
        return ns_zero(np.asarray(t_years, dtype=float), b0, b1, b2, self.lam)

    def nodes(self, ref: dt.date, ts: Optional[pd.Timestamp] = None) -> Dict[dt.date, float]:
        ys = np.array((0.0,) + _NODE_YEARS)
        z = self.zero(ref, ys, ts)
        out = {}
        for y, zz in zip(ys, z):
            d = ref if y == 0.0 else ref + dt.timedelta(days=int(round(y * 365.25)))
            out[d] = float(np.exp(-zz / 100.0 * ((d - ref).days / 365.0)))
        return out

    def ytm_at(self, ref: dt.date, ttm: float, ts: Optional[pd.Timestamp] = None) -> float:
        """Semi-annually compounded yield (percent) equivalent to the NS zero at `ttm` years."""
        z = float(self.zero(ref, [ttm], ts)[0]) / 100.0
        return 200.0 * (np.exp(z / 2.0) - 1.0)

    def quotes(self, ref: dt.date, ts: Optional[pd.Timestamp] = None) -> Dict[str, Quote]:
        """Yield quotes (percent) of the bonds alive on `ref`: issued before it and not past maturity."""
        out = {}
        for cusip, r in self.refs.items():
            if ref > r.maturity_date or ref <= r.issue_date:
                continue
            out[cusip] = Quote(ytm=float(self.ytm_at(ref, max((r.maturity_date - ref).days / 365.25, 0.05), ts) + self._spread[cusip]))
        return out

    @property
    def fixings(self) -> pd.Series:
        """The full seeded overnight-fixing history (percent, naive midnight index): what the provider publishes from, before the publication rule."""
        return self._fix

    def _fixings_at(self, ts: pd.Timestamp, ref: dt.date) -> FixingsSeries:
        k = bisect.bisect_left(self._days, ref)  # fixings dated strictly before the reference date
        ds, vs, proxied = tuple(self._days[:k]), self._fix_values[:k], ()
        if k:
            last_needed = self._cal.add_business_days(ref, -1)
            publish = pd.Timestamp(dt.datetime.combine(self._cal.add_business_days(last_needed, self.publish_after_bdays), self.publish_at)).tz_localize(self.tz)
            if publish > ts:
                if self.pre_publish == "strict":
                    raise ValueError(f"fixing for {last_needed} is not published at {ts} (policy strict)")
                j = bisect.bisect_left(ds, last_needed)
                ds, vs = ds[:j], vs[:j]
                if j:
                    ds, vs, proxied = ds + (last_needed,), vs + (vs[-1],), (last_needed,)
        return FixingsSeries(self.fixings_name, ds, vs, "percent", proxied)

    # ------------------------------------------------------------------ market data provider
    def get_pricer(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> SnapshotPricer:
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            raise ConfigError("the synthetic market needs tz-aware timestamps", code="MDP")
        ref = ts.tz_convert(self.tz).date()
        if not (self.start <= ref <= self.end):
            raise MarketDataUnavailable(ts, request, f"outside the synthetic window {self.start}..{self.end}")
        nodes = self.nodes(ref, ts)
        curve = CurveSnapshot(self.curve_name, ref, tuple(nodes), tuple(nodes.values()))
        alive = self.quotes(ref, ts)
        qs = QuoteSet(ref, alive, self.refs, {}, "unspecified")
        snap = MarketSnapshot(ts, ref, curves={self.curve_name: curve}, fixings={self.fixings_name: self._fixings_at(ts, ref)}, quotes=qs,
                              calendars={self._cal_data.name: self._cal_data}, provenance={"source": "synthetic", "seed": self.seed})
        return SnapshotPricer(snap)

    def available_timestamps(self, start: pd.Timestamp, end: pd.Timestamp, close: dt.time = dt.time(17, 0)) -> List[pd.Timestamp]:
        out = []
        for d in self._days:
            if self.start <= d <= self.end:
                t = pd.Timestamp(dt.datetime.combine(d, close)).tz_localize(self.tz)
                if start <= t <= end:
                    out.append(t)
        return out


_FLAT_NODE_DAYS = (0, 7, 14, 30, 61, 91, 182, 273, 365, 548, 730, 1095, 1461, 1826, 2557, 3653, 5479, 7305, 10958, 14610)
_FLAT_TZ = "America/New_York"


# ------------------------------------------------------------------------------ flat worlds for the layer conformance kit (testing/layer_conformance.py)
def flat_swap_world(calendar: str = "cal", *, rate: float = 0.04, holidays: Sequence[dt.date] = (), history_days: int = 900) -> Callable[[dt.date, float], MarketSnapshot]:
    """`world(ref, shock_bp)`: a flat continuously-compounded (ACT/365) discount curve of `rate` + `shock_bp` anchored at `ref`, overnight fixings CONSISTENT with the unshocked
    curve (so a started swap's realised coupon equals what the curve implied), and the named calendar."""
    cal = Calendar(holidays)

    def world(ref: dt.date, shock_bp: float = 0.0) -> MarketSnapshot:
        r = rate + shock_bp * 1e-4
        dates = [ref + dt.timedelta(days=n) for n in _FLAT_NODE_DAYS]
        curve = CurveSnapshot("curve", ref, tuple(dates), tuple(math.exp(-r * n / 365.0) for n in _FLAT_NODE_DAYS))
        days = cal.business_days(ref - dt.timedelta(days=history_days), ref - dt.timedelta(days=1))
        fx = []
        for d in days:
            n = (cal.add_business_days(d, 1) - d).days
            fx.append(100.0 * (math.exp(rate * n / 365.0) - 1.0) * 360.0 / n)
        fixings = FixingsSeries("fixings", tuple(days), tuple(fx), "percent")
        ts = pd.Timestamp(dt.datetime.combine(ref, dt.time(17, 0))).tz_localize(_FLAT_TZ)
        return MarketSnapshot(ts, ref, {"curve": curve}, {"fixings": fixings}, None, {calendar: CalendarData(calendar, tuple(holidays))})

    return world


def flat_bond_world(security: Security, calendar: str = "cal", *, yield_pct: float = 4.5, holidays: Sequence[dt.date] = ()) -> Callable[[dt.date, float], MarketSnapshot]:
    """`world(ref, shock_bp)`: one bond quoted at a flat yield of `yield_pct` + `shock_bp` (a quote panel needs no curve)."""

    def world(ref: dt.date, shock_bp: float = 0.0) -> MarketSnapshot:
        qs = QuoteSet(ref, {security.id: Quote(ytm=yield_pct + shock_bp / 100.0)}, {security.id: security}, {}, "market")
        ts = pd.Timestamp(dt.datetime.combine(ref, dt.time(17, 0))).tz_localize(_FLAT_TZ)
        return MarketSnapshot(ts, ref, {}, {}, qs, {calendar: CalendarData(calendar, tuple(holidays))})

    return world
