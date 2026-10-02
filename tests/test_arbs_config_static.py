"""Static checks on the ARBS example asset config (IMPLEMENTATION_PLAN.md P5.1).

`load_asset` only parses, validates and *compiles* (`compile(..., "eval"/"exec")`) an asset config;
per DESIGN.md section 4.4 it never executes `imports` or `code` -- that happens lazily, inside
`AssetNamespace`, on first evaluation. So calling `load_asset` on this file never imports ARBS,
never touches the network, and never reads the curve store. This module MUST NOT import ARBS and
MUST NOT construct an `AssetNamespace` or eval anything from this asset: it only inspects the
loaded `AssetConfig` object's strings and structure.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from pricebt.assets import load_asset
from pricebt.risk import contracts

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "assets" / "usd_sofr_ois_interest_rate_swap.yaml"


def test_arbs_config_loads():
    cfg = load_asset(CONFIG_PATH)
    assert cfg.name == "usd_sofr_ois_interest_rate_swap"
    assert cfg.instrument == "IRSwap"


def test_price_and_irdelta_are_mapped_in_risk_measures():
    cfg = load_asset(CONFIG_PATH)
    assert cfg.risk_measures["Price"].scalar == "npv"
    assert cfg.risk_measures["IRDelta"].scalar == "dv01"
    assert cfg.risk_measures["IRDelta"].bucketed == "delta_ladder"


def test_market_expr_forbids_nojumps_and_get_pricer():
    cfg = load_asset(CONFIG_PATH)
    assert "NOJUMPS" not in cfg.market_expr
    assert "get_pricer" not in cfg.market_expr


def test_code_block_names_last_safe():
    cfg = load_asset(CONFIG_PATH)
    assert "_LAST_SAFE" in cfg.code_src


# docs/v2/IR_STRICT_CONTRACT.md R3-0/R3-1: the 19 base rows + the 8 R3-1 rows, spelled out so this test
# does not shrink with contracts.CONTRACTS (it is also checked against it below).
STRICT_IRSWAP_FORMS = {
    "Price": ("scalar",), "IRDelta": ("scalar", "bucketed"), "IRDiscountDeltaParallel": ("scalar",),
    "IRGammaParallel": ("scalar",), "IRGamma": ("bucketed",), "IRVega": ("scalar", "bucketed"), "IRVanna": ("scalar",),
    "IRVolga": ("scalar",), "IRBasis": ("scalar",), "IRXccyDelta": ("scalar",), "IRFwdRate": ("scalar",),
    "IRSpotRate": ("scalar",), "IRAnnualImpliedVol": ("scalar",), "IRAnnualATMImpliedVol": ("scalar",),
    "IRDailyImpliedVol": ("scalar",), "Theta": ("scalar",), "ExpiryInYears": ("scalar",), "Annuity": ("scalar",),
    "Cashflows": ("frame",),
    "ParSpread": ("scalar",), "FairPremium": ("scalar",), "ForwardPrice": ("scalar",), "PremiumCents": ("scalar",),
    "LocalAnnuityInCents": ("scalar",), "CompoundedFixedRate": ("scalar",), "CRIFIRCurve": ("frame",), "PnlExplain": ("bucketed",),
}


def test_every_strict_contract_measure_is_mapped_with_no_declarations():
    cfg = load_asset(CONFIG_PATH)
    assert "unsupported_measures" not in yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert not cfg.unsupported_measures
    assert len(STRICT_IRSWAP_FORMS) == 27
    wanted = {(m, f) for m, forms in STRICT_IRSWAP_FORMS.items() for f in forms}
    wanted |= {(r.measure, f) for r in contracts.CONTRACTS["IRSwap"] for f in r.forms}
    missing = sorted(wanted - set(cfg.provided_forms))
    assert not missing, f"not mapped: {missing}"


def test_frames_scale_and_literal_zeros_only_where_the_contract_defines_zero():
    cfg = load_asset(CONFIG_PATH)
    for measure, col in (("Cashflows", "payment_amount"), ("CRIFIRCurve", "Amount")):
        spec = cfg.functions[cfg.risk_measures[measure].scalar]
        assert spec.returns == "frame" and col in spec.scale_columns and spec.scale_with_quantity, measure
    zero_ok = contracts.ZERO_BY_CONVENTION["IRSwap"]
    for measure, mapping in cfg.risk_measures.items():
        fn = mapping.scalar
        if fn in cfg.functions and cfg.functions[fn].expr.strip() in ("0", "0.0"):
            assert measure in zero_ok, f"{measure} is mapped to a literal 0 but the contract does not define it as 0"


def test_theta_is_per_day_and_irdelta_scalar_stays_the_annuity_dv01():
    cfg = load_asset(CONFIG_PATH)
    assert cfg.risk_measures["Theta"].scalar == "theta_1d" != cfg.risk_measures["IRTheta"].scalar   # never the per-year function
    assert "return 365.0 * theta_1d(m, t)" in cfg.code_src   # IRTheta = 365 x Theta, one definition
    assert cfg.functions["dv01"].expr == "0.0 if not alive(market, trade) else market.pv01(trade)"   # 040304 sizes on it
