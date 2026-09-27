"""`wrap(SnapshotPricer) -> QLPricer`: the QuantLib view of a neutral market snapshot (spec D3).

The wrapper owns everything QuantLib-specific that a snapshot can supply: the discount curve (only the snapshot tag `log_linear_df` is honoured; any other tag is a
`ConfigError`), the calendars (built from the snapshot's holiday sets, never from a named QuantLib calendar), the fixings (unit aware; QuantLib wants decimals), and the
bond reference data, quotes and on-the-run aliases. It is immutable; QuantLib objects are built lazily, kept in a private dict (dropped when pickled), and derived,
expensive products (the risk curves of the delta ladder, the on-the-run yield curve) are memoised HERE, on the wrapped pricer, never on the snapshot.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from ...errors import ConfigError, MarketDataUnavailable
from ...pricer import PricerBase
from ...snapshot import CalendarData, CurveSnapshot, Quote, Security, SnapshotPricer
from . import _compat as C

ql = C.ql


class QLPricer(PricerBase):
    """Snapshot at `ts`, ready for the QuantLib swap and bond of this package. Created by `wrap`; do not mutate."""

    LOOKUPS = ("security", "quote")

    def __init__(self, source: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None):
        snap = getattr(source, "snapshot", None)
        if snap is None:
            raise ConfigError(f"wrap needs a SnapshotPricer (a pricer holding a MarketSnapshot), got {type(source).__name__}", code="CFG-WRAP")
        object.__setattr__(self, "_frozen", False)
        self.snapshot = snap
        self.ts = source.ts
        self.reference_date = source.reference_date
        self.digest = getattr(source, "digest", snap.digest())
        self.curve_name = self._choose(curve, snap.curves, "curve")
        self.fixings_name = self._choose(fixings, snap.fixings, "fixings")
        if self.curve_name is not None:
            c = snap.curves[self.curve_name]
            if c.interpolation != "log_linear_df":
                raise ConfigError(f"curve {c.name!r} has interpolation {c.interpolation!r}; this adapter honours only 'log_linear_df'", code="CFG-INTERPOLATION")
            if c.value_kind != "discount_factor":
                raise ConfigError(f"curve {c.name!r} has value_kind {c.value_kind!r}; this adapter needs discount factors", code="CFG-INTERPOLATION")
        self._lazy: Dict[Any, Any] = {}
        object.__setattr__(self, "_frozen", True)

    @staticmethod
    def _choose(name: Optional[str], available: Mapping[str, Any], what: str) -> Optional[str]:
        if name is not None:
            if name not in available:
                raise ConfigError(f"the snapshot has no {what} {name!r}; it has {sorted(available)}", code="CFG-WRAP")
            return name
        if len(available) == 1:
            return next(iter(available))
        if len(available) > 1:
            raise ConfigError(f"the snapshot has several {what}s {sorted(available)}: name one in wrap(..., {what}=...)", code="CFG-WRAP")
        return None

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        object.__setattr__(self, name, value)

    def __getstate__(self) -> Dict[str, Any]:
        st = dict(self.__dict__)
        st["_lazy"] = {}
        return st

    def __setstate__(self, st: Dict[str, Any]) -> None:
        self.__dict__.update(st)

    # ------------------------------------------------------------------ memo
    def memo(self, key: Any, build: Callable[[], Any]) -> Any:
        """Per-wrapped-pricer cache (the wrapper is immutable; this dict is its private scratch)."""
        if key not in self._lazy:
            self._lazy[key] = build()
        return self._lazy[key]

    # ------------------------------------------------------------------ market data
    def curve_snapshot(self) -> CurveSnapshot:
        if self.curve_name is None:
            raise ConfigError("this snapshot has no curve: a swap needs one", code="CFG-WRAP")
        return self.snapshot.curves[self.curve_name]

    def curve_nodes(self) -> Tuple[Tuple[dt.date, ...], Tuple[float, ...]]:
        c = self.curve_snapshot()
        return c.node_dates, c.values

    def ql_curve(self) -> "ql.DiscountCurve":
        return self.memo("ql_curve", lambda: C.discount_curve(*self.curve_nodes()))

    def calendar(self, name: str) -> "ql.Calendar":
        def build() -> "ql.Calendar":
            cd: Optional[CalendarData] = self.snapshot.calendars.get(name)
            if cd is None:
                raise ConfigError(f"the snapshot has no calendar {name!r}; it has {sorted(self.snapshot.calendars)}", code="CFG-CALENDAR")
            return C.calendar_from_holidays(cd.name, cd.holidays, cd.weekmask)

        return self.memo(("calendar", name), build)

    def fixings_decimal(self) -> Optional[Tuple[Tuple[dt.date, ...], Tuple[float, ...]]]:
        """(dates, DECIMAL rates) of the selected fixings series, or None. The snapshot unit is honoured: percent -> /100."""
        if self.fixings_name is None:
            return None
        f = self.snapshot.fixings[self.fixings_name]
        scale = 0.01 if f.unit == "percent" else 1.0
        return f.dates, tuple(v * scale for v in f.values)

    def fixings_percent_before(self, ref: dt.date) -> Optional[Tuple[dt.date, float]]:
        """(date, PERCENT rate) of the newest fixing dated strictly before `ref`, or None (financing proxy)."""
        if self.fixings_name is None:
            return None
        f = self.snapshot.fixings[self.fixings_name]
        scale = 1.0 if f.unit == "percent" else 100.0
        prior = [(d, v * scale) for d, v in zip(f.dates, f.values) if d < ref]
        return prior[-1] if prior else None

    # ------------------------------------------------------------------ bond reference data (optional lookups)
    def security(self, token: str) -> Security:
        q = self.snapshot.quotes
        if q is None:
            raise MarketDataUnavailable(self.ts, {"security": token}, "the snapshot carries no bond quotes")
        key = q.aliases.get(token.strip(), token.strip())
        sec = q.securities.get(key)
        if sec is None:
            raise MarketDataUnavailable(self.ts, {"security": token}, f"unknown security {token!r}; aliases {sorted(q.aliases)[:10]}")
        return sec

    def quote(self, security: str) -> Quote:
        q = self.snapshot.quotes
        got = None if q is None else q.quotes.get(security)
        if got is None:
            raise MarketDataUnavailable(self.ts, {"security": security}, f"no quote for {security} at {self.ts}")
        return got

    def describe(self) -> Mapping[str, Any]:
        s = self.snapshot
        return {"type": type(self).__name__, "ts": str(self.ts), "reference_date": str(self.reference_date), "digest": self.digest, "curve": self.curve_name,
                "fixings": self.fixings_name, "calendars": sorted(s.calendars), "n_quotes": 0 if s.quotes is None else len(s.quotes.quotes)}


def wrap(pricer: SnapshotPricer, *, curve: Optional[str] = None, fixings: Optional[str] = None) -> QLPricer:
    """The QuantLib view of a snapshot pricer. `curve` / `fixings` name the curve and fixings series to use when the snapshot holds several (default: the only one)."""
    if isinstance(pricer, QLPricer):
        return pricer
    return QLPricer(pricer, curve=curve, fixings=fixings)
