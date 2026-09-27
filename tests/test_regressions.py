"""Regression tests for defects found by the real-data suites and fixed by the coordinator (each was a reproducible bug)."""
import datetime as dt

import pandas as pd
import pytest

from conftest import assert_identity, make_engine
from pricebt.config import yamlio
from pricebt.config.loader import build
from pricebt.errors import MeasureError
from pricebt.orders import CloseOrder, OpenOrder, PositionMeta
from toy_helpers import bind_method, toy_tpl
from pricebt.pricer import SnapshotIndex, TimeMapping
from pricebt.strategy import AddTradeAction, ExitAllPositionsAction, PeriodicTrigger, Strategy
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyForward, ToyMDP
from pricebt.timeutil import TimeContext, TimeGrid

pytestmark = pytest.mark.core

NY = "America/New_York"
D = lambda s: pd.Timestamp(f"{s} 17:00", tz=NY)

BASE = """
name: reg
backtest: {tz: America/New_York, grid: {start: 2024-01-02, end: 2024-03-29, freq: 1b}, progress: {show: false}}
market: {mdps: {rates: {type: toy}}, pricers: {primary: {mdp: rates}}}
instruments: {fwd: {asset_class: toy, factory: "pricebt.testing.toys:forward", conventions: {strike: 4.0, notional: 10.0}}}
"""


def test_scaled_cost_scaling_type_is_a_plain_string_in_yaml():
    cfg = yamlio.loads(BASE + """
strategy:
  triggers:
    - type: periodic
      frequency: 1m
      actions: [{type: add_trade, priceables: fwd, trade_duration: 1m, transaction_cost: {type: scaled, scaling_type: notional, scaling_level: 0.001}}]
""")
    res = build(cfg).run()
    assert res.reconcile().ok and res.equity["tcost"].iloc[-1] < 0  # cost booked: the string was NOT coerced to the action enum


def test_action_scaling_type_still_coerced_to_enum():
    cfg = yamlio.loads(BASE + """
strategy:
  triggers:
    - type: periodic
      frequency: 1m
      actions: [{type: add_scaled_trade, priceables: fwd, trade_duration: 1m, scaling_type: size, scaling_level: 2.0}]
""")
    assert build(cfg).run().reconcile().ok


def test_time_of_day_strings_in_trigger_config():
    cfg = yamlio.loads("""
name: reg
backtest: {tz: America/New_York, grid: {start: 2024-01-02 09:00, end: 2024-01-02 12:00, freq: 1h}, progress: {show: false}}
market: {mdps: {rates: {type: toy}}, pricers: {primary: {mdp: rates}}}
instruments: {fwd: {asset_class: toy, factory: "pricebt.testing.toys:forward", conventions: {strike: 4.0, notional: 10.0}}}
strategy:
  triggers:
    - {type: intraday_periodic, start_time: "09:00", end_time: "11:00", frequency: 60, actions: [{type: add_trade, priceables: fwd, trade_duration: 30min}]}
""")
    assert len(build(cfg).run().trades) >= 2


def test_derived_signal_with_named_inputs_in_yaml():
    cfg = yamlio.loads(BASE + """
signals:
  lvl: {type: pricer, lookup: level, kwargs: {name: rate}}
  lvl_z: {type: derived, op: zscore, inputs: [lvl], window: 5}
  z: {type: derived, op: clip, inputs: [lvl_z], lo: -10, hi: 10}
strategy:
  triggers:
    - {type: mkt, data_source: z, trigger_level: 1.0, direction: ABOVE, on_missing: skip, actions: [{type: add_trade, priceables: fwd, trade_duration: 5b}]}
""")
    assert len(build(cfg).run().trades) > 0


def test_on_error_record_also_covers_recorded_measures(grid):
    bad = D("2024-01-16")
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(toy_tpl(name="f", target=ToyForward, kwargs={"strike": 4.0}))]})
    eng, _, _ = make_engine(grid, strat, mdp=ToyMDP(fail_at=[bad]), on_error="record", measures=("dv01",))
    rec = eng.run()  # must not raise although the measure column needs a mark at the failing point
    assert len(rec.errors) >= 1 and "measure_dv01" in rec.equity.columns


def test_lagged_close_does_not_double_count_the_accrual_interval():
    grid = TimeGrid.daily("2024-01-02", "2024-03-29", "1b", TimeContext())
    carry = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 100.0, "carry_bp_per_day": 10.0})
    strat = Strategy(None, [PeriodicTrigger(frequency="1m", actions=[ExitAllPositionsAction(name="close"), AddTradeAction(carry, None, name="open")])])
    r0 = strat.backtest(grid, ToyMDP(), fill_lag=0)
    r1 = strat.backtest(grid, ToyMDP(), fill_lag=1)
    assert r1.equity["flows_cum"].iloc[-1] == pytest.approx(r0.equity["flows_cum"].iloc[-1], abs=1e-9)
    assert r1.equity["equity"].iloc[-1] == pytest.approx(r0.equity["equity"].iloc[-1], abs=1e-9)
    assert_identity(r1)


def test_exit_policy_next_grid_snaps_off_grid_exit_to_data_points(grid):
    off = pd.Timestamp("2024-01-20 12:00", tz=NY)  # Saturday, not a grid point
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(toy_tpl(name="f", target=ToyForward, kwargs={"strike": 4.0}), final_ts=off)]})
    rec = make_engine(grid, strat, exit_policy="next_grid")[0].run()
    assert len(rec.equity) == len(grid)  # no extra timeline point
    assert rec.positions.iloc[0]["exit_ts"] == D("2024-01-22")  # next base-grid point at/after the requested time


def test_fill_price_next_reprices_at_the_booking_point(grid):
    t1 = D("2024-01-10")
    nxt = grid.points[list(grid.points).index(t1) + 1]
    strat = ScriptedStrategy({t1: [OpenOrder(toy_tpl(name="f", target=ToyForward, kwargs={"strike": 4.0}))]})
    eng, _, mdp = make_engine(grid, strat, fill_lag=1, fill_price="next")
    rec = eng.run()
    assert rec.positions.iloc[0]["entry_ts"] == nxt
    assert rec.positions.iloc[0]["entry_pv"] == pytest.approx(mdp.get_pricer(nxt).rate - 4.0)
    assert_identity(rec)


def test_measure_missing_zero_only_for_recording(grid):
    class NoDv01:
        def value(self, ctx):
            return 1.0

    seen = {}

    def fn(ts, view, submit):
        if ts == D("2024-01-11"):
            with pytest.raises(MeasureError):
                view.measure("dv01")
            seen["zero"] = view.measure("dv01", missing="zero")

    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(toy_tpl(name="n", target=NoDv01, bind={"value": bind_method("value")})), OpenOrder(toy_tpl(name="f", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0}))]}, fn=fn)
    make_engine(grid, strat)[0].run()
    assert seen["zero"] == pytest.approx(0.1)  # only the forward contributes; NoDv01 counted as zero


def test_snapshot_index_handles_microsecond_stamps_and_eod_visibility():
    us = pd.DatetimeIndex(["2024-03-01 21:00", "2024-03-04 21:00"], tz="UTC").as_unit("us")
    idx = SnapshotIndex(us)
    assert idx.select(pd.Timestamp("2024-03-01 17:00", tz=NY), TimeMapping("asof", pd.Timedelta(hours=12))) == 0  # us units must not be misread as ns
    # eod_visible_at delays a row in EVERY mode and never exposes it early
    m = TimeMapping("asof", pd.Timedelta(days=1), eod_visible_at=dt.time(17, 30))
    assert idx.select(pd.Timestamp("2024-03-01 16:30", tz=NY), m) is None  # stamp is 16:00 NY: visible only from 17:30
    assert idx.select(pd.Timestamp("2024-03-01 17:45", tz=NY), m) == 0
    early = TimeMapping("asof", pd.Timedelta(days=1), eod_visible_at=dt.time(9, 0))
    assert idx.select(pd.Timestamp("2024-03-01 15:59", tz=NY), early) is None  # a visibility time before the stamp cannot expose it early


def test_core_cash_accrual_counts_wall_clock_days_across_a_daylight_saving_change():
    """A book on a daily grid accrues 3 days over every Friday-to-Monday weekend, including the two on which the clocks change (elapsed seconds gave 71 and 73 hours)."""
    from pricebt.accrual import ConstantCashAccrual, wall_days

    ny = "America/New_York"
    accrual = ConstantCashAccrual(0.05, 365.0, "simple")
    plain = accrual.interest(1e9, pd.Timestamp("2024-06-07 17:00", tz=ny), pd.Timestamp("2024-06-10 17:00", tz=ny))
    for fri, mon in (("2024-03-08", "2024-03-11"), ("2024-11-01", "2024-11-04")):  # spring-forward and fall-back weekends
        got = accrual.interest(1e9, pd.Timestamp(f"{fri} 17:00", tz=ny), pd.Timestamp(f"{mon} 17:00", tz=ny))
        assert got == pytest.approx(plain, rel=1e-12), (fri, got, plain)
    assert wall_days(pd.Timestamp("2024-03-10 01:00", tz=ny), pd.Timestamp("2024-03-10 04:00", tz=ny)) == pytest.approx(3 / 24), "an intraday step stays proportional to the wall clock"
    assert wall_days(pd.Timestamp("2024-01-02 12:00"), pd.Timestamp("2024-01-05 12:00")) == 3.0, "naive stamps work"
