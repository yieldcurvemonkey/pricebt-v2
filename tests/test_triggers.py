"""pricebt.backtests.triggers and pricebt.backtests.strategy (IMPLEMENTATION_PLAN.md P3.4;
DESIGN.md section 9.3, section 11 DEV-T4..T10/T12/T14).

The mean-reversion golden table (research/01 section 6.5.9) has its own file,
tests/test_mean_reversion_golden.py; this file covers everything else: check_barrier, the
Aggregate/Not fixes, the zero-triggers/zero-std/no-data-source error fixes, the intraday midnight
guard, the DEV-T4 backtest-start default, DEV-T10 reset(), and Strategy.get_available_engines.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import (
    AggregateTriggerRequirements,
    AggType,
    DateTrigger,
    DateTriggerRequirements,
    EventTriggerRequirements,
    IntradayTriggerRequirements,
    MeanReversionTriggerRequirements,
    NotTriggerRequirements,
    PeriodicTriggerRequirements,
    RiskTriggerRequirements,
    TriggerDirection,
    TriggerInfo,
    TriggerRequirements,
    check_barrier,
)
from pricebt.backtests.backtest_utils import CalcType


# --------------------------------------------------------------------------------- check_barrier


def test_check_barrier_above_is_strict():
    assert check_barrier(TriggerDirection.ABOVE, 5, 5).triggered is False
    assert check_barrier(TriggerDirection.ABOVE, 6, 5).triggered is True


def test_check_barrier_below_is_strict():
    assert check_barrier(TriggerDirection.BELOW, 5, 5).triggered is False
    assert check_barrier(TriggerDirection.BELOW, 4, 5).triggered is True


def test_check_barrier_equal_for_any_other_direction():
    assert check_barrier(TriggerDirection.EQUAL, 5, 5).triggered is True
    assert check_barrier(None, 5, 5).triggered is True
    assert check_barrier(None, 4, 5).triggered is False


def test_check_barrier_nan_never_triggers():
    nan = float('nan')
    assert check_barrier(TriggerDirection.ABOVE, nan, 5).triggered is False
    assert check_barrier(TriggerDirection.BELOW, nan, 5).triggered is False
    assert check_barrier(TriggerDirection.EQUAL, nan, 5).triggered is False


def test_trigger_info_eq_is_identity_not_value_equality():
    # research/01 section 6.5.1 (G13): TriggerInfo(True) == True but TriggerInfo(True) !=
    # TriggerInfo(True) -- the engine only ever uses truthiness, so this is harmless and kept as-is.
    assert TriggerInfo(True) == True  # noqa: E712
    assert TriggerInfo(True) != TriggerInfo(True)
    assert bool(TriggerInfo(True)) is True
    assert bool(TriggerInfo(False)) is False


# ------------------------------------------------------------------------ Aggregate: ALL_OF / ANY_OF


class _CountingReq(TriggerRequirements):
    """Bare TriggerRequirements stand-in that records how many times it was evaluated, so ALL_OF's
    short-circuit and ANY_OF's evaluate-every-child behaviour (kept on purpose, DESIGN.md section
    11) can be checked directly without building a full Trigger/Action pair."""

    def __init__(self, result: bool):
        self.result = result
        self.calls = 0

    def has_triggered(self, state, backtest=None):
        self.calls += 1
        return TriggerInfo(self.result)


def test_aggregate_all_of_short_circuits_on_first_false():
    first_false = _CountingReq(False)
    never_called = _CountingReq(True)
    agg = AggregateTriggerRequirements(triggers=[first_false, never_called], aggregate_type=AggType.ALL_OF)

    info = agg.has_triggered(dt.date(2024, 1, 1))

    assert info.triggered is False
    assert first_false.calls == 1
    assert never_called.calls == 0


def test_aggregate_any_of_evaluates_every_child():
    first_false = _CountingReq(False)
    second_true = _CountingReq(True)
    agg = AggregateTriggerRequirements(triggers=[first_false, second_true], aggregate_type=AggType.ANY_OF)

    info = agg.has_triggered(dt.date(2024, 1, 1))

    assert info.triggered is True
    assert first_false.calls == 1
    assert second_true.calls == 1  # not short-circuited, unlike ALL_OF


# ------------------------------------------------------------------------------------- DEV-T8


def test_aggregate_with_zero_triggers_raises_value_error_dev_t8():
    with pytest.raises(ValueError, match='triggers required'):
        AggregateTriggerRequirements()


def test_aggregate_with_explicit_empty_list_does_not_raise():
    # Only the None default (no args at all) is the "zero triggers" crash gs has; an explicit
    # empty list is not None and converts trivially (vacuous `all([])` is True).
    agg = AggregateTriggerRequirements(triggers=[])
    assert agg.triggers == ()


# ------------------------------------------------------------------------------------- DEV-T7


def test_aggregate_get_trigger_times_is_union_of_children_dev_t7():
    d1, d2, d3 = dt.date(2024, 1, 1), dt.date(2024, 1, 2), dt.date(2024, 1, 3)
    a = DateTriggerRequirements(dates=[d1, d2])
    b = DateTriggerRequirements(dates=[d2, d3])
    agg = AggregateTriggerRequirements(triggers=[a, b])

    assert agg.get_trigger_times() == [d1, d2, d3]


def test_not_get_trigger_times_is_the_childs_times_dev_t7():
    d1, d3 = dt.date(2024, 1, 1), dt.date(2024, 1, 3)
    child = DateTriggerRequirements(dates=[d3, d1])
    not_req = NotTriggerRequirements(trigger=child)

    assert not_req.get_trigger_times() == [d1, d3]


# ------------------------------------------------------------------------------------- DEV-T6


def test_not_requirements_setattr_sets_every_attribute_dev_t6():
    child = DateTriggerRequirements(dates=[dt.date(2024, 1, 1)])
    not_req = NotTriggerRequirements(trigger=child)

    # gs's bug: super().__setattr__ is only reached for key == 'trigger', so any OTHER attribute
    # set here would be silently dropped -- not ported.
    not_req.probe = 'set'
    assert not_req.probe == 'set'
    assert not_req.trigger is child


def test_not_requirements_still_unwraps_a_trigger_into_its_requirements():
    child = DateTriggerRequirements(dates=[dt.date(2024, 1, 1)])
    trig = DateTrigger(trigger_requirements=child, actions=[])
    not_req = NotTriggerRequirements(trigger=trig)

    assert not_req.trigger is child


# ------------------------------------------------------------------------------------- DEV-T5


def test_not_requirements_calc_type_inherits_childs_calc_type_dev_t5():
    path_dependent_child = RiskTriggerRequirements(risk=None, trigger_level=0, direction=TriggerDirection.ABOVE)
    not_req = NotTriggerRequirements(trigger=path_dependent_child)

    assert path_dependent_child.calc_type == CalcType.path_dependent
    assert not_req.calc_type == CalcType.path_dependent  # not the hard-coded 'simple' gs has


def test_not_requirements_has_triggered_inverts_the_child():
    always_false = _CountingReq(False)
    always_true = _CountingReq(True)

    assert NotTriggerRequirements(trigger=always_false).has_triggered(dt.date(2024, 1, 1)).triggered is True
    assert NotTriggerRequirements(trigger=always_true).has_triggered(dt.date(2024, 1, 1)).triggered is False


# ------------------------------------------------------------------------------------- DEV-T9


def test_intraday_midnight_wrap_stops_instead_of_looping_forever_dev_t9():
    req = IntradayTriggerRequirements(start_time=dt.time(23, 0), end_time=dt.time(23, 59), frequency=30)

    assert req.get_trigger_times() == [dt.time(23, 0), dt.time(23, 30)]


def test_intraday_non_wrapping_schedule_unaffected():
    # research/01 section 6.5.2's own verified example, to prove the DEV-T9 guard changes nothing
    # for the ordinary (non-wrapping) case.
    req = IntradayTriggerRequirements(start_time=dt.time(9, 0), end_time=dt.time(10, 0), frequency=30)

    assert req.get_trigger_times() == [dt.time(9, 0), dt.time(9, 30), dt.time(10, 0)]


# ------------------------------------------------------------------------------------- DEV-T12


def test_event_requirements_with_no_data_source_raises_value_error_dev_t12():
    with pytest.raises(ValueError, match='data_source required'):
        EventTriggerRequirements(event_name='CPI')


# ------------------------------------------------------------------------------------- DEV-T4


def test_periodic_start_date_none_uses_backtest_start_dev_t4():
    req = PeriodicTriggerRequirements(frequency='1w', end_date=dt.date(2024, 1, 22))
    req._backtest_start = dt.date(2024, 1, 1)

    times = req.get_trigger_times()

    assert times[0] == dt.date(2024, 1, 1)
    assert times[-1] == dt.date(2024, 1, 22)


def test_periodic_explicit_start_date_wins_over_backtest_start_dev_t4():
    req = PeriodicTriggerRequirements(
        start_date=dt.date(2024, 3, 1), frequency='1w', end_date=dt.date(2024, 3, 8)
    )
    req._backtest_start = dt.date(2024, 1, 1)  # must be ignored: start_date is explicit

    assert req.get_trigger_times()[0] == dt.date(2024, 3, 1)


# ------------------------------------------------------------------------------------- DEV-T10 (reset)


def test_periodic_reset_clears_memoised_trigger_dates_dev_t10():
    req = PeriodicTriggerRequirements(frequency='1w', end_date=dt.date(2024, 1, 22))
    req._backtest_start = dt.date(2024, 1, 1)
    req.get_trigger_times()
    assert req.trigger_dates != []

    req.reset()

    assert req.trigger_dates == []


def _mr_series(prices, start=dt.date(2024, 2, 1)) -> GenericDataSource:
    dates = [start + dt.timedelta(days=i) for i in range(len(prices))]
    return GenericDataSource(pd.Series(prices, index=dates), MissingDataStrategy.fill_forward)


def test_mean_reversion_reset_clears_current_position_dev_t10():
    # Six identical prices (rolling_std == 0 on the 7th day) then a jump: guaranteed to fire (see
    # the zero-std test below), which is all this test needs to get current_position off zero.
    src = _mr_series([2, 2, 2, 2, 2, 2, 3])
    req = MeanReversionTriggerRequirements(
        data_source=src, z_score_bound=1.5, rolling_mean_window=5, rolling_std_window=5
    )
    state = dt.date(2024, 2, 1) + dt.timedelta(days=6)

    info = req.has_triggered(state)
    assert info.triggered is True
    assert req.current_position != 0

    req.reset()

    assert req.current_position == 0


class _FakeEventSource:
    """Minimal duck-typed data source: `get_data(None, **kwargs) -> Series indexed by
    datetime/date` (research/01 section 6.5.11's v2 contract). Filter kwargs are accepted and
    ignored, as the contract allows."""

    def __init__(self, index):
        self._series = pd.Series(range(len(index)), index=index)

    def get_data(self, state, **kwargs):
        return self._series


def test_event_requirements_reset_clears_memoised_trigger_dates_dev_t10():
    idx = pd.DatetimeIndex([dt.datetime(2024, 1, 3), dt.datetime(2024, 1, 10)])
    req = EventTriggerRequirements(event_name='CPI', data_source=_FakeEventSource(idx))
    req.get_trigger_times()
    assert req.trigger_dates != []

    req.reset()

    assert req.trigger_dates == []


def test_base_requirements_reset_is_a_no_op():
    req = TriggerRequirements()
    req.reset()  # must not raise


def test_aggregate_reset_walks_children_dev_t10():
    src = _mr_series([2, 2, 2, 2, 2, 2, 3])
    mr = MeanReversionTriggerRequirements(
        data_source=src, z_score_bound=1.5, rolling_mean_window=5, rolling_std_window=5
    )
    mr.has_triggered(dt.date(2024, 2, 1) + dt.timedelta(days=6))
    assert mr.current_position != 0
    agg = AggregateTriggerRequirements(triggers=[mr])

    agg.reset()

    assert mr.current_position == 0


def test_not_reset_walks_its_child_dev_t10():
    src = _mr_series([2, 2, 2, 2, 2, 2, 3])
    mr = MeanReversionTriggerRequirements(
        data_source=src, z_score_bound=1.5, rolling_mean_window=5, rolling_std_window=5
    )
    mr.has_triggered(dt.date(2024, 2, 1) + dt.timedelta(days=6))
    assert mr.current_position != 0
    not_req = NotTriggerRequirements(trigger=mr)

    not_req.reset()

    assert mr.current_position == 0


# ------------------------------------------------------------------------------- kept on purpose


def test_mean_reversion_zero_std_fires():
    # DESIGN.md section 11 "kept on purpose": the MR window excludes today, uses the sample std
    # (ddof=1), and a zero std gives inf (numpy float division), which fires. Six identical prices
    # give rolling_std == 0 for the window strictly before the 7th day.
    src = _mr_series([2, 2, 2, 2, 2, 2, 3])
    state = dt.date(2024, 2, 1) + dt.timedelta(days=6)
    window = src.get_data_range(state, 5)
    assert window.std() == 0  # the premise: a genuinely zero std, not a near-zero one

    req = MeanReversionTriggerRequirements(
        data_source=src, z_score_bound=1.5, rolling_mean_window=5, rolling_std_window=5
    )
    info = req.has_triggered(state)

    assert info.triggered is True
    assert info.info_dict is not None


# ------------------------------------------------------------------------------- Strategy (DEV-T14)


def test_get_available_engines_with_zero_triggers_returns_generic_engine_dev_t14(monkeypatch):
    import pricebt.backtests.generic_engine as generic_engine_module

    class _FakeGenericEngine:
        pass

    monkeypatch.setattr(generic_engine_module, 'GenericEngine', _FakeGenericEngine, raising=False)
    strategy = Strategy(initial_portfolio=None, triggers=None)

    engines = strategy.get_available_engines()

    assert len(engines) == 1
    assert isinstance(engines[0], _FakeGenericEngine)


def test_strategy_with_no_triggers_has_empty_risks():
    strategy = Strategy(initial_portfolio=None, triggers=None)
    assert strategy.triggers == []
    assert strategy.risks == []
