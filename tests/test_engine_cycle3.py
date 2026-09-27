"""Doubt cycle 3, engine findings E1-E5 (the reviewer's reproduction scripts are not shipped; each test names its finding R<n>), and the behaviour the reviewer found SOUND, pinned so it stays so.

E1  a signal only PEEKS, whichever call path evaluates it (eager `observe`, lazy `view.signal`, `signal_window`, a recorded value, an unregistered object, a signal after a failing one)
E2  `attribution.baseline` never changes whether the run completes, under every `on_error` mode; a baseline failure is one recorded error per position
E3  ... nor when a library layer flushes (the layer-cadence counter advances for library layers only, whatever else is held)
E4  (audit: tests/test_engine_audit.py)
E5  a measure a peek cached is not reused when `value` changed the position's state
PIN switches on/off equivalence over fill lag x fill price x cadence, `Engine.run()` twice, a failing signal does not leave the engine peeking
"""
import copy
import itertools
from dataclasses import dataclass

import pandas as pd
import pytest

from pricebt.engine import Engine, EngineSettings
from pricebt.errors import SignalError
from pricebt.market import MarketData
from pricebt.orders import CloseOrder, OpenOrder, ResizeOrder
from pricebt.results import BacktestResult
from pricebt.strategy.signals import BookMeasureSignal, DerivedSignal, Observation, Signal, SignalStore
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyForward, ToyFuture, ToyMDP, ToyOption
from pricebt.timeutil import Clock, TimeContext, TimeGrid
from toy_helpers import bind_method, toy_tpl

pytestmark = pytest.mark.core

D = lambda s: pd.Timestamp(f"{s} 17:00", tz="America/New_York")  # noqa: E731


def engine(strategy, *, start="2024-01-02", end="2024-02-29", signals=None, **settings):
    grid = TimeGrid.daily(start, end, "1b", TimeContext())
    settings.setdefault("show_progress", False)
    return Engine(grid, MarketData({"primary": ToyMDP()}, Clock()), strategy, EngineSettings(**settings), signals=signals)


def same_numbers(a, b):
    """The money and the trades of two runs are identical (their error tables and added columns may differ: that is what the switch under test adds)."""
    cols = ["equity", "cash", "tcost", "positions_value", "n_positions", "step_pnl"]
    pd.testing.assert_frame_equal(a.equity[cols], b.equity[cols])
    keep = ["ts", "position", "kind", "quantity", "pv", "cash", "tcost", "template"]
    pd.testing.assert_frame_equal(a.trades[keep].reset_index(drop=True), b.trades[keep].reset_index(drop=True))


def fwd(**kw):
    return toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 50.0, **kw})


# ================================================================== E1: a signal only peeks, by construction
class Boom(Signal):
    name = "boom"

    def observe(self, ts, ctx):
        raise SignalError("this signal is broken today")


class ReadsTheBook(Signal):
    """A user-written signal that reads a book measure through the view it is given (the peeking view must be the only one it can get)."""

    def observe(self, ts, ctx):
        return Observation(ts, float(ctx.view.measure("dv01")), ts)


def _ask_log(ask, signals=None, **settings):
    """Run a strategy that asks `ask(view)` at every point; per point: (ts, cash before, cash after, how many positions the ask MARKED at this point)."""
    log = []

    def fn(ts, view, submit):
        before = view.cash
        ask(view)
        log.append((ts, before, view.cash, sum(p.last_ts == ts for p in view.positions())))

    strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd(), quantity=3.0)]}, fn=fn)
    rec = engine(strat, end="2024-01-19", signals=signals, **settings).run()
    return log, rec


_ASKS = {
    "registered": (lambda reg: (lambda v: v.signal(reg)), lambda reg: SignalStore([reg])),
    "a fresh object every call": (lambda reg: (lambda v: v.signal(BookMeasureSignal("dv01"))), lambda reg: SignalStore([])),
    "a derived signal over a fresh object": (lambda reg: (lambda v: v.signal(DerivedSignal("abs", [BookMeasureSignal("dv01")]))), lambda reg: SignalStore([])),
    "a window of a fresh object": (lambda reg: (lambda v: v.signal_window(BookMeasureSignal("dv01"), 3, inclusive=True)), lambda reg: SignalStore([])),
    "a user-written signal": (lambda reg: (lambda v: v.signal(ReadsTheBook())), lambda reg: SignalStore([])),
}


@pytest.mark.parametrize("how", list(_ASKS))
def test_e1_a_signal_evaluated_lazily_from_the_strategy_step_never_books_a_mark(how):
    """R4b: the strategy asks the store for a signal it has not evaluated yet, so the store evaluates it INSIDE the strategy step: it must peek there too."""
    make_ask, make_store = _ASKS[how]
    reg = BookMeasureSignal("dv01", name="book")
    ref, _ = _ask_log(lambda v: None)
    log, _ = _ask_log(make_ask(reg), make_store(reg))
    assert [(t, a) for t, _, a, _ in log] == [(t, a) for t, _, a, _ in ref], "the strategy sees the same cash at every point as when it asks for no signal"
    assert all(b == a and m == 0 for _, b, a, m in log), "asking for a signal moved view.cash or marked a position"
    assert any(a != 0.0 for _, _, a, _ in log), "(the scenario really has cash flows to move)"


def test_e1_a_book_signal_after_a_failing_one_still_peeks_under_on_error_record():
    """R4: `observe` stops at the first signal that raises; the later book signal is then evaluated lazily by the strategy, and must not mark."""
    ref, _ = _ask_log(lambda v: None)
    store = SignalStore([Boom(), BookMeasureSignal("dv01", name="book")])
    log, rec = _ask_log(lambda v: v.signal("book"), store, on_error="record")
    assert set(rec.errors["where"]) == {"signals"} and len(rec.errors) >= 1, "the failing signal is recorded"
    assert [(t, a) for t, _, a, _ in log] == [(t, a) for t, _, a, _ in ref]
    assert all(b == a and m == 0 for _, b, a, m in log)


def test_a_failing_signal_does_not_leave_the_engine_peeking_for_the_strategys_own_requests():
    """PIN (the old `_observing` flag was reset after an exception): after a signal raised, the strategy's own `view.measure` still MARKS, exactly as with no signals at all."""
    ref, _ = _ask_log(lambda v: v.measure("dv01"))
    log, _ = _ask_log(lambda v: v.measure("dv01"), SignalStore([Boom(), BookMeasureSignal("dv01", name="book")]), on_error="record")
    assert [(t, a, m) for t, _, a, m in log] == [(t, a, m) for t, _, a, m in ref]
    assert any(m == 1 for *_, m in log) and any(b != a for _, b, a, _ in log), "the strategy's own request books the mark (cash moves) as it always did"


def test_e1_a_recorded_signal_value_is_read_without_moving_anything():
    """The recorded `signal_<name>` column is read after the marks: the same numbers as the run that records nothing."""
    def run(rec_sig):
        store = SignalStore([BookMeasureSignal("dv01", name="b")]) if rec_sig else None
        return _ask_log(lambda v: None, store, **(dict(record_signals=("b",)) if rec_sig else {}))[1]

    off, on = run(False), run(True)
    same_numbers(off, on)
    assert on.equity["signal_b"].notna().any()


# ================================================================== E2: the baseline never changes whether the run completes
@dataclass
class Flaky(ToyForward):
    """`gamma` raises a raw library error (nothing the engine wraps)."""

    def bad_gamma(self, ctx):
        raise ValueError("library failed to bump")


@dataclass
class Matures(ToyForward):
    """`rate` is NaN from `dead` on: a swap held past its maturity."""

    dead: str = "2024-01-24"

    def late_rate(self, ctx):
        return float("nan") if ctx.ts.strftime("%Y-%m-%d") >= self.dead else float(ctx.pricer.level("rate"))


def _flaky():
    return toy_tpl(name="fwd", target=Flaky, kwargs={"strike": 4.0, "notional": 10.0}, bind={"gamma": bind_method("bad_gamma")})


def _matures():
    return toy_tpl(name="fwd", target=Matures, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 0.5}, bind={"rate": bind_method("late_rate")}, layers=("carry",))


def _hold(template, **settings):
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(template, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
    return engine(strat, **settings).run()


@pytest.mark.parametrize("lag", [0, 1])
@pytest.mark.parametrize("on_error", ["raise", "skip", "record"])
def test_e2_a_raw_library_error_in_a_baseline_measure_is_recorded_once_and_the_run_completes(on_error, lag):
    """R1: baseline OFF completes (nothing else reads `gamma`); baseline ON must complete too, with the same trades and cash, whatever `on_error` says."""
    off, on = _hold(_flaky(), on_error=on_error, fill_lag=lag), _hold(_flaky(), on_error=on_error, fill_lag=lag, baseline=True)
    same_numbers(off, on)
    assert len(off.errors) == 0
    assert list(on.errors["where"]) == ["baseline"] and list(on.errors["position"]) == ["P000001"], on.errors
    assert "library failed to bump" in on.errors["error"].iloc[0]
    assert not [n for n in on.layers_by_position["layer"].unique() if n.startswith("tay_")], "that position has no baseline rows"
    assert BacktestResult(on).reconcile().ok


@pytest.mark.parametrize("on_error", ["raise", "skip", "record"])
def test_e2_a_measure_that_stops_working_mid_life_is_one_recorded_error_and_the_run_completes(on_error):
    """R2: a position held past its maturity has a NaN `rate` (a MeasureError): with the default on_error=raise the baseline used to abort the run."""
    off, on = _hold(_matures(), on_error=on_error, audit=True, layers=("carry",), cadence="each"), _hold(_matures(), on_error=on_error, audit=True, layers=("carry",), cadence="each", baseline=True)
    same_numbers(off, on)
    base_errs = on.errors[on.errors["where"] == "baseline"]
    assert len(base_errs) == 1 and list(base_errs["position"]) == ["P000001"] and "rate" in base_errs["error"].iloc[0]
    assert base_errs["ts"].iloc[0] >= D("2024-01-24"), "it is recorded when it first fails, not at entry"
    pd.testing.assert_frame_equal(off.audit["layers"].query("layer == 'carry'").reset_index(drop=True), on.audit["layers"].query("layer == 'carry'").reset_index(drop=True))
    res = BacktestResult(on)
    assert res.reconcile().ok, "the baseline rows of that position still add up to its P&L"
    lay = on.audit["layers"]
    assert lay[lay["layer"].isin(["tay_delta", "tay_convexity"])]["ts"].max() < D("2024-01-24"), "no delta/convexity is claimed for an interval the baseline could not read"


@pytest.mark.parametrize("on_error", ["raise", "skip", "record"])
def test_e2_an_instrument_without_the_baseline_measures_is_recorded_not_fatal_under_every_on_error_mode(on_error):
    """R2, the unbound case: it used to fail fast under on_error=raise (the config loader still refuses such an instrument at load, code CFG-BASELINE)."""
    fut = toy_tpl(name="fut", target=ToyFuture, kwargs={"multiplier": 1.0})  # binds no `rate`
    off, on = _hold(fut, on_error=on_error), _hold(fut, on_error=on_error, baseline=True)
    same_numbers(off, on)
    assert list(on.errors["where"]) == ["baseline"] and "rate" in on.errors["error"].iloc[0]


def test_e2_the_tieout_harness_can_run_a_config_that_holds_a_swap_past_its_maturity():
    """R2: `run_tieout` defaults to baseline=True and on_error=raise; a 1M swap held for two months aborted the whole run."""
    from test_tieout_selftest import NUMERIC, REF
    from pricebt.tieout import run_stack

    base = copy.deepcopy(NUMERIC)
    for a in base["strategy"]["triggers"][0]["actions"]:
        a["priceables"]["terms"]["maturity"] = "1M"
    base["strategy"]["triggers"][0]["actions"] = base["strategy"]["triggers"][0]["actions"][:1]
    _, off = run_stack(base, [REF], audit_measures=("dv01",), baseline=False)
    _, on = run_stack(base, [REF], audit_measures=("dv01",), baseline=True)
    assert len(on.trades) == len(off.trades) and len(on.positions) == len(off.positions) >= 3
    assert on.equity["equity"].iloc[-1] == off.equity["equity"].iloc[-1]
    errs = on.record.errors.query("where == 'baseline'")
    assert 1 <= len(errs) == errs["position"].nunique(), "one baseline error per matured position, not one per interval"


# ================================================================== E3: nor when a library layer flushes
def _two_instruments(**kw):
    opt = toy_tpl(name="opt", target=ToyOption, kwargs={"strike": 4.0, "expiry": pd.Timestamp("2024-06-28 17:00", tz="America/New_York"), "notional": 10.0})  # binds no `carry`
    fw = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 0.5}, layers=("carry",))
    strat = ScriptedStrategy({D("2024-01-03"): [OpenOrder(opt, quantity=1.0)], D("2024-01-10"): [OpenOrder(fw, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
    return engine(strat, audit=True, layers=("carry",), **kw).run()


@pytest.mark.parametrize("cadence", ["each", "eod", "every_n:2", "every_n:3", "every_n:5"])
def test_e3_a_held_instrument_with_no_library_layer_does_not_move_the_flush_dates_of_the_ones_that_have_one(cadence):
    """R3: the every_n counter advanced whenever ANY held position needed a flush; with the baseline on that is every position, so the phase differed from the run without it."""
    a, b = _two_instruments(cadence=cadence), _two_instruments(cadence=cadence, baseline=True)
    cols = ["ts", "position", "amount"]
    fa = a.audit["layers"].query("layer == 'carry'")[cols].reset_index(drop=True)
    fb = b.audit["layers"].query("layer == 'carry'")[cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(fa, fb)
    assert len(fa) >= 3
    same_numbers(a, b)


def test_e3_the_baseline_of_a_position_with_no_library_layer_still_follows_the_cadence():
    """The other half: the counter that advances for the baseline alone (no library layer held) must still coarsen the baseline's own rows."""
    def run(cadence):
        opt = toy_tpl(name="opt", target=ToyOption, kwargs={"strike": 4.0, "expiry": pd.Timestamp("2024-06-28 17:00", tz="America/New_York"), "notional": 10.0})
        strat = ScriptedStrategy({D("2024-01-03"): [OpenOrder(opt, quantity=1.0)], D("2024-02-07"): [CloseOrder()]})
        return engine(strat, audit=True, baseline=True, cadence=cadence).run().audit["layers"].query("layer == 'tay_delta'")

    each, every3 = run("each"), run("every_n:3")
    assert len(every3) < len(each) and len(every3) >= len(each) // 3


# ================================================================== E5: a cache a peek filled is not reused across a change of state
@dataclass
class Stateful(ToyForward):
    def value(self, ctx):
        ctx.state["n_marks"] = ctx.state.get("n_marks", 0) + 1  # a running fixing count / an accrual anchor
        return super().value(ctx)

    def dv01(self, ctx):
        return self.notional * 0.01 * ctx.state.get("n_marks", 0)  # a measure that reads what `value` recorded


def _seen_dv01(with_signal):
    seen = []
    tpl = toy_tpl(name="fwd", target=Stateful, kwargs={"strike": 4.0, "notional": 10.0})
    strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(tpl, quantity=1.0)]}, fn=lambda ts, view, submit: seen.append(round(view.measure("dv01"), 6)) if view.n_positions() else None)
    engine(strat, end="2024-01-19", signals=SignalStore([BookMeasureSignal("dv01", name="b")]) if with_signal else None).run()
    return seen


def test_e5_a_measure_that_reads_the_state_value_wrote_is_the_same_with_and_without_a_signal():
    """R5: the peek evaluated the measure BEFORE `value` ran and the mark reused that number, so the strategy saw a different measure when a signal was registered."""
    a, b = _seen_dv01(False), _seen_dv01(True)
    assert a == b and len(a) >= 5 and a[0] != a[1], "(and the measure really does move with the state)"


@pytest.mark.parametrize("extra", ["an array (its == is not a bool)", "an object that cannot be copied"])
def test_e5_a_state_that_cannot_be_compared_or_copied_counts_as_changed_and_never_raises(extra):
    """Whatever a position keeps in its state, the check for 'did `value` change it' must not raise: what it cannot prove unchanged it treats as changed (the measure is computed again)."""
    import threading

    import numpy as np

    @dataclass
    class Odd(Stateful):
        def value(self, ctx):
            ctx.state.setdefault("odd", np.zeros(2) if extra.startswith("an array") else threading.Lock())
            return super().value(ctx)

    def seen(with_signal):
        out = []
        tpl = toy_tpl(name="fwd", target=Odd, kwargs={"strike": 4.0, "notional": 10.0})
        strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(tpl, quantity=1.0)]}, fn=lambda ts, view, submit: out.append(round(view.measure("dv01"), 6)) if view.n_positions() else None)
        engine(strat, end="2024-01-19", signals=SignalStore([BookMeasureSignal("dv01", name="b")]) if with_signal else None).run()
        return out

    assert seen(False) == seen(True) and len(seen(True)) >= 5


def test_e5_the_peeked_measure_is_still_reused_when_value_leaves_the_state_alone():
    """The reuse (a measure is expensive: the ladder) survives wherever it is still true: `value` did not touch the state, so the peeked number is the marked one."""
    calls = []

    @dataclass
    class Counting(ToyForward):
        def dv01(self, ctx):
            calls.append(ctx.ts)
            return super().dv01(ctx)

    tpl = toy_tpl(name="fwd", target=Counting, kwargs={"strike": 4.0, "notional": 10.0})
    engine(ScriptedStrategy({D("2024-01-08"): [OpenOrder(tpl, quantity=1.0)]}), end="2024-01-19", signals=SignalStore([BookMeasureSignal("dv01", name="b")]), measures=("dv01",)).run()
    assert calls and pd.Series(calls).value_counts().max() == 1


# ================================================================== PIN: what the reviewer found sound
def _matrix_strategy():
    tpl = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 0.5}, layers=("carry", "delta"))
    return ScriptedStrategy({
        D("2024-01-10"): [OpenOrder(tpl, quantity=3.0, final_ts=D("2024-02-01"))],
        D("2024-01-12"): [ResizeOrder("P000001", 1.0), ResizeOrder("P000001", 0.5)],  # two resizes at one point
        D("2024-01-16"): [OpenOrder(tpl, quantity=2.0)],
        D("2024-01-17"): [ResizeOrder("P000002", -2.0)],  # resize to zero
        D("2024-01-22"): [OpenOrder(tpl, quantity=1.0), CloseOrder()],  # open + close at one point
        D("2024-02-05"): [CloseOrder()],
    })


@pytest.mark.parametrize("lag,fill_price,cadence", list(itertools.product((0, 1, 2), ("decision", "next"), ("each", "eod", "every_n:3"))))
def test_pin_the_observation_switches_change_no_number_and_leave_one_row_per_key(lag, fill_price, cadence):
    """R7: audit + baseline + a recorded book signal add rows and columns and change nothing else, over fill lag x fill price x cadence, with resizes, a resize to zero, and an open and a close at one point."""
    kw = dict(fill_lag=lag, fill_price=fill_price, cadence=cadence, layers=("carry", "delta"), on_error="record")
    off = engine(_matrix_strategy(), **kw).run()
    on = engine(_matrix_strategy(), audit=True, baseline=True, signals=SignalStore([BookMeasureSignal("dv01", name="b")]), record_signals=("b",), **kw).run()
    same_numbers(off, on)
    assert len(on.errors) == len(off.errors)
    m, lay = on.audit["marks"], on.audit["layers"]
    assert not m.duplicated(["ts", "position"]).any() and not lay.duplicated(["ts", "position", "layer"]).any()
    assert not on.trades.duplicated(["ts", "position", "kind", "quantity"]).any()
    audit_only = engine(_matrix_strategy(), audit=True, **kw).run()
    library = lambda r: r.audit["layers"].query("layer in ('carry', 'delta')")[["ts", "position", "layer", "amount"]].reset_index(drop=True)  # noqa: E731
    pd.testing.assert_frame_equal(library(on), library(audit_only))  # the library layers flush on the same dates, with the same amounts, whatever else is on


def _frames(rec):
    return dict(eq=rec.equity, tr=rec.trades.drop(columns=[c for c in rec.trades.columns if c.startswith("term_")]), lay=rec.layers_by_position, err=rec.errors)


@pytest.mark.parametrize("first,second", [(dict(audit=True, baseline=True, cadence="every_n:4"), dict(audit=False, baseline=False, cadence="every_n:3")),
                                          (dict(audit=False, baseline=False, cadence="every_n:3"), dict(audit=True, baseline=True, cadence="every_n:4"))])
def test_pin_a_second_run_of_an_engine_is_the_run_a_fresh_engine_gives(first, second):
    """R17: `Engine.run()` twice (the second time with other settings and a signal store that has already seen a run) equals a fresh engine with the second settings."""
    def mk(**s):
        tpl = toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 0.5}, layers=("carry", "delta"))
        strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(tpl, quantity=3.0)], D("2024-02-07"): [CloseOrder()]})
        return engine(strat, signals=SignalStore([BookMeasureSignal("dv01", name="b")]), layers=("carry", "delta"), record_signals=("b",), on_error="record", **s)

    eng = mk(**first)
    eng.run()
    eng.settings = EngineSettings(show_progress=False, layers=("carry", "delta"), record_signals=("b",), on_error="record", **second)
    again, fresh = eng.run(), mk(**second).run()
    a, b = _frames(again), _frames(fresh)
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k], obj=k)
    assert set(again.audit) == set(fresh.audit) and all(again.audit[n].equals(fresh.audit[n]) for n in again.audit)
    assert {k: again.manifest[k] for k in ("fetches", "memo_hits", "n_points", "n_positions")} == {k: fresh.manifest[k] for k in ("fetches", "memo_hits", "n_points", "n_positions")}


# ================================================================== T8 (tie-out): the manifest says which market roles each instrument reads
def test_the_manifest_records_the_market_roles_each_instrument_reads():
    """the harness labels a difference `input` only when a snapshot that differs is in a role the instrument reads; a bare `compare` learns it from here."""
    res = BacktestResult(engine(ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd(), quantity=1.0)]}), end="2024-01-31").run())
    assert res.manifest["instruments"]["fwd"]["roles"] == ["primary"]
