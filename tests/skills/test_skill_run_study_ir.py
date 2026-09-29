"""run_study.py on swaption and bond specs: the P&L-explain rate series is the instrument's own rate
(IRFwdRate, converted to bp from its declared unit) instead of a swap-only `par_rate`, and a
delta-hedged straddle clears the spot-check gate (hedge rows reprice through the booked Portfolio)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-strategy-workflow" / "scripts"))
import run_study  # noqa: E402
import recipes  # noqa: E402  (on sys.path via run_study)
import spec as specmod  # noqa: E402

EXAMPLES = ROOT / "skills" / "pricebt-strategy-recipes" / "example"


@pytest.mark.parametrize("name", ["toy_swaption_expiry_roll.yaml", "toy_bond_carry_roll.yaml"])
def test_rate_series_is_the_own_rate_in_bp(name):
    spec, _ = specmod.apply_defaults(EXAMPLES / name)
    recipes.run(spec)  # loads the session the series is priced in
    s = run_study._rate_series(spec)
    assert s is not None and len(s) > 10
    assert s.attrs["unit"] == "bp" and 50 < float(s.iloc[0]) < 1500  # a rate of 0.5%..15%, in bp


def test_rate_series_converts_declared_units(monkeypatch):
    import pricebt.data

    def fake(inst, measure, *a, **k):
        s = pd.Series([0.04, 0.041])
        s.attrs["unit"] = {"IRFwdRate": "decimal"}.get(getattr(measure, "name", measure), "pct")
        return s

    monkeypatch.setattr(pricebt.data, "measure_series", fake)
    spec, _ = specmod.apply_defaults(EXAMPLES / "toy_swaption_expiry_roll.yaml")
    assert list(run_study._rate_series(spec)) == pytest.approx([400.0, 410.0])


def test_hedged_straddle_study_passes_the_spot_check_gate(tmp_path):
    res = run_study.run_study(EXAMPLES / "toy_short_straddle_delta_hedged.yaml", out=str(tmp_path), with_robustness=False)
    spots = (tmp_path / "spot_checks.md").read_text(encoding="utf-8")
    assert "| trade repricing | **PASS** |" in spots
    assert "P&L explain" in spots and "skipped: pass risk=" not in spots
    assert res["failed"] == []
