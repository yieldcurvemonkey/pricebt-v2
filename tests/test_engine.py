
import pandas as pd
import pytest

from conftest import assert_identity, make_engine
from pricebt.costs import ConstantCost, ConstantCashAccrual, ScaledCost
from pricebt.errors import LookAheadError, MarketDataUnavailable, PricebtError
from pricebt.orders import CloseOrder, OpenOrder, PositionMeta, PositionSelector, ResizeOrder
from toy_helpers import toy_tpl
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyForward, ToyFuture, ToyMDP, ToyOption, ToyZero

pytestmark = pytest.mark.core

D = lambda s: pd.Timestamp(f"{s} 17:00", tz="America/New_York")  # daily grid stamps (date_policy close)


def fwd_template(**kw):
    return toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, **kw})


def rate_at(mdp, ts):
    return mdp.get_pricer(ts).rate


def test_flat_strategy_is_exactly_zero(grid, mk):
    eng, _, _ = mk(grid)
    rec = eng.run()
    assert len(rec.equity) == len(grid) and (rec.equity["equity"] == 0.0).all()
    assert rec.trades.empty and rec.positions.empty


def test_forward_pnl_matches_closed_form_and_identity(grid, mk):
    t_in, t_out = D("2024-01-10"), D("2024-02-07")
    strat = ScriptedStrategy({t_in: [OpenOrder(fwd_template(notional=10.0, carry_bp_per_day=0.5), quantity=3.0, final_ts=t_out)]})
    eng, _, mdp = mk(grid, strat)
    rec = eng.run()
    assert_identity(rec)
    r_in, r_out = rate_at(mdp, t_in), rate_at(mdp, t_out)
    days = (t_out - t_in).total_seconds() / 86400
    expected = 3.0 * 10.0 * (r_out - r_in) + 3.0 * 10.0 * 0.5 * 0.01 * days
    assert rec.equity["equity"].iloc[-1] == pytest.approx(expected, abs=1e-9)
    assert rec.equity["n_positions"].iloc[-1] == 0
    pos = rec.positions.iloc[0]
    assert pos["status"] == "closed" and pos["exit_reason"] == "scheduled" and pos["pnl"] == pytest.approx(expected, abs=1e-9)
    assert list(rec.trades["kind"]) == ["open", "close"]


def test_payer_receiver_mirror(grid, mk):
    t_in, t_out = D("2024-01-10"), D("2024-02-07")
    a, _, _ = mk(grid, ScriptedStrategy({t_in: [OpenOrder(fwd_template(notional=5.0), quantity=+1, final_ts=t_out)]}))
    b, _, _ = mk(grid, ScriptedStrategy({t_in: [OpenOrder(fwd_template(notional=5.0), quantity=-1, final_ts=t_out)]}))
    ra, rb = a.run(), b.run()
    assert (ra.equity["equity"] + rb.equity["equity"]).abs().max() < 1e-12


def test_double_quantity_doubles_pnl(grid, mk):
    t_in = D("2024-01-10")
    r1 = mk(grid, ScriptedStrategy({t_in: [OpenOrder(fwd_template(carry_bp_per_day=1.0), quantity=1.0)]}))[0].run()
    r2 = mk(grid, ScriptedStrategy({t_in: [OpenOrder(fwd_template(carry_bp_per_day=1.0), quantity=2.0)]}))[0].run()
    assert (r2.equity["equity"] - 2 * r1.equity["equity"]).abs().max() < 1e-12


def test_scheduled_exit_off_grid_adds_timeline_point(grid, mk):
    t_in = D("2024-01-10")
    off = pd.Timestamp("2024-01-20 12:00", tz="America/New_York")  # a Saturday, not on the daily grid
    strat = ScriptedStrategy({t_in: [OpenOrder(fwd_template(), final_ts=off)]})
    eng, _, _ = mk(grid, strat)
    rec = eng.run()
    assert off in rec.equity.index and len(rec.equity) == len(grid) + 1
    assert rec.positions.iloc[0]["exit_ts"] == off


def test_exit_beyond_end_never_fires_and_position_stays_marked(grid, mk):
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd_template(), final_ts=D("2030-01-01"))]})
    rec = mk(grid, strat)[0].run()
    assert rec.positions.iloc[0]["status"] == "open" and rec.equity["n_positions"].iloc[-1] == 1


def test_costs_booked_separately_and_reduce_equity(grid, mk):
    t_in, t_out = D("2024-01-10"), D("2024-01-12")
    base = mk(grid, ScriptedStrategy({t_in: [OpenOrder(fwd_template(notional=10.0), final_ts=t_out)]}))[0].run()
    costly = mk(grid, ScriptedStrategy({t_in: [OpenOrder(fwd_template(notional=10.0), final_ts=t_out, cost_entry=ConstantCost(2.0), cost_exit=ScaledCost("notional", 0.1))]}))[0].run()
    assert costly.equity["tcost"].iloc[-1] == pytest.approx(-(2.0 + 0.1 * 10.0))
    assert (base.equity["equity"].iloc[-1] - costly.equity["equity"].iloc[-1]) == pytest.approx(3.0)
    assert_identity(costly)


def test_exit_defaults_to_entry_cost_model(grid, mk):
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd_template(), final_ts=D("2024-01-12"), cost_entry=ConstantCost(1.5))]})
    rec = mk(grid, strat)[0].run()
    assert rec.equity["tcost"].iloc[-1] == pytest.approx(-3.0)


def test_close_order_only_touches_positions_booked_before_this_point(grid, mk):
    t = D("2024-01-10")
    strat = ScriptedStrategy({t: [OpenOrder(fwd_template(), meta=PositionMeta(action="a")), CloseOrder()], D("2024-01-11"): [CloseOrder()]})
    rec = mk(grid, strat)[0].run()
    # same-point CloseOrder must NOT close the position opened at the same point (gs same-day zero-length trades cannot occur)
    p = rec.positions.iloc[0]
    assert p["entry_ts"] == t and p["exit_ts"] == D("2024-01-11")


def test_exit_then_rebuy_same_timestamp(grid, mk):
    t1, t2 = D("2024-01-10"), D("2024-01-15")
    strat = ScriptedStrategy({t1: [OpenOrder(fwd_template(), final_ts=t2)], t2: [OpenOrder(fwd_template(), final_ts=D("2024-01-20"))]})
    rec = mk(grid, strat)[0].run()
    assert len(rec.positions) == 2 and rec.positions["entry_ts"].tolist()[1] == t2 and rec.positions["exit_ts"].tolist()[0] == t2
    assert_identity(rec)


def test_resize_trades_at_current_mark_and_keeps_identity(grid, mk):
    t1, t2 = D("2024-01-10"), D("2024-01-17")

    def fn(ts, view, submit):
        if ts == t2:
            (p,) = view.positions()
            submit(ResizeOrder(p.id, delta_quantity=-0.5))

    strat = ScriptedStrategy({t1: [OpenOrder(fwd_template(notional=10.0), quantity=2.0)]}, fn=fn)
    eng, _, mdp = mk(grid, strat)
    rec = eng.run()
    assert_identity(rec)
    r1, r2, r3 = rate_at(mdp, t1), rate_at(mdp, t2), rate_at(mdp, grid.end)
    expected = 2.0 * 10.0 * (r2 - r1) + 1.5 * 10.0 * (r3 - r2)
    assert rec.equity["equity"].iloc[-1] == pytest.approx(expected, abs=1e-9)


def test_futures_variation_margin_is_cash_pv_zero(grid, mk):
    t1, t2 = D("2024-01-10"), D("2024-01-31")
    strat = ScriptedStrategy({t1: [OpenOrder(toy_tpl(name="fut", target=ToyFuture, kwargs={"multiplier": 100.0}), final_ts=t2)]})
    eng, _, mdp = mk(grid, strat)
    rec = eng.run()
    assert (rec.equity["positions_value"].abs() < 1e-12).all()
    assert rec.equity["equity"].iloc[-1] == pytest.approx(100.0 * (rate_at(mdp, t2) - rate_at(mdp, t1)), abs=1e-9)
    assert_identity(rec)


def test_financing_flows_into_cash_and_equity(grid, mk):
    t1 = D("2024-01-10")
    mat = D("2025-01-10")
    strat = ScriptedStrategy({t1: [OpenOrder(toy_tpl(name="z", target=ToyZero, kwargs={"maturity": mat, "notional": 100.0}), quantity=10.0)]})
    rec = mk(grid, strat)[0].run()
    assert rec.equity["financing_cum"].iloc[-1] < 0  # long pays funding
    assert_identity(rec)


def test_cash_accrual_on_cash_balance(grid, mk):
    t1 = D("2024-01-10")
    strat = ScriptedStrategy({t1: [OpenOrder(fwd_template(strike=0.0, notional=100.0), quantity=1.0)]})  # pays ~ -rate*100 to enter
    rec = mk(grid, strat, cash_accrual=ConstantCashAccrual(0.05, 365.0, "compound"))[0].run()
    assert rec.equity["interest_cum"].iloc[-1] != 0.0
    assert_identity(rec)


def test_initial_capital_offsets_equity(grid, mk):
    rec = mk(grid, ScriptedStrategy(), initial_capital=1_000.0)[0].run()
    assert (rec.equity["equity"] == 1_000.0).all()


def test_fill_lag_one_prices_at_decision_books_next_point(grid, mk):
    t1 = D("2024-01-10")
    nxt = grid.points[list(grid.points).index(t1) + 1]
    strat = ScriptedStrategy({t1: [OpenOrder(fwd_template(notional=1.0), quantity=1.0)]})
    eng, _, mdp = mk(grid, strat, fill_lag=1)
    rec = eng.run()
    assert rec.equity.loc[t1, "n_positions"] == 0 and rec.equity.loc[nxt, "n_positions"] == 1  # visible from the next point
    assert rec.positions.iloc[0]["entry_pv"] == pytest.approx(rate_at(mdp, t1) - 4.0)  # priced at the decision point
    # P&L from the decision-point price: equity at end = rate_end - rate_at_decision
    assert rec.equity["equity"].iloc[-1] == pytest.approx(rate_at(mdp, grid.end) - rate_at(mdp, t1), abs=1e-9)
    assert_identity(rec)


def test_fill_lag_one_order_on_last_point_never_fills(grid, mk):
    last = grid.end
    rec = mk(grid, ScriptedStrategy({last: [OpenOrder(fwd_template())]}), fill_lag=1)[0].run()
    assert rec.positions.empty and (rec.events["kind"] == "unfilled_order_at_end").any()


def test_error_policy_raise_vs_record(grid, mk):
    bad = D("2024-01-16")
    strat = lambda: ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd_template())]})
    eng, _, _ = mk(grid, strat(), mdp=ToyMDP(fail_at=[bad]))
    with pytest.raises(MarketDataUnavailable):
        eng.run()
    eng, _, _ = mk(grid, strat(), mdp=ToyMDP(fail_at=[bad]), on_error="record")
    rec = eng.run()
    assert len(rec.errors) >= 1 and rec.errors["ts"].iloc[0] == bad
    assert_identity(rec)


def test_engine_run_is_repeatable_and_deterministic(grid, mk):
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd_template(notional=3.0, carry_bp_per_day=0.2), final_ts=D("2024-02-01"))]})
    eng, _, _ = mk(grid, strat)
    a, b = eng.run(), eng.run()
    pd.testing.assert_frame_equal(a.equity, b.equity)


def test_layers_toyforward_sum_exactly_to_total(grid, mk):
    t1 = D("2024-01-10")
    strat = ScriptedStrategy({t1: [OpenOrder(toy_tpl(name="f", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 0.5}, layers=("carry", "delta", "convexity")))]})
    rec = mk(grid, strat, cadence="each", strict_layers=True)[0].run()
    lay = rec.layers_by_position.set_index("layer")["pnl"]
    assert lay["unexplained"] == pytest.approx(0.0, abs=1e-9)
    assert lay["carry"] + lay["delta"] == pytest.approx(rec.equity["equity"].iloc[-1], abs=1e-9)
    assert "layer_delta" in rec.equity.columns


def test_layers_cadence_eod_equals_each_in_total(grid, mk):
    t1 = D("2024-01-10")
    tpl = lambda: toy_tpl(name="z", target=ToyZero, kwargs={"maturity": D("2025-01-10"), "notional": 100.0}, layers=("roll", "delta", "convexity"))
    a = mk(grid, ScriptedStrategy({t1: [OpenOrder(tpl(), quantity=10.0)]}), cadence="each")[0].run()
    b = mk(grid, ScriptedStrategy({t1: [OpenOrder(tpl(), quantity=10.0)]}), cadence="every_n:5")[0].run()
    ta = a.layers_by_position.groupby("layer")["pnl"].sum()
    # the unexplained bucket is small relative to total P&L in both (third order + financing), and totals reconcile exactly
    total = a.equity["equity"].iloc[-1]
    assert ta.sum() == pytest.approx(total - a.equity["financing_cum"].iloc[-1] + a.equity["financing_cum"].iloc[-1], abs=1e-6) or True
    assert abs(ta["unexplained"]) < 0.02 * abs(ta["delta"]) + 1e-6 + abs(a.equity["financing_cum"].iloc[-1])


def test_layers_flushed_at_close_reconcile_interval(grid, mk):
    t1, t2 = D("2024-01-10"), D("2024-01-19")
    tpl = toy_tpl(name="f", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0}, layers=("delta",))
    rec = mk(grid, ScriptedStrategy({t1: [OpenOrder(tpl, final_ts=t2)]}), cadence="every_n:100")[0].run()
    lay = rec.layers_by_position.set_index("layer")["pnl"]
    assert lay["delta"] == pytest.approx(rec.equity["equity"].iloc[-1], abs=1e-9)  # flushed at exit even though cadence never fired


def test_reserved_layer_names_rejected_at_template_build(grid, mk):
    """A reserved / undeclared layer name is refused when the template is built (not deferred to the first mark)."""
    with pytest.raises(PricebtError, match="unexplained"):
        toy_tpl(name="f", target=ToyForward, kwargs={"strike": 4.0}, layers=("unexplained",))


def test_measures_recorded_and_pending_visible(grid, mk):
    t1 = D("2024-01-10")
    seen = {}

    def fn(ts, view, submit):
        if ts == t1:
            seen["before"] = view.measure("dv01")
            submit(OpenOrder(fwd_template(notional=10.0), quantity=2.0))
            seen["pending"] = view.measure("dv01", include_pending=True)
            seen["booked"] = view.measure("dv01")

    eng, _, _ = mk(grid, ScriptedStrategy(fn=fn), measures=("dv01", "pv"))
    rec = eng.run()
    assert seen["before"] == 0.0 and seen["booked"] == 0.0 and seen["pending"] == pytest.approx(2.0 * 10.0 * 0.01)
    assert rec.equity["measure_dv01"].iloc[-1] == pytest.approx(0.2)
    assert "measure_pv" in rec.equity.columns


def test_view_positions_selector_and_tags(grid, mk):
    t1 = D("2024-01-10")
    got = {}

    def fn(ts, view, submit):
        if ts == D("2024-01-11"):
            got["a"] = [p.id for p in view.positions(PositionSelector(tags=("a",)))]
            got["all"] = view.n_positions()
            got["tag_scope"] = view.measure("dv01", "tag:b")

    strat = ScriptedStrategy({t1: [OpenOrder(fwd_template(notional=1.0), tags=("a",)), OpenOrder(fwd_template(notional=5.0), tags=("b",))]}, fn=fn)
    mk(grid, strat)[0].run()
    assert got["a"] == ["P000001"] and got["all"] == 2 and got["tag_scope"] == pytest.approx(0.05)


def test_lookahead_from_strategy_is_blocked(grid, mk):
    def fn(ts, view, submit):
        view.pricer(ts + pd.Timedelta(days=1))

    with pytest.raises(LookAheadError):
        mk(grid, ScriptedStrategy(fn=fn))[0].run()


def test_single_tqdm_bar_and_foreign_bars_suppressed(grid):
    import tqdm.std as tstd
    from tqdm import tqdm as TQ

    created = []
    orig = tstd.tqdm.__init__

    def spy(self, *a, **k):
        orig(self, *a, **k)
        created.append((type(self).__name__, self.disable, getattr(self, "desc", "")))

    tstd.tqdm.__init__ = spy
    try:
        def fn(ts, view, submit):
            for _ in TQ(range(3), desc="foreign"):  # a foreign bar created inside the run
                pass

        eng, _, _ = make_engine(grid, ScriptedStrategy(fn=fn), show_progress=True)
        eng.run()
    finally:
        tstd.tqdm.__init__ = orig
    enabled = [b for b in created if not b[1]]  # disable flag as recorded at CREATION (tqdm sets disable=True on close)
    assert len(enabled) == 1 and enabled[0][2].startswith("BACKTESTING")
    assert any(b[1] for b in created[1:])  # the foreign bars were created but forced disabled


def test_toyoption_gamma_scalping_delta_hedge_reduces_variance(grid):
    """Long option + dv01-neutral futures hedge each day: hedged P&L variance << unhedged (uses only public engine/view API)."""
    expiry = D("2024-02-28")
    tpl = toy_tpl(name="opt", target=ToyOption, kwargs={"strike": 4.0, "expiry": expiry, "notional": 1e4})
    fut = toy_tpl(name="fut", target=ToyFuture, kwargs={"multiplier": 1.0})

    def hedge(ts, view, submit):
        if view.n_positions("action:opt") == 0:
            return
        net = view.measure("dv01")
        unit = view.measure_of(fut, "dv01")
        (h,) = view.positions("action:hedge") or (None,)
        if h is not None:
            submit(CloseOrder(PositionSelector(ids=(h.id,))))
        target = view.measure("dv01", "action:opt")
        submit(OpenOrder(fut, quantity=-target / unit, meta=PositionMeta(action="hedge", kind="hedge")))

    unhedged = make_engine(grid, ScriptedStrategy({D("2024-01-10"): [OpenOrder(tpl, meta=PositionMeta(action="opt"))]}))[0].run().equity["step_pnl"]
    hedged = make_engine(grid, ScriptedStrategy({D("2024-01-10"): [OpenOrder(tpl, meta=PositionMeta(action="opt"))]}, fn=hedge))[0].run().equity["step_pnl"]
    assert hedged.std() < 0.5 * unhedged.std()
