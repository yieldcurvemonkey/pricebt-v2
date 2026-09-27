"""gs_quant-compatible facade: `gs_quant.backtests` module and class names over the pricebt engine, with NO data infrastructure.

Port a gs_quant backtest by (a) replacing the import root `gs_quant` with `pricebt`, (b) calling `PricebtSession.use(market=..., stack=...)` once instead of
`GsSession.use()` (the market is yours; the stack is an adapter's bundle of the pricer wrapper and instrument specs), and (c) building instruments with
`pricebt.instrument` (`IRSwap`, terms on the spec the session registered; swaptions and the other gs instruments raise NotSupportedError) or your own pricables. See
docs/guides/backtesting.md for the full guide, the gs -> pricebt mapping and what is not supported.

Importing this package imports no pricing library: the facade knows no library and no convention (a library is reached only through the stack's adapter).
"""
from __future__ import annotations

from .actions import (AddScaledTradeAction, AddTradeAction, AddWeightedTradeAction, EarlyExitPositionLimitScaledAction, EnterPositionQuantityScaledAction,
                      ExitAllPositionsAction, ExitTradeAction, HedgeAction, RebalanceAction, ScalingActionType)
from .backtest_objects import (AggregateTransactionModel, BackTest, Backtest, ConstantCashAccrualModel, ConstantTransactionModel, DataCashAccrualModel,
                               ScaledTransactionModel, TransactionAggType)
from .data_sources import GenericDataSource, GsDataSource, MissingDataStrategy
from .equity_vol_engine import EquityVolEngine
from .generic_engine import GenericEngine
from .predefined_asset_engine import PredefinedAssetEngine
from .strategy import Strategy
from .triggers import (AggregateTrigger, AggregateTriggerRequirements, AggType, DateTrigger, DateTriggerRequirements, MeanReversionTrigger,
                       MeanReversionTriggerRequirements, MktTrigger, MktTriggerRequirements, NotTrigger, NotTriggerRequirements, PeriodicTrigger,
                       PeriodicTriggerRequirements, PortfolioTrigger, PortfolioTriggerRequirements, RiskTriggerRequirements, StrategyRiskTrigger,
                       TriggerDirection)

__all__ = [
    "Strategy", "GenericEngine", "EquityVolEngine", "PredefinedAssetEngine", "Backtest", "BackTest",
    "PeriodicTrigger", "PeriodicTriggerRequirements", "StrategyRiskTrigger", "RiskTriggerRequirements", "MktTrigger", "MktTriggerRequirements",
    "AggregateTrigger", "AggregateTriggerRequirements", "DateTrigger", "DateTriggerRequirements", "MeanReversionTrigger", "MeanReversionTriggerRequirements",
    "PortfolioTrigger", "PortfolioTriggerRequirements", "NotTrigger", "NotTriggerRequirements", "TriggerDirection", "AggType",
    "AddTradeAction", "AddScaledTradeAction", "AddWeightedTradeAction", "HedgeAction", "ExitTradeAction", "ExitAllPositionsAction", "RebalanceAction",
    "EarlyExitPositionLimitScaledAction", "EnterPositionQuantityScaledAction", "ScalingActionType",
    "GenericDataSource", "GsDataSource", "MissingDataStrategy",
    "ConstantTransactionModel", "ScaledTransactionModel", "AggregateTransactionModel", "TransactionAggType", "ConstantCashAccrualModel", "DataCashAccrualModel",
]
