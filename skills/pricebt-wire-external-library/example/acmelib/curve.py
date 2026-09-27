"""acmelib curves: a ZERO-rate curve (decimals, continuously compounded, ACT/365) and a `Market` (curve + published fixings).

A `ZeroCurve` is anchored at a date and has one zero rate per pillar date (NOT a discount factor: the adapter converts). Between pillars it is flat-forward (linear
in zero-rate x time, i.e. log-linear in the discount factor) and it extends the last forward. The three primitives layers are built from live here: `shifted` (a
shifted market), `rolled` (the same market moved to a later anchor, unchanged in TENOR space) and `resampled` (the same curve read at other pillar dates).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Union

from pricebt.testing.refstack import RefCurve  # the discount arithmetic is reused from pricebt: acmelib is NOT an independent implementation (README)

from .calendars import parse_date
from .errors import BadInput, CurveError


class ZeroCurve:
    BASIS_DAYS = 365.0  # acmelib's zero rates are ACT/365: t = days / 365

    def __init__(self, anchor: str, pillars: Sequence[str], zeros: Sequence[float], *, interpolation: str = "flat_forward"):
        if interpolation != "flat_forward":
            raise BadInput(f"acmelib curves interpolate 'flat_forward' only, got {interpolation!r}")
        a, ps = parse_date(anchor), [parse_date(p) for p in pillars]
        if not ps or len(ps) != len(zeros):
            raise CurveError("a curve needs at least one pillar and one zero rate per pillar")
        if ps[0] <= a or any(y <= x for x, y in zip(ps, ps[1:])):
            raise CurveError("pillars must be after the anchor and strictly ascending")
        zs = tuple(float(z) for z in zeros)
        if not all(math.isfinite(z) for z in zs):
            raise CurveError("zero rates must be finite")
        self.anchor, self.pillars, self.zeros = anchor, tuple(pillars), zs
        self._cache: Optional[RefCurve] = None

    def _curve(self) -> RefCurve:
        if self._cache is None:
            a = parse_date(self.anchor)
            ds = [a] + [parse_date(p) for p in self.pillars]
            self._cache = RefCurve(a, ds, [1.0] + [math.exp(-z * (d - a).days / self.BASIS_DAYS) for d, z in zip(ds[1:], self.zeros)])
        return self._cache

    def t(self, iso: str) -> float:
        """ACT/365 year fraction from the anchor."""
        d = parse_date(iso)
        if d < parse_date(self.anchor):
            raise CurveError(f"{iso} is before the curve's anchor {self.anchor}")
        return (d - parse_date(self.anchor)).days / self.BASIS_DAYS

    def df(self, iso: str) -> float:
        self.t(iso)
        return self._curve().df(parse_date(iso))

    def zero_rate(self, iso: str) -> float:
        t = self.t(iso)
        if t <= 0.0:
            raise CurveError("a zero rate is undefined at the anchor itself")
        return -math.log(self.df(iso)) / t

    def shifted(self, shifts: Union[float, Sequence[float]]) -> "ZeroCurve":
        """Every zero rate moved by `shifts` (decimal): one number for a parallel move, or one per pillar."""
        ss = [float(shifts)] * len(self.zeros) if isinstance(shifts, (int, float)) else [float(s) for s in shifts]
        if len(ss) != len(self.zeros):
            raise CurveError(f"{len(ss)} shifts for {len(self.zeros)} pillars")
        return ZeroCurve(self.anchor, self.pillars, [z + s for z, s in zip(self.zeros, ss)])

    def rolled(self, new_anchor: str) -> "ZeroCurve":
        """The curve anchored at a later date with the SAME zero rate at the same tenor (pillars move with the anchor): a market that did not move in tenor space."""
        n = (parse_date(new_anchor) - parse_date(self.anchor)).days
        if n < 0:
            raise CurveError("a curve can only be rolled forward")
        return ZeroCurve(new_anchor, [(parse_date(p) + dt.timedelta(days=n)).isoformat() for p in self.pillars], self.zeros)

    def resampled(self, pillars: Sequence[str]) -> "ZeroCurve":
        """The same curve read at other pillar dates."""
        return ZeroCurve(self.anchor, pillars, [self.zero_rate(p) for p in pillars])


class Market:
    """A curve and the published overnight fixings (ISO date -> DECIMAL rate) a started swap needs. Immutable by convention; its `shifted` / `rolled` return new markets."""

    def __init__(self, curve: ZeroCurve, fixings: Optional[Mapping[str, float]] = None):
        self.curve, self.fixings = curve, dict(fixings or {})
        self._books: Dict[Any, Any] = {}  # private: the pricing views built from this market, one per valuation date (see swap.py)

    def __getstate__(self) -> Dict[str, Any]:
        return {"curve": self.curve, "fixings": self.fixings, "_books": {}}  # pricing views are caches: never pickled

    def shifted(self, shifts: Union[float, Sequence[float]]) -> "Market":
        return Market(self.curve.shifted(shifts), self.fixings)

    def rolled(self, new_anchor: str, fixings: Optional[Mapping[str, float]] = None) -> "Market":
        """The market at a later anchor, unchanged in tenor space; `fixings` (default: this market's) are the ones published by then."""
        return Market(self.curve.rolled(new_anchor), self.fixings if fixings is None else fixings)
