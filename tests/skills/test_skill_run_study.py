"""skills/pricebt-strategy-workflow/scripts/run_study.py: the whole automated pipeline on the example
spec (idea -> spec -> backtest -> robustness -> spot checks -> significance -> review stub -> tearsheet)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-strategy-workflow" / "scripts"))
import run_study  # noqa: E402
import spot_check  # noqa: E402 -- already on sys.path (run_study's own top-of-file loop put it there)

EXAMPLE = ROOT / "skills" / "pricebt-strategy-workflow" / "example" / "toy_momentum_spec.yaml"
PNL_EXAMPLE = ROOT / "skills" / "pricebt-strategy-workflow" / "example" / "toy_momentum_pnl_explain_spec.yaml"


def test_pipeline_writes_every_deliverable(tmp_path):
    res = run_study.run_study(EXAMPLE, out=str(tmp_path))
    for name in ("strategy_spec.yaml", "trials.csv", "robustness.md", "spot_checks.md", "significance.json",
                 "review.md", "tearsheet.html", "tearsheet.md", "metrics.json", "trades.csv"):
        assert (tmp_path / name).exists(), name
    assert res["failed"] == []
    html = (tmp_path / "tearsheet.html").read_text(encoding="utf-8")
    for heading in ("Verdict", "Strategy spec", "Key metrics", "Spot checks", "Adversarial review findings", "Caveats"):
        assert heading in html
    assert "red flag: implausible Sharpe" in html   # the toy is noise-free: the red flag must fire
    sig = json.loads((tmp_path / "significance.json").read_text(encoding="utf-8"))
    assert sig["n_trials"] == 1 and sig["years"] > 0.9
    assert "PENDING" in (tmp_path / "review.md").read_text(encoding="utf-8")
    filled = (tmp_path / "strategy_spec.yaml").read_text(encoding="utf-8")
    assert "assumptions:" in filled


# --------------------------------------------------------------------------------- pnl_explain (T3-D)
# PNL_EXPLAIN_PLAN.md section 7 / section 4's T3 gate: run_study wires swap_pnl's explain_table /
# explain_stats into the pipeline right after recipes.run(spec), gated on bt.pnl_explain_def.


def test_pnl_explain_disabled_example_is_unchanged(tmp_path):
    """Regression: the EXISTING example spec keeps pnl_explain.enabled: false (unchanged), so this
    run must produce exactly the deliverable set it always has -- no pnl_explain.csv/.json, no
    "pnl_explain"/"pnl_stats" path entries, and the tearsheet's P&L attribution section still says
    not enabled."""
    res = run_study.run_study(EXAMPLE, out=str(tmp_path), with_robustness=False)
    assert not (tmp_path / "pnl_explain.csv").exists()
    assert not (tmp_path / "pnl_explain.json").exists()
    assert "pnl_explain" not in res["paths"] and "pnl_stats" not in res["paths"]
    html = (tmp_path / "tearsheet.html").read_text(encoding="utf-8")
    assert "P&amp;L explain not enabled for this run" in html
    spots_md = (tmp_path / "spot_checks.md").read_text(encoding="utf-8")
    assert "| P&L attribution | **INFO** |" in spots_md


def test_pnl_explain_enabled_example_writes_attribution_artefacts(tmp_path):
    """The NEW enabled-true example spec: pnl_explain.csv/.json written with sane content (T1's
    explain_table/explain_stats columns/keys), a real (non-INFO) "P&L attribution" spot check, and
    a tearsheet P&L attribution section with real numbers, not the placeholder."""
    res = run_study.run_study(PNL_EXAMPLE, out=str(tmp_path), with_robustness=False)

    assert (tmp_path / "pnl_explain.csv").exists()
    assert (tmp_path / "pnl_explain.json").exists()
    assert res["paths"]["pnl_explain"] == str(tmp_path / "pnl_explain.csv")
    assert res["paths"]["pnl_stats"] == str(tmp_path / "pnl_explain.json")

    import pandas as pd
    table = pd.read_csv(tmp_path / "pnl_explain.csv", index_col=0)
    for col in ("actual_dpv", "cash", "economic", "PNL_delta", "PNL_gamma", "PNL_carry", "explained", "residual"):
        assert col in table.columns, col
    assert len(table) > 0

    stats = json.loads((tmp_path / "pnl_explain.json").read_text(encoding="utf-8"))
    for key in ("totals", "r2", "residual_share", "abs_residual_total", "abs_economic_total",
                "abs_residual_ratio", "worst_residual_date", "worst_residual"):
        assert key in stats, key

    # the spot check ran with real stats, so it must reach a real verdict (this book trades), not INFO
    spots_md = (tmp_path / "spot_checks.md").read_text(encoding="utf-8")
    line = next(l for l in spots_md.splitlines() if l.startswith("| P&L attribution |"))
    assert "**INFO**" not in line, line

    html = (tmp_path / "tearsheet.html").read_text(encoding="utf-8")
    assert "P&amp;L explain not enabled for this run" not in html
    assert "Residual share:" in html and f"{stats['r2']:.4g}" in html


def test_failed_checks_include_a_pnl_attribution_fail(tmp_path, monkeypatch):
    """run_study's exit-code logic (`failed = [r.name for r in spots if r.status == "FAIL"] + ...`)
    already covers every spot check generically -- confirm check_pnl_attribution's FAIL status
    flows through it the same way every other spot-check FAIL already does."""
    def _forced_fail(pnl_stats, target=None):
        return spot_check.CheckResult("P&L attribution", spot_check.FAIL, "forced FAIL for test")
    monkeypatch.setattr(spot_check, "check_pnl_attribution", _forced_fail)

    res = run_study.run_study(PNL_EXAMPLE, out=str(tmp_path), with_robustness=False)
    assert "P&L attribution" in res["failed"]


def test_trial_log_grows_with_each_run(tmp_path):
    run_study.run_study(EXAMPLE, out=str(tmp_path), with_robustness=False)
    run_study.run_study(EXAMPLE, out=str(tmp_path), with_robustness=False)
    rows = (tmp_path / "trials.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 3  # header + two trials
    assert json.loads((tmp_path / "significance.json").read_text(encoding="utf-8"))["n_trials"] == 2


def test_cli_rejects_an_invalid_spec(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("spec_version: 1\nname: Bad Name\narchetype: nonsense\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT / "tests")])}
    proc = subprocess.run([sys.executable, str(ROOT / "skills/pricebt-strategy-workflow/scripts/run_study.py"), str(bad), "--out", str(tmp_path / "o")],
                          capture_output=True, text=True, env=env, cwd=str(ROOT))
    assert proc.returncode == 2, proc.stdout + proc.stderr
