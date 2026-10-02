"""P&L decomposition for swaptions and bonds (docs/v2/IR_RISK_DESIGN.md section 7 as amended by
section 00 R2-15..R2-19; research note R15 for gs pnl_explain semantics): PnlAttribute's appended
fields (DEV-E19 cross metric, DEV-E21 unit check), the shared step iterator behind pnl_explain() and
pnl_explain_table(), the ir/swaption/bond definitions, the DEV-R11 views and DEV-E20 off-grid exits.

Every scenario is a real GenericEngine run on the toy configs; each run is module-scoped and shared
by its assertions (R2-19: this file stays under 15 s). Every residual bound is computed HERE, by
finite differences on the toy pricing functions at t-1, never read back from the engine.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import pricebt.backtests.actions as _actions
import toylib.bond as tb
import toylib.rates as tr
import toylib.swaption as ts
from pricebt.backtests.actions import AddScaledTradeAction, AddTradeAction, HedgeAction, ScalingActionType
from pricebt.backtests.backtest_objects import (
    BackTest,
    PnlAttribute,
    PnlDefinition,
    bond_pnl_definition,
    ir_pnl_definition,
    swaption_pnl_definition,
)
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements, PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.common import AggregationLevel
from pricebt.errors import ConfigError
from pricebt.instrument import Bond, IRSwap, IRSwaption
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import (
    Cashflows,
    ExpiryInYears,
    IRAnnualImpliedVol,
    IRDeltaParallel,
    IRFwdRate,
    IRGammaParallel,
    IRVanna,
    IRVegaParallel,
    IRVolga,
    Price,
    Theta,
)
from pricebt.risk.results import FloatWithInfo, MultipleRiskMeasureResult, PortfolioRiskResult, PricingFuture, make_table_frame
from pricebt.session import GsSession, PricebtSession

ASSETS = Path(__file__).parent / "assets"
N = 1_000_000.0
D0 = date(2024, 1, 2)
SWAPTION_END = date(2024, 5, 31)  # (a): ~5 months daily; the 1y expiry is 2025-01-02
HEDGE_END = date(2024, 3, 28)  # (c): <= 3 months of '1b' hedges (R2-19)
SHORT_END = date(2024, 2, 1)  # (d): short runs compared row by row with (a)
BOND_ID = "TOY 4.25 2034-11-15"
BOND_START, BOND_END = date(2024, 5, 6), date(2024, 11, 22)
# a Wednesday and a Friday: each coupon ends a one-day step, so Theta(t-1) (one calendar day,
# the day's coupon counted as cash, DEV-I15) covers exactly the step it is attributed over
COUPONS = (date(2024, 5, 15), date(2024, 11, 15))
VOL_COLUMNS = ["VegaPnL", "PNL_vanna", "PNL_volga"]
SIX = ["PNL_delta", "PNL_gamma", "VegaPnL", "PNL_vanna", "PNL_volga", "PNL_theta"]


@pytest.fixture(scope="module", autouse=True)
def _module_globals():
    """Module-scoped runs set the session outside conftest's per-test `isolation` window (see
    test_040304_toy.py); put it back after the module so nothing leaks into later files."""
    saved = (PricebtSession.current, GsSession.current, _actions.action_count)
    yield
    PricebtSession.current, GsSession.current, _actions.action_count = saved


# =============================================================================== helpers


def _run(assets, triggers, end, start=D0, frequency="1b", risks=None, pnl_explain=None):
    PricebtSession.use(assets=[a if isinstance(a, Path) else ASSETS / a for a in assets])
    strategy = Strategy(initial_portfolio=None, triggers=triggers)
    return GenericEngine().run_backtest(
        strategy, start=start, end=end, frequency=frequency, risks=risks, pnl_explain=pnl_explain, show_progress=False
    )


def _on(d, *actions):
    return DateTrigger(DateTriggerRequirements(dates=[d]), actions=list(actions))


def _swaption(buy_sell="Buy", pay_or_receive="Pay", strike="ATM", name="swaption"):
    return IRSwaption(
        pay_or_receive, "10y", "USD", notional_amount=N, expiration_date="1y", strike=strike, buy_sell=buy_sell, name=name
    )


def _table(bt):
    """pnl_explain_table, with the no-NaN rule every run must pass (R2-7: one NaN poisons every
    later cumulative value)."""
    table = bt.pnl_explain_table()
    assert len(table) > 0
    assert np.isfinite(table.to_numpy(dtype=float)).all(), table
    for series in bt.pnl_explain().values():
        assert np.isfinite([float(v) for v in series.values()]).all()
    return table


def _steps(bt):
    """(prev, cur) pairs over the union of result and exit-result dates (pnl_explain's axis)."""
    dates = sorted(set(bt.results) | set(bt.trade_exit_risk_results))
    return list(zip(dates, dates[1:]))


def _z_for(rate_bp, target, z):
    """Newton: the flat zero rate whose own rate is `target` bp."""
    for _ in range(30):
        f = rate_bp(z) - target
        if abs(f) < 1e-11:
            break
        z -= f / ((rate_bp(z + 1e-7) - rate_bp(z - 1e-7)) / 2e-7)
    return z


def _taylor_bound(pv, rate_bp, d0, d1, z0, z1, s0, s1, notional):
    """IR_RISK_DESIGN R2-18's residual bound for one unit position over the step d0 -> d1.

    On the flat toy curve (one zero rate z) the own rate F = rate_bp(z) does not depend on the
    pricing date (the exp(z t/365) factor cancels), so the PV is a function W(F, s, t) =
    pv(z(F), s, t) with F and the normal vol s in bp and t in calendar days; moving t with z fixed
    is exactly the translated curve Theta uses (R2-4). Every derivative is a central finite
    difference of W at (F0, s0, d0), steps 1bp / 1bp / 1 day:

      |resid| <= 2 (|W_FFF| |dF|^3/6 + |W_FFs| dF^2 |ds|/2 + |W_Fss| |dF| ds^2/2 + |W_sss| |ds|^3/6
                    + |charm| |dF| dt + |veta| |ds| dt + |Theta_t| dt^2/2) + 1e-9 |N|

    charm = W_Ft, veta = W_st, Theta_t = W_tt (per day^2), dt = (d1 - d0).days. A PV with no vol
    dependence gets exactly 0 for every s term."""
    F0, F1 = rate_bp(z0), rate_bp(z1)
    dF, ds, dt = F1 - F0, s1 - s0, (d1 - d0).days

    def w(i, j, days=0):
        return pv(_z_for(rate_bp, F0 + i, z0), s0 + j, d0 + timedelta(days=days))

    w_fff = (w(2, 0) - 2 * w(1, 0) + 2 * w(-1, 0) - w(-2, 0)) / 2
    w_sss = (w(0, 2) - 2 * w(0, 1) + 2 * w(0, -1) - w(0, -2)) / 2
    w_ffs = ((w(1, 1) - 2 * w(0, 1) + w(-1, 1)) - (w(1, -1) - 2 * w(0, -1) + w(-1, -1))) / 2
    w_fss = ((w(1, 1) - 2 * w(1, 0) + w(1, -1)) - (w(-1, 1) - 2 * w(-1, 0) + w(-1, -1))) / 2
    charm = ((w(1, 0, 1) - w(-1, 0, 1)) - (w(1, 0) - w(-1, 0))) / 2
    veta = ((w(0, 1, 1) - w(0, -1, 1)) - (w(0, 1) - w(0, -1))) / 2
    theta_t = w(0, 0, 2) - 2 * w(0, 0, 1) + w(0, 0)
    third = (
        abs(w_fff) * abs(dF) ** 3 / 6
        + abs(w_ffs) * dF**2 * abs(ds) / 2
        + abs(w_fss) * abs(dF) * ds**2 / 2
        + abs(w_sss) * abs(ds) ** 3 / 6
    )
    time = abs(charm) * abs(dF) * dt + abs(veta) * abs(ds) * dt + abs(theta_t) * dt**2 / 2
    return 2 * (third + time) + 1e-9 * abs(notional)


def _position_bound(inst, d0, d1):
    """_taylor_bound of one held toy swaption or toy swap (pricebt applies quantity_ to the unit trade)."""
    terms = inst.resolved_terms
    z0, z1 = tr._zero_rate(d0, "USD"), tr._zero_rate(d1, "USD")
    s0, s1 = ts._sigma(d0, "USD") * 1e4, ts._sigma(d1, "USD") * 1e4
    if isinstance(inst, IRSwaption):
        start, end, unit = terms["expiration_date"], terms["termination_date"], abs(terms["notional"])

        def pv(z, s, d):
            return ts._value(tr.ToyCurve(d, "USD", z), s * 1e-4, terms)

    else:
        swap = tr.build_swap(None, terms)
        start, end, unit = swap.effective_date, swap.termination_date, abs(swap.notional)

        def pv(z, s, d):
            return tr.npv(tr.ToyCurve(d, "USD", z), swap)

    def rate_bp(z):
        return tr._par_rate(tr.ToyCurve(d0, "USD", z), start, end) * 1e4

    return abs(inst.quantity_) * _taylor_bound(pv, rate_bp, d0, d1, z0, z1, s0, s1, unit)


def _book_bound(bt, d0, d1):
    """Sum of the held positions' bounds: the table is a sum over the held book of per-position
    terms, so the book's residual is the sum of the positions' residuals."""
    return sum(_position_bound(inst, d0, d1) for inst in bt.results[d0].portfolio.all_instruments)


def _gs_pnl_explain(self):
    """gs_quant 2.1.17 BackTest.pnl_explain (research R15 section 3.2), verbatim as pricebt ported
    it before the step iterator: the reference the refactored pnl_explain() must reproduce exactly
    for every attribute gs can express (IR_RISK_DESIGN R2-17)."""
    if self.pnl_explain_def is None:
        return None

    risk_results = self.results
    exit_risk_results = self.trade_exit_risk_results
    dates = sorted(set(risk_results.keys()).union(exit_risk_results.keys()))

    pnl_explain_results = {}

    for attribute in self.pnl_explain_def.attributes:
        result = {}
        cum_total = 0.0
        for idx in range(1, len(dates)):
            metric_pnl = 0.0
            cur_date = dates[idx]
            prev_date = dates[idx - 1]
            if prev_date not in risk_results:
                result[cur_date] = cum_total
                continue
            for prev_date_inst in risk_results[prev_date].portfolio.all_instruments:
                prev_date_risk = risk_results[prev_date][prev_date_inst][attribute.attribute_metric]
                if prev_date_risk == 0:
                    continue
                prev_date_mkt_data = risk_results[prev_date][prev_date_inst][attribute.market_data_metric]
                if cur_date in risk_results and prev_date_inst in risk_results[cur_date].portfolio:
                    cur_date_mkt_data = risk_results[cur_date][prev_date_inst][attribute.market_data_metric]
                else:
                    cur_date_mkt_data = exit_risk_results[cur_date][prev_date_inst][attribute.market_data_metric]
                if attribute.second_order:
                    metric_pnl += (
                        0.5
                        * attribute.scaling_factor
                        * prev_date_risk
                        * (cur_date_mkt_data - prev_date_mkt_data)
                        * (cur_date_mkt_data - prev_date_mkt_data)
                    )
                else:
                    metric_pnl += attribute.scaling_factor * prev_date_risk * (cur_date_mkt_data - prev_date_mkt_data)
            cum_total += metric_pnl
            result[cur_date] = cum_total
        pnl_explain_results[attribute.attribute_name] = result
    return pnl_explain_results


def _assert_same_as_gs_loop(bt):
    """pnl_explain() == the verbatim gs loop, bit for bit, for every attribute without a cross
    metric (gs has no cross term); returns how many attributes were compared."""
    new, old = bt.pnl_explain(), _gs_pnl_explain(bt)
    compared = 0
    for attribute in bt.pnl_explain_def.attributes:
        if attribute.cross_market_data_metric is None:
            name = attribute.attribute_name
            assert list(new[name]) == list(old[name])
            assert [float(v) for v in new[name].values()] == [float(v) for v in old[name].values()], name
            compared += 1
    return compared


# =============================================================================== module runs


@pytest.fixture(scope="module")
def base():
    """(a): a long 1y10y ATM payer bought on D0 and held daily to SWAPTION_END."""
    add = _on(D0, AddTradeAction(_swaption(), name="Add"))
    bt = _run(["toy_usd_swaption.yaml"], [add], SWAPTION_END, pnl_explain=swaption_pnl_definition())
    return bt, _table(bt)


@pytest.fixture(scope="module")
def variants():
    """(d): the (a) swaption scaled by 2.5, sold, and as a receiver, each over D0..SHORT_END."""
    scaled = AddScaledTradeAction(_swaption(), None, scaling_type=ScalingActionType.size, scaling_level=2.5, name="Add")
    books = {
        "scaled": scaled,
        "sell": AddTradeAction(_swaption(buy_sell="Sell"), name="Add"),
        "receiver": AddTradeAction(_swaption(pay_or_receive="Receive"), name="Add"),
    }
    return {
        k: _table(_run(["toy_usd_swaption.yaml"], [_on(D0, a)], SHORT_END, pnl_explain=swaption_pnl_definition()))
        for k, a in books.items()
    }


@pytest.fixture(scope="module")
def hedged():
    """(c): the (a) swaption plus a daily HedgeAction(IRDeltaParallel) in a 10y toy_usd_irs_full
    swap held for one business day ('1b', 4-Delta Hedging's layout), to HEDGE_END."""
    hedge = HedgeAction(IRDeltaParallel, IRSwap("Pay", "10y", "USD", N, fixed_rate="ATM", name="hedge"), "1b", name="Hedge")
    hedges = PeriodicTrigger(PeriodicTriggerRequirements(start_date=D0, end_date=HEDGE_END, frequency="1b"), [hedge])
    bt = _run(
        ["toy_usd_swaption.yaml", "toy_usd_irs_full.yaml"],
        [_on(D0, AddTradeAction(_swaption(), name="Add")), hedges],
        HEDGE_END,
        pnl_explain=swaption_pnl_definition(),
    )
    return bt, _table(bt)


@pytest.fixture(scope="module")
def bond():
    """(b)/(h): a long toy bond held from BOND_START across both COUPONS, Cashflows among the risks."""
    b = Bond(identifier=BOND_ID, size=N, buy_sell="Buy", settlement_currency="USD", name="bond")
    add = _on(BOND_START, AddTradeAction(b, name="Add"))
    bt = _run(
        ["toy_usd_bond.yaml"], [add], BOND_END, start=BOND_START, risks=[Cashflows], pnl_explain=bond_pnl_definition()
    )
    return bt, _table(bt)


# =============================================================================== PnlAttribute / definitions


def test_pnl_attribute_appends_optional_fields_and_get_risks_omits_none_dev_e19_e21():
    delta = PnlAttribute("d", IRDeltaParallel, IRFwdRate, 1.0)  # gs positional call unchanged
    assert (delta.cross_market_data_metric, delta.market_data_unit, delta.cross_market_data_unit) == (None, None, None)
    assert delta.get_risks() == [IRDeltaParallel, IRFwdRate]
    vanna = IRVanna(aggregation_level=AggregationLevel.Type)
    cross = PnlAttribute("v", vanna, IRFwdRate, 1.0, False, IRAnnualImpliedVol, "bp", "bp")
    assert cross.get_risks() == [vanna, IRFwdRate, IRAnnualImpliedVol]
    assert PnlDefinition([delta, cross]).get_risks() == [IRDeltaParallel, IRFwdRate, vanna, IRFwdRate, IRAnnualImpliedVol]


def test_second_order_with_a_cross_metric_raises():
    with pytest.raises(ValueError, match="second_order cannot be combined with cross_market_data_metric"):
        PnlAttribute("v", IRVanna, IRFwdRate, 1.0, second_order=True, cross_market_data_metric=IRAnnualImpliedVol)


def test_ir_definitions_table_and_unit_factors():
    names = [a.attribute_name for a in swaption_pnl_definition().attributes]
    assert names == SIX
    assert [a.attribute_name for a in bond_pnl_definition().attributes] == ["PNL_delta", "PNL_gamma", "PNL_theta"]
    swap_like = ir_pnl_definition(vega=False, vanna=False, volga=False, theta=False)
    assert [a.attribute_name for a in swap_like.attributes] == ["PNL_delta", "PNL_gamma"]

    d, g, v, va, vo, th = ir_pnl_definition("decimal", "pct").attributes
    vanna, volga = IRVanna(aggregation_level=AggregationLevel.Type), IRVolga(aggregation_level=AggregationLevel.Type)
    def shape(a):
        return a.attribute_metric, a.market_data_metric, a.cross_market_data_metric, a.scaling_factor, a.second_order

    assert shape(d) == (IRDeltaParallel, IRFwdRate, None, 1e4, False)
    assert shape(g) == (IRGammaParallel, IRFwdRate, None, 1e8, True)
    assert shape(v) == (IRVegaParallel, IRAnnualImpliedVol, None, 100.0, False)
    assert shape(va) == (vanna, IRFwdRate, IRAnnualImpliedVol, 1e6, False)
    assert shape(vo) == (volga, IRAnnualImpliedVol, None, 1e4, True)
    assert shape(th) == (Theta, ExpiryInYears, None, -365.0, False)
    units = [(a.market_data_unit, a.cross_market_data_unit) for a in (d, g, v, va, vo, th)]
    assert units == [("decimal", None), ("decimal", None), ("pct", None), ("decimal", "pct"), ("pct", None), (None, None)]
    assert [a.scaling_factor for a in swaption_pnl_definition().attributes] == [1.0, 1.0, 1.0, 1.0, 1.0, -365.0]


@pytest.mark.parametrize(
    "call", [lambda: ir_pnl_definition("bps"), lambda: swaption_pnl_definition("bp", "vol"), lambda: bond_pnl_definition("percent")]
)
def test_invalid_unit_raises(call):
    with pytest.raises(ValueError, match="must be one of"):
        call()


# =============================================================================== hand-built known answers


def _prr(values, measures):
    """PortfolioRiskResult of {instrument: {measure: value}} (levels in bp, risks in USD); a
    FloatWithInfo or a table passes through as given."""
    insts = list(values)

    def wrap(m, v):
        if isinstance(v, (FloatWithInfo, pd.DataFrame)):
            return v
        return FloatWithInfo(v, unit={"bp": 1} if m in (IRFwdRate, IRAnnualImpliedVol) else {"USD": 1})

    futures = [PricingFuture(MultipleRiskMeasureResult(i, {m: wrap(m, values[i][m]) for m in measures})) for i in insts]
    return PortfolioRiskResult(Portfolio(insts), tuple(measures), futures)


D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)
A, B = IRSwap("Pay", "10y", "USD", N, name="A"), IRSwap("Receive", "5y", "USD", N, name="B")
VANNA = IRVanna(aggregation_level=AggregationLevel.Type)
MEASURES = (IRDeltaParallel, IRGammaParallel, VANNA, IRFwdRate, IRAnnualImpliedVol, Price)


def _hand_backtest(attributes):
    """R15 S1 extended with a vol level: delta 100, gamma 10, vanna 3; F 200 -> 201 -> 203 bp;
    vol 80 -> 82 -> 81 bp."""
    bt = BackTest(object(), [D1, D2, D3], list(MEASURES), Price, pnl_explain_def=PnlDefinition(attributes))
    for d, f, v in ((D1, 200.0, 80.0), (D2, 201.0, 82.0), (D3, 203.0, 81.0)):
        a = {IRDeltaParallel: 100.0, IRGammaParallel: 10.0, VANNA: 3.0, IRFwdRate: f, IRAnnualImpliedVol: v, Price: 0.0}
        bt._results[d] = _prr({A: a}, MEASURES)
    return bt


def test_cross_term_known_answer_and_first_and_second_order_match_gs_s1_dev_e19():
    bt = _hand_backtest(
        [
            PnlAttribute("delta", IRDeltaParallel, IRFwdRate, 1.0),
            PnlAttribute("gamma", IRGammaParallel, IRFwdRate, 1.0, second_order=True),
            PnlAttribute("vanna", VANNA, IRFwdRate, 1.0, cross_market_data_metric=IRAnnualImpliedVol),
        ]
    )
    # delta 100*1, then +100*2; gamma 0.5*10*1^2, then +0.5*10*2^2; vanna 3*1*2, then +3*2*(-1)
    assert bt.pnl_explain() == {"delta": {D2: 100.0, D3: 300.0}, "gamma": {D2: 5.0, D3: 25.0}, "vanna": {D2: 6.0, D3: 0.0}}
    assert _assert_same_as_gs_loop(bt) == 2
    table = _table(bt)
    fixed = ["actual_pnl", "cashflow_pnl", "economic_pnl"]
    assert list(table.columns) == fixed + ["delta", "gamma", "vanna", "explained_pnl", "residual_pnl"]
    assert list(table["explained_pnl"]) == [111.0, 214.0]
    assert list(table["residual_pnl"]) == [-111.0, -214.0]  # Price is flat at 0 here


def test_skipped_step_and_exit_results_match_gs_s6():
    """R15 S6: an exit-only date as prev skips the step for everyone (gs; pricebt's engine never
    produces it for a continuing position because of DEV-R1, but the loop keeps gs's rule)."""
    measures = (IRDeltaParallel, IRFwdRate, Price)
    definition = PnlDefinition([PnlAttribute("delta", IRDeltaParallel, IRFwdRate, 1.0)])
    bt = BackTest(object(), [D1, D3], list(measures), Price, pnl_explain_def=definition)

    def row(risk, level, price):
        return {IRDeltaParallel: risk, IRFwdRate: level, Price: price}

    bt._results[D1] = _prr({A: row(100.0, 200.0, 1.0), B: row(-50.0, 300.0, 2.0)}, measures)
    bt._trade_exit_risk_results[D2] = _prr({A: row(0.0, 201.0, 4.0), B: row(0.0, 301.0, 8.0)}, measures)
    bt._results[D3] = _prr({B: row(-50.0, 302.0, 16.0)}, measures)
    assert bt.pnl_explain() == {"delta": {D2: 50.0, D3: 50.0}}
    assert _assert_same_as_gs_loop(bt) == 1
    table = _table(bt)  # the same held set: A and B priced at their exit on D2, then nothing
    assert table["actual_pnl"].tolist() == [(4.0 - 1.0) + (8.0 - 2.0), 0.0]
    assert table["delta"].tolist() == [50.0, 0.0]


def test_unit_check_names_attribute_measure_instrument_and_units_dev_e21():
    ok = _hand_backtest([PnlAttribute("delta", IRDeltaParallel, IRFwdRate, 1.0, market_data_unit="bp")])
    assert ok.pnl_explain() == {"delta": {D2: 100.0, D3: 300.0}}
    bad = _hand_backtest(
        [PnlAttribute("vanna", VANNA, IRFwdRate, 1.0, False, IRAnnualImpliedVol, market_data_unit="bp", cross_market_data_unit="decimal")]
    )
    expected = r"vanna: IRAnnualImpliedVol on A has unit \{'bp': 1\}; the definition expects decimal"
    with pytest.raises(ValueError, match=expected):
        bad.pnl_explain()


def test_no_definition_gives_no_table():
    assert BackTest(strategy=object(), states=[D1], risks=[Price], price_measure=Price).pnl_explain_table() is None


def test_zero_risk_never_reads_its_level_gs():
    """gs skips a position whose risk is 0 before reading its level. Here the level's unit is wrong,
    so reading it would raise (DEV-E21): only the skip keeps both calls working."""
    measures = (IRVegaParallel, IRAnnualImpliedVol, Price)
    vega = PnlAttribute("vega", IRVegaParallel, IRAnnualImpliedVol, 1.0, market_data_unit="bp")
    bt = BackTest(object(), [D1, D2], list(measures), Price, pnl_explain_def=PnlDefinition([vega]))
    for d, vol in ((D1, 0.0080), (D2, 0.0082)):
        level = FloatWithInfo(vol, unit={"decimal": 1})
        bt._results[d] = _prr({A: {IRVegaParallel: 0.0, IRAnnualImpliedVol: level, Price: 0.0}}, measures)
    assert bt.pnl_explain() == {"vega": {D2: 0.0}}
    assert _table(bt)["vega"].tolist() == [0.0]


@pytest.mark.parametrize("names", [["actual_pnl"], ["x", "x"]])
def test_table_rejects_attribute_names_that_clash(names):
    """A name equal to a fixed column, or to another attribute's, would drop or double-count a
    column in explained_pnl."""
    bt = _hand_backtest([PnlAttribute(n, IRDeltaParallel, IRFwdRate, float(k)) for k, n in enumerate(names, 1)])
    with pytest.raises(ValueError, match=r"PnlAttribute names must be unique .*; got \['(actual_pnl|x)'\]"):
        bt.pnl_explain_table()


def test_table_rejects_prices_in_different_currencies_but_pnl_explain_keeps_gs():
    """result_summary refuses to add a USD and a EUR Price on one date; so does the table.
    pnl_explain() is gs's loop, which never looks at units."""
    measures = (IRDeltaParallel, IRFwdRate, Price)
    definition = PnlDefinition([PnlAttribute("delta", IRDeltaParallel, IRFwdRate, 1.0)])
    bt = BackTest(object(), [D1, D2], list(measures), Price, pnl_explain_def=definition)
    for d, pv in ((D1, 0.0), (D2, 100.0)):
        rows = {i: {IRDeltaParallel: 1.0, IRFwdRate: 200.0, Price: FloatWithInfo(pv, unit={c: 1})} for i, c in ((A, "USD"), (B, "EUR"))}
        bt._results[d] = _prr(rows, measures)
    with pytest.raises(ValueError, match="different units"):
        bt.result_summary
    with pytest.raises(ValueError, match=r"different units on 2024-01-03: Price in \['EUR', 'USD'\]"):
        bt.pnl_explain_table()
    assert bt.pnl_explain() == {"delta": {D2: 0.0}}


def _cash_backtest(flows, price_unit="USD"):
    """A held D1 -> D2 -> D3, Price flat at 0; its Cashflows table on each date is
    flows[date] = [(payment_date, payment_amount, currency), ...]."""
    measures = (IRDeltaParallel, IRFwdRate, Price, Cashflows)
    definition = PnlDefinition([PnlAttribute("delta", IRDeltaParallel, IRFwdRate, 1.0)])
    bt = BackTest(object(), [D1, D2, D3], list(measures), Price, pnl_explain_def=definition)
    for d in (D1, D2, D3):
        rows = [{"payment_date": p, "payment_amount": a, "currency": c, "payment_type": "Fixed"} for p, a, c in flows.get(d, [])]
        cash = make_table_frame(rows, unit={"USD": 1}, scale_columns=["payment_amount"])
        price = FloatWithInfo(0.0, unit={price_unit: 1})
        bt._results[d] = _prr({A: {IRDeltaParallel: 0.0, IRFwdRate: 200.0, Price: price, Cashflows: cash}}, measures)
    return bt


def test_cashflow_pnl_counts_the_flows_paid_in_the_open_closed_step_window():
    """(t-1, t]: a flow dated t-1 is not counted (R2-6 keeps it out of a config's table, but nothing
    enforces that), nor one dated after t."""
    flows = {
        D1: [(D1, 1.0, "USD"), (D2, 10.0, "USD"), (D3, 100.0, "USD")],
        D2: [(D2, 1000.0, "USD"), (D3, 100.0, "USD")],
    }
    table = _table(_cash_backtest(flows))
    assert table["cashflow_pnl"].tolist() == [10.0, 100.0]
    assert table["economic_pnl"].tolist() == [10.0, 100.0]


def test_table_rejects_a_due_cashflow_in_another_currency_than_the_price():
    bt = _cash_backtest({D1: [(D3, 5.0, "EUR")], D2: [(D3, 5.0, "EUR")]})
    with pytest.raises(ValueError, match=r"different units on 2024-01-04: Price in \['USD'\], Cashflows paid in \['EUR'\]"):
        bt.pnl_explain_table()


# =============================================================================== (a) swaption bought and held


def test_a_swaption_residual_within_the_taylor_bound_every_step(base):
    bt, table = base
    swaption = bt.results[D0].portfolio.all_instruments[0]
    assert swaption.resolved_terms["expiration_date"] > SWAPTION_END + timedelta(days=2)  # R2-18: no step near expiry
    steps = _steps(bt)
    assert [cur for _, cur in steps] == list(table.index) and len(steps) > 100
    bounds = np.array([_book_bound(bt, d0, d1) for d0, d1 in steps])
    residual = table["residual_pnl"].to_numpy()
    assert (np.abs(residual) <= bounds).all(), [(d1, r, b) for (d0, d1), r, b in zip(steps, residual, bounds) if abs(r) > b]
    # the bound has teeth: leaving the vega attribution out breaks it on some step
    assert (np.abs(residual + table["VegaPnL"].to_numpy()) > bounds).any()


def test_a_all_six_terms_cut_the_rms_residual_tenfold_versus_delta_only(base):
    _, table = base
    rms = lambda s: math.sqrt(float((s**2).mean()))  # noqa: E731
    assert rms(table["residual_pnl"]) * 10 <= rms(table["economic_pnl"] - table["PNL_delta"])


def test_a_volga_column_is_half_the_fd_volga_times_the_squared_vol_move(base):
    """Known answer per step, as (i) checks delta: PNL_volga = 1/2 V_ss(t-1) ds^2, V_ss the central
    second difference (1bp steps) of the toy PV in the normal vol at t-1 and ds the vol move in bp.
    The toy's daily vol moves are too small for R2-18's bound to see a missing volga term, so this
    is what pins the second-order vol attribution and its IRVolga / IRAnnualImpliedVol reads."""
    bt, table = base
    inst = bt.results[D0].portfolio.all_instruments[0]
    expected = []
    for d0, d1 in _steps(bt):
        curve, s0 = tr.ToyCurve(d0, "USD", tr._zero_rate(d0, "USD")), ts._sigma(d0, "USD")
        pv = lambda j: ts._value(curve, s0 + j * 1e-4, inst.resolved_terms)  # noqa: E731
        ds = (ts._sigma(d1, "USD") - s0) * 1e4
        expected.append(0.5 * inst.quantity_ * (pv(1) + pv(-1) - 2 * pv(0)) * ds**2)
    assert np.abs(expected).max() > 0.01
    assert table["PNL_volga"].to_numpy() == pytest.approx(expected, rel=1e-9, abs=1e-12)


# =============================================================================== (b) bond across two coupons


def _bond_bound(terms, quantity, d0, d1, y0, y1):
    """R2-18 for a bond (no vol): W(y, t) = sum of the flows paid after t of cf exp(-y tau), y in bp,
    tau = days/365 (on the flat toy world PV depends on z + s only, and delta bumps z with s
    fixed, so y is the own rate). Theta_t is taken on the carry C(k) = W(y0, d0 + k) + the flows paid
    in (d0, d0 + k], continuous across a coupon (the step's cash is in economic_pnl):
      |resid| <= 2 (|W_yyy| |dy|^3/6 + |W_yt| |dy| dt + |Theta_t| dt^2/2) + 1e-9 |face|"""

    def w(dy, days=0):
        d = d0 + timedelta(days=days)
        y = (y0 + dy) * 1e-4
        return sum(a * math.exp(-y * (p - d).days / 365.0) for p, a, *_ in tb._flows(terms, d))

    def carry(days):
        return w(0, days) + sum(a for p, a, *_ in tb._flows(terms, d0) if p <= d0 + timedelta(days=days))

    dy, dt = y1 - y0, (d1 - d0).days
    w_yyy = (w(2) - 2 * w(1) + 2 * w(-1) - w(-2)) / 2
    charm = ((w(1, 1) - w(-1, 1)) - (w(1) - w(-1))) / 2
    theta_t = carry(2) - 2 * carry(1) + carry(0)
    per_unit = 2 * (abs(w_yyy) * abs(dy) ** 3 / 6 + abs(charm) * abs(dy) * dt + abs(theta_t) * dt**2 / 2)
    return abs(quantity) * (per_unit + 1e-9 * abs(terms["face"]))


def test_b_bond_cashflow_pnl_is_the_coupon_on_exactly_the_coupon_steps(bond):
    _, table = bond
    coupon = N * 0.0425 / 2
    paid = table["cashflow_pnl"]
    assert set(paid[paid != 0].index) == set(COUPONS)
    for d in COUPONS:
        assert paid[d] == pytest.approx(coupon, rel=1e-12)
        assert table.loc[d, "economic_pnl"] == pytest.approx(table.loc[d, "actual_pnl"] + coupon, rel=1e-12)
        assert table.loc[d, "actual_pnl"] < -coupon / 2  # the dirty PV drops the paid coupon


def test_b_bond_residual_within_the_taylor_bound_every_step(bond):
    bt, table = bond
    steps = _steps(bt)
    assert [cur for _, cur in steps] == list(table.index)
    inst = bt.results[BOND_START].portfolio.all_instruments[0]
    terms = inst.resolved_terms
    ys = {d: tb.yield_bp(tb.market(d, "USD"), terms) for d in {d for s in steps for d in s}}
    bounds = np.array([_bond_bound(terms, inst.quantity_, d0, d1, ys[d0], ys[d1]) for d0, d1 in steps])
    residual = table["residual_pnl"].to_numpy()
    assert (np.abs(residual) <= bounds).all(), [(d1, r, b) for (d0, d1), r, b in zip(steps, residual, bounds) if abs(r) > b]
    assert (np.abs(residual + table["PNL_theta"].to_numpy()) > bounds).any()  # the bound has teeth


def test_b_bond_actual_pnl_telescopes_to_the_pv_change(bond):
    bt, table = bond
    terms = bt.results[BOND_START].portfolio.all_instruments[0].resolved_terms
    last = max(bt.results)
    pv = lambda d: tb.npv(tb.market(d, "USD"), terms)  # noqa: E731  (quantity 1)
    assert table["actual_pnl"].sum() == pytest.approx(pv(last) - pv(BOND_START), rel=1e-9)


# =============================================================================== (c) delta-hedged swaption book


def test_c_hedged_book_hedges_add_nothing_to_the_vol_columns(base, hedged):
    """Sanity check, not evidence of the zero-risk skip: the hedge swaps' IRVega/IRVanna/IRVolga
    are 0 (R2-8) and their vol level is constant, so they add nothing with or without the skip
    (test_zero_risk_never_reads_its_level_gs pins the skip). The book's vol columns equal the
    unhedged (a) run's, step for step."""
    bt, table = hedged
    _, alone = base
    assert any(isinstance(i, IRSwap) for d in bt.results for i in bt.results[d].portfolio.all_instruments)
    assert len(table) > 55
    for col in VOL_COLUMNS:
        assert table[col].tolist() == alone.loc[table.index, col].tolist(), col
    assert not np.allclose(table["PNL_delta"], alone.loc[table.index, "PNL_delta"])  # the hedges do move delta


def test_c_hedged_book_residual_within_the_sum_of_position_bounds(hedged):
    """Bound: the table sums per-position terms, so the book residual is the sum of the positions'
    residuals, each within its own R2-18 bound (the swaption's as in (a); a hedge swap's has only
    the rate and time terms, its vol derivatives being exactly 0), scaled by |quantity_|."""
    bt, table = hedged
    steps = _steps(bt)
    assert [cur for _, cur in steps] == list(table.index)
    bounds = np.array([_book_bound(bt, d0, d1) for d0, d1 in steps])
    residual = table["residual_pnl"].to_numpy()
    assert (np.abs(residual) <= bounds).all(), [(d1, r, b) for (d0, d1), r, b in zip(steps, residual, bounds) if abs(r) > b]


# =============================================================================== (d) scaling and direction


def test_d_scaled_by_2_5_is_2_5_times_the_unit_run_in_every_column(base, variants):
    _, unit = base
    scaled = variants["scaled"]
    ref = unit.loc[scaled.index]
    for col in scaled.columns:
        assert scaled[col].to_numpy() == pytest.approx(2.5 * ref[col].to_numpy(), rel=1e-9, abs=1e-9), col


def test_d_sell_negates_every_column(base, variants):
    """Flipping buy_sell negates the position, so every column negates. (Flipping pay_or_receive
    does not: payer - receiver is the forward swap, see the next test.)"""
    _, buy = base
    sell = variants["sell"]
    ref = buy.loc[sell.index]
    for col in sell.columns:
        assert sell[col].to_numpy() == pytest.approx(-ref[col].to_numpy(), rel=1e-12, abs=1e-12), col


def test_d_payer_and_receiver_have_the_same_vol_attribution(base, variants):
    """Put-call parity: payer - receiver = the forward swap, which has no vol exposure, so the vol
    columns agree while the delta columns differ."""
    _, payer = base
    receiver = variants["receiver"]
    ref = payer.loc[receiver.index]
    for col in VOL_COLUMNS:
        assert receiver[col].to_numpy() == pytest.approx(ref[col].to_numpy(), rel=1e-6, abs=1e-6), col
    assert (np.sign(receiver["PNL_delta"]) != np.sign(ref["PNL_delta"])).all()


# =============================================================================== (e) table vs pnl_explain vs gs


def test_e_table_cumsum_is_pnl_explain_exactly_and_pnl_explain_is_the_gs_loop(base, bond, hedged):
    for bt, table in (base, bond, hedged):
        pnl = bt.pnl_explain()
        for attribute in bt.pnl_explain_def.attributes:
            name = attribute.attribute_name
            assert list(pnl[name]) == list(table.index)
            assert table[name].cumsum().tolist() == [float(v) for v in pnl[name].values()], name
        assert _assert_same_as_gs_loop(bt) >= 3


# =============================================================================== (f) unit mismatch


def test_f_decimal_levels_under_the_bp_definition_raise_naming_the_measure(base, tmp_path):
    text = (ASSETS / "toy_usd_swaption.yaml").read_text(encoding="utf8")
    for name in ("fwd_rate", "annual_vol"):
        line = f"{name + ':':<17}{{expr: 'ts.{name}(market, trade)', unit: bp}}"
        assert line in text
        text = text.replace(line, f"{name + ':':<17}{{expr: 'ts.{name}(market, trade) / 1e4', unit: decimal}}")
    cfg = tmp_path / "toy_usd_swaption_decimal.yaml"
    cfg.write_text(text, encoding="utf8")
    end = date(2024, 1, 9)
    bt = _run([cfg], [_on(D0, AddTradeAction(_swaption(), name="Add"))], end, pnl_explain=swaption_pnl_definition())
    expected = r"PNL_delta: IRFwdRate on \S+ has unit \{'decimal': 1\}; the definition expects bp"
    with pytest.raises(ValueError, match=expected):
        bt.pnl_explain()
    with pytest.raises(ValueError, match="IRFwdRate"):
        bt.pnl_explain_table()
    bt.pnl_explain_def = swaption_pnl_definition(rate_unit="decimal")  # the vol levels are still wrong
    with pytest.raises(ValueError, match=r"VegaPnL: IRAnnualImpliedVol on \S+ has unit \{'decimal': 1\}"):
        bt.pnl_explain()
    # the matching definition scales the decimal levels back: the bp run's numbers
    bt.pnl_explain_def = swaption_pnl_definition(rate_unit="decimal", vol_unit="decimal")
    table, ref = _table(bt), base[1].loc[: end]
    for col in SIX:
        assert table[col].to_numpy() == pytest.approx(ref[col].to_numpy(), rel=1e-7, abs=1e-7), col


# =============================================================================== (g) a swap config without the explain measures


def test_g_an_irswap_config_without_the_explain_measures_fails_to_load_naming_them():
    """IR_STRICT_CONTRACT R3-0: an IRSwap config cannot declare its way out of a contract measure,
    so a swap book without gamma/vega/theta can no longer reach the explain at all -- the config
    (toy_eur_irs cut down to Price/IRDelta/IRFwdRate, no unsupported_measures:) fails to load,
    naming every measure the swaption definition would have asked for."""
    cfg = yaml.safe_load((ASSETS / "toy_eur_irs.yaml").read_text(encoding="utf8"))
    cfg["risk_measures"] = {k: cfg["risk_measures"][k] for k in ("Price", "IRDelta", "IRFwdRate")}
    with pytest.raises(ConfigError) as err:
        PricebtSession.use(assets=[cfg])
    for measure in ("IRGammaParallel", "IRVega", "IRVanna", "IRVolga", "IRAnnualImpliedVol", "Theta", "ExpiryInYears"):
        assert measure in str(err.value), measure


# =============================================================================== (h) DEV-R11 views


def test_h_views_skip_the_cashflows_table_dev_r11(bond):
    bt, table = bond
    assert Cashflows in bt.risks
    assert table["cashflow_pnl"].abs().sum() > 0  # pnl_explain_table uses it
    for frame in (bt.result_summary, bt.risk_summary):
        assert Cashflows not in frame.columns and IRDeltaParallel in frame.columns
        assert np.isfinite(frame[IRDeltaParallel].astype(float)).all()
    ts_frame = bt.strategy_as_time_series()
    assert "Cashflows" not in ts_frame["Risk Measures"].columns
    assert str(IRDeltaParallel) in ts_frame["Risk Measures"].columns
    stats = bt.summary_stats()
    assert np.isfinite(stats["Total PnL"])
    bps = bt.pnl_bps(IRDeltaParallel)
    assert len(bps) == len(bt.result_summary)
    with pytest.raises(ValueError, match="Cashflows is a table measure"):
        bt.pnl_bps(Cashflows)


# =============================================================================== (i) DEV-E20 off-grid exit


def test_i_off_grid_exit_while_another_position_is_held_attributes_correctly_dev_e20():
    """Weekly grid; a long payer held throughout and a receiver held '1b', which exits on
    Wednesday D0 + 1b, off the grid. gs's pnl_explain raises KeyError there (R15 S3); pricebt's
    results[e] holds the continuing payer (DEV-R1), so both steps attribute."""
    long_ = _swaption(name="long")
    short = _swaption(pay_or_receive="Receive", strike="A+25", name="short")
    bt = _run(
        ["toy_usd_swaption.yaml"],
        [_on(D0, AddTradeAction(long_, name="Long"), AddTradeAction(short, "1b", name="Short"))],
        D0 + timedelta(weeks=3),
        frequency="1w",
        pnl_explain=swaption_pnl_definition(),
    )
    e, d1 = date(2024, 1, 3), D0 + timedelta(weeks=1)
    assert e not in bt.states and e in bt.trade_exit_risk_results and e in bt.results
    table = _table(bt)
    assert table.index[:2].tolist() == [e, d1]
    held = {i.name.split("_")[0]: i.resolved_terms for i in bt.results[D0].portfolio.all_instruments}
    assert set(held) == {"Long", "Short"}

    def move(terms, a, b):  # toy delta at a times the own-rate move a -> b, quantity 1
        ma, mb = ts.market(a, "USD"), ts.market(b, "USD")
        return ts.delta(ma, terms) * (ts.fwd_rate(mb, terms) - ts.fwd_rate(ma, terms))

    assert table.loc[e, "PNL_delta"] == pytest.approx(move(held["Long"], D0, e) + move(held["Short"], D0, e), rel=1e-10)
    assert table.loc[d1, "PNL_delta"] == pytest.approx(move(held["Long"], e, d1), rel=1e-10)
    assert _assert_same_as_gs_loop(bt) == 5
