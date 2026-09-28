"""GenericEngine end-to-end smoke test (IMPLEMENTATION_PLAN.md P3.5; DESIGN.md section 9,
research/02 section 3-5). A toy USD swap rolled monthly by a PeriodicTrigger + AddTradeAction over
three months, verified against independently-computed PVs (never read back from the engine's own
bookkeeping) and the `Total` identity on every row.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from pricebt.backtests.action_handler import ActionHandler
from pricebt.backtests.actions import (
    Action,
    AddScaledTradeActionInfo,
    AddTradeAction,
    AddTradeActionInfo,
    EarlyExitPositionLimitScaledAction,
    ScalingActionType,
)
from pricebt.backtests.backtest_objects import BackTest, CashPayment
from pricebt.backtests.backtest_utils import CalcType
from pricebt.backtests.generic_engine import GenericEngine, _final_date_of
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import (
    DateTrigger,
    DateTriggerRequirements,
    PeriodicTrigger,
    PeriodicTriggerRequirements,
    TriggerInfo,
)
from pricebt.instrument import IRSwap
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRDelta, Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

START = date(2024, 1, 2)
END = date(2024, 4, 2)


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swap():
    return IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, name="swap")


def _strategy():
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1m", end_date=END),
        actions=[AddTradeAction(_swap(), "1m")],
    )
    return Strategy(initial_portfolio=None, triggers=[trigger])


def _run():
    _session()
    engine = GenericEngine()
    return engine.run_backtest(_strategy(), start=START, end=END, frequency="1b", show_progress=False)


def test_determinism():
    bt1 = _run()
    bt2 = _run()
    assert_frame_equal(bt1.result_summary, bt2.result_summary)


def test_ledger_names_and_dates():
    bt = _run()
    ledger = bt.trade_ledger()
    # Each AddTradeAction order is named Action1_swap_<create_date> (rule R1: 'swap' doesn't start
    # with 'Action1', so it is prefixed; rule R2 then appends the create date).
    assert len(ledger) >= 2
    for name in ledger.index:
        assert name.startswith("Action1_swap_")
        create_str = name.rsplit("_", 1)[-1]
        create_date = date.fromisoformat(create_str)
        assert START <= create_date <= END
        row = ledger.loc[name]
        assert row["Open"] == create_date
        if row["Close"] is not None:
            assert row["Close"] > row["Open"]


def test_trade_pnl_equals_close_plus_open():
    bt = _run()
    ledger = bt.trade_ledger()
    closed = ledger[ledger["Status"] == "closed"]
    assert len(closed) >= 1
    for name, row in closed.iterrows():
        assert row["Trade PnL"] == pytest.approx(row["Close Value"] + row["Open Value"])


def test_total_identity_holds_on_every_row():
    bt = _run()
    summary = bt.result_summary
    expected = summary[Price] + summary["Cumulative Cash"] + summary["Transaction Costs"]
    pd.testing.assert_series_equal(summary["Total"], expected, check_names=False)


def test_entry_and_exit_cash_are_minus_and_plus_pv_priced_independently():
    bt = _run()
    ledger = bt.trade_ledger()
    session = PricebtSession.current
    for name, row in ledger.iterrows():
        create_date = row["Open"]
        # Independently resolve (ONCE, on the entry date -- ATM is struck there, exactly as the
        # engine's own held position was) a FRESH, unrelated swap object, then price that SAME
        # resolved instrument on the entry and exit dates. Never reads the engine's own
        # resolved/renamed instrument back.
        resolved = session.pricing.resolve(_swap(), create_date, None)
        entry_pv = session.pricing.value(resolved, create_date, Price, None)
        assert row["Open Value"] == pytest.approx(-float(entry_pv))
        if row["Close"] is not None and row["Close"] != row["Open"]:
            exit_pv = session.pricing.value(resolved, row["Close"], Price, None)
            assert row["Close Value"] == pytest.approx(float(exit_pv))


def test_holding_window_create_le_s_lt_final():
    bt = _run()
    ledger = bt.trade_ledger()
    closed = ledger[ledger["Status"] == "closed"]
    for name, row in closed.iterrows():
        create_date, final_date = row["Open"], row["Close"]
        for s in bt.states:
            held = any(getattr(t, "name", None) == name for t in bt.portfolio_dict.get(s, ()))
            expected_held = create_date <= s < final_date
            assert held == expected_held, f"{name} on {s}: held={held}, expected={expected_held}"


def test_early_exit_position_limit_scaled_action_via_engine():
    """Regression test: EarlyExitPositionLimitScaledActionImpl has no `_raise_order` override
    (matches gs: it inherits AddScaledTradeActionImpl's, which must pass `trigger_infos` into
    `get_base_orders_for_states` for the subclass's own override -- computing per-instrument exit
    dates for max_concurrent_pos accounting -- to run at all). This is the first test to drive
    EarlyExitPositionLimitScaledAction through GenericEngine.run_backtest (full scenario coverage
    is P4.1's test_engine_early_exit.py; this is P3.5's own narrow regression for the bug this
    task fixed in the code it owns).
    """
    _session()
    d1, d2, d3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)  # Tue, Wed, Thu

    action = EarlyExitPositionLimitScaledAction(
        _swap(),
        trade_duration=None,
        scaling_type=ScalingActionType.size,
        scaling_level=1,
        early_exits=[d3],
        max_concurrent_pos=1,
    )
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1b", end_date=d3),
        actions=[action],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    engine = GenericEngine()
    bt = engine.run_backtest(strategy, start=d1, end=d3, frequency="1b", show_progress=False)

    ledger = bt.trade_ledger()
    # d1's position (early_exits caps its otherwise-unbounded final date to d3) is still open on
    # d2 (max_concurrent_pos=1 -- cur_no_pos=1, none exited yet), so d2's whole order is dropped by
    # get_base_orders_for_states: exactly 2 positions exist, not 3, and none is named for d2. Without
    # this task's fix, this call crashed with AttributeError (see the finding this test guards).
    assert len(ledger) == 2
    assert not any(name.endswith(f"_{d2}") for name in ledger.index)
    d1_name = next(name for name in ledger.index if name.endswith(f"_{d1}"))
    d3_name = next(name for name in ledger.index if name.endswith(f"_{d3}"))
    # early_exits caps d1's position to d3 (its natural final date, with no trade_duration, is
    # unbounded).
    assert ledger.loc[d1_name, "Close"] == d3
    # d3's own position is created ON d3, and early_exits only applies to a future exit
    # (`d > order_date`), so it is unaffected and stays open through the backtest end.
    assert ledger.loc[d3_name, "Close"] is None


def test_early_exit_impl_needs_trigger_infos_for_next_schedule_final_date():
    """Direct regression for the same finding, isolating the `trigger_infos` dependency itself:
    EarlyExitPositionLimitScaledActionImpl's own get_base_orders_for_states (generic_engine_action_
    impls.py) reads `info` from the `trigger_infos` kwarg to resolve trade_duration='next schedule'
    into a real date -- exactly like every other AddScaledTradeActionImpl trade_duration/info
    combination. If the shared `_raise_order` ever again drops `trigger_infos=trigger_infos` from
    its `get_base_orders_for_states` call, `info` is always None here and this raises
    `RuntimeError('Next schedule not supported by action')` instead of using next_schedule.
    """
    _session()
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    action = EarlyExitPositionLimitScaledAction(
        _swap(), trade_duration="next schedule", scaling_type=ScalingActionType.size, scaling_level=1
    )
    impl = GenericEngine().get_action_handler(action)
    backtest = BackTest(strategy=object(), states=[d1, d2], risks=[Price], price_measure=Price)

    impl.apply_action(d1, backtest, {d1: AddScaledTradeActionInfo(next_schedule=d2)})

    # apply_action alone (no full run_backtest/_handle_cash pass) books the entry/exit
    # CashPayments directly; check those, not trade_ledger() (which needs _handle_cash to have
    # priced them to fill in 'Close').
    exit_dates = {cp.effective_date for cps in backtest.cash_payments.values() for cp in cps if cp.direction == 1}
    assert exit_dates == {d2}


def test_final_date_of_matches_a_hedge_legs_wrapper_portfolio():
    """DEV-R1's `_final_date_of` recovers a held position's exit date from its exit CashPayment by
    matching `cp.trade.name`. For a hedge, `cp.trade` is the scaled WRAPPER Portfolio -- gs's own
    "adds leaves, not the portfolio" scheme (generic_engine.py): `portfolio_dict` gets the wrapper's
    LEAVES via `Portfolio.__add__`, while `hedge.exit_payment.trade` keeps the renamed wrapper
    itself. A leaf's own name therefore never equals the wrapper's name -- `_final_date_of` must
    also check the wrapper's leaves, or a held hedge leg's exit date can never be found and DEV-R1's
    flatness test always treats it as still continuing, even after it has actually exited.
    """
    leaf = IRSwap(
        pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1, name="Scaled_hedge"
    )
    wrapper = Portfolio([leaf], name="Scaled_Action1_hedge_2024-01-02")
    assert leaf.name != wrapper.name  # the direct cp.trade.name == inst.name match cannot find it

    backtest = BackTest(strategy=object(), states=[date(2024, 1, 2)], risks=[Price], price_measure=Price)
    exit_date = date(2024, 3, 4)
    backtest.cash_payments[exit_date].append(CashPayment(wrapper, effective_date=exit_date, direction=1))

    assert _final_date_of(leaf, backtest) == exit_date


def test_apply_action_accepts_a_bare_list_of_trigger_infos_zipped_by_date():
    """DESIGN.md section 11 DEV-T11: gs's own public `apply_action(state, backtest, trigger_info)`
    also accepts a bare positional list of infos, one per date in `state` order (kept for MUST-2 API
    parity -- a caller that builds trigger_info itself rather than going through GenericEngine).
    `_trigger_info_for_date` alone cannot zip that (it only ever sees one date at a time, and an
    info namedtuple is itself a tuple); `_normalize_trigger_info` must convert the list to a
    `{date: info}` dict first, matched against the SAME dates in order -- otherwise every date gets
    back the SAME unchanged whole list, and `ti.scaling` raises AttributeError.
    """
    _session()
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    action = AddTradeAction(_swap(), None)
    impl = GenericEngine().get_action_handler(action)
    backtest = BackTest(strategy=object(), states=[d1, d2], risks=[Price], price_measure=Price)

    info1 = AddTradeActionInfo(scaling=0.5, next_schedule=None)
    info2 = AddTradeActionInfo(scaling=2.0, next_schedule=None)
    impl.apply_action([d1, d2], backtest, [info1, info2])

    entry_insts = {
        cp.trade.name: cp.trade for cps in backtest.cash_payments.values() for cp in cps if cp.direction == -1
    }
    d1_inst = next(inst for name, inst in entry_insts.items() if name.endswith(f"_{d1}"))
    d2_inst = next(inst for name, inst in entry_insts.items() if name.endswith(f"_{d2}"))
    assert d1_inst.quantity_ == pytest.approx(0.5)
    assert d2_inst.quantity_ == pytest.approx(2.0)


def test_path_dependent_triggers_do_not_share_each_others_info_dev_t11():
    """DESIGN.md section 11 DEV-T11 (the Phase 5 half): gs's own
    `_process_triggers_and_actions_for_date` (generic_engine.py:460-471) accumulates every fired
    path-dependent trigger's info_dict entries for THIS DATE into one shared `defaultdict(list)`
    across the whole `for trigger in strategy.triggers` loop -- so two different triggers whose
    actions share a type get their infos merged into a one-item-per-trigger LIST, and neither
    action receives its own trigger's info on its own. pricebt computes `t_info` fresh per trigger
    (never accumulated across the loop) and passes `t_info.info_dict.get(type(action))` straight
    through, so each action's handler receives exactly its own trigger's info, unchanged -- not a
    list, and not the other trigger's info.
    """

    class _FakeAction(Action):
        _calc_type = CalcType.path_dependent

    calls = []

    class _RecordingHandler(ActionHandler):
        def apply_action(self, state, backtest, trigger_info):
            calls.append((self.action, trigger_info))

    class _FakeTrigger:
        calc_type = CalcType.path_dependent
        risks = None

        def __init__(self, action, info):
            self.actions = [action]
            self._info = info

        def has_triggered(self, d, backtest):
            return TriggerInfo(triggered=True, info_dict={_FakeAction: self._info})

    d = date(2024, 1, 2)
    action_a, action_b = _FakeAction(), _FakeAction()
    trigger_a = _FakeTrigger(action_a, "info-a")
    trigger_b = _FakeTrigger(action_b, "info-b")
    strategy = Strategy(initial_portfolio=None, triggers=[trigger_a, trigger_b])
    backtest = BackTest(strategy=strategy, states=[d], risks=[Price], price_measure=Price)
    engine = GenericEngine(action_impl_map={_FakeAction: _RecordingHandler})

    engine._process_triggers_and_actions_for_date(d, strategy, backtest, [Price])

    # Without the fix (gs's shared accumulating dict), both calls would see
    # ["info-a", "info-b"] instead of each trigger's own bare info.
    assert calls == [(action_a, "info-a"), (action_b, "info-b")]


def test_calc_calls_and_calculations_bookkeeping():
    """DESIGN.md MUST-2: gs's `calc_calls`/`calculations` counters (generic_engine.py's several
    `backtest.calc_calls +=` / `backtest.calculations +=` sites) must be ported, not silently
    dropped. This scenario is deliberately trivial to hand-derive: one swap held for the whole
    window via `initial_portfolio`, no triggers, and a natural termination (duration=None ->
    date.max) far past the backtest end, so:

    - `calc_calls` is ALWAYS exactly 3 for a normal run -- one tick each from
      `_price_semi_det_triggers`, `_calc_new_trades` and `_handle_cash`, regardless of scenario
      shape (gs ticks once per PricingContext-batch method call, not per calc()).
    - `calculations` comes ENTIRELY from `_price_semi_det_triggers`'s per-day loop here: the swap is
      priced once per grid date (no hedges/weighted trades), so it is exactly
      len(dates) * portfolio_size * len(risks). Every other increment site (hedges, weighted
      trades, new trades, cash) contributes 0, because there are none of those and the swap's own
      entry cash payment is already priced by the time _handle_cash looks for it, and its exit is
      beyond strategy_end_date so it is never scheduled at all.

    `risks=[IRDelta]` is passed explicitly (rather than relying on the single default price_measure)
    so `n_risks=2` -- distinct from `n_dates`/`portfolio_size` -- and the `* len(risks)` factor in
    the formula is load-bearing, not indistinguishable from `* 1`.
    """
    _session()
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]  # Tue/Wed/Thu, no weekend gaps
    strategy = Strategy(initial_portfolio=[_swap()], triggers=[])
    engine = GenericEngine()
    bt = engine.run_backtest(strategy, states=dates, risks=[IRDelta], show_progress=False)

    n_dates = len(dates)
    portfolio_size = 1  # one swap, held every grid date
    # user risks=[IRDelta], strategy.risks=[], pnl_risks=[], + the default price_measure (Price):
    # dict.fromkeys([IRDelta, Price]) dedups nothing (they're distinct) -> 2.
    n_risks = 2

    assert bt.calc_calls != 0
    assert bt.calculations != 0
    assert bt.calc_calls == 3
    assert bt.calculations == n_dates * portfolio_size * n_risks


def test_non_grid_non_flat_date_prices_the_continuing_position_fresh_dev_r1():
    """DEV-R1's harder half (DESIGN.md section 11; _handle_cash, generic_engine.py): on a non-grid
    date that is NOT flat -- a held position's own off-grid exit fires while ANOTHER position is
    still held past it -- the continuing position is priced fresh into `results[d]`, so
    `result_summary` shows its true PV on that date instead of an ffilled, stale one.

    DESIGN.md section 12.4 names test_result_shapes.py as covering this, but that file's fixtures
    are built BY HAND (never through GenericEngine), so it is structurally incapable of reaching
    generic_engine.py's `_handle_cash` at all; its only non-grid case (Case B) is a FLAT date,
    exercising DEV-R1's easier half (zeroing), not this one. This test drives the real engine.
    """
    _session()
    d1 = date(2024, 1, 2)  # grid[0]
    d2 = date(2024, 1, 9)  # grid[1], one week later
    off_grid = d1 + timedelta(days=3)  # strictly between d1 and d2; not a grid date
    held = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, name="held")
    exits = IRSwap(pay_or_receive="Receive", termination_date="5y", notional_currency="USD", notional_amount=2_000_000, name="exits")
    entry = DateTrigger(
        trigger_requirements=DateTriggerRequirements(dates=[d1]),
        actions=[
            AddTradeAction(held, None, name="Hold"),  # never exits on its own -> continues past d2
            AddTradeAction(exits, timedelta(days=3), name="Exit"),  # off-grid exit at `off_grid`
        ],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[entry])
    bt = GenericEngine().run_backtest(strategy, states=[d1, d2], show_progress=False)

    assert off_grid in bt.results  # the fix's whole effect: a priced row exists for this date at all

    session = PricebtSession.current
    resolved_held = session.pricing.resolve(held, d1, None)
    expected_fresh_pv = float(session.pricing.value(resolved_held, off_grid, Price, None))

    row = bt.result_summary.loc[off_grid]
    assert row[Price] == pytest.approx(expected_fresh_pv)
    # Non-vacuity: this must be a genuinely different number from a stale ffill of d1's row, which
    # combined BOTH positions' PVs (`held` and `exits`) on d1, not `held` alone freshly priced here.
    assert row[Price] != pytest.approx(bt.result_summary.loc[d1, Price])
