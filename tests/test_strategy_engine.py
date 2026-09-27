"""End-to-end strategy runs through the real Engine (toy market): gs parity scenarios, exactly-once evaluation, hedging and band rebalancing."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from conftest import assert_identity
from pricebt.costs import ConstantCost, ScaledCost
from pricebt.engine import Engine, EngineSettings
from pricebt.errors import EngineInvariantError, StrategyError
from pricebt.market import MarketData
from pricebt.orders import OpenOrder
from pricebt.pricable import MarkContext
from pricebt.strategy import (AddScaledTradeAction, AddTradeAction, AddWeightedTradeAction, AggregateTrigger, AggregateTriggerRequirements, CrossingTrigger, CustomTrigger,
                              DateTrigger, DateTriggerRequirements, EarlyExitPositionLimitScaledAction, ExitAllPositionsAction, ExitTradeAction, HedgeAction, MeanReversionTrigger,
                              MeanReversionTriggerRequirements, MktTrigger, MktTriggerRequirements, OrdersGeneratorTrigger, PeriodicTrigger, PeriodicTriggerRequirements,
                              PositionSpec, RebalanceAction, RebalanceTrigger, RiskBandTrigger, RiskBandTriggerRequirements, ScalingActionType, SeriesSource, SignalStore, Strategy,
                              StrategyRiskTrigger, RiskTriggerRequirements, TradeCountTriggerRequirements, TriggerDirection, CrossingTriggerRequirements)
from toy_helpers import toy_tpl
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyForward, ToyMDP, ToyOption
from pricebt.timeutil import Clock, TimeContext, TimeGrid
from test_strategy_support import D, NY, fwd, opt

pytestmark = pytest.mark.core

ABOVE, BELOW = TriggerDirection.ABOVE, TriggerDirection.BELOW


def run(strategy, grid, mdp=None, signals=None, **settings):
    mdp = mdp or ToyMDP()
    settings.setdefault("show_progress", False)
    eng = Engine(grid, MarketData({"primary": mdp}, Clock()), strategy, EngineSettings(**settings), signals=signals)
    return eng.run(), mdp


def rate(mdp, ts):
    return mdp.get_pricer(ts).rate


def swap(name="swap", **kw):
    return toy_tpl(name=name, target=ToyForward, kwargs={"strike": 4.0, "notional": 1e5, **kw})


GRID = TimeGrid.daily("2024-01-02", "2024-02-29", "1b", TimeContext())
DEC = TimeGrid.daily("2021-12-06", "2021-12-10", "1b", TimeContext())


def per(freq, start=None, end=None):
    return PeriodicTriggerRequirements(start_date=start, end_date=end, frequency=freq)


def ledger(rec):
    p = rec.positions
    return [(r.template, r.entry_ts.strftime("%m-%d"), None if pd.isna(r.exit_ts) else r.exit_ts.strftime("%m-%d")) for r in p.itertuples()]


# ------------------------------------------------------------------ gs usage + ledger identity
def test_gs_usage_periodic_add_with_duration_equals_hand_scripted_orders():
    tpl = fwd(notional=10.0, carry_bp_per_day=0.3)
    strat = Strategy(None, [PeriodicTrigger(PeriodicTriggerRequirements(start_date=dt.date(2024, 1, 2), end_date=dt.date(2024, 2, 29), frequency="1w"), AddTradeAction(tpl, trade_duration="1w"))])
    rec, mdp = run(strat, GRID)
    starts = pd.date_range("2024-01-02", "2024-02-27", freq="7D")
    script = {D(d.date().isoformat()): [OpenOrder(tpl, final_ts=D((d + pd.Timedelta(days=7)).date().isoformat()) if d < starts[-1] else None)] for d in starts}
    oracle, _ = run(ScriptedStrategy(script), GRID)
    pd.testing.assert_frame_equal(rec.equity, oracle.equity)
    assert len(rec.positions) == len(starts)
    assert_identity(rec)


def test_pnl_matches_independent_closed_form():
    strat = Strategy.periodic(fwd(notional=10.0), "1w", "1w")
    rec, mdp = run(strat, GRID)
    exp = 0.0
    for r in rec.positions.itertuples():
        out = r.exit_ts if not pd.isna(r.exit_ts) else GRID.end
        exp += 10.0 * (rate(mdp, out) - rate(mdp, r.entry_ts))
    assert rec.equity["equity"].iloc[-1] == pytest.approx(exp, abs=1e-9)


def test_strategy_accepts_single_trigger_list_or_none_and_does_not_mutate_inputs():
    mr = MeanReversionTriggerRequirements("s", 2.0, 5, 5)
    trig = MeanReversionTrigger(mr, AddTradeAction(fwd(), name="X"))
    a = Strategy(None, trig)
    b = Strategy(None, [trig])
    assert len(a.triggers) == len(b.triggers) == 1 and len(Strategy().triggers) == 0
    assert a.triggers[0] is not trig and trig.actions[0].trigger_idx == -1  # copies were bound, the caller's object was not
    mr.current_position = 5
    assert a.triggers[0].trigger_requirements.current_position == 0


def test_duplicate_action_names_rejected_and_auto_names_are_per_strategy():
    with pytest.raises(StrategyError):
        Strategy(None, [PeriodicTrigger(per("1w"), AddTradeAction(fwd(), name="X")), PeriodicTrigger(per("1b"), AddTradeAction(fwd(), name="X"))])
    shared = AddTradeAction(fwd())
    s = Strategy(None, [PeriodicTrigger(per("1w"), shared), PeriodicTrigger(per("1b"), shared)])
    assert [t.actions[0].name for t in s.triggers] == ["Action1", "Action2"]
    s2 = Strategy(None, [PeriodicTrigger(per("1w"), AddTradeAction(fwd()))])
    assert s2.triggers[0].actions[0].name == "Action1"  # no module-global counter


def test_validate_flags_next_schedule_without_a_schedule_trigger():
    ok = Strategy(None, PeriodicTrigger(per("1w"), AddTradeAction(fwd(), "next schedule")))
    bad = Strategy(None, MktTrigger(MktTriggerRequirements("s", 1, ABOVE), AddTradeAction(fwd(), "next schedule")))
    assert ok.validate() == [] and len(bad.validate()) == 1 and "next schedule" in bad.validate()[0]
    latch = Strategy(None, RiskBandTrigger(RiskBandTriggerRequirements("dv01", band=1.0, rearm=0.5), HedgeAction("dv01", fwd(), risk_percentage=50.0)))
    assert "latch" in latch.validate()[0]


# ------------------------------------------------------------------ gs test_generic_engine ports
def test_gs_generic_engine_simple_date_trigger_adds_once_and_holds():
    strat = Strategy(None, DateTrigger(DateTriggerRequirements(dates=[dt.date(2021, 12, 1)]), AddTradeAction(swap(), "1m", name="Action1")))
    grid = TimeGrid.daily("2021-12-01", "2021-12-03", "1b", TimeContext())
    rec, mdp = run(strat, grid)
    assert len(rec.equity) == 3 and len(rec.positions) == 1
    p = rec.positions.iloc[0]
    assert p["status"] == "open" and p["entry_ts"] == D("2021-12-01")  # the 1m exit is beyond the end: held
    assert rec.equity["equity"].iloc[-1] == pytest.approx(1e5 * (rate(mdp, grid.end) - rate(mdp, grid[0])))


def test_gs_exit_action_noarg_under_the_no_same_point_exit_rule():
    """gs: Action1_swap_2021-12-06 opened AND closed on 12-06 (zero-length). pricebt exits touch booked positions only, so
    06->08, 07->08, 08->10, 09->10, 10 open (deliberate deviation)."""
    strat = Strategy(None, [PeriodicTrigger(per("1b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), AddTradeAction(swap(), name="Action1")),
                            PeriodicTrigger(per("2b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), ExitTradeAction(name="ExitAction1"))])
    rec, _ = run(strat, DEC)
    assert [(e, x) for _, e, x in ledger(rec)] == [("12-06", "12-08"), ("12-07", "12-08"), ("12-08", "12-10"), ("12-09", "12-10"), ("12-10", None)]
    assert set(rec.positions["exit_reason"]) == {"signal", ""}


def test_gs_exit_action_emptyresults_exits_before_anything_exists():
    strat = Strategy(None, [PeriodicTrigger(per("2b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), AddTradeAction(swap(), name="Action1")),
                            PeriodicTrigger(per("1b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), ExitTradeAction(name="ExitAction1"))])
    rec, _ = run(strat, DEC)
    assert [(e, x) for _, e, x in ledger(rec)] == [("12-06", "12-07"), ("12-08", "12-09"), ("12-10", None)]


def test_gs_exit_action_bytradename_only_exits_that_template():
    strat = Strategy(None, [PeriodicTrigger(per("1b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), AddTradeAction([swap("swap1"), swap("swap2")], name="Action1")),
                            PeriodicTrigger(per("2b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), ExitTradeAction("swap1", name="ExitAction1"))])
    rec, _ = run(strat, DEC)
    l1 = [(e, x) for t, e, x in ledger(rec) if t == "swap1"]
    l2 = [x for t, _, x in ledger(rec) if t == "swap2"]
    assert l1 == [("12-06", "12-08"), ("12-07", "12-08"), ("12-08", "12-10"), ("12-09", "12-10"), ("12-10", None)]
    assert l2 == [None] * 5 and len(rec.positions) == 10
    nothing, _ = run(Strategy(None, [PeriodicTrigger(per("1b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), AddTradeAction([swap("swap1")], name="A")),
                                     PeriodicTrigger(per("2b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), ExitTradeAction("swap", name="E"))]), DEC)
    assert (nothing.positions["status"] == "open").all()  # 'swap' is not a substring match for 'swap1'


def test_gs_hedge_action_risk_trigger_postcondition_against_independent_greeks():
    call = opt("2024-03-29", "call", strike=4.0, notional=1e4)
    hedge_tpl = fwd("fwd", notional=50.0)
    strat = Strategy([call], StrategyRiskTrigger(RiskTriggerRequirements("dv01", 0, ABOVE), HedgeAction("dv01", hedge_tpl, "2b", name="HedgeAction1")))
    rec, mdp = run(strat, GRID, measures=("dv01",))
    hedges = rec.positions[rec.positions["kind"] == "hedge"]
    assert len(hedges) >= 3
    first = hedges.iloc[0]
    t1 = first["entry_ts"]
    opt_dv01 = ToyOption(strike=4.0, expiry=D("2024-03-29"), notional=1e4).dv01(MarkContext.standalone(mdp.get_pricer(t1)))
    unit = 50.0 * 0.01
    assert first["entry_quantity"] == pytest.approx(-opt_dv01 / unit)  # independent of the engine's measure machinery
    for t in hedges["entry_ts"].unique():
        assert rec.equity.loc[t, "measure_dv01"] == pytest.approx(0.0, abs=1e-9)  # the portfolio dv01 is the target right after a firing
    assert set(hedges["action"]) == {"HedgeAction1"} and set(hedges["tags"]) == {"hedge"}


def test_gs_hedge_without_risk_trigger_order_is_irrelevant_because_hedges_size_last():
    def build(hedge_first):
        add = PeriodicTrigger(per("1b", dt.date(2021, 12, 1), dt.date(2021, 12, 3)), AddTradeAction(opt("2022-06-30", "call"), "1b", name="AddAction1"))
        hed = PeriodicTrigger(per("1b", dt.date(2021, 11, 1), dt.date(2022, 1, 1)), HedgeAction("dv01", fwd("fwd", notional=100.0), "2b", name="HedgeAction1"))
        return Strategy(None, [hed, add] if hedge_first else [add, hed])

    grid = TimeGrid.daily("2021-12-01", "2021-12-03", "1b", TimeContext())
    a, _ = run(build(False), grid, measures=("dv01",))
    b, _ = run(build(True), grid, measures=("dv01",))
    assert a.equity["measure_dv01"].abs().max() < 1e-9  # gs: sum(FXDelta) == 0
    pd.testing.assert_frame_equal(a.equity, b.equity)
    assert len(a.positions[a.positions["kind"] == "hedge"]) == 3


def test_gs_add_scaled_size_is_linear():
    p = swap()
    base, _ = run(Strategy(None, PeriodicTrigger(per("1b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), AddScaledTradeAction(p, "1m", name="q1"))), DEC)
    big, _ = run(Strategy(None, PeriodicTrigger(per("1b", dt.date(2021, 12, 6), dt.date(2021, 12, 10)), AddScaledTradeAction(p, "1m", scaling_level=7, name="q7"))), DEC)
    assert len(base.positions) == len(big.positions) == 5
    np.testing.assert_allclose(big.equity["equity"].to_numpy(), 7 * base.equity["equity"].to_numpy(), atol=1e-9)
    np.testing.assert_allclose(big.equity["cash"].to_numpy(), 7 * base.equity["cash"].to_numpy(), atol=1e-9)


def test_gs_add_scaled_risk_measure_hits_the_level():
    strat = Strategy(None, DateTrigger(DateTriggerRequirements([dt.date(2024, 1, 10)]), AddScaledTradeAction(fwd(notional=20.0), None, "v", ScalingActionType.risk_measure, "dv01", 5.0)))
    rec, _ = run(strat, GRID, measures=("dv01",))
    assert rec.equity["measure_dv01"].iloc[-1] == pytest.approx(5.0)
    assert rec.positions.iloc[0]["entry_quantity"] == pytest.approx(5.0 / (20.0 * 0.01))


def test_gs_add_scaled_nav_spends_the_pot_and_cash_stays_at_minus_l0_every_cycle():
    l0 = 250.0
    cost = [ConstantCost(1.5), ScaledCost("notional", 0.001)]
    from pricebt.costs import AggregateCost

    tpl = fwd("nav", strike=0.0, notional=10.0)  # pv = 10 * rate > 0
    act = AddScaledTradeAction(tpl, "1w", "nav", ScalingActionType.NAV, None, l0, transaction_cost=AggregateCost(cost, "sum"))
    strat = Strategy(None, PeriodicTrigger(per("1w"), act))
    rec, mdp = run(strat, GRID)
    n = len(rec.positions)
    assert n >= 7
    cash_plus_costs = rec.equity["cash"] + rec.equity["tcost"]
    assert (cash_plus_costs - (-l0)).abs().max() < 1e-9  # every exit's proceeds are recycled into the next entry: the pot stays constant
    p0 = rec.positions.iloc[0]
    price = 10.0 * rate(mdp, p0["entry_ts"])
    q = p0["entry_quantity"]
    assert q * price + 1.5 + 0.001 * 10.0 * q == pytest.approx(l0)  # q*P + C(q) == L0, hand-checked on the first order
    assert_identity(rec)


def test_gs_add_weighted_splits_total_size_by_risk():
    strat = Strategy(None, DateTrigger(DateTriggerRequirements([dt.date(2024, 1, 10)]),
                                       AddWeightedTradeAction([fwd("a", notional=1.0), fwd("b", notional=3.0)], None, "w", "dv01", 100.0)))
    rec, _ = run(strat, GRID)
    q = rec.positions.set_index("template")["entry_quantity"]
    assert q["a"] == pytest.approx(25.0) and q["b"] == pytest.approx(75.0)


def test_gs_early_exit_position_limit_scaled_caps_concurrency_and_early_exits():
    act = EarlyExitPositionLimitScaledAction(fwd("e"), "10b", "E", ScalingActionType.size, None, 1, early_exits=[D("2024-01-12")], max_concurrent_pos=3)
    rec, _ = run(Strategy(None, PeriodicTrigger(per("1b", dt.date(2024, 1, 2), dt.date(2024, 1, 31)), act)), GRID)
    live = rec.equity["n_positions"]
    assert live.max() == 3  # the cap binds
    assert (rec.events["kind"] == "order_skipped").any()
    early = rec.positions[rec.positions["entry_ts"] < D("2024-01-12")]
    assert (early["exit_ts"] <= D("2024-01-12")).all() and early["exit_ts"].iloc[-1] == D("2024-01-12")


def test_gs_initial_portfolio_dated_segments_exit_before_next_segment_enters():
    x, z = swap("x"), swap("z")
    strat = Strategy({dt.date(2024, 1, 3): x, dt.date(2024, 1, 10): [x, z], dt.date(2024, 1, 17): [PositionSpec(z, 2.0, tags=("t",))]}, None)
    rec, _ = run(strat, GRID)
    assert ledger(rec) == [("x", "01-03", "01-10"), ("x", "01-10", "01-17"), ("z", "01-10", "01-17"), ("z", "01-17", None)]
    assert rec.positions["kind"].tolist() == ["initial"] * 4 and rec.positions.iloc[3]["entry_quantity"] == 2.0 and rec.positions.iloc[3]["tags"] == "t"
    assert rec.trades.iloc[0]["cash"] == pytest.approx(-1e5 * (rate(ToyMDP(), D("2024-01-03")) - 4.0))
    assert_identity(rec)


def test_initial_portfolio_list_enters_at_first_point_and_key_before_start_raises():
    rec, _ = run(Strategy([swap("x"), PositionSpec(swap("y"), -1.0)], None), GRID)
    assert ledger(rec) == [("x", "01-02", None), ("y", "01-02", None)] and rec.positions["entry_quantity"].tolist() == [1.0, -1.0]
    with pytest.raises(StrategyError):
        run(Strategy({dt.date(2023, 12, 1): swap()}, None), GRID)


def test_initial_holdings_are_visible_to_triggers_of_the_same_point():
    seen = []
    strat = Strategy([swap("x")], CustomTrigger(target=lambda ts, view: seen.append(view.n_positions()) or False))
    run(strat, GRID)
    assert seen[0] == 1


# ------------------------------------------------------------------ rolling / phases / next schedule
def test_next_schedule_rolls_each_position_onto_the_next_trigger_date():
    strat = Strategy(None, PeriodicTrigger(per("1w"), AddTradeAction(fwd(), "next schedule", name="Roll")))
    rec, _ = run(strat, GRID)
    p = rec.positions
    assert (p["exit_ts"].iloc[:-1].to_numpy() == p["entry_ts"].iloc[1:].to_numpy()).all()  # chained: exit_k == entry_(k+1)
    assert pd.isna(p["exit_ts"].iloc[-1])  # the last schedule date holds to the end


def test_exit_phase_runs_before_add_regardless_of_action_order_roll_semantics():
    def build(order):
        acts = {"exit": ExitAllPositionsAction(name="Roll"), "add": AddTradeAction(fwd(), name="Carry")}
        return Strategy(None, PeriodicTrigger(per("1w"), [acts[k] for k in order]))

    a, _ = run(build(["exit", "add"]), GRID)
    b, _ = run(build(["add", "exit"]), GRID)
    pd.testing.assert_frame_equal(a.equity, b.equity)
    assert ledger(a)[0][2] == ledger(a)[1][1]  # each week's position is closed at the point the next one is opened
    for rec in (a, b):
        roll = rec.trades[rec.trades["ts"] == D("2024-01-09")]
        assert roll["kind"].tolist() == ["close", "open"]  # EXIT phase is submitted (and booked) before ADD, whatever the list order


def test_path_dependent_trigger_hedges_the_same_steps_add_but_snapshot_mode_hedges_one_point_late():
    def build(mode):
        add = PeriodicTrigger(per("1w"), AddTradeAction(opt("2024-06-28", "call"), "1w", name="Add"))
        risk = StrategyRiskTrigger(RiskTriggerRequirements("dv01", 1.0, ABOVE), HedgeAction("dv01", fwd("h", notional=100.0), "1w", name="H"))
        return Strategy(None, [add, risk], eval_mode=mode)

    staged, _ = run(build("staged"), GRID)
    snap, _ = run(build("snapshot"), GRID)
    add_ts = staged.positions[staged.positions["template"] == "call"]["entry_ts"].iloc[0]
    hedge_ts = staged.positions[staged.positions["kind"] == "hedge"]["entry_ts"].iloc[0]
    assert hedge_ts == add_ts  # the gs 040301 idiom: the freshly added option is hedged in the SAME step
    snap_hedge = snap.positions[snap.positions["kind"] == "hedge"]["entry_ts"].iloc[0]
    assert snap_hedge > add_ts  # a snapshot evaluation cannot see this step's pending add
    with pytest.raises(StrategyError):
        Strategy(None, [], eval_mode="bogus")


def test_fill_lag_one_hedge_sees_the_pending_add():
    strat = Strategy(None, [PeriodicTrigger(per("1w"), AddTradeAction(fwd("add", notional=10.0), "1w", name="Add")),
                            PeriodicTrigger(per("1w"), HedgeAction("dv01", fwd("hedge", notional=5.0), "1w", name="H"))])
    rec, _ = run(strat, GRID, fill_lag=1, measures=("dv01",))
    h = rec.positions[rec.positions["kind"] == "hedge"].iloc[0]
    assert h["entry_quantity"] == pytest.approx(-2.0)  # -(10*0.01)/(5*0.01), sized on the pending add
    filled = rec.positions["entry_ts"].iloc[0]
    assert rec.equity.loc[filled, "measure_dv01"] == pytest.approx(0.0, abs=1e-12)  # both legs booked at the same next point: net zero


# ------------------------------------------------------------------ triggers end to end
S = pd.Series({dt.date(2021, 10, 1): 0.984274, dt.date(2021, 10, 4): 1.000706, dt.date(2021, 10, 5): 1.044055, dt.date(2021, 10, 6): 1.095361, dt.date(2021, 10, 7): 1.129336,
               dt.date(2021, 10, 8): 1.182954, dt.date(2021, 10, 12): 1.200108, dt.date(2021, 10, 13): 1.220607, dt.date(2021, 10, 14): 1.172837, dt.date(2021, 10, 15): 1.163660,
               dt.date(2021, 10, 18): 1.061084, dt.date(2021, 10, 19): 1.025012, dt.date(2021, 10, 20): 1.018035, dt.date(2021, 10, 21): 1.080751, dt.date(2021, 10, 22): 1.069340,
               dt.date(2021, 10, 25): 1.033413})
SGRID = TimeGrid([D(d.isoformat()) for d in S.index], TimeContext())


def test_gs_mkt_trigger_level_triggered_adds_at_every_state_above_and_crossing_only_once():
    src = SeriesSource(S, "fill_forward", name="s")
    lvl, _ = run(Strategy(None, MktTrigger(MktTriggerRequirements(src, 1.1, ABOVE), AddTradeAction(opt("2021-10-20", "o"), "expiry", name="A"))), SGRID, signals=SignalStore([src]))
    assert [e for _, e, _ in ledger(lvl)] == ["10-07", "10-08", "10-12", "10-13", "10-14", "10-15"]  # gs: len(ledger) == 6
    cross, _ = run(Strategy(None, CrossingTrigger(CrossingTriggerRequirements(src, 1.1, ABOVE), AddTradeAction(fwd("c"), name="A"))), SGRID, signals=SignalStore([src]))
    assert [e for _, e, _ in ledger(cross)] == ["10-07"]
    assert (lvl.positions["exit_ts"] == D("2021-10-20")).all()  # the 'expiry' attribute of the built ToyOption is the exit (gs: 'expiration_date')


def test_signal_store_is_built_automatically_from_the_strategy():
    src = SeriesSource(S, "fill_forward", name="s")
    strat = Strategy(None, MktTrigger(MktTriggerRequirements(src, 1.1, ABOVE), AddTradeAction(fwd("m"), name="A")))
    assert [n.source for n in strat.required_signals()] == [src]
    rec = strat.backtest(SGRID, ToyMDP())
    assert len(rec.positions) == 6


def test_mean_reversion_end_to_end_offset_trade_pnl_is_known_in_closed_form():
    days = pd.bdate_range("2024-01-02", periods=8)
    src = SeriesSource(pd.Series([100.0] * 5 + [130.0, 100.0, 100.0], index=days), "fill_forward", name="s")
    mr = MeanReversionTrigger(MeanReversionTriggerRequirements(src, 2.0, 5, 5), AddTradeAction(fwd("mr", notional=10.0), name="MR"))
    grid = TimeGrid([D(d.date().isoformat()) for d in days], TimeContext())
    rec, mdp = run(Strategy(None, mr), grid, signals=SignalStore([src]))
    assert rec.positions["entry_quantity"].tolist() == [-1.0, 1.0]  # short the spike, then the offsetting buy on the reversion
    r5, r6, end = (rate(mdp, D(days[5].date().isoformat())), rate(mdp, D(days[6].date().isoformat())), rate(mdp, grid.end))
    assert rec.equity["equity"].iloc[-1] == pytest.approx(10.0 * ((r5 - r6) + 0 * end), abs=1e-9)  # short at r5, long at r6, both marked at the end
    assert not mr.trigger_requirements.current_position  # the caller's trigger was never run
    rec2, _ = run(Strategy(None, mr), grid, signals=SignalStore([src]))
    pd.testing.assert_frame_equal(rec.equity, rec2.equity)  # a second run is identical: state was reset


def test_aggregate_gradual_entry_capped_by_trade_count():
    src = SeriesSource(pd.Series({dt.date(2024, 1, 2) + dt.timedelta(days=i): 10.0 for i in range(60)}), "fill_forward", name="s")
    agg = AggregateTrigger(AggregateTriggerRequirements([MktTriggerRequirements(src, 5.0, ABOVE), TradeCountTriggerRequirements(3, BELOW)]), AddTradeAction(fwd("g"), name="G"))
    rec, _ = run(Strategy(None, agg), GRID, signals=SignalStore([src]))
    assert len(rec.positions) == 3 and rec.equity["n_positions"].max() == 3  # one entry per point until 3 are live, then no more (pending adds are counted)
    assert [e for _, e, _ in ledger(rec)] == ["01-02", "01-03", "01-04"]


def test_strategy_risk_stop_loss_exits_everything_at_the_point_after_pnl_breaches():
    """pnl is as of the previous mark: the first point after the book's P&L drops below the barrier closes everything booked."""
    strat = Strategy(None, [PeriodicTrigger(per("1w"), AddTradeAction(fwd("a", notional=100.0), name="Add")),
                            StrategyRiskTrigger(RiskTriggerRequirements("pnl", -20.0, BELOW), ExitAllPositionsAction(name="Stop"))])
    rec, mdp = run(strat, GRID)
    entries = [D(d.date().isoformat()) for d in pd.date_range("2024-01-02", "2024-02-27", freq="7D")]
    pts = list(GRID.points)
    first_stop = None
    for prev, t in zip(pts, pts[1:]):
        booked = [e for e in entries if e <= prev]
        pnl_prev = sum(100.0 * (rate(mdp, prev) - rate(mdp, e)) for e in booked)
        if booked and pnl_prev < -20.0:
            first_stop = t
            break
    assert first_stop is not None and first_stop == D("2024-02-13")  # oracle: independent of the engine's pnl bookkeeping
    stopped = rec.positions[rec.positions["exit_ts"] == first_stop]
    assert len(stopped) == 6 and (stopped["exit_reason"] == "exit_all").all()  # the six positions booked before it; the one entered AT first_stop is untouched
    assert (stopped["entry_ts"] < first_stop).all()
    assert_identity(rec)


def test_orders_generator_trigger_submits_user_orders_through_submit_orders_action():
    class Gen(OrdersGeneratorTrigger):
        def get_trigger_times(self):
            return [dt.time(17, 0)]

        def generate_orders(self, ts, view):
            return [OpenOrder(fwd("gen"), 1.0)] if ts.day % 2 == 0 else []

    rec, _ = run(Strategy(None, Gen()), GRID)
    assert set(rec.positions["action"]) == {"Action1"} and len(rec.positions) > 5
    assert all(pd.Timestamp(t).day % 2 == 0 for t in rec.positions["entry_ts"])
    assert set(rec.positions["kind"]) == {"add"}


# ------------------------------------------------------------------ exactly once, determinism
def test_every_trigger_is_evaluated_exactly_once_per_timeline_point_including_off_grid_exits():
    calls = {"simple": [], "path": []}
    off_grid = pd.Timestamp("2024-01-20 12:00", tz=NY)  # a Saturday: the engine adds it to the timeline when the position is booked
    strat = Strategy(None, [
        CustomTrigger(target=lambda ts, view: calls["simple"].append(ts) or False, calc="simple"),
        CustomTrigger(target=lambda ts, view: calls["path"].append(ts) or False, calc="path_dependent"),
        DateTrigger(DateTriggerRequirements([dt.date(2024, 1, 10)]), AddTradeAction(fwd(), off_grid)),
    ])
    rec, _ = run(strat, GRID)
    n = len(rec.equity)
    assert n == len(GRID) + 1 and off_grid in rec.equity.index
    for k in calls:
        assert calls[k] == list(rec.equity.index)  # exactly once at every point, in timeline order


def test_double_evaluation_and_time_regression_are_engine_invariant_errors():
    from test_strategy_support import FakeView, mk_ctx

    run_ = Strategy(None, CustomTrigger(target=lambda ts, view: False)).start(mk_ctx("2024-01-02", "2024-02-29"))
    view = FakeView()
    run_.step(D("2024-01-03"), view, lambda o: None)
    with pytest.raises(EngineInvariantError):
        run_.step(D("2024-01-03"), view, lambda o: None)
    with pytest.raises(EngineInvariantError):
        run_.step(D("2024-01-02"), view, lambda o: None)


def test_strategy_runs_are_repeatable_and_use_independent_state():
    src = SeriesSource(S, "fill_forward", name="s")
    strat = Strategy(None, [CrossingTrigger(CrossingTriggerRequirements(src, 1.1, ABOVE), AddTradeAction(fwd("c"), "1w", name="A")),
                            PeriodicTrigger(per("1w"), AddTradeAction(fwd("p"), "next schedule", name="P"))])
    a, _ = run(strat, SGRID, signals=SignalStore([src]))
    b, _ = run(strat, SGRID, signals=SignalStore([src]))
    pd.testing.assert_frame_equal(a.equity, b.equity)
    pd.testing.assert_frame_equal(a.positions, b.positions)
    eng_store = SignalStore([src])
    market = MarketData({"primary": ToyMDP()}, Clock())
    eng = Engine(SGRID, market, strat, EngineSettings(show_progress=False), signals=eng_store)
    r1, r2 = eng.run(), eng.run()  # the SAME engine twice, with the SAME signal store
    pd.testing.assert_frame_equal(r1.equity, r2.equity)
    pd.testing.assert_frame_equal(r1.equity, a.equity)


# ------------------------------------------------------------------ delta band + convexity (gamma) hedge
def band_strategy(order):
    opt1 = opt("2024-03-29", "opt1", strike=4.0, notional=1e4)
    opt2 = opt("2024-09-30", "opt2", strike=4.0, notional=1e4)
    fwd_h = fwd("fwdh", notional=100.0)
    g = RiskBandTrigger(RiskBandTriggerRequirements("gamma", band=0.05), HedgeAction("gamma", opt2, name="GammaHedge"))
    d = RiskBandTrigger(RiskBandTriggerRequirements("dv01", band=1.0), HedgeAction("dv01", fwd_h, name="DeltaHedge"))
    return Strategy([opt1], [g, d] if order == "gamma_first" else [d, g])


def test_delta_band_with_gamma_hedge_leaves_delta_and_gamma_at_target_after_each_firing():
    rec, mdp = run(band_strategy("gamma_first"), GRID, measures=("dv01", "gamma"))
    hedges = rec.positions[rec.positions["kind"] == "hedge"]
    g_rows = hedges[hedges["action"] == "GammaHedge"]["entry_ts"].unique()
    d_rows = hedges[hedges["action"] == "DeltaHedge"]["entry_ts"].unique()
    assert len(g_rows) >= 2 and len(d_rows) >= 2  # both bands actually fire on this path
    initial = ToyOption(strike=4.0, expiry=D("2024-03-29"), notional=1e4)
    g0 = initial.convexity(MarkContext.standalone(mdp.get_pricer(GRID[0])))
    assert abs(g0) > 0.05  # the initial book breaches the gamma band on day one (independent oracle for the trigger)
    for t in g_rows:
        assert rec.equity.loc[t, "measure_gamma"] == pytest.approx(0.0, abs=1e-9)  # gamma hedge sized to the target
        assert rec.equity.loc[t, "measure_dv01"] == pytest.approx(0.0, abs=1e-8)  # the delta trigger ran AFTER it and saw the post-gamma-hedge book
    for t in d_rows:
        assert rec.equity.loc[t, "measure_dv01"] == pytest.approx(0.0, abs=1e-8)
    quiet = rec.equity.drop(index=list(g_rows) + list(d_rows))
    assert (quiet["measure_dv01"].abs() <= 1.0 + 1e-9).all()  # between firings the delta stays inside its band
    assert_identity(rec)


def test_reversed_trigger_order_leaves_a_delta_residual_after_the_gamma_hedge():
    """Control: the correct order is doing real work (delta after gamma), the check is not vacuous."""
    good, _ = run(band_strategy("gamma_first"), GRID, measures=("dv01", "gamma"))
    bad, _ = run(band_strategy("delta_first"), GRID, measures=("dv01", "gamma"))
    rows = lambda rec: rec.positions[rec.positions["action"] == "GammaHedge"]["entry_ts"].unique()
    assert max(abs(good.equity.loc[t, "measure_dv01"]) for t in rows(good)) < 1e-8
    assert max(abs(bad.equity.loc[t, "measure_dv01"]) for t in rows(bad)) > 1.0


def test_rebalance_trigger_brings_the_book_dv01_back_to_target_with_a_new_position():
    core = fwd("core", notional=100.0)
    reb = fwd("reb", notional=100.0)
    strat = Strategy([PositionSpec(core, 10.0, tags=("core",))],
                     RebalanceTrigger(RiskBandTriggerRequirements("dv01", band=5.0, emit="rebalance"), RebalanceAction(reb, measure="dv01", name="Reb")))
    rec, _ = run(strat, GRID, measures=("dv01",))
    assert rec.equity["measure_dv01"].iloc[0] == pytest.approx(0.0, abs=1e-9)  # dv01 10 -> brought to the target 0 by a rebalance of the OTHER template
    p = rec.positions.set_index("template")
    assert p.loc["reb", "entry_quantity"] == pytest.approx(-10.0) and p.loc["reb", "kind"] == "rebalance"


def test_rebalance_trigger_with_ordinary_flat_start_never_fires():
    strat = Strategy(None, RebalanceTrigger(RiskBandTriggerRequirements("dv01", band=5.0, emit="rebalance"), RebalanceAction(fwd("r"), measure="dv01", name="Reb")))
    rec, _ = run(strat, GRID)
    assert rec.positions.empty


def test_intraday_periodic_trigger_on_a_minute_grid_with_fixed_duration_exits():
    from pricebt.strategy import IntradayPeriodicTrigger

    grid = TimeGrid.intraday("2024-01-03", "2024-01-03", "1min", TimeContext())
    strat = Strategy(None, IntradayPeriodicTrigger(start_time=dt.time(9, 0), end_time=dt.time(12, 0), frequency="1h", actions=AddTradeAction(fwd("m"), "15min", name="A")))
    rec, _ = run(strat, grid)
    hours = [pd.Timestamp(f"2024-01-03 {h}:00", tz=NY) for h in (9, 10, 11, 12)]
    assert list(rec.positions["entry_ts"]) == hours
    assert list(rec.positions["exit_ts"]) == [h + pd.Timedelta(minutes=15) for h in hours[:3]] + [hours[3] + pd.Timedelta(minutes=15)]
    assert len(rec.equity) == len(grid)  # the trigger instants were already grid points: no extra timeline points


def test_strategy_cash_accrual_is_applied_by_the_backtest_helper():
    from pricebt.costs import ConstantCashAccrual

    strat = Strategy(None, DateTrigger(DateTriggerRequirements([dt.date(2024, 1, 3)]), AddTradeAction(fwd("c", strike=0.0, notional=100.0), name="A")), ConstantCashAccrual(0.05, 365.0, "compound"))
    rec = strat.backtest(GRID, ToyMDP())
    assert rec.equity["interest_cum"].iloc[-1] != 0.0
    plain = Strategy(None, DateTrigger(DateTriggerRequirements([dt.date(2024, 1, 3)]), AddTradeAction(fwd("c", strike=0.0, notional=100.0), name="A"))).backtest(GRID, ToyMDP())
    assert plain.equity["interest_cum"].iloc[-1] == 0.0


def test_custom_action_submits_user_built_orders_with_completed_metadata():
    def fn(ts, view, info):
        return [OpenOrder(fwd("cu"), 2.0, final_ts=None)] if ts == D("2024-01-10") else []

    from pricebt.strategy import CustomAction, PeriodicTriggerRequirements as PTR

    rec, _ = run(Strategy(None, PeriodicTrigger(PTR(frequency="1b"), CustomAction(fn, name="Mine"))), GRID)
    (p,) = rec.positions.to_dict("records")
    assert (p["action"], p["kind"], p["template"], p["entry_quantity"]) == ("Mine", "add", "cu", 2.0)
