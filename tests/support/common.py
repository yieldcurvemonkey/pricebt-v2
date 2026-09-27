"""Shared plumbing of the test-support providers: fixture paths, calendars and fixings as DATA, the fixings publication policy, the
time-mapping selector over the core `SnapshotIndex`, small caches. Imports only stdlib, numpy, pandas and pricebt core (pyarrow lazily)."""
from __future__ import annotations

import bisect
import dataclasses
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

import numpy as np
import pandas as pd

from pricebt.errors import ConfigError, MarketDataUnavailable, PricebtError, StaleSnapshot
from pricebt.pricer import SnapshotIndex, TimeMapping
from pricebt.snapshot import CalendarData, FixingsSeries, SnapshotPricer
from pricebt.timeutil import Calendar

PathLike = Union[str, Path]
DEFAULT_TZ = "America/New_York"
DEFAULT_STALENESS: Mapping[str, pd.Timedelta] = MappingProxyType({"eod": pd.Timedelta(hours=12), "minute": pd.Timedelta(minutes=5)})
_TIME_KEYS = frozenset({"mode", "max_staleness", "eod_visible_at", "unbounded_ok", "tz"})
_EPOCH = dt.date(1970, 1, 1)
#: snapshot names of the data every provider emits (provider-defined data: adapters and configs refer to these strings)
SWAP_CALENDAR = "usd_fed"
BOND_CALENDAR = "us_govt"
FIXINGS_NAME = "USD-SOFR-1D"


class DataQualityError(PricebtError):
    """A data file violates its contract (unsorted, duplicated, wrong unit, unreadable)."""


class FixturesMissing(PricebtError, FileNotFoundError):
    """The exported fixtures directory (or one of its files) is absent."""


# ------------------------------------------------------------------------------ paths
def default_fixtures_root() -> Path:
    """$PRICEBT_DATA, else $PRICEBT_FIXTURES, else <project>/data/fixtures. Raises FixturesMissing when absent."""
    env = os.environ.get("PRICEBT_DATA") or os.environ.get("PRICEBT_FIXTURES")
    root = Path(env) if env else Path(__file__).resolve().parents[2] / "data" / "fixtures"
    if not root.is_dir():
        raise FixturesMissing(f"fixtures directory not found: {root}")
    return root


def resolve_dir(root: Optional[PathLike], sub: str) -> Path:
    """`root` if given, else <fixtures>/<sub>; must be a directory."""
    p = Path(root) if root is not None else default_fixtures_root() / sub
    if not p.is_dir():
        raise FixturesMissing(f"directory not found: {p}")
    return p


def to_day(x: Any) -> Optional[dt.date]:
    return None if x is None else pd.Timestamp(x).date()


def day_of(n: int) -> dt.date:
    return _EPOCH + dt.timedelta(days=int(n))


# ------------------------------------------------------------------------------ calendars as data
def _weekmask_text(raw: Any) -> str:
    return raw if isinstance(raw, str) else "".join(str(int(x)) for x in raw)


def calendar_data(name_or_path: PathLike, calendars_root: Optional[PathLike] = None, *, as_name: Optional[str] = None) -> CalendarData:
    """A fixtures calendar json (`nyc`, `fed`, `us_govt_bond`, or a path) as snapshot `CalendarData` named `as_name` (default: the file's own name)."""
    p = Path(name_or_path)
    if p.suffix.lower() != ".json":
        p = resolve_dir(calendars_root, "calendars") / f"{name_or_path}.json"
    if not p.is_file():
        raise FixturesMissing(f"calendar file not found: {p}")
    obj = json.loads(p.read_text(encoding="utf8"))
    hol = tuple(pd.Timestamp(h).date() for h in obj.get("holidays", ()))
    prov = {k: obj[k] for k in ("source", "first", "last") if k in obj}
    return CalendarData(as_name or obj.get("name") or p.stem, hol, _weekmask_text(obj.get("weekmask", "Mon Tue Wed Thu Fri")), prov)


def to_calendar(data: CalendarData) -> Calendar:
    """The core business-day predicate of calendar data."""
    return Calendar(data.holidays, data.weekmask, data.name)


def load_calendar(name_or_path: PathLike, calendars_root: Optional[PathLike] = None) -> Calendar:
    return to_calendar(calendar_data(name_or_path, calendars_root))


# ------------------------------------------------------------------------------ fixings
def load_fixings(path: PathLike, *, unit: str = "decimal") -> pd.Series:
    """Fixings parquet (`date`, `rate`) -> PERCENT series on a naive midnight DatetimeIndex.

    File contract (DataQualityError otherwise): dates ascending and unique; values inside (0, 0.2) for `decimal`, (0, 20) for `percent`.
    """
    if unit not in ("decimal", "percent"):
        raise ConfigError(f"fixings unit must be 'decimal' or 'percent', got {unit!r}", code="FIXINGS")
    p = Path(path)
    if not p.is_file():
        raise FixturesMissing(f"fixings file not found: {p}")
    df = pd.read_parquet(p, columns=["date", "rate"])
    idx = pd.DatetimeIndex(pd.to_datetime(df["date"]))
    vals = df["rate"].to_numpy(dtype=float)
    problems: List[str] = []
    if not idx.is_monotonic_increasing:
        problems.append("dates are not ascending")
    if idx.has_duplicates:
        problems.append(f"{int(idx.duplicated().sum())} duplicated dates")
    hi = 0.2 if unit == "decimal" else 20.0
    bad = ~np.isfinite(vals) | (vals <= 0.0) | (vals >= hi)
    if bad.any():
        problems.append(f"{int(bad.sum())} values outside (0, {hi}) for unit {unit!r} (first {idx[bad][0].date()}: {vals[bad][0]!r})")
    if problems:
        raise DataQualityError(f"{p.name}: " + "; ".join(problems))
    return pd.Series(vals * (100.0 if unit == "decimal" else 1.0), index=idx.normalize(), name="SOFR")


def to_time(x: Any) -> dt.time:
    if isinstance(x, dt.time):
        return x
    if isinstance(x, str):
        return dt.time.fromisoformat(x)
    raise ConfigError(f"cannot read {x!r} as a time of day (HH:MM)", code="TIME")


def fixings_policy(cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Spec 12.7 policy `{visible_after: {business_days, time}, pre_publish}` -> `{after_bdays, at, pre_publish}`."""
    out: Dict[str, Any] = {"after_bdays": 1, "at": dt.time(8, 0), "pre_publish": "proxy_last"}
    if not cfg:
        return out
    unknown = set(cfg) - {"visible_after", "pre_publish", "after_bdays", "at"}
    if unknown:
        raise ConfigError(f"unknown fixings_policy keys {sorted(unknown)}", code="FIXINGS")
    va = dict(cfg.get("visible_after") or {})
    if set(va) - {"business_days", "time"}:
        raise ConfigError(f"unknown visible_after keys {sorted(set(va) - {'business_days', 'time'})}", code="FIXINGS")
    out["after_bdays"] = int(va.get("business_days", cfg.get("after_bdays", 1)))
    out["at"] = to_time(va.get("time", cfg.get("at", dt.time(8, 0))))
    out["pre_publish"] = str(cfg.get("pre_publish", "proxy_last"))
    if out["pre_publish"] not in ("proxy_last", "strict"):
        raise ConfigError(f"pre_publish must be proxy_last or strict, got {out['pre_publish']!r}", code="FIXINGS")
    return out


class FixingsHistory:
    """The full published overnight-fixings history (PERCENT) and the publication rule; `at(ts, reference_date)` is the series visible in a snapshot.

    Rule: only fixings dated before the reference date; a fixing dated d is public from `after_bdays` business days later at `at` local time.
    If the newest required business day (the one before the reference date) is not yet public at `ts`, its fixing is dropped and, under
    `proxy_last` (the default), REPLACED by the previous public level and listed in `proxied` (so the series stays contiguous); under `strict`
    a ValueError is raised. The snapshot therefore carries only what was visible, and no consumer applies the policy a second time.
    """

    def __init__(self, series: pd.Series, calendar: Calendar, policy: Mapping[str, Any], tz: str, name: str = FIXINGS_NAME):
        idx = pd.DatetimeIndex(series.index)
        self.name, self.calendar, self.policy, self.tz = name, calendar, dict(policy), tz
        self.dates: Tuple[dt.date, ...] = tuple(idx.date)
        self.values: Tuple[float, ...] = tuple(float(v) for v in series.to_numpy(dtype=float))

    def at(self, ts: pd.Timestamp, reference_date: dt.date) -> FixingsSeries:
        k = bisect.bisect_left(self.dates, reference_date)  # dates strictly before the reference date
        if k == 0:
            return FixingsSeries(self.name, (), (), "percent")
        ds, vs, proxied = self.dates[:k], self.values[:k], ()
        last_needed = self.calendar.add_business_days(reference_date, -1)
        publish = pd.Timestamp(dt.datetime.combine(self.calendar.add_business_days(last_needed, self.policy["after_bdays"]), self.policy["at"])).tz_localize(self.tz)
        if publish > pd.Timestamp(ts):
            if self.policy["pre_publish"] == "strict":
                raise ValueError(f"fixing for {last_needed} is not published at {ts} (policy strict)")
            j = bisect.bisect_left(ds, last_needed)
            ds, vs = ds[:j], vs[:j]
            if j:
                ds, vs, proxied = ds + (last_needed,), vs + (vs[-1],), (last_needed,)
        return FixingsSeries(self.name, ds, vs, "percent", proxied)


# ------------------------------------------------------------------------------ time
def time_mapping(cfg: Union[None, TimeMapping, Mapping[str, Any]], *, kind: str, mode: str = "asof") -> TimeMapping:
    """The provider `time:` block as the core TimeMapping. Defaults: max_staleness 12h (eod) / 5min (minute); `eod_visible_at` only DELAYS."""
    if isinstance(cfg, TimeMapping):
        return cfg
    cfg = dict(cfg or {})
    unknown = set(cfg) - _TIME_KEYS
    if unknown:
        raise ConfigError(f"unknown time keys {sorted(unknown)}; allowed {sorted(_TIME_KEYS)}", code="TIME")
    ms = cfg.get("max_staleness", DEFAULT_STALENESS[kind])
    eva = cfg.get("eod_visible_at")
    return TimeMapping(
        mode=str(cfg.get("mode", mode)), max_staleness=None if ms is None else pd.Timedelta(ms),
        eod_visible_at=None if eva in (None, "from_row") else to_time(eva), unbounded_ok=bool(cfg.get("unbounded_ok", False)), tz=str(cfg.get("tz", DEFAULT_TZ)),
    )


def one_time(time: Any, mapping: Optional[TimeMapping]) -> Any:
    """The provider's `time` argument, or `mapping` (the TimeMapping the config loader builds from a `time:` block); not both."""
    if mapping is not None and time is not None:
        raise ConfigError("give `time` or `mapping`, not both", code="TIME")
    return mapping if mapping is not None else time


def mapping_config(m: TimeMapping) -> Dict[str, Any]:
    return {
        "mode": m.mode, "max_staleness": None if m.max_staleness is None else str(m.max_staleness),
        "eod_visible_at": None if m.eod_visible_at is None else m.eod_visible_at.strftime("%H:%M"), "unbounded_ok": m.unbounded_ok, "tz": m.tz,
    }


def utc_ns(stamps: Any) -> np.ndarray:
    """Any datetime-like column -> int64 UTC NANOseconds (never `.asi8` of a microsecond index: the unit trap)."""
    idx = pd.DatetimeIndex(stamps)
    if idx.tz is None:
        raise ConfigError("snapshot stamps must be tz-aware", code="TIME")
    return idx.tz_convert("UTC").as_unit("ns").asi8.copy()


def to_utc(ts: Any, tz: str) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return (t.tz_localize(tz) if t.tzinfo is None else t).tz_convert("UTC")


@dataclass(frozen=True)
class Selection:
    """The row chosen for a requested timestamp: position in the selector arrays, its stamp, visibility instant and age (ts - stamp)."""

    pos: int
    stamp: pd.Timestamp
    visible: pd.Timestamp
    age: pd.Timedelta


class Selector:
    """The core `SnapshotIndex` search over VISIBLE instants, with staleness measured from the STAMP.

    visible = stamp, or for end-of-day data `max(stamp, local date of the stamp at eod_visible_at)` (a delay, never an advance). The core index is
    built over the visible instants and called with a mapping stripped of `eod_visible_at` / `max_staleness`, both applied here. What the core
    index does not give a provider, so it lives here: the visible instants themselves (`visible_ns`, `instants`), the age of a selection, a reason for
    every miss (`MarketDataUnavailable`) and the strictly-increasing check.
    """

    def __init__(self, stamp_ns: np.ndarray, mapping: TimeMapping, *, eod: bool):
        s = np.asarray(stamp_ns, dtype="int64")
        if len(s) and not (np.diff(s) > 0).all():
            raise DataQualityError("snapshot stamps must be strictly increasing")
        self.mapping = mapping
        self.stamp_ns = s
        vis = s.copy()
        if eod and mapping.eod_visible_at is not None and len(s):
            wall = pd.DatetimeIndex(s.view("datetime64[ns]")).tz_localize("UTC").tz_convert(mapping.tz).tz_localize(None).normalize()
            e = mapping.eod_visible_at
            floor = (wall + pd.Timedelta(hours=e.hour, minutes=e.minute, seconds=e.second)).tz_localize(mapping.tz).tz_convert("UTC").as_unit("ns").asi8
            vis = np.maximum(s, floor)
        self.visible_ns = vis
        self._index = SnapshotIndex(pd.DatetimeIndex(vis.view("datetime64[ns]")).tz_localize("UTC"))
        self._core = dataclasses.replace(mapping, eod_visible_at=None, max_staleness=None, unbounded_ok=True)

    def __len__(self) -> int:
        return len(self.stamp_ns)

    def select(self, ts: Any, request: Optional[Mapping[str, Any]] = None) -> Selection:
        m = self.mapping
        t = to_utc(ts, m.tz)
        i = self._index.select(t, self._core) if len(self) else None
        if i is None:
            why = {"asof": "before_first_snapshot", "exact": "no_snapshot_at_exact_timestamp", "date": f"no_snapshot_for_date {t.tz_convert(m.tz).date()}"}[m.mode]
            raise MarketDataUnavailable(ts, request, why)
        stamp = pd.Timestamp(int(self.stamp_ns[i]), tz="UTC")
        age = t - stamp
        if m.mode == "asof" and m.max_staleness is not None and age > m.max_staleness:
            raise StaleSnapshot(ts, request, f"stale: newest visible snapshot {stamp.tz_convert(m.tz)} is {age} old (max_staleness {m.max_staleness})")
        return Selection(int(i), stamp, pd.Timestamp(int(self.visible_ns[i]), tz="UTC"), age)

    def instants(self, start: Any, end: Any) -> pd.DatetimeIndex:
        """Instants in [start, end] at which a new snapshot becomes selectable, tz-aware in the mapping tz."""
        a, b = to_utc(start, self.mapping.tz).value, to_utc(end, self.mapping.tz).value
        v = self.visible_ns
        sel = np.unique(v[(v >= a) & (v <= b)])
        return pd.DatetimeIndex(sel.view("datetime64[ns]")).tz_localize("UTC").tz_convert(self.mapping.tz)


def check_request(request: Optional[Mapping[str, Any]], allowed: Iterable[str] = ()) -> None:
    """The providers take no request keys beyond `allowed`; anything else is a configuration error, never silently ignored."""
    extra = set(request or {}) - set(allowed)
    if extra:
        raise ConfigError(f"unsupported request keys {sorted(extra)}; this provider accepts {sorted(allowed)}", code="REQUEST")


class LRU:
    """A small bounded least-recently-used dict owned by one provider instance."""

    def __init__(self, maxsize: int):
        if maxsize < 1:
            raise ConfigError("cache size must be >= 1", code="CACHE")
        self.maxsize = int(maxsize)
        self._d: Dict[Any, Any] = {}

    def get(self, key: Any) -> Any:
        v = self._d.pop(key, None)
        if v is not None:
            self._d[key] = v
        return v

    def put(self, key: Any, value: Any) -> None:
        self._d.pop(key, None)
        self._d[key] = value
        while len(self._d) > self.maxsize:
            self._d.pop(next(iter(self._d)))

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, key: Any) -> bool:
        return key in self._d

    def clear(self) -> None:
        self._d.clear()


class CachedProvider:
    """Shared plumbing of the providers: request check, the selector, the per-stamp snapshot-pricer LRU, pickling (caches are dropped and rebuilt)."""

    kind = "eod"
    name = ""
    _cache_attrs: Tuple[str, ...] = ("_pricers",)
    _sel: Selector
    time: TimeMapping

    def _init_caches(self, max_cached_pricers: int) -> None:
        self.max_cached_pricers = int(max_cached_pricers)
        self._pricers = LRU(self.max_cached_pricers)

    def __getstate__(self) -> Dict[str, Any]:
        st = dict(self.__dict__)
        for k in self._cache_attrs:
            st.pop(k, None)
        return st

    def __setstate__(self, st: Dict[str, Any]) -> None:
        self.__dict__.update(st)
        self._init_caches(self.max_cached_pricers)

    def _selector(self) -> Selector:
        return self._sel

    def _check(self, request: Optional[Mapping[str, Any]]) -> None:
        check_request(request)

    def select(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> Selection:
        """The snapshot row `get_pricer(ts)` would serve (raises MarketDataUnavailable / StaleSnapshot exactly like it)."""
        self._check(request)
        return self._selector().select(ts, request)

    def available(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> bool:
        try:
            self.select(ts, request)
            return True
        except MarketDataUnavailable:
            return False

    def available_timestamps(self, start: pd.Timestamp, end: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> pd.DatetimeIndex:
        """Instants in [start, end] at which a new snapshot becomes selectable (tz = the mapping tz): the grid the data supports."""
        self._check(request)
        return self._selector().instants(start, end)

    def get_pricer(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> Any:
        sel = self.select(ts, request)
        hit = self._pricers.get(sel.stamp.value)
        if hit is not None:
            return hit
        p = SnapshotPricer(self._snapshot(sel))
        self._pricers.put(sel.stamp.value, p)
        return p

    def _snapshot(self, sel: Selection) -> Any:
        raise NotImplementedError

    def to_config(self) -> Dict[str, Any]:
        raise NotImplementedError

    def _type_path(self) -> str:
        return f"{type(self).__module__}:{type(self).__name__}"
