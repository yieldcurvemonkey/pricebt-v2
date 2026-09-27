"""Strategy layer: gs_quant-style triggers, actions, signals and Strategy, registered into the config registries on import."""
from __future__ import annotations

from ..registries import ACTIONS, SIGNALS, TRIGGERS
from .actions import (INHERIT, Action, AddScaledTradeAction, AddTradeAction, AddWeightedTradeAction, Bundle, CustomAction, EarlyExitPositionLimitScaledAction,
                      ExitAllPositionsAction, ExitTradeAction, HedgeAction, Leg, RebalanceAction, SubmitOrdersAction)
from .durations import CustomDuration, resolve_exit
from .infos import (NO_SCHEDULE, ActionInfo, AddScaledTradeActionInfo, AddTradeActionInfo, AddWeightedTradeActionInfo, ExitTradeActionInfo, HedgeActionInfo,
                    RebalanceActionInfo, ScheduleInfo, SubmitOrdersInfo, TriggerInfo)
from .requirements import (AggregateTriggerRequirements, AggType, CalcType, CrossingTriggerRequirements, CustomTriggerRequirements, DateTriggerRequirements,
                           EventCalendar, EventTriggerRequirements, IntradayTriggerRequirements, MeanReversionTriggerRequirements, MktTriggerRequirements,
                           NotTriggerRequirements, PeriodicTriggerRequirements, PortfolioTriggerRequirements, RebalanceTriggerRequirements, RiskBandTriggerRequirements,
                           RiskTriggerRequirements, StrategyRiskTriggerRequirements, TradeCountTriggerRequirements, TriggerDirection, TriggerRequirements, check_barrier)
from .signals import (BookLadderSignal, BookMeasureSignal, ConstantSource, DataSource, DerivedSignal, MeasureSignal, MissingDataStrategy, Observation, PricerSignal, RingBuffer, SeriesSource,
                       Signal, SignalNeed, SignalStore, StepTable)
from .sizing import LadderHedge, ScalingActionType, hedge_quantity, ladder_hedge_quantities, nav_scale, rebalance_delta, risk_scale, weighted_split
from .strategy import PositionSpec, StepReport, Strategy, StrategyRun
from .triggers import (AggregateTrigger, CrossingTrigger, CustomTrigger, DateTrigger, EventTrigger, IntradayPeriodicTrigger, MeanReversionTrigger, MktTrigger, NotTrigger,
                       OrdersGeneratorTrigger, PeriodicTrigger, PortfolioTrigger, RebalanceTrigger, RiskBandTrigger, StrategyRiskTrigger, Trigger, TradeCountTrigger)

IntradayPeriodicTriggerRequirements = IntradayTriggerRequirements

_TRIGGERS = {
    "periodic": PeriodicTrigger, "intraday_periodic": IntradayPeriodicTrigger, "date": DateTrigger, "mkt": MktTrigger, "crossing": CrossingTrigger,
    "strategy_risk": StrategyRiskTrigger, "portfolio": PortfolioTrigger, "trade_count": TradeCountTrigger, "aggregate": AggregateTrigger, "not": NotTrigger,
    "mean_reversion": MeanReversionTrigger, "event": EventTrigger, "custom": CustomTrigger, "orders_generator": OrdersGeneratorTrigger, "risk_band": RiskBandTrigger,
    "rebalance_trigger": RebalanceTrigger,
}
for _n, _c in _TRIGGERS.items():
    TRIGGERS.register(_n, _c)
TRIGGERS.register("risk", StrategyRiskTrigger)
for _n, _c in {
    "periodic": PeriodicTriggerRequirements, "intraday_periodic": IntradayTriggerRequirements, "date": DateTriggerRequirements, "mkt": MktTriggerRequirements,
    "crossing": CrossingTriggerRequirements, "strategy_risk": StrategyRiskTriggerRequirements, "portfolio": PortfolioTriggerRequirements,
    "trade_count": TradeCountTriggerRequirements, "aggregate": AggregateTriggerRequirements, "not": NotTriggerRequirements,
    "mean_reversion": MeanReversionTriggerRequirements, "event": EventTriggerRequirements, "custom": CustomTriggerRequirements, "risk_band": RiskBandTriggerRequirements,
    "rebalance_trigger": RebalanceTriggerRequirements,
}.items():
    TRIGGERS.register(f"{_n}_requirements", _c, aliases=(f"{_n}_trigger_requirements",))

for _n, _c in {
    "add_trade": AddTradeAction, "add_scaled_trade": AddScaledTradeAction, "add_weighted_trade": AddWeightedTradeAction, "hedge": HedgeAction, "exit_trade": ExitTradeAction,
    "exit_all": ExitAllPositionsAction, "rebalance": RebalanceAction, "early_exit_scaled": EarlyExitPositionLimitScaledAction, "custom": CustomAction,
    "submit_orders": SubmitOrdersAction,
}.items():
    ACTIONS.register(_n, _c)
ACTIONS.register("exit", ExitTradeAction)
ACTIONS.register("exit_all_positions", ExitAllPositionsAction)
ACTIONS.register("early_exit_position_limit_scaled", EarlyExitPositionLimitScaledAction)

for _n, _c in {"series": SeriesSource, "pricer": PricerSignal, "measure": MeasureSignal, "derived": DerivedSignal, "constant": ConstantSource, "step_table": StepTable}.items():
    SIGNALS.register(_n, _c)
SIGNALS.register("book_measure", BookMeasureSignal)
SIGNALS.register("book_ladder", BookLadderSignal)
SIGNALS.register("generic_data_source", SeriesSource)
SIGNALS.register("series_data_source", SeriesSource)
SIGNALS.register("pricer_lookup", PricerSignal)
SIGNALS.register("pricer_spread", PricerSignal.spread)
