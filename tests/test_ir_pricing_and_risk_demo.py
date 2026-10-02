"""Runs the IR pricing-and-risk demo notebook's source (`notebooks/src/ir_pricing_and_risk_toy.py`)
as a plain script on the toy assets and checks what it shows (core tier; about 2 s). The built
notebook itself is not executed here (the slow `notebook` test covers only 040304).
"""
from __future__ import annotations

import runpy
from pathlib import Path

import numpy as np
import pytest

import pricebt.backtests.actions as _actions
from pricebt.risk import IRDeltaParallel, Price
from pricebt.session import GsSession, PricebtSession

SRC = Path(__file__).resolve().parents[1] / "notebooks" / "src" / "ir_pricing_and_risk_toy.py"


@pytest.fixture(scope="module")
def ns():
    # module-scoped, so bracket the run with conftest's `isolation` save/restore (see test_040304_toy.py)
    saved = (PricebtSession.current, GsSession.current, _actions.action_count)
    try:
        yield runpy.run_path(str(SRC), run_name="__main__")
    finally:
        PricebtSession.current, GsSession.current, _actions.action_count = saved


def test_contract_and_paste_ready_block(ns):
    # 19 base rows + IR_STRICT_CONTRACT R3-1's 8 + ProbabilityOfExercise
    assert list(ns["contract"].measure)[-1] == "ProbabilityOfExercise" and len(ns["contract"]) == 28
    skeleton = ns["skeleton"]
    assert "unsupported_measures" not in skeleton and "risk_measures:" in skeleton
    assert all(m in skeleton for m in ("IRVanna", "IRVega"))


def test_portfolio_pricing_paths_and_aggregates(ns):
    scalars, book = ns["scalars"], ns["book"]
    frame = scalars.to_frame()
    assert frame.shape == (3, 6)
    assert [str(p) for p in book.all_paths] == ["(0,)", "(1, 0)", "(1, 1)"]
    assert list(ns["linear"].to_frame().index) == ["swap", "bond"]
    assert float(scalars[IRDeltaParallel].aggregate()) == pytest.approx(sum(float(scalars[i][IRDeltaParallel]) for i in book.all_instruments))
    assert float(scalars[ns["bond"]][IRDeltaParallel]) < 0 < float(scalars[ns["swaption"]][IRDeltaParallel])
    assert list(ns["ladder"].mkt_point) == ["2Y", "5Y", "10Y", "30Y"]
    assert ns["cube"].mkt_type.eq("IR VOL").all() and "10Y;1Y" in set(ns["cube"].mkt_point)
    assert ns["flows"].payment_amount.iloc[0] == pytest.approx(21250.0)
    assert len(ns["history"].aggregate()) == 5


def test_hedged_swaption_and_bond_explain(ns):
    table, summary = ns["table"], ns["residual_summary"]
    assert np.isfinite(table.to_numpy(dtype=float)).all()
    assert summary["residual / actual"] < 0.01
    bond = ns["bond_table"]
    coupon_steps = bond[bond.cashflow_pnl != 0]
    assert list(coupon_steps.cashflow_pnl) == [pytest.approx(21250.0)]
    assert (bond.residual_pnl.abs() < 5.0).all()


def test_pnl_explain_between_two_dates(ns):
    rows = ns["explain_rows"]
    assert {"IR", "IR VOL", "CROSSES"} <= set(rows.mkt_type)
    moved = float(ns["moved"].aggregate()) - float(ns["explained"][Price].aggregate())
    assert rows.value.sum() == pytest.approx(moved, rel=1e-9, abs=1e-6)  # full revaluation, no time
