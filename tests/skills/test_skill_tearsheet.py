"""skills/pricebt-tearsheet-report/scripts/tearsheet.py: the report and its artefacts are written,
metrics are finite and consistent, success criteria render, and output is deterministic apart from
the generated-at line."""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.backtest_objects import ScaledTransactionModel
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.instrument import IRSwap
from pricebt.risk import IRDeltaParallel, Price
from pricebt.session import PricebtSession

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-tearsheet-report" / "scripts"))
import tearsheet  # noqa: E402
sys.path.insert(0, str(ROOT / "skills" / "pricebt-strategy-recipes" / "scripts"))
import swap_pnl  # noqa: E402

START, END = date(2024, 1, 2), date(2024, 6, 28)
HEADINGS = ["Verdict", "Strategy spec", "Key metrics", "Charts", "Trade ledger", "Risk and P&amp;L in bp",
            "P&amp;L attribution", "Spot checks", "Adversarial review findings", "Caveats and known limitations",
            "Reproducibility"]
FINITE_KEYS = ["Total PnL", "Sharpe Ratio", "Max Drawdown", "Total Trades", "t-stat (mean daily PnL)", "Years",
               "Hit Rate (%)", "Average Holding Period (days)", "Trades per Year", "Cost Drag", "Max Abs Risk",
               "Mean Abs Risk", "Total PnL (bp)", "Gross PnL (before costs)"]


def _toy(pnl=False):
    """Monthly ATM-10y roll (the T-ROLL/A-ROLL shape). pnl=True adds swap_pnl_definition() plus
    CashPaidToDate, so swap_pnl.explain_table(bt)/explain_stats(table) work on the result."""
    PricebtSession.use(assets=[ROOT / "tests" / "assets" / "toy_usd_irs.yaml"])
    swap = IRSwap(pay_or_receive="Pay", termination_date="10y", notional_currency="USD", notional_amount=1e7, name="swap")
    action = AddTradeAction(swap, "1m", transaction_cost=ScaledTransactionModel(IRDeltaParallel, 0.25))
    trig = PeriodicTrigger(PeriodicTriggerRequirements(frequency="1m", end_date=END), [action])
    risks = [Price, IRDeltaParallel] + ([swap_pnl.CashPaidToDate] if pnl else [])
    pnl_def = swap_pnl.swap_pnl_definition() if pnl else None
    return GenericEngine().run_backtest(Strategy(None, trig), start=START, end=END, frequency="1b",
                                        risks=risks, pnl_explain=pnl_def, show_progress=False)


def _spec():
    spec = yaml.safe_load((ROOT / "skills/pricebt-strategy-intake/templates/strategy_spec.yaml").read_text(encoding="utf-8"))
    spec["success_criteria"] = {"min_sharpe": -1000.0, "min_oos_sharpe": None, "max_drawdown": 1.0, "min_trades": 3}
    spec["assumptions"] = ["toy world"]
    return spec


def test_toy_tearsheet_files_sections_images_metrics(tmp_path):
    bt = _toy()
    paths = tearsheet.build_tearsheet(bt, tmp_path, "Toy roll", spec=_spec(), risk=IRDeltaParallel,
                                      review_findings=[{"severity": "high", "finding": "toy world"}],
                                      spot_checks=[("ledger identity", "PASS", "ok")])
    for p in paths.values():
        assert Path(p).is_file() and Path(p).stat().st_size > 0
    page = Path(paths["html"]).read_text(encoding="utf-8")
    for h in HEADINGS:
        assert f"<h2>{h}</h2>" in page, h
    assert page.count('src="data:image/png;base64,') >= 4
    assert "coupons paid between marks are not booked" in page.lower()
    assert "P&amp;L explain not enabled for this run" in page  # pnl_table/pnl_stats default to None
    # success criteria: min_sharpe and min_trades pass, max_drawdown (1.0 currency) fails, null skipped
    assert "FAIL: 2/3 success criteria pass; missed: max_drawdown." in page
    assert '<td class="PASS">PASS</td>' in page and '<td class="FAIL">FAIL</td>' in page
    assert "min_oos_sharpe" not in page.split("<h2>Strategy spec</h2>")[0]

    data = json.loads(Path(paths["metrics"]).read_text(encoding="utf-8"))
    m = data["metrics"]
    for k in FINITE_KEYS:
        assert isinstance(m[k], (int, float)) and math.isfinite(m[k]), k
    assert m["t-stat (mean daily PnL)"] == pytest.approx(m["Sharpe Ratio"] * math.sqrt(m["Years"]))
    assert m["Cost Drag"] == pytest.approx(abs(m["Total Transaction Costs"]) / abs(m["Total PnL"] - m["Total Transaction Costs"]))
    assert m["Closed Trades"] == 5 and m["Open Trades"] == 1
    assert [c["status"] for c in data["success_criteria"]] == ["PASS", "FAIL", "PASS"]

    md = Path(paths["md"]).read_text(encoding="utf-8")
    assert "## Key metrics" in md and "![cumulative_pnl](cumulative_pnl.png)" in md
    assert (tmp_path / "cumulative_pnl.png").is_file()


def test_pnl_attribution_section_enabled(tmp_path):
    """PNL_EXPLAIN_PLAN.md section 7: pnl_table/pnl_stats given -> component-totals table (values
    compared against explain_stats directly, not a magic number), the pnl_attribution chart, and
    the residual_share/R2 line, with the WARN badge firing iff residual_share > swap_pnl.RS_TARGET
    (tearsheet.py's own target is swap_pnl.RS_TARGET, imported lazily rather than duplicated -- see
    tearsheet._swap_pnl_rs_target's docstring). (a monthly ATM-10y roll -- T-ROLL's own shape --
    should land in-target, but this asserts the live consistency rather than assuming a particular
    observed number)."""
    bt = _toy(pnl=True)
    table = swap_pnl.explain_table(bt)
    stats = swap_pnl.explain_stats(table)
    paths = tearsheet.build_tearsheet(bt, tmp_path, "Toy roll", spec=_spec(), risk=IRDeltaParallel,
                                      pnl_table=table, pnl_stats=stats)
    page = Path(paths["html"]).read_text(encoding="utf-8")

    assert "<h2>P&amp;L attribution</h2>" in page
    for component in ("PNL_delta", "PNL_gamma", "PNL_carry", "residual", "economic"):
        assert tearsheet._fmt(stats["totals"][component]) in page, component
    assert f"{stats['residual_share']:.4g}" in page and f"{stats['r2']:.4g}" in page
    fires = "** WARN: residual share above target **" in page
    assert fires == (stats["residual_share"] > swap_pnl.RS_TARGET)

    assert (tmp_path / "pnl_attribution.png").is_file()
    assert 'alt="pnl_attribution"' in page


def test_pnl_attribution_warn_badge_on_inflated_residual(tmp_path):
    """A hand-built pnl_stats with residual_share well above RS_TARGET fires the WARN badge, even
    though the underlying backtest/table is the same in-target run as the test above."""
    bt = _toy(pnl=True)
    table = swap_pnl.explain_table(bt)
    stats = swap_pnl.explain_stats(table)
    bad_stats = dict(stats, residual_share=swap_pnl.RS_TARGET * 10)  # well above target regardless of the real value

    paths = tearsheet.build_tearsheet(bt, tmp_path, "Toy roll", spec=_spec(), risk=IRDeltaParallel,
                                      pnl_table=table, pnl_stats=bad_stats)
    page = Path(paths["html"]).read_text(encoding="utf-8")
    assert "** WARN: residual share above target **" in page


def test_output_is_deterministic_apart_from_generated_at(tmp_path):
    bt = _toy()
    a = tearsheet.build_tearsheet(bt, tmp_path / "a", "Toy", spec=_spec(), risk=IRDeltaParallel)
    b = tearsheet.build_tearsheet(bt, tmp_path / "b", "Toy", spec=_spec(), risk=IRDeltaParallel)
    strip = lambda p: re.sub(r"Generated at: [^<\n]*", "", Path(p).read_text(encoding="utf-8"))  # noqa: E731
    for k in ("html", "md", "metrics", "trades", "summary"):
        assert strip(a[k]) == strip(b[k]), k


def test_evaluate_success_rules():
    m = {"Sharpe Ratio": 0.6, "Out-of-Sample Sharpe": None, "Max Drawdown": -5.0, "Total Trades": 9}
    got = tearsheet.evaluate_success(m, {"min_sharpe": 0.5, "min_oos_sharpe": 0.3, "max_drawdown": 4.0, "min_trades": 10,
                                         "other": 1})
    assert [g["status"] for g in got] == ["PASS", "N/A", "FAIL", "FAIL", "N/A"]


def test_demo_cli_mean_reversion_with_signal(tmp_path):
    paths = tearsheet.main(["--demo", str(tmp_path)])
    page = Path(paths["html"]).read_text(encoding="utf-8")
    assert (tmp_path / "signal.png").is_file() and (tmp_path / "risk.png").is_file()
    assert page.count('src="data:image/png;base64,') >= 6
    assert "ledger identity" in page and "determinism" in page  # spot checks rendered
    m = json.loads(Path(paths["metrics"]).read_text(encoding="utf-8"))["metrics"]
    assert m["Total Trades"] >= 1 and math.isfinite(m["Out-of-Sample Sharpe"]) and math.isfinite(m["In-Sample Sharpe"])


def test_attribution_section_only_when_supplied(tmp_path):
    """build_tearsheet(attribution=bt.pnl_explain_table()) adds a "P&L attribution by greek" section before
    the spot checks (totals, graded residual share, stacked chart); without it nothing changes."""
    plain = Path(tearsheet.build_tearsheet(_toy(), tmp_path / "plain", "Toy")["html"]).read_text(encoding="utf-8")
    assert "P&amp;L attribution by greek" not in plain and not (tmp_path / "plain" / "greek_attribution.png").exists()
    sys.path.insert(0, str(ROOT / "skills" / "pricebt-pnl-attribution" / "scripts"))
    import attribution

    bt = attribution.demo_backtest("swaption", end=date(2024, 2, 29))
    table = bt.pnl_explain_table()
    page = Path(tearsheet.build_tearsheet(bt, tmp_path / "att", "Toy swaption", attribution=table)["html"]).read_text(encoding="utf-8")
    assert page.index("<h2>P&amp;L attribution by greek</h2>") < page.index("<h2>Spot checks</h2>")
    assert "VegaPnL" in page and "PNL_theta" in page and '<td class="PASS">PASS</td>' in page
    assert (tmp_path / "att" / "greek_attribution.png").is_file() and "diagnosing-residuals.md" not in page
    unexplained = table.assign(residual_pnl=table["economic_pnl"])
    page = Path(tearsheet.build_tearsheet(bt, tmp_path / "bad", "Toy", attribution=unexplained)["html"]).read_text(encoding="utf-8")
    assert "FAIL: unexplained 110.31% (worst of residual variance share 100.00%" in page and "diagnosing-residuals.md" in page
