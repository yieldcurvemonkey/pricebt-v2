"""gs_quant `backtests.data_sources` names. `GenericDataSource(series, MissingDataStrategy.fill_forward)` is a pricebt `SeriesSource`
(immutable, tz-aware, as-of lookups with no look-ahead). Date-only keys become visible at the active session's daily time (default 17:00
America/New_York), i.e. at the same instant as that date's grid point. `GsDataSource` needs GS data services and is a stub."""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping, Union

import pandas as pd

from ..errors import NotSupportedError
from ..strategy.signals import DataSource, MissingDataStrategy, SeriesSource

__all__ = ["DataSource", "GenericDataSource", "GsDataSource", "MissingDataStrategy"]


class GenericDataSource(SeriesSource):
    """gs `GenericDataSource(data_set, missing_data_strategy=MissingDataStrategy.fail)`; extra keywords go to `SeriesSource`
    (tz, date_time, lag, max_staleness, name). `interpolate` needs the next observation (look-ahead) and raises NotSupportedError."""

    def __init__(self, data_set: Union[pd.Series, Mapping[Any, float]], missing_data_strategy: Any = MissingDataStrategy.fail, **kw: Any):
        from ..session import current_session

        s = current_session()
        if s is not None:
            kw.setdefault("tz", s.tz)
            kw.setdefault("date_time", s.daily_time)
        kw.setdefault("date_time", dt.time(17, 0))
        super().__init__(data_set, missing_data_strategy, **kw)
        self.date_time = kw["date_time"]
        self.data_set = data_set
        self.missing_data_strategy = self.strategy

    def get_data(self, state: Any, last_available: bool = False) -> float:
        """gs API: the value visible at `state` (a date, or a naive midnight, means that date's daily time in the source tz)."""
        ts = pd.Timestamp(state)
        if ts.tzinfo is None:
            if (ts.hour, ts.minute, ts.second, ts.microsecond) == (0, 0, 0, 0):
                ts = pd.Timestamp(dt.datetime.combine(ts.date(), self.date_time))
            ts = ts.tz_localize(self.tz)
        return self.get(ts)


class GsDataSource:
    """Stub: gs_quant's GsDataSource reads GS datasets."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotSupportedError("GsDataSource reads GS Marquee datasets, which pricebt does not have; load the data yourself and use "
                                "GenericDataSource(pandas_series, MissingDataStrategy.fill_forward)")
