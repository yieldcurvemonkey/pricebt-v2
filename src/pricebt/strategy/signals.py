"""Signals: market-data-like scalar sources (series, pricer lookups, measures, derived) plus the engine-fed `SignalStore`.

A signal is an immutable spec. The store owns run state: per-source ring buffers fed once per timeline point by the engine
(`observe`), so windowed statistics never look ahead and cost O(1) per step. `SeriesSource` and `StepTable` are history-backed:
they are read directly at any visible instant and need no ring.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from ..contracts.binding import TENOR
from ..errors import LookAheadError, MissingDataError, NotSupportedError, SignalError
from ..timeutil import DEFAULT_TZ


class MissingDataStrategy(Enum):
    fail = "fail"
    fill_forward = "fill_forward"
    interpolate = "interpolate"

    @classmethod
    def coerce(cls, x: Any) -> "MissingDataStrategy":
        if isinstance(x, cls):
            return x
        s = str(x).lower()
        return cls.fill_forward if s == "asof_ffill" else cls(s)


@dataclass(frozen=True)
class Observation:
    ts: pd.Timestamp  # visible-from instant (ring key)
    value: float
    obs_ts: pd.Timestamp  # stamp of the underlying observation
    exact: bool = True


@dataclass(frozen=True)
class SignalNeed:
    source: Any
    lookback: int = 0


class RingBuffer:
    """Fixed-capacity double-write ring of (key_ns, value). `window` returns a COPY, oldest first."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.cap = int(capacity)
        self._k = np.zeros(2 * self.cap, dtype=np.int64)
        self._v = np.zeros(2 * self.cap, dtype=np.float64)
        self._head = 0
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def append(self, key_ns: int, value: float) -> bool:
        if self._n:
            newest = int(self._k[(self._head - 1) % self.cap])
            if key_ns == newest:
                return False
            if key_ns < newest:
                raise SignalError(f"ring keys must increase: {key_ns} < {newest}")
        p = self._head % self.cap
        self._k[p] = self._k[p + self.cap] = key_ns
        self._v[p] = self._v[p + self.cap] = value
        self._head += 1
        self._n = min(self._n + 1, self.cap)
        return True

    def window(self, n: int, *, inclusive: bool = False) -> np.ndarray:
        """The last `n` values; exclusive (default) drops the newest first. Fewer are returned when history is short."""
        avail = self._n - (0 if inclusive else 1)
        k = max(0, min(n, avail))
        if k == 0:
            return np.empty(0)
        end = self._head - (0 if inclusive else 1)
        start = (end - k) % self.cap
        return np.array(self._v[start:start + k])

    def grown(self, capacity: int) -> "RingBuffer":
        new = RingBuffer(capacity)
        for i in range(self._n):
            j = (self._head - self._n + i) % self.cap
            new.append(int(self._k[j]), float(self._v[j]))
        return new


def zscore_of(x: float, prior: np.ndarray, *, mean_n: int, std_n: int, ddof: int = 1, rel_zero_std: float = 1e-12) -> float:
    """(x - mean(last mean_n)) / std(last std_n); a (numerically) zero std gives 0 when x == mean else +/-inf."""
    mean = float(np.mean(prior[-mean_n:]))
    std = float(np.std(prior[-std_n:], ddof=ddof))
    eps = rel_zero_std * max(abs(mean), 1.0)
    if not std > eps:
        return 0.0 if abs(x - mean) <= eps else math.copysign(math.inf, x - mean)
    return (x - mean) / std


class Signal:
    """Base class. Subclasses implement `observe(ts, ctx) -> Observation` and raise MissingDataError when nothing is visible."""

    name: Optional[str] = None
    history_backed: bool = False

    def observe(self, ts: pd.Timestamp, ctx: "SignalContext") -> Observation:
        raise NotImplementedError

    def parents(self) -> Tuple[Any, ...]:
        return ()

    def lookback(self) -> int:
        """Observations of each parent this signal reads BEFORE the current one."""
        return 0

    def __deepcopy__(self, memo: Any) -> "Signal":
        return self

    def __copy__(self) -> "Signal":
        return self


DataSource = Signal


def _is_date_only_key(k: Any, mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    if isinstance(k, dt.datetime):
        return False if k.tzinfo is not None else (k.hour, k.minute, k.second, k.microsecond) == (0, 0, 0, 0)
    if isinstance(k, dt.date):
        return True
    if isinstance(k, np.datetime64):
        return _is_date_only_key(pd.Timestamp(k).to_pydatetime(), mode)
    if isinstance(k, str):
        return ":" not in k and "T" not in k
    return False


def _localise(naive: pd.Timestamp, tz: str) -> pd.Timestamp:
    return naive.tz_localize(tz, ambiguous=True, nonexistent="shift_forward")


class SeriesSource(Signal):
    """Immutable, tz-aware, asof-looked-up series (gs GenericDataSource without its look-ahead and mutation defects).

    Date-only keys (dt.date, ISO date strings, naive midnight timestamps) become `date_time` (default 17:00 = the default session close)
    of that date in `tz`, so a daily close is NOT visible before the close. `lag` delays visibility. Strategies: `fail` (an observation on the
    SAME local date for date-only series, else the exact instant), `fill_forward` (newest visible; bounded by `max_staleness`).
    """

    history_backed = True

    def __init__(self, data_set: Union[pd.Series, Mapping[Any, float]], missing_data_strategy: Any = MissingDataStrategy.fail, *, name: Optional[str] = None,
                 tz: str = DEFAULT_TZ, date_time: dt.time = dt.time(17, 0), lag: Any = None, max_staleness: Any = None, date_only: str = "auto"):
        strat = MissingDataStrategy.coerce(missing_data_strategy)
        if strat is MissingDataStrategy.interpolate:
            raise NotSupportedError("interpolate needs the NEXT observation (look-ahead) and is not supported")
        s = pd.Series(data_set) if not isinstance(data_set, pd.Series) else data_set
        stamps, all_date_only = [], True
        for k in s.index:
            do = _is_date_only_key(k, date_only)
            all_date_only &= do
            ts = pd.Timestamp(k)
            if do:
                ts = _localise(pd.Timestamp(dt.datetime.combine(ts.date(), date_time)), tz)
            elif ts.tzinfo is None:
                ts = _localise(ts, tz)
            else:
                ts = ts.tz_convert(tz)
            stamps.append(ts)
        vals = np.asarray(s.to_numpy(dtype=float), dtype=float).copy()
        if np.isnan(vals).any():
            raise ValueError("SeriesSource data must not contain NaN")
        ns = np.array([t.value for t in stamps], dtype=np.int64)
        order = np.argsort(ns, kind="stable")
        ns, vals = ns[order], vals[order]
        if len(ns) > 1 and (np.diff(ns) == 0).any():
            raise ValueError("SeriesSource index has duplicate timestamps")
        self.name = name
        self.strategy = strat
        self.tz = tz
        self.date_only = bool(all_date_only) and len(ns) > 0
        self._lag_ns = int(pd.Timedelta(lag).value) if lag is not None else 0
        self._stale_ns = int(pd.Timedelta(max_staleness).value) if max_staleness is not None else None
        ns.flags.writeable = False
        vals.flags.writeable = False
        self._ns, self._vals = ns, vals
        self._vis = ns + self._lag_ns
        self._vis.flags.writeable = False

    @classmethod
    def from_file(cls, path: str, *, column: str, index: str = "date", **kw: Any) -> "SeriesSource":
        df = pd.read_csv(path, dtype={index: str})
        return cls(pd.Series(df[column].to_numpy(dtype=float), index=list(df[index])), **kw)

    def __len__(self) -> int:
        return len(self._ns)

    def _newest(self, ts: pd.Timestamp) -> int:
        return int(np.searchsorted(self._vis, pd.Timestamp(ts).value, side="right")) - 1

    def _local_date(self, ns: int) -> dt.date:
        return pd.Timestamp(ns, tz="UTC").tz_convert(self.tz).date()

    def _locate(self, ts: pd.Timestamp) -> int:
        j = self._newest(ts)
        if j < 0:
            raise MissingDataError(f"series {self.name!r}: no observation visible at {ts}")
        t = pd.Timestamp(ts)
        if self.strategy is MissingDataStrategy.fail:
            ok = self._local_date(int(self._ns[j])) == t.tz_convert(self.tz).date() if self.date_only else int(self._vis[j]) == t.value
            if not ok:
                raise MissingDataError(f"series {self.name!r}: no observation at {ts} (strategy fail)")
        elif self._stale_ns is not None and t.value - int(self._ns[j]) > self._stale_ns:
            raise MissingDataError(f"series {self.name!r}: newest observation older than max_staleness at {ts}")
        return j

    def observe(self, ts: pd.Timestamp, ctx: Any = None) -> Observation:
        j = self._locate(ts)
        t = pd.Timestamp(ts)
        exact = int(self._vis[j]) == t.value or (self.date_only and self._local_date(int(self._ns[j])) == t.tz_convert(self.tz).date())
        return Observation(pd.Timestamp(int(self._vis[j]), tz="UTC").tz_convert(self.tz), float(self._vals[j]), pd.Timestamp(int(self._ns[j]), tz="UTC").tz_convert(self.tz), bool(exact))

    def get(self, ts: pd.Timestamp) -> float:
        return float(self._vals[self._locate(ts)])

    def window_at(self, ts: pd.Timestamp, n: int, inclusive: bool = False) -> np.ndarray:
        """The `n` observations before the newest one visible at ts (that one included when inclusive). Returns a copy."""
        j = self._newest(ts)
        if j < 0:
            return np.empty(0)
        hi = j + 1 if inclusive else j
        return np.array(self._vals[max(0, hi - n):hi])


class StepTable(Signal):
    """Dated targets, right-continuous, effective FROM the key (a date key = local midnight of that date).

    `after_last='zero'`: 0 from the local date AFTER the last key (gs interpolate_signal); 'hold' keeps the last value. A strategy
    PARAMETER, not a market observation: no lag.
    """

    history_backed = True

    def __init__(self, mapping: Mapping[Any, float], *, name: Optional[str] = None, tz: str = DEFAULT_TZ, before_first: float = 0.0, after_last: str = "zero"):
        if after_last not in ("zero", "hold"):
            raise ValueError("after_last must be 'zero' or 'hold'")
        items = []
        for k, v in mapping.items():
            ts = pd.Timestamp(k)
            ts = _localise(ts.normalize(), tz) if _is_date_only_key(k, "auto") else (_localise(ts, tz) if ts.tzinfo is None else ts.tz_convert(tz))
            items.append((ts.value, float(v)))
        items.sort()
        self.name, self.tz, self.before_first, self.after_last = name, tz, float(before_first), after_last
        self._ns = np.array([i[0] for i in items], dtype=np.int64)
        self._vals = np.array([i[1] for i in items], dtype=float)
        self._last_date = pd.Timestamp(int(self._ns[-1]), tz="UTC").tz_convert(tz).date() if len(items) else None

    def value_at(self, ts: pd.Timestamp) -> float:
        t = pd.Timestamp(ts)
        j = int(np.searchsorted(self._ns, t.value, side="right")) - 1
        if j < 0:
            return self.before_first
        if self.after_last == "zero" and j == len(self._ns) - 1 and t.tz_convert(self.tz).date() > self._last_date:
            return 0.0
        return float(self._vals[j])

    def observe(self, ts: pd.Timestamp, ctx: Any = None) -> Observation:
        t = pd.Timestamp(ts)
        return Observation(t, self.value_at(t), t)

    def window_at(self, ts: pd.Timestamp, n: int, inclusive: bool = False) -> np.ndarray:
        raise NotSupportedError("StepTable has no observation history")


class ConstantSource(Signal):
    def __init__(self, value: float, *, name: Optional[str] = None):
        self.value = float(value)
        self.name = name

    def observe(self, ts: pd.Timestamp, ctx: Any = None) -> Observation:
        t = pd.Timestamp(ts)
        return Observation(t, self.value, t)


class PricerSignal(Signal):
    """`pricer.lookup(lookup, **kwargs) * scale` at the snapshot visible at ts. Keyed by the SNAPSHOT stamp (windows count snapshots)."""

    def __init__(self, lookup: str, kwargs: Optional[Mapping[str, Any]] = None, *, role: str = "primary", request: Optional[Mapping[str, Any]] = None,
                 scale: float = 1.0, max_staleness: Any = None, name: Optional[str] = None):
        self.lookup, self.kwargs, self.role, self.request = lookup, dict(kwargs or {}), role, dict(request) if request else None
        self.scale = float(scale)
        self.max_staleness = pd.Timedelta(max_staleness) if max_staleness is not None else None
        self.name = name

    def observe(self, ts: pd.Timestamp, ctx: "SignalContext") -> Observation:
        p = ctx.view.pricer(ts, self.role, self.request)
        pts = pd.Timestamp(p.ts)
        if self.max_staleness is not None and ts - pts > self.max_staleness:
            raise MissingDataError(f"pricer snapshot {pts} older than {self.max_staleness} at {ts}")
        newest = ctx.newest(self)
        if newest is not None and newest.ts == pts:
            return Observation(pts, newest.value, pts, pts == ts)
        v = float(p.lookup(self.lookup, **self.kwargs)) * self.scale
        if not math.isfinite(v):
            raise SignalError(f"pricer lookup {self.lookup!r} returned non-finite {v}")
        return Observation(pts, v, pts, pts == ts)

    @classmethod
    def spread(cls, lookup: str, long: Mapping[str, Any], short: Mapping[str, Any], *, scale: float = 1.0, name: Optional[str] = None, **kw: Any) -> "DerivedSignal":
        return DerivedSignal("combo", (cls(lookup, long, **kw), cls(lookup, short, **kw)), name=name, weights=(scale, -scale))


class MeasureSignal(Signal):
    """A standard measure of a freshly built template at each timeline point (e.g. the dv01 of a freshly built 10Y swap)."""

    def __init__(self, template: Any, measure: str, *, quantity: float = 1.0, name: Optional[str] = None):
        self.template, self.measure, self.quantity, self.name = template, measure, float(quantity), name

    def observe(self, ts: pd.Timestamp, ctx: "SignalContext") -> Observation:
        v = float(ctx.view.measure_of(self.template, self.measure, self.quantity))
        if not math.isfinite(v):
            raise SignalError(f"measure {self.measure!r} is non-finite")
        t = pd.Timestamp(ts)
        return Observation(t, v, t)


class BookMeasureSignal(Signal):
    """A standard measure of the BOOK: the quantity-weighted sum over the open positions in `scope` ('portfolio', 'action:<name>' or a selector), for example the
    book's dollar delta. It reads the book as it stands when the point begins (after scheduled exits and queued fills, before this point's orders), valued at THIS point's market; it
    only peeks: no mark is booked, so recording a signal changes nothing a strategy sees. `include_pending` adds orders submitted at this point."""

    def __init__(self, measure: str, scope: Any = "portfolio", *, include_pending: bool = False, name: Optional[str] = None):
        self.measure, self.scope, self.include_pending, self.name = measure, scope, bool(include_pending), name

    def observe(self, ts: pd.Timestamp, ctx: "SignalContext") -> Observation:
        v = float(ctx.view.measure(self.measure, self.scope, include_pending=self.include_pending))
        if not math.isfinite(v):
            raise SignalError(f"book measure {self.measure!r} is non-finite")
        t = pd.Timestamp(ts)
        return Observation(t, v, t)


class BookLadderSignal(Signal):
    """One bucket of a VECTOR measure of the BOOK (for example the 10Y bucket of `delta_ladder`): the quantity-weighted sum over the open positions in `scope`.
    An empty book is flat (0 in every bucket); a bucket the book's ladders do not have is a SignalError naming the ones they have. `missing='zero'` lets a
    position without the measure count as 0 instead of raising."""

    def __init__(self, measure: str, bucket: str, scope: Any = "portfolio", *, include_pending: bool = False, missing: str = "raise", name: Optional[str] = None):
        if not isinstance(bucket, str) or not TENOR.fullmatch(bucket):
            raise SignalError(f"bucket {bucket!r} is not a tenor (expected <int><D|W|M|Y>, e.g. '10Y')")
        self.measure, self.bucket, self.scope, self.include_pending, self.missing, self.name = measure, bucket, scope, bool(include_pending), missing, name

    def observe(self, ts: pd.Timestamp, ctx: "SignalContext") -> Observation:
        vec = ctx.view.measure_vector(self.measure, self.scope, include_pending=self.include_pending, missing=self.missing)
        if not vec:
            v = 0.0
        elif self.bucket in vec:
            v = float(vec[self.bucket])
        else:
            raise SignalError(f"the book's {self.measure!r} has no bucket {self.bucket!r}; it has {sorted(vec)}")
        if not math.isfinite(v):
            raise SignalError(f"book {self.measure!r} bucket {self.bucket!r} is non-finite")
        t = pd.Timestamp(ts)
        return Observation(t, v, t)


_WEIGHTS = {"spread": (1.0, -1.0), "fly": (-1.0, 2.0, -1.0)}
_ROLLING = {"rolling_mean": np.mean, "rolling_min": np.min, "rolling_max": np.max, "rolling_sum": np.sum}
_UNARY = {"abs": abs, "neg": lambda x: -x, "sign": lambda x: float(np.sign(x))}


class DerivedSignal(Signal):
    """Functions of other signals: combo|spread|fly|sum(weights), ratio, diff(lag), rolling_mean/std/min/max/sum(window), zscore(window),
    abs, neg, sign, clip(lo, hi), lag(k). Windowed ops read the parents' ring buffers (BEFORE the current observation); fewer than
    `min_periods` prior observations raise MissingDataError (warm-up)."""

    def __init__(self, op: str, inputs: Sequence[Any], *, name: Optional[str] = None, **params: Any):
        op = op.lower()
        known = {"combo", "spread", "fly", "sum", "ratio", "diff", "rolling_std", "zscore", "clip", "lag", *_ROLLING, *_UNARY}
        if op not in known:
            raise NotSupportedError(f"unknown derived op {op!r}; known: {sorted(known)}")
        self.op, self.inputs, self.params, self.name = op, tuple(inputs), dict(params), name
        if op in ("combo", "spread", "fly", "sum"):
            w = params.get("weights") or _WEIGHTS.get(op) or (1.0,) * len(self.inputs)
            if len(w) != len(self.inputs):
                raise ValueError(f"{op}: {len(w)} weights for {len(self.inputs)} inputs")
            self.params["weights"] = tuple(float(x) for x in w)
        if op.startswith("rolling") or op == "zscore":
            w = int(params["window"])
            if w < 2 and op in ("rolling_std", "zscore"):
                raise ValueError("window must be >= 2")
            self.params["window"] = w
            self.params["min_periods"] = int(params.get("min_periods", w))
            self.params["ddof"] = int(params.get("ddof", 1))

    def parents(self) -> Tuple[Any, ...]:
        return self.inputs

    def lookback(self) -> int:
        if "window" in self.params:
            return int(self.params["window"])
        return int(self.params.get("lag", 1)) if self.op in ("diff", "lag") else 0

    def observe(self, ts: pd.Timestamp, ctx: "SignalContext") -> Observation:
        obs = [ctx.observation(p) for p in self.inputs]
        xs = [o.value for o in obs]
        op, pr = self.op, self.params
        if op in ("combo", "spread", "fly", "sum"):
            v = float(sum(w * x for w, x in zip(pr["weights"], xs)))
        elif op == "ratio":
            if xs[1] == 0:
                raise SignalError("ratio: denominator is zero")
            v = xs[0] / xs[1]
        elif op in _UNARY:
            v = float(_UNARY[op](xs[0]))
        elif op == "clip":
            v = float(np.clip(xs[0], pr.get("lo", -np.inf), pr.get("hi", np.inf)))
        elif op in ("diff", "lag"):
            k = int(pr.get("lag", 1))
            prior = ctx.window(self.inputs[0], k)
            if len(prior) < k:
                raise MissingDataError(f"{op}: needs {k} prior observations, have {len(prior)}")
            v = xs[0] - prior[0] if op == "diff" else float(prior[0])
        else:
            w = pr["window"]
            prior = ctx.window(self.inputs[0], w)
            if len(prior) < pr["min_periods"]:
                raise MissingDataError(f"{op}: {len(prior)} of {pr['min_periods']} observations (warm-up)")
            if op == "zscore":
                v = zscore_of(xs[0], prior, mean_n=w, std_n=w, ddof=pr["ddof"])
            elif op == "rolling_std":
                v = float(np.std(prior, ddof=pr["ddof"]))
            else:
                v = float(_ROLLING[op](prior))
        if not (math.isfinite(v) or (op == "zscore" and math.isinf(v))):
            raise SignalError(f"derived {op} is non-finite")
        return Observation(max(o.ts for o in obs), v, max(o.obs_ts for o in obs), all(o.exact for o in obs))


class SignalContext:
    """What a signal may touch while observing at `ts`."""

    __slots__ = ("store", "view", "ts")

    def __init__(self, store: "SignalStore", view: Any, ts: pd.Timestamp):
        self.store, self.view, self.ts = store, view, ts

    def observation(self, p: Any) -> Observation:
        return self.store.observation(p, self.ts)

    def value(self, p: Any) -> float:
        return self.observation(p).value

    def window(self, p: Any, n: int, inclusive: bool = False) -> np.ndarray:
        return self.store.window(p, n, inclusive=inclusive, ts=self.ts)

    def newest(self, p: Any) -> Optional[Observation]:
        return self.store.newest(p)


class SignalStore:
    """Engine-fed signal store. One per backtest (pass as `Engine(signals=...)`)."""

    def __init__(self, sources: Sequence[Any] = (), *, slack: int = 16):
        self.slack = slack
        self._reg: Dict[int, Signal] = {}
        self._names: Dict[str, Signal] = {}
        self._look: Dict[int, int] = {}
        self._ring: Dict[int, RingBuffer] = {}
        self._cur: Dict[int, Observation] = {}
        self._miss: Dict[int, MissingDataError] = {}
        self._newest: Dict[int, Observation] = {}
        self._now: Optional[pd.Timestamp] = None
        self._view: Any = None
        for s in sources:
            self.register(s)

    @classmethod
    def for_strategy(cls, strategy: Any, extra: Sequence[Any] = ()) -> "SignalStore":
        store = cls()
        for need in strategy.required_signals():
            store.register(need.source, need.lookback)
        for s in extra:
            store.register(s)
        return store

    # ---- registration
    def resolve(self, s: Any) -> Signal:
        if isinstance(s, str):
            if s not in self._names:
                raise SignalError(f"unknown signal name {s!r}; registered: {sorted(self._names)}")
            return self._names[s]
        return s

    def register(self, source: Any, lookback: int = 0) -> Signal:
        source = self.resolve(source)
        nm = getattr(source, "name", None)
        if nm is not None:
            other = self._names.get(nm)
            if other is not None and other is not source:
                raise SignalError(f"signal name {nm!r} already registered to a different signal")
            self._names[nm] = source
        for p in source.parents():
            self.register(p, max(lookback, source.lookback()) + 1)
        k = id(source)
        if k not in self._reg:
            self._reg[k] = source
        self._look[k] = max(self._look.get(k, 0), int(lookback), source.lookback() if not source.parents() else 0)
        if not source.history_backed:
            need = max(1024, self._look[k] + self.slack)
            ring = self._ring.get(k)
            if ring is None:
                self._ring[k] = RingBuffer(need)
            elif ring.cap < need:
                self._ring[k] = ring.grown(need)
        return source

    # ---- engine hook
    def observe(self, ts: pd.Timestamp, view: Any) -> None:
        """Evaluate the eager signals at `ts`. `view` is the ONLY view any evaluation of a signal gets (this eager pass, a lazy `_ensure` from the strategy step, a recorded value): the
        engine hands it a PEEKING view, so no evaluation of a signal can book a mark (`view.cash` and every position are untouched), whichever call path reaches it."""
        ts = pd.Timestamp(ts)
        if self._now is not None and ts <= self._now:
            self._clear()
        self._now, self._view = ts, view
        self._cur.clear()
        self._miss.clear()
        for s in list(self._reg.values()):
            if not s.history_backed:
                self._eval(s, ts)

    def _clear(self) -> None:
        for k in list(self._ring):
            self._ring[k] = RingBuffer(self._ring[k].cap)
        self._cur.clear()
        self._miss.clear()
        self._newest.clear()
        self._now = None

    def _eval(self, s: Signal, ts: pd.Timestamp) -> None:
        k = id(s)
        try:
            obs = s.observe(ts, SignalContext(self, self._view, ts))
        except MissingDataError as e:
            self._miss[k] = e
            return
        if not math.isfinite(obs.value) and not (isinstance(s, DerivedSignal) and s.op == "zscore" and math.isinf(obs.value)):
            raise SignalError(f"signal {s.name or type(s).__name__} produced non-finite {obs.value}")
        self._cur[k] = obs
        self._newest[k] = obs
        if math.isfinite(obs.value):
            self._ring[k].append(obs.ts.value, obs.value)

    def _ensure(self, s: Signal) -> None:
        if id(s) not in self._reg:
            self.register(s)
        if not s.history_backed and id(s) not in self._cur and id(s) not in self._miss and self._now is not None and self._view is not None:
            for p in s.parents():
                self._ensure(self.resolve(p))
            self._eval(s, self._now)

    # ---- reads
    def newest(self, p: Any) -> Optional[Observation]:
        return self._newest.get(id(self.resolve(p)))

    def _check_ts(self, ts: Optional[pd.Timestamp]) -> pd.Timestamp:
        if self._now is None:
            raise SignalError("the signal store has not observed any timeline point yet")
        if ts is None:
            return self._now
        ts = pd.Timestamp(ts)
        if ts > self._now:
            raise LookAheadError(f"signal requested at {ts} but the clock is at {self._now}")
        return ts

    def observation(self, source: Any, ts: Optional[pd.Timestamp] = None) -> Observation:
        s = self.resolve(source)
        t = self._check_ts(ts)
        if s.history_backed:
            return s.observe(t, SignalContext(self, self._view, t))
        if t != self._now:
            raise SignalError("past values of a non-history-backed signal are served through window() only")
        self._ensure(s)
        k = id(s)
        if k in self._miss:
            raise self._miss[k]
        if k not in self._cur:
            raise MissingDataError(f"signal {s.name or type(s).__name__} has no observation at {t}")
        return self._cur[k]

    def value(self, source: Any, ts: Optional[pd.Timestamp] = None) -> float:
        return self.observation(source, ts).value

    def window(self, source: Any, n: int, *, inclusive: bool = False, ts: Optional[pd.Timestamp] = None) -> np.ndarray:
        s = self.resolve(source)
        t = self._check_ts(ts)
        if s.history_backed:
            return s.window_at(t, n, inclusive)
        if t != self._now:
            raise SignalError("windows of a non-history-backed signal are served at the current point only")
        self._ensure(s)
        ring = self._ring[id(s)]
        return ring.window(n, inclusive=inclusive or id(s) in self._miss)
