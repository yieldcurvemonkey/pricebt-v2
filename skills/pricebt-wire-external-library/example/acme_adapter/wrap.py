"""`wrap(SnapshotPricer) -> AcmePricer`: the acmelib view of a neutral market snapshot.

This is the ONE place a snapshot becomes acmelib objects, and it does four things acmelib forces on it:

* the discount curve: the snapshot holds DISCOUNT FACTORS at node dates (tag `log_linear_df`), acmelib wants ZERO RATES on its own ACT/365 basis, flat-forward between
  pillars. Only that tag is honoured (any other is a `ConfigError`, never a silent substitute); the conversion is z = -ln(DF) / t, and it round-trips to round-off.
* the fixings: the snapshot says percent or decimal in `unit`, acmelib wants decimals keyed by ISO date.
* the calendars: acmelib has its own global registry keyed by its own names; every snapshot calendar is registered there (content-addressed, see `_compat.load_calendar`).
  Nothing is left to acmelib's defaults: a calendar it does not have would make it silently use a weekend-only one.
* memoisation: a snapshot is wrapped ONCE per `SnapshotPricer` (a weak memo); the wrapped pricer is immutable data plus the acmelib `Market`, and re-registers its
  calendars when unpickled (the registry is process-global, a new process starts empty).
"""
from __future__ import annotations

import math
import weakref
from typing import Any, Dict, Mapping, Optional, Tuple

import acmelib as acme
from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.pricer import PricerBase
from pricebt.snapshot import CurveSnapshot, SnapshotPricer

from . import _compat as C


def _choose(name: Optional[str], available: Mapping[str, Any], what: str) -> Optional[str]:
    if name is not None:
        if name not in available:
            raise ConfigError(f"the snapshot has no {what} {name!r}; it has {sorted(available)}", code="CFG-WRAP")
        return name
    if len(available) > 1:
        raise ConfigError(f"the snapshot has several {what}s {sorted(available)}: name one in wrap(..., {what}=...)", code="CFG-WRAP")
    return next(iter(available), None)


def zero_curve(c: CurveSnapshot) -> acme.ZeroCurve:
    """Discount factors at node dates -> acmelib's zero-rate curve."""
    if c.interpolation != "log_linear_df":
        raise ConfigError(f"curve {c.name!r} has interpolation {c.interpolation!r}; this adapter honours only 'log_linear_df' (acmelib: flat_forward)", code="CFG-INTERPOLATION")
    if c.value_kind != "discount_factor":
        raise ConfigError(f"curve {c.name!r} has value_kind {c.value_kind!r}; this adapter needs discount factors", code="CFG-INTERPOLATION")
    a = c.reference_date
    zeros = [-math.log(v) / ((d - a).days / acme.ZeroCurve.BASIS_DAYS) for d, v in zip(c.node_dates[1:], c.values[1:])]
    return acme.ZeroCurve(C.iso(a), [C.iso(d) for d in c.node_dates[1:]], zeros)


class AcmePricer(PricerBase):
    """A snapshot at `ts`, ready for the acmelib swap of this package. Created by `wrap`; do not mutate."""

    LOOKUPS = ()

    def __init__(self, source: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None):
        snap = getattr(source, "snapshot", None)
        if snap is None:
            raise ConfigError(f"wrap needs a SnapshotPricer (a pricer holding a MarketSnapshot), got {type(source).__name__}", code="CFG-WRAP")
        self.snapshot, self.ts, self.reference_date = snap, source.ts, source.reference_date
        self.digest = getattr(source, "digest", snap.digest())
        self.curve_name = _choose(curve, snap.curves, "curve")
        self.fixings_name = _choose(fixings, snap.fixings, "fixings")
        fx: Dict[str, float] = {}
        if self.fixings_name is not None:
            f = snap.fixings[self.fixings_name]
            scale = 0.01 if f.unit == "percent" else 1.0  # acmelib: decimals
            fx = {C.iso(d): v * scale for d, v in zip(f.dates, f.values)}
        self.market: Optional[acme.Market] = None if self.curve_name is None else acme.Market(zero_curve(snap.curves[self.curve_name]), fx)
        self._calendars: Dict[str, str] = {}
        self._load_calendars()

    def _load_calendars(self) -> None:
        self._calendars = {name: C.load_calendar(cd) for name, cd in self.snapshot.calendars.items()}

    def __setstate__(self, st: Dict[str, Any]) -> None:
        self.__dict__.update(st)
        self._load_calendars()  # the registry is process-global: a fresh process (unpickling) starts without our calendars

    def calendar(self, name: str) -> str:
        """The name acmelib has the snapshot's calendar `name` under."""
        got = self._calendars.get(name)
        if got is None:
            raise ConfigError(f"the snapshot has no calendar {name!r}; it has {sorted(self._calendars)}", code="CFG-CALENDAR")
        return got

    def need_market(self) -> acme.Market:
        if self.market is None:
            raise MarketDataUnavailable(self.ts, {}, "this snapshot carries no discount curve")
        return self.market

    def describe(self) -> Mapping[str, Any]:
        return {"type": type(self).__name__, "ts": str(self.ts), "reference_date": str(self.reference_date), "digest": self.digest, "curve": self.curve_name,
                "fixings": self.fixings_name, "calendars": dict(self._calendars)}


_WRAPPED: "weakref.WeakKeyDictionary[SnapshotPricer, Dict[Tuple[Optional[str], Optional[str]], AcmePricer]]" = weakref.WeakKeyDictionary()


def wrap(pricer: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None) -> AcmePricer:
    """The acmelib view of a snapshot pricer, built once per snapshot pricer (`curve` / `fixings` name the entry when the snapshot holds several)."""
    if isinstance(pricer, AcmePricer):
        return pricer
    memo = _WRAPPED.setdefault(pricer, {})
    key = (curve, fixings)
    if key not in memo:
        memo[key] = AcmePricer(pricer, curve=curve, fixings=fixings)
    return memo[key]
