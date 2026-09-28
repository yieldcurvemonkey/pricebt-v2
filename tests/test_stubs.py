"""The P3.1 out-of-scope stubs and plain ported enums/NamedTuples (IMPLEMENTATION_PLAN.md P3.1;
DESIGN.md section 2.3, section 9.3 "stubs" row and "core" row).

Every one of these classes is GS-server-only machinery pricebt v2 does not implement: the
importable name must exist (so a ported notebook's imports still resolve, and so the real modules
that import a handful of these names by type -- DataHandler, FillEvent, OrderBase, OrderCost --
still import cleanly), but constructing any of them must raise NotSupportedError.
"""
from __future__ import annotations

import datetime as dt

import pytest

import pricebt.backtests as bt
from pricebt.backtests import (
    data_handler,
    equity_vol_engine,
    event,
    execution_engine,
    order,
    predefined_asset_engine,
    strategy_systematic,
)
from pricebt.backtests.core import (
    Backtest,
    BacktestResult,
    BacktestTradingQuantityType,
    DeltaHedgeParameters,
    MarketModel,
    TimeWindow,
    TradeInMethod,
    ValuationFixingType,
    ValuationMethod,
)
from pricebt.errors import NotSupportedError

STUB_CLASSES = [
    Backtest,
    BacktestResult,
    equity_vol_engine.EquityVolEngine,
    predefined_asset_engine.PredefinedAssetEngine,
    strategy_systematic.StrategySystematic,
    order.OrderBase,
    order.OrderTWAP,
    order.OrderMarketOnClose,
    order.OrderCost,
    order.OrderAtMarket,
    order.OrderTwapBTIC,
    event.Event,
    event.MarketEvent,
    event.ValuationEvent,
    event.OrderEvent,
    event.FillEvent,
    data_handler.Clock,
    data_handler.DataHandler,
    execution_engine.ExecutionEngine,
    execution_engine.SimulatedExecutionEngine,
]


@pytest.mark.parametrize("cls", STUB_CLASSES, ids=[c.__name__ for c in STUB_CLASSES])
def test_stub_raises_not_supported_error_on_construction(cls):
    with pytest.raises(NotSupportedError, match="GS-server-only"):
        cls()


def test_delta_hedge_parameters_raises_on_construction_too():
    """A dataclass (its fields matter for constructor parity, DESIGN.md MUST-2), so it needs its
    own call rather than the bare `cls()` the other stubs get."""
    with pytest.raises(NotSupportedError, match="GS-server-only"):
        DeltaHedgeParameters(frequency="1m")


def test_order_hierarchy_matches_gs_order_twap_btic_subclasses_order_twap():
    assert issubclass(order.OrderTwapBTIC, order.OrderTWAP)
    assert issubclass(order.OrderTWAP, order.OrderBase)


def test_event_hierarchy_matches_gs():
    for cls in (event.MarketEvent, event.ValuationEvent, event.OrderEvent, event.FillEvent):
        assert issubclass(cls, event.Event)


# --------------------------------------------------------------------------------------- core enums


def test_trade_in_method_value():
    assert TradeInMethod.FixedRoll.value == "fixedRoll"


def test_market_model_values():
    assert MarketModel.STICKY_FIXED_STRIKE.value == "SFK"
    assert MarketModel.STICKY_DELTA.value == "SD"


def test_valuation_fixing_type_value():
    assert ValuationFixingType.PRICE.value == "price"


def test_enums_are_case_insensitive_like_gs_enumbase():
    assert MarketModel("sfk") is MarketModel.STICKY_FIXED_STRIKE
    assert ValuationFixingType("PRICE") is ValuationFixingType.PRICE


def test_backtest_trading_quantity_type_members():
    expected = {"notional", "quantity", "vega", "gamma", "NAV", "premium", "vegaNotional"}
    assert {m.value for m in BacktestTradingQuantityType} == expected


def test_time_window_is_a_namedtuple_with_start_end_defaults_none():
    tw = TimeWindow()
    assert tw.start is None and tw.end is None
    tw2 = TimeWindow(dt.time(9, 0), dt.time(17, 0))
    assert tw2.start == dt.time(9, 0)
    assert tw2.end == dt.time(17, 0)


def test_valuation_method_defaults_to_price_and_no_window():
    vm = ValuationMethod()
    assert vm.data_tag is ValuationFixingType.PRICE
    assert vm.window is None


# ---------------------------------------------------------------------- backtests/__init__ re-exports


def test_backtests_package_reexports_core_names_and_strategy_systematic():
    assert bt.TimeWindow is TimeWindow
    assert bt.ValuationFixingType is ValuationFixingType
    assert bt.MarketModel is MarketModel
    assert bt.TradeInMethod is TradeInMethod
    assert bt.BacktestTradingQuantityType is BacktestTradingQuantityType
    assert bt.DeltaHedgeParameters is DeltaHedgeParameters
    assert bt.StrategySystematic is strategy_systematic.StrategySystematic
