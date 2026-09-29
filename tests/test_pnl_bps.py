"""BackTest.pnl_bps (DESIGN.md section 8.4) -- a pure pricebt addition, not in gs, so there is no
DEV marker for it: it is new API, not a deviation from existing gs behaviour.
"""
from __future__ import annotations

import datetime as dt

import pytest

from pricebt.backtests.backtest_objects import BackTest
from pricebt.instrument import IRSwap
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRDelta, IRDeltaParallel, Price
from pricebt.risk.results import FloatWithInfo, MultipleRiskMeasureResult, PortfolioRiskResult, PricingFuture, make_bucketed_frame

D1 = dt.date(2024, 1, 2)
D2 = dt.date(2024, 1, 3)
D3 = dt.date(2024, 1, 4)


def _swap(name="s"):
    return IRSwap(name=name)


def _prr(inst, price, delta, price_measure=Price, delta_measure=IRDeltaParallel):
    portfolio = Portfolio([inst])
    mm = MultipleRiskMeasureResult(
        inst,
        {price_measure: FloatWithInfo(price, unit={"USD": 1}), delta_measure: FloatWithInfo(delta, unit={"USD": 1})}
    )
    return PortfolioRiskResult(portfolio, (price_measure, delta_measure), [PricingFuture(mm)])


def _three_date_backtest():
    """PnL per date: D2 - D1 = 50, D3 - D2 = 30. Risk (shifted, i.e. the PREVIOUS row's risk):
    D2 uses D1's risk (100), D3 uses D2's risk (90)."""
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1, D2, D3], risks=[Price, IRDeltaParallel], price_measure=Price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])
    bt.portfolio_dict[D3] = Portfolio([inst])
    bt._results[D1] = _prr(inst, 0.0, 100.0)
    bt._results[D2] = _prr(inst, 50.0, 90.0)
    bt._results[D3] = _prr(inst, 80.0, 95.0)
    return bt


def test_pnl_bps_hand_computation():
    bt = _three_date_backtest()
    pb = bt.pnl_bps(IRDeltaParallel)
    assert list(pb.columns) == ["PnL", "Risk", "PnL (bps)", "Cumulative PnL (bps)"]
    assert pb.loc[D2, "PnL"] == 50.0
    assert pb.loc[D2, "Risk"] == 100.0
    assert pb.loc[D2, "PnL (bps)"] == pytest.approx(50.0 / 100.0)
    assert pb.loc[D3, "PnL"] == 30.0
    assert pb.loc[D3, "Risk"] == 90.0
    assert pb.loc[D3, "PnL (bps)"] == pytest.approx(30.0 / 90.0)
    assert pb.loc[D3, "Cumulative PnL (bps)"] == pytest.approx(50.0 / 100.0 + 30.0 / 90.0)


def test_pnl_bps_first_row_is_nan_but_cumulative_treats_it_as_zero():
    bt = _three_date_backtest()
    pb = bt.pnl_bps(IRDeltaParallel)
    assert pb.loc[D1, "PnL"] != pb.loc[D1, "PnL"]  # NaN != NaN
    assert pb.loc[D1, "Cumulative PnL (bps)"] == 0.0


def test_pnl_bps_nan_below_min_abs_risk():
    """|risk| < min_abs_risk gives NaN, not a huge or infinite division result."""
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1, D2], risks=[Price, IRDeltaParallel], price_measure=Price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])
    bt._results[D1] = _prr(inst, 0.0, 1e-12)  # below the default min_abs_risk=1e-9
    bt._results[D2] = _prr(inst, 5.0, 1.0)
    pb = bt.pnl_bps(IRDeltaParallel)
    assert pb.loc[D2, "PnL (bps)"] != pb.loc[D2, "PnL (bps)"]  # NaN


def test_pnl_bps_custom_min_abs_risk_threshold():
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1, D2], risks=[Price, IRDeltaParallel], price_measure=Price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])
    bt._results[D1] = _prr(inst, 0.0, 0.5)
    bt._results[D2] = _prr(inst, 5.0, 1.0)
    below = bt.pnl_bps(IRDeltaParallel, min_abs_risk=1.0)
    assert below.loc[D2, "PnL (bps)"] != below.loc[D2, "PnL (bps)"]  # 0.5 < 1.0 -> NaN
    above = bt.pnl_bps(IRDeltaParallel, min_abs_risk=0.1)
    assert above.loc[D2, "PnL (bps)"] == pytest.approx(5.0 / 0.5)


def test_pnl_bps_missing_column_raises_value_error_with_hint():
    bt = _three_date_backtest()
    with pytest.raises(ValueError, match=r"is not a column of result_summary; add it to run_backtest"):
        bt.pnl_bps(Price(currency="EUR"))


def test_pnl_bps_result_ccy_retry_finds_the_parameterised_column():
    """If price_measure carries a currency (a result_ccy run) and the bare risk is absent,
    risk(currency=that) is retried before giving up."""
    inst = _swap()
    ccy_price = Price(currency="USD")
    ccy_delta = IRDeltaParallel(currency="USD")
    bt = BackTest(strategy=object(), states=[D1, D2], risks=[ccy_price, ccy_delta], price_measure=ccy_price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])
    bt._results[D1] = _prr(inst, 0.0, 100.0, price_measure=ccy_price, delta_measure=ccy_delta)
    bt._results[D2] = _prr(inst, 50.0, 90.0, price_measure=ccy_price, delta_measure=ccy_delta)
    pb = bt.pnl_bps(IRDeltaParallel)  # bare, unparameterised
    assert pb.loc[D2, "PnL (bps)"] == pytest.approx(50.0 / 100.0)


def test_pnl_bps_result_ccy_retry_still_raises_when_nothing_matches():
    ccy_price = Price(currency="USD")
    bt = BackTest(strategy=object(), states=[D1], risks=[ccy_price], price_measure=ccy_price)
    with pytest.raises(ValueError, match="is not a column of result_summary"):
        bt.pnl_bps(IRDeltaParallel)


def test_pnl_bps_non_scalar_measure_raises_value_error():
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1, D2], risks=[Price, IRDelta], price_measure=Price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])

    def prr_bucketed(price):
        portfolio = Portfolio([inst])
        frame = make_bucketed_frame({"2y": 5.0}, labels={"mkt_type": "IR"})
        mm = MultipleRiskMeasureResult(inst, {Price: FloatWithInfo(price, unit={"USD": 1}), IRDelta: frame})
        return PortfolioRiskResult(portfolio, (Price, IRDelta), [PricingFuture(mm)])

    bt._results[D1] = prr_bucketed(0.0)
    bt._results[D2] = prr_bucketed(50.0)
    with pytest.raises(ValueError, match="is not a scalar measure; use e.g. IRDeltaParallel"):
        bt.pnl_bps(IRDelta)
