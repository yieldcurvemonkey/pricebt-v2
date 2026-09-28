"""DataFrequency, Dataset (stub), measure_series (a pricebt extension) (DESIGN.md section 10)."""
from __future__ import annotations

import logging
from datetime import date as _date
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from ..base import EnumBase
from ..datetime.relative_date import RelativeDateSchedule
from ..errors import ConfigError, NotSupportedError, PricebtError
from ..risk import RiskMeasure
from ..risk.results import LazyFuture

__all__ = ["DataFrequency", "Dataset", "measure_series"]

logger = logging.getLogger(__name__)


class DataFrequency(EnumBase, str, Enum):
    DAILY = "daily"
    REAL_TIME = "realTime"
    ANY = "any"


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
    start: _date,
    end: _date,
    frequency: str = "1b",
    holiday_calendar: Optional[Any] = None,
    csa: Optional[str] = None,
    fill: Optional[str] = "ffill",
) -> pd.Series:
    """DESIGN.md section 10: replaces gs's `Dataset` for a per-instrument measure history (used by
    the 040304 notebook's par-rate series). `instrument` must be UNRESOLVED: a fresh, quantity-1
    clone of it is resolved independently on each schedule date, so a date's market never leaks
    into another date's value.

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
