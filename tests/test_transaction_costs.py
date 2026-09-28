"""Transaction-cost models and TransactionCostEntry (IMPLEMENTATION_PLAN.md P3.2; research/02
section 2.6, research/03 sections 10-11).

DEV-E12 (this file's part): ScaledTransactionModel.get_unit_cost's risk-based branch compares the
pricing date against `_BACKTEST_END` (set by the engine for the run) instead of gs's
`dt.date.today()`, so the result is deterministic regardless of when the test happens to run.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from pricebt.backtests.backtest_objects import (
    _BACKTEST_END,
    AggregateTransactionModel,
    ConstantTransactionModel,
    ScaledTransactionModel,
    TransactionAggType,
    TransactionCostEntry,
)
from pricebt.instrument import IRSwap
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRDeltaParallel
from pricebt.risk.results import FloatWithInfo, PortfolioRiskResult, PricingFuture

D1 = dt.date(2024, 1, 2)


def _swap(notional=50000, ccy="GBP", name="s"):
    return IRSwap(notional_amount=notional, notional_currency=ccy, name=name)


# --------------------------------------------------------------------------------- ConstantTransactionModel


def test_constant_transaction_model_returns_fixed_cost_regardless_of_instrument():
    model = ConstantTransactionModel(5)
    assert model.get_unit_cost(D1, None, _swap()) == 5
    assert model.class_type == "constant_transaction_model"


def test_transaction_model_frozen_and_hashable():
    a = ConstantTransactionModel(5)
    b = ConstantTransactionModel(5)
    assert a == b
    assert hash(a) == hash(b)
    with pytest.raises(Exception):  # frozen dataclass: FrozenInstanceError (a subclass of AttributeError)
        a.cost = 10


# ----------------------------------------------------------------------------------- ScaledTransactionModel


def test_scaled_transaction_model_string_scaling_type_reads_the_attribute():
    model = ScaledTransactionModel("notional_amount", 0.0001)
    swap = _swap(notional=50000)
    assert model.get_unit_cost(D1, None, swap) == 50000


def test_scaled_transaction_model_unrecognised_attribute_raises_runtime_error():
    model = ScaledTransactionModel("no_such_field", 0.0001)
    swap = _swap()
    with pytest.raises(RuntimeError, match="no_such_field not recognised for instrument"):
        model.get_unit_cost(D1, None, swap)


def test_scaled_transaction_model_risk_measure_returns_nan_beyond_backtest_end_dev_e12():
    """DEV-E12: with _BACKTEST_END set (as run_backtest does for the run), a pricing date beyond
    it returns NaN via the ContextVar, not gs's dt.date.today()."""
    model = ScaledTransactionModel(IRDeltaParallel, 0.0001)
    swap = _swap()
    token = _BACKTEST_END.set(dt.date(2024, 1, 10))
    try:
        got = model.get_unit_cost(dt.date(2024, 1, 11), None, swap)
        assert np.isnan(got)
        # a date within the backtest end is not affected by this guard (it goes on to price)
    finally:
        _BACKTEST_END.reset(token)
    assert _BACKTEST_END.get() is None


def test_scaled_transaction_model_falls_back_to_today_when_backtest_end_unset():
    """Outside an active run (_BACKTEST_END unset), the gs dt.date.today() guard still applies."""
    model = ScaledTransactionModel(IRDeltaParallel, 0.0001)
    swap = _swap()
    assert _BACKTEST_END.get() is None
    far_future = dt.date.today() + dt.timedelta(days=3650)
    assert np.isnan(model.get_unit_cost(far_future, None, swap))


# -------------------------------------------------------------------------------- AggregateTransactionModel


def test_aggregate_transaction_model_sum_max_min():
    a = ConstantTransactionModel(2)
    b = ConstantTransactionModel(5)
    swap = _swap()
    assert AggregateTransactionModel((a, b), TransactionAggType.SUM).get_unit_cost(D1, None, swap) == 7
    assert AggregateTransactionModel((a, b), TransactionAggType.MAX).get_unit_cost(D1, None, swap) == 5
    assert AggregateTransactionModel((a, b), TransactionAggType.MIN).get_unit_cost(D1, None, swap) == 2


def test_aggregate_transaction_model_empty_gives_zero():
    assert AggregateTransactionModel().get_unit_cost(D1, None, _swap()) == 0


# --------------------------------------------------------------------------------------- TransactionCostEntry
# research/02 section 2.6 / research/03 section 10's worked test_scaled_transaction_cost: 5 daily
# GBP swaps, 50k notional each, ScaledTransactionModel('notional_amount', 0.0001) -- entry cost is
# 0.0001*50000 = 5 per trade, so 5 trades give -25 total.


def test_scaled_transaction_cost_worked_example_5_per_trade():
    model = ScaledTransactionModel("notional_amount", 0.0001)
    total = 0.0
    for i in range(5):
        swap = _swap(notional=50000, name=f"s{i}")
        tce = TransactionCostEntry(D1, swap, model)
        tce.calculate_unit_cost()
        cost = tce.get_final_cost()
        assert cost == 5.0
        total += cost
    assert total == 25.0


def test_transaction_cost_entry_all_instruments_for_a_portfolio():
    swap_a, swap_b = _swap(name="a"), _swap(name="b")
    portfolio = Portfolio([swap_a, swap_b])
    tce = TransactionCostEntry(D1, portfolio, ConstantTransactionModel(1))
    assert set(tce.all_instruments) == {swap_a, swap_b}


def test_transaction_cost_entry_get_final_cost_nets_across_a_portfolio():
    """gs comment: 'charges may net out for portfolios' -- a ScaledTransactionModel(RiskMeasure)
    sums signed unit costs across the portfolio's instruments before scaling and abs()."""
    swap_a, swap_b = _swap(name="a"), _swap(name="b")
    portfolio = Portfolio([swap_a, swap_b])
    model = ScaledTransactionModel(IRDeltaParallel, 0.0001)
    tce = TransactionCostEntry(D1, portfolio, model)
    # bypass PricingContext/instrument.calc: fill the per-instrument unit costs directly
    tce._unit_cost_by_model_by_inst[model] = {swap_a: 100.0, swap_b: -40.0}
    assert tce.get_final_cost() == 0.0001 * abs(100.0 - 40.0)


def test_transaction_cost_entry_get_final_cost_unwraps_pricing_future_and_prr():
    model = ConstantTransactionModel(0)  # placeholder; unit costs set directly below
    swap = _swap()
    tce = TransactionCostEntry(D1, swap, model)
    portfolio = Portfolio([swap])
    prr = PortfolioRiskResult(portfolio, (IRDeltaParallel,), [FloatWithInfo(3.0, unit={"USD": 1})])
    tce._unit_cost_by_model_by_inst[model] = {swap: prr}
    assert tce.get_final_cost() == 3.0

    tce2 = TransactionCostEntry(D1, swap, model)
    tce2._unit_cost_by_model_by_inst[model] = {swap: PricingFuture(4.0)}
    assert tce2.get_final_cost() == 4.0


def test_transaction_cost_entry_no_of_risk_calcs_counts_risk_measure_scaled_models():
    swap = _swap()
    agg = AggregateTransactionModel((ConstantTransactionModel(1), ScaledTransactionModel(IRDeltaParallel, 0.0001)))
    tce = TransactionCostEntry(D1, swap, agg)
    assert tce.no_of_risk_calcs == 1


def test_transaction_cost_entry_get_cost_by_component_sum_split():
    swap = _swap()
    fixed = ConstantTransactionModel(2)
    scaled = ScaledTransactionModel("notional_amount", 0.0001)
    agg = AggregateTransactionModel((fixed, scaled))
    tce = TransactionCostEntry(D1, swap, agg)
    tce.calculate_unit_cost()
    fixed_cost, scaled_cost = tce.get_cost_by_component()
    assert fixed_cost == 2
    assert scaled_cost == pytest.approx(0.0001 * 50000)


def test_transaction_cost_entry_date_get_set():
    swap = _swap()
    tce = TransactionCostEntry(D1, swap, ConstantTransactionModel(0))
    assert tce.date == D1
    later = dt.date(2024, 2, 1)
    tce.date = later
    assert tce.date == later


def test_transaction_cost_entry_additional_scaling_get_set_default_one():
    tce = TransactionCostEntry(D1, _swap(), ConstantTransactionModel(0))
    assert tce.additional_scaling == 1
    tce.additional_scaling = 2.5
    assert tce.additional_scaling == 2.5
