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

EXAMPLE = ROOT / "skills" / "pricebt-strategy-workflow" / "example" / "toy_momentum_spec.yaml"


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
