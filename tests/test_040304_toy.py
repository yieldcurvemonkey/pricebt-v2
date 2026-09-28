"""Runs the toy 040304 mean-reversion notebook's source (`notebooks/src/040304_mean_reversion_toy.py`)
as a plain script and checks the frame shapes against research/05-notebook-coverage.md section 6.5
(IMPLEMENTATION_PLAN.md P4.3; DESIGN.md section 10, Appendix B). The toy rates world is a
deterministic closed-form sine curve (tests/toylib/rates.py), so the mean-reversion trigger firing
at least once over the chosen date range is a reproducible fact, not a flaky one.
"""
from __future__ import annotations

import re
import runpy
from datetime import date
from pathlib import Path

import pytest

import pricebt.backtests.actions as _actions
from pricebt.risk import Price
from pricebt.session import GsSession, PricebtSession

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "notebooks" / "src" / "040304_mean_reversion_toy.py"

_LEDGER_COLUMNS = ["Open", "Close", "Open Value", "Close Value", "Long Short", "Status", "Trade PnL"]
_NAME_RE = re.compile(r"^Action\d+_swap_10y_\d{4}-\d{2}-\d{2}$")


@pytest.fixture(scope="module")
def notebook_ns():
    # Like every test in this suite (IMPLEMENTATION_PLAN.md section 0.3), this assumes the process
    # cwd is the repo root, which is how the asset config's relative path
    # ("tests/assets/toy_usd_irs.yaml") resolves. Deliberately NOT `monkeypatch.chdir(ROOT)` here:
    # os.chdir() before matplotlib's Tk backend initialises breaks Tcl's `tcl_findLibrary` search
    # (observed on this machine) -- an unrelated, environment-specific footgun not worth chasing
    # for a test that already runs from the right directory under the project's own convention.
    #
    # module-scoped (not the pytest default of function-scoped): running the script re-runs the
    # WHOLE thing, including a real backtest and the `.plot()` call at the bottom, which builds a
    # Tk canvas. Nothing in this file mutates the returned namespace, so 3 separate executions per
    # process bought nothing but redoing the same backtest 3x -- and repeated in-process Tk
    # initialisation is flaky on this machine (`_tkinter.TclError` on some fraction of runs). One
    # execution shared by every test in the module fixes the waste and the flakiness together.
    #
    # A module-scoped fixture runs its setup OUTSIDE every individual test's function-scoped
    # `isolation` autouse fixture window (conftest.py): `isolation` saves/restores per TEST, but
    # this fixture's own setup (and, critically, its total absence of a teardown before this fix)
    # ran once, mutated PricebtSession.current/GsSession.current/actions.action_count, and never
    # put them back -- so every test file that pytest collects AFTER this one (alphabetically,
    # `test_040304_toy.py` sorts first) saw a polluted session and a bumped counter. Bracket the
    # run with the same save/restore `isolation` does, at module scope, so nothing leaks past this
    # module's own tests.
    prev_pricebt_session = PricebtSession.current
    prev_gs_session = GsSession.current
    prev_action_count = _actions.action_count
    try:
        yield runpy.run_path(str(SRC), run_name="__main__")
    finally:
        PricebtSession.current = prev_pricebt_session
        GsSession.current = prev_gs_session
        _actions.action_count = prev_action_count


def test_par_rate_series_shape(notebook_ns):
    s = notebook_ns["s"]
    assert s.attrs["unit"] == "bp"
    assert s.attrs["missing_dates"] == []  # the toy world has no holes by default
    assert len(s) > 0


def test_ledger_shape_and_row_invariants(notebook_ns):
    backtest = notebook_ns["backtest"]
    ledger = backtest.trade_ledger()
    assert list(ledger.columns) == _LEDGER_COLUMNS
    # deterministic toy world: the par-rate series swings well past the z-score bound over the
    # chosen year (research/05 section 6.4/6.5), so the trigger must fire at least once.
    assert len(ledger) > 0, "expected at least one mean-reversion trigger over the notebook's date range"
    assert list(ledger.index) == sorted(ledger.index)  # research/05 section 6.5: "rows are sorted by name"
    start_date, end_date = notebook_ns["start_date"], notebook_ns["end_date"]
    for name, row in ledger.iterrows():
        assert _NAME_RE.match(name), name
        assert isinstance(row["Open"], date)
        assert start_date <= row["Open"] <= end_date
        assert row["Close"] is None  # research/05 section 6.5: both legs of the trade stay open forever
        assert row["Close Value"] == 0
        assert row["Open Value"] == pytest.approx(0.0, abs=1e-6)  # the ATM entry: par strike, PV is float noise
        assert row["Long Short"] == -1  # research/05 section 6.5: the direction of the opening payment
        assert row["Status"] == "open"
        assert row["Trade PnL"] is None


def test_result_summary_shape_and_total_identity(notebook_ns):
    backtest = notebook_ns["backtest"]
    rs = backtest.result_summary
    assert list(rs.columns) == [Price, "Cumulative Cash", "Transaction Costs", "Total"]
    assert len(rs) > 0
    assert list(rs.index) == sorted(rs.index)
    # DESIGN.md section 8.3: Total = price_measure + Cumulative Cash + Transaction Costs.
    expected_total = rs[Price] + rs["Cumulative Cash"] + rs["Transaction Costs"]
    assert (rs["Total"] - expected_total).abs().max() < 1e-9
