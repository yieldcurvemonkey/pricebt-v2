import datetime as dt

import pandas as pd
import pytest

from pricebt.errors import EngineInvariantError
from pricebt.timeutil import Calendar, Clock, TimeContext, TimeGrid, apply_tenor, parse_tenor, schedule

pytestmark = pytest.mark.core


def test_calendar_business_days_and_roll():
    cal = Calendar([dt.date(2024, 5, 27)])
    assert not cal.is_business_day(dt.date(2024, 5, 25))  # Saturday
    assert not cal.is_business_day(dt.date(2024, 5, 27))  # holiday
    assert cal.following(dt.date(2024, 5, 25)) == dt.date(2024, 5, 28)
    assert cal.preceding(dt.date(2024, 5, 27)) == dt.date(2024, 5, 24)
    assert cal.add_business_days(dt.date(2024, 5, 24), 1) == dt.date(2024, 5, 28)
    assert cal.add_business_days(dt.date(2024, 5, 28), -1) == dt.date(2024, 5, 24)


def test_tenor_rules_match_gs_relativedate_probes():
    cal = Calendar()
    # gs notes: Fri+2d = Sun (calendar days, no adjust); Mar31 Sun + 1m style roll FORWARD
    assert apply_tenor(dt.date(2024, 3, 22), "2d", cal) == dt.date(2024, 3, 24)
    assert apply_tenor(dt.date(2024, 1, 31), "2m", cal) == dt.date(2024, 4, 1)  # Mar 31 is Sunday -> Apr 1
    assert parse_tenor("15min") == (15, "min")
    with pytest.raises(Exception):
        parse_tenor("xx")


def test_schedule_multiples_from_base_not_iterated():
    # gs: 1m from 2024-01-31 = Jan31, Feb29, Apr1 (Mar31 Sunday rolled fwd), Apr30, May31
    got = schedule(dt.date(2024, 1, 31), dt.date(2024, 5, 31), "1m", Calendar())
    assert got == [dt.date(2024, 1, 31), dt.date(2024, 2, 29), dt.date(2024, 4, 1), dt.date(2024, 4, 30), dt.date(2024, 5, 31)]


def test_gs_control_daily_schedule_five_business_days():
    got = schedule(dt.date(2024, 5, 6), dt.date(2024, 5, 10), "1b", Calendar())
    assert len(got) == 5


def test_daily_grid_tz_aware_sorted_unique(tctx):
    g = TimeGrid.daily("2024-01-01", "2024-01-12", "1b", tctx)
    assert len(g) == 10
    assert g.points.tz is not None and g.points.is_monotonic_increasing and g.points.is_unique
    assert all(p.hour == 17 for p in g)  # date_policy close


def test_intraday_grid_minutes_and_dst():
    ctx = TimeContext(session_open=dt.time(8, 0), session_close=dt.time(17, 0))
    g = TimeGrid.intraday("2024-03-08", "2024-03-11", "1min", ctx)  # DST starts 2024-03-10 (Sunday); Fri and Mon
    per_day = g.points.to_series().groupby(g.points.date).size()
    assert (per_day == 541).all()  # 08:00..17:00 inclusive


def test_clock_monotone():
    c = Clock()
    c.advance(pd.Timestamp("2024-01-02", tz="UTC"))
    with pytest.raises(EngineInvariantError):
        c.advance(pd.Timestamp("2024-01-02", tz="UTC"))


def test_periods_per_year_daily_grid(tctx):
    g = TimeGrid.daily("2023-01-02", "2023-12-29", "1b", tctx)
    assert 245 < g.periods_per_year() < 265


def test_the_statistics_year_is_one_named_julian_year():
    from pricebt.timeutil import SECONDS_PER_YEAR

    assert SECONDS_PER_YEAR == 365.25 * 86400.0
    g = TimeGrid.daily("2024-01-01", "2025-01-01", "1b", TimeContext())
    span = (g.points[-1] - g.points[0]).total_seconds() / SECONDS_PER_YEAR
    assert g.periods_per_year() == pytest.approx((len(g.points) - 1) / span)
