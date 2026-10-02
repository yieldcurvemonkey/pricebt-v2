"""Tests for pricebt.assets.registry.AssetRegistry: add (path/dir/dict/AssetConfig), duplicate
names, market.key sharing checks, and instrument matching (DESIGN.md sections 4.4 and 5.3,
IMPLEMENTATION_PLAN.md P2.3).

Configs here are self-contained dicts (test_asset_config.py's `_minimal()` style), never the real
toy library, so this file has no dependency on any sibling task's state.
"""
from __future__ import annotations

import yaml
import pytest

from pricebt.assets.config import load_asset
from pricebt.assets.registry import AssetRegistry
from pricebt.errors import ConfigError
from pricebt.risk import contracts


def _asset(name, *, instrument="ConfigInstrument", match=None, currency="USD", market_key=None, imports="", code="", market_expr="1", defaults=None):
    cfg = {
        "schema_version": 1,
        "asset": name,
        "instrument": instrument,
        "currency": currency,
        "defaults": defaults or {},
        "imports": imports,
        "code": code,
        "market": {"expr": market_expr, **({"key": market_key} if market_key else {})},
        "functions": {"f": {"expr": "1", "unit": "ccy"}},
        "risk_measures": {"Price": "f"},
    }
    # A class with a measure contract must provide all of it (IR_RISK_DESIGN.md section 2). These
    # tests are about matching, so a strict class (IRSwap: mapping only, IR_STRICT_CONTRACT.md R3-0)
    # gets the contract's mapping skeleton with literal stubs, any other everything but Price declared.
    missing = [(r.measure, f) for r in contracts.contract_for(instrument) for f in r.forms if r.measure != "Price"]
    if contracts.is_strict(instrument):
        filled = yaml.safe_load(contracts.mapping_skeleton(instrument, missing).replace(contracts.SKELETON_EXPR, "0.0"))
        cfg["functions"].update(filled["functions"])
        cfg["portfolio_functions"] = filled["portfolio_functions"]
        cfg["risk_measures"].update(filled["risk_measures"])
    elif missing:
        cfg["unsupported_measures"] = {m: "registry test: only Price is priced" for m, _f in missing}
    if match is not None:
        cfg["match"] = match
    return cfg


class _FakeEnumValue:
    """Stands in for a pricebt enum member: has `.value`, like `PayReceive.Pay` (DESIGN.md section
    5.3's normalisation rule `str(getattr(v, 'value', v)).upper()`)."""

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f"_FakeEnumValue({self.value!r})"


# ------------------------------------------------------------------------------------ add()


def test_add_dict_registers_by_name():
    reg = AssetRegistry()
    (cfg,) = reg.add(_asset("a"))
    assert reg["a"] is cfg
    assert "a" in reg
    assert reg.names() == ["a"]


def test_add_path(tmp_path):
    p = tmp_path / "b.yaml"
    p.write_text(yaml.safe_dump(_asset("b")), encoding="utf8")
    reg = AssetRegistry()
    (cfg,) = reg.add(p)
    assert cfg.name == "b"


def test_add_directory_loads_every_yaml(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(_asset("a")), encoding="utf8")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(_asset("b")), encoding="utf8")
    (tmp_path / "not_yaml.txt").write_text("ignore me", encoding="utf8")
    reg = AssetRegistry()
    added = reg.add(tmp_path)
    assert {c.name for c in added} == {"a", "b"}
    assert reg.names() == ["a", "b"]


def test_add_asset_config_object():
    reg = AssetRegistry()
    pre_loaded = load_asset(_asset("c"))
    (cfg,) = reg.add(pre_loaded)
    assert cfg is pre_loaded
    assert reg["c"] is pre_loaded


def test_add_one_rejects_a_directory_yielding_more_than_one(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(_asset("a")), encoding="utf8")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(_asset("b")), encoding="utf8")
    reg = AssetRegistry()
    with pytest.raises(ConfigError):
        reg.add_one(tmp_path)


def test_duplicate_asset_name_raises():
    reg = AssetRegistry()
    reg.add(_asset("dup"))
    with pytest.raises(ConfigError, match="duplicate asset name 'dup'"):
        reg.add(_asset("dup"))


# ------------------------------------------------------------------------------------ market-key sharing (DESIGN.md 4.4)


def test_shared_market_key_ok_when_texts_are_identical():
    reg = AssetRegistry()
    reg.add(_asset("first", market_key="shared", imports="import math", code="X = 1", market_expr="X"))
    reg.add(_asset("second", market_key="shared", imports="import math", code="X = 1", market_expr="X"))
    assert reg.evaluating_asset("shared") == "first"  # first-registered wins the namespace


@pytest.mark.parametrize(
    "differing_field, override",
    [
        ("imports", {"imports": "import math"}),
        ("code", {"code": "X = 2"}),
        ("market.expr", {"market_expr": "X + 1"}),
    ],
)
def test_shared_market_key_conflict_is_detected_per_field(differing_field, override):
    reg = AssetRegistry()
    reg.add(_asset("first", market_key="shared", imports="", code="X = 1", market_expr="X"))
    second_kwargs = dict(market_key="shared", imports="", code="X = 1", market_expr="X")
    second_kwargs.update(override)
    with pytest.raises(ConfigError, match=r"market key shared: " + differing_field.replace(".", r"\.") + r" differs between assets first and second"):
        reg.add(_asset("second", **second_kwargs))


def test_market_key_defaults_to_asset_name_so_different_assets_dont_conflict():
    reg = AssetRegistry()
    reg.add(_asset("x", code="X = 1"))
    reg.add(_asset("y", code="X = 2"))  # different market_key (defaults to name) -> no conflict
    assert reg.names() == ["x", "y"]


# ------------------------------------------------------------------------------------ match() (DESIGN.md 5.3)


def test_match_explicit_asset_found():
    reg = AssetRegistry()
    reg.add(_asset("only"))
    cfg = reg.match("AnyClass", {}, explicit_asset="only")
    assert cfg.name == "only"


def test_match_explicit_asset_unknown_raises():
    reg = AssetRegistry()
    reg.add(_asset("only"))
    with pytest.raises(ConfigError, match=r"unknown asset 'missing'; registered: \['only'\]"):
        reg.match("AnyClass", {}, explicit_asset="missing")


def test_match_single_candidate_wins():
    reg = AssetRegistry()
    reg.add(_asset("usd", instrument="IRSwap", match={"notional_currency": "USD"}))
    reg.add(_asset("eur", instrument="IRSwap", match={"notional_currency": "EUR"}))
    cfg = reg.match("IRSwap", {"notional_currency": "USD"})
    assert cfg.name == "usd"


def test_match_defaults_are_merged_before_rules_are_checked():
    reg = AssetRegistry()
    reg.add(_asset("usd", instrument="IRSwap", match={"notional_currency": "USD"}, defaults={"notional_currency": "USD"}))
    cfg = reg.match("IRSwap", {})  # no kwargs given -> falls back to the config's own default
    assert cfg.name == "usd"


def test_match_zero_candidates_because_no_asset_declares_the_instrument():
    reg = AssetRegistry()
    reg.add(_asset("usd", instrument="IRSwap", match={"notional_currency": "USD"}))
    with pytest.raises(ConfigError, match=r"no asset matches FXOption"):
        reg.match("FXOption", {})


def test_match_zero_matches_lists_why_each_candidate_failed():
    reg = AssetRegistry()
    reg.add(_asset("usd", instrument="IRSwap", match={"notional_currency": "USD"}))
    reg.add(_asset("eur", instrument="IRSwap", match={"notional_currency": "EUR"}))
    with pytest.raises(ConfigError) as exc:
        reg.match("IRSwap", {"notional_currency": "GBP"})
    msg = str(exc.value)
    assert "usd:" in msg and "eur:" in msg


def test_match_ambiguous_raises():
    reg = AssetRegistry()
    reg.add(_asset("a1", instrument="IRSwap", match={}))
    reg.add(_asset("a2", instrument="IRSwap", match={}))
    with pytest.raises(ConfigError, match=r"ambiguous: IRSwap matches \['a1', 'a2'\]; pass pricebt_asset="):
        reg.match("IRSwap", {})


def test_match_compares_enum_like_value_against_plain_string_case_insensitively():
    reg = AssetRegistry()
    reg.add(_asset("payer", instrument="IRSwap", match={"pay_or_receive": "pay"}))
    cfg = reg.match("IRSwap", {"pay_or_receive": _FakeEnumValue("Pay")})
    assert cfg.name == "payer"


def test_match_missing_kwarg_key_fails_that_candidate():
    reg = AssetRegistry()
    reg.add(_asset("usd", instrument="IRSwap", match={"notional_currency": "USD"}))
    with pytest.raises(ConfigError, match=r"missing kwarg 'notional_currency'"):
        reg.match("IRSwap", {})
