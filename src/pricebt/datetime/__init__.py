"""business_day_offset, is_business_day, prev_business_date, date_range, business_day_count, today.

gs-shaped date helpers (DESIGN.md section 3.2) backed by `numpy.busday_offset`/`busdaycalendar`
only. `calendars` (holiday-calendar codes) is accepted for signature parity but is always empty
here: pricebt has no GS-infrastructure calendar lookup, so only `week_mask` (weekends) applies
(pricebt DEV-T3, DESIGN.md section 11 -- one-time warning via `_warn_calendars_ignored()` below).
Holiday dates come in only through `RelativeDate.apply_rule(holiday_calendar=[...])`
(relative_date.py), which pricebt code calling these functions directly does not have.
"""
from __future__ import annotations

import warnings
from datetime import date, datetime
from typing import Iterable, Tuple, Union
from zoneinfo import ZoneInfo

import numpy as np

__all__ = [
    "business_day_offset",
    "is_business_day",
    "prev_business_date",
    "date_range",
    "business_day_count",
    "today",
]

_DEFAULT_WEEK_MASK = "1111100"

DateOrDates = Union[date, Iterable[date]]

_warned_calendars = False  # pricebt DEV-T3: one-time warning, see _warn_calendars_ignored() below


def _warn_calendars_ignored() -> None:
    # pricebt DEV-T3: `calendars` would need a GS-infrastructure holiday-calendar lookup, which
    # pricebt has no access to; only `week_mask` (weekends) is applied here. Pass
    # RelativeDate(...).apply_rule(holiday_calendar=[...]) for holiday-aware dates instead. Warn
    # once per process (same shape as relative_date.py's _warn_ignored(), kept separate since the
    # message and call sites differ).
    global _warned_calendars
    if not _warned_calendars:
        warnings.warn(
            "pricebt.datetime: calendars is ignored (no GS infrastructure holiday-calendar lookup "
            "available); only week_mask (weekends) is applied here.",
            stacklevel=3,
        )
        _warned_calendars = True


def _cal(week_mask, calendars=()) -> np.busdaycalendar:
    if calendars:
        _warn_calendars_ignored()
    return np.busdaycalendar(weekmask=week_mask or _DEFAULT_WEEK_MASK)


def business_day_offset(dates: DateOrDates, offsets, roll: str = "raise", calendars=(), week_mask=None) -> DateOrDates:
    # pricebt DEV-T3: calendars is accepted for signature parity and passed to _cal(), which warns
    # once and otherwise ignores it -- see _warn_calendars_ignored() above.
    res = np.busday_offset(dates, offsets, roll, busdaycal=_cal(week_mask, calendars))
    if isinstance(res, np.ndarray):
        return tuple(res.astype("datetime64[D]").astype(object))
    return res.astype("datetime64[D]").astype(object)


def is_business_day(dates: DateOrDates, calendars=(), week_mask=None) -> Union[bool, Tuple[bool, ...]]:
    # pricebt DEV-T3: see business_day_offset above.
    res = np.is_busday(dates, busdaycal=_cal(week_mask, calendars))
    if isinstance(res, np.ndarray):
        return tuple(bool(x) for x in res)
    return bool(res)


def prev_business_date(dates: DateOrDates = date.today(), calendars=(), week_mask=None) -> DateOrDates:
    # Matches gs literally, including its known quirk: the default is evaluated once, at import
    # time, not per call (IMPLEMENTATION_PLAN.md section 9: keep unlisted gs behaviour rather than
    # silently "fixing" it -- not a DESIGN.md section 11 DEV row, so pricebt does not diverge here).
    # pricebt DEV-T3: calendars is not used directly here -- it is forwarded to business_day_offset,
    # which is where the one-time warning actually fires.
    return business_day_offset(dates, -1, roll="forward", calendars=calendars, week_mask=week_mask)


def business_day_count(begin_dates: DateOrDates, end_dates: DateOrDates, calendars=(), week_mask=None):
    # pricebt DEV-T3: see business_day_offset above.
    res = np.busday_count(begin_dates, end_dates, busdaycal=_cal(week_mask, calendars))
    if isinstance(res, np.ndarray):
        return tuple(int(x) for x in res)
    return int(res)


def date_range(begin: Union[int, date], end: Union[int, date], calendars=(), week_mask=None):
    """The 3 gs call forms: (date, date) ascending inclusive; (date, int N) N ascending points from
    begin; (int N, date) N descending points ending at end (ported verbatim, including the gs quirk
    that a non-business-day `begin` in the (date, date) form raises when the range advances)."""
    # pricebt DEV-T3: calendars is not used directly here either -- every branch below forwards it
    # to business_day_offset, which is where the one-time warning fires (on first `next()`, since
    # this function returns a generator).
    if isinstance(begin, date):
        if isinstance(end, date):

            def _f():
                prev = begin
                if prev > end:
                    raise ValueError("begin must be <= end")
                while prev <= end:
                    yield prev
                    prev = business_day_offset(prev, 1, calendars=calendars, week_mask=week_mask)

            return (d for d in _f())
        elif isinstance(end, int):
            return (business_day_offset(begin, i, calendars=calendars, week_mask=week_mask) for i in range(end))
        raise ValueError("end must be a date or int")
    elif isinstance(begin, int):
        if isinstance(end, date):
            return (
                business_day_offset(end, -i, roll="preceding", calendars=calendars, week_mask=week_mask)
                for i in range(begin)
            )
        raise ValueError("end must be a date if begin is an int")
    raise ValueError("begin must be a date or int")


_LOCATION_TZ = {
    # gs's own gs_quant.datetime.date.location_to_tz_mapping, keyed by the PricingLocation enum's
    # own string values. No P1 task owns a PricingLocation type (not in P1.1's enum list, not
    # referenced by any DESIGN.md section 11 row), so this keys on the plain code string instead
    # of importing an enum pricebt does not have; `today()` still accepts anything exposing
    # `.value`, so a future PricingLocation enum works here unchanged.
    "NYC": "America/New_York",
    "LDN": "Europe/London",
    "HKG": "Asia/Hong_Kong",
    "TKO": "Asia/Tokyo",
}


def today(location=None) -> date:
    if not location:
        return date.today()
    tz_name = _LOCATION_TZ.get(getattr(location, "value", location))
    if tz_name is None:
        raise ValueError(f"Unrecognized timezone {location}")
    return datetime.now(ZoneInfo(tz_name)).date()
