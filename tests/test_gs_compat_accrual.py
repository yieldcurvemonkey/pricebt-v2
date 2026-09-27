"""Cash accrual of the gs-compat module counts CALENDAR days like gs (`(date1 - date0).days`), not elapsed seconds (adversarial review, finding M9).

An elapsed-seconds count makes a Friday-to-Monday weekend 71 h across the spring-forward and 73 h across the fall-back, so 1bn at 5% accrued -5,709.70 / +5,709.73
away from gs. The two stamps are compared on the wall clock (local time, zone dropped); an intraday interval stays proportional.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from pricebt.backtests.backtest_objects import ConstantCashAccrualModel, DataCashAccrualModel
from pricebt.backtests.data_sources import GenericDataSource

pytestmark = pytest.mark.core

NY = "America/New_York"


def ny(s):
    return pd.Timestamp(s, tz=NY)


def gs(balance, days, rate=0.05, annual=True):
    """gs_quant ConstantCashAccrualModel.get_accrued_value: balance * ((1 + rate/365) ** days - 1) for `days` whole calendar days."""
    return balance * ((1 + (rate / 365 if annual else rate)) ** days - 1)


WEEKENDS = [
    ("plain", "2024-02-02 17:00", "2024-02-05 17:00"),
    ("spring forward (23 h Sunday)", "2024-03-08 17:00", "2024-03-11 17:00"),
    ("fall back (25 h Sunday)", "2024-11-01 17:00", "2024-11-04 17:00"),
]


@pytest.mark.parametrize("label,a,b", WEEKENDS, ids=[w[0] for w in WEEKENDS])
def test_a_daily_grid_across_a_weekend_accrues_exactly_the_calendar_days_gs_counts(label, a, b):
    got = ConstantCashAccrualModel(0.05).interest(1e9, ny(a), ny(b))
    assert got == pytest.approx(gs(1e9, 3), abs=1e-6), f"{label}: 3 calendar days = {gs(1e9, 3):,.2f}, got {got:,.2f}"


@pytest.mark.parametrize("label,a,b", WEEKENDS, ids=[w[0] for w in WEEKENDS])
def test_the_data_driven_model_counts_the_same_calendar_days(label, a, b):
    ds = GenericDataSource(pd.Series([0.04], index=[dt.date.fromisoformat(a[:10])]))
    assert DataCashAccrualModel(ds).interest(1e9, ny(a), ny(b)) == pytest.approx(gs(1e9, 3, 0.04), abs=1e-6)


def test_a_single_day_step_is_one_day_on_the_dst_change_dates_themselves():
    spring = ConstantCashAccrualModel(0.05).interest(1e9, ny("2024-03-09 17:00"), ny("2024-03-10 17:00"))
    fall = ConstantCashAccrualModel(0.05).interest(1e9, ny("2024-11-02 17:00"), ny("2024-11-03 17:00"))
    assert spring == pytest.approx(gs(1e9, 1), abs=1e-6) and fall == pytest.approx(gs(1e9, 1), abs=1e-6)


def test_the_non_annual_form_and_the_intraday_step_stay_proportional_to_the_wall_clock():
    m = ConstantCashAccrualModel(0.001, annual=False)
    assert m.interest(1e6, ny("2024-03-08 17:00"), ny("2024-03-11 17:00")) == pytest.approx(gs(1e6, 3, 0.001, annual=False))
    half = ConstantCashAccrualModel(0.05).interest(1e9, ny("2024-01-03 05:00"), ny("2024-01-03 17:00"))
    assert half == pytest.approx(gs(1e9, 0.5)), "12 wall-clock hours = half a day"
    across = ConstantCashAccrualModel(0.05).interest(1e9, ny("2024-03-10 00:00"), ny("2024-03-10 12:00"))
    assert across == pytest.approx(gs(1e9, 0.5)), "midnight to noon on the 23-hour day is still half a day on the wall clock"


def test_naive_stamps_and_stamps_in_another_zone_are_read_on_the_first_stamps_wall_clock():
    m = ConstantCashAccrualModel(0.05)
    naive = m.interest(1e9, pd.Timestamp("2024-03-08 17:00"), pd.Timestamp("2024-03-11 17:00"))
    assert naive == pytest.approx(gs(1e9, 3), abs=1e-6)
    in_utc = m.interest(1e9, ny("2024-03-08 17:00"), ny("2024-03-11 17:00").tz_convert("UTC"))
    assert in_utc == pytest.approx(gs(1e9, 3), abs=1e-6), "the second stamp is converted to the first one's zone before the zone is dropped"
