"""pricebt.backtests.data_sources (IMPLEMENTATION_PLAN.md P3.1; DESIGN.md section 9.3, DEV-T13).

Every fixture value here is derived from the input series in the test itself (no magic constants):
e.g. the "look-ahead" check builds a series whose LAST point (5.0) is obviously wrong for a lookup
between two much smaller values (1.0, 2.0), so a passing assertion of `== 2.0` cannot be satisfied
by accident.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

from pricebt.backtests.data_sources import (
    DataManager,
    DataSource,
    GenericDataSource,
    GsDataSource,
    MissingDataStrategy,
)
from pricebt.errors import NotSupportedError

DATES = [dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 4), dt.date(2024, 1, 5)]
VALUES = [1.0, 2.0, 4.0, 5.0]


def _series(index=DATES, values=VALUES) -> pd.Series:
    return pd.Series(values, index=list(index))


# ------------------------------------------------------------------------------- DataSource base


def test_sub_classes_registers_gs_data_source_then_generic_data_source():
    # Sliced, not exact-equal: other tests in this session may register further DataSource
    # subclasses (e.g. a fake source for a later phase's CashAccrualModel test), but the two real
    # module-level classes always register first, at import time, in file order.
    assert DataSource.sub_classes()[:2] == (GsDataSource, GenericDataSource)


def test_data_source_base_methods_raise_runtime_error():
    class Bespoke(DataSource):
        pass

    with pytest.raises(RuntimeError):
        Bespoke().get_data(None)
    with pytest.raises(RuntimeError):
        Bespoke().get_data_range(None, None)


# ------------------------------------------------------------------------- GenericDataSource.get_data


def test_fill_forward_returns_the_previous_value_not_a_later_one_dev_t13():
    """research/01 section 7.4: gs's version inserts the missing key at the end of the series and
    discards the re-sort before ffill, so it wrongly returns the LAST value (5.0) instead of the
    correct previous value (2.0)."""
    src = GenericDataSource(_series(), MissingDataStrategy.fill_forward)
    assert src.get_data(dt.date(2024, 1, 3)) == 2.0


def test_fill_forward_before_the_first_point_is_nan():
    src = GenericDataSource(_series(), MissingDataStrategy.fill_forward)
    assert math.isnan(src.get_data(dt.date(2023, 12, 31)))


def test_fill_forward_after_the_last_point_holds_the_last_value():
    src = GenericDataSource(_series(), MissingDataStrategy.fill_forward)
    assert src.get_data(dt.date(2024, 1, 10)) == 5.0


def test_interpolate_is_by_position_not_by_time():
    """A gap of 3 calendar days (Jan2 -> Jan5) interpolated at Jan3 (1 day in): by TIME the
    result would be 2 + (8-2) * 1/3 = 4.0; pandas' default (position-based) interpolation instead
    treats Jan1/Jan2/Jan3/Jan5 as equally spaced, giving 2 + (8-2) * 1/2 = 5.0."""
    series = pd.Series([1.0, 2.0, 8.0], index=[dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 5)])
    src = GenericDataSource(series, MissingDataStrategy.interpolate)
    assert src.get_data(dt.date(2024, 1, 3)) == 5.0


def test_interpolate_matches_the_implementation_plans_worked_example():
    """IMPLEMENTATION_PLAN.md P3.1 names this exact worked example ("interpolate 3.0"): the
    canonical fixture {1st:1, 2nd:2, 4th:4, 5th:5} (this module's own DATES/VALUES, also used for
    the fill_forward test above) looked up at the missing 3rd. Sorted index positions are
    Jan1(1.0), Jan2(2.0), Jan3(missing), Jan4(4.0), Jan5(5.0); position-based interpolation between
    the two neighbouring positions gives 2.0 + (4.0 - 2.0) * 1/2 = 3.0."""
    src = GenericDataSource(_series(), MissingDataStrategy.interpolate)
    assert src.get_data(dt.date(2024, 1, 3)) == 3.0


def test_fail_strategy_raises_key_error_on_a_miss():
    src = GenericDataSource(_series(), MissingDataStrategy.fail)
    with pytest.raises(KeyError):
        src.get_data(dt.date(2024, 1, 3))


def test_exact_hit_returned_for_every_strategy():
    for strategy in MissingDataStrategy:
        src = GenericDataSource(_series(), strategy)
        assert src.get_data(dt.date(2024, 1, 2)) == 2.0


def test_get_data_none_returns_the_whole_series():
    src = GenericDataSource(_series())
    got = src.get_data(None)
    assert isinstance(got, pd.Series)
    assert len(got) == len(DATES)


def test_get_data_list_returns_a_list_of_values():
    src = GenericDataSource(_series())
    assert src.get_data([dt.date(2024, 1, 1), dt.date(2024, 1, 2)]) == [1.0, 2.0]


# -------------------------------------------------------------------- GenericDataSource.get_data_range


def test_get_data_range_int_end_is_the_n_points_strictly_before_start():
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    src = GenericDataSource(pd.Series(range(10), index=dates))
    got = src.get_data_range(dt.date(2024, 1, 6), 3)  # index 5; strictly before -> indices 2,3,4
    assert list(got.index) == dates[2:5]
    assert list(got.values) == [2, 3, 4]


def test_get_data_range_date_end_is_start_exclusive_end_inclusive():
    dates = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    src = GenericDataSource(pd.Series(range(10), index=dates))
    got = src.get_data_range(dt.date(2024, 1, 3), dt.date(2024, 1, 7))
    assert list(got.index) == dates[3:7]  # (start, end]: Jan3 excluded, Jan7 included


def test_get_data_range_on_a_datetime_index_accepts_a_plain_date_no_type_error_dev_t13():
    """research/01 section 7.4: gs raises `TypeError: Invalid comparison between
    dtype=datetime64[ns] and date` here on pandas 2.3.1."""
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    src = GenericDataSource(pd.Series([10.0, 20.0, 30.0, 40.0, 50.0], index=idx))
    got = src.get_data_range(dt.date(2024, 1, 2), dt.date(2024, 1, 4))
    assert list(got.values) == [30.0, 40.0]


# --------------------------------------------------------------------------------------- no mutation


def test_construction_and_lookups_never_mutate_the_input_series():
    original = _series()
    passed_in = original.copy()
    src = GenericDataSource(passed_in, MissingDataStrategy.fill_forward)
    src.get_data(dt.date(2024, 1, 3))  # a miss, handled by insertion in gs's buggy version
    src.get_data(dt.date(2024, 1, 3))  # again: gs's mutation would have turned this into a hit
    src.get_data_range(dt.date(2024, 1, 1), dt.date(2024, 1, 5))
    pd.testing.assert_series_equal(passed_in, original)


def test_eq_compares_the_original_unnormalised_data_set():
    a = GenericDataSource(_series(), MissingDataStrategy.fill_forward)
    b = GenericDataSource(_series(), MissingDataStrategy.fill_forward)
    c = GenericDataSource(_series(), MissingDataStrategy.fail)
    assert a == b
    assert a != c
    assert a != object()


# --------------------------------------------------------------------------------- index normalisation


def test_date_only_series_normalises_to_a_date_index():
    src = GenericDataSource(_series())
    assert all(type(k) is dt.date for k in src.get_data(None).index)


def test_a_naive_datetime_lookup_against_a_date_indexed_series_matches_its_date():
    src = GenericDataSource(_series())
    assert src.get_data(dt.datetime(2024, 1, 2, 0, 0)) == 2.0


def test_tz_aware_series_labels_a_naive_lookup_as_utc():
    idx = pd.to_datetime([dt.datetime(2024, 1, 2, 10, 0)]).tz_localize("UTC")
    src = GenericDataSource(pd.Series([1.0], index=idx))
    assert src.get_data(dt.datetime(2024, 1, 2, 10, 0)) == 1.0


# ---------------------------------------------------------------------------------- Out-of-scope stubs


def test_gs_data_source_raises_on_construction():
    with pytest.raises(NotSupportedError):
        GsDataSource(data_set="MY_DATASET", asset_id="MA123")


def test_data_manager_raises_on_construction():
    with pytest.raises(NotSupportedError):
        DataManager()
