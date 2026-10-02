"""FICTIONAL bank-style pricing SDK ("Meridian") for the pricebt skills example: not any real vendor's API.

It has the *shape* of an enterprise pricing platform (a session/client, market handles fetched by
date, trade specs, batch pricing with measure codes) and deliberately NON-pricebt conventions, so
the asset config in ../meridian_usd_irs.yaml has to convert every one of them:

- dates are ISO strings ("2024-01-02"), not datetime.date;
- fixed rates and par rates are in PERCENT (3.85), not decimal or bp;
- notional is always positive; the side is a separate direction "PAY" / "RECEIVE" (of fixed);
- "DV01" is the PV change for a 1bp DECREASE in rates (receiver-positive);
- "BUCKET_DV01" is keyed "USD.SOFR:2Y" and uses the same receiver-positive sign;
- a tenor `end` ("10Y") is resolved against the spot date of the market passed to price(), i.e. a
  relative trade floats (quoted-swap convention) -- a config must pin maturities itself;
- market() RAISES on closed days (MarketClosed) and outside its history (NoData);
- "CASHFLOWS" lists each future flow with a POSITIVE amount and a direction ("PAY" / "RECEIVE",
  from the trade holder's side) and an ISO date;
- scenario() re-values a snapshot: shifts are in bp of the pillar zero rates, and a new
  valuation_date keeps the ZERO rates by default (the curve rolls down); hold="FORWARDS" keeps
  the forwards instead (DF(x) / DF(valuation_date), a translated curve).

Math (deterministic, closed form, no data files): zero rates at pillars 1Y..30Y, linearly
interpolated in time (flat outside), continuously compounded, ACT/365F. Pillar level and slope move
with the business-day index so the 10y par rate mean-reverts over a few months. Swaps: annual fixed
coupons vs float coupons on the same schedule (each valued N*(DF(prev) - DF(c)), so the float leg is
N*(DF(start) - DF(end)) before the first coupon). PV drops each flow on its payment date. DV01/BUCKET_DV01 are analytic first-order sensitivities to
the pillar zero rates, so the buckets sum to DV01 exactly (up to float rounding).
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Optional

import numpy as np
from dateutil.relativedelta import relativedelta

__all__ = ["connect", "Client", "MarketHandle", "TradeSpec", "MarketClosed", "NoData", "HOLIDAYS", "MEASURES", "HOLD"]


class MarketClosed(Exception):
    """The requested as-of date is a weekend or a Meridian holiday."""


class NoData(Exception):
    """No market snapshot exists for the requested as-of date (outside history, or a data gap)."""


FIRST_DATE = date(2020, 1, 2)
LAST_DATE = date(2026, 6, 30)
HOLIDAYS = frozenset(
    date.fromisoformat(s)
    for s in (
        "2024-01-01", "2024-01-15", "2024-02-19", "2024-05-27", "2024-06-19", "2024-07-04",
        "2024-09-02", "2024-11-28", "2024-12-25",
        "2025-01-01", "2025-01-20", "2025-02-17", "2025-05-26", "2025-06-19", "2025-07-04",
        "2025-09-01", "2025-11-27", "2025-12-25",
    )
)
DEFAULT_GAPS = ("2024-03-14",)  # one business day with no snapshot, to exercise NoData end to end
MEASURES = ("PV", "PAR_PCT", "DV01", "BUCKET_DV01", "FIXED_PCT", "CASHFLOWS")
HOLD = ("ZEROS", "FORWARDS")  # scenario(valuation_date=...): what stays fixed when the date moves

_PILLARS = (("1Y", 1.0), ("2Y", 2.0), ("5Y", 5.0), ("10Y", 10.0), ("30Y", 30.0))
_CURVES = {"USD.SOFR": "USD"}  # curve id -> currency
_TENOR = re.compile(r"^(\d+)([DWMY])$")
_HOL = np.array(sorted(HOLIDAYS), dtype="datetime64[D]")


def _bday(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def _following(d: date) -> date:
    while not _bday(d):
        d += timedelta(days=1)
    return d


def _spot(d: date) -> date:
    for _ in range(2):
        d = _following(d + timedelta(days=1))
    return d


def _add_tenor(d: date, tenor: str) -> date:
    m = _TENOR.match(tenor.strip().upper())
    if not m:
        raise ValueError(f"Meridian: bad tenor {tenor!r} (use e.g. '3M', '10Y')")
    n, u = int(m.group(1)), m.group(2)
    return d + {"D": relativedelta(days=n), "W": relativedelta(weeks=n), "M": relativedelta(months=n), "Y": relativedelta(years=n)}[u]


@dataclass(frozen=True)
class TradeSpec:
    """A vanilla fixed-float swap. `end` is an ISO date or a tenor (resolved at pricing time)."""

    currency: str
    start: str
    end: str
    fixed_rate_pct: Optional[float]  # None = struck at par on whatever market prices it
    notional: float                  # always positive
    direction: str                   # "PAY" or "RECEIVE" (of the fixed leg)


class MarketHandle:
    """An end-of-day curve snapshot. Plain class: callers may memoise on its __dict__."""

    def __init__(self, client: "Client", as_of: date, curve: str, zeros: tuple):
        self.client = client
        self.as_of = as_of.isoformat()
        self.curve = curve
        self._d = as_of
        self._zeros = zeros  # zero rate at each pillar, decimal

    def _t(self, d: date) -> float:
        return (d - self._d).days / 365.0

    def _weights(self, t: float) -> list:
        """Linear-interpolation weight of each pillar at time t (flat outside); sums to 1."""
        ts = [p[1] for p in _PILLARS]
        w = [0.0] * len(ts)
        if t <= ts[0]:
            w[0] = 1.0
        elif t >= ts[-1]:
            w[-1] = 1.0
        else:
            i = next(k for k in range(len(ts) - 1) if ts[k] <= t <= ts[k + 1])
            a = (t - ts[i]) / (ts[i + 1] - ts[i])
            w[i], w[i + 1] = 1.0 - a, a
        return w

    def _df_and_grad(self, d: date):
        """DF(d) and dDF/dz_i for each pillar zero rate z_i."""
        t = self._t(d)
        w = self._weights(t)
        z = sum(wi * zi for wi, zi in zip(w, self._zeros))
        df = math.exp(-z * t)
        return df, [-t * df * wi for wi in w]


class _ForwardHeldHandle(MarketHandle):
    """`base` re-valued from a later (or earlier) date with every forward held: DF(x) / DF(as_of)."""

    def __init__(self, base: MarketHandle, as_of: date):
        super().__init__(base.client, as_of, base.curve, base._zeros)
        self._base = base

    def _df_and_grad(self, d: date):
        df, g = self._base._df_and_grad(d)
        d0, g0 = self._base._df_and_grad(self._d)
        return df / d0, [(gi * d0 - df * g0i) / (d0 * d0) for gi, g0i in zip(g, g0)]


def _zeros_for(d: date) -> tuple:
    n = int(np.busday_count(FIRST_DATE, d, holidays=_HOL))
    level = 0.035 + 0.010 * math.sin(2 * math.pi * n / 120.0)
    slope = 0.006 * math.cos(2 * math.pi * n / 90.0)
    return tuple(level + slope * (1.0 - math.exp(-t / 4.0)) for _, t in _PILLARS)


class Client:
    """A Meridian session. Every remote-style call increments `calls` (and sleeps `latency` s)."""

    def __init__(self, env: str, gaps: Iterable[str], latency: float):
        self.env = env
        self.gaps = frozenset(date.fromisoformat(g) for g in gaps)
        self.latency = latency
        self.calls = 0

    def _remote(self) -> None:
        self.calls += 1
        if self.latency:
            time.sleep(self.latency)

    # ---------------------------------------------------------------- market data
    def market(self, as_of: str, curve: str) -> MarketHandle:
        self._remote()
        if curve not in _CURVES:
            raise ValueError(f"Meridian: unknown curve {curve!r}; known: {sorted(_CURVES)}")
        d = date.fromisoformat(as_of)
        if not _bday(d):
            raise MarketClosed(f"{as_of} is not a Meridian business day")
        if d < FIRST_DATE or d > LAST_DATE or d in self.gaps:
            raise NoData(f"no {curve} snapshot for {as_of}")
        return MarketHandle(self, d, curve, _zeros_for(d))

    def scenario(self, market: MarketHandle, shift_bp: float = 0.0, pillar_shifts_bp: Optional[dict] = None,
                 valuation_date: Optional[str] = None, hold: str = "ZEROS") -> MarketHandle:
        """A what-if copy of `market` (one remote call). `shift_bp` moves every pillar zero rate (bp,
        + = up); `pillar_shifts_bp` moves single pillars, keyed like BUCKET_DV01 ("USD.SOFR:10Y").
        `valuation_date` (ISO, any calendar day) re-values the snapshot from that date: hold="ZEROS"
        (default) keeps the pillar zero rates, so the curve rolls; hold="FORWARDS" keeps every
        forward (DF(x) / DF(valuation_date)). Shifts apply before the date moves."""
        self._remote()
        if hold not in HOLD:
            raise ValueError(f"Meridian: hold must be one of {HOLD}, got {hold!r}")
        names = [f"{market.curve}:{p}" for p, _ in _PILLARS]
        extra = dict(pillar_shifts_bp or {})
        bad = sorted(set(extra) - set(names))
        if bad:
            raise ValueError(f"Meridian: unknown pillar(s) {bad}; known: {names}")
        if isinstance(market, _ForwardHeldHandle):
            raise ValueError("Meridian: scenario() of a forward-held scenario is not supported; shift the snapshot first")
        zeros = tuple(z + (shift_bp + extra.get(n, 0.0)) * 1e-4 for z, n in zip(market._zeros, names))
        shifted = MarketHandle(self, market._d, market.curve, zeros)
        if valuation_date is None:
            return shifted
        d = date.fromisoformat(valuation_date)
        if hold == "ZEROS":
            return MarketHandle(self, d, market.curve, zeros)
        return _ForwardHeldHandle(shifted, d)

    def discount_factors(self, market: MarketHandle, dates: List[str]) -> List[float]:
        """DF from the market's valuation date to each ISO date (one remote call)."""
        self._remote()
        return [market._df_and_grad(date.fromisoformat(x))[0] for x in dates]

    # ---------------------------------------------------------------- date helpers
    def spot_date(self, market: MarketHandle) -> str:
        """as_of + 2 Meridian business days."""
        self._remote()
        return _spot(market._d).isoformat()

    def maturity(self, market: MarketHandle, start: str, tenor: str) -> str:
        """start + tenor, rolled to the following business day."""
        self._remote()
        return _following(_add_tenor(date.fromisoformat(start), tenor)).isoformat()

    # ---------------------------------------------------------------- trades
    def swap(self, currency: str, start: str, end: str, fixed_rate_pct: Optional[float] = None,
             notional: float = 1e6, direction: str = "PAY") -> TradeSpec:
        if notional <= 0:
            raise ValueError("Meridian: notional must be positive; use direction for the side")
        if direction not in ("PAY", "RECEIVE"):
            raise ValueError(f"Meridian: direction must be 'PAY' or 'RECEIVE', got {direction!r}")
        date.fromisoformat(start)  # validate
        return TradeSpec(currency, start, end, None if fixed_rate_pct is None else float(fixed_rate_pct), float(notional), direction)

    # ---------------------------------------------------------------- pricing
    def price(self, specs: List[TradeSpec], market: MarketHandle, measures: List[str]) -> List[dict]:
        """Batch-price `specs` on `market`: ONE remote call however many specs."""
        self._remote()
        bad = [m for m in measures if m not in MEASURES]
        if bad:
            raise ValueError(f"Meridian: unknown measure(s) {bad}; known: {MEASURES}")
        return [self._price_one(s, market, measures) for s in specs]

    def _price_one(self, s: TradeSpec, mk: MarketHandle, measures) -> dict:
        if _CURVES[mk.curve] != s.currency:
            raise ValueError(f"Meridian: {s.currency} trade on {mk.curve} market")
        start = date.fromisoformat(s.start)
        if _TENOR.match(s.end.strip().upper()):  # vendor convention: tenor counts from THIS market's spot
            end = _following(_add_tenor(_spot(mk._d), s.end))
        else:
            end = date.fromisoformat(s.end)
        n_pil = len(_PILLARS)

        # annual coupons from start; final (possibly stub) coupon at end; only those after as_of
        cpns, prev, i = [], start, 1
        while True:
            c = min(start + relativedelta(years=i), end)
            if c > mk._d:
                cpns.append((c, (c - prev).days / 365.0, prev))
            if c >= end:
                break
            prev, i = c, i + 1
        ann, ann_g = 0.0, [0.0] * n_pil
        for c, acc, _p in cpns:
            df, g = mk._df_and_grad(c)
            ann += acc * df
            ann_g = [a + acc * gi for a, gi in zip(ann_g, g)]
        if end > mk._d:
            # float coupons on the fixed schedule, each N*(DF(prev)-DF(c)): they telescope to
            # N*(DF(start of the current period) - DF(end)). ponytail: the current period uses
            # today's curve (DF(prev) > 1 once it has started) -- no fixings history. Fine for a toy;
            # a real library uses fixings.
            dfs, gs_ = mk._df_and_grad(cpns[0][2])
            dfe, ge = mk._df_and_grad(end)
            flt, flt_g = dfs - dfe, [a - b for a, b in zip(gs_, ge)]
        else:
            flt, flt_g = 0.0, [0.0] * n_pil
        par = flt / ann if ann > 0 else float("nan")
        k = par if s.fixed_rate_pct is None else s.fixed_rate_pct / 100.0
        sgn = 1.0 if s.direction == "PAY" else -1.0
        pv = sgn * s.notional * (flt - k * ann)
        dpv_dz = [sgn * s.notional * (fg - k * ag) for fg, ag in zip(flt_g, ann_g)]
        # receiver-positive: PV change for a 1bp DECREASE, to first order = -1e-4 * dPV/dz
        bucket = {f"{mk.curve}:{name}": -1e-4 * g for (name, _), g in zip(_PILLARS, dpv_dz)}

        out = {}
        for m in measures:
            if m == "PV":
                out[m] = pv
            elif m == "PAR_PCT":
                out[m] = par * 100.0
            elif m == "DV01":
                out[m] = sum(bucket.values())
            elif m == "BUCKET_DV01":
                out[m] = dict(bucket)
            elif m == "FIXED_PCT":
                out[m] = k * 100.0
            elif m == "CASHFLOWS":
                # vendor: positive amounts, the holder's direction; only flows PV still includes
                fixed_dir, float_dir = ("PAY", "RECEIVE") if s.direction == "PAY" else ("RECEIVE", "PAY")
                rows = []
                for c, acc, p in cpns:
                    rows.append({"date": c.isoformat(), "amount": abs(s.notional * k * acc), "direction": fixed_dir if k >= 0 else float_dir, "leg": "FIXED"})
                    proj = s.notional * (mk._df_and_grad(p)[0] / mk._df_and_grad(c)[0] - 1.0)   # projected float coupon
                    rows.append({"date": c.isoformat(), "amount": abs(proj), "direction": float_dir if proj >= 0 else fixed_dir, "leg": "FLOAT"})
                out[m] = rows
        return out


def connect(env: str = "SIM", gaps: Optional[Iterable[str]] = None, latency: float = 0.0) -> Client:
    """Open a Meridian session. Only the offline "SIM" environment exists (this SDK is fictional)."""
    if env != "SIM":
        raise ConnectionError(f"Meridian: environment {env!r} unavailable; this fictional SDK only has 'SIM'")
    return Client(env, DEFAULT_GAPS if gaps is None else gaps, latency)
