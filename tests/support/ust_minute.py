"""UstMinute: minute yields (percent) of US Treasuries -> one `QuoteSet` snapshot per minute (yield quotes, `price_convention='market'`). No pricing library.
Default time mapping `exact` (the source is a minute grid).

The source forward-fills its 07:00-17:00 local minute grid (weekends and holidays included). Every quote carries flags `repeat` (same yield as the
previous minute) and `run` (consecutive repeats up to this minute over the UNFILTERED grid, a lower bound of the quote's age); they travel in
`QuoteSet.provenance['flags']` (metadata, not part of the digest). `drop_flat_days` removes dates on which every present CUSIP is constant all session.

`outlier_bp` (opt-in, None = off) is a cross-sectional bad-tick filter: a quote whose move since the CUSIP's last accepted quote differs from the median
move of all CUSIPs over the same minute by more than `outlier_bp` basis points is NOT served; the last accepted yield is served instead with flag
`filtered=True` (and `repeat=True`). A deviation lasting `outlier_max_run` consecutive minutes is accepted as a genuine level change. Measured
motivation: 91282CPZ8 (the 10-year on-the-run) prints 3.333/3.331/4.134 at 08:43-08:45 on 2026-03-12 between 4.225 and 4.218 while no other CUSIP moves (a
90bp two-minute spike). `filtered_counts` records what was removed.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

import numpy as np
import pandas as pd

from pricebt.errors import ConfigError
from pricebt.pricer import TimeMapping
from pricebt.snapshot import MarketSnapshot, Quote

from .common import DataQualityError, PathLike, Selection, Selector, mapping_config, one_time, time_mapping, to_time
from .ust_common import QuoteProvider, data_file

log = logging.getLogger(__name__)


class UstMinute(QuoteProvider):
    kind = "minute"
    name = "ust_minute"

    def __init__(
        self, *, root: Optional[PathLike] = None, quotes: PathLike = "webull_minute.parquet", reference: PathLike = "reference_fiscaldata.parquet",
        time: Union[None, TimeMapping, Mapping[str, Any]] = None, session: Tuple[Any, Any] = ("07:00", "17:00"), drop_flat_days: bool = True,
        ytm_band: Tuple[float, float] = (-2.0, 25.0), calendar: str = "fed", calendars_root: Optional[PathLike] = None, curve_mdp: Any = None,
        max_cached_pricers: int = 512, mapping: Optional[TimeMapping] = None, fixings: Optional[PathLike] = None, fixings_unit: str = "decimal",
        fixings_policy_cfg: Optional[Mapping[str, Any]] = None, outlier_bp: Optional[float] = None, outlier_max_run: int = 5,
    ):
        self.time = time_mapping(one_time(time, mapping), kind="minute", mode="exact")
        self._init_common(root, reference, calendar, calendars_root, curve_mdp, max_cached_pricers, fixings, fixings_unit, fixings_policy_cfg)
        self._finish_init()
        self.quotes_path = data_file(self.root, quotes)
        self.session = (to_time(session[0]), to_time(session[1]))
        self.drop_flat_days = bool(drop_flat_days)
        self.ytm_band = (float(ytm_band[0]), float(ytm_band[1]))
        if outlier_bp is not None and not float(outlier_bp) > 0:
            raise ConfigError(f"outlier_bp must be > 0 (or None to disable), got {outlier_bp}", code="BOND")
        if int(outlier_max_run) < 1:
            raise ConfigError("outlier_max_run must be >= 1", code="BOND")
        self.outlier_bp = None if outlier_bp is None else float(outlier_bp)
        self.outlier_max_run = int(outlier_max_run)
        self._load()

    def _load(self) -> None:
        df = pd.read_parquet(self.quotes_path, columns=["ts_utc", "cusip", "ytm_pct"])
        ns = pd.DatetimeIndex(df["ts_utc"]).tz_convert("UTC").as_unit("ns").asi8
        minutes = np.unique(ns)
        cusips = np.array(sorted(df["cusip"].unique()), dtype=object)
        mi = np.searchsorted(minutes, ns)
        ci = np.searchsorted(cusips, df["cusip"].to_numpy(dtype=object))
        if pd.DataFrame({"m": mi, "c": ci}).duplicated().any():
            raise DataQualityError(f"{self.quotes_path.name}: duplicated (minute, cusip) rows")
        y = np.full((len(minutes), len(cusips)), np.nan)
        y[mi, ci] = df["ytm_pct"].to_numpy(dtype=float)
        y, filt = self._filter_outliers(y)
        self._filt = filt
        self.filtered_counts = {str(c): int(n) for c, n in zip(cusips, filt.sum(axis=0)) if n}
        rep = np.zeros_like(y, dtype=bool)
        rep[1:] = y[1:] == y[:-1]
        run = np.zeros(y.shape, dtype="int64")
        for k in range(1, len(minutes)):
            run[k] = np.where(rep[k], run[k - 1] + 1, 0)
        local = pd.DatetimeIndex(minutes.view("datetime64[ns]")).tz_localize("UTC").tz_convert(self.time.tz)
        tod = np.array([t.time() for t in local], dtype=object)
        keep = (tod >= self.session[0]) & (tod <= self.session[1])
        dates = np.array(local.date, dtype=object)
        flat_days: List[dt.date] = []
        if self.drop_flat_days:
            for d in sorted(set(dates[keep])):
                block = y[keep & (dates == d)]
                present = np.isfinite(block).any(axis=0)
                if present.any() and all(np.nanmax(block[:, j]) == np.nanmin(block[:, j]) for j in np.flatnonzero(present)):
                    flat_days.append(d)
            if flat_days:
                keep &= ~np.isin(dates, np.array(flat_days, dtype=object))
        self.flat_days = tuple(flat_days)
        self._rows = np.flatnonzero(keep)
        self._minutes, self._cusips, self._y, self._rep, self._run, self._dates = minutes, cusips, y, rep, run, dates
        self._sel = Selector(minutes[self._rows], self.time, eod=False)
        log.debug("ust_minute: %d minutes served of %d; flat days %s", len(self._rows), len(minutes), [str(d) for d in flat_days])

    def _filter_outliers(self, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """(served yields, filtered mask) under the cross-sectional bad-tick rule of the module docstring; identity when outlier_bp is None."""
        filt = np.zeros(y.shape, dtype=bool)
        if self.outlier_bp is None or y.shape[0] < 2:
            return y, filt
        thr = self.outlier_bp / 100.0
        out = y.copy()
        last = y[0].copy()
        streak = np.zeros(y.shape[1], dtype="int64")
        for k in range(1, y.shape[0]):
            row = y[k]
            move = row - last
            ok = np.isfinite(move)
            if ok.sum() < 3:  # too few CUSIPs to form a cross-section: accept as is
                seen = np.isfinite(row)
                last[seen] = row[seen]
                streak[:] = 0
                continue
            excess = move - float(np.median(move[ok]))
            bad = ok & (np.abs(excess) > thr) & (streak + 1 < self.outlier_max_run)
            accept = np.isfinite(row) & ~bad
            out[k, bad] = last[bad]
            filt[k] = bad
            streak = np.where(bad, streak + 1, 0)
            last[accept] = row[accept]
        return out, filt

    def quote_flags(self, ts: pd.Timestamp) -> pd.DataFrame:
        """Per CUSIP at the minute selected for `ts`: ytm_pct, repeat (bool), run (int minutes of unchanged yield), filtered (bad tick replaced)."""
        r = self._rows[self.select(ts).pos]
        return pd.DataFrame({"ytm_pct": self._y[r], "repeat": self._rep[r], "run": self._run[r], "filtered": self._filt[r]}, index=pd.Index(self._cusips, name="cusip"))

    def _snapshot(self, sel: Selection) -> MarketSnapshot:
        r = self._rows[sel.pos]
        lo, hi = self.ytm_band
        quotes: Dict[str, Quote] = {}
        flags: Dict[str, Dict[str, Any]] = {}
        for j, c in enumerate(self._cusips):
            v = self._y[r, j]
            if np.isfinite(v) and lo <= v <= hi and self.reference.get(c) is not None:
                quotes[c] = Quote(ytm=float(v))
                flags[c] = {"repeat": bool(self._rep[r, j]), "run": int(self._run[r, j])}
                if self.outlier_bp is not None:
                    flags[c]["filtered"] = bool(self._filt[r, j])
        tz = self.time.tz
        stamp = sel.stamp.tz_convert(tz)
        day = stamp.date()
        prov = {
            "source": self.name, "snapshot_id": f"{self.name}:{sel.stamp.isoformat()}", "kind": "intraday", "stamp": stamp.isoformat(),
            "visible_at": sel.visible.tz_convert(tz).isoformat(), "trading_date": day.isoformat(), "n_quotes": len(quotes),
            "n_repeat": int(sum(f["repeat"] for f in flags.values())),
        }
        if self.outlier_bp is not None:
            prov["n_filtered"] = int(sum(f["filtered"] for f in flags.values()))
        return self._quote_snapshot(stamp, day, quotes, prov, flags)

    def describe(self, ts: Optional[pd.Timestamp] = None) -> Mapping[str, Any]:
        d = dict(self._describe(ts))
        if ts is None:
            d.update({"quotes": str(self.quotes_path), "cusips": len(self._cusips), "minutes_total": len(self._minutes),
                      "flat_days": [str(x) for x in self.flat_days], "session": [t.strftime("%H:%M") for t in self.session],
                      "outlier_bp": self.outlier_bp, "filtered_counts": dict(self.filtered_counts)})
        return d

    def to_config(self) -> Dict[str, Any]:
        if self.curve_mdp is not None:
            raise ConfigError("curve_mdp is a Python object and has no config form", code="CFG")
        out = {
            "type": self._type_path(), "root": str(self.root), "quotes": str(self.quotes_path), "reference": str(self.reference_path), "time": mapping_config(self.time),
            "session": [t.strftime("%H:%M") for t in self.session], "drop_flat_days": self.drop_flat_days, "ytm_band": list(self.ytm_band),
            "calendar": self.calendar, **self._fixings_config(),
        }
        if self.outlier_bp is not None:
            out.update({"outlier_bp": self.outlier_bp, "outlier_max_run": self.outlier_max_run})
        return out
