"""DataFrequency, DataContext (the gs date-window context), Dataset (stub), measure_series (a
pricebt extension) (DESIGN.md section 10; docs/v2/MARKET_CONTEXT_DESIGN.md section 6)."""
from __future__ import annotations

import datetime as _dt
import logging
import re
from datetime import date as _date
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from ..base import EnumBase
from ..datetime.relative_date import RelativeDateSchedule
from ..errors import ConfigError, NotSupportedError, PricebtError
from ..risk import RiskMeasure
from ..risk.results import LazyFuture
from ..risk.scenarios import require_no_scenario

__all__ = ["DataContext", "DataFrequency", "Dataset", "measure_series"]

logger = logging.getLogger(__name__)


class DataFrequency(EnumBase, str, Enum):
    DAILY = "daily"
    REAL_TIME = "realTime"
    ANY = "any"


_DATA_STACK: List["DataContext"] = []
_DATA_DEFAULT: Optional["DataContext"] = None  # `DataContext.current = ...`, used while _DATA_STACK is empty


class _DataContextMeta(type):
    """gs `ContextMeta` for `DataContext` (a `ContextBaseWithDefault`): `current` is the innermost
    entered context, else the default set by assigning `DataContext.current = ...`, else a fresh
    `DataContext()`. The same shape as `pricebt.markets.PricingContext.current`."""

    @property
    def current(cls) -> "DataContext":
        return _DATA_STACK[-1] if _DATA_STACK else _DATA_DEFAULT or DataContext()

    @current.setter
    def current(cls, current: "DataContext") -> None:
        global _DATA_DEFAULT
        if _DATA_STACK:
            raise ValueError(f"Cannot set current while in a nested context {cls.__name__}")
        _DATA_DEFAULT = current


class DataContext(metaclass=_DataContextMeta):
    """gs `gs_quant.data.DataContext`, ported: the date (or time) window data requests default to.

    >>> with DataContext(date(2024, 1, 2), date(2024, 3, 29)):
    ...     dv01 = measure_series(IRSwap("Pay", "10y", "USD"), IRDelta(aggregation_level="Type"))

    `start`/`end` are a `date`, a `datetime` or an ISO string (`'2024-01-02'`, or
    `'2024-01-02T00:00:00Z'` for the time properties); unset, the window is the last 30 days
    (`start_date`/`end_date`) or the last day (`start_time`/`end_time`, UTC). Nothing is inherited
    from an enclosing `DataContext`, as in gs. In pricebt it is read by `measure_series`."""

    def __init__(self, start=None, end=None, interval=None):
        self.__start = start
        self.__end = end
        if interval is not None:
            if not isinstance(interval, str):
                raise TypeError("interval must be a str")  # gs: MqTypeError (an MqError, not a TypeError; pricebt DEV-M4)
            if not re.fullmatch("[1-9]\\d{0,2}[a-z]", interval):
                raise ValueError("interval must be a valid str e.g. 1m, 2h, 3d")  # gs: MqValueError, which is a ValueError
        self.__interval = interval
        self._is_entered = False

    @staticmethod
    def _get_date(o, default):
        if o is None:
            return default
        if isinstance(o, _dt.datetime):  # a datetime is also a date
            return o.date()
        if isinstance(o, _dt.date):
            return o
        if isinstance(o, str):
            loc = o.find("T")
            return _dt.datetime.strptime(o[:loc] if loc != -1 else o, "%Y-%m-%d").date()
        raise ValueError(f"{o} is not a valid date")

    @staticmethod
    def _get_datetime(o, default):
        if o is None:
            return default
        if isinstance(o, _dt.datetime):
            return o
        if isinstance(o, _dt.date):
            return _dt.datetime.combine(o, _dt.time(tzinfo=_dt.timezone.utc))
        if isinstance(o, str):
            return _dt.datetime.strptime(o, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        raise ValueError(f"{o} is not a valid date")

    @property
    def start_date(self) -> _date:
        return self._get_date(self.__start, _date.today() - _dt.timedelta(days=30))

    @property
    def end_date(self) -> _date:
        return self._get_date(self.__end, _date.today())

    @property
    def start_time(self) -> _dt.datetime:
        return self._get_datetime(self.__start, _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1))

    @property
    def end_time(self) -> _dt.datetime:
        return self._get_datetime(self.__end, _dt.datetime.now(_dt.timezone.utc))

    @property
    def interval(self) -> Optional[str]:
        return self.__interval

    @property
    def is_entered(self) -> bool:
        return self._is_entered

    def __enter__(self) -> "DataContext":
        _DATA_STACK.append(self)
        self._is_entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _DATA_STACK.pop()
        self._is_entered = False
        return False


class Dataset:
    """gs-compatible stub: pricebt has no server-side dataset store, so construction always
    raises (DESIGN.md section 10)."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotSupportedError(
            "gs Dataset reads GS server-side data, which pricebt does not have; build a pandas "
            "Series from your own data or use pricebt.data.measure_series(...)"
        )


def measure_series(
    instrument: Any,
    measure: Union[str, RiskMeasure],
    start: Optional[_date] = None,
    end: Optional[_date] = None,
    frequency: str = "1b",
    holiday_calendar: Optional[Any] = None,
    csa: Optional[str] = None,
    fill: Optional[str] = "ffill",
) -> pd.Series:
    """DESIGN.md section 10: replaces gs's `Dataset` for a per-instrument measure history (used by
    the 040304 notebook's par-rate series). `instrument` must be UNRESOLVED: a fresh, quantity-1
    clone of it is resolved independently on each schedule date, so a date's market never leaks
    into another date's value. `start`/`end` default to the current `DataContext`'s
    `start_date`/`end_date` (gs `DataCoordinate.get_range`), so a block of series shares one window:
    `with DataContext(start, end): ...`.

    `measure` is either the name of a `functions:`/`portfolio_functions:` entry in the matched
    asset config, or a `RiskMeasure` resolved through the section 8.1 measure -> function mapping
    (only its SCALAR form is supported: a bucketed result has no single float to put in the
    series). A date with no market is missing; `fill='ffill'` forward-fills from the previous
    available date and drops any LEADING missing dates (never back-fills them); `fill=None` drops
    every missing point. `series.attrs['unit']` is the function's declared unit string (e.g. 'bp'),
    except that a currency-denominated unit (`ccy`/`ccy_per_bp`/`ccy_per_bp2`) is reported as the
    resolved currency code (e.g. 'USD') instead of the raw label -- the same `_unit_dict` rule
    `PricingService.value` uses, so the function-name route and the `RiskMeasure` route always agree
    for the same underlying function (matching how `FloatWithInfo.unit` reports it elsewhere in
    pricebt). `series.attrs['missing_dates']` is the list of dates that had no market.
    """
    if fill != "ffill" and fill is not None:
        raise ValueError(f"measure_series: fill must be 'ffill' or None, got {fill!r}")
    require_no_scenario("measure_series")  # pricebt DEV-S7
    if start is None:
        start = DataContext.current.start_date
    if end is None:
        end = DataContext.current.end_date
    if instrument.resolved_terms is not None:
        raise ValueError("measure_series: instrument must be UNRESOLVED (pass a freshly constructed instrument, not an already-resolved one), so each schedule date resolves it independently")

    from ..assets.pricing import _unit_dict  # import DAG: assets.pricing sits above data (section 3.2)
    from ..session import PricebtSession  # import DAG: session sits above data (section 3.2 item 5)

    session = PricebtSession.current
    if session is None:
        raise PricebtError("no PricebtSession: call PricebtSession.use(assets=[...]) first")
    service = session.pricing
    asset = service.asset_for(instrument)

    is_function_name = isinstance(measure, str)
    unit: Optional[str] = None
    if is_function_name:
        spec = asset.functions.get(measure) or asset.portfolio_functions.get(measure)
        if spec is None:
            raise ConfigError(
                f"asset {asset.name} has no function named {measure!r}; add it under functions: or portfolio_functions:",
                asset=asset.name,
            )
        unit = next(iter(_unit_dict(spec, spec.currency or asset.currency)), "number")

    dates = RelativeDateSchedule(frequency, start, end).apply_rule(holiday_calendar=holiday_calendar)
    fresh = instrument.clone(quantity_=1.0)  # DESIGN section 10 step 2: evaluate for quantity 1

    raw: Dict[_date, float] = {}
    missing: List[_date] = []
    for d in dates:
        if not service.has_market(asset, d, csa):
            missing.append(d)
            continue
        if is_function_name:
            value = service.unit_value(fresh, d, measure, csa)
        else:
            value = service.value(fresh, d, measure, csa)
            if isinstance(value, LazyFuture):
                raise NotSupportedError(
                    f"measure_series: {measure!r} is a bucketed risk measure; pass a scalar RiskMeasure "
                    "(e.g. set aggregation_level=Type/Asset/Class) or a function name instead"
                )
            if unit is None:
                unit = next(iter(getattr(value, "unit", None) or {}), "number")
        raw[d] = float(value)

    logger.info("measure_series: %s of %s scheduled date(s) had no market for %s.%s", len(missing), len(dates), asset.name, measure)

    if fill == "ffill":
        series_dict: Dict[_date, float] = {}
        last: Optional[float] = None
        for d in dates:
            if d in raw:
                last = raw[d]
            if last is not None:  # drop leading missing dates rather than back-filling them
                series_dict[d] = last
    else:
        series_dict = raw

    series = pd.Series(series_dict, dtype=float)
    series.attrs["unit"] = unit or "number"
    series.attrs["missing_dates"] = missing
    return series
