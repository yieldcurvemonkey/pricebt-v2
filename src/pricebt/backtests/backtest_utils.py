"""
Copyright 2019 Goldman Sachs.
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
"""
# Ported to pricebt from gs_quant 2.1.17 (Apache-2.0); see NOTICE. Changes: DEV-T1, DEV-T2, DEV-T16
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, Union

import pandas as pd

from ..common import CurrencyName
from ..datetime.relative_date import RelativeDate


class CalcType(Enum):
    simple = 'simple'
    semi_path_dependent = 'semi_path_dependent'
    path_dependent = 'path_dependent'


@dataclass
class CustomDuration:
    durations: tuple[Union[str, dt.date, dt.timedelta], ...]
    # gs's `function` is required (no default); a pricebt DEV-* default here would need a §11 row,
    # which decision 0.1 never granted -- get_final_date calls it unconditionally (no None-check),
    # so a None default only ever produced an object that crashes later anyway.
    function: Callable[[tuple[Union[str, dt.date, dt.timedelta], ...]], Union[str, dt.date, dt.timedelta]]

    def __hash__(self):
        return hash((self.durations, self.function))


def make_list(thing):
    if thing is None:
        return []
    if isinstance(thing, str):
        return [thing]
    else:
        try:
            iter(thing)
        except TypeError:
            return [thing]
        else:
            return list(thing)


final_date_cache: dict = {}


def clear_final_date_cache() -> None:
    """Clear the module-global get_final_date cache.

    gs's final_date_cache is a module-global dict that is never cleared, so a stale result from
    one backtest run leaks into the next one that happens to build the same (instrument,
    create_date, duration, holiday_calendar) key. GenericEngine.run_backtest calls this at the
    start of every run (P3.5); the test isolation fixture also calls it between tests."""
    # pricebt DEV-T2: gs never clears final_date_cache; this function exists so callers can.
    final_date_cache.clear()


def get_final_date(inst, create_date, duration, holiday_calendar=None, trigger_info=None):
    if isinstance(holiday_calendar, list):
        # pricebt DEV-T16: a list holiday_calendar is unhashable, so using it unchanged in the
        # cache_key tuple below raises `TypeError: unhashable type: 'list'`. Normalise to a tuple
        # (also the value stored in the cache key, and the value threaded through the CustomDuration
        # recursion and the RelativeDate call below).
        holiday_calendar = tuple(holiday_calendar)

    cache_key = (inst, create_date, duration, holiday_calendar)
    if cache_key in final_date_cache:
        return final_date_cache[cache_key]

    if duration is None:
        final_date_cache[cache_key] = dt.date.max
        return dt.date.max
    if isinstance(duration, dt.timedelta):
        # pricebt DEV-T1: gs crashes on a timedelta duration (`.lower()` has no meaning on a
        # timedelta -- AttributeError/TypeError depending on version), even though the `Duration`
        # type and the action docstrings promise it is supported.
        final_date_cache[cache_key] = create_date + duration
        return create_date + duration
    if isinstance(duration, (dt.datetime, dt.date)):
        final_date_cache[cache_key] = duration
        return duration
    if hasattr(inst, str(duration)):
        final_date_cache[cache_key] = getattr(inst, str(duration))
        return getattr(inst, str(duration))
    if str(duration).lower() == 'next schedule':
        if hasattr(trigger_info, 'next_schedule'):
            return trigger_info.next_schedule or dt.date.max
        raise RuntimeError('Next schedule not supported by action')
    if isinstance(duration, CustomDuration):
        return duration.function(
            *(get_final_date(inst, create_date, d, holiday_calendar, trigger_info) for d in duration.durations)
        )

    final_date_cache[cache_key] = RelativeDate(duration.lower(), create_date).apply_rule(
        holiday_calendar=holiday_calendar
    )
    return final_date_cache[cache_key]


def scale_trade(inst, ratio: float):
    new_inst = inst.scale(ratio)
    return new_inst


def map_ccy_name_to_ccy(currency_name: Union[str, CurrencyName]):
    map = {
        'United States Dollar': 'USD',
        'Australian Dollar': 'AUD',
        'Canadian Dollar': 'CAD',
        'Swiss Franc': 'CHF',
        'Yuan Renminbi (Hong Kong)': 'CNH',
        'Czech Republic Koruna': 'CZK',
        'Euro': 'EUR',
        'Pound Sterling': 'GBP',
        'Japanese Yen': 'JPY',
        'South Korean Won': 'KRW',
        'Malasyan Ringgit': 'MYR',
        'Norwegian Krone': 'NOK',
        'New Zealand Dollar': 'NZD',
        'Polish Zloty': 'PLN',
        'Russian Rouble': 'RUB',
        'Swedish Krona': 'SEK',
        'South African Rand': 'ZAR',
        'Yuan Renminbi (Onshore)': 'CHY',
    }

    return map.get(currency_name.value if isinstance(currency_name, CurrencyName) else currency_name)


def interpolate_signal(signal: dict, method=None) -> pd.Series:
    """Ported: gs_quant.backtests.backtest_utils.interpolate_signal, minus the gs_quant.timeseries
    dependency (DESIGN.md section 9.2's import map drops `Interpolate`/`interpolate`, whose only
    call site in gs's own backtests package always used `method=Interpolate.STEP`): step-
    interpolated ("value carried forward") via reindex + ffill on the full calendar-day range,
    which gives the same result. `method` is kept (position 2, like gs) for constructor-shape
    parity only -- it is accepted and ignored, since step is the only behaviour implemented; the
    parity snapshot's default-repr for this parameter cannot match gs's `Interpolate.STEP` without
    importing gs_quant, which MUST-1 forbids."""
    min_date = min(signal.keys())
    max_date = max(signal.keys())
    all_dates = [min_date + dt.timedelta(days=day) for day in range((max_date - min_date).days + 1)]
    return pd.Series(signal).sort_index().reindex(all_dates).ffill()


# Used for strict intraday interval parsing (d/h/m/s only)
_TIMEDELTA_PATTERN = re.compile(
    r'^(?:(\d+(?:\.\d+)?)\s*(?:d|day|days))?\s*'
    r'(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours))?\s*'
    r'(?:(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes))?\s*'
    r'(?:(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds))?\s*$',
    re.IGNORECASE,
)


def parse_timedelta(value: Optional[Union[int, float, str, dt.timedelta]]) -> Optional[dt.timedelta]:
    """Decode a string time interval into datetime.timedelta.

    This is for *fixed* time intervals only (intraday intervals). It will:

    - return timedelta values unchanged
    - interpret numbers (int/float) as seconds
    - interpret numeric strings as seconds
    - parse d/h/m/s formatted strings (e.g. '15m', '1h30m', '2d', '90s', '1d12h')

    If the string cannot be parsed as a fixed interval (e.g. '1b', '15m' intended as months, '3M', etc),
    it raises ValueError.

    Note: use decode_frequency for tenor/frequency strings.
    """
    if value is None:
        return None
    if isinstance(value, dt.timedelta):
        return value

    if isinstance(value, (int, float)):
        return dt.timedelta(seconds=float(value))

    if isinstance(value, str):
        s = value.strip()

        # numeric string -> seconds
        try:
            return dt.timedelta(seconds=float(s))
        except ValueError:
            pass

        m = _TIMEDELTA_PATTERN.match(s)
        if m and any(m.groups()):
            days, hours, minutes, seconds = (float(v) if v else 0.0 for v in m.groups())
            return dt.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

        raise ValueError(f'Cannot parse {value!r} as a timedelta. Examples: 3600, "60m", "2h", "90s", "1d12h30m"')

    raise TypeError(f'Cannot convert {value!r} to timedelta')
