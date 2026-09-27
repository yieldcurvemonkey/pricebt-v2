"""gs_quant backtesting guide (gs_quant/skills/gs-quant-overview/backtesting.md) sections 3-9, ported snippet by snippet to the pricebt facade.

The snippets are kept as written (only the import root changes); the market is the pure-python toy market (pricebt.testing.toys, no rateslib)
supplied once through PricebtSession, and `instrument` is a user pricable template (the "plug in your own instrument" path). Every test checks
the outcome against an independent expectation (closed-form toy P&L, hand-built schedules, pandas ffill of the signal, ...).
"""
import datetime as dt
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import pytest

from pricebt.backtests.actions import (AddScaledTradeAction, AddTradeAction, EnterPositionQuantityScaledAction, ExitAllPositionsAction, ExitTradeAction,
                                       HedgeAction, ScalingActionType, BacktestTradingQuantityType)
from pricebt.backtests.backtest_objects import (AggregateTransactionModel, ConstantTransactionModel, PnlDefinition, ScaledTransactionModel,
                                                TransactionAggType)
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import (AggregateTrigger, AggregateTriggerRequirements, AggType, DateTrigger, DateTriggerRequirements, MktTrigger,
                                        MktTriggerRequirements, NotTrigger, NotTriggerRequirements, PeriodicTrigger, PeriodicTriggerRequirements,
                                        RiskTriggerRequirements, StrategyRiskTrigger, TriggerDirection)
from pricebt.common import AggregationLevel
from pricebt.errors import ConfigError, MissingDataError, NotSupportedError
from toy_helpers import bind_method, toy_tpl
from pricebt.risk import FXDelta, IRDelta, IRDeltaParallel, Price
from pricebt.session import PricebtSession
from pricebt.testing.toys import ToyForward, ToyFuture, ToyMDP, ToyOption

pytestmark = pytest.mark.core

NY = "America/New_York"
start_date, end_date = date(2024, 1, 2), date(2024, 6, 28)
GS_STATS = ["Start Date", "End Date", "Duration (days)", "Total PnL", "Total Transaction Costs", "Total Trades", "Peak PnL", "Annualised Return",
            "Annualised Volatility", "Sharpe Ratio", "Sortino Ratio", "Max Drawdown", "Max Drawdown Duration (days)", "Calmar Ratio", "Current Drawdown",
            "Average Daily PnL", "Daily PnL Std Dev", "Best Day", "Worst Day", "% Positive Days", "Skewness", "Kurtosis"]
LEDGER = ["Open", "Close", "Open Value", "Close Value", "Long Short", "Status", "Trade PnL"]


def D(d):
    return pd.Timestamp(f"{pd.Timestamp(d).date()} 17:00", tz=NY)


def rate(d):
    return ToyMDP().get_pricer(D(d)).rate


def bdays(a=start_date, b=end_date):
    return [x.date() for x in pd.bdate_range(a, b)]


# ------------------------------------------------------------------ user instruments (the "your own pricable" path)
@dataclass
class NotionalForward(ToyForward):
    """Toy linear forward exposing gs's `notional_amount` (pv = notional x (rate - strike), dv01 = notional x 0.01)."""

    @property
    def notional_amount(self):
        return self.notional


@dataclass
class ExpiringOption(ToyOption):
    @property
    def expiration_date(self):
        return self.expiry.date()


def inst(name="swap", notional=1e5, **extra):
    return toy_tpl(name=name, target=NotionalForward, kwargs={"strike": 4.0, "notional": notional}, **extra)


def fx(name="eurusd_fwd", notional=1e6):
    """An FX-forward-like user pricable: binds gs's FXDelta measure name ('fx_delta') to its delta method (notional x 0.01 per unit)."""
    return inst(name, notional, bind={"fx_delta": bind_method("dv01")}, extra={"fx_delta": "measure"})


def fut(name):
    return toy_tpl(name=name, target=ToyFuture, kwargs={"multiplier": 1.0})


class RecordingMDP(ToyMDP):
    def __init__(self):
        super().__init__()
        self.requests = []

    def get_pricer(self, ts, request=None):
        self.requests.append(dict(request or {}))
        return super().get_pricer(ts, request)


@pytest.fixture(autouse=True)
def session():
    with PricebtSession(market=RecordingMDP()) as s:
        yield s


def run(strategy, frequency="1b", **kw):
    kw.setdefault("show_progress", False)
    return GenericEngine().run_backtest(strategy, start=kw.pop("start", start_date), end=kw.pop("end", end_date), frequency=frequency, **kw)


def pos(bt, **eq):
    p = bt.result.positions
    for k, v in eq.items():
        p = p[p[k] == v]
    return p


def entries(bt, **eq):
    return sorted(t.date() for t in pos(bt, **eq)["entry_ts"])


def identity_ok(bt):
    rs = bt.result_summary
    return float((rs["Total"] - (rs["Price"] + rs["Cumulative Cash"] + rs["Transaction Costs"])).abs().max()) < 1e-6


def gs_schedule(a, b, freq):
    """gs RelativeDate rules on a weekends-only calendar, written independently of pricebt: b business days, w +7k days rolled forward,
    m +k months rolled forward, y +k years rolled back; multiples are taken from the start."""
    n, u = int(freq[:-1]), freq[-1]
    fwd = lambda d: d + dt.timedelta(days={5: 2, 6: 1}.get(d.weekday(), 0))
    back = lambda d: d - dt.timedelta(days={5: 1, 6: 2}.get(d.weekday(), 0))
    if u == "b":
        return bdays(a, b)[::n]
    out, k = [a], 1
    while True:
        if u == "w":
            d = fwd(a + dt.timedelta(weeks=n * k))
        elif u == "m":
            m = a.month - 1 + n * k
            y, mm = a.year + m // 12, m % 12 + 1
            import calendar as _c

            d = fwd(date(y, mm, min(a.day, _c.monthrange(y, mm)[1])))
        else:
            d = back(date(a.year + n * k, a.month, a.day))
        if d > b:
            return out
        out.append(d)
        k += 1


# ================================================================== section 3: strategy construction
def test_section3_strategy_constructions():
    trigger = PeriodicTrigger(PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1m"), AddTradeAction(inst(), "1m"))
    initial_instrument = inst("initial_instrument")
    empty = run(Strategy(None, trigger))  # empty starting portfolio
    seeded = run(Strategy(initial_instrument, trigger))  # start with an instrument
    trigger_add = PeriodicTrigger(PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1m"), AddTradeAction(inst("a"), "1m", name="add"))
    trigger_hedge = PeriodicTrigger(PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1w"),
                                    HedgeAction(IRDeltaParallel, inst("h"), "1w", name="hedge"))
    multi = run(Strategy(None, [trigger_add, trigger_hedge]))  # multiple triggers

    monthly = gs_schedule(start_date, end_date, "1m")
    assert entries(empty) == monthly and len(pos(empty, kind="initial")) == 0
    ini = pos(seeded, kind="initial")
    assert len(ini) == 1 and ini.iloc[0]["status"] == "open" and ini.iloc[0]["entry_ts"] == D(start_date)
    diff = seeded.result_summary["Total"] - empty.result_summary["Total"]
    assert diff.iloc[-1] == pytest.approx(1e5 * (rate(end_date) - rate(start_date)), abs=1e-6), "the initial instrument is held for the whole run"
    weekly = gs_schedule(start_date, end_date, "1w")
    assert entries(multi, action="add") == monthly and entries(multi, action="hedge") == weekly
    dv01 = multi.result_summary[IRDeltaParallel]
    assert dv01.loc[weekly].abs().max() < 1e-9, "the hedge trigger (listed second) sees the same-day entry and flattens dv01"
    # 03-04's '1m' add lives to 04-04 and overlaps 04-02's add; 04-02's hedge covers both, so the book is short 1,000 dv01 from 04-04 to 04-09
    assert dv01.loc[date(2024, 4, 4)] == pytest.approx(-1000.0) and dv01.loc[date(2024, 4, 9)] == pytest.approx(0.0)
    assert all(identity_ok(b) for b in (empty, seeded, multi))


# ================================================================== section 4: triggers
@pytest.mark.parametrize("freq", ["1b", "1w", "1m", "3m", "1y"])
def test_section41_periodic_trigger_schedule_follows_gs_relative_dates(freq):
    action = AddTradeAction(inst(), None)
    trig_req = PeriodicTriggerRequirements(
        start_date=start_date,
        end_date=end_date,
        frequency=freq,  # '1b' (daily), '1w', '1m', '3m', '1y'
    )
    trigger = PeriodicTrigger(trig_req, action)
    bt = run(Strategy(None, trigger))
    assert entries(bt) == gs_schedule(start_date, end_date, freq)
    assert len(bt.result_summary) == len(bdays()), "evaluation frequency ('1b') is independent of the trigger frequency"


def test_section42_strategy_risk_trigger_hedges_on_a_risk_breach():
    add = PeriodicTrigger(PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1w"), AddTradeAction(fx("exposure", 2e6), None, name="add"))
    hedge_action = HedgeAction(FXDelta(aggregation_level=AggregationLevel.Type, currency="USD"), fx("hedge_fwd", 1e6), None, name="hedge")

    hedge_risk = FXDelta(aggregation_level=AggregationLevel.Type, currency="USD")
    trig_req = RiskTriggerRequirements(
        risk=hedge_risk,
        trigger_level=50_000,
        direction=TriggerDirection.ABOVE,
    )
    trigger = StrategyRiskTrigger(trig_req, hedge_action)
    bt = run(Strategy(None, [add, trigger]), risks=[hedge_risk])

    weekly = gs_schedule(start_date, end_date, "1w")  # each add is +20,000 fx_delta; 60,000 > 50,000 on every third add
    assert entries(bt, action="hedge") == weekly[2::3]
    fxd = bt.result_summary[hedge_risk]
    assert fxd.max() <= 50_000 + 1e-6 and fxd.max() == pytest.approx(40_000)
    assert (fxd.loc[weekly[2::3]].abs() < 1e-6).all(), "hedged back to zero when the barrier is breached"
    h = pos(bt, action="hedge")
    np.testing.assert_allclose(h["entry_quantity"], -6.0)


def _signal():
    """Observed on Wednesdays only (gaps on the other days), oscillating around 100."""
    wed = [d for d in pd.date_range(start_date - dt.timedelta(days=7), end_date, freq="W-WED")]
    return pd.Series([100.0 + 6.0 * np.sin(k / 2.0) for k in range(len(wed))], index=[d.date() for d in wed])


def test_section43_mkt_trigger_on_a_generic_data_source_fill_forward():
    pandas_series = _signal()
    action = AddTradeAction(inst("mkt"), "1b")
    data_source = GenericDataSource(pandas_series, MissingDataStrategy.fill_forward)
    trig_req = MktTriggerRequirements(
        data_source=data_source,
        trigger_level=100.0,
        direction=TriggerDirection.BELOW,
    )
    trigger = MktTrigger(trig_req, action)
    bt = run(Strategy(None, trigger))
    s = pandas_series.copy()
    s.index = pd.DatetimeIndex(s.index)
    seen = s.reindex(pd.DatetimeIndex(bdays()), method="ffill")  # the value visible on each grid date (same-day close counts)
    expected = [d.date() for d, v in seen.items() if v < 100.0]
    assert entries(bt) == expected and 0 < len(expected) < len(bdays())


def test_section43_generic_data_source_default_is_gs_fail_on_missing_data():
    trigger = MktTrigger(MktTriggerRequirements(data_source=GenericDataSource(_signal()), trigger_level=100.0, direction=TriggerDirection.BELOW),
                         AddTradeAction(inst(), "1b"))
    with pytest.raises(MissingDataError):
        run(Strategy(None, trigger))


def test_section44_date_trigger_trades_on_the_given_dates():
    action = AddTradeAction(inst("dated"), None)
    trig_req = DateTriggerRequirements(
        dates=[date(2024, 3, 15), date(2024, 6, 15), date(2024, 9, 15)],
    )
    trigger = DateTrigger(trig_req, action)
    bt = run(Strategy(None, trigger))
    assert entries(bt) == [date(2024, 3, 15), date(2024, 6, 15)], "gs adds trigger dates to the states (06-15 is a Saturday); 09-15 is after the end"
    assert date(2024, 6, 15) in bt.result_summary.index


def _aggregate(aggregate_type, level):
    periodic_trigger = PeriodicTrigger(PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1w"), AddTradeAction(fut("p"), name="p_only"))
    risk_trigger = StrategyRiskTrigger(RiskTriggerRequirements(risk=Price, trigger_level=level, direction=TriggerDirection.ABOVE), AddTradeAction(fut("r"), name="r_only"))
    action = AddTradeAction(fut("agg"), None, name="agg")  # a future has pv 0, so the book's Price is the initial forward's only
    agg_req = AggregateTriggerRequirements(
        triggers=[periodic_trigger, risk_trigger],
        aggregate_type=aggregate_type,  # ALL_OF (AND) or ANY_OF (OR)
    )
    trigger = AggregateTrigger(agg_req, action)
    return run(Strategy(inst("book"), trigger))


def test_section45_aggregate_trigger_all_of_vs_any_of():
    days = bdays()
    pv = {d: 1e5 * (rate(d) - 4.0) for d in days}
    level = float(np.median(list(pv.values())))
    weekly = set(gs_schedule(start_date, end_date, "1w"))
    risky = {d for d in days if pv[d] > level}
    all_of, any_of = _aggregate(AggType.ALL_OF, level), _aggregate(AggType.ANY_OF, level)
    assert entries(all_of, action="agg") == sorted(weekly & risky)
    assert entries(any_of, action="agg") == sorted(weekly | risky)
    assert 0 < len(weekly & risky) < len(weekly) < len(weekly | risky)
    assert len(pos(all_of, action="p_only")) == len(pos(all_of, action="r_only")) == 0, "child triggers' own actions are ignored, as in gs"


def test_section46_not_trigger_inverts_its_requirements():
    action = AddTradeAction(fut("inverted"), None)
    some_trigger_requirements = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1w")
    not_req = NotTriggerRequirements(trigger=some_trigger_requirements)
    trigger = NotTrigger(not_req, action)
    bt = run(Strategy(None, trigger))
    assert entries(bt) == sorted(set(bdays()) - set(gs_schedule(start_date, end_date, "1w")))


# ================================================================== section 5: actions
def _duration_case(trade_duration, priceable):
    trig = DateTrigger(DateTriggerRequirements(dates=[date(2024, 1, 2), date(2024, 2, 1)]), AddTradeAction(priceables=priceable, trade_duration=trade_duration, name="my_trade"))
    bt = run(Strategy(None, trig))
    p = pos(bt).sort_values("entry_ts")
    return [(e.date(), None if pd.isna(x) else pd.Timestamp(x)) for e, x in zip(p["entry_ts"], p["exit_ts"])]


def test_section51_trade_duration_options():
    opt = toy_tpl(name="opt", target=ExpiringOption, kwargs={"strike": 4.0, "expiry": D("2024-04-15"), "notional": 1e4}, resolved=("expiration_date",))
    a, b = date(2024, 1, 2), date(2024, 2, 1)
    assert _duration_case(None, inst()) == [(a, None), (b, None)], "None: hold forever"
    assert _duration_case("1m", inst()) == [(a, D("2024-02-02")), (b, D("2024-03-01"))], "tenor"
    assert _duration_case("expiration_date", opt) == [(a, D("2024-04-15")), (b, D("2024-04-15"))], "held to the instrument's expiry"
    assert _duration_case("next schedule", inst()) == [(a, D(b)), (b, None)], "exit at the trigger's next date; the last is held"
    assert _duration_case(date(2024, 3, 1), inst()) == [(a, D("2024-03-01")), (b, D("2024-03-01"))], "explicit date"
    assert _duration_case(dt.timedelta(days=10), inst()) == [(a, D(a) + pd.Timedelta(days=10)), (b, D(b) + pd.Timedelta(days=10))], "timedelta"


def test_section52_hedge_action_fx_forward_is_a_stub_and_the_snippet_runs_on_a_user_fx_pricable():
    from pricebt.instrument import FXForward

    with pytest.raises(NotSupportedError):
        FXForward(pair="EURUSD", settlement_date="1y", name="hedge_fwd")
    hedge_risk = FXDelta(aggregation_level="Type", currency="USD")
    action = HedgeAction(
        risk=hedge_risk,
        priceables=fx("hedge_fwd", 1e6),  # FXForward(pair='EURUSD', settlement_date='1y', name='hedge_fwd') in gs
        trade_duration="1m",
    )
    weekly = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1w")
    bt = run(Strategy(None, PeriodicTrigger(weekly, [AddTradeAction(fx("exposure", 3e6), None, name="exposure"), action])), risks=[hedge_risk])
    fxd = bt.result_summary[hedge_risk]
    dates = gs_schedule(start_date, end_date, "1w")
    assert (fxd.loc[dates].abs() < 1e-6).all(), "net FX delta is zero after every hedge"
    assert fxd.abs().max() > 1e4, "hedges expire after 1m between hedge dates, so the book is not always flat"
    assert entries(bt, kind="hedge") == dates


def test_section53_add_scaled_trade_action_size_scales_the_trade():
    unit = inst("unit", notional=1.0)
    action = AddScaledTradeAction(
        priceables=unit,
        trade_duration="1m",
        scaling_type=ScalingActionType.size,
        scaling_level=1_000_000,
    )
    req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1m")
    scaled = run(Strategy(None, PeriodicTrigger(req, action)))
    plain = run(Strategy(None, PeriodicTrigger(req, AddTradeAction(unit, "1m"))))
    np.testing.assert_allclose(scaled.result_summary["Total"], 1_000_000 * plain.result_summary["Total"], rtol=1e-12, atol=1e-6)
    assert (pos(scaled)["entry_quantity"] == 1_000_000).all()


def test_section54_enter_position_quantity_scaled_action_is_a_stub():
    with pytest.raises(NotSupportedError, match="AddScaledTradeAction"):
        EnterPositionQuantityScaledAction(
            priceables=inst(),
            trade_duration="1m",
            trade_quantity=1000,
            trade_quantity_type=BacktestTradingQuantityType.quantity,
        )


def test_section55_exit_trade_action_by_name_and_exit_all():
    weekly = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1w")
    adds = PeriodicTrigger(weekly, [AddTradeAction(priceables=inst("swap_a"), trade_duration=None, name="my_trade"),
                                    AddTradeAction(priceables=inst("swap_b"), trade_duration=None, name="other")])
    on = DateTriggerRequirements(dates=[date(2024, 3, 1)])
    exit_named = ExitTradeAction(priceable_names="my_trade")  # gs 5.1 names the ACTION 'my_trade'
    bt = run(Strategy(None, [adds, DateTrigger(on, exit_named)]))
    mine, other = pos(bt, action="my_trade"), pos(bt, action="other")
    before = mine[mine["entry_ts"] < D("2024-03-01")]
    assert len(before) > 0 and (before["exit_ts"] == D("2024-03-01")).all() and (mine[mine["entry_ts"] >= D("2024-03-01")]["status"] == "open").all()
    assert (other["status"] == "open").all()
    by_instrument = run(Strategy(None, [adds, DateTrigger(on, ExitTradeAction(priceable_names=["swap_b"]))]))
    assert (pos(by_instrument, template="swap_a")["status"] == "open").all() and (pos(by_instrument, template="swap_b")["exit_ts"].dropna() == D("2024-03-01")).all()
    exit_all = ExitAllPositionsAction()
    everything = run(Strategy(None, [adds, DateTrigger(on, exit_all)]))
    p = pos(everything)
    assert (p[p["entry_ts"] < D("2024-03-01")]["exit_ts"] == D("2024-03-01")).all() and (p[p["entry_ts"] >= D("2024-03-01")]["status"] == "open").all()


def test_section56_multiple_actions_exit_then_add_on_one_trigger():
    trig_req = RiskTriggerRequirements(risk=IRDeltaParallel, trigger_level=0.0, direction=TriggerDirection.ABOVE)  # the seed forward keeps dv01 > 0
    exit_action = ExitTradeAction(priceable_names="roll")
    add_action = AddTradeAction(inst("roll"), None, name="roll")
    bt = run(Strategy(inst("seed"), StrategyRiskTrigger(trig_req, [exit_action, add_action])))
    rolls = pos(bt, action="roll").sort_values("entry_ts")
    days = bdays()
    assert [t.date() for t in rolls["entry_ts"]] == days, "fires every day"
    assert [t.date() for t in rolls["exit_ts"].iloc[:-1]] == days[1:] and rolls["status"].iloc[-1] == "open", "each roll is exited the next day"
    swapped = run(Strategy(inst("seed"), StrategyRiskTrigger(trig_req, [add_action, exit_action])))
    pd.testing.assert_series_equal(bt.result_summary["Total"], swapped.result_summary["Total"])  # pricebt: exits run in the EXIT phase whatever the list order


# ================================================================== section 6: GenericEngine parameters
def test_section6_states_override_start_end_frequency():
    states = [date(2024, 1, 2), date(2024, 1, 10), date(2024, 2, 5), date(2024, 3, 7)]
    bt = GenericEngine().run_backtest(Strategy(inst(), []), start=start_date, end=end_date, frequency="1b", states=states, show_progress=False)
    assert list(bt.result_summary.index) == states and bt.states == states


def test_section6_frequency_start_end_initial_value_and_currency():
    base = run(Strategy(inst(), []), frequency="1w")
    assert list(base.result_summary.index) == gs_schedule(start_date, end_date, "1w")
    rich = run(Strategy(inst(), []), frequency="1w", initial_value=1e6)
    np.testing.assert_allclose(rich.result_summary["Total"] - base.result_summary["Total"], 1e6)
    np.testing.assert_allclose(rich.result_summary["Cumulative Cash"] - base.result_summary["Cumulative Cash"], 1e6)
    same = run(Strategy(inst(), []), frequency="1w", result_ccy="USD", is_batch=False, visible_to_gs=True, show_progress=True)
    pd.testing.assert_frame_equal(pd.DataFrame(same.result_summary), pd.DataFrame(base.result_summary))
    with pytest.raises(NotSupportedError):
        run(Strategy(inst(), []), result_ccy="EUR")
    with pytest.raises(NotSupportedError):
        run(Strategy(inst(), []), calc_risk_at_trade_exits=True)
    with pytest.raises(NotSupportedError):
        run(Strategy(inst(), []), pnl_explain=PnlDefinition([]))
    with pytest.raises(ConfigError):
        GenericEngine().run_backtest(Strategy(inst(), []))


def test_section6_holiday_calendar_drives_grid_and_date_maths():
    hol = [date(2024, 1, 15), date(2024, 2, 19)]  # two Mondays
    trig = DateTrigger(DateTriggerRequirements(dates=[date(2024, 1, 12), date(2024, 2, 16)]), AddTradeAction(inst(), "1b"))  # Fridays
    bt = run(Strategy(None, trig), end=date(2024, 2, 29), holiday_calendar=hol)
    assert not set(hol) & set(bt.result_summary.index) and len(bt.result_summary) == len(bdays(start_date, date(2024, 2, 29))) - 2
    p = pos(bt)
    assert [(e.date(), x.date()) for e, x in zip(p["entry_ts"], p["exit_ts"])] == [(date(2024, 1, 12), date(2024, 1, 16)), (date(2024, 2, 16), date(2024, 2, 20))], (
        "'1b' skips the weekend and the holiday")


def test_section6_csa_and_market_data_location_reach_the_market(session):
    session.market.requests.clear()
    run(Strategy(inst(), []), frequency="1m", csa_term="USD-SOFR", market_data_location="NYC")
    assert session.market.requests and all(r == {"csa_term": "USD-SOFR", "market_data_location": "NYC"} for r in session.market.requests)
    session.market.requests.clear()
    run(Strategy(inst(), []), frequency="1m")
    assert all(r == {} for r in session.market.requests)


# ================================================================== section 7: results
def _monthly_backtest(**action_kw):
    trig_req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1m")
    return run(Strategy(None, PeriodicTrigger(trig_req, AddTradeAction(inst("swap"), trade_duration="1m", name="my_trade", **action_kw)))), trig_req


def test_section7_result_summary_columns_identity_and_plot():
    backtest, _ = _monthly_backtest(transaction_cost=ConstantTransactionModel(500))
    summary = backtest.result_summary
    assert list(summary.columns) == ["Price", "Cumulative Cash", "Transaction Costs", "Total"]
    assert identity_ok(backtest) and all(isinstance(d, date) for d in summary.index) and list(summary.index) == bdays()
    tc = summary["Transaction Costs"]
    assert (tc <= 0).all() and (tc.diff().dropna() <= 0).all(), "cumulative and never positive"
    live = pos(backtest, status="open")
    assert summary["Price"].iloc[-1] == pytest.approx(float((live["quantity"] * 1e5 * (rate(end_date) - 4.0)).sum())), "Price = MTM of live instruments"
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    ax = backtest.result_summary["Total"].plot(title="Strategy Performance")
    assert ax.get_title() == "Strategy Performance"
    matplotlib.pyplot.close("all")


def test_section7_trade_ledger_matches_closed_form_trade_pnl():
    backtest, _ = _monthly_backtest()
    ledger = backtest.trade_ledger()
    assert list(ledger.columns) == LEDGER
    monthly = gs_schedule(start_date, end_date, "1m")
    assert list(ledger.index) == [f"my_trade_swap_{d.isoformat()}" for d in monthly]
    closed = ledger[ledger["Status"] == "closed"]
    for name, r in closed.iterrows():
        assert r["Trade PnL"] == pytest.approx(1e5 * (rate(r["Close"]) - rate(r["Open"])), abs=1e-6), name
        assert r["Trade PnL"] == pytest.approx(r["Open Value"] + r["Close Value"], abs=1e-9)
        assert r["Open Value"] == pytest.approx(-1e5 * (rate(r["Open"]) - 4.0))
    last = ledger.iloc[-1]
    assert last["Status"] == "open" and last["Close"] is None and np.isnan(last["Trade PnL"]) and last["Close Value"] == 0.0 and last["Long Short"] == 1


def _independent_stats(total, n_trades, tc_last, af=252):
    x = total.to_numpy(dtype=float)
    d = np.diff(x)
    peak = np.maximum.accumulate(x)
    dd = x - peak
    longest, run_start = 0, None
    idx = list(total.index)
    for i in range(len(x)):
        if dd[i] < 0:
            run_start = i - 1 if run_start is None else run_start
            longest = max(longest, (idx[i] - idx[run_start]).days)
        else:
            run_start = None
    ann = d.mean() * af
    vol = d.std(ddof=1) * np.sqrt(af)
    down = d[d < 0]
    return {"Total PnL": x[-1], "Total Transaction Costs": tc_last, "Total Trades": n_trades, "Peak PnL": peak[-1], "Annualised Return": ann,
            "Annualised Volatility": vol, "Sharpe Ratio": ann / vol, "Sortino Ratio": ann / (np.sqrt((down ** 2).mean()) * np.sqrt(af)),
            "Max Drawdown": dd.min(), "Max Drawdown Duration (days)": longest, "Calmar Ratio": ann / abs(dd.min()), "Current Drawdown": dd[-1],
            "Average Daily PnL": d.mean(), "Daily PnL Std Dev": d.std(ddof=1), "Best Day": d.max(), "Worst Day": d.min(),
            "% Positive Days": 100.0 * (d > 0).mean(), "Duration (days)": (idx[-1] - idx[0]).days}


def test_section7_summary_stats_labels_and_values():
    backtest, _ = _monthly_backtest(transaction_cost=ConstantTransactionModel(500))
    stats = backtest.summary_stats()
    assert list(stats.index) == GS_STATS
    total = backtest.result_summary["Total"]
    want = _independent_stats(total, len(backtest.trade_ledger()), backtest.result_summary["Transaction Costs"].iloc[-1])
    for k, v in want.items():
        assert stats[k] == pytest.approx(v, rel=1e-9, abs=1e-9), k
    assert stats["Start Date"] == start_date and stats["End Date"] == end_date
    assert stats["Skewness"] == pytest.approx(total.diff().dropna().skew()) and stats["Kurtosis"] == pytest.approx(total.diff().dropna().kurtosis())
    assert backtest.summary_stats(annualisation_factor=12)["Annualised Return"] == pytest.approx(want["Average Daily PnL"] * 12)


def test_section7_compare_two_backtests():
    backtest_a, _ = _monthly_backtest(transaction_cost=ConstantTransactionModel(500))
    backtest_b, _ = _monthly_backtest()
    table = pd.DataFrame({"A": backtest_a.summary_stats(), "B": backtest_b.summary_stats()})
    assert list(table.columns) == ["A", "B"] and list(table.index) == GS_STATS
    n = len(backtest_b.trade_ledger())
    assert table.loc["Total Transaction Costs", "A"] == pytest.approx(-500.0 * (2 * n - 1)) and table.loc["Total Transaction Costs", "B"] == 0
    assert table.loc["Total PnL", "B"] - table.loc["Total PnL", "A"] == pytest.approx(500.0 * (2 * n - 1)), "costs reduce Total"


def test_section7_additional_risks_are_columns_selectable_by_risk_measure():
    GE = GenericEngine()
    weekly = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1w")
    strategy = Strategy(None, PeriodicTrigger(weekly, AddTradeAction(fx("exposure", 1e6), "1m")))
    backtest = GE.run_backtest(
        strategy,
        start=start_date,
        end=end_date,
        frequency="1b",
        risks=[Price, FXDelta(aggregation_level=AggregationLevel.Type, currency="USD"), IRDelta(aggregation_level="Asset")],
        show_progress=False,
    )
    delta_series = backtest.result_summary[FXDelta(aggregation_level=AggregationLevel.Type, currency="USD")]
    rs = backtest.result_summary
    open_count = pd.Series([sum(1 for e, x in zip(pos(backtest)["entry_ts"], pos(backtest)["exit_ts"]) if e <= D(d) and (pd.isna(x) or x > D(d))) for d in rs.index],
                           index=rs.index)
    np.testing.assert_allclose(delta_series.to_numpy(), 1e4 * open_count.to_numpy())
    np.testing.assert_allclose(rs[IRDeltaParallel].to_numpy(), 1e4 * open_count.to_numpy())
    assert list(rs.columns)[:3] == ["Price", "fx_delta", "dv01"] and rs[Price].equals(rs["Price"])
    assert identity_ok(backtest)


def _ladder(self, ctx):
    return pd.Series({"2Y": 0.3 * self.notional * 0.01, "10Y": 0.7 * self.notional * 0.01})


@dataclass
class LadderForward(NotionalForward):
    def delta_ladder(self, ctx):
        return _ladder(self, ctx)


def test_section7_a_bucketed_risk_is_recorded_as_a_ladder_per_point():
    tpl = toy_tpl(name="lad", target=LadderForward, kwargs={"strike": 4.0, "notional": 1e5}, bind={"delta_ladder": {**bind_method("delta_ladder"), "reduce": "series_to_tenor_dict", "keys": ["2Y", "10Y"]}})
    weekly = PeriodicTriggerRequirements(start_date=start_date, end_date=date(2024, 2, 29), frequency="1w")
    bt = run(Strategy(None, PeriodicTrigger(weekly, AddTradeAction(tpl, None))), end=date(2024, 2, 29), risks=[IRDelta])
    lad = bt.ladder(IRDelta)
    n = pd.Series([len([e for e in pos(bt)["entry_ts"] if e <= D(d)]) for d in lad.index], index=lad.index)
    np.testing.assert_allclose(lad["2Y"], 300.0 * n) and np.testing.assert_allclose(lad["10Y"], 700.0 * n)
    cell = bt.result_summary[IRDelta].iloc[-1]
    assert isinstance(cell, pd.Series) and cell["10Y"] == pytest.approx(700.0 * n.iloc[-1])


# ================================================================== section 8: transaction costs
def test_section8_constant_transaction_model_entry_and_exit():
    instrument = inst("swap")
    action = AddTradeAction(
        instrument,
        trade_duration="1m",
        transaction_cost=ConstantTransactionModel(500),
        transaction_cost_exit=ConstantTransactionModel(250),
    )
    req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1m")
    costly, free = run(Strategy(None, PeriodicTrigger(req, action))), run(Strategy(None, PeriodicTrigger(req, AddTradeAction(instrument, trade_duration="1m"))))
    n_in, n_out = len(pos(costly)), int((pos(costly)["status"] == "closed").sum())
    tc = costly.result_summary["Transaction Costs"]
    assert tc.iloc[-1] == pytest.approx(-(500.0 * n_in + 250.0 * n_out))
    np.testing.assert_allclose(costly.result_summary["Total"] - free.result_summary["Total"], tc, atol=1e-9)
    assert identity_ok(costly)


def _cost_run(model, notional=1e5):
    req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1m")
    return run(Strategy(None, PeriodicTrigger(req, AddTradeAction(inst("swap", notional), trade_duration="1m", transaction_cost=model))))


def test_section8_scaled_transaction_model_by_notional_and_by_price():
    by_notional = _cost_run(ScaledTransactionModel(scaling_type="notional_amount", scaling_level=0.0001))
    p = pos(by_notional)
    trades = len(p) + int((p["status"] == "closed").sum())  # the exit uses the entry model (gs: transaction_cost_exit defaults to transaction_cost)
    assert by_notional.result_summary["Transaction Costs"].iloc[-1] == pytest.approx(-0.0001 * 1e5 * trades)
    by_price = _cost_run(ScaledTransactionModel(scaling_type=Price, scaling_level=0.01))
    p = pos(by_price)
    want = 0.01 * (p["entry_pv"].abs().sum() + p.loc[p["status"] == "closed", "exit_pv"].abs().sum())
    assert by_price.result_summary["Transaction Costs"].iloc[-1] == pytest.approx(-want, rel=1e-12) and want > 0
    assert identity_ok(by_notional) and identity_ok(by_price)


@pytest.mark.parametrize("aggregate_type,per_trade", [(None, 105.0), (TransactionAggType.SUM, 105.0), (TransactionAggType.MAX, 100.0), (TransactionAggType.MIN, 5.0)])
def test_section8_aggregate_transaction_model(aggregate_type, per_trade):
    kw = {} if aggregate_type is None else {"aggregate_type": aggregate_type}
    combined = AggregateTransactionModel(
        transaction_models=(
            ConstantTransactionModel(100),
            ScaledTransactionModel("notional_amount", 0.00005),  # 0.5bp of 1e5 = 5
        ),
        **kw,
    )
    bt = _cost_run(combined)
    p = pos(bt)
    trades = len(p) + int((p["status"] == "closed").sum())
    assert bt.result_summary["Transaction Costs"].iloc[-1] == pytest.approx(-per_trade * trades)


# ================================================================== section 9: best practices
def test_section9_exit_before_add_and_matching_duration_roll_keep_one_position():
    trig_req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1m")
    exit_action, add_action = ExitTradeAction(priceable_names="swap"), AddTradeAction(inst("swap"), None, name="roll")
    ordered = run(Strategy(None, PeriodicTrigger(trig_req, [exit_action, add_action])))
    rolling = run(Strategy(None, PeriodicTrigger(trig_req, AddTradeAction(inst("swap"), "next schedule", name="roll"))))  # 'next schedule' auto-rolls
    matched = run(Strategy(None, PeriodicTrigger(trig_req, AddTradeAction(inst("swap"), "1m", name="roll"))))  # '1m' + '1m'
    monthly = gs_schedule(start_date, end_date, "1m")
    for bt in (ordered, rolling, matched):
        assert entries(bt) == monthly
    assert ordered.result.equity["n_positions"].max() == 1
    pd.testing.assert_series_equal(ordered.result_summary["Total"], rolling.result_summary["Total"])
    n = matched.result.equity["n_positions"]
    assert n.max() == 2 and list(n[n == 2].index.date) == [date(2024, 4, 2), date(2024, 4, 3)], (
        "a '1m' duration rolls from each ENTRY date: 03-04 + 1m = 04-04 overlaps the 04-02 trade (gs schedules from the start date)")


@pytest.mark.parametrize("hedge_first", [False, True])
def test_section9_entry_before_hedge(hedge_first):
    req = PeriodicTriggerRequirements(start_date=start_date, end_date=end_date, frequency="1m")
    entry_trigger = PeriodicTrigger(req, AddTradeAction(inst("entry"), "1m", name="entry"))
    hedge_trigger = PeriodicTrigger(req, HedgeAction(IRDeltaParallel, inst("hedge", 2e5), "1m", name="hedge"))
    order = [hedge_trigger, entry_trigger] if hedge_first else [entry_trigger, hedge_trigger]
    bt = run(Strategy(None, order))
    dv01 = bt.result_summary[IRDeltaParallel]
    assert dv01.loc[gs_schedule(start_date, end_date, "1m")].abs().max() < 1e-9, "the hedge sees the same-day entry (pricebt runs hedges after adds in any trigger order)"
    np.testing.assert_allclose(pos(bt, action="hedge")["entry_quantity"], -0.5)


def test_section9_names_appear_in_the_ledger_and_daily_pnl_is_independent_of_trigger_frequency():
    backtest, _ = _monthly_backtest()
    assert all("_swap_" in n for n in backtest.trade_ledger().index)
    assert len(backtest.result_summary) == len(bdays()) and len(backtest.trade_ledger()) == len(gs_schedule(start_date, end_date, "1m"))
    assert backtest.result_summary["Total"].diff().abs().gt(0).sum() > 100, "daily P&L between the monthly trades"
