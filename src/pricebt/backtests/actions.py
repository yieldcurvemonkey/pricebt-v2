"""gs_quant `backtests.actions` names over the pricebt actions (same positional field orders as gs).

What the facade adds on top of `pricebt.strategy.actions`:
* gs transaction models (`ConstantTransactionModel`, `ScaledTransactionModel`, `AggregateTransactionModel`) are converted to pricebt cost
  models; as in gs, `transaction_cost_exit=None` means "same model as the entry".
* priceables may be pricebt templates, `pricebt.instrument` constructors (`IRSwap`: terms on the session's registered spec; swaptions are not supported) or lists
  of them; a list mixing templates and Bundles is flattened into one list of legs.
* `ExitTradeAction(priceable_names=...)` selects open positions whose INSTRUMENT name (the gs `name=`; both legs of a named two-leg Bundle), leg
  template name, creating ACTION name, or gs trade name ('<Action>_<Instrument>_<date>') is listed.
* `HedgeAction(risk=IRDelta(...))`: a scalar RiskMeasure sizes one hedge quantity; a vector one (bare `IRDelta`, the bucketed ladder) solves
  one quantity per hedge leg (pricebt ladder hedge).

`EnterPositionQuantityScaledAction` (EquityVolEngine only) is a stub raising NotSupportedError.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, FrozenSet, Optional

from ..common import BacktestTradingQuantityType
from ..errors import NotSupportedError
from ..orders import PositionSelector
from ..risk import RiskMeasure
from ..strategy import actions as _pb
from ..strategy.actions import Action, Bundle, CustomAction, Leg
from ..strategy.infos import (AddScaledTradeActionInfo, AddTradeActionInfo, AddWeightedTradeActionInfo, ExitTradeActionInfo, HedgeActionInfo,
                              RebalanceActionInfo)
from ..strategy.sizing import ScalingActionType
from .backtest_objects import to_cost_model

__all__ = [
    "Action", "AddTradeAction", "AddScaledTradeAction", "AddWeightedTradeAction", "HedgeAction", "ExitTradeAction", "ExitAllPositionsAction",
    "RebalanceAction", "EarlyExitPositionLimitScaledAction", "EnterPositionQuantityScaledAction", "CustomAction", "ScalingActionType",
    "BacktestTradingQuantityType", "AddTradeActionInfo", "AddScaledTradeActionInfo", "AddWeightedTradeActionInfo", "HedgeActionInfo",
    "ExitTradeActionInfo", "RebalanceActionInfo", "Bundle", "Leg",
]


def _flatten(x: Any) -> Any:
    """A list mixing templates and Bundles (a two-leg structure) -> one flat list of legs (pricebt's as_bundle cannot nest bundles)."""
    if isinstance(x, (list, tuple)) and any(isinstance(i, Bundle) for i in x):
        out = []
        for i in x:
            out.extend(i.legs if isinstance(i, Bundle) else [i])
        return out
    return x


def _gs_costs(action: Any) -> None:
    for attr in ("transaction_cost", "transaction_cost_exit"):
        if hasattr(action, attr):
            setattr(action, attr, to_cost_model(getattr(action, attr)))


def _prep_entry(action: Any) -> None:
    action.priceables = _flatten(action.priceables)
    if getattr(action, "dated_priceables", None):
        action.dated_priceables = {k: _flatten(v) for k, v in action.dated_priceables.items()}
    _gs_costs(action)


class AddTradeAction(_pb.AddTradeAction):
    """gs `AddTradeAction(priceables, trade_duration=None, name=None, transaction_cost=None, transaction_cost_exit=None, holiday_calendar=None)`.
    trade_duration: None (hold), tenor ('1m'), the name of a date among the built instrument's resolved terms (e.g. 'maturity'), 'next schedule', date, timedelta."""

    def __post_init__(self) -> None:
        _prep_entry(self)
        super().__post_init__()


class AddScaledTradeAction(_pb.AddScaledTradeAction):
    """gs `AddScaledTradeAction(priceables, trade_duration, name, scaling_type=ScalingActionType.size, scaling_risk=None, scaling_level=1, ...)`.
    size: quantity x scaling_level; risk_measure: scaling_level / unit risk (scaling_risk a RiskMeasure); NAV: spend scaling_level + realised cash."""

    def __post_init__(self) -> None:
        _prep_entry(self)
        super().__post_init__()


class AddWeightedTradeAction(_pb.AddWeightedTradeAction):
    def __post_init__(self) -> None:
        _prep_entry(self)
        super().__post_init__()


class EarlyExitPositionLimitScaledAction(_pb.EarlyExitPositionLimitScaledAction):
    def __post_init__(self) -> None:
        _prep_entry(self)
        super().__post_init__()


class HedgeAction(_pb.HedgeAction):
    """gs `HedgeAction(risk, priceables, trade_duration, name, csa_term, scaling_parameter, transaction_cost, transaction_cost_exit,
    risk_transformation, holiday_calendar, risk_percentage=100)`: after this point's other orders, add q units of the hedge so that
    net risk + q x unit risk = 0 (x risk_percentage/100). With trade_duration '1b' the hedge is replaced every day (the notebook's scheme)."""

    def __post_init__(self) -> None:
        _prep_entry(self)
        if isinstance(self.risk, RiskMeasure) and getattr(self, "vector", False) is None:
            self.vector = self.risk.vector
        super().__post_init__()


def _gs_match(names: FrozenSet[str], extra: Optional[Callable[[Any], bool]]) -> Callable[[Any], bool]:
    def pred(p: Any) -> bool:
        m = p.meta
        spec = getattr(p, "spec", None)
        hit = (m.template in names or m.action in names or getattr(spec, "gs_name", None) in names
               or (m.extra or {}).get("name") in names)
        return hit and (extra is None or extra(p))

    return pred


class ExitTradeAction(_pb.ExitTradeAction):
    """gs `ExitTradeAction(priceable_names=None, name=None, transaction_cost=None)`: close every open position (booked at an earlier point)
    or only those matching `priceable_names` (instrument name, leg name, creating action name or gs trade name)."""

    def __post_init__(self) -> None:
        _gs_costs(self)
        super().__post_init__()

    def selector(self, info: Any = None) -> PositionSelector:
        sel = super().selector(info)
        if not self.priceable_names:
            return sel
        return dataclasses.replace(sel, templates=None, predicate=_gs_match(frozenset(self.priceable_names), sel.predicate))


class ExitAllPositionsAction(_pb.ExitAllPositionsAction):
    """gs `ExitAllPositionsAction()`: close every open position booked at an earlier point."""

    def __post_init__(self) -> None:
        _gs_costs(self)
        super().__post_init__()


class RebalanceAction(_pb.RebalanceAction):
    """gs `RebalanceAction(priceable, size_parameter, method, transaction_cost, transaction_cost_exit, name)`; `method(ts, view, info)` returns
    the target size in `size_parameter` units (gs passed the backtest object as the second argument; pricebt passes the read-only EngineView)."""

    def __post_init__(self) -> None:
        self.priceable = _flatten(self.priceable)
        _gs_costs(self)
        super().__post_init__()


class EnterPositionQuantityScaledAction:
    """Stub: only gs's server-side EquityVolEngine executes this action."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotSupportedError("EnterPositionQuantityScaledAction runs only on gs_quant's server-side EquityVolEngine. Use "
                                "AddScaledTradeAction(priceables, trade_duration, scaling_type=ScalingActionType.size, scaling_level=<quantity>) "
                                "on GenericEngine with your own equity-option pricable")
