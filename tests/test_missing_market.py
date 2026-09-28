"""GenericEngine's missing-market handling (IMPLEMENTATION_PLAN.md P3.5; DESIGN.md sections 9.5,
0.4, DEV-E16). Every case from DESIGN.md section 12.4's row for this file, exercised through the
toy library's `HOLES` set (tests/toylib/rates.py): 'drop' warns/lists/removes grid dates and
recomputes start/end; a weekend start with an initial portfolio; an off-grid exit on a hole is
rolled forward and recorded; a 'next schedule' landing on a dropped grid date is mapped forward; a
final date past the backtest end on a hole is not booked and does not error; every grid date
missing raises regardless of policy; 'raise' raises immediately on any missing grid date.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import toylib.rates as tr
from pricebt.backtests.actions import AddScaledTradeAction, AddTradeAction, ScalingActionType
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import (
    DateTrigger,
    DateTriggerRequirements,
    PeriodicTrigger,
    PeriodicTriggerRequirements,
)
from pricebt.errors import MarketDataUnavailable
from pricebt.instrument import IRSwap
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"


def _session(**kwargs):
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"], **kwargs)


def _swap(name="swap"):
    return IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1_000_000, name=name)


def _engine():
    return GenericEngine()


# --------------------------------------------------------------------------------- 'drop' policy


def test_drop_warns_lists_and_removes_grid_dates():
    _session()
    hole = date(2024, 1, 4)  # a Thursday, mid-week -- a real grid date at frequency='1b'
    tr.HOLES.add(hole)
    strategy = Strategy(
        initial_portfolio=None,
        triggers=[DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[date(2024, 1, 2)]), actions=[AddTradeAction(_swap(), None)])],
    )
    with pytest.warns(UserWarning, match="dropped 1 dates"):
        bt = _engine().run_backtest(strategy, start=date(2024, 1, 2), end=date(2024, 1, 8), frequency="1b", show_progress=False)
    assert bt.missing_market_dates == [hole]
    assert hole not in bt.states


def test_recomputed_start_and_end_after_drop():
    _session()
    start = date(2024, 1, 2)
    end = date(2024, 1, 5)
    tr.HOLES.add(start)  # drop the FIRST grid date
    tr.HOLES.add(end)  # drop the LAST grid date
    strategy = Strategy(initial_portfolio=[_swap()], triggers=[])
    with pytest.warns(UserWarning):
        bt = _engine().run_backtest(strategy, start=start, end=end, frequency="1b", show_progress=False)
    assert bt.states[0] > start
    assert bt.states[-1] < end
    assert bt.states[0] == bt.states[0]  # sanity: states sorted, non-empty
    assert start not in bt.states and end not in bt.states


def test_weekend_start_with_initial_portfolio_is_dropped_and_resolved_on_new_start():
    _session()
    weekend_start = date(2024, 1, 6)  # a real Saturday
    tr.HOLES.add(weekend_start)  # the toy market has no notion of weekends; simulate "no market" directly
    strategy = Strategy(initial_portfolio=[_swap()], triggers=[])
    with pytest.warns(UserWarning):
        bt = _engine().run_backtest(strategy, start=weekend_start, end=date(2024, 1, 10), frequency="1b", show_progress=False)
    new_start = bt.states[0]
    assert new_start > weekend_start
    # the initial portfolio was entered (and its entry cash booked) on the NEW start, not the
    # dropped weekend one.
    assert new_start in bt.cash_payments
    entered_names = [cp.trade.name for cp in bt.cash_payments[new_start] if cp.direction == -1]
    assert any(name.startswith(f"swap_{new_start}") for name in entered_names)


def test_raise_policy_raises_immediately_on_any_missing_grid_date():
    _session(missing_market="raise")
    hole = date(2024, 1, 4)
    tr.HOLES.add(hole)
    strategy = Strategy(
        initial_portfolio=None,
        triggers=[DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[date(2024, 1, 2)]), actions=[AddTradeAction(_swap(), None)])],
    )
    with pytest.raises(MarketDataUnavailable):
        _engine().run_backtest(strategy, start=date(2024, 1, 2), end=date(2024, 1, 8), frequency="1b", show_progress=False)


def test_all_grid_dates_missing_raises_regardless_of_policy():
    _session()  # default 'drop'
    start, end = date(2024, 1, 2), date(2024, 1, 5)
    for d in (date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)):
        tr.HOLES.add(d)
    strategy = Strategy(
        initial_portfolio=None,
        triggers=[DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[start]), actions=[AddTradeAction(_swap(), None)])],
    )
    with pytest.raises(MarketDataUnavailable, match="no market data on any"):
        _engine().run_backtest(strategy, start=start, end=end, frequency="1b", show_progress=False)


# --------------------------------------------------------------------------------- DEV-E16: off-grid rolling


def test_off_grid_exit_on_a_hole_is_rolled_forward_and_recorded():
    _session()
    d0, d1, d2, d3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)
    tr.HOLES.add(d2)  # d2 is deliberately excluded from `states` below -- an off-grid date
    strategy = Strategy(
        initial_portfolio=None,
        triggers=[DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[d0]), actions=[AddTradeAction(_swap(), d2)])],
    )
    bt = _engine().run_backtest(strategy, states=[d0, d1, d3], show_progress=False)
    assert bt.missing_market_moves, "expected a recorded (name, original, used) roll"
    name, original, used = bt.missing_market_moves[0]
    assert original == d2
    assert used == d3
    ledger = bt.trade_ledger()
    assert ledger.iloc[0]["Close"] == d3


def test_nav_unwind_pricing_is_rolled_off_a_hole():
    """DESIGN.md section 9.5 step 6 explicitly lists "NAV unwind pricing" among the off-grid dates
    DEV-E16 rolls: AddScaledTradeActionImpl._nav_scale_orders prices `unscaled_unwind_prices_by_day`
    at each instrument's (unrolled) final date, which raises MarketDataUnavailable on a hole; it
    must roll that date exactly like the (already-rolled) exit CashPayment/TCE final_date computed
    for the very same exit in apply_action, or the two disagree on which date this exit happened on.
    """
    _session()
    d0, d1, d2, d3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)
    tr.HOLES.add(d2)  # the swap's exit date, deliberately off-grid
    # off-market (fixed_rate far from the ~3% USD par level) so the swap has a nonzero PV -- NAV
    # scaling divides available cash by that PV, and a par (0 PV) swap would ZeroDivisionError.
    off_market_swap = IRSwap(
        pay_or_receive="Pay",
        termination_date="10y",
        notional_currency="USD",
        notional_amount=1_000_000,
        fixed_rate=0.10,
        name="swap",
    )
    action = AddScaledTradeAction(off_market_swap, d2, scaling_type=ScalingActionType.NAV, scaling_level=1_000_000)
    strategy = Strategy(
        initial_portfolio=None,
        triggers=[DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[d0]), actions=[action])],
    )
    # must not raise MarketDataUnavailable pricing the NAV unwind at the hole d2.
    bt = _engine().run_backtest(strategy, states=[d0, d1, d3], show_progress=False)
    # exactly one recorded roll: _nav_scale_orders and apply_action both roll the SAME (inst, d2)
    # exit independently -- _roll_to_market_date must dedup, not record the same move twice.
    assert len(bt.missing_market_moves) == 1, bt.missing_market_moves
    name, original, used = bt.missing_market_moves[0]
    assert original == d2
    assert used == d3
    ledger = bt.trade_ledger()
    # the exit CashPayment/TCE (apply_action) and the NAV unwind pricing (_nav_scale_orders) must
    # agree on the same rolled exit date for this trade.
    assert ledger.iloc[0]["Close"] == d3


def test_final_date_past_backtest_end_on_a_hole_is_not_booked_and_does_not_error():
    _session()
    start, end = date(2024, 1, 2), date(2024, 1, 5)
    beyond = date(2024, 1, 20)
    tr.HOLES.add(beyond)
    strategy = Strategy(
        initial_portfolio=None,
        triggers=[DateTrigger(trigger_requirements=DateTriggerRequirements(dates=[start]), actions=[AddTradeAction(_swap(), beyond)])],
    )
    bt = _engine().run_backtest(strategy, start=start, end=end, frequency="1b", show_progress=False)  # must not raise
    assert beyond not in bt.result_summary.index
    assert not any(o == beyond and u for _n, o, u in bt.missing_market_moves)


def test_next_schedule_landing_on_a_dropped_date_is_mapped_forward():
    _session()
    d0, d1, d2 = date(2024, 1, 2), date(2024, 2, 2), date(2024, 3, 2)
    tr.HOLES.add(d1)  # the periodic trigger's own next fire date after d0
    strategy = Strategy(
        initial_portfolio=None,
        triggers=[
            PeriodicTrigger(
                trigger_requirements=PeriodicTriggerRequirements(frequency="1m", end_date=d2),
                actions=[AddTradeAction(_swap(), "next schedule")],
            )
        ],
    )
    with pytest.warns(UserWarning):
        bt = _engine().run_backtest(strategy, start=d0, end=d2, frequency="1b", show_progress=False)
    assert d1 in bt.missing_market_dates
    ledger = bt.trade_ledger()
    first_order = ledger[ledger["Open"] == d0].iloc[0]
    # DESIGN.md section 9.5 step 5: next_schedule (d1) maps to the first KEPT GRID date >= it --
    # the run's own grid is daily ('1b'), so that is the next business day after d1, not the
    # periodic trigger's own next monthly date (d2).
    assert first_order["Close"] == date(2024, 2, 5)
    assert first_order["Close"] > d1
