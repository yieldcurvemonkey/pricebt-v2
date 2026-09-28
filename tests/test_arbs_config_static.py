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

from pricebt.assets import load_asset

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
