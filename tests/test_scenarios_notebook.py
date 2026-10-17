"""Runs the scenario notebook's source (`notebooks/src/scenarios_toy.py`) as a plain script on the toy
assets and checks what it shows (core tier). The built notebook itself is not executed here."""
from __future__ import annotations

import runpy
from pathlib import Path

import pytest

import pricebt.backtests.actions as _actions
from pricebt.session import GsSession, PricebtSession

SRC = Path(__file__).resolve().parents[1] / "notebooks" / "src" / "scenarios_toy.py"


@pytest.fixture(scope="module")
def ns():
    # module-scoped, so bracket the run with conftest's `isolation` save/restore (see test_040304_toy.py)
    import pricebt.markets as markets

    saved = (PricebtSession.current, GsSession.current, _actions.action_count, markets._DEFAULT)
    try:
        yield runpy.run_path(str(SRC), run_name="__main__")
    finally:
        PricebtSession.current, GsSession.current, _actions.action_count, markets._DEFAULT = saved


def test_shocks_move_what_they_name(ns):
    t = ns["shock_table"]
    assert t.loc["USD +50bp", "swaption"] < 0 < t.loc["USD +50bp", "swap"]  # receiver loses, payer gains
    assert t.loc["EUR +50bp"].abs().max() == 0.0
    assert t.loc["vol +10bp", "swaption"] > 0 and t.loc["vol +10bp", "swap"] == 0.0


def test_greeks_by_shocks_match_the_measures(ns):
    g = ns["greeks_by_shocks"]
    assert g.loc["delta per bp of the forward", "by shocks"] == pytest.approx(g.loc["delta per bp of the forward", "measure"], rel=1e-9)
    assert g.loc["vega per bp of vol", "by shocks"] == pytest.approx(g.loc["vega per bp of vol", "measure"], rel=1e-5)


def test_composite_order_and_curve_shapes(ns):
    assert ns["a_then_b"] == ns["composite"] != ns["b_then_a"]
    moves = ns["curve_moves"]
    assert moves["bear steepener"] > 0 > moves["bull flattener"] and moves["parallel +10"] > moves["bear steepener"]


def test_lifecycle_and_measure_overrides(ns):
    life = ns["lifecycle_table"]
    assert life.loc["44b", "OTM"] == 0.0 and life.loc["44b", "ITM"] > 0
    assert life.loc["0b", "OTM"] > life.loc["16b", "OTM"] > 0  # time value decays to expiry
    rows = ns["measure_rows"]
    assert rows["forward +25bp"]["IRFwdRate"] == pytest.approx(ns["f0"] + 25, abs=1e-8)
    assert rows["vol 100bp"]["IRAnnualImpliedVol"] == pytest.approx(100.0)


def test_terminal_payoffs_are_a_v_and_a_tent(ns):
    p = ns["payoffs"]
    assert p.loc[0, "straddle"] == pytest.approx(0.0, abs=1e-6) and p["straddle"].idxmin() == 0
    assert p["fly"].idxmax() == 0 and p.loc[-100, "fly"] == pytest.approx(0.0, abs=1e-6) and p.loc[100, "fly"] == pytest.approx(0.0, abs=1e-6)
    s = ns["stress_grid"]
    assert (s.diff(axis=1).iloc[:, 1:] > 0).all().all()  # long vol
    assert s.loc[-50, 0] > s.loc[0, 0] < s.loc[50, 0]  # long gamma
