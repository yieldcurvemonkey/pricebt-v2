"""Neutral schemas (spec S1-S7, T2): data files, validated on load, `extends`, terms and direction, layer ids, did-you-mean."""
from __future__ import annotations

import copy
import datetime as dt

import pandas as pd
import pytest

from pricebt.contracts.schema import AssetSchema, SchemaRegistry, parse_schema
from pricebt.errors import ConfigError

pytestmark = pytest.mark.core


@pytest.fixture(scope="module")
def reg():
    return SchemaRegistry.default()


def minimal(**over):
    raw = {"schema_version": 1, "asset_class": "toy"}
    raw.update(over)
    return raw


# ------------------------------------------------------------------------------ shipped schemas
def test_the_shipped_core_schemas_are_generic_swap_and_bond(reg):
    assert reg.names() == ["bond", "generic", "swap"]


def test_extends_merges_the_generic_value_contract_into_a_child(reg):
    swap = reg.get("swap")
    assert "value" in swap.methods and "pv" in swap.measures and swap.methods["value"].required
    assert swap.extends == "generic"


def test_swap_requires_value_dv01_gamma_rate_and_the_delta_ladder(reg):
    swap = reg.get("swap")
    assert swap.required_names() == {"method": ["value"], "measure": ["delta_ladder", "dv01", "gamma", "pv", "rate"]}
    assert swap.required_layers() == ["carry", "convexity", "delta", "roll"]  # required of an adapter's conformance kit, not of a load


def test_derived_measures_need_no_binding(reg):
    swap = reg.get("swap")
    assert swap.measures["pv"].derived == "value.pv" and swap.measures["notional"].derived == "terms.notional"
    assert "pv" not in swap.bindable_names("measure") and "notional" not in swap.bindable_names("measure")
    assert set(swap.bindable_names("measure")) == {"dv01", "gamma", "rate", "delta_ladder"}


def test_the_delta_ladder_contract_is_a_plain_dict_keyed_by_tenor_and_required_on_swap(reg):
    m = reg.get("swap").measures["delta_ladder"]
    assert m.required and m.returns == "dict[str, float]" and "tenor" in m.doc.lower()
    assert m.args["tenors"]["required"] is False


def test_bond_has_its_own_direction_and_a_negative_long_dv01_documented(reg):
    b = reg.get("bond")
    assert b.direction.sign == {"buy": 1, "sell": -1}
    assert "negative" in b.measures["dv01"].sign


def test_layer_ids_carry_a_version_so_results_stay_interpretable(reg):
    assert reg.get("swap").layer_ids() == {"carry": "swap.carry@1", "roll": "swap.roll@1", "delta": "swap.delta@1", "convexity": "swap.convexity@1"}
    assert reg.get("bond").layer_ids()["carry"] == "bond.carry@1"


def test_reserved_engine_rows_are_declared_as_data_and_never_layers(reg):
    g = reg.get("generic")
    assert {"total", "transactions", "cash_interest", "financing", "unexplained"} <= set(g.reserved_rows)
    assert not (set(g.reserved_rows) & set(reg.get("swap").layers))


def test_default_layers_are_the_four_required_swap_layers(reg):
    assert reg.get("swap").default_layers == ("carry", "roll", "delta", "convexity")


def test_unknown_asset_class_lists_what_is_known(reg):
    with pytest.raises(ConfigError) as ei:
        reg.get("swapp")
    assert "swap" in str(ei.value)


# ------------------------------------------------------------------------------ file validation (S5)
def test_unknown_top_level_key_is_an_error():
    with pytest.raises(ConfigError) as ei:
        parse_schema(minimal(surprise=1))
    assert ei.value.code == "CFG-UNKNOWN-KEY" and "surprise" in str(ei.value)


def test_schema_version_must_be_1():
    for v in (None, 2, "1"):
        with pytest.raises(ConfigError):
            parse_schema({"asset_class": "toy", "schema_version": v} if v is not None else {"asset_class": "toy"})


def test_unknown_entry_key_names_the_entry_and_the_allowed_keys():
    with pytest.raises(ConfigError) as ei:
        parse_schema(minimal(measures={"dv01": {"kind": "measure", "methd": "pv01"}}))
    assert "dv01" in str(ei.value) and "methd" in str(ei.value)


def test_a_schema_cannot_say_which_method_implements_a_name_S3():
    """The old `measures: {x: {method: <name>, reducer: ...}}` is gone: `method` and `reducer` are unknown keys."""
    for bad in ({"method": "pv01"}, {"reducer": "vector"}):
        with pytest.raises(ConfigError):
            parse_schema(minimal(measures={"dv01": {"kind": "measure", **bad}}))


def test_kind_must_match_the_section():
    with pytest.raises(ConfigError):
        parse_schema(minimal(measures={"dv01": {"kind": "method"}}))
    assert parse_schema(minimal(measures={"dv01": {}})).measures["dv01"].kind == "measure"  # kind defaults to the section's


def test_a_layer_needs_an_id_and_an_integer_version():
    with pytest.raises(ConfigError):
        parse_schema(minimal(layers={"carry": {"kind": "layer"}}))
    with pytest.raises(ConfigError):
        parse_schema(minimal(layers={"carry": {"id": "toy.carry", "version": "one"}}))


def test_a_layer_missing_only_its_id_or_only_its_version_is_rejected():
    with pytest.raises(ConfigError) as ei:
        parse_schema(minimal(layers={"carry": {"version": 1}}))
    assert "id" in str(ei.value)
    with pytest.raises(ConfigError) as ei:
        parse_schema(minimal(layers={"carry": {"id": "toy.carry"}}))
    assert "version" in str(ei.value)


def test_a_layer_may_not_take_a_reserved_engine_name():
    for name in ("unexplained", "total", "financing", "transactions", "cash_interest"):
        with pytest.raises(ConfigError):
            parse_schema(minimal(layers={name: {"id": f"toy.{name}", "version": 1}}))


def test_default_layers_must_be_declared_layers():
    with pytest.raises(ConfigError):
        parse_schema(minimal(layers={"carry": {"id": "t.carry", "version": 1}}, default_layers=["carry", "roll"]))


def test_extends_cycles_and_missing_parents_are_errors():
    reg = SchemaRegistry()
    reg.register(minimal(asset_class="a", extends="b"))
    reg.register(minimal(asset_class="b", extends="a"))
    with pytest.raises(ConfigError):
        reg.get("a")
    reg2 = SchemaRegistry()
    reg2.register(minimal(asset_class="c", extends="nope"))
    with pytest.raises(ConfigError):
        reg2.get("c")


def test_a_direction_must_name_a_declared_enum_term_and_a_sign_for_every_value():
    terms = {"side": {"type": "enum", "values": ["up", "down"]}}
    with pytest.raises(ConfigError):
        parse_schema(minimal(terms=terms, direction={"term": "side", "sign": {"up": 1}}))  # no sign for `down`
    with pytest.raises(ConfigError):
        parse_schema(minimal(terms=terms, direction={"term": "nope", "sign": {"up": 1, "down": -1}}))
    assert parse_schema(minimal(terms=terms, direction={"term": "side", "sign": {"up": 1, "down": -1}})).direction.sign == {"up": 1, "down": -1}


def test_schema_files_contain_no_library_argument_as_a_signature_key_S1(reg):
    from guards import scan

    files = sorted((scan.PROJECT / "src" / "pricebt" / "contracts" / "schemas").glob("*.yaml"))
    assert [f.name for f in files] == ["bond.yaml", "generic.yaml", "swap.yaml"]
    for f in files:
        assert scan.scan_schema_args(f, scan.PROJECT / "src") == [], f
        assert scan.scan_yaml(f, scan.PROJECT / "src") == [], f


# ------------------------------------------------------------------------------ terms (T2)
def test_side_aliases_map_onto_one_direction_sign(reg):
    swap = reg.get("swap")
    for given, canon, sign in (("pay", "pay", 1), ("buy", "pay", 1), ("receive", "receive", -1), ("sell", "receive", -1)):
        terms, direction = swap.normalise_terms({"side": given, "maturity": "10Y", "notional": 1e7})
        assert (terms["side"], direction) == (canon, sign)


def test_bond_side_aliases_long_short(reg):
    b = reg.get("bond")
    assert b.normalise_terms({"side": "long", "security": "CT10", "notional": 1e6})[1] == 1
    assert b.normalise_terms({"side": "short", "security": "CT10", "notional": 1e6})[1] == -1


def test_defaults_are_filled_and_relative_dates_pass_through_untouched(reg):
    terms, _ = reg.get("swap").normalise_terms({"side": "pay", "maturity": "10y", "notional": 5e6})
    assert terms["effective"] == "spot" and terms["fixed_rate"] == "par" and terms["maturity"] == "10y"  # `10y` is NOT upper-cased or resolved


def test_dates_numbers_and_the_par_token_are_accepted(reg):
    swap = reg.get("swap")
    ok = {"side": "pay", "notional": 1e7, "effective": dt.date(2024, 3, 1), "maturity": pd.Timestamp("2034-03-01"), "fixed_rate": 4.25}
    terms, _ = swap.normalise_terms(ok)
    assert terms["fixed_rate"] == 4.25 and terms["effective"] == dt.date(2024, 3, 1)
    assert swap.normalise_terms({**ok, "fixed_rate": "par"})[0]["fixed_rate"] == "par"
    assert swap.normalise_terms({**ok, "fixed_rate": " PAR "})[0]["fixed_rate"] == "par"  # the token is canonicalised, dates and tenors are not
    assert swap.normalise_terms({**ok, "effective": "2024-03-01", "maturity": "3M"})[0]["maturity"] == "3M"


@pytest.mark.parametrize("bad", [
    {"side": "pay", "notional": 1e7},  # maturity missing
    {"maturity": "10Y", "notional": 1e7},  # side missing
    {"side": "pay", "maturity": "10Y"},  # notional missing
    {"side": "long", "maturity": "10Y", "notional": 1e7},  # a bond alias is not a swap alias
    {"side": "pay", "maturity": "10Y", "notional": "1e7"},
    {"side": "pay", "maturity": "10Y", "notional": True},
    {"side": "pay", "maturity": "10Y", "notional": float("nan")},
    {"side": "pay", "maturity": "next tuesday", "notional": 1e7},
    {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": "atm"},
    {"side": "pay", "maturity": "10Y", "notional": 1e7, "extras": [1]},
    {"side": "pay", "maturity": "10Y", "notional": 1e7, "colour": "red"},  # unknown term
])
def test_bad_terms_are_errors_that_list_the_schema(reg, bad):
    with pytest.raises(ConfigError) as ei:
        reg.get("swap").normalise_terms(bad)
    assert ei.value.code == "CFG-TERMS" and "swap" in str(ei.value)


def test_reference_strings_are_accepted_for_any_term_and_resolved_later(reg):
    terms, direction = reg.get("swap").normalise_terms({"side": "@signal.direction", "maturity": "@param.tenor", "notional": "@trigger.scaling"}, allow_refs=True)
    assert terms["side"] == "@signal.direction" and direction is None  # direction unknown until the reference is resolved
    with pytest.raises(ConfigError):
        reg.get("swap").normalise_terms({"side": "@signal.direction", "maturity": "10Y", "notional": 1e6})  # references not allowed here


# ------------------------------------------------------------------------------ did-you-mean (B4)
def test_suggestions_use_edit_distance_and_the_schemas_synonyms(reg):
    swap = reg.get("swap")
    assert "convexity" in swap.suggest("gamma", ["convexity", "npv", "pv01"])
    assert "pv01" in swap.suggest("dv01", ["convexity", "npv", "pv01"])
    assert "ladder" in swap.suggest("delta_ladder", ["ladder", "zzz"])
    assert swap.suggest("dv01", ["zzz", "qqq"]) == []
    assert swap.suggest("rate", ["rates", "zzz"]) == ["rates"]  # no synonym, plain edit distance
    assert swap.suggest("gamma", ["convexity", "gammas"])[0] == "convexity"  # a declared synonym ranks before an edit-distance match


def test_synonyms_are_data_declared_on_the_entry(reg):
    assert "convexity" in reg.get("swap").measures["gamma"].synonyms


def test_registering_the_same_class_again_replaces_it_and_resolution_is_recomputed():
    reg = SchemaRegistry()
    reg.register(minimal(asset_class="x", methods={"value": {"required": True, "returns": "float"}}))
    assert reg.get("x").required_names()["method"] == ["value"]
    reg.register(minimal(asset_class="x"))
    assert reg.get("x").required_names() == {"method": [], "measure": []}


def test_a_child_inherits_terms_direction_and_default_layers_it_does_not_redeclare():
    reg = SchemaRegistry()
    reg.register(minimal(asset_class="base", terms={"side": {"type": "enum", "values": ["up", "down"], "required": True}},
                         direction={"term": "side", "sign": {"up": 1, "down": -1}}, layers={"carry": {"id": "b.carry", "version": 1, "returns": "float", "unit": "ccy"}}, default_layers=["carry"]))
    reg.register(minimal(asset_class="kid", extends="base", terms={"size": {"type": "number"}}))
    kid = reg.get("kid")
    assert set(kid.terms) == {"side", "size"} and kid.direction.sign == {"up": 1, "down": -1} and kid.default_layers == ("carry",)
    assert kid.normalise_terms({"side": "DOWN"})[1] == -1  # enum input is case-insensitive


def test_bond_security_must_be_a_non_empty_string_and_extras_a_mapping(reg):
    b = reg.get("bond")
    for bad in ({"side": "buy", "security": "", "notional": 1e6}, {"side": "buy", "security": 7, "notional": 1e6}, {"side": "buy", "security": "CT10", "notional": 1e6, "extras": "x"}):
        with pytest.raises(ConfigError):
            b.normalise_terms(bad)
    assert b.normalise_terms({"side": "buy", "security": "CT10", "notional": 1e6, "extras": {"repo": 4.0}})[0]["extras"] == {"repo": 4.0}


def test_schema_dataclasses_are_frozen(reg):
    s = reg.get("swap")
    assert isinstance(s, AssetSchema)
    with pytest.raises(Exception):
        s.asset_class = "other"
    _ = copy.deepcopy(s)  # and picklable / copyable
