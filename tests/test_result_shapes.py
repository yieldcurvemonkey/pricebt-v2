"""pricebt.backtests.backtest_objects.BackTest and its support objects (IMPLEMENTATION_PLAN.md
P3.2; DESIGN.md sections 7, 8.3, 9.3; research/03 in full).

BackTest is normally filled in by GenericEngine (not ported until P3.5), so every fixture here
builds the internal state BY HAND, exactly as research/03 section 9's worked examples do, and
checks the view methods (result_summary, risk_summary, trade_ledger, strategy_as_time_series,
pnl_explain) against that research note's own verified numbers. Case A/B/E names and the swap
setup (USD pay-fixed, 1mm notional, risks=[Price, IRDelta]) are research/03 section 9's own.
"""
from __future__ import annotations

import datetime as dt

import pytest

from pricebt.backtests.backtest_objects import (
    _BACKTEST_END,
    BackTest,
    CashPayment,
    Hedge,
    PnlAttribute,
    PnlDefinition,
    PredefinedAssetBacktest,
    ScalingPortfolio,
    WeightedScalingPortfolio,
    WeightedTrade,
    fx_pnl_definition,
)
from pricebt.errors import NotSupportedError
from pricebt.instrument import IRSwap
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import FXAnnualImpliedVol, FXDeltaLocalCcy, FXGammaLocalCcy, FXSpot, FXVegaLocalCcy, IRDelta, Price
from pricebt.risk.results import FloatWithInfo, MultipleRiskMeasureResult, PortfolioRiskResult, PricingFuture, make_bucketed_frame

D1 = dt.date(2024, 1, 2)
D2 = dt.date(2024, 1, 3)
D3 = dt.date(2024, 1, 4)


def _swap(name="Action1_swap_2024-01-02"):
    return IRSwap(
        pay_or_receive="Pay",
        notional_currency="USD",
        notional_amount=1000000,
        fixed_rate=0.04,
        termination_date="10y",
        name=name,
    )


def _prr(inst, price, ladder):
    """One instrument, Price + IRDelta, matching research/03 section 9's worked
    PortfolioRiskResult shape: one future per direct child of the portfolio, wrapping a
    MultipleRiskMeasureResult keyed by risk measure."""
    portfolio = Portfolio([inst])
    frame = make_bucketed_frame(ladder, labels={"mkt_type": "IR", "mkt_asset": "USD", "mkt_class": "OIS"})
    mm = MultipleRiskMeasureResult(inst, {Price: FloatWithInfo(price, unit={"USD": 1}), IRDelta: frame})
    return PortfolioRiskResult(portfolio, (Price, IRDelta), [PricingFuture(mm)])


# ----------------------------------------------------------------------------------------- Case A
# research/03 section 9 Case A: 2 dates, 1 trade held open (trade_duration=None, so the exit
# payment sits at dt.date.max and is never priced).


def _case_a():
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1, D2], risks=[Price, IRDelta], price_measure=Price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])
    bt._results[D1] = _prr(inst, 0.0, {"2y": 50.0, "10y": 800.0})
    bt._results[D2] = _prr(inst, 1500.0, {"2y": 49.0, "10y": 798.0})
    entry = CashPayment(inst, effective_date=D1, direction=-1)
    entry.cash_paid["USD"] = -0.0
    bt.cash_payments[D1] = [entry]
    bt.cash_payments[dt.date.max] = [CashPayment(inst, effective_date=dt.date.max, direction=1)]
    bt.cash_dict[D1] = {"USD": 0.0}
    bt.cash_dict[D2] = {"USD": 0.0}
    bt.transaction_costs = {D1: -0.0, dt.date.max: -0.0}
    return bt, inst


def test_case_a_result_summary_price_and_total():
    bt, _ = _case_a()
    rs = bt.result_summary
    assert list(rs.index) == [D1, D2]
    assert list(rs.columns) == [Price, IRDelta, "Cumulative Cash", "Transaction Costs", "Total"]
    assert rs[Price].tolist() == [0.0, 1500.0]
    assert rs["Total"].tolist() == [0.0, 1500.0]


def test_case_a_risk_summary_has_no_date_max_row():
    """DEV-R1: the still-open position's placeholder exit (cash_payments/transaction_costs at
    dt.date.max, never priced) must not surface as a spurious zero row in risk_summary -- it is
    not a real off-grid pricing date, and gs's own risk_summary never produces one here either
    (its zero_on_empty_dates block only iterates cash_dict, which has no dt.date.max key)."""
    bt, _ = _case_a()
    rsum = bt.risk_summary
    assert list(rsum.index) == [D1, D2]
    assert rsum[Price].tolist() == [0.0, 1500.0]


def test_case_a_bucketed_irdelta_cell_is_a_dataframe_with_two_buckets():
    bt, _ = _case_a()
    ladder_d1 = bt.result_summary[IRDelta].iloc[0]
    assert ladder_d1["mkt_point"].tolist() == ["2y", "10y"]
    assert ladder_d1["value"].tolist() == [50.0, 800.0]


def test_case_a_trade_ledger_still_open():
    bt, _ = _case_a()
    tl = bt.trade_ledger()
    assert list(tl.columns) == ["Open", "Close", "Open Value", "Close Value", "Long Short", "Status", "Trade PnL"]
    row = tl.loc["Action1_swap_2024-01-02"]
    assert row["Open"] == D1
    assert row["Close"] is None
    assert row["Status"] == "open"
    assert row["Trade PnL"] is None
    assert row["Long Short"] == -1


def test_case_a_strategy_as_time_series_shape_and_name_rename():
    """research/03 section 7/9: row MultiIndex (Pricing Date, Instrument Name) -- the 'name' column
    of Portfolio.to_frame() is renamed to 'Instrument Name'; bucketed IRDelta is summed per trade
    (50 + 800 = 850 on D1)."""
    bt, inst = _case_a()
    sats = bt.strategy_as_time_series()
    assert sats.index.names == ["Pricing Date", "Instrument Name"]
    assert set(c[0] for c in sats.columns) == {"Static Instrument Data", "Risk Measures", "Cash Payments"}
    row_d1 = sats.loc[(D1, inst.name)]
    assert row_d1[("Static Instrument Data", "asset_class")] == "Rates"
    assert row_d1[("Static Instrument Data", "notional_currency")] == "USD"
    assert row_d1[("Static Instrument Data", "quantity_")] == 1.0  # DEV-I2
    assert row_d1[("Risk Measures", "IRDelta")] == 850.0
    assert row_d1[("Risk Measures", "Price")] == 0.0
    assert row_d1[("Cash Payments", "Cash Ccy")] == "USD"
    row_d2 = sats.loc[(D2, inst.name)]
    assert row_d2[("Risk Measures", "IRDelta")] == 49.0 + 798.0


# ----------------------------------------------------------------------------------------- Case B
# research/03 section 9 Case B: entered D1 (PV 0, TC 5), exited D3 via ExitTradeAction (exit PV
# 2300, TC 5). D3 is a grid date on which the trade is NOT held (holding window create <= s <
# final, research/03 section 2.2), so portfolio_dict[D3] is empty -> flat (DEV-R1).


def _case_b():
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1, D2, D3], risks=[Price, IRDelta], price_measure=Price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])
    bt.portfolio_dict[D3] = Portfolio([])  # exited before D3
    bt._results[D1] = _prr(inst, 0.0, {"2y": 50.0, "10y": 800.0})
    bt._results[D2] = _prr(inst, 1500.0, {"2y": 49.0, "10y": 798.0})
    # D3: nothing held, nothing priced -- no entry in _results at all.
    entry = CashPayment(inst, effective_date=D1, direction=-1)
    entry.cash_paid["USD"] = -0.0
    exit_ = CashPayment(inst, effective_date=D3, direction=1)
    exit_.cash_paid["USD"] = 2300.0
    bt.cash_payments[D1] = [entry]
    bt.cash_payments[D3] = [exit_]
    bt.cash_dict[D1] = {"USD": 0.0}
    bt.cash_dict[D2] = {"USD": 0.0}
    bt.cash_dict[D3] = {"USD": 2300.0}
    bt.transaction_costs = {D1: -5.0, D3: -5.0}
    return bt, inst


def test_case_b_total_on_d3_is_2290_not_3790_dev_r1():
    """The economically correct Total on D3 is Price(0, flat) + Cumulative Cash(2300) +
    Transaction Costs(-10) = 2290. gs's own bug (research/03 section 4.4 quirk Q1) ffills the
    stale D2 Price (1500) onto D3, giving 3790 -- confirmed by disabling BackTest._flat_dates."""
    bt, _ = _case_b()
    rs = bt.result_summary
    assert rs.loc[D3, Price] == 0.0
    assert rs.loc[D3, "Cumulative Cash"] == 2300.0
    assert rs.loc[D3, "Transaction Costs"] == -10.0
    assert rs.loc[D3, "Total"] == 2290.0


def test_case_b_bucketed_irdelta_is_zero_on_flat_date_not_ffilled_dev_r4():
    bt, _ = _case_b()
    cell = bt.result_summary.loc[D3, IRDelta]
    assert cell == 0  # zeroed, not the D2 DataFrameWithInfo ladder


def test_case_b_risk_summary_zero_on_flat_date():
    """research/03 section 5: risk_summary shows D3 as Price 0, IRDelta 0."""
    bt, _ = _case_b()
    rsum = bt.risk_summary
    assert rsum.loc[D3, Price] == 0
    assert rsum.loc[D3, IRDelta] == 0
    assert rsum.loc[D1, Price] == 0.0
    assert rsum.loc[D2, Price] == 1500.0


def test_case_b_trade_ledger_closed():
    bt, _ = _case_b()
    row = bt.trade_ledger().loc["Action1_swap_2024-01-02"]
    assert row["Open"] == D1
    assert row["Close"] == D3
    assert row["Close Value"] == 2300.0
    assert row["Status"] == "closed"
    assert row["Trade PnL"] == 2300.0


def test_case_b_flat_date_disabled_reproduces_the_gs_bug():
    """Mutation check for DEV-R1: with _flat_dates forced empty (the pre-fix gs behaviour), D3's
    Total reverts to the stale-ffill value 3790, proving the fix in get_risk_summary_df is what
    makes the real test above pass."""
    bt, _ = _case_b()
    bt._flat_dates = lambda: set()
    assert bt.result_summary.loc[D3, "Total"] == 3790.0


# ----------------------------------------------------------------------------------------- Case E


def test_case_e_same_day_open_close_direction_zero():
    """research/03 section 9 Case E: a same-day open+close nets direction to 0."""
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1], risks=[Price, IRDelta], price_measure=Price)
    bt.cash_payments[D1] = [CashPayment(inst, effective_date=D1, direction=0)]
    row = bt.trade_ledger().loc["Action1_swap_2024-01-02"]
    assert row["Open"] == D1
    assert row["Close"] == D1
    assert row["Open Value"] == 0
    assert row["Close Value"] == 0
    assert row["Long Short"] == 0
    assert row["Status"] == "closed"
    assert row["Trade PnL"] == 0


# ------------------------------------------------------------------------------------- DEV-E14/R2


def test_dev_e14_result_summary_risk_columns_follow_self_risks_order():
    """DEV-E14: column order follows self.risks (an ordered list), not gs's non-deterministic set,
    and stays stable across repeated calls."""
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1, D2], risks=[IRDelta, Price], price_measure=Price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])
    bt._results[D1] = _prr(inst, 0.0, {"2y": 50.0})
    bt._results[D2] = _prr(inst, 1500.0, {"2y": 49.0})
    cols_first = list(bt.result_summary.columns)
    cols_second = list(bt.result_summary.columns)
    assert cols_first == [IRDelta, Price, "Cumulative Cash", "Transaction Costs", "Total"]
    assert cols_first == cols_second


def test_dev_r2_get_risk_summary_df_is_recomputed_not_cached():
    """DEV-R2: gs caches summary_dict forever in self._risk_summary_dict; pricebt recomputes it
    every call, so a later change to self.results is picked up by the next view call. D2 is held
    (portfolio_dict[D2] non-empty, so it is not a DEV-R1 flat date) but not yet priced, so it is
    genuinely absent -- not zeroed -- from the first call's frame."""
    inst = _swap()
    bt = BackTest(strategy=object(), states=[D1, D2], risks=[Price], price_measure=Price)
    bt.portfolio_dict[D1] = Portfolio([inst])
    bt.portfolio_dict[D2] = Portfolio([inst])
    bt._results[D1] = _prr(inst, 0.0, {})
    first = bt.get_risk_summary_df()
    assert D2 not in first.index

    bt._results[D2] = _prr(inst, 42.0, {})
    second = bt.get_risk_summary_df()
    assert D2 in second.index
    assert second.loc[D2, Price] == 42.0


# --------------------------------------------------------------------------------- multi-currency


def test_result_summary_multi_currency_cash_raises_with_hint():
    """DESIGN.md section 7 point 4: the gs RuntimeError, plus a hint naming the fix."""
    bt = BackTest(strategy=object(), states=[D1], risks=[Price], price_measure=Price)
    bt.cash_dict[D1] = {"USD": 1.0, "EUR": 2.0}
    with pytest.raises(RuntimeError, match="Cannot aggregate cash in multiple currencies"):
        bt.result_summary
    with pytest.raises(RuntimeError, match="pass result_ccy=..."):
        bt.result_summary


def test_result_summary_no_results_at_all_returns_empty_frame():
    """research/03 section 4.4 quirk Q4: 2.1.17 already guards this (df.empty check); a backtest
    with no results and no cash gives an empty result_summary, not a crash."""
    bt = BackTest(strategy=object(), states=[D1], risks=[Price], price_measure=Price)
    rs = bt.result_summary
    assert rs.empty


# --------------------------------------------------------------------------------- pnl_explain


def test_pnl_explain_none_when_no_definition():
    bt = BackTest(strategy=object(), states=[D1], risks=[Price], price_measure=Price)
    assert bt.pnl_explain() is None


def test_pnl_explain_hand_computation():
    """research/03 section 8: pnl += scaling_factor * prev_risk * (m1 - m0), cumulative from the
    second date onward."""
    inst = _swap()
    attr = PnlAttribute("delta", attribute_metric=Price, market_data_metric=IRDelta, scaling_factor=1.0)
    bt = BackTest(
        strategy=object(),
        states=[D1, D2],
        risks=[Price, IRDelta],
        price_measure=Price,
        pnl_explain_def=PnlDefinition(attributes=[attr]),
    )

    def prr_scalars(risk_val, mkt_val):
        portfolio = Portfolio([inst])
        mm = MultipleRiskMeasureResult(
            inst, {Price: FloatWithInfo(risk_val, unit={"USD": 1}), IRDelta: FloatWithInfo(mkt_val, unit={"USD": 1})}
        )
        return PortfolioRiskResult(portfolio, (Price, IRDelta), [PricingFuture(mm)])

    bt._results[D1] = prr_scalars(2.0, 10.0)  # attribute_metric(Price)=2.0, market_data_metric(IRDelta)=10.0
    bt._results[D2] = prr_scalars(3.0, 15.0)
    got = bt.pnl_explain()
    expect = 1.0 * 2.0 * (15.0 - 10.0)  # scaling_factor * prev risk * (m1 - m0)
    assert got == {"delta": {D2: expect}}


def test_fx_pnl_definition_shape():
    """fx_pnl_definition() is ported verbatim -- no DEV row."""
    pdef = fx_pnl_definition()
    names = [a.attribute_name for a in pdef.attributes]
    assert names == ["PNL_delta", "PNL_gamma", "VegaPnL"]
    delta, gamma, vega = pdef.attributes
    assert delta.attribute_metric is FXDeltaLocalCcy and delta.market_data_metric is FXSpot
    assert delta.second_order is False
    assert gamma.attribute_metric is FXGammaLocalCcy and gamma.second_order is True
    assert vega.attribute_metric is FXVegaLocalCcy and vega.market_data_metric is FXAnnualImpliedVol
    assert vega.scaling_factor == 100.0
    assert pdef.get_risks() == [FXDeltaLocalCcy, FXSpot, FXGammaLocalCcy, FXSpot, FXVegaLocalCcy, FXAnnualImpliedVol]


# --------------------------------------------------------------------------------- other support objects


def test_cash_payment_to_frame_one_row_per_currency():
    inst = _swap()
    cp = CashPayment(inst, effective_date=D1, direction=-1)
    cp.cash_paid["USD"] = -100.0
    df = cp.to_frame()
    assert list(df.columns) == ["Cash Ccy", "Cash Amount", "Instrument Name", "Pricing Date"]
    assert df["Cash Ccy"].tolist() == ["USD"]
    assert df["Cash Amount"].tolist() == [-100.0]
    assert df["Instrument Name"].tolist() == [inst.name]
    assert df["Pricing Date"].tolist() == [D1]


def test_cash_payment_to_frame_no_rows_when_never_priced():
    inst = _swap()
    cp = CashPayment(inst, effective_date=dt.date.max, direction=1)
    assert len(cp.to_frame()) == 0


def test_scaling_portfolio_and_hedge_construction():
    inst = _swap()
    sp = ScalingPortfolio(inst, dates=[D1, D2], risk=Price, risk_percentage=50)
    assert sp.trade is inst and sp.dates == [D1, D2] and sp.risk is Price and sp.risk_percentage == 50
    assert sp.results is None
    entry = CashPayment(inst, effective_date=D1, direction=-1)
    hedge = Hedge(sp, entry, None)
    assert hedge.scaling_portfolio is sp and hedge.entry_payment is entry and hedge.exit_payment is None


def test_weighted_scaling_portfolio_and_weighted_trade_construction():
    portfolio = Portfolio([_swap("a"), _swap("b")])
    wsp = WeightedScalingPortfolio(portfolio, dates=[D1], risk=Price, total_size=100000.0)
    assert wsp.trades is portfolio and wsp.total_size == 100000.0 and wsp.results is None
    entries = [CashPayment(portfolio, effective_date=D1, direction=-1)]
    wt = WeightedTrade(wsp, entries, [None])
    assert wt.scaling_portfolio is wsp and wt.entry_payments is entries and wt.exit_payments == [None]


def test_predefined_asset_backtest_is_a_stub():
    """DESIGN.md section 2.3/9.3: PredefinedAssetEngine is out of scope; the name must exist
    (triggers.py imports it) but construction raises."""
    with pytest.raises(NotSupportedError):
        PredefinedAssetBacktest(data_handler=None, initial_value=0.0)


def test_backtest_end_context_var_defaults_to_none():
    assert _BACKTEST_END.get() is None
