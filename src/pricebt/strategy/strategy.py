"""`Strategy(initial_portfolio, triggers, cash_accrual)` and the per-run `StrategyRun` the engine drives.

Per timeline point (`StrategyRun.step`), every trigger is evaluated EXACTLY ONCE and actions run in slots:
  INITIAL (initial-portfolio orders, booked immediately) -> stage-0 triggers evaluated on the snapshot -> EXIT -> ADD ->
  per trigger in list order: stage-1 (path-dependent) triggers evaluated NOW, seeing the pending orders of the earlier slots, all their
  actions run in place; stage-0 triggers run their ADJUST actions -> HEDGE.
`eval_mode='snapshot'` makes every trigger stage 0 (all triggers are evaluated on the snapshot and their actions run in (phase, trigger_idx, action_idx) order).
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from ..errors import EngineInvariantError, StrategyError, TriggerError
from ..orders import Instruction, OpenOrder, PositionMeta
from ..timeutil import TimelineContext
from ..types import Phase, as_list
from .actions import Action, as_bundle
from .infos import ANY_ACTION, ENTRY, ENTRY_KINDS, match_info
from .requirements import CalcType, RiskBandTriggerRequirements
from .signals import SignalNeed
from .triggers import Trigger, times_of

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionSpec:
    """An initial holding with an explicit size / tags / entry cost."""

    item: Any
    quantity: float = 1.0
    name: Optional[str] = None
    tags: Tuple[str, ...] = ()
    transaction_cost: Any = None


@dataclass(frozen=True)
class StepReport:
    ts: pd.Timestamp
    fired: Tuple[Tuple[int, str, Tuple[str, ...]], ...]
    n_instructions: int


class Strategy:
    """gs-compatible: `Strategy(None, [PeriodicTrigger(...)])`. Triggers are deep-copied (the caller's objects are never mutated) and `start()`
    clones again, so one Strategy can be run any number of times with identical results."""

    def __init__(self, initial_portfolio: Any = None, triggers: Any = None, cash_accrual: Any = None, *, name: str = "strategy", eval_mode: str = "staged"):
        if eval_mode not in ("staged", "snapshot"):
            raise StrategyError("eval_mode must be 'staged' or 'snapshot'")
        self.name = name
        self.eval_mode = eval_mode
        self.cash_accrual = cash_accrual
        self.initial_portfolio = copy.deepcopy(initial_portfolio)
        raw = as_list(triggers)
        self.triggers: Tuple[Trigger, ...] = tuple(copy.deepcopy(t) for t in raw)
        for t in self.triggers:
            if not isinstance(t, Trigger):
                raise StrategyError(f"triggers must be Trigger objects, got {type(t).__name__}")
        n, seen = 0, {}
        for i, t in enumerate(self.triggers):
            for j, a in enumerate(t.actions):
                if getattr(a, "name", None) is None:
                    n += 1
                    a.bind(name=f"Action{n}", trigger_idx=i, action_idx=j)
                else:
                    a.bind(name=a.name, trigger_idx=i, action_idx=j)
                if a.name in seen:
                    raise StrategyError(f"duplicate action name {a.name!r} (triggers {seen[a.name]} and {i}); names must be unique")
                seen[a.name] = i

    # ---- declarations
    @property
    def risks(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(m for t in self.triggers for m in t.requires_measures()))

    def required_signals(self) -> Tuple[SignalNeed, ...]:
        out: Dict[int, SignalNeed] = {}
        for t in self.triggers:
            for need in t.requires_signals():
                if isinstance(need.source, str):
                    continue
                k = id(need.source)
                out[k] = SignalNeed(need.source, max(need.lookback, out[k].lookback if k in out else 0))
        return tuple(out.values())

    def validate(self) -> List[str]:
        """Structural problems; [] means OK."""
        probs = []
        for i, t in enumerate(self.triggers):
            for a in t.actions:
                if str(getattr(a, "trade_duration", "")).lower().strip() == "next schedule" and not t.supplies_schedule():
                    probs.append(f"trigger {i}: action {a.name!r} uses 'next schedule' but the trigger supplies no schedule")
                req = t.trigger_requirements
                if isinstance(req, RiskBandTriggerRequirements) and req.rearm is not None and getattr(a, "risk_percentage", 100.0) < 100.0:
                    probs.append(f"trigger {i}: a rearm latch with a partial hedge can leave the book outside the band; prefer cooldown")
        return probs

    def clone(self) -> "Strategy":
        return copy.deepcopy(self)

    def start(self, ctx: TimelineContext) -> "StrategyRun":
        return StrategyRun(self, ctx)

    # ---- conveniences
    def engine_settings(self, **kw: Any) -> Any:
        from ..engine import EngineSettings

        kw.setdefault("cash_accrual", self.cash_accrual)
        kw.setdefault("measures", ())
        return EngineSettings(**kw)

    def backtest(self, grid: Any, mdps: Any, *, signals: Any = None, **settings: Any) -> Any:
        """Run on `grid` with market provider(s) `mdps` (a provider or {role: provider}); returns the engine's RunRecord. A SignalStore is
        built automatically when the strategy needs signals."""
        from ..engine import Engine
        from ..market import MarketData
        from ..timeutil import Clock
        from .signals import SignalStore

        bindings = mdps if isinstance(mdps, Mapping) else {"primary": mdps}
        settings.setdefault("show_progress", False)
        if signals is None and self.required_signals():
            signals = SignalStore.for_strategy(self)
        return Engine(grid, MarketData(bindings, Clock()), self, self.engine_settings(**settings), signals=signals).run()

    @classmethod
    def periodic(cls, template: Any, frequency: str = "1m", trade_duration: Any = None, *, start: Any = None, end: Any = None, **action_kw: Any) -> "Strategy":
        """`Strategy.periodic(swap, '1m', '1m')`: add `template` every `frequency`, each held for `trade_duration`."""
        from .actions import AddTradeAction
        from .triggers import PeriodicTrigger

        return cls(None, [PeriodicTrigger(start_date=start, end_date=end, frequency=frequency, actions=AddTradeAction(template, trade_duration, **action_kw))])


class StrategyRun:
    """One run's state (clones of the triggers, initial-portfolio segments). Implements the engine's `StrategyRun` protocol."""

    def __init__(self, strategy: Strategy, ctx: TimelineContext):
        self.ctx = ctx
        self.mode = strategy.eval_mode
        self.triggers: List[Trigger] = [copy.deepcopy(t) for t in strategy.triggers]
        self._last: List[Optional[pd.Timestamp]] = [None] * len(self.triggers)
        for t in self.triggers:
            t.reset()
            t.bind(ctx)
            for a in t.actions:
                a.bind_ctx(ctx)
        self._segments = self._compile_initial(copy.deepcopy(strategy.initial_portfolio))
        self._stage = [1 if (t.calc_type is CalcType.path_dependent and self.mode == "staged") else 0 for t in self.triggers]

    # ---- initial portfolio
    def _compile_initial(self, ip: Any) -> List[Tuple[pd.Timestamp, Optional[pd.Timestamp], List[Any]]]:
        if ip is None or (isinstance(ip, (list, tuple)) and not ip):
            return []
        if not isinstance(ip, Mapping):
            return [(self.ctx.start, None, as_list(ip))]
        keys = sorted((self.ctx.at(k), as_list(v)) for k, v in ip.items())
        if keys and keys[0][0] < self.ctx.start:
            raise StrategyError(f"initial_portfolio key {keys[0][0]} is before the backtest start {self.ctx.start}")
        segs = []
        for k, (ts, items) in enumerate(keys):
            if ts > self.ctx.end:
                log.warning("initial_portfolio key %s is after the backtest end and is ignored", ts)
                continue
            final = keys[k + 1][0] if k + 1 < len(keys) and keys[k + 1][0] <= self.ctx.end else None
            segs.append((ts, final, items))
        return segs

    def _initial_orders(self, ts: pd.Timestamp) -> List[OpenOrder]:
        out = []
        for entry, final, items in self._segments:
            if entry != ts:
                continue
            for it in items:
                spec = it if isinstance(it, PositionSpec) else PositionSpec(it)
                bundle = as_bundle(spec.item)
                for leg in bundle.legs:
                    lname = leg.name or getattr(leg.template, "name", "leg")
                    meta = PositionMeta(action="initial", kind="initial", template=lname, tags=tuple(spec.tags),
                                        extra={"name": spec.name or f"{lname}_{ts.strftime('%Y-%m-%d')}"})
                    out.append(OpenOrder(template=leg.template, quantity=spec.quantity * leg.weight, final_ts=final, tags=tuple(spec.tags), meta=meta,
                                         cost_entry=spec.transaction_cost, immediate=True))
        return out

    # ---- engine protocol
    def timeline_extras(self) -> Sequence[pd.Timestamp]:
        vals: List[Any] = []
        for t in self.triggers:
            vals.extend(times_of(t, self.ctx))
        extra = [s[0] for s in self._segments] + [s[1] for s in self._segments if s[1] is not None]
        return sorted(set(self.ctx.normalise(vals)) | {e for e in extra if self.ctx.start <= e <= self.ctx.end})

    def _eval(self, i: int, ts: pd.Timestamp, view: Any) -> Any:
        last = self._last[i]
        if last is not None and ts <= last:
            raise EngineInvariantError(f"trigger {i} would be evaluated twice / out of order at {ts} (last {last})")
        self._last[i] = ts
        trig = self.triggers[i]
        info = trig.has_triggered(ts, view)
        if info.triggered and info.strict:
            self._check_keys(i, trig, info)
        return info

    @staticmethod
    def _check_keys(i: int, trig: Trigger, info: Any) -> None:
        valid = {ANY_ACTION}
        for a in trig.actions:
            valid |= {a.name, a.kind, *a.info_kinds}
            if a.kind in ENTRY_KINDS or any(k in ENTRY_KINDS for k in a.info_kinds):
                valid.add(ENTRY)
        bad = [k for k in info.info if k not in valid]
        if bad:
            raise TriggerError(f"trigger {i} returned info keys {bad} matching no action; valid: {sorted(valid)}")

    def _run_action(self, ts: pd.Timestamp, view: Any, submit: Callable[[Instruction], None], info: Any, action: Action) -> int:
        eff = match_info(info.info, action.name, action.kind, action.info_kinds)
        orders = list(action.apply(ts, view, eff))
        for o in orders:
            submit(o)
        return len(orders)

    def step(self, ts: pd.Timestamp, view: Any, submit: Callable[[Instruction], None]) -> StepReport:
        ts = pd.Timestamp(ts)
        n = 0
        for o in self._initial_orders(ts):
            submit(o)
            n += 1
        infos: Dict[int, Any] = {}
        fired: List[Tuple[int, str, Tuple[str, ...]]] = []
        stage0 = [i for i in range(len(self.triggers)) if self._stage[i] == 0]
        for i in stage0:
            infos[i] = self._eval(i, ts, view)
        live0 = [i for i in stage0 if infos[i].triggered]
        ran: Dict[int, List[str]] = {i: [] for i in range(len(self.triggers))}

        def run(i: int, a: Action) -> None:
            nonlocal n
            n += self._run_action(ts, view, submit, infos[i], a)
            ran[i].append(a.name)

        for phase in (Phase.EXIT, Phase.ADD):
            for i in live0:
                for a in self.triggers[i].actions:
                    if a.effective_phase is phase:
                        run(i, a)
        for i, trig in enumerate(self.triggers):
            if self._stage[i] == 1:
                infos[i] = self._eval(i, ts, view)
                if infos[i].triggered:
                    for a in trig.actions:
                        run(i, a)
            elif infos[i].triggered:
                for a in trig.actions:
                    if a.effective_phase is Phase.ADJUST:
                        run(i, a)
        for i in live0:
            for a in self.triggers[i].actions:
                if a.effective_phase is Phase.HEDGE:
                    run(i, a)
        for i, t in enumerate(self.triggers):
            if infos[i].triggered:
                fired.append((i, t.name or type(t).__name__, tuple(ran[i])))
                view.event("trigger_fired", trigger_idx=i, trigger=t.name or type(t).__name__, actions=tuple(ran[i]))
        report = StepReport(ts, tuple(fired), n)
        return report
