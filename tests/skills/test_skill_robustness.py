"""skills/pricebt-adversarial-review/scripts/robustness.py on toy specs, plus non-vacuity for the
look-ahead (truncation) comparison."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-adversarial-review" / "scripts"))
import robustness  # noqa: E402

TOY = "tests/assets/toy_usd_irs.yaml"


def _spec(archetype, **extra):
    s = {
        "name": f"toy-{archetype}".replace("_", "-"), "archetype": archetype, "assets": [TOY],
        "dates": {"start": dt.date(2024, 1, 2), "end": dt.date(2025, 1, 2), "in_sample_end": dt.date(2024, 9, 30)},
        "costs": {"model": "dv01_bp", "level": 0.25},
    }
    s.update(extra)
    return s


@pytest.fixture(scope="module")
def momentum_results():
    # module scope runs outside conftest's per-test `isolation`, so restore the session here
    from pricebt.session import PricebtSession
    prev = PricebtSession.current
    try:
        return {r.name: r for r in robustness.run_all(_spec("momentum", signal={"type": "rate_momentum", "lookback": 20}))}
    finally:
        PricebtSession.current = prev


def test_every_experiment_reports(momentum_results):
    assert set(momentum_results) == set(robustness.EXPERIMENTS)
    assert all(r.status in ("PASS", "WARN", "FAIL", "N/A") for r in momentum_results.values())


def test_cost_ladder_is_monotone_in_costs(momentum_results):
    t = momentum_results["cost_ladder"].table
    assert list(t["multiplier"]) == [0, 1, 2, 3]
    assert t["total_pnl"].is_monotonic_decreasing and t["total_pnl"].iloc[0] > t["total_pnl"].iloc[-1]


def test_truncation_passes_for_a_causal_strategy(momentum_results):
    assert momentum_results["truncation"].status == "PASS"


def test_signal_experiments_run_for_signal_archetypes(momentum_results):
    assert momentum_results["signal_shift"].table is not None
    assert len(momentum_results["parameter_sweep"].table) >= 4
    t = momentum_results["is_oos"].table
    assert list(t["sample"]) == ["in", "out"] and t["days"].sum() > 200


def test_non_signal_archetype_marks_signal_experiments_na():
    res = {r.name: r for r in robustness.run_all(_spec("periodic_roll"), experiments=["parameter_sweep", "signal_shift", "sub_periods"])}
    assert res["parameter_sweep"].status == "N/A" and res["signal_shift"].status == "N/A"
    assert res["sub_periods"].table is not None and len(res["sub_periods"].table) == 3


def test_costs_off_is_flagged():
    res = robustness.run_all(_spec("periodic_roll", costs={"model": "none", "level": 0}), experiments=["cost_ladder"])
    assert res[0].status == "WARN" and "costs are off" in res[0].detail


class _FakeBT:
    def __init__(self, ledger):
        self._ledger = ledger

    def trade_ledger(self):
        return self._ledger


def test_truncation_fails_when_earlier_trades_change(monkeypatch):
    """Non-vacuity: if removing later data changes an earlier trade (look-ahead), the check FAILs."""
    base = _FakeBT(pd.DataFrame({"Open": [dt.date(2024, 3, 1)], "Open Value": [-100.0]}, index=["A_2024-03-01"]))
    leaky = _FakeBT(pd.DataFrame({"Open": [dt.date(2024, 3, 1)], "Open Value": [-250.0]}, index=["A_2024-03-01"]))
    monkeypatch.setattr(robustness, "_run", lambda spec: leaky)
    spec = {"dates": {"start": dt.date(2024, 1, 2), "end": dt.date(2025, 1, 2)}}
    assert robustness.truncation(spec, base).status == "FAIL"
    monkeypatch.setattr(robustness, "_run", lambda spec: base)
    assert robustness.truncation(spec, base).status == "PASS"


def test_a_crashing_experiment_becomes_a_fail_row(monkeypatch):
    monkeypatch.setitem(robustness.EXPERIMENTS, "sub_periods", lambda s, b: 1 / 0)
    res = robustness.run_all(_spec("periodic_roll"), experiments=["sub_periods"])
    assert res[0].status == "FAIL" and "ZeroDivisionError" in res[0].detail
