"""skills/pricebt-verify-asset-config/scripts/check_asset_ir.py, through check_asset.run_checks: the
three full-contract toy configs have no FAIL and exercise every IR row (non-vacuity: the rows PASS,
not SKIP); each broken IR fixture fails its named row; --pack forces a pack and a contract on a
ConfigInstrument copy; one in-memory mutation per remaining row makes that row report the mistake.
Kept apart from test_skill_check_asset.py so the in-flight P&L-explain branch merges cleanly (R14)."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import warnings
from datetime import date
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from pricebt.assets import yamlio

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "skills" / "pricebt-verify-asset-config" / "scripts" / "check_asset.py"
FIXTURES = REPO / "tests" / "skills" / "fixtures" / "check_asset"
ASSETS = REPO / "tests" / "assets"
sys.path.insert(0, str(SCRIPT.parent))

import check_asset  # noqa: E402

DATES = [date(2024, 1, 3), date(2024, 2, 5)]

COMMON_PASS = [
    "contract_declarations", "ir_expiry_in_years", "ir_taylor", "ir_theta", "ir_gamma_ratio", "ir_dead_levels",
    "ir_vega_cube_keys", "ir_cashflows", "fd_params", "risk_measures[PnlExplain]",
    "quantity_scaling[IRDelta bucketed]", "quantity_scaling[IRGamma bucketed]", "quantity_scaling[Cashflows frame]",
]
PACK_PASS = {
    "toy_usd_irs_full.yaml": ["swap_dv01_sign", "swap_bucket_sum", "swap_par_rate_atm", "swap_pnl_explain", "swap_annuity_sign", "measure_series"],
    "toy_usd_swaption.yaml": [
        "swaption_buy_sell_fold", "swaption_straddle", "swaption_strike_pinned", "swaption_parity", "swaption_fwd_unit", "swaption_vol_unit", "swaption_vega_sign",
        "swaption_delta_sign", "swaption_gamma_sign", "swaption_prob_exercise", "swaption_expiry", "quantity_scaling[IRVega bucketed]",
    ],
    "toy_usd_bond.yaml": [
        "ir_cashflow_drop", "bond_buy_sell_fold", "bond_size_linearity", "bond_dv01_sign", "bond_gamma_sign", "bond_lightning_dv01",
        "bond_yield_unit", "bond_price_yield", "bond_cashflows_bound", "bond_expiry",
        "measure_series",  # tracks the yield (the IRFwdRate function), not the constant 0.0 vol level
    ],
}


@lru_cache(maxsize=None)
def _toy(config):
    return tuple(check_asset.run_checks(ASSETS / config, dates=DATES))


def _rows(results):
    return {r.name: r for r in results}


@pytest.mark.parametrize("config", sorted(PACK_PASS))
def test_full_contract_toys_have_no_fail(config):
    results = _toy(config)
    assert not [r for r in results if r.status == check_asset.FAIL]
    rows = _rows(results)
    assert "smoke_backtest" in rows and "performance" in rows
    contract = [r for r in results if r.name.startswith("contract[")]
    assert contract and all(r.status == check_asset.PASS for r in contract), contract  # every measure mapped
    for name in COMMON_PASS + PACK_PASS[config]:
        assert rows[name].status == check_asset.PASS, (name, rows.get(name))


def test_ladder_sums_are_info_only():
    rows = _rows(_toy("toy_usd_bond.yaml"))
    assert rows["ir_ladder_sum[IRDelta]"].status == check_asset.INFO


BROKEN_IR = [
    # (fixture, row, status, substring of the detail)
    ("bad_swaption_sell_not_folded", "swaption_buy_sell_fold", "FAIL", "Buy vs Sell"),
    ("bad_swaption_theta_per_year", "ir_theta", "FAIL", "looks per year"),
    ("bad_bond_dv01_positive", "bond_dv01_sign", "FAIL", "IRDelta"),
    ("bad_half_gamma_swaption", "ir_gamma_ratio", "FAIL", "half-gamma"),
    ("bad_todo_reason", "contract[Theta]", "WARN", "declaration reason is a TODO"),
    # the loader refuses an extensive time level, so the row never runs: the load error names it
    ("bad_expiry_in_years_extensive", "config_loads", "FAIL", "ExpiryInYears"),
]


@pytest.mark.parametrize("fixture,row,status,text", BROKEN_IR, ids=[b[0] for b in BROKEN_IR])
def test_broken_ir_fixture_fails_its_row(fixture, row, status, text):
    rows = _rows(check_asset.run_checks(FIXTURES / f"{fixture}.yaml", dates=DATES, sys_path=[FIXTURES], backtest=False))
    assert rows[row].status == status and text in rows[row].detail, rows[row]


def test_theta_per_year_also_breaks_the_taylor_row():
    rows = _rows(check_asset.run_checks(FIXTURES / "bad_swaption_theta_per_year.yaml", dates=DATES, backtest=False))
    assert rows["ir_taylor"].status == check_asset.FAIL


def _load(config):
    return yamlio.load_file(ASSETS / config)  # pricebt's loader: reads 1.0e6 as a float


def _config_instrument_swaption():
    raw = _load("toy_usd_swaption.yaml")
    raw.update(asset="ci_swaption", instrument="ConfigInstrument")
    raw.pop("match")
    return raw


def test_pack_override_on_config_instrument():
    raw = _config_instrument_swaption()
    auto = _rows(check_asset.run_checks(copy.deepcopy(raw), dates=DATES, backtest=False))
    assert not any(n.startswith(("swaption_", "contract", "ir_")) for n in auto)
    forced = check_asset.run_checks(copy.deepcopy(raw), dates=DATES, backtest=False, pack="IRSwaption")
    rows = _rows(forced)
    assert not [r for r in forced if r.status == check_asset.FAIL]
    for name in PACK_PASS["toy_usd_swaption.yaml"] + ["contract[IRVega]", "ir_taylor", "ir_gamma_ratio"]:
        assert rows[name].status == check_asset.PASS, rows[name]


def test_pack_override_reports_a_missing_contract_measure():
    raw = _config_instrument_swaption()
    del raw["risk_measures"]["IRVolga"]  # ConfigInstrument has no contract, so this loads
    rows = _rows(check_asset.run_checks(raw, dates=DATES, backtest=False, pack="IRSwaption"))
    assert rows["contract[IRVolga]"].status == check_asset.FAIL
    assert "neither mapped nor declared" in rows["contract[IRVolga]"].detail


def test_pack_none_and_unknown():
    rows = _rows(check_asset.run_checks(ASSETS / "toy_usd_swaption.yaml", dates=DATES, backtest=False, pack="none"))
    assert not any(n.startswith(("swaption_", "contract", "ir_", "fd_params")) for n in rows)
    with pytest.raises(ValueError, match="pack"):
        check_asset.run_checks(ASSETS / "toy_usd_swaption.yaml", dates=DATES, backtest=False, pack="Swaption")


def test_cli_pack_override(tmp_path):
    cfg = tmp_path / "ci_swaption.yaml"
    cfg.write_text(yaml.safe_dump(_config_instrument_swaption(), sort_keys=False), encoding="utf-8")
    out = tmp_path / "out.json"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(["src", "tests"])}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(cfg), "--pack", "IRSwaption", "--no-backtest", "--date", "2024-01-03", "--date", "2024-02-05", "--json", str(out)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "INFO=" in proc.stdout
    statuses = {r["name"]: r["status"] for r in json.loads(out.read_text())}
    assert statuses["swaption_buy_sell_fold"] == "PASS" and statuses["contract[Price]"] == "PASS"


def _set(cfg, path, value):
    *head, last = path
    node = cfg
    for k in head:
        node = node[k]
    node[last] = value
    return cfg


_CUBE = 'ts.vega_cube(market, trades, weights, ("1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y"), ("1Y", "2Y", "5Y", "10Y", "30Y"))'
_STRADDLE_REJECTED = ('ts.resolve_swaption(market, kwargs) if str(getattr(kwargs["pay_or_receive"], "value", kwargs["pay_or_receive"])).lower() != "straddle"'
                      ' else ts.resolve_swaption(market, dict(kwargs, pay_or_receive="Pay", strike="straddle not priced here"))')

MUTATIONS = [
    # (toy config, config path, new value, row, expected status)
    ("toy_usd_swaption.yaml", ("functions", "delta", "expr"), "-ts.delta(market, trade)", "swaption_delta_sign", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "vega", "expr"), "-ts.vega(market, trade)", "swaption_vega_sign", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "gamma", "expr"), "-ts.gamma(market, trade)", "swaption_gamma_sign", "FAIL"),
    ("toy_usd_swaption.yaml", ("resolve", "expr"), 'dict(ts.resolve_swaption(market, kwargs), strike=kwargs.get("strike", "ATM"))', "swaption_strike_pinned", "FAIL"),
    ("toy_usd_swaption.yaml", ("resolve", "expr"), _STRADDLE_REJECTED, "swaption_straddle", "PASS"),
    ("toy_usd_swaption.yaml", ("functions", "annuity", "expr"), "ts.annuity(market, trade) / 1e6", "swaption_parity", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "prob_exercise", "expr"), "100 * ts.prob_exercise(market, trade)", "swaption_prob_exercise", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "annual_vol", "expr"), 'ts.annual_vol(market, trade) if trade["expiration_date"] > market.curve.ref_date else float("nan")', "swaption_expiry", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "annual_vol", "expr"), 'ts.annual_vol(market, trade) if trade["expiration_date"] > market.curve.ref_date else float("nan")', "ir_dead_levels", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "expiry_in_years", "expr"), "ts.expiry_in_years(market, trade) * 365 / 365.25", "ir_expiry_in_years", "FAIL"),
    ("toy_usd_swaption.yaml", ("portfolio_functions", "vega_cube", "expr"), '{";".join(k.split(";")[::-1]): v for k, v in ' + _CUBE + ".items()}", "ir_vega_cube_keys", "WARN"),
    ("toy_usd_swaption.yaml", ("portfolio_functions", "vega_cube", "expr"), '{k.replace(";", "x"): v for k, v in ' + _CUBE + ".items()}", "ir_vega_cube_keys", "FAIL"),
    ("toy_usd_swaption.yaml", ("portfolio_functions", "vega_cube", "scale_with_quantity"), False, "quantity_scaling[IRVega bucketed]", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "delta", "expr"), "ts.delta(market, trade) * (1.0 if pricebt_bump_size is None else 1.0 + 1e-3 * pricebt_bump_size)", "fd_params[IRDelta]", "INFO"),
    ("toy_usd_swaption.yaml", ("functions", "delta", "expr"), "ts.delta(market, trade) if pricebt_bump_size is None or True else 0.0", "fd_params[IRDelta]", "WARN"),
    ("toy_usd_irs_full.yaml", ("functions", "delta", "expr"), "100 * tri.delta(market, trade)", "ir_taylor", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "annuity", "expr"), "-tri.annuity(market, trade)", "swap_annuity_sign", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "annual_vol", "expr"), "ts.annual_vol(market, trade) / 100", "swaption_vol_unit", "WARN"),
    ("toy_usd_swaption.yaml", ("functions", "annual_vol", "expr"), "ts.annual_vol(market, trade) / 1e4", "swaption_vol_unit", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "fwd_rate", "expr"), "ts.fwd_rate(market, trade) / 100", "swaption_fwd_unit", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "gamma", "expr"), "0.5 * tb.gamma(market, trade)", "ir_gamma_ratio", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "cashflows", "scale_columns"), ["payment_amount", "currency"], "quantity_scaling[Cashflows frame]", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "yield_dv01", "expr"), "100 * tb.yield_dv01(market, trade)", "bond_lightning_dv01", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "gamma", "expr"), "-tb.gamma(market, trade)", "bond_gamma_sign", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "yield_bp", "expr"), "tb.yield_bp(market, trade) / 1e4", "bond_yield_unit", "FAIL"),
    ("toy_usd_bond.yaml", ("resolve", "expr"), 'tb.resolve_bond(market, dict(kwargs, buy_sell="Buy"))', "bond_buy_sell_fold", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "cashflows", "expr"), "tb.cashflows(market, trade).assign(payment_amount=lambda f: f.payment_amount.abs())", "ir_cashflows", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "cashflows", "expr"), "tb.cashflows(market, trade).assign(payment_amount=lambda f: 2 * f.payment_amount)", "ir_cashflow_drop", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "cashflows", "expr"), "tb.cashflows(market, trade).query(\"payment_type != 'Principal'\")", "bond_cashflows_bound", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "cashflows", "scale_columns"), ["payment_amount", "notional", "rate"], "quantity_scaling[Cashflows frame]", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "cashflows", "scale_columns"), ["payment_amount"], "quantity_scaling[Cashflows frame]", "FAIL"),
    ("toy_usd_bond.yaml", ("portfolio_functions", "delta_ladder", "scale_with_quantity"), False, "quantity_scaling[IRDelta bucketed]", "FAIL"),
]


@pytest.mark.parametrize("config,path,value,row,expected", MUTATIONS, ids=[f"{m[3]}-{m[4]}-{i}" for i, m in enumerate(MUTATIONS)])
def test_each_ir_row_catches_a_mistake(config, path, value, row, expected):
    rows = _rows(check_asset.run_checks(_set(copy.deepcopy(_load(config)), path, value), dates=DATES, backtest=False))
    assert rows[row].status == expected, rows[row]


def test_stale_declaration_warns():
    raw = _set(_load("toy_usd_irs_full.yaml"), ("unsupported_measures",), {"Theta": "carry is not wired"})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # the loader's own R2-9 warning
        rows = check_asset.run_checks(raw, dates=DATES, backtest=False)
    stale = [r for r in rows if r.name == "contract_declarations"]
    assert [r.status for r in stale] == ["WARN"] and "stale declaration" in stale[0].detail
    assert _rows(rows)["contract[Theta]"].status == check_asset.PASS  # the mapping wins


def _swap_pv01_half_gamma():
    """The shipped-config shape: an annuity pv01 as the IRDelta scalar, and d(pv01)/dr as gamma (half)."""
    raw = _set(_load("toy_usd_irs_full.yaml"), ("functions", "delta", "expr"), "tr.pv01(market, trade)")
    return _set(raw, ("functions", "ir_gamma", "expr"), "0.5 * tri.ir_gamma(market, trade)")


def test_fixed_annuity_half_gamma_is_caught_whatever_the_annuity_sign():
    """|IRDelta| = |Annuity| x 1e-4 identifies the fixed-annuity delta even with Annuity negated (a
    QuantLib-style fixedLegBPS), so the half gamma FAILs, and swap_annuity_sign FAILs the sign."""
    raw = _set(_swap_pv01_half_gamma(), ("functions", "annuity", "expr"), "-tri.annuity(market, trade)")
    rows = _rows(check_asset.run_checks(raw, dates=DATES, backtest=False))
    assert rows["ir_gamma_ratio"].status == check_asset.FAIL and "fixed-annuity pv01" in rows["ir_gamma_ratio"].detail
    assert rows["swap_annuity_sign"].status == check_asset.FAIL and "fixedLegBPS" in rows["swap_annuity_sign"].detail


def test_swap_gamma_without_annuity_is_unverifiable_not_pass():
    """With Annuity declared, a fixed-annuity delta and a half gamma read ~1: WARN, never PASS."""
    raw = _swap_pv01_half_gamma()
    del raw["functions"]["annuity"], raw["risk_measures"]["Annuity"]
    raw["unsupported_measures"] = {"Annuity": "test: no annuity call"}
    rows = _rows(check_asset.run_checks(raw, dates=DATES, backtest=False))
    assert rows["ir_gamma_ratio"].status == check_asset.WARN and "unverifiable: map Annuity" in rows["ir_gamma_ratio"].detail
    assert rows["swap_annuity_sign"].status == check_asset.SKIP


def test_theta_warn_also_points_at_the_delta():
    """An annuity pv01 as IRDelta off-market leaves its first-order error in the implied carry."""
    raw = _set(_load("toy_usd_irs_full.yaml"), ("functions", "delta", "expr"), "tr.pv01(market, trade)")
    theta = _rows(check_asset.run_checks(raw, dates=DATES, backtest=False))["ir_theta"]
    assert theta.status == check_asset.WARN and "not the total own-rate derivative" in theta.detail
