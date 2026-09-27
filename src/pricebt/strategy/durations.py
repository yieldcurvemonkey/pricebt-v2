"""`trade_duration` grammar resolved against the backtest calendar, plus the calendar-tenor schedule used by PeriodicTrigger.

Grammar (case-insensitive): None | tenor chain '1m', '1w', '2b', '1m+1b', '1b@close', '2w@10:30' | fixed '15min', '2h', '90s' |
date / Timestamp / datetime | pandas Timedelta | 'next schedule' | the name of a RESOLVED TERM holding a date (for example `maturity`) | CustomDuration.
`m` is ALWAYS months (minutes are `min`). `d` rolls FORWARD onto a business day (a weekend exit would be off any market grid).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd

from ..errors import DurationError, StrategyError
from ..timeutil import Calendar, TimelineContext, apply_tenor
from .infos import NO_SCHEDULE

FOREVER = pd.Timestamp.max.tz_localize("UTC")
_CAL_UNITS = {"b": "b", "bd": "b", "d": "d", "w": "w", "m": "m", "mo": "m", "y": "y"}
_FIXED = {"s": 1.0, "sec": 1.0, "min": 60.0, "h": 3600.0, "hr": 3600.0}
_PART = re.compile(r"([+-]?)\s*(\d+)\s*([a-zA-Z]+)")
_CHAIN = re.compile(r"\s*(?:[+-]?\s*\d+\s*[a-zA-Z]+\s*)+")


class PastExitError(DurationError):
    """The resolved exit is not after the entry."""


@dataclass(frozen=True)
class CustomDuration:
    durations: Tuple[Any, ...]
    function: Union[str, Callable[..., Any]] = "min"

    def __post_init__(self) -> None:
        object.__setattr__(self, "durations", tuple(self.durations))

    @staticmethod
    def earliest(*d: Any) -> "CustomDuration":
        return CustomDuration(tuple(d), "min")

    @staticmethod
    def latest(*d: Any) -> "CustomDuration":
        return CustomDuration(tuple(d), "max")


Duration = Union[None, str, dt.date, dt.datetime, pd.Timestamp, dt.timedelta, CustomDuration]


def local_at(d: dt.date, tod: dt.time, tz: str) -> pd.Timestamp:
    """Local wall-clock instant; a nonexistent time moves forward, an ambiguous one takes the first occurrence."""
    return pd.Timestamp(dt.datetime.combine(d, tod)).tz_localize(tz, ambiguous=True, nonexistent="shift_forward")


def _parse_tenor(s: str) -> Optional[Tuple[List[Tuple[int, str]], Optional[str]]]:
    """(parts, '@time' text) when `s` is a tenor chain / fixed token, else None."""
    body, _, at = s.partition("@")
    if not _CHAIN.fullmatch(body):
        return None
    parts = []
    for sign, n, unit in _PART.findall(body):
        u = unit.lower()
        if u not in _CAL_UNITS and u not in _FIXED:
            return None
        parts.append((-int(n) if sign == "-" else int(n), u))
    return (parts, at.strip().lower() or None) if parts else None


def _time_of(text: str, ctx: TimelineContext) -> dt.time:
    if text == "open":
        return ctx.time.session_open
    if text == "close":
        return ctx.time.session_close
    try:
        return dt.time.fromisoformat(text)
    except ValueError as e:
        raise DurationError(f"bad time {text!r} in duration") from e


def roll_tenor(base: dt.date, parts: Sequence[Tuple[int, str]], cal: Calendar) -> dt.date:
    d = base
    for n, u in parts:
        unit = _CAL_UNITS[u]
        d = apply_tenor(d, (n, unit), cal)
        if unit == "d":
            d = cal.following(d)
    return d


def _to_ts(x: Any, ctx: TimelineContext, cal: Calendar) -> pd.Timestamp:
    if isinstance(x, (pd.Timestamp, dt.datetime)):
        if x.tzinfo is None and (x.hour, x.minute, x.second, x.microsecond) == (0, 0, 0, 0):
            return ctx.at(cal.following(x.date()))  # a naive midnight stamp is a date (pandas has no date type): date_policy applies
        return ctx.time.localize(x)
    if isinstance(x, dt.date):
        return ctx.at(cal.following(x))
    raise DurationError(f"cannot interpret {x!r} as an instant")


def _resolve(duration: Any, entry_ts: pd.Timestamp, terms: Optional[Mapping[str, Any]], info: Any, ctx: TimelineContext, cal: Calendar) -> Optional[pd.Timestamp]:
    if duration is None:
        return None
    if isinstance(duration, CustomDuration):
        vals = []
        for d in duration.durations:
            r = _resolve(d, entry_ts, terms, info, ctx, cal)
            vals.append(FOREVER if r is None else r)
        fn = {"min": min, "max": max}.get(duration.function) if isinstance(duration.function, str) else duration.function
        if fn is None:
            raise DurationError(f"unknown CustomDuration function {duration.function!r}")
        out = fn(*vals)
        return None if pd.Timestamp(out) == FOREVER else ctx.time.localize(out)
    if isinstance(duration, (dt.timedelta, pd.Timedelta)):
        return entry_ts + pd.Timedelta(duration)
    if isinstance(duration, (dt.date, dt.datetime, pd.Timestamp)):
        return _to_ts(duration, ctx, cal)
    if not isinstance(duration, str):
        raise DurationError(f"unsupported trade_duration {duration!r}")
    text = duration.strip()
    tenor = _parse_tenor(text)
    if tenor is not None:
        parts, at = tenor
        fixed = [p for p in parts if p[1] in _FIXED]
        if fixed:
            if len(fixed) != len(parts) or at:
                raise DurationError(f"fixed durations cannot be chained with calendar tokens or '@time': {duration!r}")
            return entry_ts + pd.Timedelta(seconds=sum(n * _FIXED[u] for n, u in parts))
        local = entry_ts.tz_convert(ctx.tz)
        d1 = roll_tenor(local.date(), parts, cal)
        tod = _time_of(at, ctx) if at else local.time()
        return local_at(d1, tod, ctx.tz)
    if re.sub(r"\s+", " ", text.lower()) == "next schedule":
        ns = getattr(info, "next_schedule", NO_SCHEDULE)
        if ns is NO_SCHEDULE:
            raise DurationError("'next schedule' needs a trigger that supplies next_schedule (Periodic, Date, Event)")
        return None if ns is None else ctx.time.localize(ns)
    if terms is not None and text in terms:
        v = terms[text]
        if v is None or not isinstance(v, (dt.date, dt.datetime, pd.Timestamp)):
            raise DurationError(f"resolved term {text!r} is not a date: {v!r}")
        return _to_ts(v, ctx, cal)
    raise DurationError(f"cannot resolve trade_duration {duration!r}: not a tenor, 'next schedule', or the name of a resolved date term ({sorted(terms) if terms else 'none available'})")


def resolve_exit(duration: Duration, *, entry_ts: pd.Timestamp, terms: Optional[Mapping[str, Any]] = None, info: Any = None, ctx: TimelineContext, calendar: Optional[Calendar] = None) -> Optional[pd.Timestamp]:
    """Exit instant for a position entered at `entry_ts`; None = hold to the end. Beyond the backtest end resolves to None.
    Raises PastExitError when the result is not strictly after the entry."""
    out = _resolve(duration, pd.Timestamp(entry_ts), terms, info, ctx, calendar or ctx.calendar)
    if out is None:
        return None
    if out <= entry_ts:
        raise PastExitError(f"trade_duration {duration!r} resolves to {out}, not after entry {entry_ts}")
    return None if out > ctx.end else out


def needs_terms(duration: Any) -> bool:
    """True when resolving `duration` may read a RESOLVED term of the built instrument (a name that is neither a tenor nor 'next schedule')."""
    if isinstance(duration, CustomDuration):
        return any(needs_terms(d) for d in duration.durations)
    if not isinstance(duration, str):
        return False
    return _parse_tenor(duration.strip()) is None and re.sub(r"\s+", " ", duration.strip().lower()) != "next schedule"


def periodic_schedule(base: dt.date, frequency: str, end: dt.date, cal: Calendar) -> List[dt.date]:
    """[base] + multiples k*N of the single-token rule FROM base (gs RelativeDateSchedule) while <= end; `d` rolls forward."""
    parsed = _parse_tenor(str(frequency))
    if parsed is None or len(parsed[0]) != 1 or parsed[1] or parsed[0][0][1] not in _CAL_UNITS:
        raise StrategyError(f"frequency must be one calendar token like '1b','1w','1m' (fixed durations belong to IntradayPeriodicTrigger): {frequency!r}")
    n, u = parsed[0][0]
    if n < 1:
        raise StrategyError(f"frequency must be >= 1: {frequency!r}")
    out, k, guard = [base], 1, 0
    while True:
        d = roll_tenor(base, [(k * n, u)], cal)
        if d > end:
            break
        if d > out[-1]:
            out.append(d)
        k += 1
        guard += 1
        if guard > 100_000:
            raise StrategyError(f"schedule {frequency!r} from {base} to {end} did not terminate")
    return out
