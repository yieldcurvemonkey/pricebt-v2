"""UstEod: end-of-day clean prices of US Treasury notes and bonds (2010-2026) -> one `QuoteSet` snapshot per business day. No pricing library.

Rows carry a date only; each day is stamped `stamp_time` (default 17:00) in `tz`, the instant it becomes visible (the quotes are struck around 15:30
local time; 17:00 never exposes them early). A day with fewer than `min_valid_rows` prices inside `price_band` (the 13 all-zero days) is removed from the
index: it is unavailable, never a zero-price snapshot. Single rows outside the band are left out of the panel, so `bond_quote(cusip)` raises for them.
Quotes carry `price_convention='market'` (accrued interest is 0 on a coupon date) and, when both sides are positive, the bid and offer.

`partial_day_min_missing` (opt-in, None = off): a served day on which at least this many CUSIPs that are validly quoted on BOTH the previous and the next
served day are absent is a PARTIAL day and is removed like the zero-price days (reason `partial_day(n)`). Measured 2018-2026: 2023-02-10 (16 missing),
2023-06-15 (26), 2023-08-01 (30), 2023-11-20 (48), 2023-12-04 (29).
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, Mapping, Optional, Tuple, Union

import numpy as np
import pandas as pd

from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.pricer import TimeMapping
from pricebt.snapshot import MarketSnapshot, Quote

from .common import DataQualityError, PathLike, Selection, Selector, day_of, mapping_config, one_time, time_mapping, to_time
from .ust_common import QuoteProvider, data_file

log = logging.getLogger(__name__)
_EPOCH = dt.date(1970, 1, 1)


class UstEod(QuoteProvider):
    kind = "eod"
    name = "ust_eod"

    def __init__(
        self, *, root: Optional[PathLike] = None, prices: PathLike = "fedinvest_2010_2026.parquet", reference: PathLike = "reference_fiscaldata.parquet",
        time: Union[None, TimeMapping, Mapping[str, Any]] = None, stamp_time: Any = "17:00", price_band: Tuple[float, float] = (20.0, 250.0),
        min_valid_rows: int = 100, calendar: str = "fed", calendars_root: Optional[PathLike] = None, window: Tuple[Any, Any] = (None, None),
        curve_mdp: Any = None, max_cached_pricers: int = 64, mapping: Optional[TimeMapping] = None, fixings: Optional[PathLike] = None,
        fixings_unit: str = "decimal", fixings_policy_cfg: Optional[Mapping[str, Any]] = None, partial_day_min_missing: Optional[int] = None,
    ):
        self.time = time_mapping(one_time(time, mapping), kind="eod")
        self._init_common(root, reference, calendar, calendars_root, curve_mdp, max_cached_pricers, fixings, fixings_unit, fixings_policy_cfg)
        self._finish_init()
        if partial_day_min_missing is not None and int(partial_day_min_missing) < 1:
            raise ConfigError("partial_day_min_missing must be >= 1 (or None to disable)", code="BOND")
        self.partial_day_min_missing = None if partial_day_min_missing is None else int(partial_day_min_missing)
        self.prices_path = data_file(self.root, prices)
        self.stamp_time = to_time(stamp_time)
        lo, hi = float(price_band[0]), float(price_band[1])
        if not 0.0 < lo < hi:
            raise ConfigError(f"price_band must satisfy 0 < lo < hi, got {price_band}", code="BOND")
        self.price_band = (lo, hi)
        self.min_valid_rows = int(min_valid_rows)
        self.window = tuple(None if w is None else pd.Timestamp(w).date() for w in window)
        self._load()

    def _load(self) -> None:
        df = pd.read_parquet(self.prices_path, columns=["date", "cusip", "coupon", "offer_price", "bid_price", "eod_price"])
        days = (pd.to_datetime(df["date"]).to_numpy().astype("datetime64[D]").astype("int64"))
        lo, hi = self.window
        m = np.ones(len(df), dtype=bool)
        if lo is not None:
            m &= days >= (lo - _EPOCH).days
        if hi is not None:
            m &= days <= (hi - _EPOCH).days
        df, days = df[m], days[m]
        order = np.lexsort((df["cusip"].to_numpy(), days))
        df, days = df.iloc[order].reset_index(drop=True), days[order]
        if df.duplicated(["date", "cusip"]).any():
            raise DataQualityError(f"{self.prices_path.name}: duplicated (date, cusip) rows")
        eod = df["eod_price"].to_numpy(dtype=float)
        valid = np.isfinite(eod) & (eod >= self.price_band[0]) & (eod <= self.price_band[1])
        uniq, start = np.unique(days, return_index=True)
        end = np.append(start[1:], len(days))
        nvalid = np.add.reduceat(valid.astype("int64"), start) if len(start) else np.zeros(0, "int64")
        served = nvalid >= self.min_valid_rows
        reasons = {int(k): ("zero_eod_day" if n == 0 else f"thin_day({n})") for k, n in zip(np.flatnonzero(~served), nvalid[~served])}
        if self.partial_day_min_missing is not None:
            cus = df["cusip"].to_numpy(dtype=object)
            srv = np.flatnonzero(served)
            sets = {int(k): set(cus[start[k]:end[k]][valid[start[k]:end[k]]]) for k in srv}
            for a, b, c in zip(srv[:-2], srv[1:-1], srv[2:]):
                n_missing = len((sets[int(a)] & sets[int(c)]) - sets[int(b)])
                if n_missing >= self.partial_day_min_missing:
                    served[b] = False
                    reasons[int(b)] = f"partial_day({n_missing} missing)"
        gone = np.flatnonzero(~served)
        self.dropped_days = pd.DataFrame({"date": [day_of(uniq[k]) for k in gone], "valid_rows": nvalid[gone], "reason": [reasons[int(k)] for k in gone]})
        self.n_rows_out_of_band = int((~valid).sum())
        self._days, self._start, self._end = uniq[served], start[served], end[served]
        self._cusip = df["cusip"].to_numpy(dtype=object)
        self._eod, self._valid = eod, valid
        self._bid, self._offer = df["bid_price"].to_numpy(dtype=float), df["offer_price"].to_numpy(dtype=float)
        local = pd.to_datetime(self._days, unit="D") + pd.Timedelta(hours=self.stamp_time.hour, minutes=self.stamp_time.minute, seconds=self.stamp_time.second)
        stamps = pd.DatetimeIndex(local).tz_localize(self.time.tz).tz_convert("UTC").as_unit("ns").asi8
        self._sel = Selector(stamps, self.time, eod=True)
        log.debug("ust_eod: %d served days, %d dropped, %d rows out of band", len(self._days), len(self.dropped_days), self.n_rows_out_of_band)

    def panel(self, day: dt.date) -> pd.DataFrame:
        """The day's rows (cusip, eod_price, bid_price, offer_price, valid); a dropped or absent day raises."""
        k = int(np.searchsorted(self._days, (day - _EPOCH).days))
        if k >= len(self._days) or self._days[k] != (day - _EPOCH).days:
            raise MarketDataUnavailable(day, {}, f"no panel for {day}")
        s = slice(self._start[k], self._end[k])
        return pd.DataFrame({"cusip": self._cusip[s], "eod_price": self._eod[s], "bid_price": self._bid[s], "offer_price": self._offer[s], "valid": self._valid[s]})

    def _snapshot(self, sel: Selection) -> MarketSnapshot:
        s = slice(self._start[sel.pos], self._end[sel.pos])
        day = day_of(self._days[sel.pos])
        cus, px, ok, bid, off = self._cusip[s], self._eod[s], self._valid[s], self._bid[s], self._offer[s]
        quotes: Dict[str, Quote] = {}
        for c, p, v, b, o in zip(cus, px, ok, bid, off):
            if v and self.reference.get(c) is not None:  # a quote needs reference data (4 CUSIPs of the price file have none)
                two = b > 0 and o > 0
                quotes[c] = Quote(clean=float(p), bid=float(b) if two else None, offer=float(o) if two else None)
        tz = self.time.tz
        stamp = sel.stamp.tz_convert(tz)
        prov = {
            "source": self.name, "snapshot_id": f"{self.name}:{day.isoformat()}", "kind": "eod", "stamp": stamp.isoformat(),
            "visible_at": sel.visible.tz_convert(tz).isoformat(), "trading_date": day.isoformat(), "n_rows": int(len(cus)), "n_quotes": len(quotes),
            "price_band": list(self.price_band),
        }
        return self._quote_snapshot(stamp, day, quotes, prov)

    def describe(self, ts: Optional[pd.Timestamp] = None) -> Mapping[str, Any]:
        d = dict(self._describe(ts))
        if ts is None:
            d.update({"prices": str(self.prices_path), "price_band": list(self.price_band), "min_valid_rows": self.min_valid_rows,
                      "partial_day_min_missing": self.partial_day_min_missing,
                      "dropped_days": {str(r.date): r.reason for r in self.dropped_days.itertuples()}, "rows_out_of_band": self.n_rows_out_of_band})
        return d

    def to_config(self) -> Dict[str, Any]:
        if self.curve_mdp is not None:
            raise ConfigError("curve_mdp is a Python object and has no config form", code="CFG")
        return {
            "type": self._type_path(), "root": str(self.root), "prices": str(self.prices_path), "reference": str(self.reference_path),
            "time": mapping_config(self.time), "stamp_time": self.stamp_time.strftime("%H:%M"), "price_band": list(self.price_band),
            "min_valid_rows": self.min_valid_rows, "calendar": self.calendar, "window": [None if w is None else w.isoformat() for w in self.window],
            **self._fixings_config(), **({} if self.partial_day_min_missing is None else {"partial_day_min_missing": self.partial_day_min_missing}),
        }
