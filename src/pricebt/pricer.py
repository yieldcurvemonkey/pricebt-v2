"""Pricer / MarketDataProvider contracts, the time-mapping model, and generic provider wrappers."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

import numpy as np
import pandas as pd

from .errors import ConfigError, StaleSnapshot


@runtime_checkable
class Pricer(Protocol):
    """Immutable market snapshot at one timestamp: `ts`, `describe()` and `lookup(name, **kw)`, plus an optional `reference_date`.

    A pricer may expose any objects as attributes; a binding reaches them by reference (`@pricer.<attr>`), so the core never knows their names. Lookups whose
    meaning is a convention are optional capabilities declared in a schema (`pricer_capabilities`); no core code path requires one.
    """

    ts: pd.Timestamp

    def lookup(self, name: str, /, **kw: Any) -> Any: ...

    def describe(self) -> Mapping[str, Any]: ...


class PricerBase:
    """Convenience base: subclasses set `ts`, `reference_date` and list their lookup method names in LOOKUPS."""

    LOOKUPS: ClassVar[Tuple[str, ...]] = ()
    ts: pd.Timestamp
    reference_date: dt.date

    def lookup(self, name: str, /, **kw: Any) -> Any:
        if name not in self.LOOKUPS or not callable(getattr(self, name, None)):
            raise LookupError(f"{type(self).__name__} has no lookup {name!r}; available: {sorted(self.LOOKUPS)}")
        return getattr(self, name)(**kw)

    def describe(self) -> Mapping[str, Any]:
        return {"type": type(self).__name__, "ts": str(getattr(self, "ts", None)), "reference_date": str(getattr(self, "reference_date", None))}


@runtime_checkable
class MarketDataProvider(Protocol):
    """timestamp (+ request) -> Pricer. Must not mutate `request`; raises MarketDataUnavailable on a miss."""

    def get_pricer(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> Pricer: ...


@dataclass(frozen=True)
class TimeMapping:
    """How a requested timestamp maps to a stored snapshot.

    mode 'asof': latest snapshot stamp <= ts within `max_staleness` (None = unbounded, requires unbounded_ok).
    mode 'exact': stamp == ts.   mode 'date': latest stamp on the same local date that is already visible at ts
    (`eod_visible_at`: local time-of-day from which an end-of-day stamp becomes visible; None = stamp itself).
    """

    mode: str = "asof"
    max_staleness: Optional[pd.Timedelta] = pd.Timedelta(minutes=5)
    eod_visible_at: Optional[dt.time] = None
    unbounded_ok: bool = False
    tz: str = "America/New_York"

    def __post_init__(self) -> None:
        if self.mode not in ("asof", "exact", "date"):
            raise ConfigError(f"unknown time mode {self.mode!r}", code="TIME")
        if self.mode == "asof" and self.max_staleness is None and not self.unbounded_ok:
            raise ConfigError("asof mapping needs max_staleness (or unbounded_ok: true)", code="TIME")


class SnapshotIndex:
    """Sorted snapshot stamps with the ONE asof/exact/date selection routine every frame-backed provider uses.

    A stamp becomes VISIBLE at `visible(stamp) = max(stamp, <local date of the stamp> at eod_visible_at)` (eod_visible_at can only delay a row,
    never expose it early). asof: latest row visible at ts, age measured from the stamp; exact: a row whose visible instant == ts;
    date: latest row stamped on ts's local date and visible at ts.
    """

    def __init__(self, stamps: Sequence[pd.Timestamp]):
        idx = pd.DatetimeIndex(stamps)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        idx = idx.as_unit("ns")
        order = np.argsort(idx.asi8, kind="stable")
        self._order = order
        self.stamps = idx[order]
        self._ns = self.stamps.asi8
        self._vis: Dict[Tuple[Any, str], np.ndarray] = {}

    def __len__(self) -> int:
        return len(self.stamps)

    def _visible_ns(self, mapping: TimeMapping) -> np.ndarray:
        if mapping.eod_visible_at is None:
            return self._ns
        key = (mapping.eod_visible_at, mapping.tz)
        got = self._vis.get(key)
        if got is None:
            t = mapping.eod_visible_at
            wall = self.stamps.tz_convert(mapping.tz).normalize().tz_localize(None) + pd.Timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)  # naive LOCAL wall clock
            floor = wall.tz_localize(mapping.tz, ambiguous=True, nonexistent="shift_forward")  # a wall-clock time, so a 23- or 25-hour day is not an hour off
            got = np.maximum(self._ns, floor.tz_convert("UTC").as_unit("ns").asi8)
            self._vis[key] = got
        return got

    def select(self, ts: pd.Timestamp, mapping: TimeMapping) -> Optional[int]:
        """Position (into the ORIGINAL stamp order) of the selected snapshot, or None. Raises StaleSnapshot when asof finds only an old row."""
        if len(self.stamps) == 0:
            return None
        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            t = t.tz_localize(mapping.tz)
        ns = t.as_unit("ns").value
        vis = self._visible_ns(mapping)
        j = int(np.searchsorted(vis, ns, side="right")) - 1  # latest row visible at ts
        if mapping.mode == "exact":
            return int(self._order[j]) if j >= 0 and vis[j] == ns else None
        if mapping.mode == "asof":
            if j < 0:
                return None
            if mapping.max_staleness is not None and (t - self.stamps[j]) > mapping.max_staleness:
                raise StaleSnapshot(ts, None, f"newest visible snapshot {self.stamps[j]} older than {mapping.max_staleness}")
            return int(self._order[j])
        day = t.tz_convert(mapping.tz).date()
        k = j
        while k >= 0:
            st = self.stamps[k].tz_convert(mapping.tz)
            if st.date() < day:
                return None
            if st.date() == day:
                return int(self._order[k])
            k -= 1
        return None

    def within(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
        return self.stamps[(self.stamps >= start) & (self.stamps <= end)]


class FunctionMDP:
    """Wrap `fn(ts, request) -> Pricer` as a MarketDataProvider."""

    def __init__(self, fn: Callable[[pd.Timestamp, Mapping[str, Any]], Pricer], *, available: Optional[Callable[[pd.Timestamp, pd.Timestamp], Sequence[pd.Timestamp]]] = None):
        self._fn = fn
        self._available = available

    def get_pricer(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> Pricer:
        return self._fn(ts, dict(request or {}))

    def available_timestamps(self, start: pd.Timestamp, end: pd.Timestamp) -> Sequence[pd.Timestamp]:
        if self._available is None:
            raise NotImplementedError("this provider does not list its timestamps")
        return self._available(start, end)
