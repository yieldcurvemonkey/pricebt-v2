"""ArbsErisEodProvider: an EOD curve provider over a known, finite list of NY business dates, backed by ARBS's own `IRSwapsMDP` (source
`"ERIS_EOD_LIVE-RL_BASIC"`, curve `"USD-SOFR-1D"`). Modelled on `tests/support/curves.py:CurveStore` and reusing `tests/support/common.py`'s
`Selector`/`Selection`/`CachedProvider`/`LRU` directly (they need no ARBS at all).

Read-only: every call is `get_pricer(request={"curve_name": ..., "timestamp": <date>})`, an EOD curve read, nothing else -- no write to ARBS's caches
or curve store beyond whatever ARBS itself performs on a genuine cache miss (its ordinary behaviour, the same a human gets from the notebook), and no
Excel/COM path is ever reached by an EOD request. A FRESH request dict is built per call because ARBS pops the keys it reads (`tools/arbs_live.py`'s
`request_for` documents the same rule for the same MDP).

Time mapping: mode `"date"` (`pricebt.pricer.TimeMapping`) -- exactly one snapshot per calendar date, the simplest fit for an EOD source that only
serves a known, finite list of dates (it does not enumerate ARBS's whole history). The Selector is built over PROVISIONAL 15:00 America/New_York
stamps (ARBS's own EOD convention, `IRSwapsMDP._to_eris_eod_timestamp`) purely to route a query timestamp to one of the known dates; the snapshot's
real `ts` is always ARBS's own served `meta()["timestamp"]`, asserted (never trusted) to fall on the requested calendar date.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

import numpy as np
import pandas as pd

from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.pricer import TimeMapping
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot

from support.common import CachedProvider, Selection, Selector, calendar_data, time_mapping, to_day

from . import _compat as C

_REPO_ROOT = Path(__file__).resolve().parent.parent
_USD_FED_JSON = _REPO_ROOT / "configs" / "suite" / "calendars" / "usd_fed.json"
_NYC_FIXTURE_JSON = _REPO_ROOT / "data" / "fixtures" / "calendars" / "nyc.json"
_EOD_TIME = dt.time(15, 0)  # ARBS's own EOD stamp convention (IRSwapsMDP._to_eris_eod_timestamp): 15:00 America/New_York
_NY = "America/New_York"


def _nyc_calendar() -> CalendarData:
    """The SHIPPED "nyc" calendar: `configs/suite/calendars/usd_fed.json` (tracked, not git-ignored) if present, else the fixtures copy
    `data/fixtures/calendars/nyc.json`. Both hold the identical rateslib-sourced holiday list (checked in the GATE 1 script's calendar cross-check)."""
    path = _USD_FED_JSON if _USD_FED_JSON.is_file() else _NYC_FIXTURE_JSON
    if not path.is_file():
        raise ConfigError(
            f"no shipped 'nyc' calendar found ({_USD_FED_JSON} or {_NYC_FIXTURE_JSON})", code="CFG-CALENDAR"
        )
    return calendar_data(path, as_name="nyc")


class ArbsErisEodProvider(CachedProvider):
    """timestamp -> `SnapshotPricer` from ARBS's own `USD-SOFR-1D` EOD curve, over the dates given at construction.

    `dates`: an iterable of dates (or anything `pandas.Timestamp` accepts) this provider will actually be asked for. `time`: a `TimeMapping` or the
    `time:` block a config loader builds (mode `"date"` by default -- pass `{"mode": "asof", ...}` to relax it). `curve_id`: the name curves and
    fixings are keyed under in the emitted snapshot (default `"sofr"`, matching `pricebt.contrib.rateslib`'s own convention).
    """

    _cache_attrs = ("_pricers", "_mdp_inst")

    def __init__(
        self,
        dates: Iterable[Any],
        *,
        curve_name: str = "USD-SOFR-1D",
        time: Union[None, TimeMapping, Mapping[str, Any]] = None,
        curve_id: str = "sofr",
        calendars_root: Optional[Any] = None,
        max_cached_pricers: int = 64,
    ):
        self.curve_name = str(curve_name)
        self.curve_id = str(curve_id)
        days = sorted({to_day(d) for d in dates})
        if not days:
            raise ConfigError("ArbsErisEodProvider needs at least one date", code="CFG-PROVIDER")
        self.dates = tuple(days)
        self.time = time_mapping(time, kind="eod", mode="date")
        self._calendar = _nyc_calendar() if calendars_root is None else calendar_data("nyc", calendars_root, as_name="nyc")
        stamps = pd.DatetimeIndex(
            [pd.Timestamp(dt.datetime.combine(d, _EOD_TIME)) for d in self.dates]
        ).tz_localize(_NY)
        stamp_ns = stamps.tz_convert("UTC").as_unit("ns").asi8.copy()
        self._sel = Selector(stamp_ns, self.time, eod=True)
        self._init_caches(max_cached_pricers)

    def _init_caches(self, max_cached_pricers: int) -> None:
        super()._init_caches(max_cached_pricers)
        self._mdp_inst: Any = None  # rebuilt lazily; dropped across pickling like every other cache

    def _mdp(self) -> Any:
        if self._mdp_inst is None:
            IRSwapsMDP = C.irswaps_mdp()
            self._mdp_inst = IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC")
        return self._mdp_inst

    def _fetch(self, day: dt.date) -> Any:
        """One read-only `get_pricer` call for `day`, a FRESH request dict (ARBS pops the keys it reads). Any ARBS exception (a miss, a bad
        business day, ...) becomes `MarketDataUnavailable`, never a crash."""
        req = {"curve_name": self.curve_name, "timestamp": day}
        try:
            return self._mdp().get_pricer(request=req)
        except Exception as e:  # noqa: BLE001 - ARBS's own exception types are not ours to enumerate; translate every one
            raise MarketDataUnavailable(pd.Timestamp(dt.datetime.combine(day, _EOD_TIME), tz=_NY), req, f"arbs_miss: {type(e).__name__}: {e}") from e

    def _snapshot(self, sel: Selection) -> MarketSnapshot:
        day = self.dates[sel.pos]
        raw = self._fetch(day)
        nodes = raw.nodes()  # {date: discount_factor}; the first entry IS the reference date, value 1.0
        node_dates = sorted(nodes)
        if not node_dates:
            raise MarketDataUnavailable(sel.stamp, {}, f"arbs served an empty curve for {day}")
        reference_date = node_dates[0]
        values = tuple(float(nodes[d]) for d in node_dates)
        curve = CurveSnapshot(
            self.curve_id, reference_date, tuple(node_dates), values, interpolation="log_linear_df",
            provenance={"source": "arbs:ERIS_EOD_LIVE-RL_BASIC", "curve_name": self.curve_name, "requested_date": str(day)},
        )
        fx = raw.index()  # a pandas Series, PERCENT, naive midnight index (ARBS's own fixings, already *100 -- see IRSwapsMDP._build_eris_eod_rl_curve)
        fx_dates = tuple(pd.Timestamp(x).date() for x in fx.index)
        fx_values = np.asarray(fx.to_numpy(), dtype=float)
        keep = [i for i, d in enumerate(fx_dates) if d < reference_date]  # MarketSnapshot's own invariant: fixings strictly before the reference date
        fixings = FixingsSeries(self.curve_id, tuple(fx_dates[i] for i in keep), tuple(float(fx_values[i]) for i in keep), unit="percent")
        meta = raw.meta()
        ts = meta.get("timestamp")
        if ts is None or pd.Timestamp(ts).tzinfo is None:
            raise MarketDataUnavailable(sel.stamp, {}, f"arbs served no tz-aware snapshot stamp for {day} (meta={meta!r})")
        ts = pd.Timestamp(ts)
        served_day = ts.tz_convert(_NY).date()
        if served_day != day:
            raise MarketDataUnavailable(sel.stamp, {}, f"arbs served a stamp dated {served_day} for a request for {day}")
        return MarketSnapshot(
            ts, reference_date, curves={self.curve_id: curve}, fixings={self.curve_id: fixings}, calendars={"nyc": self._calendar},
            provenance={"source": "arbs_adapter:ArbsErisEodProvider", "curve_name": self.curve_name, "requested_date": str(day)},
        )

    def to_config(self) -> dict:
        return {
            "type": self._type_path(), "dates": [d.isoformat() for d in self.dates], "curve_name": self.curve_name, "curve_id": self.curve_id,
            "time": {"mode": self.time.mode, "tz": self.time.tz},
        }
