"""CurveStore: a provider over curve-store partitions (`<root>/asset=<A>/date=<YYYY-MM-DD>/*.parquet`) emitting `CurveSnapshot`s. No pricing library.

Row schema (read from the files): `timestamp_utc` timestamp[us, UTC], `timestamp_local` (naive: never used), `trading_date` date32 (== the partition date),
`node_dates` list<date32>, `discount_factors` list<double>, `interpolation`, `source_variant`, `spline_knots` (null in node-table rows). A row is one
immutable curve snapshot stamped `timestamp_utc`; the snapshot also carries the swap calendar (`usd_fed`) and the overnight fixings visible at the stamp.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time as _time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

import numpy as np
import pandas as pd

from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.pricer import TimeMapping
from pricebt.snapshot import CurveSnapshot, MarketSnapshot

from .common import (
    FIXINGS_NAME, SWAP_CALENDAR, CachedProvider, FixingsHistory, FixturesMissing, LRU, PathLike, Selection, Selector, calendar_data, day_of, fixings_policy,
    load_fixings, mapping_config, one_time, resolve_dir, time_mapping, to_calendar, to_day,
)

log = logging.getLogger(__name__)
_EPOCH = dt.date(1970, 1, 1)
_DAY_COLUMNS = ("timestamp_utc", "trading_date", "node_dates", "discount_factors", "interpolation", "source_variant", "spline_knots")
#: the store's interpolation tag -> the snapshot tag (named by its maths); any other store tag cannot be represented and is an error
TAGS = MappingProxyType({"log_linear": "log_linear_df"})


@dataclass(frozen=True)
class AssetProfile:
    """How to read one store asset. freshness: a row is served iff `node_dates[0]` equals its `trading_date` (`trading_date`), the last
    business day on or before the stamp's local date in `freshness_tz` (`local_bd`, exchange-dated assets), or always (`none`)."""

    kind: str
    freshness: str = "trading_date"
    freshness_tz: str = "America/New_York"
    holiday_calendar: str = "nyc"

    def __post_init__(self) -> None:
        if self.kind not in ("eod", "minute"):
            raise ConfigError(f"profile kind must be eod or minute, got {self.kind!r}", code="PROFILE")
        if self.freshness not in ("trading_date", "local_bd", "none"):
            raise ConfigError(f"unknown freshness rule {self.freshness!r}", code="PROFILE")


PROFILES: Mapping[str, AssetProfile] = MappingProxyType({
    "USD-SOFR-1D-CITIVELOEXCEL": AssetProfile("eod"),
    "USD-SOFR-1D": AssetProfile("eod"),
    "USD-OIS": AssetProfile("eod"),
    "USD-SOFR-1D-CITIVELOEXCELMIN": AssetProfile("minute"),
    "USD-SOFR-1D-Q12STIRT": AssetProfile("minute", "local_bd", "America/Chicago"),
    "USD-SOFR-1D-Q16STIRT": AssetProfile("minute", "local_bd", "America/Chicago"),
})


def _profile(asset: str, profile: Union[None, AssetProfile, Mapping[str, Any]]) -> AssetProfile:
    if isinstance(profile, AssetProfile):
        return profile
    if isinstance(profile, Mapping):
        return AssetProfile(**dict(profile))
    if asset in PROFILES:
        return PROFILES[asset]
    raise ConfigError(f"no built-in profile for asset {asset!r}; pass profile={{kind: eod|minute, freshness: ...}}", code="PROFILE")


def snapshot_tag(row_tag: Any) -> str:
    """The row's interpolation tag (a missing one is the store default `log_linear`) as a snapshot tag; a tag with no snapshot equivalent is an error."""
    tag = str(row_tag or "log_linear")
    if tag not in TAGS:
        raise ConfigError(f"store interpolation {tag!r} has no snapshot tag; known: {sorted(TAGS)}", code="SNAPSHOT")
    return TAGS[tag]


class CurveStore(CachedProvider):
    """Timestamp -> `SnapshotPricer` (a `CurveSnapshot` + fixings + calendar) from one curve-store asset.

    Time mapping (`time:` block, core `TimeMapping`): `asof` (latest visible row, `ts - stamp <= max_staleness`; default 12h EOD / 5min minute), `exact`,
    `date`; `eod_visible_at: "HH:MM"` only delays EOD rows. Rows failing the freshness rule (holiday rows that carry the previous day's nodes) and duplicate
    stamps are dropped at index time and never served. The snapshot stamp is the row stamp and its reference date the first node date. Fixings (decimal
    parquet) enter the snapshot under the publication policy evaluated at the row stamp (see `FixingsHistory`). The index is built lazily over `window`
    (whole store by default); partition reads go through a bounded per-day LRU and built pricers through a bounded LRU keyed by snapshot stamp.
    """

    _cache_attrs = ("_days", "_pricers")

    def __init__(
        self, asset: str, *, root: Optional[PathLike] = None, time: Union[None, TimeMapping, Mapping[str, Any]] = None,
        window: Tuple[Any, Any] = (None, None), profile: Union[None, AssetProfile, Mapping[str, Any]] = None, fixings: Optional[PathLike] = "auto",
        fixings_unit: str = "decimal", fixings_policy_cfg: Optional[Mapping[str, Any]] = None, calendars_root: Optional[PathLike] = None,
        curve_id: str = "sofr", max_cached_days: int = 8, max_cached_pricers: int = 256, mapping: Optional[TimeMapping] = None,
    ):
        self.asset = str(asset)
        self.root = resolve_dir(root, "curves")
        self.asset_dir = self.root / f"asset={self.asset}"
        if not self.asset_dir.is_dir():
            raise FixturesMissing(f"no store partitions for asset {self.asset!r} under {self.root}")
        self.profile = _profile(self.asset, profile)
        self.kind = self.profile.kind
        self.time = time_mapping(one_time(time, mapping), kind=self.profile.kind)
        self.window = (to_day(window[0]), to_day(window[1]))
        self.calendars_root = Path(calendars_root) if calendars_root is not None else self.root.parent / "calendars"
        if fixings == "auto":
            cand = self.root.parent / "fixings" / "USD-SOFR-1D.parquet"
            fixings = cand if cand.is_file() else None
        self.fixings_path = None if fixings is None else Path(fixings)
        self.fixings_unit = fixings_unit
        self.fixings_policy_cfg = dict(fixings_policy_cfg or {})
        self.policy = fixings_policy(self.fixings_policy_cfg)
        self.swap_calendar = calendar_data("nyc", self.calendars_root, as_name=SWAP_CALENDAR)
        self.fixings = None
        if self.fixings_path is not None:
            self.fixings = FixingsHistory(load_fixings(self.fixings_path, unit=fixings_unit), to_calendar(self.swap_calendar), self.policy, self.time.tz)
        self.curve_id = curve_id
        self.max_cached_days = int(max_cached_days)
        self._init_caches(max_cached_pricers)
        self._idx: Optional[Dict[str, Any]] = None

    def _init_caches(self, max_cached_pricers: int) -> None:
        super()._init_caches(max_cached_pricers)
        self._days = LRU(self.max_cached_days)
        self.n_day_reads = 0

    # ------------------------------------------------------------------ index
    def partitions(self) -> List[dt.date]:
        """Partition dates of the asset inside `window` (ONE scandir of the asset directory; the store is never walked)."""
        out = []
        lo, hi = self.window
        with os.scandir(self.asset_dir) as it:
            for e in it:
                if e.is_dir() and e.name.startswith("date="):
                    d = dt.date.fromisoformat(e.name[5:])
                    if (lo is None or d >= lo) and (hi is None or d <= hi):
                        out.append(d)
        return sorted(out)

    def _files(self, day: dt.date) -> List[str]:
        p = self.asset_dir / f"date={day.isoformat()}"
        with os.scandir(p) as it:
            return sorted(str(p / e.name) for e in it if e.is_file() and e.name.endswith(".parquet"))

    @property
    def index(self) -> Dict[str, Any]:
        if self._idx is None:
            self._idx = self._build_index()
        return self._idx

    def _selector(self) -> Selector:
        return self.index["selector"]

    def _build_index(self) -> Dict[str, Any]:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.dataset as ds

        t0 = _time.perf_counter()
        parts = self.partitions()
        files = [f for d in parts for f in self._files(d)]
        if not files:
            raise MarketDataUnavailable(None, {}, f"no partitions for {self.asset} in window {self.window}")
        tab = ds.dataset(files, format="parquet").to_table(columns=["timestamp_utc", "trading_date", "node_dates"])
        stamp = tab["timestamp_utc"].cast(pa.timestamp("ns", tz="UTC")).cast(pa.int64()).to_numpy(zero_copy_only=False)
        tdays = tab["trading_date"].cast(pa.int32()).to_numpy(zero_copy_only=False).astype("int64")
        node0 = pc.list_element(tab["node_dates"], 0).cast(pa.int32()).to_numpy(zero_copy_only=False).astype("int64")
        order = np.lexsort((np.arange(len(stamp)), stamp))
        stamp, tdays, node0 = stamp[order], tdays[order], node0[order]
        fresh = self._fresh(stamp, tdays, node0)
        keep = fresh.copy()
        fi = np.flatnonzero(fresh)
        dup = np.zeros(len(fi), dtype=bool)
        dup[:-1] = stamp[fi][:-1] == stamp[fi][1:]  # of equal stamps the last row is kept
        keep[fi[dup]] = False
        dropped = pd.DataFrame({
            "stamp": pd.DatetimeIndex(stamp[~keep].view("datetime64[ns]")).tz_localize("UTC"),
            "trading_date": [day_of(x) for x in tdays[~keep]],
            "reason": np.where(~fresh[~keep], "stale_reference", "duplicate_stamp"),
        })
        sel = Selector(stamp[keep], self.time, eod=self.profile.kind == "eod")
        log.debug("%s: indexed %d rows (%d dropped) from %d files in %.2fs", self.asset, int(keep.sum()), int((~keep).sum()), len(files), _time.perf_counter() - t0)
        return {"selector": sel, "tdays": tdays[keep], "node0": node0[keep], "dropped": dropped, "n_files": len(files), "rows": len(stamp)}

    def _fresh(self, stamp: np.ndarray, tdays: np.ndarray, node0: np.ndarray) -> np.ndarray:
        rule = self.profile.freshness
        if rule == "none":
            return np.ones(len(stamp), dtype=bool)
        if rule == "trading_date":
            return node0 == tdays
        cal = to_calendar(calendar_data(self.profile.holiday_calendar, self.calendars_root))
        local = pd.DatetimeIndex(stamp.view("datetime64[ns]")).tz_localize("UTC").tz_convert(self.profile.freshness_tz)
        ld = np.array([(d - _EPOCH).days for d in local.date], dtype="int64")
        uniq = np.unique(ld)
        bd = {int(u): (cal.preceding(_EPOCH + dt.timedelta(days=int(u))) - _EPOCH).days for u in uniq}
        return node0 == np.array([bd[int(x)] for x in ld], dtype="int64")

    @property
    def dropped(self) -> pd.DataFrame:
        """Rows never served: stamp, trading_date, reason (`stale_reference` | `duplicate_stamp`)."""
        return self.index["dropped"].copy()

    # ------------------------------------------------------------------ selection
    def _check(self, request: Optional[Mapping[str, Any]]) -> None:
        super()._check(request if not request else {k: v for k, v in request.items() if k != "curve"})
        if request and request.get("curve") not in (None, self.asset, self.curve_id):
            raise ConfigError(f"request curve {request.get('curve')!r} is not served by {self.asset!r}", code="REQUEST")

    # ------------------------------------------------------------------ rows and snapshots
    def _day(self, day: dt.date) -> Dict[str, Any]:
        hit = self._days.get(day)
        if hit is not None:
            return hit
        import pyarrow as pa
        import pyarrow.parquet as pq

        files = self._files(day)
        if not files:
            raise MarketDataUnavailable(None, {}, f"partition {day} of {self.asset} vanished")
        tab = pa.concat_tables([pq.ParquetFile(f).read(columns=list(_DAY_COLUMNS)) for f in files])
        ns = tab["timestamp_utc"].cast(pa.timestamp("ns", tz="UTC")).cast(pa.int64()).to_numpy(zero_copy_only=False)
        order = np.lexsort((np.arange(len(ns)), ns))
        ns, tab = ns[order], tab.take(pa.array(order))
        last = np.ones(len(ns), dtype=bool)
        last[:-1] = ns[:-1] != ns[1:]
        out = {"ns": ns[last], "tab": tab.filter(pa.array(last))}
        self._days.put(day, out)
        self.n_day_reads += 1
        return out

    def row(self, sel: Selection) -> Dict[str, Any]:
        """The raw store row of a selection: node_dates, discount_factors, interpolation, spline_knots, source_variant, trading_date."""
        idx = self.index
        day = day_of(idx["tdays"][sel.pos])
        d = self._day(day)
        k = int(np.searchsorted(d["ns"], sel.stamp.value))
        if k >= len(d["ns"]) or d["ns"][k] != sel.stamp.value:
            raise MarketDataUnavailable(sel.stamp, {}, f"row {sel.stamp} not found in partition {day} of {self.asset}")
        t = d["tab"]
        return {c: t[c][k].as_py() for c in _DAY_COLUMNS if c != "timestamp_utc"}

    def _snapshot(self, sel: Selection) -> MarketSnapshot:
        row = self.row(sel)
        node0 = day_of(self.index["node0"][sel.pos])
        tz = self.time.tz
        stamp = sel.stamp.tz_convert(tz)
        sid = f"{self.asset}:{sel.stamp.isoformat()}"
        knots = row.get("spline_knots")
        if knots is not None and len(knots) > 0:
            raise ConfigError("spline-interpolated store rows are not supported; only node-table (DF) curves", code="SNAPSHOT")
        dates = list(row["node_dates"])
        dfs = np.asarray(row["discount_factors"], dtype=float)
        if len(dfs) < 2 or len(dates) != len(dfs) or not np.isfinite(dfs).all() or (dfs <= 0).any() or abs(dfs[0] - 1.0) > 1e-12:
            raise MarketDataUnavailable(stamp, {}, f"bad_df: store row {sid} has invalid discount factors")
        prov = {
            "source": f"curve_store:{self.asset}", "asset": self.asset, "snapshot_id": sid, "kind": "eod" if self.profile.kind == "eod" else "intraday",
            "stamp": stamp.isoformat(), "visible_at": sel.visible.tz_convert(tz).isoformat(), "trading_date": str(row["trading_date"]),
            "partition": str(row["trading_date"]), "source_variant": row.get("source_variant"), "interpolation": row.get("interpolation"), "time_mode": self.time.mode,
        }
        curve = CurveSnapshot(self.curve_id, node0, tuple(dates), tuple(float(v) for v in dfs), interpolation=snapshot_tag(row.get("interpolation")), provenance=prov)
        fixings = {} if self.fixings is None else {FIXINGS_NAME: self.fixings.at(stamp, node0)}
        return MarketSnapshot(stamp, node0, curves={self.curve_id: curve}, fixings=fixings, calendars={SWAP_CALENDAR: self.swap_calendar}, provenance=prov)

    # ------------------------------------------------------------------ provenance
    def describe(self, ts: Optional[pd.Timestamp] = None) -> Mapping[str, Any]:
        """Provider summary; with `ts`, the provenance of the snapshot served at `ts` (asset, stamp, visible_at, staleness_s)."""
        if ts is not None:
            sel = self.select(ts)
            tz = self.time.tz
            return {
                "source": f"curve_store:{self.asset}", "asset": self.asset, "requested": pd.Timestamp(ts).isoformat(),
                "snapshot_id": f"{self.asset}:{sel.stamp.isoformat()}", "stamp": sel.stamp.tz_convert(tz).isoformat(),
                "visible_at": sel.visible.tz_convert(tz).isoformat(), "staleness_s": sel.age.total_seconds(),
                "trading_date": str(day_of(self.index["tdays"][sel.pos])),
            }
        idx = self.index
        sel = idx["selector"]
        vis = sel.instants(pd.Timestamp.min.tz_localize("UTC"), pd.Timestamp.max.tz_localize("UTC")) if len(sel) else pd.DatetimeIndex([])
        return {
            "source": f"curve_store:{self.asset}", "asset": self.asset, "kind": self.profile.kind, "root": str(self.root), "time": mapping_config(self.time),
            "profile": asdict(self.profile), "window": [str(self.window[0]), str(self.window[1])], "files": idx["n_files"], "rows": idx["rows"],
            "served": len(sel), "dropped": idx["dropped"]["reason"].value_counts().to_dict(),
            "first_visible": vis[0].isoformat() if len(vis) else None, "last_visible": vis[-1].isoformat() if len(vis) else None,
            "fixings": None if self.fixings is None else {"path": str(self.fixings_path), "first": str(self.fixings.dates[0]), "last": str(self.fixings.dates[-1])},
            "fixings_policy": {k: str(v) for k, v in self.policy.items()},
        }

    def to_config(self) -> Dict[str, Any]:
        return {
            "type": self._type_path(), "asset": self.asset, "root": str(self.root), "time": mapping_config(self.time),
            "window": [None if w is None else w.isoformat() for w in self.window], "profile": asdict(self.profile),
            "fixings": None if self.fixings_path is None else str(self.fixings_path), "fixings_unit": self.fixings_unit,
            "fixings_policy_cfg": dict(self.fixings_policy_cfg), "curve_id": self.curve_id,
        }
