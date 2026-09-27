"""`Trigger(trigger_requirements, actions)` and one thin subclass per requirements class (gs layout).

Every `*Trigger` also accepts the requirement fields as keyword arguments (flat form, used by config):
`PeriodicTrigger(frequency='1m', actions=[...])` == `PeriodicTrigger(PeriodicTriggerRequirements(frequency='1m'), [...])`.
"""
from __future__ import annotations

import inspect
from typing import Any, ClassVar, Optional, Sequence, Tuple, Type

import pandas as pd

from ..errors import StrategyError
from ..orders import Instruction
from ..timeutil import TimelineContext
from ..types import as_list
from .actions import Action, SubmitOrdersAction
from .infos import SubmitOrdersInfo, TriggerInfo  # noqa: F401
from .requirements import (AggregateTriggerRequirements, CalcType, CrossingTriggerRequirements, CustomTriggerRequirements, DateTriggerRequirements, EventTriggerRequirements,
                           IntradayTriggerRequirements, MeanReversionTriggerRequirements, MktTriggerRequirements, NotTriggerRequirements, PeriodicTriggerRequirements,
                           PortfolioTriggerRequirements, RebalanceTriggerRequirements, RiskBandTriggerRequirements, StrategyRiskTriggerRequirements,
                           TradeCountTriggerRequirements, TriggerRequirements)
from .signals import SignalNeed


def times_of(obj: Any, ctx: Optional[TimelineContext]) -> Sequence[Any]:
    """Call `get_trigger_times`, passing ctx only when the (possibly user-written) method accepts it."""
    fn = obj.get_trigger_times
    params = [p for p in inspect.signature(fn).parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return list(fn(ctx)) if params else list(fn())


class Trigger:
    requirements_cls: ClassVar[Optional[Type[TriggerRequirements]]] = None

    def __init__(self, trigger_requirements: Optional[TriggerRequirements] = None, actions: Any = None, *, name: Optional[str] = None, **req_kwargs: Any):
        if req_kwargs:
            if trigger_requirements is not None or self.requirements_cls is None:
                raise StrategyError(f"{type(self).__name__}: unexpected arguments {sorted(req_kwargs)}")
            trigger_requirements = self.requirements_cls(**req_kwargs)
        self.trigger_requirements = trigger_requirements
        acts = as_list(actions)
        self.actions: Tuple[Action, ...] = tuple(acts) if acts else self.default_actions()
        for a in self.actions:
            if not isinstance(a, Action):
                raise StrategyError(f"trigger actions must be Action objects, got {type(a).__name__}")
        self.name = name

    def default_actions(self) -> Tuple[Action, ...]:
        return ()

    def __eq__(self, other: Any) -> bool:
        return type(self) is type(other) and self.__dict__ == other.__dict__

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.trigger_requirements!r}, {list(self.actions)!r})"

    # ---- delegation
    @property
    def calc_type(self) -> CalcType:
        return self.trigger_requirements.calc_type

    @property
    def stateful(self) -> bool:
        return self.trigger_requirements.stateful

    def bind(self, ctx: TimelineContext) -> None:
        self.trigger_requirements.bind(ctx)

    def reset(self) -> None:
        self.trigger_requirements.reset()

    def get_trigger_times(self, ctx: Optional[TimelineContext] = None) -> Sequence[Any]:
        return self.trigger_requirements.get_trigger_times(ctx)

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        return self.trigger_requirements.has_triggered(ts, view)

    def supplies_schedule(self) -> bool:
        return self.trigger_requirements.supplies_schedule()

    def requires_measures(self) -> Tuple[str, ...]:
        own = self.trigger_requirements.requires_measures() if self.trigger_requirements is not None else ()
        return tuple(dict.fromkeys((*own, *(m for a in self.actions for m in a.requires_measures()))))

    @property
    def risks(self) -> Tuple[str, ...]:
        return self.requires_measures()

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        own = self.trigger_requirements.requires_signals() if self.trigger_requirements is not None else ()
        return (*own, *(n for a in self.actions for n in a.requires_signals()))


def _make(name: str, req: Type[TriggerRequirements]) -> Type[Trigger]:
    return type(name, (Trigger,), {"requirements_cls": req, "__doc__": f"Trigger over {req.__name__}."})


PeriodicTrigger = _make("PeriodicTrigger", PeriodicTriggerRequirements)
IntradayPeriodicTrigger = _make("IntradayPeriodicTrigger", IntradayTriggerRequirements)
DateTrigger = _make("DateTrigger", DateTriggerRequirements)
MktTrigger = _make("MktTrigger", MktTriggerRequirements)
CrossingTrigger = _make("CrossingTrigger", CrossingTriggerRequirements)
StrategyRiskTrigger = _make("StrategyRiskTrigger", StrategyRiskTriggerRequirements)
PortfolioTrigger = _make("PortfolioTrigger", PortfolioTriggerRequirements)
TradeCountTrigger = _make("TradeCountTrigger", TradeCountTriggerRequirements)
AggregateTrigger = _make("AggregateTrigger", AggregateTriggerRequirements)
NotTrigger = _make("NotTrigger", NotTriggerRequirements)
MeanReversionTrigger = _make("MeanReversionTrigger", MeanReversionTriggerRequirements)
EventTrigger = _make("EventTrigger", EventTriggerRequirements)
CustomTrigger = _make("CustomTrigger", CustomTriggerRequirements)
RiskBandTrigger = _make("RiskBandTrigger", RiskBandTriggerRequirements)
RebalanceTrigger = _make("RebalanceTrigger", RebalanceTriggerRequirements)


class OrdersGeneratorTrigger(Trigger):
    """gs idiom: subclass, implement `get_trigger_times(self)` (dt.time or timestamps) and `generate_orders(self, ts, view) -> [Instruction]`."""

    def __init__(self, actions: Any = None, *, name: Optional[str] = None):
        super().__init__(None, actions, name=name)
        self._bound: Optional[frozenset] = None

    def default_actions(self) -> Tuple[Action, ...]:
        return (SubmitOrdersAction(),)

    @property
    def calc_type(self) -> CalcType:
        return CalcType.simple

    @property
    def stateful(self) -> bool:
        return False

    def get_trigger_times(self, ctx: Optional[TimelineContext] = None) -> Sequence[Any]:
        return ()

    def generate_orders(self, ts: pd.Timestamp, view: Any) -> Sequence[Instruction]:
        raise NotImplementedError

    def bind(self, ctx: TimelineContext) -> None:
        self._bound = frozenset(t.value for t in ctx.normalise(times_of(self, ctx)))

    def reset(self) -> None:
        return None

    def supplies_schedule(self) -> bool:
        return False

    def requires_measures(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(m for a in self.actions for m in a.requires_measures()))

    def requires_signals(self) -> Tuple[SignalNeed, ...]:
        return tuple(n for a in self.actions for n in a.requires_signals())

    def has_triggered(self, ts: pd.Timestamp, view: Any) -> TriggerInfo:
        if self._bound is None:
            raise StrategyError("OrdersGeneratorTrigger used before bind(ctx)")
        if pd.Timestamp(ts).value not in self._bound:
            return TriggerInfo(False)
        orders = tuple(self.generate_orders(ts, view) or ())
        return TriggerInfo(True, {"submit_orders": SubmitOrdersInfo(orders=orders)}) if orders else TriggerInfo(False)
