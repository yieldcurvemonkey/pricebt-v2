import datetime as dt
import logging

import pandas as pd
import pytest

from pricebt.errors import MissingDataError, StrategyError, TriggerError
from pricebt.strategy import (AddTradeAction, AggregateTriggerRequirements, AggType, CalcType, CrossingTriggerRequirements,
                              CustomTriggerRequirements, DateTriggerRequirements, EventCalendar, EventTriggerRequirements, IntradayTriggerRequirements, MeanReversionTriggerRequirements,
                              MktTriggerRequirements, NotTriggerRequirements, PeriodicTrigger, PeriodicTriggerRequirements, PortfolioTriggerRequirements,
                              RebalanceTriggerRequirements, RiskBandTriggerRequirements, SeriesSource, SignalStore, StrategyRiskTriggerRequirements,
                              TradeCountTriggerRequirements, TriggerDirection, check_barrier)
from pricebt.timeutil import TimeContext, TimelineContext
from test_strategy_support import D, FakeView, NY, fwd, mk_ctx

pytestmark = pytest.mark.core

ABOVE, BELOW, ANY, EQUAL = TriggerDirection.ABOVE, TriggerDirection.BELOW, TriggerDirection.ANY, TriggerDirection.EQUAL


def bound(req, ctx):
    req.bind(ctx)
    return req


# ------------------------------------------------------------------ barrier
def test_check_barrier_strict_and_nonfinite_raises():
    assert check_barrier(5, 4, ABOVE) and not check_barrier(4, 4, ABOVE)
    assert check_barrier(3, 4, BELOW) and not check_barrier(4, 4, BELOW)
    assert check_barrier(4.05, 4, EQUAL, tol=0.1) and not check_barrier(4.2, 4, EQUAL, tol=0.1)
    with pytest.raises(TriggerError):
        check_barrier(float("nan"), 4, ABOVE)


# ------------------------------------------------------------------ Periodic
def test_periodic_gs_month_end_schedule_from_the_base_and_next_schedule_chain():
    ctx = mk_ctx("2024-01-31", "2024-05-31")
    r = bound(PeriodicTriggerRequirements(dt.date(2024, 1, 31), dt.date(2024, 5, 31), "1m"), ctx)
    want = [D(d) for d in ("2024-01-31", "2024-02-29", "2024-04-01", "2024-04-30", "2024-05-31")]  # Mar 31 is a Sunday -> Apr 1
    assert list(r.get_trigger_times()) == want
    infos = [r.has_triggered(t, None) for t in want]
    assert all(i.triggered for i in infos)
    assert [i.info["*"].next_schedule for i in infos] == want[1:] + [None]  # the last date holds to the end
    assert not r.has_triggered(D("2024-02-01"), None)


def test_periodic_defaults_to_backtest_window_and_1d_is_every_business_day_once():
    ctx = mk_ctx("2024-05-24", "2024-05-31")
    r = bound(PeriodicTriggerRequirements(frequency="1d"), ctx)
    days = [t.date() for t in r.get_trigger_times()]
    assert days == [dt.date(2024, 5, d) for d in (24, 27, 28, 29, 30, 31)] and all(d.weekday() < 5 for d in days)


def test_periodic_adjust_start_and_local_holiday_calendar():
    ctx = mk_ctx("2024-01-02", "2024-02-29")
    sat = dt.date(2024, 1, 6)
    assert bound(PeriodicTriggerRequirements(sat, None, "1w"), ctx).get_trigger_times()[0] == D("2024-01-08")
    assert bound(PeriodicTriggerRequirements(sat, None, "1w", adjust_start="none"), ctx).get_trigger_times()[0] == D("2024-01-06")
    assert bound(PeriodicTriggerRequirements(sat, None, "1w", adjust_start="preceding"), ctx).get_trigger_times()[0] == D("2024-01-05")
    plain = bound(PeriodicTriggerRequirements(dt.date(2024, 1, 2), None, "1w"), ctx).get_trigger_times()
    held = bound(PeriodicTriggerRequirements(dt.date(2024, 1, 2), None, "1w", calendar=[dt.date(2024, 1, 9)]), ctx).get_trigger_times()
    assert plain[1] == D("2024-01-09") and held[1] == D("2024-01-10")  # local holiday pushes the schedule date forward


@pytest.mark.parametrize("bad", ["0b", "-1b", "1.5b", "1b1w", "15min", "abc"])
def test_periodic_bad_frequency_fails_at_construction(bad):
    with pytest.raises(StrategyError):
        PeriodicTriggerRequirements(None, None, bad)
    with pytest.raises(StrategyError):
        PeriodicTriggerRequirements(None, None, None)


def test_periodic_used_before_bind_raises_and_reset_is_idempotent():
    r = PeriodicTriggerRequirements(None, None, "1w")
    with pytest.raises(TriggerError):
        r.has_triggered(D("2024-01-02"), None)
    r.reset()


# ------------------------------------------------------------------ Intraday
def ctx_days(*days):
    tc = TimeContext()
    return TimelineContext(D(days[0]), D(days[-1]), tc, tuple(dt.date.fromisoformat(d) for d in days))


def utc(ts_list):
    return [t.tz_convert("UTC").strftime("%H:%M") for t in ts_list]


def test_intraday_5min_session_has_79_instants_per_day():
    ctx = ctx_days("2024-05-01", "2024-05-02")
    r = bound(IntradayTriggerRequirements(dt.time(9, 30), dt.time(16, 0), "5min"), ctx)
    assert len(r.get_trigger_times()) == 2 * 79


def test_intraday_wrap_cases_terminate_and_are_finite():
    ctx = ctx_days("2024-05-01")
    late = bound(IntradayTriggerRequirements(dt.time(22, 0), dt.time(23, 30), 60), ctx)  # gs looped forever on this
    assert [t.strftime("%H:%M") for t in late.get_trigger_times()] == ["22:00", "23:00"]
    full = bound(IntradayTriggerRequirements(dt.time(0, 0), dt.time(23, 59), 30), ctx)
    assert len(full.get_trigger_times()) == 48
    with pytest.raises(StrategyError):
        bound(IntradayTriggerRequirements(dt.time(22, 0), dt.time(1, 0), 60), ctx)
    wrapped = bound(IntradayTriggerRequirements(dt.time(22, 0), dt.time(1, 0), 60, wrap_midnight=True), ctx)
    assert [t.strftime("%d %H:%M") for t in wrapped.get_trigger_times()] == ["01 22:00", "01 23:00", "02 00:00", "02 01:00"]


def test_intraday_dst_gap_skipped_and_ambiguous_first_occurrence():
    spring = bound(IntradayTriggerRequirements(dt.time(1, 0), dt.time(4, 0), 30), ctx_days("2026-03-08"))
    assert [t.strftime("%H:%M") for t in spring.get_trigger_times()] == ["01:00", "01:30", "03:00", "03:30", "04:00"]  # 02:xx does not exist
    assert utc(spring.get_trigger_times())[:3] == ["06:00", "06:30", "07:00"]
    fall = bound(IntradayTriggerRequirements(dt.time(0, 30), dt.time(3, 0), 30), ctx_days("2025-11-02"))
    assert utc(fall.get_trigger_times()) == ["04:30", "05:00", "05:30", "07:00", "07:30", "08:00"]  # 01:xx resolved to the FIRST (EDT) occurrence


def test_intraday_frequency_forms_and_validation():
    ctx = ctx_days("2024-05-01")
    assert len(bound(IntradayTriggerRequirements(dt.time(9, 0), dt.time(10, 0), "90s"), ctx).get_trigger_times()) == 41
    assert len(bound(IntradayTriggerRequirements(dt.time(9, 0), dt.time(10, 0), "1h"), ctx).get_trigger_times()) == 2
    for bad in (0, -5, "0min", "abc"):
        with pytest.raises(StrategyError):
            IntradayTriggerRequirements(None, None, bad)


def test_intraday_next_schedule_and_exact_match():
    r = bound(IntradayTriggerRequirements(dt.time(9, 0), dt.time(10, 0), 30), ctx_days("2024-05-01"))
    t = pd.Timestamp("2024-05-01 09:30", tz=NY)
    info = r.has_triggered(t)
    assert info.info["*"].next_schedule == pd.Timestamp("2024-05-01 10:00", tz=NY)
    assert not r.has_triggered(t + pd.Timedelta(seconds=1))


# ------------------------------------------------------------------ Date
def test_date_trigger_exact_instant_and_datetime_forms():
    ctx = mk_ctx("2024-01-02", "2024-02-29")
    r = bound(DateTriggerRequirements([dt.date(2024, 1, 10), dt.datetime(2024, 1, 12, 9, 0)]), ctx)
    assert r.has_triggered(D("2024-01-10")).info["*"].next_schedule == pd.Timestamp("2024-01-12 09:00", tz=NY)
    assert r.has_triggered(pd.Timestamp("2024-01-12 09:00", tz=NY)).info["*"].next_schedule is None
    assert not r.has_triggered(pd.Timestamp("2024-01-10 09:00", tz=NY))  # date-only input = the date_policy instant, not any time that day
    assert list(r.get_trigger_times()) == [D("2024-01-10"), pd.Timestamp("2024-01-12 09:00", tz=NY)]


def test_date_trigger_entire_day_all_vs_first():
    ctx = mk_ctx("2024-01-02", "2024-02-29")
    minutes = [pd.Timestamp("2024-01-10 09:00", tz=NY) + pd.Timedelta(minutes=m) for m in range(3)]
    other = pd.Timestamp("2024-01-11 09:00", tz=NY)
    all_ = bound(DateTriggerRequirements([dt.date(2024, 1, 10)], entire_day=True), ctx)
    assert [bool(all_.has_triggered(t)) for t in minutes + [other]] == [True, True, True, False]
    first = bound(DateTriggerRequirements([dt.date(2024, 1, 10)], entire_day=True, fire="first"), ctx)
    assert [bool(first.has_triggered(t)) for t in minutes] == [True, False, False]
    first.reset()
    assert first.has_triggered(minutes[0])  # reset clears the fired-dates memory: a second run is identical
    assert first.stateful and not all_.stateful


def test_date_trigger_empty_warns_and_never_fires(caplog):
    with caplog.at_level(logging.WARNING):
        r = bound(DateTriggerRequirements([]), mk_ctx("2024-01-02", "2024-01-31"))
    assert "never fire" in caplog.text and not r.has_triggered(D("2024-01-02"))


# ------------------------------------------------------------------ Mkt vs Crossing
SERIES = [1, 3, 5, 4, 2, 6, 7, 1, 0, 5]


def run_series(req, series=SERIES):
    out = []
    for i, x in enumerate(series):
        if req.has_triggered(D("2024-01-02"), FakeView(x=x)).triggered:
            out.append(i)
    return out


def test_mkt_trigger_is_level_triggered():
    assert run_series(MktTriggerRequirements("s", 4, ABOVE)) == [2, 5, 6, 9]  # fires at EVERY point where the state holds
    assert run_series(MktTriggerRequirements("s", 4, BELOW)) == [0, 1, 4, 7, 8]
    assert run_series(MktTriggerRequirements("s", 4, EQUAL, tol=0.0)) == [3]


def test_mkt_trigger_validation_and_missing_data_policy():
    with pytest.raises(TriggerError):
        MktTriggerRequirements("s", 1, None)
    with pytest.raises(TriggerError):
        MktTriggerRequirements("s", 1, ANY)

    class Missing(FakeView):
        def signal(self, source, ts=None):
            raise MissingDataError("nothing visible")

    with pytest.raises(MissingDataError):
        MktTriggerRequirements("s", 1, ABOVE).has_triggered(D("2024-01-02"), Missing())
    assert not MktTriggerRequirements("s", 1, ABOVE, on_missing="skip").has_triggered(D("2024-01-02"), Missing())


def test_crossing_truth_tables_edge_triggered():
    C = CrossingTriggerRequirements
    assert run_series(C("s", 4, ABOVE)) == [2, 5, 9]
    assert run_series(C("s", 4, ABOVE, hysteresis=3)) == [2, 9]  # the re-arm needs x <= 1; the 6 at index 5 is missed
    assert run_series(C("s", 4, BELOW)) == [4, 7]
    assert run_series(C("s", 4, ANY)) == [2, 4, 5, 7, 9]
    assert run_series(C("s", 4, ANY, hysteresis=1)) == [5, 7]


def test_crossing_first_observation_only_initialises_unless_fire_on_first():
    C = CrossingTriggerRequirements
    assert run_series(C("s", 4, ABOVE), [9, 9, 1, 9]) == [3]  # started above: no crossing until it comes back
    assert run_series(C("s", 4, ABOVE, fire_on_first=True), [9, 9, 1, 9]) == [0, 3]
    assert run_series(C("s", 4, ANY, fire_on_first=True), [9, 1]) == [0, 1]


def test_crossing_reset_and_validation():
    c = CrossingTriggerRequirements("s", 4, ABOVE)
    first = run_series(c)
    c.reset()
    assert run_series(c) == first  # a second run is identical
    for kw in ({"direction": EQUAL}, {"hysteresis": -1}):
        with pytest.raises(TriggerError):
            CrossingTriggerRequirements("s", 4, **{"direction": ABOVE, **kw})
    with pytest.raises(TriggerError):
        c.has_triggered(D("2024-01-02"), FakeView(x=float("nan")))


# ------------------------------------------------------------------ StrategyRisk / Portfolio / TradeCount
def test_strategy_risk_barrier_flat_book_and_transformation():
    R = StrategyRiskTriggerRequirements
    v = FakeView(measures={"dv01": 60.0}, n=2)
    assert R("dv01", 50, ABOVE).has_triggered(D("2024-01-02"), v)
    assert not R("dv01", 50, ABOVE).has_triggered(D("2024-01-02"), FakeView(measures={"dv01": 60.0}, n=0))  # flat book: not fired
    assert R("dv01", 0, BELOW).has_triggered(D("2024-01-02"), FakeView(measures={"dv01": 0.0}, n=0), ) is not None
    assert not R("dv01", 0, BELOW).has_triggered(D("2024-01-02"), FakeView(measures={"dv01": -5.0}, n=0))  # flat + BELOW must not fire on an empty book
    assert R("dv01", 50, ABOVE, risk_transformation="abs").has_triggered(D("2024-01-02"), FakeView(measures={"dv01": -60.0}, n=1))
    assert R("dv01", 50, BELOW, risk_transformation="neg").has_triggered(D("2024-01-02"), FakeView(measures={"dv01": 60.0}, n=1))
    assert R("dv01", 0, ABOVE).calc_type is CalcType.path_dependent and R("dv01", 0, ABOVE).requires_measures() == ("dv01",)
    assert R("equity", 0, ABOVE).requires_measures() == ()


def test_strategy_risk_equity_drawdown_pnl_and_check_every():
    R = StrategyRiskTriggerRequirements
    dd = R("drawdown", 10.0, ABOVE)
    fired = []
    for eq in (100.0, 120.0, 115.0, 105.0, 130.0):  # running peak 120 -> drawdown 15 at 105
        fired.append(bool(dd.has_triggered(D("2024-01-02"), FakeView(equity=eq, n=0))))
    assert fired == [False, False, False, True, False]
    dd.reset()
    assert not dd.has_triggered(D("2024-01-02"), FakeView(equity=50.0, n=0))  # the peak is cleared by reset
    assert dd.stateful

    class PV(FakeView):
        def positions(self, scope="portfolio", *, include_pending=False):
            return (type("P", (), {"pnl": -30.0})(), type("P", (), {"pnl": -25.0})())

    assert R("pnl", -50.0, BELOW).has_triggered(D("2024-01-02"), PV(n=2))  # stop-loss idiom
    ce = R("dv01", 0, ABOVE, check_every=3)
    got = [bool(ce.has_triggered(D("2024-01-02"), FakeView(measures={"dv01": 1.0}, n=1))) for _ in range(6)]
    assert got == [True, False, False, True, False, False]


def test_strategy_risk_nonfinite_measure_raises():
    with pytest.raises(TriggerError):
        StrategyRiskTriggerRequirements("dv01", 0, ABOVE).has_triggered(D("2024-01-02"), FakeView(measures={"dv01": float("nan")}, n=1))


def test_portfolio_and_trade_count():
    v = FakeView(n=3)
    assert PortfolioTriggerRequirements("len", 3, EQUAL).has_triggered(D("2024-01-02"), v)
    assert not PortfolioTriggerRequirements("len", 0, EQUAL).has_triggered(D("2024-01-02"), v)  # gs counted date keys: this pinned "len" wrongly
    assert PortfolioTriggerRequirements("net:dv01", 5, ABOVE).has_triggered(D("2024-01-02"), FakeView(measures={"dv01": 6.0}, n=1))
    assert TradeCountTriggerRequirements(10, BELOW).has_triggered(D("2024-01-02"), FakeView(n=9))
    assert not TradeCountTriggerRequirements(10, BELOW).has_triggered(D("2024-01-02"), FakeView(n=10))
    assert TradeCountTriggerRequirements(3, ABOVE).calc_type is CalcType.path_dependent
    with pytest.raises(TriggerError):
        TradeCountTriggerRequirements(None, BELOW)


# ------------------------------------------------------------------ Aggregate / Not
def test_aggregate_evaluates_every_child_at_every_point_no_short_circuit():
    """gs short-circuited ALL_OF, so a stateful child silently skipped observations after an earlier False."""
    always_false = MktTriggerRequirements("s", 1000, ABOVE)
    crossing = CrossingTriggerRequirements("s", 4, ABOVE)
    agg = AggregateTriggerRequirements([always_false, crossing], AggType.ALL_OF)
    fires = [bool(agg.has_triggered(D("2024-01-02"), FakeView(x=x))) for x in SERIES]
    assert not any(fires)
    assert crossing._armed is not None and crossing._armed is False  # it saw the whole series (last value 5 > 4 breached and disarmed)
    a2 = AggregateTriggerRequirements([MktTriggerRequirements("s", 4, ABOVE), MktTriggerRequirements("s", 6, BELOW)], AggType.ALL_OF)
    o2 = AggregateTriggerRequirements([MktTriggerRequirements("s", 4, ABOVE), MktTriggerRequirements("s", 6, BELOW)], AggType.ANY_OF)
    assert [bool(a2.has_triggered(D("2024-01-02"), FakeView(x=x))) for x in (3, 5, 7)] == [False, True, False]
    assert [bool(o2.has_triggered(D("2024-01-02"), FakeView(x=x))) for x in (3, 5, 7)] == [True, True, True]


def test_aggregate_calc_type_stateful_times_and_merged_info():
    ctx = mk_ctx("2024-01-02", "2024-02-29")
    per = PeriodicTriggerRequirements(dt.date(2024, 1, 2), None, "1w")
    mr = MeanReversionTriggerRequirements("s", 2.0, 5, 5)
    agg = AggregateTriggerRequirements([per, MktTriggerRequirements("s", 1, ABOVE), TradeCountTriggerRequirements(3, BELOW)])
    assert agg.calc_type is CalcType.path_dependent
    assert not AggregateTriggerRequirements([per, mr]).calc_type is CalcType.path_dependent and AggregateTriggerRequirements([per, mr]).stateful
    agg2 = AggregateTriggerRequirements([per, MktTriggerRequirements("s", 1, ABOVE)])
    agg2.bind(ctx)
    assert list(agg2.get_trigger_times()) == list(per.get_trigger_times())  # union of the children's times
    info = agg2.has_triggered(D("2024-01-09"), FakeView(x=5.0))
    assert info.triggered and info.info["*"].next_schedule == D("2024-01-16")


def test_aggregate_rejects_empty_and_ignores_nested_trigger_actions_with_warning(caplog):
    with pytest.raises(StrategyError):
        AggregateTriggerRequirements([])
    with caplog.at_level(logging.WARNING):
        agg = AggregateTriggerRequirements([PeriodicTrigger(frequency="1w", actions=AddTradeAction(fwd(), name="inner"))])
    assert "ignored" in caplog.text and isinstance(agg.triggers[0], PeriodicTriggerRequirements)


def test_not_trigger_inverts_and_propagates_child_properties():
    child = StrategyRiskTriggerRequirements("dv01", 0, ABOVE)
    n = NotTriggerRequirements(child)
    assert n.calc_type is CalcType.path_dependent  # gs: always simple
    assert n.has_triggered(D("2024-01-02"), FakeView(measures={"dv01": -1.0}, n=1)).triggered
    assert not n.has_triggered(D("2024-01-02"), FakeView(measures={"dv01": 1.0}, n=1)).triggered
    assert n.requires_measures() == ("dv01",)
    ns = NotTriggerRequirements(PeriodicTriggerRequirements(None, None, "1w"))
    ns.bind(mk_ctx("2024-01-02", "2024-02-01"))
    assert not ns.has_triggered(D("2024-01-02"), None).triggered and ns.has_triggered(D("2024-01-03"), None).info == {}


# ------------------------------------------------------------------ MeanReversion
def mr_run(values, req, days=None):
    days = days or pd.bdate_range("2024-01-02", periods=len(values))
    src = SeriesSource(pd.Series(values, index=days), "fill_forward", name="s")
    store = SignalStore([src])
    v = FakeView(store=store)
    out = []
    for d in days:
        t = pd.Timestamp(f"{d.date()} 17:00", tz=NY)
        store.observe(t, v)
        info = req.has_triggered(t, v)
        out.append(info.info["add_trade"].scaling if info.triggered else None)
    return out, src


def test_mean_reversion_flat_spike_revert_gives_short_then_offset():
    """Ported gs scenario T5: window mean 106 at the exit, entry z = +inf (flat window)."""
    vals = [100.0] * 5 + [130.0, 100.0]
    out, _ = mr_run(vals, MeanReversionTriggerRequirements("s", 2.0, 5, 5))
    assert out == [None] * 5 + [-1.0, 1.0]


def test_mean_reversion_long_entry_below_then_exit_above_mean():
    vals = [100.0] * 5 + [70.0, 90.0, 120.0]
    req = MeanReversionTriggerRequirements("s", 2.0, 5, 5)
    out, _ = mr_run(vals, req)
    assert out[:6] == [None] * 5 + [1.0]
    assert out[6] is None  # 90 is below the window mean: still long
    assert out[7] == -1.0 and req.current_position == 0  # 120 > mean: offsetting sell, back to flat


def test_mean_reversion_warmup_no_fire_no_state_change_and_reset():
    req = MeanReversionTriggerRequirements("s", 1.0, 5, 5)
    out, _ = mr_run([100.0, 100.0, 130.0, 100.0, 100.0], req)  # 2 prior observations at the spike: warming up
    assert out == [None] * 5 and req.current_position == 0
    out, _ = mr_run([100.0] * 5 + [130.0], req)
    assert out[-1] == -1.0 and req.current_position == -1
    req.reset()
    assert req.current_position == 0 and req.position == 0


def test_mean_reversion_quiet_series_never_fires_and_allow_short_false():
    quiet = [100 + 0.01 * (i % 3) for i in range(30)]
    assert set(mr_run(quiet, MeanReversionTriggerRequirements("s", 2.0, 5, 5))[0]) == {None}
    vals = [100.0] * 5 + [130.0]
    assert mr_run(vals, MeanReversionTriggerRequirements("s", 2.0, 5, 5, allow_short=False))[0][-1] is None


def test_mean_reversion_close_mode_marks_exit_key_and_skips_the_add_on_exit():
    req = MeanReversionTriggerRequirements("s", 2.0, 5, 5, exit_mode="close")
    days = pd.bdate_range("2024-01-02", periods=7)
    src = SeriesSource(pd.Series([100.0] * 5 + [130.0, 100.0], index=days), "fill_forward", name="s")
    store = SignalStore([src])
    v = FakeView(store=store)
    infos = []
    for d in days:
        t = pd.Timestamp(f"{d.date()} 17:00", tz=NY)
        store.observe(t, v)
        infos.append(req.has_triggered(t, v))
    enter, leave = infos[5], infos[6]
    assert enter.info["exit"].skip and not enter.info["add_trade"].skip  # the ENTRY firing must not close anything
    assert leave.info["add_trade"].skip and not leave.info["exit"].skip
    assert not enter.strict


def test_mean_reversion_requires_signal_lookback_and_validation():
    req = MeanReversionTriggerRequirements("s", 2.0, 30, 20)
    assert req.requires_signals() == ()  # a name string is resolved by the store, not declared here
    src = SeriesSource(pd.Series([1.0], index=[dt.date(2024, 1, 2)]))
    assert MeanReversionTriggerRequirements(src, 2.0, 30, 20).requires_signals()[0].lookback == 30
    with pytest.raises(TriggerError):
        MeanReversionTriggerRequirements("s", 2.0, 1, 5)
    with pytest.raises(TriggerError):
        MeanReversionTriggerRequirements("s", None, 5, 5)


# ------------------------------------------------------------------ Event / Custom
def test_event_trigger_offsets_roll_and_schedule():
    ctx = mk_ctx("2024-01-02", "2024-06-28")
    cal = EventCalendar({"CPI": [dt.date(2024, 2, 13), dt.date(2024, 3, 12), dt.date(2025, 1, 1)]})
    r = bound(EventTriggerRequirements("CPI", 0, events=cal), ctx)
    assert list(r.get_trigger_times()) == [D("2024-02-13"), D("2024-03-12")]  # the 2025 event is outside the window
    assert r.has_triggered(D("2024-02-13")).info["*"].next_schedule == D("2024-03-12")
    off = bound(EventTriggerRequirements("CPI", 3, events={"CPI": [dt.date(2024, 2, 13)]}, roll="following"), ctx)
    assert list(off.get_trigger_times()) == [D("2024-02-16")]
    sat = bound(EventTriggerRequirements("CPI", 4, events={"CPI": [dt.date(2024, 2, 13)]}, roll="following"), ctx)
    assert list(sat.get_trigger_times()) == [D("2024-02-19")]  # 02-17 is a Saturday
    with pytest.raises(StrategyError):
        EventTriggerRequirements("CPI")
    with pytest.raises(StrategyError):
        EventTriggerRequirements("CPI", events={"CPI": []}, data_source=cal)
    with pytest.raises(TriggerError):
        bound(EventTriggerRequirements("NFP", events=cal), ctx)


def custom_fn(ts, view, level=0):
    return view.x > level


def test_custom_trigger_callable_and_dotted_path_and_declarations():
    c = CustomTriggerRequirements(custom_fn, {"level": 5}, times=[dt.date(2024, 1, 3)], calc="path_dependent", is_stateful=True, needs_measures=("dv01",))
    assert c.has_triggered(D("2024-01-02"), FakeView(x=6)).triggered and not c.has_triggered(D("2024-01-02"), FakeView(x=4)).triggered
    assert c.calc_type is CalcType.path_dependent and c.stateful and c.requires_measures() == ("dv01",) and list(c.get_trigger_times()) == [dt.date(2024, 1, 3)]
    d = CustomTriggerRequirements("pricebt.strategy.requirements:check_barrier", None)
    assert d._fn is check_barrier
    with pytest.raises(Exception):
        CustomTriggerRequirements("os:getcwd")  # not under an allowed module prefix


# ------------------------------------------------------------------ RiskBand
def rb_run(req, xs, ctx=None):
    if ctx is not None:
        req.bind(ctx)
    out = []
    for i, x in enumerate(xs):
        info = req.has_triggered(D("2024-01-02") + pd.Timedelta(days=i), FakeView(measures={"dv01": x}, n=1))
        out.append(info)
    return out


def test_risk_band_symmetric_band_target_default_and_info():
    infos = rb_run(RiskBandTriggerRequirements("dv01", band=25.0), [0.0, 24.9, 25.1, -30.0])
    assert [bool(i) for i in infos] == [False, False, True, True]
    h = infos[2].info["hedge"]
    assert h.target == 0.0 and h.data["scope"] == "portfolio" and h.data["band"] == (-25.0, 25.0) and "outside" in h.reason and not infos[2].strict


def test_risk_band_pair_band_hedge_to_edge_and_rebalance_emission():
    infos = rb_run(RiskBandTriggerRequirements("dv01", band=(-10.0, 30.0), hedge_to="edge"), [40.0, -20.0])
    assert [i.info["hedge"].target for i in infos] == [30.0, -10.0]
    mid = rb_run(RiskBandTriggerRequirements("dv01", band=(-10.0, 30.0)), [40.0])[0].info["hedge"].target
    assert mid == 10.0  # the midpoint is the default target of an absolute band
    r = rb_run(RebalanceTriggerRequirements("dv01", band=50.0, target=5.0), [80.0])[0]
    assert "rebalance" in r.info and r.info["rebalance"].target == 5.0
    assert rb_run(RiskBandTriggerRequirements("dv01", band={"abs": 5.0}), [6.0])[0]
    assert rb_run(RiskBandTriggerRequirements("dv01", band={"lo": -1.0, "hi": 1.0}), [2.0])[0]


def test_risk_band_rearm_latch_truth_table():
    """Schmitt latch: after a firing stay disarmed until |x - target| <= rearm."""
    req = RiskBandTriggerRequirements("dv01", band=25.0, rearm=10.0)
    fired = [bool(i) for i in rb_run(req, [30.0, 40.0, 20.0, 11.0, 30.0, 5.0, 26.0])]
    assert fired == [True, False, False, False, False, False, True]  # re-armed only at 5.0 (<= 10), then 26 breaches again


def test_risk_band_cooldown_in_points_and_durations_and_initial_state():
    fired = [bool(i) for i in rb_run(RiskBandTriggerRequirements("dv01", band=1.0, cooldown=3), [5.0] * 8)]
    assert fired == [True, False, False, True, False, False, True, False]
    ctx = mk_ctx("2024-01-02", "2024-03-29")
    req = RiskBandTriggerRequirements("dv01", band=1.0, cooldown="1w")
    req.bind(ctx)
    fired = []
    for t in (D("2024-01-02"), D("2024-01-05"), D("2024-01-09"), D("2024-01-10")):
        fired.append(bool(req.has_triggered(t, FakeView(measures={"dv01": 5.0}, n=1))))
    assert fired == [True, False, True, False]  # 1w from 01-02 is 01-09 (allowed again exactly then)
    assert rb_run(RiskBandTriggerRequirements("dv01", band=1.0, rearm=0.5), [5.0])[0]  # armed at reset: a trigger starting outside the band fires


def test_risk_band_flat_book_reset_and_validation():
    assert not RiskBandTriggerRequirements("dv01", band=1.0).has_triggered(D("2024-01-02"), FakeView(measures={"dv01": 9.0}, n=0))
    req = RiskBandTriggerRequirements("dv01", band=1.0, cooldown=100)
    rb_run(req, [5.0])
    req.reset()
    assert rb_run(req, [5.0])[0]
    for kw in ({"band": None}, {"rearm": 99.0}, {"hedge_to": "x"}, {"emit": "z"}, {"cooldown": -1}, {"transform": "abs", "hedge_to": "edge"}, {"band": (2.0, 1.0)}):
        with pytest.raises(TriggerError):
            RiskBandTriggerRequirements("dv01", **{"band": 1.0, **kw})
    assert RiskBandTriggerRequirements("gamma", band=1.0).requires_measures() == ("gamma",)
    assert RiskBandTriggerRequirements("gamma", band=1.0).calc_type is CalcType.path_dependent
    with pytest.raises(TriggerError):
        RiskBandTriggerRequirements("dv01", band=1.0).has_triggered(D("2024-01-02"), FakeView(measures={"dv01": float("inf")}, n=1))
