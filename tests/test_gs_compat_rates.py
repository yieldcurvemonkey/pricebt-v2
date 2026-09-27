"""gs_quant facade on the rateslib adapter (`pricebt.instrument.IRSwap` over the library-free synthetic market, wrapped by the adapter's `STACK`): constructor semantics
as TERMS on the registered spec, the guide's section-11 quick-reference template (swap form: swaptions are out of scope, spec 0.3), rates risks in result_summary,
risk-scaled costs, and the 4-Delta-Hedging notebook's generic-backtester cell on swaps. The session is the ONE call F-6 allows in place of `GsSession.use()`:
`PricebtSession.use(market=<provider>, stack="pricebt.contrib.rateslib:STACK")`."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("rateslib")
pytestmark = pytest.mark.adapter_rateslib

from test_rl_common import ctx, synthetic  # noqa: E402

import pricebt.contrib.rateslib as RL  # noqa: E402
from pricebt.backtests.actions import AddTradeAction, HedgeAction  # noqa: E402
from pricebt.backtests.backtest_objects import ScaledTransactionModel  # noqa: E402
from pricebt.backtests.generic_engine import GenericEngine  # noqa: E402
from pricebt.backtests.strategy import Strategy  # noqa: E402
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements  # noqa: E402
from pricebt.common import AggregationLevel, Currency, PayReceive  # noqa: E402
from pricebt.contrib.rateslib import DEFAULT_TENORS, RLSwap  # noqa: E402
from pricebt.contrib.rateslib import _compat as C  # noqa: E402
from pricebt.errors import ConfigError, NotSupportedError  # noqa: E402
from pricebt.instrument import InstrumentTemplate, IRSwap  # noqa: E402
from pricebt.pricable import MarkContext  # noqa: E402
from pricebt.risk import IRDelta, IRDeltaParallel, Price  # noqa: E402
from pricebt.session import PricebtSession  # noqa: E402

NY = "America/New_York"
STACK = "pricebt.contrib.rateslib:STACK"
GS_STATS = ["Start Date", "End Date", "Duration (days)", "Total PnL", "Total Transaction Costs", "Total Trades", "Peak PnL", "Annualised Return",
            "Annualised Volatility", "Sharpe Ratio", "Sortino Ratio", "Max Drawdown", "Max Drawdown Duration (days)", "Calmar Ratio", "Current Drawdown",
            "Average Daily PnL", "Daily PnL Std Dev", "Best Day", "Worst Day", "% Positive Days", "Skewness", "Kurtosis"]


@pytest.fixture(autouse=True)
def _no_session_leaks():
    PricebtSession.reset()
    yield
    PricebtSession.reset()


@pytest.fixture(scope="module")
def mdp():
    return synthetic("2023-01-03", "2024-12-31")


@pytest.fixture
def session(mdp):
    with PricebtSession(market=mdp, stack=STACK, calendar="nyc") as s:  # F-3: the test registers the adapter's specs, once
        yield s


def at(mdp, d):
    ts = pd.Timestamp(f"{d} 17:00", tz=NY)
    return RL.wrap(mdp.get_pricer(ts)), ts


def build(tpl, mdp, d="2024-03-04"):
    p, ts = at(mdp, d)
    return tpl.build(p, ts), p


# ------------------------------------------------------------------ IRSwap
def test_irswap_atmf_is_par_on_the_trade_date_and_decimal_rates_become_percent(session, mdp):
    atm = IRSwap(PayReceive.Pay, "10y", Currency.USD, notional_amount=1e8, name="10y_swap")
    assert isinstance(atm, InstrumentTemplate) and atm.name == "10y_swap" and atm.notional_amount == 1e8 and atm.asset_class == "swap"
    assert atm.spec.name == "usd_sofr_ois" and "delta_ladder" in atm.spec.bindings, "the swap schema requires a delta ladder; the adapter's kit binds it by default"
    b, p = build(atm, mdp)
    s = b.obj
    assert isinstance(s, RLSwap) and s.sign == 1 and s.notional == 1e8 and b.terms["direction"] == 1
    assert b.terms["fixed_rate"] == pytest.approx(s.rate(ctx=ctx(p)), abs=1e-12), "ATMF: the resolved rate is the swap's own par rate"
    assert abs(s.value(ctx=ctx(p)).pv) < 1e-6 * 1e8, "ATMF: zero value at inception"
    assert build(IRSwap("Pay", "10y", "USD", fixed_rate=0.04), mdp)[0].terms["fixed_rate"] == pytest.approx(4.0), "gs decimal 0.04 -> percent 4.0"
    assert build(IRSwap("Pay", "10y", "USD", fixed_rate="ATMF+10"), mdp)[0].terms["fixed_rate"] == pytest.approx(b.terms["fixed_rate"] + 0.10, abs=1e-12)
    assert build(IRSwap("Pay", "10y", "USD", fixed_rate="ATM-5bp"), mdp)[0].terms["fixed_rate"] == pytest.approx(b.terms["fixed_rate"] - 0.05, abs=1e-12)


def test_irswap_direction_notional_dates_and_ladder(session, mdp):
    rec, p = build(IRSwap(PayReceive.Receive, "5y", "USD", notional_amount=2e7), mdp)
    c = ctx(p)
    assert rec.obj.sign == -1 and rec.obj.dv01(ctx=c) < 0 and rec.obj.notional == 2e7 and rec.terms["direction"] == -1
    flipped, _ = build(IRSwap("Pay", "5y", "USD", notional_amount=-2e7), mdp)
    assert flipped.obj.sign == -1 and flipped.obj.dv01(ctx=c) == pytest.approx(rec.obj.dv01(ctx=c)), "a negative notional flips the direction (gs scale)"
    fwd, _ = build(IRSwap("Pay", "5y", "USD", effective_date="1y"), mdp)
    cal = p.calendar("nyc")
    spot = cal.add_bus_days(C.to_dt(p.reference_date), 2, True)
    assert fwd.obj.effective == C.rl.add_tenor(spot, "1y", "MF", cal).date() == fwd.terms["effective"], "forward start = spot rolled forward a year, on the snapshot calendar"
    dated, _ = build(IRSwap("Pay", date(2030, 3, 6), "USD"), mdp)
    assert dated.obj.termination == date(2030, 3, 6) == dated.terms["maturity"]
    payer, _ = build(IRSwap("Pay", "10y", "USD", notional_amount=1e7), mdp)
    fresh = ctx(p)
    assert sum(payer.obj.delta_ladder(ctx=fresh).values()) == pytest.approx(payer.obj.dv01(ctx=fresh), rel=0.02)


def test_irswap_rejects_what_it_cannot_price(session):
    with pytest.raises(NotSupportedError):
        IRSwap("Pay", "10y", Currency.EUR)
    with pytest.raises(NotSupportedError, match="fixed_rate_frequency"):
        IRSwap("Pay", "10y", "USD", fixed_rate_frequency="3m")
    with pytest.raises(TypeError):
        IRSwap("Pay", "10y", "USD", not_a_gs_field=1)
    with pytest.raises(ConfigError, match="decimal"):
        IRSwap("Pay", "10y", "USD", fixed_rate=4.0)
    with pytest.raises(ConfigError):
        IRSwap("Pay", None, "USD")
    IRSwap("Pay", "10y", "USD", floating_rate_option="USD-SOFR-COMPOUND", fee=0)  # SOFR conventions and a zero fee are what is priced: the stack says so (accepts_gs)


def test_irswap_needs_a_session_that_registered_a_spec_and_names_what_to_register(mdp):
    with pytest.raises(ConfigError, match="PricebtSession.use"):
        IRSwap("Pay", "10y", "USD")  # no session at all
    with PricebtSession(market=mdp, calendar="nyc"):  # a session without a stack registers no spec
        with pytest.raises(NotSupportedError, match="swap:USD"):
            IRSwap("Pay", "10y", "USD")


# ------------------------------------------------------------------ section 11: the quick-reference template (swap form)
def test_section11_quick_reference_template(mdp):
    # gs: `GsSession.use()`; pricebt: supply the market once
    PricebtSession.use(market=mdp, stack=STACK, calendar="nyc")
    start_date = date(2023, 1, 3)
    end_date = date(2024, 12, 31)

    # 1. Define the instrument
    instrument = IRSwap("Pay", "10y", Currency.USD, name="10y")

    # 2. Define trigger + action
    trig_req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="6m")
    action = AddTradeAction(instrument, trade_duration="6m")
    trigger = PeriodicTrigger(trig_req, action)

    # 3. Build strategy
    strategy = Strategy(None, trigger)

    # 4. Run
    GE = GenericEngine()
    backtest = GE.run_backtest(strategy, start=start_date, end=end_date, frequency="1b", show_progress=True)

    # 5. View results
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    backtest.result_summary["Total"].plot(title="Performance")
    matplotlib.pyplot.close("all")
    ledger = backtest.trade_ledger()
    stats = backtest.summary_stats()

    rs = backtest.result_summary
    assert (rs["Total"] - rs["Price"] - rs["Cumulative Cash"] - rs["Transaction Costs"]).abs().max() < 1e-6
    assert list(ledger.index) == [f"Action1_10y_{d}" for d in ("2023-01-03", "2023-07-03", "2024-01-03", "2024-07-03")]
    assert list(ledger["Status"]) == ["closed", "closed", "closed", "open"]
    pos = backtest.result.positions.sort_values("entry_ts")
    closed = pos[pos["status"] == "closed"]
    # a par swap held for six months: Trade PnL = -entry price + exit price + the coupons swept meanwhile
    np.testing.assert_allclose(ledger.loc[ledger["Status"] == "closed", "Trade PnL"], -closed["entry_pv"].to_numpy() + closed["exit_pv"].to_numpy() + closed["flow_cash"].to_numpy(), atol=1e-6)
    assert (ledger["Open Value"].abs() < 1e-3).all(), "ATMF swaps cost nothing to enter"
    assert (ledger.loc[ledger["Status"] == "closed", "Close Value"].abs() > 1e4).all(), "the exit proceeds are the whole P&L of a par swap (no coupon falls inside six months of a 10y annual swap)"
    assert list(stats.index) == GS_STATS and stats["Total Trades"] == 4 and stats["Total PnL"] == pytest.approx(rs["Total"].iloc[-1])
    assert rs.index[0] == start_date and rs.index[-1] == end_date and len(rs) == len(C.nyc_calendar().business_days(start_date, end_date))


def test_section11_block_of_the_pricebt_guide_runs_verbatim():
    import re
    from pathlib import Path

    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    text = (Path(__file__).resolve().parents[1] / "docs" / "guides" / "backtesting.md").read_text(encoding="utf8")
    section = text.split("## 11. Quick Reference Template", 1)[1]
    code = re.search(r"```python\n(.*?)```", section, flags=re.S).group(1)
    assert "PricebtSession.use(market=" in code and "stack=" in code and "gs_quant" not in code and "IRSwaption" not in code
    ns = {}
    try:
        exec(compile(code, "backtesting.md#11", "exec"), ns)
    finally:
        PricebtSession.reset()
        matplotlib.pyplot.close("all")
    bt = ns["backtest"]
    assert len(bt.trade_ledger()) == 4 and list(bt.summary_stats().index) == GS_STATS


# ------------------------------------------------------------------ rates risks and risk-scaled costs
def test_rates_risks_are_result_summary_columns_and_the_ladder_sums_to_parallel_dv01(session, mdp):
    a, b = date(2024, 3, 1), date(2024, 3, 28)
    req = PeriodicTriggerRequirements(start_date=a, end_date=b, frequency="1w")
    strategy = Strategy(None, PeriodicTrigger(req, [AddTradeAction(IRSwap("Pay", "10y", "USD", notional_amount=1e7, name="p10"), None)]))
    parallel = IRDelta(aggregation_level=AggregationLevel.Type, currency="USD")
    bt = GenericEngine().run_backtest(strategy, start=a, end=b, frequency="1b", risks=[Price, parallel, IRDelta], show_progress=False)
    rs = bt.result_summary
    assert list(rs.columns)[:3] == ["Price", "dv01", "delta_ladder"]
    # independent: rebuild every open position at the last point and sum its own dollar delta
    p, ts = at(mdp, b.isoformat())
    live = bt.result.positions[bt.result.positions["status"] == "open"]
    tpl = strategy.triggers[0].actions[0].priceables.legs[0].template
    dv = 0.0
    for _, r in live.iterrows():
        e = RL.wrap(mdp.get_pricer(r["entry_ts"]))
        obj = tpl.build(e, r["entry_ts"]).obj
        dv += r["quantity"] * obj.dv01(ctx=ctx(p))  # one context per object: adapters cache greeks in ctx.cache keyed by id(object)
    assert rs[parallel].iloc[-1] == pytest.approx(dv, rel=1e-9) and len(live) >= 3
    lad = bt.ladder(IRDelta)
    assert list(lad.columns) == list(DEFAULT_TENORS)
    assert isinstance(rs[IRDelta].iloc[-1], pd.Series) and list(rs[IRDelta].iloc[-1].index) == list(DEFAULT_TENORS), "gs shape: a Series per row (the core measure is the dict)"
    one = tpl.build(p, ts).obj
    assert lad.iloc[-1].sum() == pytest.approx(live["quantity"].sum() * sum(one.delta_ladder(ctx=ctx(p)).values()), rel=0.05)


def test_price_scaled_cost_of_a_swap_is_a_percent_of_its_price_at_entry_and_at_exit(session, mdp):
    a, b = date(2024, 3, 1), date(2024, 6, 28)
    req = PeriodicTriggerRequirements(start_date=a, end_date=b, frequency="1m")
    action = AddTradeAction(IRSwap("Pay", "10y", "USD", fixed_rate=0.04, name="p10"), trade_duration="1m", transaction_cost=ScaledTransactionModel(scaling_type=Price, scaling_level=0.01))
    bt = GenericEngine().run_backtest(Strategy(None, PeriodicTrigger(req, action)), start=a, end=b, frequency="1b", show_progress=False)
    pos = bt.result.positions
    closed = pos[pos["status"] == "closed"]
    assert len(pos) == 4 and len(closed) == 3 and (pos["entry_pv"].abs() > 1e4).all() and (closed["exit_pv"].abs() > 1e4).all(), "off-market swaps: a non-zero price at both ends"
    tc = bt.result_summary["Transaction Costs"].iloc[-1]
    assert tc == pytest.approx(-0.01 * (pos["entry_pv"].abs().sum() + closed["exit_pv"].abs().sum()), rel=1e-12), "1% of the price at entry and 1% of the price at exit"
    led = bt.trade_ledger()
    np.testing.assert_allclose(led.loc[led["Status"] == "closed", "Trade PnL"].to_numpy(), (closed["exit_pv"] - closed["entry_pv"] + closed["flow_cash"]).to_numpy(), atol=1e-6)


# ------------------------------------------------------------------ 4-Delta Hedging.ipynb, "Generic Backtesting Framework" cell (a book of swaps: swaptions are out of scope)
@pytest.fixture(scope="module")
def notebook():
    m = synthetic("2024-01-02", "2024-12-31")
    with PricebtSession(market=m, stack=STACK, calendar="nyc"):
        start_date, end_date = date(2024, 3, 1), date(2024, 5, 31)
        # dates on which actions will be triggered
        trig_req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1b")

        # instrument that will be added on AddTradeAction: a receiver swap held a month, a new one every day
        book_swap = IRSwap(PayReceive.Receive, "7y", "USD", fixed_rate="ATMF", notional_amount=1e8, name="7y_swap")
        swap_hedge = IRSwap(PayReceive.Pay, "10y", "USD", fixed_rate="ATMF", notional_amount=1e8, name="10y_swap")
        action_trade = AddTradeAction(book_swap, "1m")
        action_hedge = HedgeAction(IRDelta(aggregation_level=AggregationLevel.Type, currency="local"), swap_hedge, "1b")

        # starting with empty portfolio (first arg to Strategy), apply actions on trig_req
        triggers = PeriodicTrigger(trig_req, [action_trade, action_hedge])
        strategy = Strategy(None, triggers)

        # run backtest
        GE = GenericEngine()
        backtest = GE.run_backtest(strategy, start=start_date, end=end_date, frequency="1b", show_progress=False)
        unhedged = GE.run_backtest(Strategy(None, PeriodicTrigger(trig_req, [action_trade])), start=start_date, end=end_date, frequency="1b",
                                   risks=[IRDelta(aggregation_level=AggregationLevel.Type, currency="local")], show_progress=False)
    return backtest, unhedged


def test_notebook_delta_hedged_book_is_flat_after_every_hedge(notebook):
    backtest, unhedged = notebook
    dv = IRDelta(aggregation_level=AggregationLevel.Type, currency="local")
    rs = backtest.result_summary
    assert dv in list(rs.columns), "the hedge's risk is recorded like gs (strategy risks join result_summary)"
    gross = unhedged.result_summary[dv].abs().max()
    assert gross > 1e5 and rs[dv].abs().max() < 1e-9 * gross, "net dv01 ~ 0 at every point: the '1b' hedge is replaced daily"
    hedges = backtest.result.positions[backtest.result.positions["kind"] == "hedge"].sort_values("entry_ts")
    days = list(rs.index)
    assert [t.date() for t in hedges["entry_ts"]] == days and [t.date() for t in hedges["exit_ts"].iloc[:-1]] == days[1:]


def test_notebook_delta_hedging_reduces_pnl_variance(notebook):
    backtest, unhedged = notebook
    dh = backtest.result_summary["Total"].diff().dropna()
    du = unhedged.result_summary["Total"].diff().dropna()
    assert dh.var() < 0.5 * du.var(), (dh.var(), du.var())
    book = backtest.result.positions[backtest.result.positions["kind"] == "add"]
    assert len(book) == len(backtest.result_summary) and set(book["template"]) == {"7y_swap"}
    pd.testing.assert_frame_equal(book[["entry_ts", "exit_ts", "entry_pv"]].reset_index(drop=True),
                                  unhedged.result.positions[["entry_ts", "exit_ts", "entry_pv"]].reset_index(drop=True))


def test_notebook_comparison_cell_old_cash_column(notebook):
    backtest, _ = notebook
    rs = backtest.result_summary
    generic = backtest.result_summary["Cash"].cumsum() + backtest.result_summary[Price]  # the notebook's last cell (pre-2021 gs 'Cash' column)
    np.testing.assert_allclose(generic.to_numpy(), rs["Total"].to_numpy(), rtol=1e-12, atol=1e-6)
    assert list(backtest.summary_stats().index) == GS_STATS
