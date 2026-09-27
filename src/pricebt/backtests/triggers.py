"""gs_quant `backtests.triggers` names: the pricebt triggers under gs's names and positional field orders.

`Trigger(trigger_requirements, actions)`; requirements keep gs field order (`PeriodicTriggerRequirements(start_date, end_date, frequency)`,
`RiskTriggerRequirements(risk, trigger_level, direction)`, `MktTriggerRequirements(data_source, trigger_level, direction)`, ...).
`AggregateTriggerRequirements(triggers=[periodic_trigger, risk_trigger])` accepts Trigger OBJECTS like gs (their requirements are used; the
children's own actions are ignored with a log warning, as in gs). `risk=` takes a pricebt.risk RiskMeasure (or any measure name).

Differences from gs (deliberate, see pricebt.strategy): every trigger is evaluated once per timeline point in list order (date-major, not
trigger-major); MktTrigger is level-triggered on an as-of signal with no look-ahead; exits only touch positions booked at an earlier point.
"""
from __future__ import annotations

from ..strategy.infos import TriggerInfo
from ..strategy.requirements import (AggregateTriggerRequirements, AggType, CalcType, DateTriggerRequirements, EventTriggerRequirements,
                                     IntradayTriggerRequirements, MeanReversionTriggerRequirements, MktTriggerRequirements, NotTriggerRequirements,
                                     PeriodicTriggerRequirements, PortfolioTriggerRequirements, RiskTriggerRequirements, StrategyRiskTriggerRequirements,
                                     TradeCountTriggerRequirements, TriggerDirection, TriggerRequirements)
from ..strategy.triggers import (AggregateTrigger, DateTrigger, EventTrigger, IntradayPeriodicTrigger, MeanReversionTrigger, MktTrigger, NotTrigger,
                                 OrdersGeneratorTrigger, PeriodicTrigger, PortfolioTrigger, StrategyRiskTrigger, TradeCountTrigger, Trigger)

IntradayPeriodicTriggerRequirements = IntradayTriggerRequirements

__all__ = [
    "Trigger", "TriggerInfo", "TriggerRequirements", "TriggerDirection", "AggType", "CalcType",
    "PeriodicTrigger", "PeriodicTriggerRequirements", "IntradayPeriodicTrigger", "IntradayTriggerRequirements", "IntradayPeriodicTriggerRequirements",
    "StrategyRiskTrigger", "RiskTriggerRequirements", "StrategyRiskTriggerRequirements", "MktTrigger", "MktTriggerRequirements",
    "DateTrigger", "DateTriggerRequirements", "AggregateTrigger", "AggregateTriggerRequirements", "NotTrigger", "NotTriggerRequirements",
    "MeanReversionTrigger", "MeanReversionTriggerRequirements", "PortfolioTrigger", "PortfolioTriggerRequirements", "TradeCountTrigger",
    "TradeCountTriggerRequirements", "EventTrigger", "EventTriggerRequirements", "OrdersGeneratorTrigger",
]
