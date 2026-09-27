from dataclasses import replace

import numpy as np
import pytest

from conftest import make_engine
from pricebt.orders import CloseOrder, OpenOrder, PositionMeta, PositionSelector
from toy_helpers import toy_tpl
from pricebt.results import BacktestResult, ReconcileError
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyFuture, ToyMDP, ToyOption
from test_results_common import D, forward_run, fwd, make_grid, rich_run, run, zero

pytestmark = pytest.mark.core


def hedged_run(**settings):
    expiry = D("2024-02-28")
    opt = toy_tpl(name="opt", target=ToyOption, kwargs={"strike": 4.0, "expiry": expiry, "notional": 1e4})
    fut = toy_tpl(name="fut", target=ToyFuture, kwargs={"multiplier": 1.0})

    def hedge(ts, view, submit):
        if view.n_positions("action:opt") == 0:
            return
        (h,) = view.positions("action:hedge") or (None,)
        if h is not None:
            submit(CloseOrder(PositionSelector(ids=(h.id,))))
        unit = view.measure_of(fut, "dv01")
        submit(OpenOrder(fut, quantity=-view.measure("dv01", "action:opt") / unit, meta=PositionMeta(action="hedge", kind="hedge")))

    return run({D("2024-01-10"): [OpenOrder(opt, meta=PositionMeta(action="opt"))]}, fn=hedge, **settings)


HONEST = {
    "forward": lambda: forward_run(),
    "forward_lag1": lambda: forward_run(fill_lag=1),
    "rich": lambda: rich_run(),
    "rich_capital": lambda: rich_run(initial_capital=1e6),
    "hedged": lambda: hedged_run(),
    "open_close_lag1": lambda: run({D("2024-01-10"): [OpenOrder(fwd(notional=3.0))], D("2024-01-19"): [CloseOrder()]}, fill_lag=1),
    "zero_financing": lambda: run({D("2024-01-10"): [OpenOrder(zero(layers=("roll", "delta")), quantity=5.0)]}, cadence="eod"),
    "flat": lambda: BacktestResult(make_engine(make_grid(), ScriptedStrategy())[0].run()),
    "on_error_record": lambda: run({D("2024-01-10"): [OpenOrder(fwd())]}, mdp=ToyMDP(fail_at=[D("2024-01-16")]), on_error="record"),
}


@pytest.mark.parametrize("name", list(HONEST))
def test_honest_runs_reconcile_cleanly(name):
    rep = HONEST[name]().reconcile()
    assert rep.ok, rep.to_frame()
    assert all(c.passed for c in rep.checks if c.severity == "error")
    assert rep.max_abs_error < 1e-6
    HONEST[name]().reconcile(strict=True)


def test_lag1_resize_layers_close_to_ledger():
    rep = rich_run(fill_lag=1, cadence="each").reconcile()
    assert {c.name for c in rep.failed()} <= {"layers"}
    assert rep.ok


def mutate(res, **edits):
    """A copy of the result with frames edited by ``edits[frame] = fn(frame_copy) -> None`` (pure on the original)."""
    rec = res.record
    frames = {}
    for name in ("equity", "positions", "trades", "orders", "errors", "events", "layers_by_position"):
        df = getattr(rec, name).copy()
        if name in edits:
            edits[name](df)
        frames[name] = df
    return BacktestResult(replace(rec, **frames))


def failed(res, **edits):
    return {c.name for c in mutate(res, **edits).reconcile().failed()}


def test_corrupted_cash_column_is_caught():
    """The mutation test the design demands: an honest run passes, the same run with one cash cell bumped fails."""
    res = rich_run()
    assert res.reconcile().ok
    bad = mutate(res, equity=lambda e: e.iloc.__setitem__((5, e.columns.get_loc("cash")), e["cash"].iloc[5] + 1000.0))
    rep = bad.reconcile()
    assert not rep.ok
    assert {"identity", "cash_rollforward"} <= {c.name for c in rep.failed()}
    assert rep["identity"].first_violation == res.equity.index[5] and rep["identity"].max_abs_error == pytest.approx(1000.0)
    assert rep["identity"].n_violations == 1
    with pytest.raises(ReconcileError) as exc:
        bad.reconcile(strict=True)
    assert exc.value.report is not None and "identity" in str(exc.value)


def bump(col, row, delta):
    def f(df):
        df.iloc[row, df.columns.get_loc(col)] += delta

    return f


CORRUPTIONS = {
    "positions_value_mid": (dict(equity=bump("positions_value", 4, 50.0)), {"identity"}),
    "positions_value_last": (dict(equity=bump("positions_value", -1, 1.0)), {"identity", "positions_value"}),
    "tcost_last": (dict(equity=bump("tcost", -1, 3.0)), {"identity", "tcost_rollforward"}),
    "step_pnl": (dict(equity=bump("step_pnl", 7, 0.25)), {"step_pnl"}),
    "interest": (dict(equity=bump("interest_cum", 9, 0.5)), {"cash_rollforward"}),
    "flows": (dict(equity=bump("flows_cum", 12, 0.5)), {"cash_rollforward"}),
    "n_positions": (dict(equity=bump("n_positions", 3, 1)), {"n_positions"}),
    "trade_quantity": (dict(trades=bump("quantity", 0, 5.0)), {"positions_trades"}),
    "trade_cash": (dict(trades=bump("cash", 0, 1.0)), {"cash_rollforward", "positions_trades"}),
    "trade_tcost": (dict(trades=bump("tcost", 1, 1.0)), {"tcost_rollforward", "positions_trades"}),
    "position_pnl": (dict(positions=bump("pnl", 0, 1.0)), {"pnl_sum"}),
    "position_tcost": (dict(positions=bump("tcost", 0, 1.0)), {"positions_trades"}),
    "layer_row": (dict(layers_by_position=bump("pnl", 0, 0.5)), {"layers"}),
    "equity_layer_col": (dict(equity=bump("layer_delta", -1, 0.5)), {"layers"}),
}


@pytest.mark.parametrize("name", list(CORRUPTIONS))
def test_each_corruption_trips_the_intended_checks(name):
    edits, expected = CORRUPTIONS[name]
    got = failed(rich_run(cadence="each"), **edits)
    assert expected <= got, (name, got)


def test_nan_and_index_problems_are_caught():
    res = rich_run()
    nan = mutate(res, equity=lambda e: e.iloc.__setitem__((3, e.columns.get_loc("cash")), np.nan))
    assert {"finite", "identity"} <= {c.name for c in nan.reconcile().failed()}
    shifted = mutate(res, equity=lambda e: e.iloc.__setitem__((3, e.columns.get_loc("equity")), -5.0))
    assert "identity" in {c.name for c in shifted.reconcile().failed()}


def test_tolerance_scales_with_book_size():
    res = rich_run(initial_capital=1e6)
    tiny = mutate(res, equity=bump("cash", 5, 1e-6))  # 1e-12 relative on a 1e6 book: noise
    assert tiny.reconcile().ok
    big = mutate(res, equity=bump("cash", 5, 1.0))
    assert not big.reconcile().ok
    assert not mutate(res, equity=bump("cash", 5, 1e-6)).reconcile(tol=0.0, atol=1e-9).ok  # tolerances are parameters


def test_report_frame_and_lookup():
    rep = rich_run().reconcile()
    frame = rep.to_frame()
    assert list(frame.columns)[:3] == ["passed", "severity", "max_abs_error"] and frame.index.name == "check"
    assert rep["layers"].passed and rep.ok
    with pytest.raises(KeyError):
        rep["nope"]


def test_layer_checks_skipped_gracefully_without_layers():
    res = run({D("2024-01-10"): [OpenOrder(fwd(), final_ts=D("2024-02-01"))]})
    rep = res.reconcile()
    assert rep["layers"].passed and "no layers" in rep["layers"].detail


def test_layer_failure_when_layer_row_dropped_while_equity_column_kept():
    res = rich_run(cadence="each")
    drop = mutate(res, layers_by_position=lambda l: l.drop(l.index[l["layer"] == "delta"], inplace=True))
    assert "layers" in {c.name for c in drop.reconcile().failed()}
