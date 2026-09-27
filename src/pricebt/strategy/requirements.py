"""Trigger requirements: ALL trigger logic (gs `*TriggerRequirements`), positional field order as in gs_quant.

`bind(ctx)` runs once per run (schedules, validation); `has_triggered(ts, view)` is called EXACTLY ONCE per timeline point;
`reset()` clears run state so a second run is identical. Requirements are dataclasses (structural equality, deepcopy-safe).
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from ..errors import StrategyError, TriggerError, MissingDataError
from ..orders import OpenOrder, PositionSelector
from ..registry import resolve_dotted
from ..timeutil import Calendar, TimelineContext, to_date
from .durations import periodic_schedule, resolve_exit
from .infos import (AddTradeActionInfo, ExitTradeActionInfo, HedgeActionInfo, RebalanceActionInfo, ScheduleInfo, TriggerInfo, merge_infos)
from .signals import SignalNeed, zscore_of

log = logging.getLogger(__name__)
_WEEKENDS = Calendar.weekends_only()


class TriggerDirection(Enum):
    ABOVE = 1
    BELOW = 2
    EQUAL = 3
    ANY = 4

    @classmethod
    def coerce(cls, x: Any) -> Optional["TriggerDirection"]:
        if x is None or isinstance(x, cls):
            return x
        try:
            return cls[str(x).upper()]
        except KeyError as e:
            raise TriggerError(f"unknown direction {x!r}") from e


class AggType(Enum):
    ALL_OF = 1
    ANY_OF = 2

    @classmethod
    def coerce(cls, x: Any) -> "AggType":
        return x if isinstance(x, cls) else cls[str(x).upper()]


class CalcType(Enum):
    simple = "simple"
    semi_path_dependent = "semi_path_dependent"
    path_dependent = "path_dependent"


_ORDER = {CalcType.simple: 0, CalcType.semi_path_dependent: 1, CalcType.path_dependent: 2}


def check_barrier(x: float, level: float, direction: TriggerDirection, tol: float = 0.0) -> bool:
    """ABOVE: x > level; BELOW: x < level; EQUAL: |x - level| <= tol. A non-finite x raises (a silent False hides bad data)."""
    if not math.isfinite(x):
        raise TriggerError(f"non-finite value {x} compared against barrier {level}")
    if direction is TriggerDirection.ABOVE:
        return x > level
    if direction is TriggerDirection.BELOW:
        return x < level
    if direction is TriggerDirection.EQUAL:
        return abs(x - level) <= tol
    raise TriggerError(f"direction {direction} is not valid for a barrier check")


def _instants(values: Sequence[Any], ctx: TimelineContext, time: Optional[dt.time]) -> List[pd.Timestamp]:
    out = []
    for v in values:
        if isinstance(v, (pd.Timestamp, dt.datetime)):
            out.append(ctx.time.localize(v))
        else:
            out.append(ctx.at(to_date(v), time=time))
    return sorted(set(out))


class TriggerRequirements:
    """Base. Subclasses are dataclasses that set `kind`."""

    kind: ClassVar[str] = ""

    @property
    def calc_type(self) -> CalcType:
        return CalcType.simple

    @property
    def stateful(self) -> bool:
        return False

    def bind(self, ctx: TimelineContext) -> None:
        return None

    def get_trigger_times(self, ctx: Optional[TimelineContext] = None) -> Sequence[Any]:
        return ()

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        raise NotImplementedError

    def reset(self) -> None:
        return None

    def requires_measures(self) -> Tuple[str, ...]:
        return ()

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return ()

    def supplies_schedule(self) -> bool:
        return False


class _Scheduled(TriggerRequirements):
    """Shared schedule machinery: `_times` (sorted instants) and `_next` (value -> next instant or None)."""

    def _set_times(self, times: Sequence[pd.Timestamp]) -> None:
        ts = sorted(set(times))
        self._times = tuple(ts)
        self._next = {t.value: (ts[i + 1] if i + 1 < len(ts) else None) for i, t in enumerate(ts)}

    def _need_bound(self) -> None:
        if not hasattr(self, "_next"):
            raise TriggerError(f"{type(self).__name__} used before bind(ctx)")

    def get_trigger_times(self, ctx: Optional[TimelineContext] = None) -> Sequence[pd.Timestamp]:
        self._need_bound()
        return self._times

    def has_triggered(self, ts: pd.Timestamp, view: Any = None) -> TriggerInfo:
        self._need_bound()
        v = pd.Timestamp(ts).value
        if v in self._next:
            return TriggerInfo(True, {"*": ScheduleInfo(next_schedule=self._next[v])})
        return TriggerInfo(False)

    def supplies_schedule(self) -> bool:
        return True


@dataclass
class PeriodicTriggerRequirements(_Scheduled):
    """[start] + multiples of `frequency` from start, on the backtest calendar (+ `calendar` extra holidays). None start/end = the backtest window."""

    kind: ClassVar[str] = "periodic"
    start_date: Any = None
    end_date: Any = None
    frequency: Optional[str] = None
    calendar: Optional[Sequence[Any]] = None
    time: Optional[dt.time] = field(default=None, kw_only=True)
    adjust_start: str = field(default="following", kw_only=True)

    def __post_init__(self) -> None:
        if self.frequency is None:
            raise StrategyError("PeriodicTriggerRequirements needs a frequency")
        if self.adjust_start not in ("following", "preceding", "none"):
            raise StrategyError(f"adjust_start must be following|preceding|none, got {self.adjust_start!r}")
        periodic_schedule(dt.date(2000, 1, 3), self.frequency, dt.date(2000, 1, 3), _WEEKENDS)  # validates the token now

    def bind(self, ctx: TimelineContext) -> None:
        cal = ctx.calendar.extended(self.calendar) if self.calendar else ctx.calendar
        start = to_date(self.start_date) if self.start_date is not None else ctx.start.tz_convert(ctx.tz).date()
        end = to_date(self.end_date) if self.end_date is not None else ctx.end.tz_convert(ctx.tz).date()
        b0 = start if self.adjust_start == "none" else (cal.following(start) if self.adjust_start == "following" else cal.preceding(start))
        self._set_times([ctx.at(d, time=self.time) for d in periodic_schedule(b0, self.frequency, end, cal)])


_FREQ = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|min|h|hr)?\s*$", re.I)


def _freq_seconds(f: Any) -> int:
    if isinstance(f, (int, float)):
        secs = float(f) * 60.0
    else:
        m = _FREQ.match(str(f))
        if not m:
            raise StrategyError(f"bad intraday frequency {f!r}")
        unit = (m.group(2) or "min").lower()
        secs = float(m.group(1)) * {"s": 1, "sec": 1, "min": 60, "h": 3600, "hr": 3600}[unit]
    n = round(secs)
    if n <= 0 or abs(secs - n) > 1e-6:
        raise StrategyError(f"intraday frequency must be a positive whole number of seconds, got {f!r}")
    return n


@dataclass
class IntradayTriggerRequirements(_Scheduled):
    """Time-of-day offsets range(start, end+1, f) seconds on every grid day; finite by construction; DST gaps skipped, ambiguous = first."""

    kind: ClassVar[str] = "intraday_periodic"
    start_time: Optional[dt.time] = None
    end_time: Optional[dt.time] = None
    frequency: Any = None
    tz: Optional[str] = field(default=None, kw_only=True)
    wrap_midnight: bool = field(default=False, kw_only=True)
    days: Optional[Sequence[dt.date]] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.frequency is None:
            raise StrategyError("IntradayTriggerRequirements needs a frequency")
        self._f = _freq_seconds(self.frequency)

    def bind(self, ctx: TimelineContext) -> None:
        tz = self.tz or ctx.tz
        sec = lambda t: t.hour * 3600 + t.minute * 60 + t.second
        s = sec(self.start_time or ctx.time.session_open)
        e = sec(self.end_time or ctx.time.session_close)
        if e < s:
            if not self.wrap_midnight:
                raise StrategyError("end_time before start_time needs wrap_midnight=True")
            e += 86400
        times = []
        for d in (self.days if self.days is not None else ctx.grid_days):
            for off in range(s, e + 1, self._f):
                day = d + dt.timedelta(days=off // 86400)
                r = off % 86400
                naive = pd.Timestamp(dt.datetime.combine(day, dt.time(r // 3600, (r % 3600) // 60, r % 60)))
                t = naive.tz_localize(tz, ambiguous=True, nonexistent="NaT")
                if not pd.isna(t):
                    times.append(t.tz_convert(ctx.tz))
        self._set_times(times)


@dataclass
class DateTriggerRequirements(_Scheduled):
    """Fires at the given instants, or (`entire_day`) at every timeline point on those local dates (`fire='first'`: only the first of each)."""

    kind: ClassVar[str] = "date"
    dates: Optional[Sequence[Any]] = None
    entire_day: bool = False
    fire: str = field(default="all", kw_only=True)
    time: Optional[dt.time] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.fire not in ("all", "first"):
            raise StrategyError("fire must be 'all' or 'first'")
        self._fired: Set[dt.date] = set()
        if not self.dates:
            log.warning("DateTriggerRequirements has no dates and will never fire")

    @property
    def stateful(self) -> bool:
        return self.entire_day and self.fire == "first"

    def bind(self, ctx: TimelineContext) -> None:
        self._set_times(_instants(list(self.dates or ()), ctx, self.time))
        self._tz = ctx.tz
        self._days = frozenset(t.tz_convert(ctx.tz).date() for t in self._times)
        self._fired = set()

    def reset(self) -> None:
        self._fired = set()

    def has_triggered(self, ts: pd.Timestamp, view: Any = None) -> TriggerInfo:
        self._need_bound()
        t = pd.Timestamp(ts)
        if not self.entire_day:
            return super().has_triggered(t, view)
        d = t.tz_convert(self._tz).date()
        if d not in self._days:
            return TriggerInfo(False)
        if self.fire == "first":
            if d in self._fired:
                return TriggerInfo(False)
            self._fired.add(d)
        nxt = next((x for x in self._times if x > t), None)
        return TriggerInfo(True, {"*": ScheduleInfo(next_schedule=nxt)})


def _read_signal(view: Any, source: Any, on_missing: str) -> Optional[float]:
    try:
        return float(view.signal(source))
    except MissingDataError:
        if on_missing == "skip":
            return None
        raise


def _sig_need(source: Any, lookback: int = 0) -> Tuple[SignalNeed, ...]:
    return (SignalNeed(source, lookback),) if source is not None and not isinstance(source, str) else ()


@dataclass
class MktTriggerRequirements(TriggerRequirements):
    """LEVEL-triggered: fires at every timeline point where the signal satisfies the barrier."""

    kind: ClassVar[str] = "mkt"
    data_source: Any = None
    trigger_level: Optional[float] = None
    direction: Any = None
    tol: float = field(default=0.0, kw_only=True)
    on_missing: str = field(default="raise", kw_only=True)

    def __post_init__(self) -> None:
        self.direction = TriggerDirection.coerce(self.direction)
        if self.direction is None or self.data_source is None or self.trigger_level is None:
            raise TriggerError("MktTrigger needs data_source, trigger_level and direction")
        if self.direction is TriggerDirection.ANY:
            raise TriggerError("direction ANY is only valid for CrossingTrigger")

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return _sig_need(self.data_source)

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        x = _read_signal(view, self.data_source, self.on_missing)
        if x is None:
            return TriggerInfo(False)
        return TriggerInfo(check_barrier(x, self.trigger_level, self.direction, self.tol))


@dataclass
class CrossingTriggerRequirements(TriggerRequirements):
    """EDGE-triggered barrier crossing with hysteresis (Schmitt trigger for ANY)."""

    kind: ClassVar[str] = "crossing"
    data_source: Any = None
    trigger_level: Optional[float] = None
    direction: Any = TriggerDirection.ABOVE
    hysteresis: float = field(default=0.0, kw_only=True)
    fire_on_first: bool = field(default=False, kw_only=True)
    on_missing: str = field(default="raise", kw_only=True)

    def __post_init__(self) -> None:
        self.direction = TriggerDirection.coerce(self.direction)
        if self.direction is TriggerDirection.EQUAL or self.direction is None:
            raise TriggerError("CrossingTrigger direction must be ABOVE, BELOW or ANY")
        if self.hysteresis < 0:
            raise TriggerError("hysteresis must be >= 0")
        if self.data_source is None or self.trigger_level is None:
            raise TriggerError("CrossingTrigger needs data_source and trigger_level")
        self._armed: Optional[bool] = None
        self._side: Optional[int] = None

    @property
    def stateful(self) -> bool:
        return True

    def reset(self) -> None:
        self._armed = None
        self._side = None

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return _sig_need(self.data_source)

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        x = _read_signal(view, self.data_source, self.on_missing)
        if x is None:
            return TriggerInfo(False)
        if not math.isfinite(x):
            raise TriggerError(f"non-finite signal value {x}")
        lvl, h, d = self.trigger_level, self.hysteresis, self.direction
        if d is TriggerDirection.ANY:
            side = 1 if x > lvl + h else (-1 if x < lvl - h else self._side)
            first = self._side is None
            fire = side is not None and (self._side is not None and side != self._side or (first and self.fire_on_first))
            self._side = side
            return TriggerInfo(bool(fire))
        above = d is TriggerDirection.ABOVE
        breached = x > lvl if above else x < lvl
        rearmed = x <= lvl - h if above else x >= lvl + h
        if self._armed is None:
            self._armed = not breached
            if self.fire_on_first and breached:
                return TriggerInfo(True)
            return TriggerInfo(False)
        if not self._armed and rearmed:
            self._armed = True
        if self._armed and breached:
            self._armed = False
            return TriggerInfo(True)
        return TriggerInfo(False)


_BUILTIN_SOURCES = ("equity", "drawdown", "pnl")


def read_risk(view: Any, risk: str, scope: Any, include_pending: bool) -> float:
    """A risk source: measure name | 'layer.<name>' | 'pnl' | 'equity' | 'drawdown' (needs the caller's peak)."""
    if risk == "equity":
        return float(view.equity)
    if risk == "pnl":
        return float(sum(p.pnl for p in view.positions(scope)))
    if risk.startswith(("layer.", "value.")):
        return float(view.layer_pnl(risk.split(".", 1)[1], scope))
    return float(view.measure(risk, scope, include_pending=include_pending))


def _transform(fn: Any) -> Callable[[float], float]:
    if fn is None:
        return lambda x: x
    if fn == "abs":
        return abs
    if fn == "neg":
        return lambda x: -x
    if callable(fn):
        return fn
    raise TriggerError(f"unknown risk_transformation {fn!r}")


@dataclass
class StrategyRiskTriggerRequirements(TriggerRequirements):
    """Barrier on a portfolio risk source, read after this point's earlier orders (include_pending)."""

    kind: ClassVar[str] = "strategy_risk"
    risk: Optional[str] = None
    trigger_level: Optional[float] = None
    direction: Any = None
    risk_transformation: Any = None
    scope: Any = field(default="portfolio", kw_only=True)
    when_flat: str = field(default="skip", kw_only=True)
    include_pending: bool = field(default=True, kw_only=True)
    check_every: Optional[int] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        self.direction = TriggerDirection.coerce(self.direction)
        if self.risk is None or self.trigger_level is None or self.direction is None:
            raise TriggerError("StrategyRiskTrigger needs risk, trigger_level and direction")
        if self.direction is TriggerDirection.ANY:
            raise TriggerError("direction ANY is only valid for CrossingTrigger")
        self._peak: Optional[float] = None
        self._n = 0

    @property
    def calc_type(self) -> CalcType:
        return CalcType.path_dependent

    @property
    def stateful(self) -> bool:
        return self.risk == "drawdown" or self.check_every is not None

    def reset(self) -> None:
        self._peak, self._n = None, 0

    def requires_measures(self) -> Tuple[str, ...]:
        return () if self.risk in _BUILTIN_SOURCES or self.risk.startswith(("layer.", "value.")) else (self.risk,)

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        self._n += 1
        if self.risk == "drawdown":
            self._peak = view.equity if self._peak is None else max(self._peak, view.equity)
        if self.check_every and (self._n - 1) % self.check_every != 0:
            return TriggerInfo(False)
        if self.when_flat == "skip" and view.n_positions(self.scope, include_pending=self.include_pending) == 0 and self.risk not in ("equity", "drawdown"):
            return TriggerInfo(False)
        x = float(self._peak - view.equity) if self.risk == "drawdown" else read_risk(view, self.risk, self.scope, self.include_pending)
        return TriggerInfo(check_barrier(_transform(self.risk_transformation)(x), self.trigger_level, self.direction))


RiskTriggerRequirements = StrategyRiskTriggerRequirements


@dataclass
class PortfolioTriggerRequirements(TriggerRequirements):
    """Barrier on a portfolio statistic: 'len'/'positions' (open + pending), 'firings', 'gross:<measure>', 'net:<measure>'."""

    kind: ClassVar[str] = "portfolio"
    data_source: str = "len"
    trigger_level: Optional[float] = None
    direction: Any = None
    scope: Any = field(default="portfolio", kw_only=True)
    tol: float = field(default=0.0, kw_only=True)
    include_pending: bool = field(default=True, kw_only=True)

    def __post_init__(self) -> None:
        self.direction = TriggerDirection.coerce(self.direction)
        if self.trigger_level is None or self.direction is None:
            raise TriggerError("PortfolioTrigger needs trigger_level and direction")

    @property
    def calc_type(self) -> CalcType:
        return CalcType.path_dependent

    def requires_measures(self) -> Tuple[str, ...]:
        ds = str(self.data_source)
        return (ds.split(":", 1)[1],) if ds.startswith(("gross:", "net:")) else ()

    def _statistic(self, view: Any) -> float:
        ds = str(self.data_source)
        if ds in ("len", "positions"):
            return float(view.n_positions(self.scope, include_pending=self.include_pending))
        if ds == "firings":
            return float(_count_firings(view, self.scope, self.include_pending))
        kind, _, m = ds.partition(":")
        if kind == "net":
            return float(view.measure(m, self.scope, include_pending=self.include_pending))
        if kind == "gross":
            return float(sum(abs(view.measure(m, PositionSelector(ids=(p.id,)))) for p in view.positions(self.scope)))
        raise TriggerError(f"unknown portfolio statistic {ds!r}")

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        return TriggerInfo(check_barrier(self._statistic(view), self.trigger_level, self.direction, self.tol))


def _count_firings(view: Any, scope: Any, include_pending: bool) -> int:
    ids = {p.meta.extra.get("firing", p.id) for p in view.positions(scope)}
    if include_pending:
        for o in view.pending_orders:
            if isinstance(o, OpenOrder):
                ids.add(o.meta.extra.get("firing", id(o)))
    return len(ids)


@dataclass
class TradeCountTriggerRequirements(TriggerRequirements):
    """Number of positions (or firings) held, incl. this point's pending orders (gs 040314 gradual-entry cap)."""

    kind: ClassVar[str] = "trade_count"
    trade_count: Optional[float] = None
    direction: Any = None
    count: str = field(default="positions", kw_only=True)
    scope: Any = field(default="portfolio", kw_only=True)
    include_pending: bool = field(default=True, kw_only=True)

    def __post_init__(self) -> None:
        self.direction = TriggerDirection.coerce(self.direction)
        if self.trade_count is None or self.direction is None:
            raise TriggerError("TradeCountTrigger needs trade_count and direction")
        if self.count not in ("positions", "firings"):
            raise TriggerError("count must be 'positions' or 'firings'")

    @property
    def calc_type(self) -> CalcType:
        return CalcType.path_dependent

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        n = _count_firings(view, self.scope, self.include_pending) if self.count == "firings" else view.n_positions(self.scope, include_pending=self.include_pending)
        return TriggerInfo(check_barrier(float(n), float(self.trade_count), self.direction))


def _reqs(items: Sequence[Any]) -> Tuple[TriggerRequirements, ...]:
    out = []
    for c in items:
        req = getattr(c, "trigger_requirements", c)
        if req is not c and getattr(c, "actions", ()):
            log.warning("actions of a trigger nested in an aggregate/not are ignored: %s", [a.name for a in c.actions])
        if not isinstance(req, TriggerRequirements):
            raise StrategyError(f"aggregate child must be trigger requirements, got {type(c).__name__}")
        out.append(req)
    return tuple(out)


@dataclass
class AggregateTriggerRequirements(TriggerRequirements):
    """ALL_OF / ANY_OF over children. EVERY child is evaluated at every point (no short-circuit, so stateful children see every point)."""

    kind: ClassVar[str] = "aggregate"
    triggers: Sequence[Any] = ()
    aggregate_type: Any = AggType.ALL_OF

    def __post_init__(self) -> None:
        self.triggers = _reqs(self.triggers)
        self.aggregate_type = AggType.coerce(self.aggregate_type)
        if not self.triggers:
            raise StrategyError("AggregateTrigger needs at least one child")

    @property
    def calc_type(self) -> CalcType:
        return max((c.calc_type for c in self.triggers), key=_ORDER.get)

    @property
    def stateful(self) -> bool:
        return any(c.stateful for c in self.triggers)

    def bind(self, ctx: TimelineContext) -> None:
        for c in self.triggers:
            c.bind(ctx)

    def reset(self) -> None:
        for c in self.triggers:
            c.reset()

    def get_trigger_times(self, ctx: Optional[TimelineContext] = None) -> Sequence[Any]:
        return [t for c in self.triggers for t in c.get_trigger_times(ctx)]

    def requires_measures(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(m for c in self.triggers for m in c.requires_measures()))

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return tuple(n for c in self.triggers for n in c.requires_signals())

    def supplies_schedule(self) -> bool:
        return any(c.supplies_schedule() for c in self.triggers)

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        results = [c.has_triggered(ts, view) for c in self.triggers]
        fired = [r for r in results if r.triggered]
        ok = len(fired) == len(results) if self.aggregate_type is AggType.ALL_OF else bool(fired)
        if not ok:
            return TriggerInfo(False)
        info: Dict[str, Any] = {}
        strict = True
        for r in fired:
            info = merge_infos(info, r.info)
            strict &= r.strict
        return TriggerInfo(True, info, strict=strict)


@dataclass
class NotTriggerRequirements(TriggerRequirements):
    kind: ClassVar[str] = "not"
    trigger: Any = None

    def __post_init__(self) -> None:
        self.trigger = _reqs([self.trigger])[0]

    @property
    def calc_type(self) -> CalcType:
        return self.trigger.calc_type

    @property
    def stateful(self) -> bool:
        return self.trigger.stateful

    def bind(self, ctx: TimelineContext) -> None:
        self.trigger.bind(ctx)

    def reset(self) -> None:
        self.trigger.reset()

    def get_trigger_times(self, ctx: Optional[TimelineContext] = None) -> Sequence[Any]:
        return self.trigger.get_trigger_times(ctx)

    def requires_measures(self) -> Tuple[str, ...]:
        return self.trigger.requires_measures()

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return self.trigger.requires_signals()

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        return TriggerInfo(not self.trigger.has_triggered(ts, view).triggered)


@dataclass
class MeanReversionTriggerRequirements(TriggerRequirements):
    """z-score of the current observation against the PRIOR window; entry when |z| > bound (short above the mean, long below).

    The exit is an offsetting trade (`exit_mode='offset'`, gs) or a close of matching positions (`'close'`). Warm-up: no decision and no
    state change until `min_periods` prior observations exist."""

    kind: ClassVar[str] = "mean_reversion"
    data_source: Any = None
    z_score_bound: Optional[float] = None
    rolling_mean_window: Optional[int] = None
    rolling_std_window: Optional[int] = None
    min_periods: Optional[int] = field(default=None, kw_only=True)
    ddof: int = field(default=1, kw_only=True)
    exit_mode: str = field(default="offset", kw_only=True)
    exit_z: Optional[float] = field(default=None, kw_only=True)
    allow_short: bool = field(default=True, kw_only=True)

    def __post_init__(self) -> None:
        if None in (self.data_source, self.z_score_bound, self.rolling_mean_window, self.rolling_std_window):
            raise TriggerError("MeanReversionTrigger needs data_source, z_score_bound and both windows")
        if self.rolling_mean_window < 2 or self.rolling_std_window < 2:
            raise TriggerError("rolling windows must be >= 2")
        if self.exit_mode not in ("offset", "close"):
            raise TriggerError("exit_mode must be 'offset' or 'close'")
        self.current_position = 0

    @property
    def position(self) -> int:
        return self.current_position

    @property
    def stateful(self) -> bool:
        return True

    def reset(self) -> None:
        self.current_position = 0

    def _lookback(self) -> int:
        return max(self.rolling_mean_window, self.rolling_std_window)

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return _sig_need(self.data_source, self._lookback())

    def _info(self, scaling: Optional[float], reason: str, *, exit_: bool = False) -> TriggerInfo:
        if self.exit_mode == "close":
            ex = ExitTradeActionInfo(reason=reason, skip=not exit_)
            add = AddTradeActionInfo(scaling=scaling, reason=reason, skip=exit_)
            return TriggerInfo(True, {"add_trade": add, "entry": add, "exit": ex}, strict=False)
        add = AddTradeActionInfo(scaling=scaling, reason=reason)
        return TriggerInfo(True, {"add_trade": add, "entry": add}, strict=False)

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        x = float(view.signal(self.data_source))
        prior = view.signal_window(self.data_source, self._lookback())
        if len(prior) < (self.min_periods or self._lookback()):
            return TriggerInfo(False)
        mean = float(np.mean(prior[-self.rolling_mean_window:]))
        z = zscore_of(x, prior, mean_n=self.rolling_mean_window, std_n=self.rolling_std_window, ddof=self.ddof)
        pos = self.current_position
        if pos == 1 and (x > mean if self.exit_z is None else abs(z) < self.exit_z):
            self.current_position = 0
            return self._info(-1.0, f"exit long z={z:.3f}", exit_=True)
        if pos == -1 and (x < mean if self.exit_z is None else abs(z) < self.exit_z):
            self.current_position = 0
            return self._info(1.0, f"exit short z={z:.3f}", exit_=True)
        if pos == 0 and abs(z) > self.z_score_bound:
            side = -1 if x > mean else 1
            if side == -1 and not self.allow_short:
                return TriggerInfo(False)
            self.current_position = side
            return self._info(float(side), f"enter z={z:.3f}")
        return TriggerInfo(False)


class EventCalendar:
    """User-supplied dated events: {name: [dates]}; immutable."""

    def __init__(self, events: Mapping[str, Sequence[Any]]):
        self._e = {k: tuple(sorted(to_date(d) for d in v)) for k, v in events.items()}

    def dates(self, event_name: str, **filters: Any) -> Tuple[dt.date, ...]:
        if event_name not in self._e:
            raise TriggerError(f"unknown event {event_name!r}; known: {sorted(self._e)}")
        return self._e[event_name]


@dataclass
class EventTriggerRequirements(_Scheduled):
    """Fires on the dates of a named event (+ offset_days, rolled onto the backtest calendar). Needs `events` or `data_source`."""

    kind: ClassVar[str] = "event"
    event_name: Optional[str] = None
    offset_days: int = 0
    country: Optional[str] = None
    currency: Optional[str] = None
    source: Optional[str] = None
    start: Any = None
    end: Any = None
    data_source: Any = None
    events: Any = field(default=None, kw_only=True)
    time: Optional[dt.time] = field(default=None, kw_only=True)
    roll: str = field(default="none", kw_only=True)

    def __post_init__(self) -> None:
        if (self.events is None) == (self.data_source is None):
            raise StrategyError("EventTrigger needs exactly one of events / data_source")
        if self.event_name is None:
            raise StrategyError("EventTrigger needs an event_name")
        if self.roll not in ("none", "following", "preceding"):
            raise StrategyError("roll must be none|following|preceding")
        if isinstance(self.events, Mapping):
            self.events = EventCalendar(self.events)

    def bind(self, ctx: TimelineContext) -> None:
        src = self.events if self.events is not None else self.data_source
        lo = to_date(self.start) if self.start is not None else ctx.start.date()
        hi = to_date(self.end) if self.end is not None else ctx.end.date()
        raw = src.dates(self.event_name, country=self.country, currency=self.currency, source=self.source)
        days = []
        for d in raw:
            d = to_date(d)
            if not lo <= d <= hi:
                continue
            d = d + dt.timedelta(days=self.offset_days)
            days.append(ctx.calendar.adjust(d, "unadjusted" if self.roll == "none" else self.roll))
        self._set_times([ctx.at(d, time=self.time) for d in days])


@dataclass
class CustomTriggerRequirements(TriggerRequirements):
    """`target(ts, view, **kwargs) -> bool | TriggerInfo`; target is a callable or an allow-listed 'pkg.mod:fn'."""

    kind: ClassVar[str] = "custom"
    target: Any = None
    kwargs: Optional[Mapping[str, Any]] = None
    times: Sequence[Any] = field(default=(), kw_only=True)
    calc: Any = field(default="simple", kw_only=True)
    is_stateful: bool = field(default=False, kw_only=True)
    needs_measures: Sequence[str] = field(default=(), kw_only=True)
    needs_signals: Sequence[Any] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        if self.target is None:
            raise TriggerError("CustomTrigger needs a target")
        self._fn = resolve_dotted(self.target) if isinstance(self.target, str) else self.target
        self.calc = self.calc if isinstance(self.calc, CalcType) else CalcType(self.calc)

    @property
    def calc_type(self) -> CalcType:
        return self.calc

    @property
    def stateful(self) -> bool:
        return self.is_stateful

    def get_trigger_times(self, ctx: Optional[TimelineContext] = None) -> Sequence[Any]:
        return list(self.times)

    def requires_measures(self) -> Tuple[str, ...]:
        return tuple(self.needs_measures)

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return tuple(n if isinstance(n, SignalNeed) else SignalNeed(n) for n in self.needs_signals)

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        out = self._fn(ts, view, **dict(self.kwargs or {}))
        return out if isinstance(out, TriggerInfo) else TriggerInfo(bool(out))


@dataclass
class RiskBandTriggerRequirements(TriggerRequirements):
    """Fires when a standard measure leaves a band [target-w, target+w] (or absolute (lo, hi)); emits hedge/rebalance info with the level to size to.

    `rearm` = Schmitt latch: after firing, stay disarmed until |x - target| <= rearm. `cooldown` = minimum spacing (int = timeline points,
    str/timedelta = duration). The two gates are independent. The emitted info carries the trigger's scope: hedge/rebalance actions size over it."""

    kind: ClassVar[str] = "risk_band"
    measure: str = "dv01"
    band: Any = None
    target: Optional[float] = None
    scope: Any = "portfolio"
    rearm: Optional[float] = field(default=None, kw_only=True)
    cooldown: Any = field(default=None, kw_only=True)
    hedge_to: str = field(default="target", kw_only=True)
    emit: str = field(default="hedge", kw_only=True)
    transform: Any = field(default=None, kw_only=True)
    include_pending: bool = field(default=True, kw_only=True)
    when_flat: str = field(default="skip", kw_only=True)
    check_every: Optional[int] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.band is None:
            raise TriggerError("RiskBandTrigger needs a band")
        if isinstance(self.band, Mapping):
            self.band = (self.band["lo"], self.band["hi"]) if "lo" in self.band else float(self.band["abs"])
        if isinstance(self.band, (tuple, list)):
            lo, hi = float(self.band[0]), float(self.band[1])
            tgt = 0.5 * (lo + hi) if self.target is None else float(self.target)
        else:
            tgt = 0.0 if self.target is None else float(self.target)
            lo, hi = tgt - abs(float(self.band)), tgt + abs(float(self.band))
        if not lo <= hi:
            raise TriggerError("band lower bound exceeds upper bound")
        self._lo, self._hi, self._tgt = lo, hi, tgt
        if self.hedge_to not in ("target", "edge"):
            raise TriggerError("hedge_to must be 'target' or 'edge'")
        if self.transform is not None and self.hedge_to != "target":
            raise TriggerError("a transform requires hedge_to='target'")
        if self.emit not in ("hedge", "rebalance", "*"):
            raise TriggerError("emit must be 'hedge', 'rebalance' or '*'")
        if self.rearm is not None and not 0 <= self.rearm <= (hi - lo) / 2:
            raise TriggerError("rearm must lie within [0, half band width]")
        if isinstance(self.cooldown, (int, float)) and not isinstance(self.cooldown, bool) and self.cooldown < 0:
            raise TriggerError("cooldown must be >= 0")
        self._ctx: Optional[TimelineContext] = None
        self.reset()

    @property
    def calc_type(self) -> CalcType:
        return CalcType.path_dependent

    @property
    def stateful(self) -> bool:
        return True

    def bind(self, ctx: TimelineContext) -> None:
        self._ctx = ctx

    def reset(self) -> None:
        self._armed = True
        self._until: Any = None
        self._n = 0

    def requires_measures(self) -> Tuple[str, ...]:
        return () if self.measure in _BUILTIN_SOURCES or self.measure.startswith(("layer.", "value.")) else (self.measure,)

    def _cooling(self, ts: pd.Timestamp) -> bool:
        if self._until is None:
            return False
        return self._n < self._until if isinstance(self._until, int) else ts < self._until

    def _start_cooldown(self, ts: pd.Timestamp) -> None:
        c = self.cooldown
        if c is None:
            return
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            self._until = self._n + int(c)
            return
        if self._ctx is None:
            raise TriggerError("a duration cooldown needs bind(ctx)")
        end = resolve_exit(c, entry_ts=ts, ctx=self._ctx)
        self._until = pd.Timestamp.max.tz_localize("UTC") if end is None else end

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        self._n += 1
        if self.check_every and (self._n - 1) % self.check_every != 0:
            return TriggerInfo(False)
        if self.when_flat == "skip" and view.n_positions(self.scope, include_pending=self.include_pending) == 0:
            return TriggerInfo(False)
        x = _transform(self.transform)(read_risk(view, self.measure, self.scope, self.include_pending))
        if not math.isfinite(x):
            raise TriggerError(f"non-finite {self.measure}: {x}")
        if self.rearm is not None and not self._armed and abs(x - self._tgt) <= self.rearm:
            self._armed = True
        breach = x < self._lo or x > self._hi
        if not (breach and self._armed and not self._cooling(ts)):
            return TriggerInfo(False)
        self._start_cooldown(ts)
        if self.rearm is not None:
            self._armed = False
        level = self._tgt if self.hedge_to == "target" else (self._hi if x > self._hi else self._lo)
        why = f"{self.measure}={x:.6g} outside [{self._lo:.6g}, {self._hi:.6g}]"
        data = {"measure": self.measure, "scope": self.scope, "value": x, "band": (self._lo, self._hi)}
        if self.emit == "rebalance":
            info: Any = {"rebalance": RebalanceActionInfo(target=level, reason=why, data=data)}
        else:
            info = {("*" if self.emit == "*" else "hedge"): HedgeActionInfo(target=level, reason=why, data=data)}
        return TriggerInfo(True, info, strict=False)


@dataclass
class RebalanceTriggerRequirements(RiskBandTriggerRequirements):
    kind: ClassVar[str] = "rebalance_trigger"
    emit: str = field(default="rebalance", kw_only=True)
