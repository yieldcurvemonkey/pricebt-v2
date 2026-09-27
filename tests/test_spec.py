"""Instrument specs, kits, trade templates (spec B4-B6, T1-T5): strict bindings, terms in, resolved terms out, the mismatched-names proof."""
from __future__ import annotations

import copy
import datetime as dt

import pandas as pd
import pytest

from pricebt.contracts.binding import Env, call_binding
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Built, InstrumentSpec, Kit, TradeTemplate, build_spec, conventions_digest
from pricebt.errors import ConfigError

import spec_helpers as H

pytestmark = pytest.mark.core

ALLOW = ("pricebt", "spec_helpers")
SCHEMAS = SchemaRegistry.default()
TERMS = {"side": "pay", "maturity": "10Y", "notional": 1e7}


def spec(raw=None, **over):
    base = {"asset_class": "swap", "factory": H.weird_kit, "conventions": {"day_count": "act360"}}
    base.update(raw or {})
    base.update(over)
    return build_spec("usd_sofr_ois", base, schemas=SCHEMAS, allow=ALLOW)


def env_for(sp, built, market):
    return Env(pricer=market, ctx=None, instrument=built.obj, terms=built.terms, state={})


# ------------------------------------------------------------------------------ the mismatched-names proof (B5, SC4, SC5)
def test_a_swap_with_arbitrary_method_and_parameter_names_conforms_by_binding_alone():
    sp = spec()
    assert isinstance(sp, InstrumentSpec) and sp.asset_class == "swap"
    built = TradeTemplate("t", sp, TERMS).build(H.Market(4.0), pd.Timestamp("2024-01-02 17:00", tz="America/New_York"))
    env = env_for(sp, built, H.Market(4.0))
    assert call_binding(sp.bindings["value"], env) == pytest.approx(1e7 * (4.0 - 4.0) * 1e-2)  # par resolved by the factory
    assert call_binding(sp.bindings["dv01"], env) == pytest.approx(1e7 * 1e-4)
    assert call_binding(sp.bindings["gamma"], env) == 1.0  # bump_bp literal 2.0 in the binding
    lad = call_binding(sp.bindings["delta_ladder"], env)
    assert lad == {"2Y": pytest.approx(500.0), "10Y": pytest.approx(500.0)} and sum(lad.values()) == pytest.approx(1000.0)  # plain dict[str, float] via a reducer
    assert built.obj.calls == ["zz_value", "pp01", "curv", "lad"]  # the same-name traps were never touched


def test_the_same_toy_with_no_bindings_is_rejected_and_nothing_is_resolved_implicitly():
    with pytest.raises(ConfigError) as ei:
        spec(factory=H.weird_kit_no_defaults)
    assert ei.value.code == "CFG-REQUIRED-BINDING"
    msg = str(ei.value)
    for name in ("value", "dv01", "gamma", "rate", "delta_ladder"):
        assert name in msg
    # ... even though the class HAS methods called value / dv01 / gamma / delta_ladder: presence of a same-name method is not a binding


def test_did_you_mean_draws_on_the_classes_public_names_and_the_schemas_synonyms():
    class Plain:
        def npv(self): ...
        def pv01(self): ...
        def convexity(self): ...
        def par(self): ...
        def ladder(self): ...

    kit = Kit(factory=lambda p, t, *, terms, conventions: Built(Plain(), terms), asset_class="swap", default_bind={}, cls=Plain)
    with pytest.raises(ConfigError) as ei:
        spec(factory=kit)
    msg = str(ei.value)
    assert "gamma" in msg and "convexity" in msg  # synonym: gamma <- convexity
    assert "dv01" in msg and "pv01" in msg
    assert "delta_ladder" in msg and "ladder" in msg


def test_a_user_bind_overrides_one_name_of_the_kits_default_block_and_keeps_the_rest():
    sp = spec({"bind": {"dv01": {"target": {"method": "pp01"}, "kwargs": {"market": "@pricer"}, "scale": 2.0}}})
    assert sp.bindings["dv01"].scale == 2.0 and sp.bindings["gamma"].kwargs["bump_bp"] == 2.0
    built = TradeTemplate("t", sp, TERMS).build(H.Market(), pd.Timestamp("2024-01-02", tz="UTC"))
    assert call_binding(sp.bindings["dv01"], env_for(sp, built, H.Market())) == pytest.approx(2e3)


# ------------------------------------------------------------------------------ strict validation (B4)
def test_binding_a_name_the_schema_does_not_declare_is_an_error_with_a_suggestion():
    with pytest.raises(ConfigError) as ei:
        spec({"bind": {"dv011": {"target": {"method": "pp01"}}}})
    assert ei.value.code == "CFG-UNKNOWN-BINDING" and "dv01" in str(ei.value)


def test_a_derived_measure_takes_no_binding():
    with pytest.raises(ConfigError) as ei:
        spec({"bind": {"pv": {"target": {"method": "zz_value"}}}})
    assert ei.value.code == "CFG-UNKNOWN-BINDING" and "derived" in str(ei.value)


def test_a_method_target_missing_from_the_known_class_is_a_load_error_naming_what_exists():
    with pytest.raises(ConfigError) as ei:
        spec({"bind": {"dv01": {"target": {"method": "pv01"}, "kwargs": {"market": "@pricer"}}}})
    assert ei.value.code == "CFG-BINDING" and "pp01" in str(ei.value)


def test_declared_kwargs_the_target_cannot_bind_are_a_load_error_validation_not_injection():
    with pytest.raises(ConfigError) as ei:
        spec({"bind": {"dv01": {"target": {"method": "pp01"}, "kwargs": {"market": "@pricer", "extra": 1}}}})
    assert ei.value.code == "CFG-BINDING" and "extra" in str(ei.value)
    with pytest.raises(ConfigError):
        spec({"bind": {"dv01": {"target": {"method": "pp01"}}}})  # `market` is required by pp01 and the binding does not supply it: nothing is injected


def test_static_and_class_methods_are_signature_checked_without_a_bound_self():
    class S:
        @staticmethod
        def st(x):
            return x

        @classmethod
        def cm(cls, x):
            return x

        def value(self, *, ctx): ...

    bind = {"value": {"target": {"method": "value"}, "kwargs": {"ctx": "@ctx"}}}
    for tgt in ("st", "cm"):
        ok = spec(factory=Kit(factory=lambda p, t, *, terms, conventions: Built(S(), terms), asset_class="generic", default_bind=bind, cls=S), asset_class="generic",
                  bind={"notional": {"target": {"method": tgt}, "kwargs": {"x": 1}}})
        assert ok.bindings["notional"].target == tgt
        with pytest.raises(ConfigError):
            spec(factory=Kit(factory=lambda p, t, *, terms, conventions: Built(S(), terms), asset_class="generic", default_bind=bind, cls=S), asset_class="generic",
                 bind={"notional": {"target": {"method": tgt}, "kwargs": {"y": 1}}})


def test_a_spec_whose_schema_declares_no_terms_rejects_terms():
    t = TradeTemplate.wrap(H.Weird(1.0, 1, 4.0), bind={"value": {"target": {"method": "zz_value"}, "kwargs": {"market": "@pricer"}}}, schemas=SCHEMAS)
    with pytest.raises(ConfigError) as ei:
        TradeTemplate("x", t.spec, {"notional": 1.0})
    assert ei.value.code == "CFG-TERMS"


def test_a_function_target_is_signature_checked_at_load():
    with pytest.raises(ConfigError) as ei:
        spec({"bind": {"dv01": {"target": {"function": "pricebt.testing.toys:years_between"}, "kwargs": {"nope": 1}}}})
    assert ei.value.code == "CFG-BINDING"


def test_required_layers_must_be_bound_at_load_like_any_other_required_name():
    no_layers = {k: v for k, v in H.BIND.items() if k not in ("carry", "roll", "delta", "convexity")}
    with pytest.raises(ConfigError) as ei:
        spec(factory=H.weird_factory, bind=no_layers)
    assert ei.value.code == "CFG-REQUIRED-BINDING" and "carry" in str(ei.value) and "convexity" in str(ei.value)


def test_instrument_level_layers_must_be_declared_layers_of_the_asset_class():
    with pytest.raises(ConfigError) as ei:
        spec({"layers": ["nonesuch"]})
    assert ei.value.code == "CFG-UNKNOWN-BINDING" and "nonesuch" in str(ei.value)
    assert spec({"layers": ["carry", "roll"]}).layers == ("carry", "roll")


def test_the_factory_may_be_a_dotted_path_under_the_allow_list_and_is_imported_only_then():
    sp = spec(factory="spec_helpers:weird_kit")
    assert sp.factory_path == "spec_helpers:weird_kit"
    with pytest.raises(ConfigError) as ei:
        spec(factory="os:getcwd")
    assert ei.value.code == "CFG-ALLOW"


def test_a_plain_callable_factory_needs_every_required_binding_written_out():
    sp = spec(factory=H.weird_factory, bind=H.BIND)
    assert set(sp.bindings) == set(H.BIND)
    with pytest.raises(ConfigError):
        spec(factory=H.weird_factory)  # a callable is not a kit: no defaults


def test_asset_class_comes_from_the_kit_and_must_agree_if_also_given():
    assert spec({"asset_class": None}).asset_class == "swap"
    with pytest.raises(ConfigError) as ei:
        spec(asset_class="bond")
    assert ei.value.code == "CFG-SCHEMA" and "swap" in str(ei.value)
    with pytest.raises(ConfigError) as ei:
        spec(factory=H.weird_factory, bind=H.BIND, asset_class=None)  # a plain callable must say what it is
    assert ei.value.code == "CFG-REQUIRED"


def test_the_spec_keeps_its_own_copy_of_the_conventions_it_was_built_from():
    conv = {"day_count": "act360"}
    sp = spec(conventions=conv)
    digest = sp.conventions_digest
    conv["day_count"] = "changed after the fact"
    assert sp.conventions == {"day_count": "act360"} and sp.conventions_digest == digest


def test_unknown_spec_keys_are_errors():
    with pytest.raises(ConfigError) as ei:
        spec(surprise=1)
    assert ei.value.code == "CFG-UNKNOWN-KEY"


# ------------------------------------------------------------------------------ terms in, resolved terms out (T1-T5)
def test_a_trade_template_validates_terms_against_the_asset_schema_and_normalises_the_side():
    sp = spec()
    t = TradeTemplate("t", sp, {"side": "sell", "maturity": "5Y", "notional": 2e7})
    assert t.terms["side"] == "receive" and t.direction == -1 and t.terms["effective"] == "spot" and t.terms["fixed_rate"] == "par"
    with pytest.raises(ConfigError) as ei:
        TradeTemplate("t", sp, {"side": "pay", "notional": 1e7})
    assert ei.value.code == "CFG-TERMS"


def test_one_spec_serves_pay_and_receive_at_several_tenors():
    sp = spec()
    ts = [TradeTemplate(f"{s}{m}", sp, {"side": s, "maturity": m, "notional": 1e6}) for s in ("pay", "receive") for m in ("2Y", "10Y")]
    assert {t.spec.name for t in ts} == {"usd_sofr_ois"} and [t.direction for t in ts] == [1, 1, -1, -1]


def test_the_factory_receives_terms_plus_direction_and_the_verbatim_conventions_and_nothing_else():
    seen = {}

    def factory(pricer, ts, *, terms, conventions):
        seen.update(pricer=pricer, ts=ts, terms=dict(terms), conventions=conventions)
        return Built(H.Weird(1.0, 1, 4.0), {**H.resolve_dates(terms, ts), "fixed_rate": 4.0})

    sp = spec(factory=Kit(factory=factory, asset_class="swap", default_bind=H.BIND, cls=H.Weird))
    m = H.Market()
    ts = pd.Timestamp("2024-01-02", tz="UTC")
    TradeTemplate("t", sp, {**TERMS, "effective": "1Y", "fixed_rate": "par"}).build(m, ts)
    assert seen["pricer"] is m and seen["ts"] == ts
    assert seen["terms"] == {"side": "pay", "effective": "1Y", "maturity": "10Y", "notional": 1e7, "fixed_rate": "par", "direction": 1}
    assert seen["conventions"] == {"day_count": "act360"}


def test_resolved_terms_are_returned_merged_over_the_given_ones():
    sp = spec()
    built = TradeTemplate("t", sp, TERMS).build(H.Market(3.75), pd.Timestamp("2024-01-02", tz="UTC"))
    assert built.terms["fixed_rate"] == 3.75 and built.terms["direction"] == 1  # `par` was resolved by the library, the direction sign is recorded
    assert built.terms["effective"] == dt.date(2024, 1, 4) and built.terms["maturity"] == dt.date(2034, 1, 4)  # so were `spot` and `10Y`
    assert built.terms["side"] == "pay" and built.terms["notional"] == 1e7  # terms the factory did not touch are kept


def test_a_factory_may_return_only_what_it_resolved_and_the_rest_is_kept_with_the_direction():
    def minimal_factory(pricer, ts, *, terms, conventions):
        return Built(H.Weird(1.0, 1, 4.0), {"effective": dt.date(2024, 1, 4), "maturity": dt.date(2034, 1, 4), "fixed_rate": 4.1})

    sp = spec(factory=Kit(factory=minimal_factory, asset_class="swap", default_bind=H.BIND, cls=H.Weird))
    built = TradeTemplate("t", sp, {**TERMS, "side": "receive"}).build(H.Market(), pd.Timestamp("2024-01-02", tz="UTC"))
    assert built.terms == {"side": "receive", "notional": 1e7, "effective": dt.date(2024, 1, 4), "maturity": dt.date(2034, 1, 4), "fixed_rate": 4.1, "direction": -1}


def test_an_unresolved_par_token_or_relative_tenor_in_the_returned_terms_is_an_error_T4():
    def lazy(pricer, ts, *, terms, conventions):
        return Built(H.Weird(1.0, 1, 4.0), H.resolve_dates(terms, ts))  # resolves the dates but returns `par` unresolved

    sp = spec(factory=Kit(factory=lazy, asset_class="swap", default_bind=H.BIND, cls=H.Weird))
    with pytest.raises(ConfigError) as ei:
        TradeTemplate("t", sp, TERMS).build(H.Market(), pd.Timestamp("2024-01-02", tz="UTC"))
    assert ei.value.code == "CFG-TERMS-UNRESOLVED" and "fixed_rate" in str(ei.value)


def test_a_factory_that_does_not_return_built_is_an_error():
    sp = spec(factory=Kit(factory=lambda p, t, *, terms, conventions: H.Weird(1.0, 1, 4.0), asset_class="swap", default_bind=H.BIND, cls=H.Weird))
    with pytest.raises(ConfigError):
        TradeTemplate("t", sp, {**TERMS, "fixed_rate": 4.0}).build(H.Market(), pd.Timestamp("2024-01-02", tz="UTC"))


def test_with_terms_revalidates_and_leaves_the_original_untouched():
    sp = spec()
    t = TradeTemplate("t", sp, TERMS)
    u = t.with_terms(side="receive", maturity="30Y")
    assert (u.terms["side"], u.terms["maturity"], u.direction) == ("receive", "30Y", -1) and t.terms["side"] == "pay"
    with pytest.raises(ConfigError):
        t.with_terms(colour="red")


def test_references_are_allowed_in_action_terms_and_block_a_build_until_resolved():
    sp = spec()
    t = TradeTemplate("t", sp, {"side": "@signal.direction", "maturity": "10Y", "notional": "@trigger.scaling"}, allow_refs=True)
    assert t.has_refs and t.direction is None
    with pytest.raises(ConfigError):
        t.build(H.Market(), pd.Timestamp("2024-01-02", tz="UTC"))
    with pytest.raises(ConfigError):
        TradeTemplate("t", sp, {"side": "@signal.direction", "maturity": "10Y", "notional": 1e6})  # not allowed outside an action


# ------------------------------------------------------------------------------ conventions, roles, copies
def test_conventions_are_hashed_into_a_stable_digest_independent_of_key_order():
    a = conventions_digest({"day_count": "act360", "calendar": "x", "n": 2})
    assert a == conventions_digest({"n": 2, "calendar": "x", "day_count": "act360"})
    assert a != conventions_digest({"day_count": "act365", "calendar": "x", "n": 2})
    assert spec().conventions_digest == conventions_digest({"day_count": "act360"})


def test_pricer_roles_default_to_primary_and_extra_roles_are_declared_in_the_spec():
    assert spec().roles() == {"primary": "primary"}
    assert spec(pricer="rates", pricers={"funding": "sofr"}).roles() == {"primary": "rates", "funding": "sofr"}


def test_templates_and_specs_survive_deepcopy_and_pickle():
    import pickle

    sp = spec()
    t = TradeTemplate("t", sp, TERMS)
    for clone in (copy.deepcopy(t), pickle.loads(pickle.dumps(TradeTemplate("t", spec(factory="spec_helpers:weird_kit"), TERMS)))):
        assert clone.terms == t.terms and clone.spec.name == "usd_sofr_ois"


def test_wrapping_a_ready_made_object_gives_each_position_its_own_copy_and_uses_the_generic_schema():
    class Mark:
        def __init__(self):
            self.n = 0

        def val(self, *, ctx):
            return 1.0

    bind = {"value": {"target": {"method": "val"}, "kwargs": {"ctx": "@ctx"}}}
    obj = Mark()
    t = TradeTemplate.wrap(obj, bind=bind, name="m", schemas=SCHEMAS)
    b1, b2 = t.build(H.Market(), pd.Timestamp("2024-01-02", tz="UTC")), t.build(H.Market(), pd.Timestamp("2024-01-02", tz="UTC"))
    assert b1.obj is not obj and b1.obj is not b2.obj and t.spec.asset_class == "generic" and b1.terms == {}
    with pytest.raises(ConfigError):
        TradeTemplate.wrap(obj, bind={}, name="m", schemas=SCHEMAS)  # `value` is required and unbound
