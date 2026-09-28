"""BackTest.summary_stats (ported verbatim from gs_quant 2.1.17; decision 0.1 -- a 2.1.17
additive feature, in scope. No DEV marker: ported unchanged).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from pricebt.backtests.backtest_objects import BackTest, CashPayment
from pricebt.instrument import IRSwap
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import Price
from pricebt.risk.results import FloatWithInfo, PortfolioRiskResult, PricingFuture

DATES = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(5)]
TOTALS = [0.0, 10.0, 5.0, 20.0, 15.0]  # -> daily PnL [10, -5, 15, -5]


def _backtest_with_totals(dates, totals):
    """Drive result_summary['Total'] to exactly `totals` by pricing a single held instrument at
    those PVs, with no cash and no transaction costs."""
    inst = IRSwap(name="s")
    bt = BackTest(strategy=object(), states=dates, risks=[Price], price_measure=Price)
    for d, v in zip(dates, totals):
        bt.portfolio_dict[d] = Portfolio([inst])
        portfolio = Portfolio([inst])
        bt._results[d] = PortfolioRiskResult(portfolio, (Price,), [PricingFuture(FloatWithInfo(v, unit={"USD": 1}))])
    return bt


def test_summary_stats_empty_backtest_returns_empty_series():
    bt = BackTest(strategy=object(), states=[DATES[0]], risks=[Price], price_measure=Price)
    stats = bt.summary_stats()
    assert stats.empty


def test_summary_stats_basic_fields_hand_computation():
    bt = _backtest_with_totals(DATES, TOTALS)
    total = bt.result_summary["Total"]
    assert total.tolist() == TOTALS
    daily = total.diff().dropna()
    assert daily.tolist() == [10.0, -5.0, 15.0, -5.0]

    stats = bt.summary_stats(annualisation_factor=252)
    assert stats["Start Date"] == DATES[0]
    assert stats["End Date"] == DATES[-1]
    assert stats["Duration (days)"] == (DATES[-1] - DATES[0]).days
    assert stats["Total PnL"] == TOTALS[-1]
    assert stats["Total Transaction Costs"] == 0
    assert stats["Peak PnL"] == max(TOTALS)
    assert stats["Average Daily PnL"] == pytest.approx(daily.mean())
    assert stats["Daily PnL Std Dev"] == pytest.approx(daily.std())
    assert stats["Best Day"] == 15.0
    assert stats["Worst Day"] == -5.0
    assert stats["% Positive Days"] == pytest.approx(2 / 4 * 100)  # two of four days are positive


def test_summary_stats_annualised_return_and_volatility_hand_computation():
    bt = _backtest_with_totals(DATES, TOTALS)
    daily = bt.result_summary["Total"].diff().dropna()
    stats = bt.summary_stats(annualisation_factor=100)
    assert stats["Annualised Return"] == pytest.approx(daily.mean() * 100)
    assert stats["Annualised Volatility"] == pytest.approx(daily.std() * np.sqrt(100))
    assert stats["Sharpe Ratio"] == pytest.approx(stats["Annualised Return"] / stats["Annualised Volatility"])


def test_summary_stats_max_drawdown_hand_computation():
    bt = _backtest_with_totals(DATES, TOTALS)
    total = bt.result_summary["Total"]
    running_max = total.cummax()
    drawdown = total - running_max
    assert stats_max_drawdown(bt) == pytest.approx(drawdown.min())
    assert bt.summary_stats()["Current Drawdown"] == pytest.approx(drawdown.iloc[-1])


def stats_max_drawdown(bt):
    return bt.summary_stats()["Max Drawdown"]


def test_summary_stats_monotonically_rising_total_has_zero_drawdown_and_nan_calmar():
    """A strategy that only ever makes money has max_drawdown == 0, so Calmar Ratio (which
    divides by |max_drawdown|) is NaN by construction (gs's own `if max_drawdown != 0 else
    np.nan`), not a ZeroDivisionError."""
    bt = _backtest_with_totals(DATES, [0.0, 5.0, 10.0, 15.0, 20.0])
    stats = bt.summary_stats()
    assert stats["Max Drawdown"] == 0.0
    assert stats["Max Drawdown Duration (days)"] == 0
    assert np.isnan(stats["Calmar Ratio"])


def test_summary_stats_trade_count_from_trade_ledger():
    inst = IRSwap(name="Action1_swap_2024-01-01")
    bt = _backtest_with_totals(DATES, TOTALS)
    entry = CashPayment(inst, effective_date=DATES[0], direction=-1)
    entry.cash_paid["USD"] = -0.0
    bt.cash_payments[DATES[0]] = [entry]
    stats = bt.summary_stats()
    assert stats["Total Trades"] == 1


def test_summary_stats_trade_count_is_nan_when_trade_ledger_raises():
    """gs wraps trade_ledger() in a bare except; a multi-currency-style crash there should not
    prevent the rest of summary_stats from returning."""
    bt = _backtest_with_totals(DATES, TOTALS)

    def boom():
        raise RuntimeError("boom")

    bt.trade_ledger = boom
    stats = bt.summary_stats()
    assert np.isnan(stats["Total Trades"])
    assert stats["Total PnL"] == TOTALS[-1]
