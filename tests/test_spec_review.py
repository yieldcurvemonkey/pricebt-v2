"""Findings of the adversarial review of the contracts package (cycle 1) that concern instrument specs and trade templates (spec B4, B6, T4, T5)."""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys

import pandas as pd
import pytest

from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Built, Kit, TradeTemplate, build_spec, conventions_digest
from pricebt.errors import ConfigError

import spec_helpers as H

pytestmark = pytest.mark.core

ALLOW = ("pricebt", "spec_helpers")
SCHEMAS = SchemaRegistry.default()
TERMS = {"side": "pay", "maturity": "10Y", "notional": 1e7}
TS = pd.Timestamp("2024-01-02", tz="UTC")


def spec(raw=None, **over):
    base = {"asset_class": "swap", "factory": H.weird_kit, "conventions": {"day_count": "act360"}}
    base.update(raw or {})
    base.update(over)
    return build_spec("usd_sofr_ois", base, schemas=SCHEMAS, allow=ALLOW)


# ------------------------------------------------------------------------------ load-time validation regardless of the factory kind
def test_a_function_binding_is_signature_checked_even_when_the_factory_is_a_plain_callable():
    bad = {**H.BIND, "dv01": {"target": {"function": "pricebt.testing.toys:years_between"}, "kwargs": {"nope": 1}}}
    with pytest.raises(ConfigError) as ei:
        spec(factory=H.weird_factory, bind=bad)
    assert ei.value.code == "CFG-BINDING"


def test_a_method_binding_with_no_known_class_cannot_be_checked_at_load_and_fails_at_the_first_call_instead():
    """Documented limit: with a plain-callable factory pricebt does not know the class, so a method target is validated when it is first called."""
    sp = spec(factory=H.weird_factory, bind={**H.BIND, "dv01": {"target": {"method": "nope_typo"}}})
    assert sp.bindings["dv01"].target == "nope_typo"


@pytest.mark.parametrize("bad_factory", [
    lambda pricer, ts, *, trade, conv: None,
    lambda pricer: None,
    lambda pricer, ts, terms, conventions, extra: None,
])
def test_the_factory_signature_is_validated_at_load(bad_factory):
    with pytest.raises(ConfigError) as ei:
        spec(factory=bad_factory, bind=H.BIND)
    assert ei.value.code == "CFG-FACTORY"


def test_a_kits_factory_signature_is_validated_too():
    with pytest.raises(ConfigError) as ei:
        spec(factory=Kit(factory=lambda pricer, ts, *, trade, conv: None, asset_class="swap", default_bind=H.BIND, cls=H.Weird))
    assert ei.value.code == "CFG-FACTORY"


# ------------------------------------------------------------------------------ kit extension names
def test_a_valid_namespaced_extension_layer_can_be_bound_and_requested():
    kit = Kit(factory=H.weird_factory, asset_class="swap", default_bind={**H.BIND, "lib.fixings": {"target": {"method": "lay_a"}, "kwargs": {"market": "@pricer"}}},
              cls=H.Weird, extra={"lib.fixings": "layer"})
    sp = spec(factory=kit, layers=["lib.fixings"])
    assert "lib.fixings" in sp.bindings and sp.layers == ("lib.fixings",)


@pytest.mark.parametrize("extra,why", [
    ({"lib.x": "banana"}, "kind"),
    ({"unexplained": "layer"}, "reserved"),
    ({"financing": "layer"}, "reserved"),
    ({"dv01": "measure"}, "collides"),
    ({"carry": "layer"}, "collides"),
])
def test_kit_extension_names_may_not_be_reserved_or_collide_with_schema_names_and_need_a_valid_kind(extra, why):
    kit = Kit(factory=H.weird_factory, asset_class="swap", default_bind=H.BIND, cls=H.Weird, extra=extra)
    with pytest.raises(ConfigError) as ei:
        spec(factory=kit)
    assert ei.value.code == "CFG-UNKNOWN-BINDING"


# ------------------------------------------------------------------------------ malformed config shapes are ConfigErrors
@pytest.mark.parametrize("over", [
    {"bind": [1, 2]}, {"bind": "dv01"}, {"layers": 5}, {"layers": "carry"}, {"layers": ["carry", "carry"]}, {"params": ["a"]}, {"pricers": ["x"]}, {"conventions": [1]},
    {"conventions": {1: "a"}}, {"factory": 5},
])
def test_malformed_spec_sections_are_config_errors_not_raw_exceptions(over):
    with pytest.raises(ConfigError):
        spec(**over)


def test_a_layers_string_is_reported_as_a_type_error_not_iterated_per_character():
    with pytest.raises(ConfigError) as ei:
        spec(layers="delta")  # would otherwise become d, e, l, t, a
    assert "must be a list" in str(ei.value)


# ------------------------------------------------------------------------------ resolved terms are re-validated (T4)
def _spec_returning(terms_fn):
    def factory(pricer, ts, *, terms, conventions):
        return Built(H.Weird(1.0, 1, 4.0), terms_fn(H.resolve_dates(terms, ts), terms))

    return spec(factory=Kit(factory=factory, asset_class="swap", default_bind=H.BIND, cls=H.Weird))


@pytest.mark.parametrize("patch", [
    {"fixed_rate": None}, {"fixed_rate": float("nan")}, {"fixed_rate": "atm"}, {"effective": None}, {"maturity": "next tuesday"}, {"notional": -5.0}, {"notional": 0.0},
    {"notional": 2e7}, {"side": "receive"},
])
def test_terms_a_factory_returns_are_validated_and_the_trade_defining_ones_cannot_change(patch):
    sp = _spec_returning(lambda resolved, terms: {**resolved, "fixed_rate": 4.0, **patch})
    with pytest.raises(ConfigError):
        TradeTemplate("t", sp, TERMS).build(H.Market(), TS)


def test_extra_keys_a_factory_reports_are_kept_on_the_resolved_terms():
    sp = _spec_returning(lambda resolved, terms: {**resolved, "fixed_rate": 4.0, "curve_id": "sofr-2024-01-02"})
    assert TradeTemplate("t", sp, TERMS).build(H.Market(), TS).terms["curve_id"] == "sofr-2024-01-02"


# ------------------------------------------------------------------------------ isolation of conventions and terms
def test_a_factory_that_mutates_the_conventions_or_terms_it_was_given_changes_nothing_that_is_kept():
    def factory(pricer, ts, *, terms, conventions):
        conventions["day_count"] = "HACKED"
        conventions.setdefault("calendar", []).append("HACKED")
        terms["extras"]["leak"] = True
        return Built(H.Weird(1.0, 1, 4.0), H.resolve_dates({**terms, "fixed_rate": 4.0}, ts))

    sp = spec(factory=Kit(factory=factory, asset_class="swap", default_bind=H.BIND, cls=H.Weird), conventions={"day_count": "act360", "calendar": ["a"]})
    digest = sp.conventions_digest
    t = TradeTemplate("t", sp, {**TERMS, "extras": {"spread": 1.0}})
    t.build(H.Market(), TS)
    t.build(H.Market(), TS)  # a second build sees the ORIGINAL conventions
    assert sp.conventions == {"day_count": "act360", "calendar": ["a"]} and sp.conventions_digest == digest and t.terms["extras"] == {"spread": 1.0}


def test_the_conventions_digest_is_canonical_for_sets_and_rejects_non_string_keys():
    assert conventions_digest({"cal": {"b", "a", "c"}}) == conventions_digest({"cal": {"c", "a", "b"}})
    assert conventions_digest({"cal": {"a"}}) != conventions_digest({"cal": {"a", "b"}})
    with pytest.raises(ConfigError):
        conventions_digest({1: "a"})


def test_the_conventions_digest_does_not_depend_on_hash_randomisation():
    code = ("import sys; sys.path[:0] = ['src', 'tests']; from pricebt.contracts.spec import conventions_digest as d; print(d({'cal': {'usd', 'eur', 'gbp', 'jpy'}, 'n': 2}))")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": s, "PYTHONDONTWRITEBYTECODE": "1"}).stdout.strip() for s in ("1", "2", "999")}
    assert len(outs) == 1 and "" not in outs


# ------------------------------------------------------------------------------ references in terms
def test_a_reference_nested_in_a_mapping_term_blocks_a_build_until_resolved():
    sp = spec()
    t = TradeTemplate("t", sp, {**TERMS, "extras": {"spread": "@param.s"}}, allow_refs=True)
    assert t.has_refs
    with pytest.raises(ConfigError):
        t.build(H.Market(), TS)


def test_wrap_can_expose_named_attributes_as_resolved_terms_and_takes_an_allow_list():
    class Exp:
        expiry = dt.date(2024, 6, 1)

        def val(self, *, ctx):
            return 1.0

    bind = {"value": {"target": {"method": "val"}, "kwargs": {"ctx": "@ctx"}}, "notional": {"target": {"attribute": "expiry"}}}
    t = TradeTemplate.wrap(Exp(), bind=bind, schemas=SCHEMAS, resolved=("expiry",))
    assert t.build(H.Market(), TS).terms == {"expiry": dt.date(2024, 6, 1)}
    fn_bind = {"value": {"target": {"function": "spec_helpers:scalar_fn"}}}
    assert TradeTemplate.wrap(Exp(), bind=fn_bind, schemas=SCHEMAS, allow=ALLOW).spec.bindings["value"].kind == "function"
    with pytest.raises(ConfigError) as ei:
        TradeTemplate.wrap(Exp(), bind=fn_bind, schemas=SCHEMAS)  # spec_helpers is not under the default allow-list
    assert ei.value.code == "CFG-ALLOW"


# ------------------------------------------------------------------------------ second review: derived templates and the recorded factory
LABEL_SCHEMA = {"schema_version": 1, "asset_class": "swap_label", "extends": "swap", "terms": {"label": {"kind": "term", "type": "string"}}}


def label_spec():
    kit = Kit(factory=H.weird_factory, asset_class="swap_label", default_bind=H.BIND, cls=H.Weird, schema=LABEL_SCHEMA)
    return build_spec("labelled", {"factory": kit}, schemas=SCHEMAS, allow=ALLOW)


def test_with_terms_and_renamed_keep_an_escaped_literal_literal():
    """`@@ON10` is the literal `@ON10`; deriving a template re-validates its terms, and a literal must not come back as a reference."""
    t = TradeTemplate("t", label_spec(), {**TERMS, "label": "@@ON10"})
    assert t.terms["label"] == "@ON10"
    assert t.renamed("r").terms["label"] == "@ON10" and t.with_terms(notional=2e6).terms["label"] == "@ON10"
    assert t.with_terms(label="@@X").terms["label"] == "@X" and t.with_terms(label="plain").terms["label"] == "plain"
    chained = t.with_terms(notional=2e6).with_terms(maturity="5Y").renamed("q")
    assert chained.terms["label"] == "@ON10" and chained.terms["notional"] == 2e6 and chained.terms["maturity"] == "5Y", "and again, and again: each change is kept"


def test_a_literal_next_to_a_reference_survives_resolving_the_reference():
    t = TradeTemplate("t", label_spec(), {"side": "pay", "maturity": "10Y", "notional": "@signal.size", "label": "@@ON10"}, allow_refs=True)
    assert t.has_refs and t.terms["label"] == "@ON10" and t.terms["notional"] == "@signal.size"
    r = t.with_terms(notional=3e6)
    assert not r.has_refs and r.terms["notional"] == 3e6 and r.terms["label"] == "@ON10"
    assert t.renamed("r").terms["notional"] == "@signal.size", "a reference stays a reference"
    with pytest.raises(ConfigError, match="reference"):
        t.with_terms(label="@@ON10", notional="@bad")


def test_a_derived_template_does_not_share_or_alias_the_terms_it_was_derived_from():
    t = TradeTemplate("t", spec(), {**TERMS, "extras": {"a": {"b": 1}}})
    u = t.with_terms(notional=2e6)
    u.terms["extras"]["a"]["b"] = 99
    assert t.terms["extras"] == {"a": {"b": 1}} and t.with_terms(notional=3e6).terms["extras"] == {"a": {"b": 1}}
    assert u.with_terms(notional=4e6).terms["extras"] == {"a": {"b": 1}}, "the terms of a template are not the terms it derives from"
    ex = {"a": {"b": 1}}
    v = t.with_terms(extras=ex)
    ex["a"]["b"] = 7
    assert v.with_terms(notional=5e6).terms["extras"] == {"a": {"b": 1}}, "nor the mapping a caller passed to with_terms"
    written = {**TERMS, "extras": {"a": {"b": 1}}}
    w = TradeTemplate("w", spec(), written)
    written["extras"]["a"]["b"] = 5
    assert w.terms["extras"] == {"a": {"b": 1}} and w.with_terms(notional=2e6).terms["extras"] == {"a": {"b": 1}}, "nor the mapping the caller wrote"


def test_the_recorded_factory_of_a_spec_built_from_an_object_is_a_stable_path_without_an_address():
    sp = spec(factory=H.weird_kit)
    assert sp.factory_path == "spec_helpers:weird_factory" and H.weird_kit.factory_path == "spec_helpers:weird_factory"
    assert spec(factory=H.weird_factory, bind=H.BIND).factory_path == "spec_helpers:weird_factory"
    import functools

    part = functools.partial(H.weird_factory)
    assert spec(factory=part, bind=H.BIND).factory_path == "spec_helpers:weird_factory", "a partial is named after what it wraps"
    obj = spec(factory=Kit(factory=part, asset_class="swap", default_bind=H.BIND, cls=H.Weird))
    assert obj.factory_path == "spec_helpers:weird_factory"

    class Exp:
        expiry = dt.date(2024, 6, 1)

        def val(self, *, ctx):
            return 1.0

    bind = {"value": {"target": {"method": "val"}, "kwargs": {"ctx": "@ctx"}}, "notional": {"target": {"attribute": "expiry"}}}
    copies = TradeTemplate.wrap(Exp(), bind=bind, schemas=SCHEMAS)
    assert copies.spec.factory_path == "pricebt.contracts.spec:_Copies"
    for p in (sp.factory_path, copies.spec.factory_path):
        assert "0x" not in p and " at " not in p and "<" not in p.split(":")[0]



def test_a_template_holding_only_a_literal_at_string_has_no_references_and_builds():
    t = TradeTemplate("t", label_spec(), {**TERMS, "label": "@@ON10"})
    assert not t.has_refs and t.ref_terms == {} and t.terms["label"] == "@ON10"
    t.build(H.Market(4.0), TS)  # a literal `@ON10` is not an 'unresolved reference'
    r = TradeTemplate("r", label_spec(), {"side": "@signal.dir", "maturity": "10Y", "notional": "@trigger.scaling", "label": "@@ON10"}, allow_refs=True)
    assert r.has_refs and r.ref_terms == {"side": "@signal.dir", "notional": "@trigger.scaling"}
    with pytest.raises(ConfigError, match="notional.*side|side.*notional") as ei:
        r.build(H.Market(4.0), TS)
    assert "label" not in str(ei.value), "the literal is not listed among the unresolved references"


def test_the_action_side_resolution_leaves_a_literal_at_string_next_to_a_signal_reference_alone():
    from types import SimpleNamespace

    from pricebt.strategy.actions import resolve_template_refs

    t = TradeTemplate("t", label_spec(), {"side": "pay", "maturity": "10Y", "notional": "@signal.size", "label": "@@ON10"}, allow_refs=True)
    view = SimpleNamespace(signal=lambda name: 2.5e6)
    r = resolve_template_refs(t, view, SimpleNamespace())
    assert r.terms["notional"] == 2.5e6 and r.terms["label"] == "@ON10", "the literal is not looked up as a signal (`ON10`)"
    plain = TradeTemplate("t", label_spec(), {"side": "pay", "maturity": "10Y", "notional": 1e6, "label": "@@ON10"}, allow_refs=True)
    assert resolve_template_refs(plain, view, SimpleNamespace()) is plain
