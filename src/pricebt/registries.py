"""The shared registries config resolves names against. Components register themselves at import of their package."""
from __future__ import annotations

from .registry import Registry

TRIGGERS = Registry("trigger")  # e.g. periodic, date, mkt, strategy_risk, aggregate, not, mean_reversion, risk_band ...
ACTIONS = Registry("action")  # e.g. add_trade, add_scaled_trade, hedge, exit_trade, rebalance ...
SIGNALS = Registry("signal")  # e.g. series, pricer, measure, derived, constant
COSTS = Registry("cost")  # constant, scaled, aggregate
CASH_ACCRUAL = Registry("cash_accrual")  # constant, series, callable
MDPS = Registry("mdp")  # market data providers
CALENDARS = Registry("calendar")  # named calendars: a Calendar, an iterable of holidays or a zero-argument callable returning one; adapters register theirs on import

from .costs import AggregateCost, CallableCashAccrual, ConstantCashAccrual, ConstantCost, ScaledCost, SeriesCashAccrual  # noqa: E402

COSTS.register("constant", ConstantCost)
COSTS.register("scaled", ScaledCost)
COSTS.register("aggregate", AggregateCost)
CASH_ACCRUAL.register("constant", ConstantCashAccrual)
CASH_ACCRUAL.register("series", SeriesCashAccrual)
CASH_ACCRUAL.register("callable", CallableCashAccrual)

from .testing import toys as _toys  # noqa: E402

MDPS.register("toy", _toys.ToyMDP)
