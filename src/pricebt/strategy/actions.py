"""Actions: `apply(ts, view, info) -> [Instruction]`. Actions are pure (they read the view and return orders; the runner submits).

Sizing is delegated to `sizing`; exits address positions by structured selectors (tags/templates/actions/ids), never by parsing names.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from ..contracts.evaluate import is_vector, vector_scale
from ..contracts.schema import is_term_ref
from ..contracts.spec import TradeTemplate
from ..costs import CostContext
from ..errors import NotSupportedError, SizingError, StrategyError
from ..orders import CloseOrder, Instruction, OpenOrder, PositionMeta, PositionSelector, ResizeOrder
from ..registry import resolve_dotted
from ..timeutil import TimelineContext, to_date
from ..types import Phase
from .durations import FOREVER, Duration, PastExitError, needs_terms, resolve_exit
from .signals import Signal, SignalNeed, StepTable
from .sizing import ScalingActionType, hedge_quantity, l1, ladder_hedge_quantities, nav_scale, rebalance_delta, risk_scale, weighted_split

log = logging.getLogger(__name__)
EPS = 1e-12


class _Inherit:
    def __repr__(self) -> str:
        return "INHERIT"

    def __deepcopy__(self, memo: Any) -> "_Inherit":
        return self


INHERIT = _Inherit()
_KEEP = object()


@dataclass(frozen=True)
class Leg:
    template: Any
    weight: float = 1.0
    name: Optional[str] = None


@dataclass(frozen=True)
class Bundle:
    name: str
    legs: Tuple[Leg, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "legs", tuple(as_leg(x) for x in self.legs))


def as_leg(x: Any) -> Leg:
    if isinstance(x, Leg):
        return x
    if isinstance(x, TradeTemplate):
        return Leg(x, 1.0, x.name)
    if isinstance(x, str):
        raise StrategyError(f"template name {x!r} must be resolved to a TradeTemplate before building the strategy (config loader's job)")
    raise StrategyError(f"an action trades a TradeTemplate (an instrument spec plus terms), got {type(x).__name__}: wrap a ready-made object with TradeTemplate.wrap(...)")


def resolve_template_refs(template: TradeTemplate, view: Any, info: Any) -> TradeTemplate:
    """Resolve the `@signal.<n>` and `@trigger.scaling` references in an action's terms at the firing time (T3). A numeric signal in the DIRECTION term
    (`side`) picks the side by its sign (positive: the primary side); a zero signal means no trade. `@param.<n>` is substituted when the config loads."""
    if not template.has_refs:
        return template
    schema = template.spec.schema

    def one(v: Any) -> Any:
        if is_term_ref(v):
            root, _, name = v[1:].partition(".")
            if root == "signal":
                return float(view.signal(name))
            if root == "trigger":
                if name != "scaling":
                    raise StrategyError(f"unknown trigger reference {v!r}: only @trigger.scaling exists")
                sc = getattr(info, "scaling", None)
                if sc is None:
                    raise StrategyError(f"{v!r} needs the trigger to supply a scaling in its info, and it supplied none")
                return float(sc)
            raise StrategyError(f"reference {v!r} is unresolved at firing time (@param.<n> is substituted when the config loads)")
        if isinstance(v, dict):
            return {k: one(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [one(x) for x in v]
        return v

    changes = {}
    for k, v in template.ref_terms.items():  # the terms that hold a REFERENCE as given (an escaped literal such as `@@x` is not one)
        r = one(v)
        if schema.direction is not None and k == schema.direction.term and isinstance(r, float):
            if r == 0.0:
                raise StrategyError(f"the signal driving term {k!r} is 0: no direction, no trade")
            r = next(val for val, sign in schema.direction.sign.items() if sign == (1 if r > 0 else -1))
        changes[k] = r
    return template.with_terms(**changes)


def as_bundle(x: Any) -> Optional[Bundle]:
    if x is None or isinstance(x, Bundle):
        return x
    if isinstance(x, (list, tuple)):
        return Bundle("bundle", tuple(as_leg(i) for i in x))
    leg = as_leg(x)
    return Bundle(leg.name or "bundle", (leg,))


@dataclass
class Action:
    """Base. `name`/indices are assigned by Strategy; the TimelineContext is bound at run start."""

    kind: ClassVar[str] = "action"
    info_kinds: ClassVar[Tuple[str, ...]] = ()
    default_phase: ClassVar[Phase] = Phase.ADD
    meta_kind: ClassVar[str] = "add"

    tags: Tuple[str, ...] = field(default=(), kw_only=True)
    phase: Optional[Phase] = field(default=None, kw_only=True)
    trigger_idx: int = field(default=-1, init=False, repr=False, compare=False)
    action_idx: int = field(default=-1, init=False, repr=False, compare=False)
    _ctx: Optional[TimelineContext] = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.tags = tuple(self.tags)
        if self.phase is not None:
            self.phase = Phase(self.phase)
            if self.phase is Phase.INITIAL:
                raise StrategyError("phase INITIAL is reserved for the initial portfolio")

    @property
    def effective_phase(self) -> Phase:
        return self.phase if self.phase is not None else self.default_phase

    def bind(self, *, name: str, trigger_idx: int, action_idx: int) -> None:
        if getattr(self, "name", None) is None:
            self.name = name
        self.trigger_idx, self.action_idx = trigger_idx, action_idx

    def bind_ctx(self, ctx: TimelineContext) -> None:
        self._ctx = ctx

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        raise NotImplementedError

    def requires_measures(self) -> Tuple[str, ...]:
        return ()

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return ()

    def _complete(self, order: Instruction) -> Instruction:
        """Fill the position metadata of a user-built OpenOrder that left it blank."""
        if isinstance(order, OpenOrder) and not order.meta.action:
            m = dataclasses.replace(order.meta, action=self.name, kind=order.meta.kind if order.meta.kind != "add" else self.meta_kind, trigger_idx=self.trigger_idx, action_idx=self.action_idx)
            return dataclasses.replace(order, meta=m)
        return order


@dataclass
class _EntryAction(Action):
    """Shared machinery of actions that open positions."""

    dated_priceables: Optional[Mapping[Any, Any]] = field(default=None, kw_only=True)
    pricer_request: Optional[Mapping[str, Any]] = field(default=None, kw_only=True)
    on_past_exit: str = field(default="raise", kw_only=True)

    def __post_init__(self) -> None:
        Action.__post_init__(self)
        if self.on_past_exit not in ("raise", "skip"):
            raise StrategyError("on_past_exit must be 'raise' or 'skip'")
        self.priceables = as_bundle(self.priceables)
        if self.dated_priceables:
            self.dated_priceables = {(pd.Timestamp(k) if isinstance(k, (pd.Timestamp, dt.datetime)) else to_date(k)): as_bundle(v) for k, v in self.dated_priceables.items()}
        self._cal_cache: Any = None

    def bind_ctx(self, ctx: TimelineContext) -> None:
        super().bind_ctx(ctx)
        hc = getattr(self, "holiday_calendar", None)
        self._cal_cache = ctx.calendar.extended(hc) if hc else None

    def _bundle_at(self, ts: pd.Timestamp) -> Bundle:
        if self.dated_priceables:
            if ts in self.dated_priceables:
                return self.dated_priceables[ts]
            d = ts.tz_convert(self._ctx.tz).date() if self._ctx is not None else ts.date()
            if d in self.dated_priceables:
                return self.dated_priceables[d]
        if self.priceables is None:
            raise StrategyError(f"action {self.name!r} has no priceables")
        return self.priceables

    def _cap_final(self, ts: pd.Timestamp, final: Optional[pd.Timestamp]) -> Tuple[Optional[pd.Timestamp], Optional[str]]:
        return final, None

    def _stamp(self, ts: pd.Timestamp) -> str:
        if self._ctx is not None and ts == self._ctx.at(ts.tz_convert(self._ctx.tz).date()):
            return ts.strftime("%Y-%m-%d")
        return ts.strftime("%Y-%m-%dT%H%M%S")

    def _orders(self, ts: pd.Timestamp, view: Any, info: Any, quantities: Sequence[float], *, bundle: Optional[Bundle] = None, meta_kind: Optional[str] = None,
                group: Optional[str] = None, duration: Any = _KEEP, parent: Optional[str] = None) -> List[Instruction]:
        bundle = bundle or self._bundle_at(ts)
        duration = self.trade_duration if duration is _KEEP else duration
        firing = f"{ts.value}:{self.trigger_idx}:{self.action_idx}"
        out: List[Instruction] = []
        for leg, qty in zip(bundle.legs, quantities):
            lname = leg.name or getattr(leg.template, "name", "leg")
            if abs(qty) < EPS:
                view.event("order_skipped", action=self.name, leg=lname, reason="zero_quantity")
                continue
            tpl = resolve_template_refs(leg.template, view, info)
            built = view.build(tpl, request=self.pricer_request) if needs_terms(duration) else None
            try:
                final = resolve_exit(duration, entry_ts=ts, terms=built.terms if built is not None else None, info=info, ctx=self._ctx, calendar=self._cal_cache)
            except PastExitError:
                if self.on_past_exit == "skip":
                    view.event("order_skipped", action=self.name, leg=lname, reason="past_exit")
                    continue
                raise
            final, exit_reason = self._cap_final(ts, final)
            extra: Dict[str, Any] = {"firing": firing, "action_kind": self.kind, "name": f"{self.name}_{lname}_{self._stamp(ts)}"}
            if info is not None and getattr(info, "reason", ""):
                extra["reason"] = info.reason
            if exit_reason:
                extra["exit_reason"] = exit_reason
            meta = PositionMeta(action=self.name, kind=meta_kind or self.meta_kind, trigger_idx=self.trigger_idx, action_idx=self.action_idx, template=lname, tags=self.tags, group=group, parent=parent, extra=extra)
            out.append(OpenOrder(template=tpl, quantity=float(qty), final_ts=final, tags=self.tags, meta=meta, cost_entry=self.transaction_cost, cost_exit=self.transaction_cost_exit))
        return out

    def _unit(self, view: Any, bundle: Bundle, measure: str, info: Any = None) -> float:
        return float(sum(l.weight * view.measure_of(resolve_template_refs(l.template, view, info), measure) for l in bundle.legs))


@dataclass
class AddTradeAction(_EntryAction):
    kind: ClassVar[str] = "add_trade"
    priceables: Any = None
    trade_duration: Duration = None
    name: Optional[str] = None
    transaction_cost: Any = None
    transaction_cost_exit: Any = None
    holiday_calendar: Optional[Sequence[Any]] = None
    quantity: float = field(default=1.0, kw_only=True)

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        if info is not None and info.skip:
            return []
        sc = getattr(info, "scaling", None)
        q = self.quantity * (1.0 if sc is None else float(sc))
        b = self._bundle_at(ts)
        return self._orders(ts, view, info, [q * l.weight for l in b.legs], bundle=b)


def _level(lv: Any, ts: pd.Timestamp, view: Any) -> float:
    if isinstance(lv, StepTable):
        return lv.value_at(ts)
    if isinstance(lv, (Signal, str)):
        return float(view.signal(lv))
    return float(lv)


@dataclass
class AddScaledTradeAction(_EntryAction):
    """size: q = quantity*L(ts); risk_measure: q = L/M(risk) (signed); NAV: the scale spending exactly the pot L0 + action_cash (costs included)."""

    kind: ClassVar[str] = "add_scaled_trade"
    priceables: Any = None
    trade_duration: Duration = None
    name: Optional[str] = None
    scaling_type: Any = ScalingActionType.size
    scaling_risk: Optional[str] = None
    scaling_level: Any = 1
    transaction_cost: Any = None
    transaction_cost_exit: Any = None
    holiday_calendar: Optional[Sequence[Any]] = None
    quantity: float = field(default=1.0, kw_only=True)
    after_last: str = field(default="zero", kw_only=True)
    on_zero_price: str = field(default="raise", kw_only=True)

    def __post_init__(self) -> None:
        _EntryAction.__post_init__(self)
        self.scaling_type = ScalingActionType.coerce(self.scaling_type)
        if isinstance(self.scaling_level, Mapping):
            self.scaling_level = StepTable(self.scaling_level, after_last=self.after_last)
        if self.scaling_type is ScalingActionType.risk_measure and not self.scaling_risk:
            raise StrategyError("risk_measure scaling needs scaling_risk")
        if self.scaling_type is ScalingActionType.NAV and not isinstance(self.scaling_level, (int, float)):
            raise StrategyError("NAV scaling needs a scalar scaling_level")

    def requires_measures(self) -> Tuple[str, ...]:
        return (self.scaling_risk,) if self.scaling_type is ScalingActionType.risk_measure else ()

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        lv = self.scaling_level
        return (SignalNeed(lv),) if isinstance(lv, Signal) else ()

    def _nav_scale(self, ts: pd.Timestamp, view: Any, b: Bundle, info: Any = None) -> float:
        pot = max(float(self.scaling_level) + view.action_cash(self.name), 0.0)
        price = self._unit(view, b, "pv", info)
        tpls = [resolve_template_refs(l.template, view, info) for l in b.legs]
        memo: Dict[Tuple[int, str], float] = {}

        def unit_measure(i: int, n: str) -> float:
            if (i, n) not in memo:
                memo[(i, n)] = float(view.measure_of(tpls[i], n))
            return memo[(i, n)]

        def cost_at(s: float) -> float:
            if self.transaction_cost is None:
                return 0.0
            return float(sum(self.transaction_cost.cost(CostContext(ts, s * l.weight, lambda n, i=i: unit_measure(i, n))) for i, l in enumerate(b.legs)))

        try:
            return nav_scale(pot, price, cost_at)
        except SizingError:
            if self.on_zero_price == "skip":
                view.event("order_skipped", action=self.name, reason="zero_price")
                return 0.0
            raise

    def scale(self, ts: pd.Timestamp, view: Any, b: Bundle, info: Any = None) -> float:
        st = self.scaling_type
        if st is ScalingActionType.size:
            return self.quantity * _level(self.scaling_level, ts, view)
        if st is ScalingActionType.risk_measure:
            return risk_scale(_level(self.scaling_level, ts, view), self._unit(view, b, self.scaling_risk, info))
        return self._nav_scale(ts, view, b, info)

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        if info is not None and info.skip:
            return []
        b = self._bundle_at(ts)
        q = self.scale(ts, view, b, info)
        sc = getattr(info, "scaling", None)
        if sc is not None:
            q *= float(sc)
        if abs(q) < EPS:
            return []
        if not self._room(view, b):
            return []
        return self._orders(ts, view, info, [q * l.weight for l in b.legs], bundle=b, meta_kind="scaled")

    def _room(self, view: Any, b: Bundle) -> bool:
        return True


@dataclass
class AddWeightedTradeAction(_EntryAction):
    """Splits `total_size` of quantity across the legs by risk (gs: |risk_i| / sum |risk|)."""

    kind: ClassVar[str] = "add_weighted_trade"
    priceables: Any = None
    trade_duration: Duration = None
    name: Optional[str] = None
    scaling_risk: Optional[str] = None
    total_size: float = 100000.0
    transaction_cost: Any = None
    transaction_cost_exit: Any = None
    holiday_calendar: Optional[Sequence[Any]] = None
    weighting: str = field(default="risk_proportional", kw_only=True)

    def __post_init__(self) -> None:
        _EntryAction.__post_init__(self)
        if not self.scaling_risk:
            raise StrategyError("AddWeightedTradeAction needs scaling_risk")

    def requires_measures(self) -> Tuple[str, ...]:
        return (self.scaling_risk,)

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        if info is not None and info.skip:
            return []
        b = self._bundle_at(ts)
        if any(l.weight != 1.0 for l in b.legs):
            raise StrategyError("AddWeightedTradeAction legs must have weight 1")
        qs = weighted_split(self.total_size, [view.measure_of(resolve_template_refs(l.template, view, info), self.scaling_risk) for l in b.legs], weighting=self.weighting)
        sc = getattr(info, "scaling", None)
        if sc is not None:
            qs = tuple(q * float(sc) for q in qs)
        return self._orders(ts, view, info, qs, bundle=b, meta_kind="scaled")


@dataclass
class EarlyExitPositionLimitScaledAction(AddScaledTradeAction):
    """AddScaled with a cap on concurrent positions (the whole firing is dropped when it would exceed it) and early-exit instants."""

    kind: ClassVar[str] = "early_exit_scaled"
    info_kinds: ClassVar[Tuple[str, ...]] = ("add_scaled_trade",)
    early_exits: Optional[Sequence[Any]] = field(default=None, kw_only=True)
    max_concurrent_pos: Optional[int] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        AddScaledTradeAction.__post_init__(self)
        self._early: Tuple[Any, ...] = ()

    def bind_ctx(self, ctx: TimelineContext) -> None:
        super().bind_ctx(ctx)
        self._early = tuple(sorted(ctx.normalise(self.early_exits or ())))

    def _cap_final(self, ts: pd.Timestamp, final: Optional[pd.Timestamp]) -> Tuple[Optional[pd.Timestamp], Optional[str]]:
        nxt = next((e for e in self._early if e > ts), None)
        if nxt is not None and (final is None or nxt < final):
            return nxt, "early_exit"
        return final, None

    def _room(self, view: Any, b: Bundle) -> bool:
        if self.max_concurrent_pos is None:
            return True
        n = len(view.positions(PositionSelector(actions=(self.name,))))
        n += sum(1 for o in view.pending_orders if isinstance(o, OpenOrder) and o.meta.action == self.name)
        if n + len(b.legs) > self.max_concurrent_pos:
            view.event("order_skipped", action=self.name, reason="position_limit", open=n)
            return False
        return True


@dataclass
class HedgeAction(_EntryAction):
    """Size a hedge so that `net + q*U = target` (100%), q = -(net - target)/U * pct/100, with net over the (info/own) scope incl. pending orders.

    mode 'add': static, held for its trade_duration. mode 'resize': one live position per hedge (re-hedged to the residual each firing).

    LADDER hedge: when `risk` is a vector measure (its schema return type is a mapping, e.g. `delta_ladder`; `vector=True` states it explicitly, a NAME decides nothing) the bundle's legs are the hedge instruments and
    ONE quantity per leg is solved by weighted least squares so the book's bucketed ladder is flattened (`ridge`, per-bucket `bucket_weights`); `target` may then be
    a bucket -> level mapping. mode 'resize' keeps one live position per leg."""

    kind: ClassVar[str] = "hedge"
    default_phase: ClassVar[Phase] = Phase.HEDGE
    meta_kind: ClassVar[str] = "hedge"
    risk: Optional[str] = None
    priceables: Any = None
    trade_duration: Duration = None
    name: Optional[str] = None
    csa_term: Optional[str] = None
    scaling_parameter: str = "notional_amount"
    transaction_cost: Any = None
    transaction_cost_exit: Any = None
    risk_transformation: Any = None
    holiday_calendar: Optional[Sequence[Any]] = None
    risk_percentage: float = 100.0
    mode: str = field(default="add", kw_only=True)
    target: float = field(default=0.0, kw_only=True)
    scope: Any = field(default=None, kw_only=True)
    min_trade_risk: float = field(default=0.0, kw_only=True)
    on_zero_hedge_risk: str = field(default="raise", kw_only=True)
    vector: Optional[bool] = field(default=None, kw_only=True)
    ridge: float = field(default=0.0, kw_only=True)
    bucket_weights: Optional[Mapping[str, float]] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        _EntryAction.__post_init__(self)
        if self.risk is None:
            raise StrategyError("HedgeAction needs a risk measure")
        if self.vector is None:  # decided by the SCHEMA's return type of the measure on the hedge instruments, never by the measure's name
            self.vector = _risk_is_vector(self.priceables, self.risk)
        if self.mode not in ("add", "resize"):
            raise StrategyError("mode must be 'add' or 'resize'")
        if self.scaling_parameter != "notional_amount" or self.risk_transformation is not None:
            raise NotSupportedError("scaling_parameter / risk_transformation are not supported; use the measure's reducer")
        if self.on_zero_hedge_risk not in ("raise", "skip"):
            raise StrategyError("on_zero_hedge_risk must be 'raise' or 'skip'")
        if self.csa_term is not None:
            self.pricer_request = {**dict(self.pricer_request or {}), "csa_term": self.csa_term}
        if self.mode == "resize" and not self.vector and self.priceables is not None and len(self.priceables.legs) != 1:
            raise StrategyError("mode='resize' needs a single-leg hedge (a ladder hedge may have several)")
        if self.vector and self.priceables is not None and any(l.weight != 1.0 for l in self.priceables.legs):
            raise StrategyError("a ladder hedge solves one quantity per leg: leg weights must be 1")
        if not self.tags:
            self.tags = ("hedge",)

    def requires_measures(self) -> Tuple[str, ...]:
        return (self.risk,)

    def _scope(self, info: Any) -> Any:
        s = (getattr(info, "data", None) or {}).get("scope")
        return s if s is not None else (self.scope if self.scope is not None else "portfolio")

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        if info is not None and info.skip:
            return []
        if self.vector:
            return self._apply_ladder(ts, view, info)
        b = self._bundle_at(ts)
        tgt = getattr(info, "target", None)
        target = float(self.target if tgt is None else tgt)
        scope = self._scope(info)
        net = float(view.measure(self.risk, scope, include_pending=True))
        unit = self._unit(view, b, self.risk, info)
        group = f"hedge:{self.name}"
        sel = PositionSelector(actions=(self.name,), kinds=("hedge",))
        held = tuple(p for p in view.positions(sel) if not _pending_close(view, p)) if self.mode == "resize" else ()
        if self.mode == "resize":
            net -= float(view.measure(self.risk, sel, include_pending=True))
        try:
            q, why = hedge_quantity(net, unit, target=target, risk_percentage=self.risk_percentage, min_trade_risk=self.min_trade_risk)
        except SizingError:
            if self.on_zero_hedge_risk == "skip":
                view.event("hedge_skipped", action=self.name, reason="zero_hedge_risk")
                return []
            raise
        if self.mode == "add":
            if why:
                view.event("hedge_skipped", action=self.name, reason=why)
                return []
            return self._orders(ts, view, info, [q * l.weight for l in b.legs], bundle=b, group=group)
        return self._resize(ts, view, info, b, q, held, unit, group)

    def _apply_ladder(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        b = self._bundle_at(ts)
        tgt = getattr(info, "target", None)
        target = self.target if tgt is None else tgt
        if isinstance(target, (int, float)):
            if target != 0:
                raise StrategyError("a ladder hedge target must be 0 (flat) or a bucket -> level mapping")
            target = None
        scope = self._scope(info)
        net = view.measure_vector(self.risk, scope, include_pending=True)
        group = f"hedge:{self.name}"
        resize = self.mode == "resize"
        lnames = [l.name or getattr(l.template, "name", "leg") for l in b.legs]
        scope_ids = {p.id for p in view.positions(scope)} if resize else set()
        held_by_leg: List[Tuple[Any, ...]] = []
        units: List[Dict[str, float]] = []
        counted: List[float] = []
        for leg, lname in zip(b.legs, lnames):
            held = tuple(p for p in view.positions(PositionSelector(actions=(self.name,), kinds=("hedge",), templates=(lname,))) if not _pending_close(view, p)) if resize else ()
            if held:
                # an aged hedge position's ladder differs from a fresh template's: hedge with the position's own per-unit ladder
                newest = max(held, key=lambda p: (p.entry_ts, p.id))
                units.append(vector_scale(view.measure_vector(self.risk, PositionSelector(ids=(newest.id,))), 1.0 / newest.quantity))
            else:
                units.append(view.measure_of_vector(resolve_template_refs(leg.template, view, info), self.risk))
            held_by_leg.append(held)
            counted.append(float(sum(p.quantity for p in held if p.id in scope_ids)))
        try:
            res = ladder_hedge_quantities(net, units, target=target, weights=self.bucket_weights, ridge=self.ridge, risk_percentage=self.risk_percentage, min_trade_risk=self.min_trade_risk)
        except SizingError:
            if self.on_zero_hedge_risk == "skip":
                view.event("hedge_skipped", action=self.name, reason="zero_hedge_risk")
                return []
            raise
        totals = [c + x for c, x in zip(counted, res.quantities)]
        view.event("ladder_hedge", action=self.name, quantities=dict(zip(lnames, totals)), l1_before=l1(res.before), l1_after=l1(res.after))
        if not resize:
            if res.skip:
                view.event("hedge_skipped", action=self.name, reason=res.skip)
                return []
            return self._orders(ts, view, info, list(res.quantities), bundle=b, group=group)
        out: List[Instruction] = []
        for leg, held, q, u in zip(b.legs, held_by_leg, totals, units):
            out += self._resize(ts, view, info, Bundle(b.name, (leg,)), q, held, l1(u), group)  # the size of a leg's risk is its ladder's L1 (a twist leg has ~0 parallel risk)
        return out

    def _resize(self, ts: pd.Timestamp, view: Any, info: Any, b: Bundle, q: float, held: Sequence[Any], unit: float, group: str) -> List[Instruction]:
        cur = sum(p.quantity for p in held)
        dq = q - cur
        if abs(dq * unit) <= max(self.min_trade_risk, 1e-9 * max(abs(q * unit), 1.0)):
            return []
        if not held:
            return self._orders(ts, view, info, [q], bundle=b, group=group)
        newest = max(held, key=lambda p: (p.entry_ts, p.id))
        if abs(q) < EPS or (cur * q < 0):
            out: List[Instruction] = [CloseOrder(PositionSelector(ids=tuple(p.id for p in held)), reason="hedge_unwind")]
            return out + (self._orders(ts, view, info, [q], bundle=b, group=group) if abs(q) >= EPS else [])
        return [ResizeOrder(newest.id, dq, reason="hedge_resize")]


def _risk_is_vector(bundle: Optional[Bundle], risk: Optional[str]) -> bool:
    """True when the schema of the hedge instruments declares `risk` as a dict-valued (vector) measure."""
    if bundle is None or risk is None:
        return False
    for leg in bundle.legs:
        m = leg.template.spec.schema.measures.get(risk)
        if m is not None:
            return is_vector(m)
    return False


def _pending_close(view: Any, pos: Any) -> bool:
    """True when a CloseOrder submitted earlier at this point already targets `pos`."""
    return any(isinstance(o, CloseOrder) and o.selector.matches(pos) for o in view.pending_orders)


@dataclass
class ExitTradeAction(Action):
    """Close visible open positions matching ALL given criteria (any-of within a list). No criteria = every open position.

    `tags` here are SELECTION tags. Only positions booked before this point can be closed (engine rule). `transaction_cost` overrides the
    position's own exit model when given."""

    kind: ClassVar[str] = "exit"
    default_phase: ClassVar[Phase] = Phase.EXIT
    priceable_names: Any = None
    name: Optional[str] = None
    transaction_cost: Any = None
    match_tags: str = field(default="any", kw_only=True)
    actions: Tuple[str, ...] = field(default=(), kw_only=True)
    kinds: Tuple[str, ...] = field(default=(), kw_only=True)
    position_ids: Tuple[str, ...] = field(default=(), kw_only=True)
    select: Optional[Callable[[Any], bool]] = field(default=None, kw_only=True)
    reason: Optional[str] = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        Action.__post_init__(self)
        if isinstance(self.priceable_names, str):
            self.priceable_names = (self.priceable_names,)
        self.priceable_names = tuple(self.priceable_names) if self.priceable_names else ()
        if self.match_tags not in ("any", "all"):
            raise StrategyError("match_tags must be 'any' or 'all'")
        self.actions, self.kinds, self.position_ids = tuple(self.actions), tuple(self.kinds), tuple(self.position_ids)

    def selector(self, info: Any = None) -> PositionSelector:
        ids = tuple(dict.fromkeys((*self.position_ids, *getattr(info, "position_ids", ()))))
        tags = self.tags
        preds = [self.select] if self.select is not None else []
        if tags and self.match_tags == "all":
            preds.append(lambda p, t=frozenset(tags): t <= set(p.tags))
        pred = (lambda p: all(f(p) for f in preds)) if preds else None
        return PositionSelector(ids=ids or None, tags=tags if tags and self.match_tags == "any" else None, templates=self.priceable_names or None,
                                actions=self.actions or None, kinds=self.kinds or None, predicate=pred)

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        if info is not None and info.skip:
            return []
        sel = self.selector(info)
        if not view.positions(sel):
            return []
        return [CloseOrder(sel, reason=self.reason or ("exit_all" if self.kind == "exit_all" else "signal"), cost_model=self.transaction_cost)]


@dataclass
class ExitAllPositionsAction(ExitTradeAction):
    kind: ClassVar[str] = "exit_all"
    info_kinds: ClassVar[Tuple[str, ...]] = ("exit",)


@dataclass
class RebalanceAction(_EntryAction):
    """Bring holdings to a target. `method(ts, view, info)` -> target SIZE (in `size_parameter` units) or `measure` mode: target measure level
    from the info (RiskBand) or `target`. mode 'add': the delta is a NEW position of the same template (parent = latest-expiring match);
    mode 'resize': a ResizeOrder on the newest matched position."""

    kind: ClassVar[str] = "rebalance"
    default_phase: ClassVar[Phase] = Phase.ADJUST
    meta_kind: ClassVar[str] = "rebalance"
    priceable: Any = None
    size_parameter: str = "quantity"
    method: Optional[Callable[..., float]] = None
    transaction_cost: Any = None
    transaction_cost_exit: Any = None
    name: Optional[str] = None
    mode: str = field(default="add", kw_only=True)
    measure: Optional[str] = field(default=None, kw_only=True)
    target: Optional[float] = field(default=None, kw_only=True)
    scope: Any = field(default=None, kw_only=True)
    match_templates: Optional[Sequence[str]] = field(default=None, kw_only=True)
    trade_duration: Any = field(default=INHERIT, kw_only=True)
    holiday_calendar: Optional[Sequence[Any]] = None

    def __post_init__(self) -> None:
        self.priceables = self.priceable
        _EntryAction.__post_init__(self)
        if (self.method is None) == (self.measure is None):
            raise StrategyError("RebalanceAction needs exactly one of method / measure")
        if self.mode not in ("add", "resize"):
            raise StrategyError("mode must be 'add' or 'resize'")
        if len(self.priceables.legs) != 1:
            raise StrategyError("RebalanceAction needs a single template")

    def requires_measures(self) -> Tuple[str, ...]:
        return (self.measure,) if self.measure else ()

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        if info is not None and info.skip:
            return []
        leg = self.priceables.legs[0]
        tname = leg.name or leg.template.name
        names = tuple(self.match_templates) if self.match_templates else (tname,)
        matched = list(view.positions(PositionSelector(templates=names)))
        if self.method is not None:
            if self.size_parameter == "quantity":
                cur, u = float(sum(p.quantity for p in matched)), 1.0
            else:  # a MEASURE of the instrument (for example `notional`), read through its binding: pricebt never reads an attribute of the object
                cur = float(view.measure(self.size_parameter, PositionSelector(templates=names)))
                u = float(view.measure_of(resolve_template_refs(leg.template, view, info), self.size_parameter))
            dq = rebalance_delta(cur, float(self.method(ts, view, info)), u)
        else:
            scope = (getattr(info, "data", None) or {}).get("scope")
            scope = scope if scope is not None else (self.scope if self.scope is not None else PositionSelector(templates=names))
            tgt = getattr(info, "target", None)
            tgt = self.target if tgt is None else tgt
            if tgt is None:
                raise StrategyError(f"RebalanceAction {self.name!r}: no target in the info and none configured")
            x = float(view.measure(self.measure, scope, include_pending=True))
            dq = rebalance_delta(x, float(tgt), float(view.measure_of(resolve_template_refs(leg.template, view, info), self.measure)))
        if abs(dq) < EPS:
            return []
        if self.mode == "resize" and matched:
            newest = max(matched, key=lambda p: (p.entry_ts, p.id))
            return [ResizeOrder(newest.id, dq, reason="rebalance")]
        parent = max(matched, key=lambda p: (p.final_ts or FOREVER, p.id)).id if matched else None
        duration = self.trade_duration
        if duration is INHERIT:
            finals = [p.final_ts for p in matched]
            duration = None if (not finals or any(f is None for f in finals)) else max(finals)
        return self._orders(ts, view, info, [dq], bundle=self.priceables, group=f"rebalance:{self.name}", duration=duration, parent=parent)


@dataclass
class CustomAction(Action):
    """`fn(ts, view, info, **kwargs) -> orders`; fn is a callable or an allow-listed 'pkg.mod:fn'."""

    kind: ClassVar[str] = "custom"
    fn: Any = None
    name: Optional[str] = None
    kwargs: Optional[Mapping[str, Any]] = field(default=None, kw_only=True)
    needs_measures: Sequence[str] = field(default=(), kw_only=True)
    needs_signals: Sequence[Any] = field(default=(), kw_only=True)

    def __post_init__(self) -> None:
        Action.__post_init__(self)
        if self.fn is None:
            raise StrategyError("CustomAction needs fn")
        self._fn = resolve_dotted(self.fn) if isinstance(self.fn, str) else self.fn

    def requires_measures(self) -> Tuple[str, ...]:
        return tuple(self.needs_measures)

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return tuple(n if isinstance(n, SignalNeed) else SignalNeed(n) for n in self.needs_signals)

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        return [self._complete(o) for o in (self._fn(ts, view, info, **dict(self.kwargs or {})) or ())]


@dataclass
class SubmitOrdersAction(Action):
    """Submits the orders an OrdersGeneratorTrigger put in the info."""

    kind: ClassVar[str] = "submit_orders"
    name: Optional[str] = None

    def apply(self, ts: pd.Timestamp, view: Any, info: Any) -> Sequence[Instruction]:
        return [self._complete(o) for o in getattr(info, "orders", ())]
