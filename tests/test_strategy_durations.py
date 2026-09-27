import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from pricebt.errors import DurationError, StrategyError
from pricebt.strategy import CustomDuration, ScheduleInfo, resolve_exit
from pricebt.strategy.durations import PastExitError, periodic_schedule
from pricebt.timeutil import Calendar, TimeContext, TimelineContext
from test_strategy_support import D, NY, mk_ctx

pytestmark = pytest.mark.core

CTX = mk_ctx("2024-01-02", "2026-12-31")
E = D("2024-01-31")  # a Wednesday


def res(dur, entry=E, terms=None, info=None, ctx=CTX):
    return resolve_exit(dur, entry_ts=entry, terms=terms, info=info, ctx=ctx)


def test_none_holds_to_the_end():
    assert res(None) is None


@pytest.mark.parametrize("tenor, expected", [
    ("1b", "2024-02-01"), ("2b", "2024-02-02"), ("1w", "2024-02-07"), ("2W", "2024-02-14"),
    ("1m", "2024-02-29"),  # Jan 31 + 1m clipped to month end
    ("1y", "2025-01-31"), ("1m+1b", "2024-03-01"),
])
def test_calendar_tenors_keep_entry_time_of_day(tenor, expected):
    assert res(tenor) == pd.Timestamp(f"{expected} 17:00", tz=NY)


def test_m_is_months_and_min_is_minutes():
    assert res("15m") == pd.Timestamp("2025-04-30 17:00", tz=NY)  # 15 MONTHS from Jan 31 2024 (gs RelativeDate)
    assert res("15min") == E + pd.Timedelta(minutes=15)
    assert res("2h") == E + pd.Timedelta(hours=2) and res("90s") == E + pd.Timedelta(seconds=90)


def test_d_rolls_forward_onto_a_business_day():
    fri = D("2024-05-24")
    assert res("2d", entry=fri) == pd.Timestamp("2024-05-27 17:00", tz=NY)  # gs: Fri + 2d = Sunday, off any market grid
    assert res("1d", entry=fri) == pd.Timestamp("2024-05-27 17:00", tz=NY)


def test_business_day_rules_use_the_backtest_calendar_holiday():
    cal = Calendar([dt.date(2024, 5, 27)])
    ctx = TimelineContext(D("2024-01-02"), D("2024-12-31"), TimeContext(calendar=cal), ())
    assert resolve_exit("1b", entry_ts=D("2024-05-24"), ctx=ctx) == pd.Timestamp("2024-05-28 17:00", tz=NY)


def test_at_time_suffix_and_session_names():
    assert res("1b@10:30") == pd.Timestamp("2024-02-01 10:30", tz=NY)
    assert res("1b@open") == pd.Timestamp("2024-02-01 08:00", tz=NY)


def test_dst_nonexistent_exit_moves_forward_and_ambiguous_takes_first_occurrence():
    week7 = TimelineContext(D("2024-01-02"), D("2024-12-31"), TimeContext(calendar=Calendar(weekmask=(1,) * 7)), ())
    gap = resolve_exit("1d", entry_ts=pd.Timestamp("2024-03-09 02:30", tz=NY), ctx=week7)  # 2024-03-10 02:30 does not exist
    assert gap == pd.Timestamp("2024-03-10 03:00", tz=NY)  # the next valid instant after the gap
    amb = resolve_exit("1d", entry_ts=pd.Timestamp("2024-11-02 01:30", tz=NY), ctx=week7)  # 2024-11-03 01:30 occurs twice
    assert amb.tz_convert("UTC") == pd.Timestamp("2024-11-03 05:30", tz="UTC")  # the first (EDT) occurrence


def test_date_and_timestamp_and_timedelta_forms():
    assert res(dt.date(2024, 3, 2)) == pd.Timestamp("2024-03-04 17:00", tz=NY)  # a Saturday rolls forward
    assert res(pd.Timestamp("2024-03-05 09:00")) == pd.Timestamp("2024-03-05 09:00", tz=NY)
    assert res(dt.datetime(2024, 3, 5, 9, 0)) == pd.Timestamp("2024-03-05 09:00", tz=NY)
    assert res(pd.Timedelta(days=3)) == E + pd.Timedelta(days=3)
    assert res(dt.timedelta(hours=1)) == E + pd.Timedelta(hours=1)


def test_attribute_duration_reads_the_resolved_terms():
    p = {"expiration_date": dt.date(2024, 6, 14)}
    assert res("expiration_date", terms=p) == pd.Timestamp("2024-06-14 17:00", tz=NY)
    with pytest.raises(DurationError):
        res("expiration_date", terms={"expiration_date": None})
    with pytest.raises(DurationError):
        res("nonexistent", terms=p)


def test_tenor_grammar_wins_over_a_permissive_resolved_term():
    class Permissive(dict):
        def __contains__(self, k):
            return True

        def __getitem__(self, k):
            return dt.date(2030, 1, 1)

    assert res("1m", terms=Permissive()) == pd.Timestamp("2024-02-29 17:00", tz=NY)


def test_next_schedule_reads_info_none_means_hold_and_missing_raises():
    nxt = D("2024-02-07")
    assert res("next schedule", info=ScheduleInfo(next_schedule=nxt)) == nxt
    assert res("Next  Schedule", info=ScheduleInfo(next_schedule=nxt)) == nxt
    assert res("next schedule", info=ScheduleInfo(next_schedule=None)) is None  # the last schedule date
    with pytest.raises(DurationError):
        res("next schedule", info=ScheduleInfo())
    with pytest.raises(DurationError):
        res("next schedule", info=None)


def test_custom_duration_min_max_and_none_as_forever():
    assert res(CustomDuration.earliest("1m", "1w")) == pd.Timestamp("2024-02-07 17:00", tz=NY)
    assert res(CustomDuration.latest("1m", "1w")) == pd.Timestamp("2024-02-29 17:00", tz=NY)
    assert res(CustomDuration.earliest(None, "1w")) == pd.Timestamp("2024-02-07 17:00", tz=NY)
    assert res(CustomDuration.latest(None, "1w")) is None  # None (hold to the end) dominates a max
    assert res(CustomDuration((D("2024-03-01"), "1y"), lambda *v: v[0])) == D("2024-03-01")


def test_exit_beyond_backtest_end_is_hold_and_not_after_entry_raises():
    short = mk_ctx("2024-01-02", "2024-03-01")
    assert resolve_exit("1y", entry_ts=E, ctx=short) is None
    with pytest.raises(PastExitError):
        res(D("2024-01-30"))
    with pytest.raises(PastExitError):
        res("0b")


def test_bad_tokens_raise():
    with pytest.raises(DurationError):
        res("5x")
    with pytest.raises(DurationError):
        res("1m+15min")  # calendar and fixed tokens cannot be chained


def test_periodic_schedule_from_base_not_iterative_and_dedupes():
    cal = Calendar.weekends_only()
    assert periodic_schedule(dt.date(2024, 1, 31), "1m", dt.date(2024, 5, 31), cal) == [
        dt.date(2024, 1, 31), dt.date(2024, 2, 29), dt.date(2024, 4, 1), dt.date(2024, 4, 30), dt.date(2024, 5, 31)]
    d = periodic_schedule(dt.date(2024, 5, 24), "1d", dt.date(2024, 5, 30), cal)
    assert d == [dt.date(2024, 5, 24), dt.date(2024, 5, 27), dt.date(2024, 5, 28), dt.date(2024, 5, 29), dt.date(2024, 5, 30)]


@pytest.mark.parametrize("bad", ["0b", "-1b", "1.5b", "1b1w", "15min", "", "abc"])
def test_periodic_schedule_rejects_bad_frequencies(bad):
    with pytest.raises(StrategyError):
        periodic_schedule(dt.date(2024, 1, 2), bad, dt.date(2024, 2, 1), Calendar.weekends_only())
