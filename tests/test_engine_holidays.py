"""GenericEngine scenario: a 040311-style holiday calendar, passed both as a TUPLE and as a LIST,
to `run_backtest` itself and to an action's own `holiday_calendar=` (DESIGN.md section 12.4 row
`test_engine_holidays.py`; DEV-T16, section 11). A list is unhashable, so it must not crash
`backtest_utils.get_final_date`'s cache key; both representations must also produce IDENTICAL
results, proving the normalisation is representation-only.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.datetime.relative_date import RelativeDateSchedule
from pricebt.instrument import IRSwap
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"

START, END = date(2024, 1, 2), date(2024, 1, 10)  # a 7-business-day window, no weekends skipped
GRID_HOLIDAY = date(2024, 1, 4)  # a business day inside [START, END] to knock out of the '1b' grid


def _session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])


def _swap(**kw):
    return IRSwap("Pay", "10y", "USD", 10_000_000, fixed_rate="ATM", name="10y", **kw)


def _run_backtest_grid(holiday_calendar):
    _session()
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1m", end_date=START),
        actions=[AddTradeAction(_swap(), "1m")],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    return GenericEngine().run_backtest(
        strategy, start=START, end=END, frequency="1b", holiday_calendar=holiday_calendar, show_progress=False
    )


def test_run_backtest_holiday_calendar_as_a_list_matches_the_tuple_form_and_actually_excludes_the_date():
    # Ground truth from the real date-schedule code (not a magic constant): without a holiday
    # calendar, GRID_HOLIDAY is a plain business day and is IN the schedule; with it flagged, the
    # '1b' stepping (backtest_utils/relative_date's np.busdaycalendar) must skip it.
    plain_schedule = RelativeDateSchedule("1b", START, END).apply_rule()
    assert GRID_HOLIDAY in plain_schedule

    bt_list = _run_backtest_grid([GRID_HOLIDAY])  # list: unhashable, must not crash get_final_date's cache
    bt_tuple = _run_backtest_grid((GRID_HOLIDAY,))

    assert GRID_HOLIDAY not in bt_list.states
    assert GRID_HOLIDAY not in bt_tuple.states
    assert list(bt_list.states) == list(bt_tuple.states)
    assert_frame_equal(bt_list.result_summary, bt_tuple.result_summary)


def _run_action_holiday(holiday_calendar):
    _session()
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1m", end_date=date(2024, 1, 2)),
        actions=[AddTradeAction(_swap(), "1m", name="RollTest", holiday_calendar=holiday_calendar)],
    )
    strategy = Strategy(initial_portfolio=None, triggers=[trigger])
    return GenericEngine().run_backtest(
        strategy, states=[date(2024, 1, 2), date(2024, 2, 5)], show_progress=False
    )


def test_action_holiday_calendar_as_a_list_matches_the_tuple_form_and_rolls_the_exit_date():
    d0 = date(2024, 1, 2)
    # Ground truth: '1m' from d0 lands on 2024-02-02 (a Friday, already a business day) with no
    # holiday calendar; flagging that exact date as a holiday must roll the exit forward to the
    # next business day, 2024-02-05 (Monday) -- confirmed against RelativeDate directly above the
    # engine, not read back from the engine's own output.
    action_holiday = date(2024, 2, 2)

    bt_no_holiday = _run_action_holiday(None)
    assert bt_no_holiday.trade_ledger().iloc[0]["Close"] == action_holiday

    bt_list = _run_action_holiday([action_holiday])  # list: must not crash the cache key
    bt_tuple = _run_action_holiday((action_holiday,))

    for bt in (bt_list, bt_tuple):
        ledger = bt.trade_ledger()
        assert len(ledger) == 1
        assert ledger.iloc[0]["Close"] == date(2024, 2, 5)  # rolled forward off the flagged holiday

    assert_frame_equal(bt_list.trade_ledger(), bt_tuple.trade_ledger())
    assert_frame_equal(bt_list.result_summary, bt_tuple.result_summary)


def test_action_list_holiday_calendar_is_stored_as_a_tuple():
    a = AddTradeAction(_swap(), "1m", holiday_calendar=[date(2024, 1, 1)])
    assert isinstance(a.holiday_calendar, tuple)
