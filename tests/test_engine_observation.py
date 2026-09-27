"""Observation-only switches must not change what the run DOES (doubt cycle 2, findings C1-C4, H1, H6, M11): turning on `baseline`, `audit` or `record_signals` may add rows and columns, but
never a trade, a fill, a cash figure, whether the run completes, or the flush dates of the library layers. Each case runs the same strategy with the switch off and on."""
from dataclasses import dataclass

import pandas as pd
import pytest

from pricebt.engine import Engine, EngineSettings
from pricebt.errors import MarketDataUnavailable
from pricebt.market import MarketData
from pricebt.orders import CloseOrder, OpenOrder
from pricebt.results import BacktestResult
from pricebt.strategy.signals import BookMeasureSignal, SignalStore
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyForward, ToyFuture, ToyMDP
from pricebt.timeutil import Clock, TimeContext, TimeGrid
from toy_helpers import bind_method, toy_tpl

pytestmark = pytest.mark.core

D = lambda s: pd.Timestamp(f"{s} 17:00", tz="America/New_York")  # noqa: E731


def engine(strategy, *, start="2024-01-02", end="2024-02-29", mdp=None, signals=None, **settings):
    grid = TimeGrid.daily(start, end, "1b", TimeContext())
    settings.setdefault("show_progress", False)
    return Engine(grid, MarketData({"primary": mdp or ToyMDP()}, Clock()), strategy, EngineSettings(**settings), signals=signals)


def same_run(a, b):
    """Every number the run produced except what the switch adds."""
    cols = ["equity", "cash", "tcost", "positions_value", "n_positions", "step_pnl"]
    pd.testing.assert_frame_equal(a.equity[cols], b.equity[cols])
    keep = ["ts", "position", "kind", "quantity", "pv", "cash", "tcost", "template"]
    pd.testing.assert_frame_equal(a.trades[keep].reset_index(drop=True), b.trades[keep].reset_index(drop=True))
    assert len(a.errors) == len(b.errors)


# ------------------------------------------------------------------ C1: the baseline never drops a trade
def test_c1_an_instrument_without_the_baseline_measures_still_trades_under_on_error_record():
    fut = toy_tpl(name="fut", target=ToyFuture, kwargs={"multiplier": 1.0})  # no `rate` bound

    def run(**kw):
        strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fut, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
        return engine(strat, on_error="record", **kw).run()

    off, on = run(), run(baseline=True)
    assert len(off.trades) == len(on.trades) == 2 and len(on.positions) == 1
    assert on.equity["equity"].iloc[-1] == off.equity["equity"].iloc[-1]
    assert list(on.errors["where"]).count("baseline") == 1, "the missing measure is reported once (not swallowed, not once per flush)"
    assert not [n for n in on.layers_by_position["layer"].unique() if n.startswith("tay_")], "and that position simply has no baseline rows"


def test_c1_the_same_setup_does_not_fail_the_run_under_on_error_raise_either():
    """CHANGED in doubt cycle 3 (E2; it pinned fail-fast): `attribution.baseline` never changes whether a run completes, so under the default on_error=raise the missing measure is
    ONE recorded error and the run goes on. (The config loader still refuses such an instrument at load, CFG-BASELINE; tests/test_engine_cycle3.py covers every on_error mode.)"""
    fut = toy_tpl(name="fut", target=ToyFuture, kwargs={"multiplier": 1.0})
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fut, quantity=3.0)]})
    rec = engine(strat, baseline=True).run()
    assert len(rec.positions) == 1 and list(rec.errors["where"]) == ["baseline"] and "rate" in rec.errors["error"].iloc[0]


# ------------------------------------------------------------------ C2: an audit measure that fails never aborts, half-books or changes the run
@dataclass
class FlakyForward(ToyForward):
    boom: str = "value"

    def flaky_gamma(self, ctx):
        if self.boom == "value":
            raise ValueError("library failed to bump")
        raise MarketDataUnavailable(ctx.ts, {}, "extra snapshot missing")


@pytest.mark.parametrize("boom", ["value", "mdu"], ids=["a-raw-library-error", "a-pricebt-data-error"])
def test_c2_a_failing_audit_measure_leaves_the_run_and_the_bookkeeping_untouched_under_record(boom):
    t = toy_tpl(name="fwd", target=FlakyForward, kwargs={"strike": 4.0, "notional": 10.0, "boom": boom}, bind={"gamma": bind_method("flaky_gamma")})

    def run(**kw):
        strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(t, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
        return engine(strat, on_error="record", **kw).run()

    off, on = run(), run(audit=True, audit_measures=("gamma",))
    assert len(on.trades) == len(off.trades) == 2 and (on.positions["status"] == "closed").all()
    assert BacktestResult(on).reconcile().ok
    assert on.equity["equity"].iloc[-1] == off.equity["equity"].iloc[-1]
    assert on.audit["marks"]["measure_gamma"].isna().all(), "the cell is NaN, and the failure is not a trade or a position"


# ------------------------------------------------------------------ C3: record_signals reads the book without changing what a strategy sees
def _cash_gated(with_signal):
    fwd = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 50.0})
    hedge = toy_tpl(name="hedge", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0})
    state = {"done": False}

    def fn(ts, view, submit):
        if not state["done"] and view.cash > 20.0:  # 'sweep cash into a hedge once it exceeds 20'
            state["done"] = True
            submit(OpenOrder(hedge, quantity=1.0))

    strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd, quantity=3.0)]}, fn=fn)
    store = SignalStore([BookMeasureSignal("dv01", name="book_dv01")]) if with_signal else None
    extra = dict(record_signals=("book_dv01",)) if with_signal else {}
    return engine(strat, end="2024-01-31", signals=store, **extra).run()


def test_c3_record_signals_does_not_move_when_a_cash_gated_strategy_acts():
    a, b = _cash_gated(False), _cash_gated(True)
    assert a.trades.query("template == 'hedge'")["ts"].tolist() == b.trades.query("template == 'hedge'")["ts"].tolist()
    same_run(a, b)


def test_c3_the_strategy_sees_the_same_cash_at_every_point_with_or_without_the_signal():
    seen = {False: [], True: []}
    for flag in (False, True):
        fwd = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 50.0})
        strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd, quantity=3.0)]}, fn=lambda ts, view, submit, flag=flag: seen[flag].append((ts, view.cash)))
        store = SignalStore([BookMeasureSignal("dv01", name="book_dv01")]) if flag else None
        engine(strat, end="2024-01-31", signals=store, **(dict(record_signals=("book_dv01",)) if flag else {})).run()
    assert seen[False] == seen[True] and any(c != 0.0 for _, c in seen[True])


# ------------------------------------------------------------------ C4: a data gap under a book signal stays a recorded error
def test_c4_a_market_data_gap_under_a_book_signal_is_recorded_not_fatal_when_on_error_is_record():
    def run(with_signal):
        fwd = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0})
        strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd, quantity=3.0)]})
        store = SignalStore([BookMeasureSignal("dv01", name="book_dv01")]) if with_signal else None
        extra = dict(record_signals=("book_dv01",)) if with_signal else {}
        return engine(strat, end="2024-01-31", mdp=ToyMDP(fail_at=[D("2024-01-16")]), on_error="record", signals=store, **extra).run()

    off, on = run(False), run(True)
    assert len(on.equity) == len(off.equity) and len(off.errors) >= 1
    assert set(on.errors["where"]) <= {"mark", "signals"}


# ------------------------------------------------------------------ H1: one audit row per (ts, position), whatever the fill lag
@pytest.mark.parametrize("lag,fill_price", [(0, "decision"), (1, "decision"), (2, "decision"), (1, "next")])
def test_h1_audit_marks_are_unique_per_timestamp_and_position(lag, fill_price):
    fwd = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0})
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
    rec = engine(strat, audit=True, audit_measures=("rate",), fill_lag=lag, fill_price=fill_price).run()
    m = rec.audit["marks"]
    assert not m.duplicated(["ts", "position"]).any(), m[m.duplicated(["ts", "position"], keep=False)]
    first = m.groupby("position")["ts"].min()
    assert (first.values == rec.trades.query("kind == 'open'")["ts"].values).all(), "a position's first audited mark is at its booking point"
    off = engine(ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd, quantity=3.0)], D("2024-02-07"): [CloseOrder()]}), fill_lag=lag, fill_price=fill_price).run()
    same_run(off, rec)


# ------------------------------------------------------------------ H6: the baseline does not move the library layers' flush dates
def test_h6_library_layers_flush_on_the_same_dates_with_and_without_the_baseline_under_every_n():
    def run(**kw):
        fwd = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 0.5}, layers=("carry", "delta"))
        strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
        return engine(strat, audit=True, layers=("carry", "delta"), cadence="every_n:5", **kw).run()

    a, b = run(), run(baseline=True)
    fa = a.audit["layers"].query("layer == 'carry'")[["ts", "amount"]].reset_index(drop=True)
    fb = b.audit["layers"].query("layer == 'carry'")[["ts", "amount"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(fa, fb)
    assert len(fa) >= 4


# ------------------------------------------------------------------ M11: nothing survives from one run of an engine to the next
def test_m11_a_second_run_of_the_same_engine_reports_its_own_counters_and_rewraps():
    from pricebt.market import Binding

    fwd = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0})
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
    wrapped = []
    grid = TimeGrid.daily("2024-01-02", "2024-02-29", "1b", TimeContext())
    class DigestMDP(ToyMDP):  # the same content digest at the same time in every run
        def get_pricer(self, ts, request=None):
            p = super().get_pricer(ts, request)
            object.__setattr__(p, "digest", f"toy-{pd.Timestamp(ts).value}")
            return p

    eng = Engine(grid, MarketData({"primary": Binding(DigestMDP(), {}, lambda p: wrapped.append(p) or p)}, Clock()), strat, EngineSettings(show_progress=False))
    r1, n1 = eng.run(), len(wrapped)
    r2, n2 = eng.run(), len(wrapped) - n1
    assert n1 > 0 and n2 == n1, "the second run wraps its own snapshots again: nothing is left over from the first"
    assert r1.manifest["fetches"] == r2.manifest["fetches"] and r1.manifest["memo_hits"] == r2.manifest["memo_hits"], (r1.manifest["fetches"], r2.manifest["fetches"])


# ------------------------------------------------------------------ M5: the wrapped pricer keeps the stamp of the request it answers
def test_m5_two_requests_for_one_snapshot_at_different_times_do_not_share_a_wrapped_pricer():
    from types import SimpleNamespace

    class Snap:
        digest = "one-digest-for-both"

        def __init__(self, ts):
            self.ts = ts

    class Provider:
        def get_pricer(self, ts, request):
            return Snap(ts)  # the same content (digest) at every request time, stamped with the request

    market = MarketData({"primary": Provider()}, Clock())
    market.bindings["primary"] = type(market.bindings["primary"])(Provider(), {}, lambda p: SimpleNamespace(ts=p.ts, inner=p))
    market.clock.advance(D("2024-01-10"))
    a = market.pricer(D("2024-01-09"))
    b = market.pricer(D("2024-01-10"))
    assert a.ts == D("2024-01-09") and b.ts == D("2024-01-10"), "a wrapped pricer answers the request it was made for"
    assert market.pricer(D("2024-01-09")) is a and market.n_wraps == 2, "and the same request is still served from the memo"


# ------------------------------------------------------------------ the peek: a strategy that asks for a measure after a signal was observed still marks (only a signal's own view peeks), and no measure is computed twice
# (E5, doubt cycle 3: "no measure is computed twice" holds while `value` leaves the position's state alone; when it writes the state the peeked number would differ from the marked one, so the
# reuse is dropped: tests/test_engine_cycle3.py)
@dataclass
class CountingForward(ToyForward):
    calls = []

    def dv01(self, ctx=None):
        CountingForward.calls.append(ctx.ts)
        return super().dv01(ctx) if ctx is not None else super().dv01()


def test_a_measure_requested_by_the_strategy_after_a_signal_marks_exactly_as_it_does_without_the_signal():
    seen = {}
    for flag in (False, True):
        fwd = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 50.0})
        log = []

        def fn(ts, view, submit, log=log):
            view.measure("dv01")  # the strategy's own request marks the positions now, as ever
            log.append((ts, view.cash))

        strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd, quantity=3.0)]}, fn=fn)
        store = SignalStore([BookMeasureSignal("dv01", name="book_dv01")]) if flag else None  # registered and observed, not recorded
        engine(strat, end="2024-01-31", signals=store).run()
        seen[flag] = log
    assert seen[False] == seen[True] and any(c != 0.0 for _, c in seen[True])


def test_a_signal_peek_and_the_mark_of_the_same_point_compute_a_bound_measure_once():
    """Kept (E5): true for an instrument whose `value` leaves `ctx.state` untouched, as here; a `value` that writes the state drops the reuse (tests/test_engine_cycle3.py)."""
    CountingForward.calls.clear()
    fwd = toy_tpl(name="fwd", target=CountingForward, kwargs={"strike": 4.0, "notional": 10.0})
    strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd, quantity=3.0)]})
    store = SignalStore([BookMeasureSignal("dv01", name="book_dv01")])
    rec = engine(strat, end="2024-01-31", signals=store, record_signals=("book_dv01",), measures=("dv01",)).run()
    held_points = int((rec.equity["n_positions"] > 0).sum())
    assert held_points >= 10
    per_point = pd.Series(CountingForward.calls).value_counts()
    assert per_point.max() == 1, "the signal, the recorded measure and the mark share one evaluation per point"


# ------------------------------------------------------------------ a recorded measure that is NOT BOUND is 0; one that FAILS is an error (agent A's finding: a typo in a binding was recorded as 0)
@dataclass
class BrokenDv01Forward(ToyForward):
    def dv01(self, ctx=None):
        raise TypeError("the library rejected the call")


def _recorded_dv01(target, **settings):
    fwd = toy_tpl(name="fwd", target=target, kwargs={"strike": 4.0, "notional": 10.0})
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
    return engine(strat, measures=("dv01",), **settings).run()


def test_a_bound_measure_that_fails_is_not_recorded_as_zero():
    with pytest.raises(Exception, match="dv01|rejected"):
        _recorded_dv01(BrokenDv01Forward)
    rec = _recorded_dv01(BrokenDv01Forward, on_error="record")
    assert len(rec.errors) >= 1 and "dv01" in " ".join(rec.errors["error"]), "under on_error=record the failure is an ErrorRecord, not a silent zero"


def test_a_measure_the_instrument_does_not_bind_is_still_zero_in_the_recorded_column():
    fut = toy_tpl(name="fut", target=ToyFuture, kwargs={"multiplier": 1.0})  # the toy future binds no `rate`
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fut, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
    rec = engine(strat, measures=("rate",)).run()
    assert (rec.equity["measure_rate"] == 0.0).all() and len(rec.errors) == 0
