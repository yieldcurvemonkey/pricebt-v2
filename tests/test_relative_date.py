"""RelativeDate / RelativeDateSchedule / pricebt.datetime tests (IMPLEMENTATION_PLAN.md P1.2).

Fixture values come from docs/v2/research/04-gs-instrument-risk-session.md section 8.3 ("Verified
outputs" and "More checks") and docs/v2/research/01-gs-strategy-triggers-datasources.md section 5.4;
every one of them was independently re-derived from numpy.busday_offset/dateutil.relativedelta and
cross-checked against the installed gs_quant 1.5.4 reference while writing this module -- none are
magic numbers.
"""
from __future__ import annotations

import warnings
from datetime import date, datetime

import pytest

from pricebt import datetime as dt_module
from pricebt.datetime import (
    business_day_count,
    business_day_offset,
    date_range,
    is_business_day,
    prev_business_date,
    today,
)
from pricebt.datetime import relative_date as rd_module
from pricebt.datetime.relative_date import RelativeDate, RelativeDateSchedule
from pricebt.markets import PricingContext

BASE_A = date(2024, 1, 31)  # Wednesday
BASE_B = date(2024, 6, 1)  # Saturday

# research 04 section 8.3 "Verified outputs" table
FIXTURES = {
    "10y": (date(2034, 1, 31), date(2034, 6, 1)),
    "1y": (date(2025, 1, 31), date(2025, 6, 2)),
    "30y": (date(2054, 2, 2), date(2054, 6, 1)),
    "5y": (date(2029, 1, 31), date(2029, 6, 1)),
    "6m": (date(2024, 7, 31), date(2024, 12, 2)),
    "3m": (date(2024, 4, 30), date(2024, 9, 2)),
    "1m": (date(2024, 2, 29), date(2024, 7, 1)),
    "1w": (date(2024, 2, 7), date(2024, 6, 10)),
    "2b": (date(2024, 2, 2), date(2024, 6, 4)),
    "0b": (date(2024, 1, 31), date(2024, 6, 3)),
    "-1b": (date(2024, 1, 30), date(2024, 5, 31)),
    "1d": (date(2024, 2, 1), date(2024, 6, 2)),
}


@pytest.mark.parametrize("rule", sorted(FIXTURES))
def test_verified_outputs_from_both_base_dates(rule):
    expected_a, expected_b = FIXTURES[rule]
    assert RelativeDate(rule, BASE_A).apply_rule() == expected_a
    assert RelativeDate(rule, BASE_B).apply_rule() == expected_b


def test_1M_and_1m_are_the_same_rule():
    # pricebt DEV-T15: units are lower-cased. gs 1.5.4 treats '1M' as an unrelated rule ("next
    # 1st Monday"); 2.1.17 (this port's behavioural target) lower-cases, so '1M' == '1m'.
    assert RelativeDate("1M", BASE_A).apply_rule() == RelativeDate("1m", BASE_A).apply_rule() == date(2024, 2, 29)


def test_compound_rule_applies_left_to_right():
    # verified against the installed gs_quant 1.5.4: RelativeDate('1m-1b', 2024-01-31) == 2024-02-28
    assert RelativeDate("1m-1b", BASE_A).apply_rule() == date(2024, 2, 28)
    # verified against gs_quant 1.5.4: RelativeDate('-1y+1m', 2024-01-31) == 2023-02-28
    assert RelativeDate("-1y+1m", BASE_A).apply_rule() == date(2023, 2, 28)


def test_5b_from_friday_matches_get_final_date_fixture():
    # research 01 section 5.4's verified result (the RelativeDate part of get_final_date, whose
    # own port is out of scope for P1.2)
    assert RelativeDate("5b", date(2024, 1, 5)).apply_rule() == date(2024, 1, 12)


def test_holiday_calendar_list_or_tuple():
    expected = [date(2024, 5, 24), date(2024, 5, 28), date(2024, 5, 29), date(2024, 5, 30), date(2024, 5, 31)]
    as_list = RelativeDateSchedule("1b", date(2024, 5, 24), date(2024, 5, 31)).apply_rule(holiday_calendar=[date(2024, 5, 27)])
    as_tuple = RelativeDateSchedule("1b", date(2024, 5, 24), date(2024, 5, 31)).apply_rule(holiday_calendar=(date(2024, 5, 27),))
    assert as_list == expected
    assert as_tuple == expected


def test_saturday_start_is_kept_unadjusted_in_a_schedule():
    result = RelativeDateSchedule("1b", date(2024, 6, 1), date(2024, 6, 7)).apply_rule()
    assert result == [
        date(2024, 6, 1),
        date(2024, 6, 3),
        date(2024, 6, 4),
        date(2024, 6, 5),
        date(2024, 6, 6),
        date(2024, 6, 7),
    ]
    assert result[0] == date(2024, 6, 1)  # raw Saturday, unadjusted


@pytest.mark.parametrize("rule,n", [("1b", 89), ("1w", 18), ("2w", 9), ("1m", 5), ("3m", 2)])
def test_schedule_counts(rule, n):
    assert len(RelativeDateSchedule(rule, BASE_A, date(2024, 6, 3)).apply_rule()) == n


def test_1m_schedule_exact_dates():
    result = RelativeDateSchedule("1m", BASE_A, date(2024, 6, 3)).apply_rule()
    assert result == [date(2024, 1, 31), date(2024, 2, 29), date(2024, 4, 1), date(2024, 4, 30), date(2024, 5, 31)]


def test_3m_schedule_exact_dates():
    assert RelativeDateSchedule("3m", BASE_A, date(2024, 6, 3)).apply_rule() == [date(2024, 1, 31), date(2024, 4, 30)]


def test_schedule_with_no_end_date_is_just_the_base_date():
    assert RelativeDateSchedule("1m", BASE_A).apply_rule() == [BASE_A]


# The optional rules (k, e, x, v, j, a, r, u, g) are not in the task's required-fixture list, so
# every value below was independently verified against the installed gs_quant 1.5.4 reference
# (`RelativeDate(rule, base).apply_rule()`) before being written here -- not a magic number.
OPTIONAL_RULE_BASE = date(2024, 3, 15)  # Friday
OPTIONAL_RULE_FIXTURES = {
    "e": date(2024, 3, 31),
    "x": date(2024, 3, 29),
    "J": date(2024, 3, 1),  # dispatch is case-insensitive (DEV-T15)
    "v": date(2024, 3, 29),  # a bare letter means number=0, exactly as gs's own parser treats it
    "2v": date(2024, 5, 31),
    "g": date(2024, 3, 15),
    "1g": date(2024, 3, 22),
    "-1g": date(2024, 3, 8),
    "r": date(2024, 12, 31),
    "2r": date(2026, 12, 31),
    "-1r": date(2023, 12, 31),
    "u": date(2024, 3, 15),
    "2u": date(2024, 3, 19),
    "-2u": date(2024, 3, 13),
    "0u": date(2024, 3, 15),
    "-0u": date(2024, 3, 15),
    "k": date(2024, 3, 15),
    "2k": date(2026, 3, 16),  # the k-rule's weekend-then-holiday roll, same algorithm as y
    # bare/'0a': real gs ARule is `base.replace(month=1, day=1) + relativedelta(year=n)`, and
    # relativedelta(year=0) is a dateutil no-op -- so this is OPTIONAL_RULE_BASE's own year with
    # month/day reset to 1/1, not `date(0, 1, 1)` (which raises).
    "a": date(OPTIONAL_RULE_BASE.year, 1, 1),
    "0a": date(OPTIONAL_RULE_BASE.year, 1, 1),
}


@pytest.mark.parametrize("rule", sorted(OPTIONAL_RULE_FIXTURES))
def test_optional_rules_bare_and_numbered_forms(rule):
    assert RelativeDate(rule, OPTIONAL_RULE_BASE).apply_rule() == OPTIONAL_RULE_FIXTURES[rule]


def test_optional_rule_a_matches_its_verified_gs_uppercase_formula():
    # gs 1.5.4 only defines uppercase 'A' (lower-case 'a' raises NotImplementedError there);
    # pricebt's DEV-T15 lower-casing deliberately generalises past that, so this is verified
    # against the formula ('2024A' -> 2024-01-01) rather than the lower-case spelling itself.
    assert RelativeDate("2024a", OPTIONAL_RULE_BASE).apply_rule() == date(2024, 1, 1)
    assert RelativeDate("1a", OPTIONAL_RULE_BASE).apply_rule() == date(1, 1, 1)


def test_optional_rule_e_and_x_ignore_the_day_of_week_of_the_base_date():
    saturday = date(2024, 3, 16)
    assert RelativeDate("e", saturday).apply_rule() == date(2024, 3, 31)
    assert RelativeDate("x", saturday).apply_rule() == date(2024, 3, 29)
    assert RelativeDate("1v", saturday).apply_rule() == date(2024, 4, 30)


def test_unknown_rule_letter_raises_not_implemented():
    # verified against gs_quant 1.5.4: NotImplementedError('Rule 1q not implemented')
    with pytest.raises(NotImplementedError, match=r"^Rule 1q not implemented$"):
        RelativeDate("1q", BASE_A).apply_rule()


def test_base_date_none_uses_pricing_context_current_pricing_date():
    with PricingContext(pricing_date=date(2024, 3, 4)):
        assert RelativeDate("0b").base_date == date(2024, 3, 4)
    assert RelativeDate("0b").base_date == date.today()  # nothing entered


def test_as_dict():
    assert RelativeDate("1m", BASE_A).as_dict() == {"rule": "1m", "baseDate": "2024-01-31"}
    with PricingContext(pricing_date=BASE_A):
        assert RelativeDate("1m").as_dict() == {"rule": "1m"}  # base_date not passed in


def test_currencies_exchanges_ignored_with_one_time_warning(monkeypatch):
    monkeypatch.setattr(rd_module, "_warned_currencies_exchanges", False)
    with pytest.warns(UserWarning, match="currencies/exchanges are ignored"):
        RelativeDate("1d", BASE_A).apply_rule(currencies=["USD"])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        RelativeDate("1d", BASE_A).apply_rule(currencies=["USD"])  # no warning the second time
    assert RelativeDate("1d", BASE_A).apply_rule(currencies=["USD"]) == date(2024, 2, 1)  # still works


# ---------------------------------------------------------------------------------- pricebt.datetime


def test_business_day_offset_more_checks():
    # research 04 section 8.3 "More checks"
    sat = date(2024, 6, 1)
    assert business_day_offset(sat, -1, roll="preceding") == date(2024, 5, 30)
    assert business_day_offset(sat, 0, roll="forward") == date(2024, 6, 3)
    assert business_day_offset(date(2024, 6, 3), 1) == date(2024, 6, 4)


def test_business_day_offset_raise_on_non_business_day():
    with pytest.raises(ValueError):
        business_day_offset(date(2024, 6, 1), 0)  # default roll='raise'


def test_prev_business_date():
    assert prev_business_date(date(2024, 6, 3)) == date(2024, 5, 31)


def test_prev_business_date_default_matches_gs_signature_literally():
    # gs's own default is `dates=date.today()`, evaluated once at import time, not `None` -- a
    # known gs quirk this port keeps deliberately (see the comment on prev_business_date). Calling
    # with no `dates` must not raise and must return a business day on-or-before that bound date.
    import inspect

    default = inspect.signature(prev_business_date).parameters["dates"].default
    assert isinstance(default, date)
    result = prev_business_date()
    assert result == business_day_offset(default, -1, roll="forward")
    assert result <= date.today()


def test_date_range_date_date_form():
    assert tuple(date_range(date(2024, 5, 30), date(2024, 6, 4))) == (
        date(2024, 5, 30),
        date(2024, 5, 31),
        date(2024, 6, 3),
        date(2024, 6, 4),
    )


def test_date_range_date_int_form_is_ascending():
    assert tuple(date_range(date(2024, 5, 30), 3)) == (date(2024, 5, 30), date(2024, 5, 31), date(2024, 6, 3))


def test_date_range_int_date_form_is_descending():
    assert tuple(date_range(3, date(2024, 6, 4))) == (date(2024, 6, 4), date(2024, 6, 3), date(2024, 5, 31))


def test_date_range_weekend_begin_raises():
    # verified against the installed gs_quant 1.5.4: a weekend `begin` in the (date, date) form
    # raises once the range steps past it (the gs quirk this module ports verbatim).
    with pytest.raises(ValueError):
        list(date_range(date(2024, 6, 1), date(2024, 6, 1)))


def test_is_business_day():
    assert is_business_day(date(2024, 6, 1)) is False
    assert is_business_day(date(2024, 6, 3)) is True
    assert is_business_day([date(2024, 6, 1), date(2024, 6, 3)]) == (False, True)


def test_business_day_count():
    assert business_day_count(date(2024, 1, 1), date(2024, 1, 10)) == 7


def test_today_returns_a_date():
    assert today() == date.today()


def test_today_with_known_location_uses_that_timezone():
    from zoneinfo import ZoneInfo

    # bracket rather than assert equality, to avoid a flake in the sliver of a second the local
    # and Tokyo clocks could disagree on the date across the call.
    before = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    result = today("TKO")
    after = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    assert before <= result <= after


def test_today_with_unrecognized_location_raises():
    with pytest.raises(ValueError, match="Unrecognized timezone"):
        today("MARS")


def test_calendars_ignored_with_one_time_warning_in_datetime_module(monkeypatch):
    # DEV-T3: pricebt.datetime.* silently drops `calendars` the same way RelativeDate drops
    # currencies/exchanges (see test_currencies_exchanges_ignored_with_one_time_warning above) --
    # mirrors that test's shape exactly.
    monkeypatch.setattr(dt_module, "_warned_calendars", False)
    with pytest.warns(UserWarning, match="calendars is ignored"):
        business_day_offset(date(2024, 6, 3), 1, calendars=("NYSE",))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        business_day_offset(date(2024, 6, 3), 1, calendars=("NYSE",))  # no warning the second time
    # still computes as if calendars had never been passed
    assert business_day_offset(date(2024, 6, 3), 1, calendars=("NYSE",)) == business_day_offset(date(2024, 6, 3), 1)


def test_calendars_warning_also_fires_through_the_other_four_functions(monkeypatch):
    # prev_business_date and date_range don't call _cal() directly -- they forward calendars to
    # business_day_offset, which does. Confirm the warning still reaches every public entry point.
    monkeypatch.setattr(dt_module, "_warned_calendars", False)
    with pytest.warns(UserWarning, match="calendars is ignored"):
        is_business_day(date(2024, 6, 3), calendars=("NYSE",))

    monkeypatch.setattr(dt_module, "_warned_calendars", False)
    with pytest.warns(UserWarning, match="calendars is ignored"):
        business_day_count(date(2024, 1, 1), date(2024, 1, 10), calendars=("NYSE",))

    monkeypatch.setattr(dt_module, "_warned_calendars", False)
    with pytest.warns(UserWarning, match="calendars is ignored"):
        prev_business_date(date(2024, 6, 3), calendars=("NYSE",))

    monkeypatch.setattr(dt_module, "_warned_calendars", False)
    with pytest.warns(UserWarning, match="calendars is ignored"):
        list(date_range(date(2024, 5, 30), date(2024, 6, 4), calendars=("NYSE",)))  # generator: must consume it
