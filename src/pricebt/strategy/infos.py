"""TriggerInfo and the per-action info objects (the trigger -> action side channel).

Keys are strings: an action NAME, an action KIND ('add_trade', 'hedge', ...), the wildcard '*', or 'entry' (any entry action).
gs_quant class keys (`info[AddTradeAction]`) are normalised to the class's kind.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple


from ..errors import TriggerError

ANY_ACTION = "*"
ENTRY = "entry"
ENTRY_KINDS = frozenset({"add_trade", "add_scaled_trade", "add_weighted_trade", "early_exit_scaled", "hedge", "rebalance", "custom"})


class _NoSchedule:
    """Sentinel: the trigger supplies no schedule (distinct from None = 'last scheduled instant, hold to the end')."""

    def __repr__(self) -> str:
        return "NO_SCHEDULE"

    def __deepcopy__(self, memo: Any) -> "_NoSchedule":
        return self

    def __copy__(self) -> "_NoSchedule":
        return self

    def __reduce__(self) -> str:
        return "NO_SCHEDULE"


NO_SCHEDULE = _NoSchedule()


@dataclass(frozen=True)
class ActionInfo:
    reason: str = field(default="", kw_only=True)
    data: Mapping[str, Any] = field(default_factory=dict, kw_only=True)
    skip: bool = field(default=False, kw_only=True)


@dataclass(frozen=True)
class ScheduleInfo(ActionInfo):
    next_schedule: Any = NO_SCHEDULE


@dataclass(frozen=True)
class AddTradeActionInfo(ActionInfo):
    scaling: Optional[float] = None
    next_schedule: Any = NO_SCHEDULE


@dataclass(frozen=True)
class AddScaledTradeActionInfo(ActionInfo):
    next_schedule: Any = NO_SCHEDULE
    scaling: Optional[float] = None


@dataclass(frozen=True)
class AddWeightedTradeActionInfo(ActionInfo):
    next_schedule: Any = NO_SCHEDULE
    scaling: Optional[float] = None


@dataclass(frozen=True)
class HedgeActionInfo(ActionInfo):
    next_schedule: Any = NO_SCHEDULE
    target: Optional[float] = None


@dataclass(frozen=True)
class ExitTradeActionInfo(ActionInfo):
    not_applicable: Any = None
    position_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RebalanceActionInfo(ActionInfo):
    not_applicable: Any = None
    target: Optional[float] = None


@dataclass(frozen=True)
class SubmitOrdersInfo(ActionInfo):
    orders: Tuple[Any, ...] = ()


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def key_of(k: Any) -> str:
    """Normalise a class / instance / string key to its string form (class -> kind)."""
    if isinstance(k, str):
        return k
    cls = k if isinstance(k, type) else type(k)
    kind = getattr(cls, "kind", None)
    if isinstance(kind, str):
        return kind
    n = cls.__name__
    return snake(n[:-6] if n.endswith("Action") else n)


class InfoMap(dict):
    """dict[str, ActionInfo] whose lookups also accept gs-style class keys."""

    def __getitem__(self, k: Any) -> ActionInfo:
        return super().__getitem__(key_of(k))

    def get(self, k: Any, default: Any = None) -> Any:
        return super().get(key_of(k), default)

    def __contains__(self, k: Any) -> bool:
        return super().__contains__(key_of(k))


class TriggerInfo:
    """`TriggerInfo(triggered, info)`. `strict=False` marks built-in emissions whose keys may legitimately match no action."""

    def __init__(self, triggered: bool, info: Optional[Mapping[Any, ActionInfo]] = None, *, info_dict: Optional[Mapping[Any, ActionInfo]] = None, strict: bool = True):
        if info is not None and info_dict is not None:
            raise TriggerError("give either info or info_dict, not both")
        raw = info if info is not None else info_dict
        self.triggered = bool(triggered)
        self.strict = strict
        self.info: InfoMap = InfoMap()
        for k, v in (raw or {}).items():
            if not isinstance(v, ActionInfo):
                raise TriggerError(f"info value for {k!r} must be an ActionInfo, got {type(v).__name__}")
            dict.__setitem__(self.info, key_of(k), v)

    @property
    def info_dict(self) -> InfoMap:
        return self.info

    def __bool__(self) -> bool:
        return self.triggered

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, TriggerInfo):
            return self.triggered == other.triggered and dict(self.info) == dict(other.info)
        if isinstance(other, bool):
            return self.triggered == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.triggered)

    def __repr__(self) -> str:
        return f"TriggerInfo({self.triggered}, {dict(self.info)})"


def _defaults(cls: type) -> Dict[str, Any]:
    return {f.name: (f.default if f.default is not dataclasses.MISSING else None) for f in dataclasses.fields(cls)}


def overlay(base: ActionInfo, top: ActionInfo) -> ActionInfo:
    """Fields of `top` at their defaults are filled from `base` (same-named, non-default). skip/reason/data are never inherited."""
    if base is top:
        return top
    dflt = _defaults(type(top))
    upd = {}
    for f in dataclasses.fields(top):
        if f.name in ("skip", "reason", "data"):
            continue
        cur = getattr(top, f.name)
        if (cur is dflt[f.name] or cur == dflt[f.name]) and hasattr(base, f.name):
            bv = getattr(base, f.name)
            bd = _defaults(type(base)).get(f.name)
            if not (bv is bd or bv == bd):
                upd[f.name] = bv
    return dataclasses.replace(top, **upd) if upd else top


def merge_infos(early: Mapping[str, ActionInfo], late: Mapping[str, ActionInfo]) -> Dict[str, ActionInfo]:
    """Shared keys merge field-wise (non-default `late` fields win, data merges, reasons join, skip = AND); distinct keys are unioned."""
    out: Dict[str, ActionInfo] = dict(early)
    for k, b in late.items():
        a = out.get(k)
        if a is None:
            out[k] = b
            continue
        cls = type(a) if len(dataclasses.fields(a)) > len(dataclasses.fields(b)) else type(b)
        dflt = _defaults(cls)
        vals: Dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            if f.name in ("skip", "reason", "data"):
                continue
            v = dflt[f.name]
            for src in (a, b):
                if hasattr(src, f.name):
                    sv = getattr(src, f.name)
                    if not (sv is _defaults(type(src)).get(f.name) or sv == _defaults(type(src)).get(f.name)):
                        v = sv
            vals[f.name] = v
        out[k] = cls(**vals, reason="; ".join(x for x in (a.reason, b.reason) if x), data={**dict(a.data), **dict(b.data)}, skip=a.skip and b.skip)
    return out


def match_info(info: Mapping[str, ActionInfo], name: Optional[str], kind: str, parent_kinds: Tuple[str, ...] = ()) -> Optional[ActionInfo]:
    """The info an action receives: most specific match (name, kind, parent kinds, 'entry' for entry kinds), defaults overlaid from '*'."""
    wild = info.get(ANY_ACTION)
    specific: Optional[ActionInfo] = None
    if name is not None and name in info:
        specific = info[name]
    else:
        for k in (kind, *parent_kinds):
            if k in info:
                specific = info[k]
                break
        else:
            if ENTRY in info and (kind in ENTRY_KINDS or any(p in ENTRY_KINDS for p in parent_kinds)):
                specific = info[ENTRY]
    if specific is None:
        return wild
    return overlay(wild, specific) if wild is not None else specific
