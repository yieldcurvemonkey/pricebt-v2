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
# IR_STRICT_CONTRACT R3-1: the identity rows of the new strict-contract measures, and R3-0's constant scan
STRICT_PASS = ["ir_premium_cents", "ir_local_annuity", "ir_forward_price", "ir_fair_premium", "ir_par_spread", "ir_compounded_fixed_rate", "ir_crif", "ir_fake_constant"]
# the strict-contract identities a Bond shares with the swaps (its ForwardPrice, FairPremium, PremiumCents
# and ParSpread have Bond texts, BOND_DESIGN section 3) and the bond identity and financing rows
BOND_IDENTITY_PASS = ["ir_local_annuity", "ir_compounded_fixed_rate", "ir_crif", "ir_fake_constant"]
BOND_PASS = [
    "bond_clean_dirty", "bond_fair_premium", "bond_premium_cents", "bond_duration", "bond_convexity", "bond_settlement",
    "bond_accrued_over_coupon", "bond_repo", "bond_financing", "bond_forward_parity", "bond_carry_roll", "bond_holding_cash",
]
PACK_PASS = {
    "toy_usd_irs_full.yaml": ["swap_dv01_sign", "swap_bucket_sum", "swap_par_rate_atm", "swap_pnl_explain", "swap_annuity_sign", "measure_series", *STRICT_PASS],
    "toy_usd_swaption.yaml": [*STRICT_PASS, 
        "swaption_buy_sell_fold", "swaption_straddle", "swaption_strike_pinned", "swaption_parity", "swaption_fwd_unit", "swaption_vol_unit", "swaption_vega_sign",
        "swaption_delta_sign", "swaption_gamma_sign", "swaption_prob_exercise", "swaption_expiry", "quantity_scaling[IRVega bucketed]",
    ],
    "toy_usd_bond.yaml": [*BOND_IDENTITY_PASS, *BOND_PASS,
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
    # BOND_DESIGN section 3: one break each in the bond identities and the financing contract
    ("bad_bond_financing_sign", "bond_financing", "FAIL", "a long pays the repo interest"),
    ("bad_bond_clean_dirty", "bond_clean_dirty", "FAIL", "an accrued added instead of subtracted"),
    ("bad_bond_forward_parity", "bond_forward_parity", "FAIL", "a coupon paid before H must be subtracted"),
    ("bad_half_gamma_swaption", "ir_gamma_ratio", "FAIL", "half-gamma"),
    # one per strict-contract identity row (R3-1) and the constant scan (R3-0)
    ("bad_premium_cents_pct", "ir_premium_cents", "FAIL", "Price / |notional_amount|"),
    ("bad_local_annuity_per_bp", "ir_local_annuity", "FAIL", "Annuity / |notional_amount|"),
    ("bad_forward_price_times_df", "ir_forward_price", "FAIL", "Price x DF"),
    ("bad_fair_premium_final_date", "ir_fair_premium", "FAIL", "that is ForwardPrice"),
    ("bad_par_spread_reversed", "ir_par_spread", "FAIL", "sign is reversed"),
    ("bad_compounded_rate_decompounded", "ir_compounded_fixed_rate", "FAIL", "outside the bounds"),
    ("bad_crif_upper_tenors", "ir_crif", "FAIL", "not SIMM tenors"),
    ("bad_fake_constant", "ir_fake_constant", "FAIL", "IRDiscountDeltaParallel -> zero_per_bp"),
    # the loader refuses an extensive time level, so the row never runs: the load error names it
    ("bad_expiry_in_years_extensive", "config_loads", "FAIL", "ExpiryInYears"),
]


@pytest.mark.parametrize("fixture,row,status,text", BROKEN_IR, ids=[b[0] for b in BROKEN_IR])
def test_broken_ir_fixture_fails_its_row(fixture, row, status, text):
    results = check_asset.run_checks(FIXTURES / f"{fixture}.yaml", dates=DATES, sys_path=[FIXTURES], backtest=False)
    rows = _rows(results)
    assert rows[row].status == status and text in rows[row].detail, rows[row]
    if row in STRICT_PASS + BOND_PASS:  # a one-error copy of a full toy: exactly its own row fails
        assert [r.name for r in results if r.status == check_asset.FAIL] == [row]


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
    detail = rows["contract[IRVolga]"].detail
    assert "not mapped" in detail and "require a mapping for every contract measure" in detail and "declare" not in detail


def test_pack_override_fails_a_declaration_of_a_strict_contract_measure():
    """R3-0: for IRSwaption a declaration never satisfies a row -- declared AND mapped is a FAIL (no
    R2-9 'mapping wins'), declared and unmapped too; a declared preset counts as its base."""
    raw = _config_instrument_swaption()
    del raw["risk_measures"]["IRVolga"]
    raw["unsupported_measures"] = {"IRVolga": "no vol bump", "IRVanna": "mapped, yet declared", "IRDeltaParallel": "a preset of IRDelta"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # ConfigInstrument: the loader's own R2-9 warning
        rows = _rows(check_asset.run_checks(raw, dates=DATES, backtest=False, pack="IRSwaption"))
    for m in ("IRVolga", "IRVanna", "IRDelta"):
        assert rows[f"contract[{m}]"].status == check_asset.FAIL and "a declaration cannot satisfy it" in rows[f"contract[{m}]"].detail, rows[f"contract[{m}]"]
    declarations = [r for r in check_asset.run_checks(raw, dates=DATES, backtest=False, pack="IRSwaption") if r.name == "contract_declarations"]
    assert {r.status for r in declarations} >= {check_asset.FAIL}
    # the same declaration on a Bond pack FAILs too: Bond is strict (BOND_DESIGN 4.1)
    bond = _load("toy_usd_bond.yaml")
    bond.update(asset="ci_bond", instrument="ConfigInstrument")
    bond.pop("match")
    del bond["risk_measures"]["Theta"]
    bond["unsupported_measures"] = {"Theta": "no carry call"}
    theta = _rows(check_asset.run_checks(bond, dates=DATES, backtest=False, pack="Bond"))["contract[Theta]"]
    assert theta.status == check_asset.FAIL and "a declaration cannot satisfy it" in theta.detail and "not mapped" in theta.detail


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


MULTI = "multi"  # a MUTATIONS path meaning: value is a list of (path, value) edits


def _set(cfg, path, value):
    if path == MULTI:
        for p, v in value:
            _set(cfg, p, v)
        return cfg
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
    # strict-contract rows (R3-0/R3-1): one realistic slip each, beyond the fixtures
    ("toy_usd_irs_full.yaml", ("functions", "par_spread", "expr"), "tri.par_spread(market, trade) * (-1.0 if trade.notional > 0 else 1.0)", "ir_par_spread", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "par_spread", "expr"), "tri.par_spread(market, trade) / 100.0", "ir_par_spread", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "compounded_rate", "expr"), "tr.par_rate(market, trade)", "ir_compounded_fixed_rate", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "crif_ir_curve", "expr"), "tri.crif_ir_curve(market, trade).assign(Amount=lambda f: -f.Amount)", "ir_crif", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "crif_ir_curve", "expr"), "tri.crif_ir_curve(market, trade).assign(Qualifier='EUR')", "ir_crif", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "premium_cents", "expr"), "tri.npv(market, trade) / trade.notional * 1e4", "ir_premium_cents", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "forward_price", "expr"), "-tri.forward_price(market, trade)", "ir_forward_price", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "fair_premium", "expr"), "ts.fair_premium(market, trade) / 0.995", "ir_fair_premium", "WARN"),
    ("toy_usd_swaption.yaml", ("functions", "local_annuity", "expr"), "ts.annuity(market, trade) / abs(trade['notional']) * 1.002", "ir_local_annuity", "WARN"),
    ("toy_usd_swaption.yaml", ("risk_measures", "IRVega"), {"scalar": "zero_per_bp", "bucketed": "vega_cube"}, "ir_fake_constant", "FAIL"),
    # fix round F4: slips the earlier rows let through (each PASSed on the previous check_asset_ir.py)
    # an unsigned premium / annuity: right on the Buy side (Price > 0), wrong only on the Sell side
    ("toy_usd_swaption.yaml", ("functions", "premium_cents", "expr"), "abs(ts.premium_cents(market, trade, pricebt_date))", "ir_premium_cents", "FAIL"),
    ("toy_usd_swaption.yaml", ("functions", "local_annuity", "expr"), "abs(ts.local_annuity_in_cents(market, trade))", "ir_local_annuity", "FAIL"),
    # a semiannual fixed leg (as the kwarg says) restated as K itself, uncompounded; the annual kwarg passes
    ("toy_usd_irs_full.yaml", ("defaults", "fixed_rate_frequency"), "6m", "ir_compounded_fixed_rate", "FAIL"),
    ("toy_usd_irs_full.yaml", ("defaults", "fixed_rate_frequency"), "1y", "ir_compounded_fixed_rate", "PASS"),
    # CRIF labels: an int Bucket, a non-SIMM sub-curve, an AmountCurrency that is not the Qualifier's
    ("toy_usd_irs_full.yaml", ("functions", "crif_ir_curve", "expr"), "tri.crif_ir_curve(market, trade).assign(Bucket=1)", "ir_crif", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "crif_ir_curve", "expr"), "tri.crif_ir_curve(market, trade).assign(Label2='SOFR')", "ir_crif", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "crif_ir_curve", "expr"), "tri.crif_ir_curve(market, trade).assign(AmountCurrency='EUR')", "ir_crif", "FAIL"),
    # constants: a non-zero zero-by-convention value, and NaN / inf placeholders
    ("toy_usd_irs_full.yaml", ("functions", "zero_per_bp", "expr"), "5.0", "ir_fake_constant", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "spot_rate", "expr"), "float('nan')", "ir_fake_constant", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "spot_rate", "expr"), "math.nan", "ir_fake_constant", "FAIL"),
    ("toy_usd_irs_full.yaml", ("functions", "zero_per_bp", "expr"), "-math.inf", "ir_fake_constant", "FAIL"),
    # BOND_DESIGN section 3: one realistic slip per bond identity and financing row (the fixtures add three more)
    ("toy_usd_bond.yaml", ("functions", "dirty_price", "expr"), "tb.dirty_price(market, trade, pricebt_date) / 100", "bond_clean_dirty", "FAIL"),
    ("toy_usd_bond.yaml", MULTI, [(("functions", "fair_premium"), {"expr": "-tb.npv(market, trade, pricebt_date)", "unit": "ccy"}),
                                  (("risk_measures", "FairPremium"), "fair_premium")], "bond_fair_premium", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "premium_cents", "expr"), "tb.premium_cents(market, trade, pricebt_date) / 100", "bond_premium_cents", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "mod_duration", "expr"), "-tb.modified_duration(market, trade)", "bond_duration", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "convexity", "expr"), "0.5 * tb.convexity(market, trade)", "bond_convexity", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "days_to_settle", "expr"), "1.0", "bond_settlement", "FAIL"),
    # business days with a calendar-day settlement_date attribute that agrees with it: only the Friday rule catches it
    ("toy_usd_bond.yaml", MULTI, [(("functions", "days_to_settle", "expr"), "1.0"), (("attributes", "settlement_date"), 'resolved["trade_date"] + tb._DAY')],
     "bond_settlement", "FAIL"),
    # a settlement_date attribute that is the trade date (T+0) while DaysToSettlement is right: only the attribute check catches it
    ("toy_usd_bond.yaml", ("attributes", "settlement_date"), 'resolved["trade_date"]', "bond_settlement", "FAIL"),
    # an unsigned financing: 0 on the trade date, so only the later fold date shows it
    ("toy_usd_bond.yaml", ("functions", "financing", "expr"), "abs(tb.financing_to_date(market, trade))", "bond_buy_sell_fold", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "discount_delta", "expr"), "0.0", "ir_fake_constant", "FAIL"),  # not zero by convention for a bond
    ("toy_usd_bond.yaml", ("functions", "accrued", "expr"), "tb._accrued_at(trade, market.curve.ref_date)", "bond_accrued_over_coupon", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "repo_haircut", "expr"), "100 * tb.repo_haircut(market, trade)", "bond_repo", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "financing", "expr"), 'tb.financing_to_date(market, trade) / (1 - trade["haircut"])', "bond_financing", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "forward_price", "expr"), "-tb.forward_price(market, trade)", "bond_forward_parity", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "carry", "expr"), "-tb.carry(market, trade)", "bond_carry_roll", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "cashflows", "expr"), "tb.cashflows(market, trade).assign(payment_date=lambda f: [tb.next_weekday(p) for p in f.payment_date])",
     "bond_holding_cash", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "clean_price", "expr"), 'tb.clean_price(market, trade, pricebt_date) * (1 if trade["face"] > 0 else -1)', "bond_buy_sell_fold", "FAIL"),
    ("toy_usd_bond.yaml", ("functions", "repo_rate", "expr"), 'tb.repo_rate(market, trade) * abs(trade["face"]) / 1e6', "bond_size_linearity", "FAIL"),
]


@pytest.mark.parametrize("config,path,value,row,expected", MUTATIONS, ids=[f"{m[3]}-{m[4]}-{i}" for i, m in enumerate(MUTATIONS)])
def test_each_ir_row_catches_a_mistake(config, path, value, row, expected):
    rows = _rows(check_asset.run_checks(_set(copy.deepcopy(_load(config)), path, value), dates=DATES, backtest=False))
    assert rows[row].status == expected, rows[row]


# the toy USD curve moved down to ~0.25%: below 0.5% a Price x DF slip sat inside the old relative band
_LOW_RATES = [(("market", "expr"), '(lambda m: None if m is None else tri.bumped(m, -0.0375))(tr.market(pricebt_date, "USD", pricebt_csa))'),
              (("market", "key"), "toy_usd_ois_low_rates")]


@pytest.mark.parametrize("forward_price,expected", [
    (None, "PASS"),                                                                                              # honest: no false FAIL at 0.25%
    ("tri.npv(market, trade, pricebt_date) * market.discount_factor(trade.termination_date)", "FAIL"),           # Price x DF
])
def test_forward_price_times_df_is_caught_at_low_rates(forward_price, expected):
    cfg = copy.deepcopy(_load("toy_usd_irs_full.yaml"))
    for path, value in _LOW_RATES + ([(("functions", "forward_price", "expr"), forward_price)] if forward_price else []):
        _set(cfg, path, value)
    rows = _rows(check_asset.run_checks(cfg, dates=DATES, backtest=False))
    assert rows["ir_forward_price"].status == expected, rows["ir_forward_price"]
    assert "own rate 0.2" in rows["ir_forward_price"].detail                                                    # really at ~0.25%


MERIDIAN = REPO / "skills" / "pricebt-connect-pricing-library" / "example"


@pytest.mark.parametrize("fair_premium,expected", [
    (None, "PASS"),
    ('_risk(market, trade, pricebt_date)["PV"] * _dfs(market, trade, pricebt_date)[0]', "FAIL"),   # Price x DF(spot)
])
def test_fair_premium_times_df_spot_is_caught(fair_premium, expected):
    """The toys have no spot lag (FairPremium == Price), so the direction of the ratio is proven on the
    Meridian example, which settles at spot: Price x DF(spot) is within the old ~10-day band."""
    raw = yamlio.load_file(MERIDIAN / "meridian_usd_irs.yaml")
    if fair_premium:
        raw["functions"]["fair_premium"]["expr"] = fair_premium
    rows = _rows(check_asset.run_checks(raw, dates=[date(2024, 1, 2), date(2024, 4, 2)], sys_path=[MERIDIAN], backtest=False))
    assert rows["ir_fair_premium"].status == expected, rows["ir_fair_premium"]


@pytest.mark.parametrize("config", ["toy_usd_bond.yaml", "toy_usd_irs_full.yaml"])
def test_a_stale_declaration_fails_to_load_on_every_contract_class(config):
    """R2-9 (a mapping wins over a stale declaration, with a warning) survives only for classes with
    no contract: on a Bond (BOND_DESIGN 4.1) or an IRSwap (R3-0) declaring a mapped contract measure
    is a load error naming it, so config_loads FAILs."""
    raw = _set(_load(config), ("unsupported_measures",), {"Theta": "carry is not wired"})
    loads = _rows(check_asset.run_checks(raw, dates=DATES, backtest=False))["config_loads"]
    assert loads.status == check_asset.FAIL and "unsupported_measures declares Theta" in loads.detail and "Bond/IRSwap/IRSwaption configs must map" in loads.detail


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
    """Without Annuity, a fixed-annuity delta and a half gamma read ~1: WARN, never PASS. An IRSwap
    config cannot leave Annuity out any more (R3-0), so this runs as a ConfigInstrument checked with
    --pack IRSwap -- where the missing Annuity is also a contract FAIL."""
    raw = _swap_pv01_half_gamma()
    del raw["functions"]["annuity"], raw["risk_measures"]["Annuity"]
    raw.update(asset="ci_swap", instrument="ConfigInstrument")
    raw.pop("match")
    rows = _rows(check_asset.run_checks(raw, dates=DATES, backtest=False, pack="IRSwap"))
    assert rows["ir_gamma_ratio"].status == check_asset.WARN and "unverifiable: map Annuity" in rows["ir_gamma_ratio"].detail
    assert rows["swap_annuity_sign"].status == check_asset.SKIP
    assert rows["contract[Annuity]"].status == check_asset.FAIL


def test_theta_warn_also_points_at_the_delta():
    """An annuity pv01 as IRDelta off-market leaves its first-order error in the implied carry."""
    raw = _set(_load("toy_usd_irs_full.yaml"), ("functions", "delta", "expr"), "tr.pv01(market, trade)")
    theta = _rows(check_asset.run_checks(raw, dates=DATES, backtest=False))["ir_theta"]
    assert theta.status == check_asset.WARN and "not the total own-rate derivative" in theta.detail


def test_bond_gamma_ratio_fits_the_delta_drift_per_settlement_day():
    """A bond's Price is a settlement-date value (BOND_DESIGN 4.5), so its delta drifts per settlement
    day: a Friday step moves settlement by one business day, not three calendar days. Fitted per
    calendar day, the drift leaks into the gamma slope on a quiet stretch (2026-06-05..07-03: ratio
    ~1.8, a false WARN); per settlement day the ratio is ~1."""
    rows = _rows(check_asset.run_checks(ASSETS / "toy_usd_bond.yaml", dates=[date(2026, 6, 5), date(2026, 7, 6)], backtest=False))
    assert rows["ir_gamma_ratio"].status == check_asset.PASS, rows["ir_gamma_ratio"]
