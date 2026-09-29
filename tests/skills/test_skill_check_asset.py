"""skills/pricebt-verify-asset-config/scripts/check_asset.py: passes the toy configs, and FAILs the
specific check on each deliberately broken fixture (non-vacuity), in-process and via the CLI."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from pricebt.assets import yamlio

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills" / "pricebt-verify-asset-config" / "scripts" / "check_asset.py"
FIXTURES = REPO / "tests" / "skills" / "fixtures" / "check_asset"
ASSETS = REPO / "tests" / "assets"
sys.path.insert(0, str(SCRIPT.parent))

import check_asset  # noqa: E402

DATES = [date(2024, 1, 3), date(2024, 2, 5)]

BROKEN = [
    ("bad_dv01_sign", "swap_dv01_sign"),
    ("bad_par_rate_decimal", "swap_par_rate_unit"),
    ("bad_unpinned_maturity", "resolve_pins_terms"),
    ("bad_weekend_raises", "market_weekend"),
]


def _status(results, name):
    return {r.name: r.status for r in results}[name]


@pytest.mark.parametrize("config", ["toy_usd_irs.yaml", "toy_eur_irs.yaml"])
def test_toy_configs_pass(config):
    results = check_asset.run_checks(ASSETS / config, fx=ASSETS / "toy_fx.yaml", dates=DATES)
    fails = [r for r in results if r.status == check_asset.FAIL]
    assert not fails, fails
    names = {r.name for r in results}
    assert {"smoke_backtest", "swap_dv01_sign", "swap_pnl_explain", "performance"} <= names
    assert _status(results, "swap_pnl_explain") == check_asset.PASS  # rates moved enough to be a real test


@pytest.mark.parametrize("fixture,check", BROKEN)
def test_broken_fixture_fails_its_check(fixture, check):
    results = check_asset.run_checks(FIXTURES / f"{fixture}.yaml", dates=DATES, sys_path=[FIXTURES])
    assert _status(results, check) == check_asset.FAIL


def _toy():
    return yamlio.load_file(ASSETS / "toy_usd_irs.yaml")  # pricebt's loader: reads 1.0e6 as a float


def _set(cfg, path, value):
    *head, last = path
    node = cfg
    for k in head:
        node = node[k]
    node[last] = value
    return cfg


MUTATIONS = [
    # (config path, new value, check row, expected status)
    (("imports",), "import toylib.no_such_module as tr\n", "imports_execute", "FAIL"),
    (("market", "expr"), 'tr.market(pricebt_date, "GBP", pricebt_csa)', "market_available", "FAIL"),
    (("match",), {"notional_currency": "EUR"}, "match", "WARN"),
    (("functions", "npv", "scale_with_quantity"), False, "quantity_scaling[Price]", "FAIL"),
    # an UNMAPPED copy of par_rate: IRFwdRate's own function must keep a rate unit or the config no
    # longer loads (measure contract, docs/v2/IR_RISK_DESIGN.md section 2)
    (("functions", "par_rate_raw"), {"expr": "tr.par_rate(market, trade)", "unit": "ccy"}, "notional_linearity[par_rate_raw]", "FAIL"),
    (("functions", "dv01", "expr"), "tr.pv01(market, trade) * 100", "swap_dv01_band", "FAIL"),
    (("functions", "npv", "expr"), "-tr.npv(market, trade)", "swap_pnl_explain", "FAIL"),
    (("functions", "npv", "expr"), "float('nan')", "smoke_backtest", "FAIL"),
    (("market", "expr"), 'tr.market(tr._EPOCH, "USD", pricebt_csa)', "measure_series", "WARN"),
    (("risk_measures", "IRDeltaTypo"), "dv01", "risk_measures[IRDeltaTypo]", "WARN"),
    # known answers for the pinning scan: a convention tenor is fine; an ISO-string date is not a date
    (("resolve", "expr"), "{**tr.resolve_swap(market, kwargs), 'fixed_rate_frequency': '6m'}", "resolve_pins_terms", "PASS"),
    (("resolve", "expr"), "{**tr.resolve_swap(market, kwargs), 'maturity_date': '2034-01-03'}", "resolve_pins_terms", "FAIL"),
]


@pytest.mark.parametrize("path,value,check,expected", MUTATIONS, ids=[m[2] + "-" + m[3] for m in MUTATIONS])
def test_each_check_catches_a_mistake(path, value, check, expected):
    results = check_asset.run_checks(_set(copy.deepcopy(_toy()), path, value), dates=DATES)
    assert _status(results, check) == expected, [r for r in results if r.name == check]


def _cli(*args, tmp_path):
    out = tmp_path / "out.json"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(["src", "tests"])}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--date", "2024-01-03", "--date", "2024-02-05", "--json", str(out)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
    )
    return proc, {r["name"]: r["status"] for r in json.loads(out.read_text())}


def test_cli_passes_toy(tmp_path):
    proc, statuses = _cli("tests/assets/toy_usd_irs.yaml", tmp_path=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "| check | status | detail |" in proc.stdout
    assert "FAIL" not in statuses.values()


def test_cli_fails_broken_fixture_via_sys_path(tmp_path):
    proc, statuses = _cli(
        "tests/skills/fixtures/check_asset/bad_unpinned_maturity.yaml",
        "--sys-path", "tests/skills/fixtures/check_asset", "--no-backtest", tmp_path=tmp_path,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert statuses["resolve_pins_terms"] == "FAIL"
    assert "smoke_backtest" not in statuses


# cross-skill: the connect skill's worked example passes, and each of its documented mistakes FAILs
MERIDIAN = REPO / "skills" / "pricebt-connect-pricing-library" / "example"
MERIDIAN_DATES = [date(2024, 1, 2), date(2024, 4, 2)]


@pytest.mark.parametrize("config,check", [
    ("meridian_usd_irs.yaml", None),
    ("mistakes/dv01_sign_not_flipped.yaml", "swap_dv01_sign"),
    ("mistakes/par_rate_in_percent.yaml", "swap_par_rate_atm"),
    ("mistakes/maturity_not_pinned.yaml", "resolve_pins_terms"),
])
def test_meridian_example_and_mistakes(config, check):
    results = check_asset.run_checks(MERIDIAN / config, dates=MERIDIAN_DATES, sys_path=[MERIDIAN], backtest=check is None)
    fails = [r.name for r in results if r.status == check_asset.FAIL]
    assert (check in fails) if check else not fails, fails
