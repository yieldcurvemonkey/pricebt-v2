"""pricebt.backtests.backtest_utils (IMPLEMENTATION_PLAN.md P3.1; DESIGN.md section 9.3).

Fixture dates for the tenor rule (`'3m'` from 2024-01-31 -> 2024-04-30, `'1b'` from a holiday-free
Friday -> the next Monday) are the same ones research/01 section 5.4 independently verified against
gs_quant 1.5.4/2.1.17 and tests/test_relative_date.py already exercises for RelativeDate itself;
this file is about get_final_date's own dispatch rules, not re-deriving the tenor math.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from pricebt.backtests.backtest_utils import (
    CustomDuration,
    clear_final_date_cache,
    final_date_cache,
    get_final_date,
    interpolate_signal,
    make_list,
)
from pricebt.instrument import IRSwap

CREATE = dt.date(2024, 1, 31)  # Wednesday


def _inst(**kw):
    return IRSwap(**kw)


# --------------------------------------------------------------------------------------- make_list
# research/01 section 5.3


def test_make_list_none_is_empty():
    assert make_list(None) == []


def test_make_list_string_is_not_iterated():
    assert make_list("abc") == ["abc"]


def test_make_list_non_iterable_wraps():
    assert make_list(42) == [42]
    assert make_list(_inst()) == [_inst()]


@pytest.mark.parametrize(
    "thing,expected",
    [
        ([1, 2, 3], [1, 2, 3]),
        ((1, 2), [1, 2]),
        ({1, 2}, None),  # order not guaranteed for a set; checked separately below
    ],
)
def test_make_list_iterables_become_lists(thing, expected):
    got = make_list(thing)
    assert isinstance(got, list)
    if expected is not None:
        assert got == expected
    else:
        assert set(got) == thing


def test_make_list_generator_is_consumed_into_a_list():
    assert make_list(i for i in range(3)) == [0, 1, 2]


def test_make_list_dict_becomes_list_of_keys():
    assert make_list({"a": 1, "b": 2}) == ["a", "b"]


# --------------------------------------------------------------------------------- get_final_date


def test_rule_none_means_held_forever():
    assert get_final_date(_inst(), CREATE, None) == dt.date.max


def test_rule_date_literal_returned_unchanged():
    d = dt.date(2030, 1, 1)
    assert get_final_date(_inst(), CREATE, d) == d


def test_rule_datetime_literal_returned_unchanged():
    d = dt.datetime(2030, 1, 1, 9, 30)
    assert get_final_date(_inst(), CREATE, d) == d


def test_rule_timedelta_duration_dev_t1():
    """pricebt DEV-T1: gs crashes on a timedelta (`.lower()` on a timedelta); pricebt adds it to
    create_date instead."""
    delta = dt.timedelta(days=10)
    assert get_final_date(_inst(), CREATE, delta) == CREATE + delta


def test_rule_hasattr_reads_the_instrument_attribute():
    inst = _inst(termination_date=dt.date(2024, 4, 30))
    assert get_final_date(inst, CREATE, "termination_date") == dt.date(2024, 4, 30)


def test_rule_next_schedule_without_trigger_info_raises():
    with pytest.raises(RuntimeError, match="Next schedule not supported by action"):
        get_final_date(_inst(), CREATE, "next schedule")


def test_rule_next_schedule_is_case_insensitive_and_uses_trigger_info():
    class TriggerInfo:
        next_schedule = dt.date(2024, 6, 1)

    assert get_final_date(_inst(), CREATE, "Next Schedule", trigger_info=TriggerInfo()) == dt.date(2024, 6, 1)


def test_rule_next_schedule_none_falls_back_to_date_max():
    class TriggerInfo:
        next_schedule = None

    assert get_final_date(_inst(), CREATE, "next schedule", trigger_info=TriggerInfo()) == dt.date.max


def test_rule_next_schedule_is_not_cached():
    """The same (inst, create_date, 'next schedule', None) cache key must resolve to whatever this
    call's trigger_info says, not a value memoised from an earlier call."""

    class TI:
        def __init__(self, d):
            self.next_schedule = d

    inst = _inst()
    first = get_final_date(inst, CREATE, "next schedule", trigger_info=TI(dt.date(2024, 3, 1)))
    second = get_final_date(inst, CREATE, "next schedule", trigger_info=TI(dt.date(2024, 9, 1)))
    assert first == dt.date(2024, 3, 1)
    assert second == dt.date(2024, 9, 1)


def test_rule_custom_duration_applies_function_to_resolved_durations():
    cd = CustomDuration(("3m", "1y"), min)
    assert get_final_date(_inst(), CREATE, cd) == min(
        get_final_date(_inst(), CREATE, "3m"),
        get_final_date(_inst(), CREATE, "1y"),
    )


def test_rule_tenor_string_uses_relative_date():
    assert get_final_date(_inst(), CREATE, "3m") == dt.date(2024, 4, 30)


def test_rule_tenor_string_is_case_insensitive():
    assert get_final_date(_inst(), CREATE, "3M") == get_final_date(_inst(), CREATE, "3m")


def test_list_holiday_calendar_is_normalised_to_a_tuple_dev_t16():
    """pricebt DEV-T16: a list holiday_calendar is unhashable, so building the cache key with it
    unchanged raises TypeError. Both forms must give the same result, and the key stored in the
    cache must be the tuple form (not the original list)."""
    inst = _inst()
    hc_list = [dt.date(2024, 2, 1)]
    from_list = get_final_date(inst, CREATE, "1b", holiday_calendar=hc_list)
    from_tuple = get_final_date(inst, CREATE, "1b", holiday_calendar=tuple(hc_list))
    assert from_list == from_tuple
    assert any(isinstance(key[3], tuple) and key[3] == tuple(hc_list) for key in final_date_cache)


def test_final_date_cache_actually_caches():
    inst = _inst()
    get_final_date(inst, CREATE, "3m")
    key = (inst, CREATE, "3m", None)
    assert key in final_date_cache
    assert final_date_cache[key] == dt.date(2024, 4, 30)


def test_clear_final_date_cache_empties_it():
    get_final_date(_inst(), CREATE, "3m")
    assert len(final_date_cache) > 0
    clear_final_date_cache()
    assert len(final_date_cache) == 0


# --------------------------------------------------------------------------------- interpolate_signal


def test_interpolate_signal_step_carries_the_previous_value_forward():
    """research/01 section 5.6: {1st:1, 2nd:2, 4th:4} -> {1st:1, 2nd:2, 3rd:2, 4th:4}."""
    signal = {dt.date(2024, 1, 1): 1.0, dt.date(2024, 1, 2): 2.0, dt.date(2024, 1, 4): 4.0}
    got = interpolate_signal(signal)
    assert isinstance(got, pd.Series)
    assert got.to_dict() == {
        dt.date(2024, 1, 1): 1.0,
        dt.date(2024, 1, 2): 2.0,
        dt.date(2024, 1, 3): 2.0,
        dt.date(2024, 1, 4): 4.0,
    }


def test_interpolate_signal_covers_every_calendar_day_between_min_and_max():
    signal = {dt.date(2024, 1, 1): 1.0, dt.date(2024, 1, 10): 10.0}
    got = interpolate_signal(signal)
    assert list(got.index) == [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)]
