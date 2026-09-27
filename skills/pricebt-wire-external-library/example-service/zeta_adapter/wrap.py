"""`wrap(SnapshotPricer) -> ZetaPricer`: the zeta view of a neutral market snapshot, and the ONE place a snapshot becomes a zeta market.

What zeta forces on the conversion (ZETA_DOCS.md section 2):

* the curve: the snapshot holds DISCOUNT FACTORS at node dates (tag `log_linear_df`), zeta wants CONTINUOUSLY COMPOUNDED ZERO RATES IN BASIS POINTS on a declared day count, log-linear in
  the discount factor between nodes and flat-forward beyond the last node. Declared `ACT/365`, so `z_bp = -ln(DF) * 365 / days * 1e4` round-trips to round-off. Only that tag is honoured;
* the fixings: the snapshot says percent or decimal in `unit`, zeta wants BASIS POINTS keyed by ISO date;
* the calendar: a zeta market holds ONE calendar (holidays + Saturday and Sunday; its name is a label). The snapshot's calendar is loaded; a weekmask that is not Monday-Friday is refused;
* the upload: zeta is a service and uploading is the expensive step (120 ms simulated, section 9). A snapshot is uploaded LAZILY, on the first price() that needs it, and ONCE: the market id
  lives in the wrapped pricer's private memo (which `MarketData` builds once per snapshot and stamp). Worlds DERIVED from two snapshots (the layers) are content-addressed in the same memo.
"""
from __future__ import annotations

import hashlib
import json
import math
import weakref
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.pricer import PricerBase
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, SnapshotPricer
from pricebt.timeutil import weekmask_names

from . import _compat as C

_WEEK = "Mon Tue Wed Thu Fri"
_BP = {"percent": 100.0, "decimal": 1e4}  # a fixing in the snapshot's unit -> zeta's basis points


def _choose(name: Optional[str], available: Mapping[str, Any], what: str) -> Optional[str]:
    if name is not None:
        if name not in available:
            raise ConfigError(f"the snapshot has no {what} {name!r}; it has {sorted(available)}", code="CFG-WRAP")
        return name
    if len(available) > 1:
        raise ConfigError(f"the snapshot has several {what}s {sorted(available)}: name one in wrap(..., {what}=...)", code="CFG-WRAP")
    return next(iter(available), None)


def zero_nodes(c: CurveSnapshot) -> List[List[float]]:
    """Discount factors at node dates -> zeta's `[days, zero_rate_bp]` nodes (ACT/365, continuously compounded); the anchor (day 0) is implicit."""
    if c.interpolation != "log_linear_df":
        raise ConfigError(f"curve {c.name!r} has interpolation {c.interpolation!r}; this adapter honours only 'log_linear_df' (zeta: log-linear in the discount factor)", code="CFG-INTERPOLATION")
    if c.value_kind != "discount_factor":
        raise ConfigError(f"curve {c.name!r} has value_kind {c.value_kind!r}; this adapter needs discount factors", code="CFG-INTERPOLATION")
    a = c.reference_date
    return [[(d - a).days, -math.log(v) * 365.0 / (d - a).days * 1e4] for d, v in zip(c.node_dates[1:], c.values[1:])]


def _calendar(cd: CalendarData) -> Dict[str, Any]:
    if weekmask_names(cd.weekmask) != _WEEK:
        raise ConfigError(f"calendar {cd.name!r} has weekmask {cd.weekmask!r}; zeta knows only Saturday and Sunday as weekend days ({_WEEK!r})", code="CFG-CALENDAR")
    return {"name": cd.name, "holidays": [h.isoformat() for h in cd.holidays]}


def _fixings(f: Optional[FixingsSeries]) -> Dict[str, float]:
    if f is None:
        return {}
    k = _BP[f.unit]
    return {C.iso(d): v * k for d, v in zip(f.dates, f.values)}


class ZetaPricer(PricerBase):
    """A snapshot at `ts`, ready for the zeta swap of this package. Created by `wrap`; immutable (the memo is private and only fills)."""

    LOOKUPS = ()

    def __init__(self, source: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None, calendar: Optional[str] = None):
        snap = getattr(source, "snapshot", None)
        if snap is None:
            raise ConfigError(f"wrap needs a SnapshotPricer (a pricer holding a MarketSnapshot), got {type(source).__name__}", code="CFG-WRAP")
        object.__setattr__(self, "snapshot", snap)
        object.__setattr__(self, "ts", source.ts)
        object.__setattr__(self, "reference_date", source.reference_date)
        object.__setattr__(self, "digest", getattr(source, "digest", snap.digest()))
        object.__setattr__(self, "curve_name", _choose(curve, snap.curves, "curve"))
        object.__setattr__(self, "fixings_name", _choose(fixings, snap.fixings, "fixings"))
        object.__setattr__(self, "calendar_name", _choose(calendar, snap.calendars, "calendar"))
        market: Optional[Dict[str, Any]] = None
        if self.curve_name is not None:
            c = snap.curves[self.curve_name]
            market = {"asof": C.iso(c.reference_date), "curve": {"name": "SOFR", "day_count": "ACT/365", "nodes": zero_nodes(c)},
                      "fixings": {"SOFR": _fixings(snap.fixings.get(self.fixings_name))}}
            if self.calendar_name is not None:
                market["calendar"] = _calendar(snap.calendars[self.calendar_name])
        object.__setattr__(self, "_market", market)
        object.__setattr__(self, "_client", C.client())
        object.__setattr__(self, "_memo", {})

    # ---- immutability, pickling: a market id belongs to the client that uploaded it, so it never travels
    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ZetaPricer is immutable")

    def __getstate__(self) -> Dict[str, Any]:
        st = dict(self.__dict__)
        st.pop("_memo", None)
        st.pop("_client", None)
        return st

    def __setstate__(self, st: Dict[str, Any]) -> None:
        self.__dict__.update(st)
        self.__dict__["_client"] = C.client()
        self.__dict__["_memo"] = {}

    # ---- the zeta side
    @property
    def client(self) -> Any:
        return self._client

    def memo(self, key: Any, fn: Callable[[], Any]) -> Any:
        """Per-snapshot cache (market ids, price answers): a pricer is immutable data, so a cached answer can never go stale."""
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def need_market(self) -> Dict[str, Any]:
        """The zeta market dict of this snapshot (do not mutate: copy it)."""
        if self._market is None:
            raise MarketDataUnavailable(self.ts, {}, "this snapshot carries no discount curve")
        return self._market

    def market_id(self) -> str:
        """The id of this snapshot's market: uploaded on the first call, ONCE."""
        return self.memo("market_id", lambda: C.upload(self._client, self.need_market(), self.ts, "snapshot"))

    def world_id(self, market: Mapping[str, Any]) -> str:
        """The id of a market DERIVED from snapshots (a rolled or shocked world): one upload per distinct content."""
        key = ("world", hashlib.sha256(json.dumps(market, sort_keys=True).encode("utf8")).hexdigest())
        return self.memo(key, lambda: C.upload(self._client, market, self.ts, "derived"))

    def price(self, mid: str, as_of: Any, trades: List[Mapping[str, Any]], measures: List[str], scenario: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
        return C.price(self._client, mid, as_of, trades, measures, self.ts, scenario)

    def describe(self) -> Mapping[str, Any]:
        return {"type": type(self).__name__, "ts": str(self.ts), "reference_date": str(self.reference_date), "digest": self.digest, "curve": self.curve_name,
                "fixings": self.fixings_name, "calendar": self.calendar_name}


_WRAPPED: "weakref.WeakKeyDictionary[SnapshotPricer, Dict[Tuple[Optional[str], Optional[str], Optional[str]], ZetaPricer]]" = weakref.WeakKeyDictionary()


def wrap(pricer: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None, calendar: Optional[str] = None) -> ZetaPricer:
    """The zeta view of a snapshot pricer, built once per snapshot pricer (`curve` / `fixings` / `calendar` name the entry when the snapshot holds several)."""
    if isinstance(pricer, ZetaPricer):
        return pricer
    memo = _WRAPPED.setdefault(pricer, {})
    key = (curve, fixings, calendar)
    if key not in memo:
        memo[key] = ZetaPricer(pricer, curve=curve, fixings=fixings, calendar=calendar)
    return memo[key]
