"""MarketData: the engine-facing facade over named MarketDataProviders with a memo and the look-ahead guard."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Hashable, List, Mapping, Optional, Tuple

import pandas as pd

from .errors import ConfigError, LookAheadError, MarketDataUnavailable
from .pricer import MarketDataProvider, Pricer
from .timeutil import Clock


@dataclass
class Binding:
    """A named market: which provider, the request template merged into every request, and an optional `wrap(pricer) -> pricer` that turns the
    provider's library-free snapshot pricer into an adapter's pricer (memoised per snapshot digest by MarketData)."""

    mdp: MarketDataProvider
    request: Mapping[str, Any] = field(default_factory=dict)
    wrap: Optional[Callable[[Pricer], Pricer]] = None


def freeze(obj: Any) -> Hashable:
    if isinstance(obj, Mapping):
        return tuple(sorted((str(k), freeze(v)) for k, v in obj.items()))
    if isinstance(obj, (list, tuple, set, frozenset)):
        return tuple(freeze(v) for v in obj)
    try:
        hash(obj)
        return obj
    except TypeError:
        return repr(obj)


class MarketData:
    """`pricer(ts, role)` returns the snapshot for a named binding.

    Guard (one place for every pricer look-ahead channel): ts must not exceed the clock; the returned snapshot's own timestamp must
    not exceed ts. Results are memoised per (role, ts, request) in a bounded LRU; `n_fetches` counts provider calls.
    """

    def __init__(self, bindings: Mapping[str, Any], clock: Optional[Clock] = None, *, maxsize: int = 32, guard: bool = True):
        self.bindings: Dict[str, Binding] = {}
        for role, b in bindings.items():
            self.bindings[role] = b if isinstance(b, Binding) else Binding(b)
        if "primary" not in self.bindings and len(self.bindings) == 1:
            self.bindings["primary"] = next(iter(self.bindings.values()))
        self.clock = clock or Clock()
        self.maxsize = maxsize
        self.guard = guard
        self._memo: "OrderedDict[Hashable, Pricer]" = OrderedDict()
        self._wrapped: "OrderedDict[Hashable, Tuple[Pricer, Pricer]]" = OrderedDict()  # (role, snapshot digest) -> (unwrapped kept alive, wrapped)
        self.n_fetches = 0
        self.n_hits = 0
        self.n_wraps = 0
        self.audit: Optional[List[Dict[str, Any]]] = None  # when a list: one row per distinct provider fetch (the tie-out's input record, spec D5)

    def pricer(self, ts: Optional[pd.Timestamp] = None, role: str = "primary", request: Optional[Mapping[str, Any]] = None) -> Pricer:
        if role not in self.bindings:
            raise ConfigError(f"unknown pricer role {role!r}; known: {sorted(self.bindings)}", code="ROLE")
        if ts is None:
            if self.clock.now is None:
                raise LookAheadError("no timestamp given and the clock has not started")
            ts = self.clock.now
        ts = pd.Timestamp(ts)
        if self.guard and self.clock.now is not None and ts > self.clock.now:
            raise LookAheadError(f"pricer requested for {ts} but the clock is at {self.clock.now}")
        b = self.bindings[role]
        req = {**dict(b.request), **dict(request or {})}
        key = (role, ts.value, freeze(req))
        hit = self._memo.get(key)
        if hit is not None:
            self._memo.move_to_end(key)
            self.n_hits += 1
            return hit
        p = b.mdp.get_pricer(ts, req)
        self.n_fetches += 1
        if p is None:
            raise MarketDataUnavailable(ts, req, f"provider {type(b.mdp).__name__} returned None")
        if self.audit is not None:  # BEFORE the adapter's wrap: the digest is the snapshot's, the same whichever library consumes it
            self.audit.append({"ts": ts, "role": role, "digest": getattr(p, "digest", None), "stamp": getattr(p, "ts", None)})
        if self.guard:
            pts = getattr(p, "ts", None)
            if pts is not None and pd.Timestamp(pts) > ts:
                raise LookAheadError(f"provider returned a snapshot stamped {pts} for a request at {ts}")
        if b.wrap is not None:
            p = self._wrap(role, b.wrap, p)
        self._memo[key] = p
        while len(self._memo) > self.maxsize:
            self._memo.popitem(last=False)
        return p

    def _wrap(self, role: str, wrap: Callable[[Pricer], Pricer], p: Pricer) -> Pricer:
        """Apply the role's adapter wrapper ONCE per snapshot: the memo key is the snapshot digest (or the object identity for a pricer without one)."""
        pts = getattr(p, "ts", None)  # the wrapped pricer carries its own stamp: two requests for one snapshot at different times must not share it
        wkey = (role, getattr(p, "digest", None) or id(p), None if pts is None else pd.Timestamp(pts).value)
        hit = self._wrapped.get(wkey)
        if hit is not None:
            self._wrapped.move_to_end(wkey)
            return hit[1]
        wrapped = wrap(p)
        self.n_wraps += 1
        self._wrapped[wkey] = (p, wrapped)
        while len(self._wrapped) > self.maxsize:
            self._wrapped.popitem(last=False)
        return wrapped

    def reset(self) -> None:
        """Forget everything a run left behind: the memo, the wrapped pricers and the counters (a second run of the same engine reports its own)."""
        self._memo.clear()
        self._wrapped.clear()
        self.n_fetches = self.n_hits = self.n_wraps = 0
        self.audit = None

    def evict_before(self, ts: pd.Timestamp) -> None:
        v = pd.Timestamp(ts).value
        for k in [k for k in self._memo if k[1] < v]:
            del self._memo[k]

    def available_timestamps(self, start: pd.Timestamp, end: pd.Timestamp, role: str = "primary") -> List[pd.Timestamp]:
        fn = getattr(self.bindings[role].mdp, "available_timestamps", None)
        if not callable(fn):
            raise NotImplementedError(f"{type(self.bindings[role].mdp).__name__} cannot list its timestamps")
        return list(fn(start, end))
