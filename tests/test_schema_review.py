"""Findings of the adversarial review of the contracts package (cycle 1) that concern schemas and terms (spec S2, T2, T4, T5, B6)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pricebt.contracts.schema import SchemaRegistry, parse_schema
from pricebt.errors import ConfigError

pytestmark = pytest.mark.core


@pytest.fixture(scope="module")
def reg():
    return SchemaRegistry.default()


def minimal(**over):
    raw = {"schema_version": 1, "asset_class": "toy"}
    raw.update(over)
    return raw


def test_a_child_entry_inherits_the_fields_it_leaves_empty_so_the_swap_value_keeps_its_contract(reg):
    v = reg.get("swap").methods["value"]
    assert v.returns == "Valuation | float" and v.unit == "currency" and v.sign.startswith("holder") and v.required
    assert reg.get("bond").methods["value"].returns == "Valuation | float"


def test_s2_every_method_measure_and_layer_of_a_resolved_schema_declares_its_type_and_unit():
    r1 = SchemaRegistry()
    r1.register(minimal(asset_class="a", measures={"m": {}}))
    with pytest.raises(ConfigError) as ei:
        r1.get("a")
    assert "returns" in str(ei.value)
    r2 = SchemaRegistry()
    r2.register(minimal(asset_class="b", measures={"m": {"returns": "float"}}))
    with pytest.raises(ConfigError) as ei:
        r2.get("b")
    assert "unit" in str(ei.value)
    r3 = SchemaRegistry()
    r3.register(minimal(asset_class="p", measures={"m": {"returns": "float", "unit": "u"}}))
    r3.register(minimal(asset_class="c", extends="p", measures={"m": {}}))  # the child inherits returns and unit
    assert r3.get("c").measures["m"].unit == "u"


def test_a_child_that_redeclares_the_direction_term_with_other_values_is_a_clean_error_not_a_keyerror():
    r = SchemaRegistry()
    r.register(minimal(asset_class="p", terms={"side": {"type": "enum", "values": ["up", "down"]}}, direction={"term": "side", "sign": {"up": 1, "down": -1}}))
    r.register(minimal(asset_class="c", extends="p", terms={"side": {"type": "enum", "values": ["left", "right"]}}))
    with pytest.raises(ConfigError) as ei:
        r.get("c")
    assert "direction" in str(ei.value)
    r.register(minimal(asset_class="d", extends="p", terms={"side": {"type": "enum", "values": ["left", "right"]}}, direction={"term": "side", "sign": {"left": 1, "right": -1}}))
    assert r.get("d").normalise_terms({"side": "right"})[1] == -1  # restating the direction for the redeclared term is allowed


def test_enum_values_must_be_lower_case_and_an_alias_may_not_shadow_a_value():
    with pytest.raises(ConfigError):
        parse_schema(minimal(terms={"side": {"type": "enum", "values": ["Pay", "receive"]}}))
    with pytest.raises(ConfigError):
        parse_schema(minimal(terms={"side": {"type": "enum", "values": ["pay", "receive"], "aliases": {"pay": "receive"}}}))


def test_a_size_term_declared_positive_rejects_zero_and_negative(reg):
    swap = reg.get("swap")
    base = {"side": "pay", "maturity": "10Y"}
    for bad in (-1e7, 0, 0.0):
        with pytest.raises(ConfigError) as ei:
            swap.normalise_terms({**base, "notional": bad})
        assert "positive" in str(ei.value)


def test_numpy_numbers_are_numbers_and_booleans_and_nat_are_not(reg):
    swap = reg.get("swap")
    base = {"side": "pay", "maturity": "10Y"}
    for good in (np.int64(5), np.float32(2.5), np.float64(1e7)):
        assert swap.normalise_terms({**base, "notional": good})[0]["notional"] == good
    assert swap.normalise_terms({**base, "notional": 1e7, "fixed_rate": np.float64(4.5)})[0]["fixed_rate"] == 4.5
    for bad in (True, np.bool_(True)):
        with pytest.raises(ConfigError):
            swap.normalise_terms({**base, "notional": bad})
    with pytest.raises(ConfigError):
        swap.normalise_terms({"side": "pay", "notional": 1.0, "maturity": pd.NaT})


def test_iso_date_strings_must_be_real_dates_and_tokens_are_canonicalised(reg):
    swap = reg.get("swap")
    base = {"side": "pay", "notional": 1e7}
    assert swap.normalise_terms({**base, "maturity": "2034-01-04"})[0]["maturity"] == "2034-01-04"
    for bad in ("2024-13-45", "2024-02-30", "2034-1-4", "next tuesday", ""):
        with pytest.raises(ConfigError):
            swap.normalise_terms({**base, "maturity": bad})
    t, _ = swap.normalise_terms({**base, "maturity": "10Y", "effective": " SPOT "})
    assert t["effective"] == "spot" and t["maturity"] == "10Y"  # tokens are normalised, tenors and dates are not


def test_only_the_documented_reference_roots_are_accepted_in_action_terms(reg):
    swap = reg.get("swap")
    ok = {"side": "@signal.dir", "maturity": "@param.tenor", "notional": "@trigger.scaling"}
    assert swap.normalise_terms(ok, allow_refs=True)[1] is None
    for bad in ("@nonsense", "@signal", "@signal.", "@pricer.curve", "@signal.dir\n", "@signal.__x"):
        with pytest.raises(ConfigError):
            swap.normalise_terms({**ok, "notional": bad}, allow_refs=True)


def test_a_reference_nested_inside_a_mapping_term_is_detected_and_only_allowed_in_actions(reg):
    swap = reg.get("swap")
    terms = {"side": "pay", "maturity": "10Y", "notional": 1e6, "extras": {"spread": "@param.s", "n": [1, "@param.k"]}}
    assert swap.normalise_terms(terms, allow_refs=True)[0]["extras"]["spread"] == "@param.s"
    with pytest.raises(ConfigError):
        swap.normalise_terms(terms)


def test_a_double_at_is_a_literal_string_and_reaches_the_factory_single(reg):
    t, _ = reg.get("bond").normalise_terms({"side": "buy", "security": "@@CT10", "notional": 1e6})
    assert t["security"] == "@CT10"


def test_check_resolved_revalidates_what_a_factory_returns(reg):
    swap = reg.get("swap")
    given, _ = swap.normalise_terms({"side": "pay", "maturity": "10Y", "notional": 1e7})
    full = {"effective": dt.date(2024, 1, 4), "maturity": dt.date(2034, 1, 4), "fixed_rate": 4.1}
    ok = swap.check_resolved(given, {**full, "curve_id": "abc"})
    assert ok["curve_id"] == "abc" and ok["fixed_rate"] == 4.1 and ok["side"] == "pay"  # extra keys the library reports are kept
    for bad in ({"fixed_rate": None}, {"fixed_rate": float("nan")}, {"fixed_rate": "par"}, {"effective": None}, {"maturity": "next tuesday"}, {"maturity": "10Y"}, {"effective": "spot"},
                {"notional": -5.0}, {"notional": 0.0}, {"notional": 2e7}, {"side": "receive"}):
        with pytest.raises(ConfigError):
            swap.check_resolved(given, {**full, **bad})


def test_unknown_conventions_are_reported_for_an_adapter_to_reject_T5(reg):
    swap = reg.get("swap")
    assert swap.unknown_conventions({"day_count": "x", "calendar": "y"}) == []
    assert swap.unknown_conventions({"day_count": "x", "colour": 1, "moon": 2}) == ["colour", "moon"]


def test_extends_must_be_one_name_and_flags_must_be_booleans():
    for bad in (["a"], 5, ""):
        with pytest.raises(ConfigError):
            parse_schema(minimal(extends=bad))
    with pytest.raises(ConfigError):
        parse_schema(minimal(methods={"value": {"required": "false"}}))
    with pytest.raises(ConfigError):
        parse_schema(minimal(terms={"x": {"type": "number", "required": "no"}}))


def test_duplicate_keys_in_a_schema_file_are_an_error_not_last_wins(tmp_path):
    p = tmp_path / "dup.yaml"
    p.write_text("schema_version: 1\nasset_class: a\nmethods:\n  value: {returns: float}\n  value: {returns: int}\n", encoding="utf8")
    with pytest.raises(ConfigError) as ei:
        SchemaRegistry().register_file(p)
    assert "duplicate key" in str(ei.value)


def test_default_registry_fails_loudly_when_the_package_carries_no_schema_files(monkeypatch, tmp_path):
    import importlib.resources as ir

    monkeypatch.setattr(ir, "files", lambda pkg: tmp_path)
    with pytest.raises(ConfigError) as ei:
        SchemaRegistry.default()
    assert "no schemas found" in str(ei.value)


def test_the_neutral_schema_files_are_shipped_as_package_data():
    from guards import scan

    text = (scan.PROJECT / "pyproject.toml").read_text(encoding="utf8")
    assert "contracts/schemas/*.yaml" in text


def test_an_invalid_reference_root_nested_inside_a_mapping_term_is_rejected(reg):
    swap = reg.get("swap")
    terms = {"side": "pay", "maturity": "10Y", "notional": 1e6}
    for bad in ({"spread": "@pricer.curve"}, {"n": [1, "@nonsense"]}, {"deep": {"x": "@signal"}}):
        with pytest.raises(ConfigError):
            swap.normalise_terms({**terms, "extras": bad}, allow_refs=True)


def test_duplicate_default_layers_are_an_error():
    layer = {"id": "t.carry", "version": 1, "returns": "float", "unit": "u"}
    with pytest.raises(ConfigError) as ei:
        parse_schema(minimal(layers={"carry": layer}, default_layers=["carry", "carry"]))
    assert "duplicate" in str(ei.value)


# ------------------------------------------------------------------------------ second review: a date or tenor is passed on stripped; one term can be checked alone
def test_a_tenor_or_iso_date_string_is_passed_on_stripped_as_it_was_validated(reg):
    swap = reg.get("swap")
    base = {"side": "pay", "notional": 1e6}
    t, _ = swap.normalise_terms({**base, "maturity": "  10y ", "effective": "\t1Y\n"})
    assert t["maturity"] == "10y" and t["effective"] == "1Y"
    t, _ = swap.normalise_terms({**base, "maturity": " 2034-01-02 ", "effective": " SPOT "})
    assert t["maturity"] == "2034-01-02" and t["effective"] == "spot"
    resolved = swap.check_resolved(swap.normalise_terms({**base, "maturity": "10y"})[0], {"effective": " 2024-01-04 ", "maturity": " 2034-01-04 ", "fixed_rate": 4.0})
    assert resolved["effective"] == "2024-01-04" and resolved["maturity"] == "2034-01-04"


def test_check_term_validates_one_term_by_the_schemas_own_rules_and_names_it(reg):
    swap = reg.get("swap")
    assert swap.check_term("maturity", " 10Y ") == "10Y" and swap.check_term("effective", "SPOT") == "spot" and swap.check_term("notional", 5) == 5
    for name, bad in (("maturity", "banana"), ("maturity", "spot"), ("effective", "whenever"), ("effective", ""), ("maturity", 10), ("maturity", pd.NaT), ("notional", float("nan")),
                      ("notional", True), ("notional", -1.0), ("side", "sideways")):
        with pytest.raises(ConfigError, match=name) as ei:
            swap.check_term(name, bad)
        assert ei.value.code == "CFG-TERMS"
    assert reg.get("bond").check_term("security", "@@ON10") == "@ON10", "a literal is unescaped as normalise_terms does"
    with pytest.raises(ConfigError, match="nope"):
        swap.check_term("nope", 1)
    with pytest.raises(ConfigError, match="reference"):
        swap.check_term("notional", "@signal.n")
