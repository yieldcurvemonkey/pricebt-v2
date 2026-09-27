"""Time model: tz-aware Timestamps, Calendar, tenor grammar (gs_quant RelativeDate rules), TimeGrid, Clock."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd
from dateutil.relativedelta import relativedelta

from .errors import ConfigError, EngineInvariantError

DEFAULT_TZ = "America/New_York"
DateLike = Union[dt.date, dt.datetime, pd.Timestamp, str]

#: the length of the year that STATISTICS annualise by (a Julian year of 365.25 days): points per year, CAGR. It is a unit of the statistics, not a pricing day count.
SECONDS_PER_YEAR = 31_557_600.0


class Calendar:
    """Business-day predicate: weekmask + explicit holiday dates. One instance per backtest."""

    def __init__(self, holidays: Iterable[DateLike] = (), weekmask: Sequence[int] = (1, 1, 1, 1, 1, 0, 0), name: str = ""):
        self.name = name
        self.weekmask = _parse_weekmask(weekmask)
        self._holidays = frozenset(_to_date(h) for h in holidays)

    @property
    def holidays(self) -> frozenset:
        return self._holidays

    def is_business_day(self, d: DateLike) -> bool:
        d = _to_date(d)
        return bool(self.weekmask[d.weekday()]) and d not in self._holidays

    def following(self, d: DateLike) -> dt.date:
        d = _to_date(d)
        while not self.is_business_day(d):
            d += dt.timedelta(days=1)
        return d

    def preceding(self, d: DateLike) -> dt.date:
        d = _to_date(d)
        while not self.is_business_day(d):
            d -= dt.timedelta(days=1)
        return d

    def modified_following(self, d: DateLike) -> dt.date:
        d = _to_date(d)
        f = self.following(d)
        return f if f.month == d.month else self.preceding(d)

    def adjust(self, d: DateLike, convention: str = "following") -> dt.date:
        c = convention.lower()
        if c in ("none", "unadjusted"):
            return _to_date(d)
        if c in ("following", "f"):
            return self.following(d)
        if c in ("preceding", "p"):
            return self.preceding(d)
        if c in ("modified_following", "mf"):
            return self.modified_following(d)
        raise ConfigError(f"unknown roll convention {convention!r}", code="CAL")

    def add_business_days(self, d: DateLike, n: int) -> dt.date:
        d = _to_date(d)
        if n == 0:
            return d
        step = 1 if n > 0 else -1
        if not self.is_business_day(d):
            d = self.preceding(d) if n > 0 else self.following(d)
        left = abs(n)
        while left:
            d += dt.timedelta(days=step)
            if self.is_business_day(d):
                left -= 1
        return d

    def business_days(self, start: DateLike, end: DateLike) -> List[dt.date]:
        s, e = _to_date(start), _to_date(end)
        out, d = [], s
        while d <= e:
            if self.is_business_day(d):
                out.append(d)
            d += dt.timedelta(days=1)
        return out

    def extended(self, extra_holidays: Iterable[DateLike]) -> "Calendar":
        return Calendar(set(self._holidays) | {_to_date(h) for h in extra_holidays}, self.weekmask, self.name)

    @classmethod
    def from_json(cls, path: Union[str, Path]) -> "Calendar":
        obj = json.loads(Path(path).read_text(encoding="utf8"))
        return cls(obj.get("holidays", ()), obj.get("weekmask", (1, 1, 1, 1, 1, 0, 0)), obj.get("name", Path(path).stem))

    @classmethod
    def weekends_only(cls) -> "Calendar":
        return cls((), name="weekends")

    def __repr__(self) -> str:
        return f"Calendar({self.name or 'unnamed'}, {len(self._holidays)} holidays)"


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _parse_weekmask(wm: Any) -> Tuple[int, ...]:
    """(1,1,1,1,1,0,0) | '1111100' | 'Mon Tue Wed Thu Fri'."""
    if isinstance(wm, str):
        s = wm.strip()
        if set(s) <= {"0", "1"} and len(s) == 7:
            return tuple(int(c) for c in s)
        names = {t[:3].lower() for t in s.replace(",", " ").split()}
        bad = names - set(_DAYS)
        if bad:
            raise ConfigError(f"unknown weekday names {sorted(bad)} in weekmask {wm!r}", code="CAL")
        return tuple(1 if d in names else 0 for d in _DAYS)
    return tuple(int(x) for x in wm)


_DAY_TITLES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def weekmask_names(wm: Any) -> str:
    """The canonical spelling of a weekmask, `'Mon Tue Wed Thu Fri'`, from names, a 7-bit string or a 7-sequence (what every adapter reads)."""
    bits = _parse_weekmask(wm)
    if len(bits) != 7 or any(b not in (0, 1) for b in bits):
        raise ConfigError(f"a weekmask has seven 0/1 entries (Mon..Sun) or weekday names, got {wm!r}", code="CAL")
    return " ".join(t for t, b in zip(_DAY_TITLES, bits) if b)


def _to_date(d: DateLike) -> dt.date:
    if isinstance(d, pd.Timestamp):
        return d.date()
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, dt.date):
        return d
    if isinstance(d, str):
        return pd.Timestamp(d).date()
    raise TypeError(f"cannot interpret {d!r} as a date")


to_date = _to_date

_TENOR_RE = re.compile(r"^\s*(-?\d+)\s*([a-zA-Z]+)\s*$")
_UNIT_ALIASES = {"bd": "b", "bday": "b", "min": "min", "h": "h", "hr": "h", "mo": "m"}


def parse_tenor(tenor: Union[str, Tuple[int, str]]) -> Tuple[int, str]:
    """'3m' -> (3, 'm'); '15min' -> (15, 'min'); units b,d,w,m,y (calendar) and min,h,s (intraday)."""
    if isinstance(tenor, tuple):
        return int(tenor[0]), str(tenor[1]).lower()
    m = _TENOR_RE.match(str(tenor))
    if not m:
        raise ConfigError(f"bad tenor {tenor!r}", code="TENOR")
    n, unit = int(m.group(1)), m.group(2).lower()
    unit = _UNIT_ALIASES.get(unit, unit)
    if unit not in ("b", "d", "w", "m", "y", "min", "h", "s"):
        raise ConfigError(f"unknown tenor unit {unit!r} in {tenor!r}", code="TENOR")
    return n, unit


def apply_tenor(d: DateLike, tenor: Union[str, Tuple[int, str]], cal: Optional[Calendar] = None) -> dt.date:
    """gs_quant RelativeDate rules: b=business days; d=calendar days (no roll); w,m roll FORWARD; y rolls BACKWARD."""
    cal = cal or Calendar.weekends_only()
    d = _to_date(d)
    n, unit = parse_tenor(tenor)
    if unit == "b":
        return cal.add_business_days(d, n)
    if unit == "d":
        return d + dt.timedelta(days=n)
    if unit == "w":
        return cal.following(d + dt.timedelta(weeks=n))
    if unit == "m":
        return cal.following(d + relativedelta(months=n))
    if unit == "y":
        return cal.preceding(d + relativedelta(years=n))
    raise ConfigError(f"tenor unit {unit!r} is not a date tenor", code="TENOR")


def schedule(start: DateLike, end: DateLike, freq: str, cal: Optional[Calendar] = None) -> List[dt.date]:
    """[start] + multiples k*N of the rule FROM start while <= end (gs RelativeDateSchedule; start is not adjusted)."""
    cal = cal or Calendar.weekends_only()
    s, e = _to_date(start), _to_date(end)
    n, unit = parse_tenor(freq)
    if n <= 0:
        raise ConfigError(f"frequency must be positive: {freq!r}", code="TENOR")
    out, k = [s], 1
    while True:
        nxt = apply_tenor(s, (k * n, unit), cal)
        if nxt > e:
            break
        if nxt > out[-1]:
            out.append(nxt)
        k += 1
        if k > 200_000:
            raise ConfigError("schedule did not terminate", code="TENOR")
    return out


@dataclass(frozen=True)
class TimeContext:
    """tz, calendar and date policy shared by grid, triggers and durations (gs had three calendars; we have one)."""

    tz: str = DEFAULT_TZ
    calendar: Calendar = field(default_factory=Calendar.weekends_only)
    date_policy: str = "close"  # open | close | midnight
    session_open: dt.time = dt.time(8, 0)
    session_close: dt.time = dt.time(17, 0)

    def localize(self, x: Any) -> pd.Timestamp:
        ts = pd.Timestamp(x)
        if ts.tzinfo is None:
            return ts.tz_localize(self.tz)
        return ts.tz_convert(self.tz)

    def at(self, d: DateLike, *, time: Optional[dt.time] = None) -> pd.Timestamp:
        """A date becomes a Timestamp per date_policy; a datetime/Timestamp is localised."""
        if isinstance(d, (pd.Timestamp, dt.datetime)):
            return self.localize(d)
        d = _to_date(d)
        t = time or {"open": self.session_open, "close": self.session_close, "midnight": dt.time(0, 0)}[self.date_policy]
        return pd.Timestamp(dt.datetime.combine(d, t)).tz_localize(self.tz)

    def normalise(self, values: Iterable[Any], grid_days: Optional[Sequence[dt.date]] = None) -> List[pd.Timestamp]:
        out: List[pd.Timestamp] = []
        for v in values:
            if isinstance(v, dt.time) and not isinstance(v, dt.datetime):
                for d in (grid_days or ()):
                    out.append(self.at(d, time=v))
            else:
                out.append(self.at(v))
        return sorted(set(out))

    def in_session(self, ts: pd.Timestamp) -> bool:
        loc = self.localize(ts)
        return self.calendar.is_business_day(loc.date()) and self.session_open <= loc.time() <= self.session_close


class TimeGrid:
    """Sorted unique tz-aware Timestamps plus the TimeContext that produced them."""

    def __init__(self, points: Iterable[pd.Timestamp], ctx: Optional[TimeContext] = None):
        self.ctx = ctx or TimeContext()
        idx = pd.DatetimeIndex(sorted({self.ctx.localize(p) for p in points}))
        if len(idx) and idx.tz is None:
            idx = idx.tz_localize(self.ctx.tz)
        self.points = idx

    def __len__(self) -> int:
        return len(self.points)

    def __iter__(self):
        return iter(self.points)

    def __getitem__(self, i):
        return self.points[i]

    @property
    def start(self) -> pd.Timestamp:
        return self.points[0]

    @property
    def end(self) -> pd.Timestamp:
        return self.points[-1]

    @property
    def days(self) -> Tuple[dt.date, ...]:
        return tuple(sorted({p.date() for p in self.points}))

    def union(self, extra: Iterable[pd.Timestamp]) -> "TimeGrid":
        return TimeGrid(list(self.points) + [self.ctx.localize(e) for e in extra], self.ctx)

    def periods_per_year(self) -> float:
        """Annualisation factor derived from the grid: points per calendar year on the observed span."""
        if len(self.points) < 2:
            return 252.0
        span_years = (self.points[-1] - self.points[0]).total_seconds() / SECONDS_PER_YEAR
        return (len(self.points) - 1) / span_years if span_years > 0 else 252.0

    # ---- builders -------------------------------------------------------------------------------
    @classmethod
    def daily(cls, start: DateLike, end: DateLike, freq: str = "1b", ctx: Optional[TimeContext] = None, *, business_only: bool = True) -> "TimeGrid":
        ctx = ctx or TimeContext()
        days = schedule(start, end, freq, ctx.calendar)
        if business_only:
            days = [d for d in days if ctx.calendar.is_business_day(d)]
        return cls([ctx.at(d) for d in days], ctx)

    @classmethod
    def intraday(cls, start: DateLike, end: DateLike, freq: str = "1min", ctx: Optional[TimeContext] = None, *, include_close: bool = True) -> "TimeGrid":
        ctx = ctx or TimeContext()
        pts: List[pd.Timestamp] = []
        for d in ctx.calendar.business_days(start, end):
            lo = pd.Timestamp(dt.datetime.combine(d, ctx.session_open))
            hi = pd.Timestamp(dt.datetime.combine(d, ctx.session_close))
            naive = pd.date_range(lo, hi, freq=_pandas_freq(freq), inclusive="both" if include_close else "left")
            loc = naive.tz_localize(ctx.tz, ambiguous="NaT", nonexistent="NaT")
            pts.extend(loc[~loc.isna()])
        return cls(pts, ctx)

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any], ctx: Optional[TimeContext] = None) -> "TimeGrid":
        """{start, end, freq: '1b'|'1min'|..., explicit: [...]}"""
        ctx = ctx or TimeContext()
        if "explicit" in cfg:
            return cls([ctx.at(x) for x in cfg["explicit"]], ctx)
        freq = str(cfg.get("freq", "1b"))
        n, unit = parse_tenor(freq)
        if unit in ("min", "h", "s"):
            return cls.intraday(cfg["start"], cfg["end"], freq, ctx)
        return cls.daily(cfg["start"], cfg["end"], freq, ctx)


def _pandas_freq(freq: str) -> str:
    n, unit = parse_tenor(freq)
    return f"{n}{ {'min': 'min', 'h': 'h', 's': 's'}[unit] }"


class Clock:
    """Monotone clock. The look-ahead guard at the market boundary compares against `now`."""

    def __init__(self) -> None:
        self.now: Optional[pd.Timestamp] = None

    def advance(self, ts: pd.Timestamp) -> None:
        if self.now is not None and ts <= self.now:
            raise EngineInvariantError(f"clock must advance strictly: {ts} <= {self.now}")
        self.now = ts

    def reset(self) -> None:
        self.now = None


@dataclass(frozen=True)
class TimelineContext:
    """What triggers/actions/signals know about the run's timeline (built by the engine, passed to Strategy.start)."""

    start: pd.Timestamp
    end: pd.Timestamp
    time: TimeContext
    grid_days: Tuple[dt.date, ...] = ()

    @property
    def tz(self) -> str:
        return self.time.tz

    @property
    def calendar(self) -> Calendar:
        return self.time.calendar

    @property
    def date_policy(self) -> str:
        return self.time.date_policy

    def at(self, d: Any, *, time: Optional[dt.time] = None) -> pd.Timestamp:
        return self.time.at(d, time=time)

    def normalise(self, values: Iterable[Any]) -> List[pd.Timestamp]:
        return [t for t in self.time.normalise(values, self.grid_days) if self.start <= t <= self.end]

    def in_session(self, ts: pd.Timestamp) -> bool:
        return self.time.in_session(ts)
