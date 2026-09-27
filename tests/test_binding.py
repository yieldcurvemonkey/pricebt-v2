"""Bindings (spec B1-B3, Z3): schema name -> binding -> callable -> exactly the arguments the binding declares. No inference by name."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd
import pytest

from pricebt.contracts.binding import Binding, Env, call_binding, parse_binding, validate_signature
from pricebt.errors import ConfigError, MethodCallError

pytestmark = pytest.mark.core


class Recorder:
    """A library-shaped object whose names match NOTHING in a schema; it records exactly what it is called with."""

    def __init__(self):
        self.calls = []

    def zzz(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return 41.0

    def other(self, *, q):
        return q

    value_attr = 7.0


def env(**over):
    base = dict(pricer=SimpleNamespace(curve="C", fx=None, nested=SimpleNamespace(k=3)), ctx=SimpleNamespace(ts="T0", prev_pricer="P0"),
                instrument=Recorder(), terms={"notional": 5.0, "maturity": "10Y"}, state={"n": 2})
    base.update(over)
    return Env(**base)


def parse(raw, name="dv01"):
    return parse_binding(name, raw)


# -------------------------------------------------------------------------------- targets
def test_method_target_calls_the_configured_name_with_exactly_the_declared_kwargs():
    e = env()
    b = parse({"target": {"method": "zzz"}, "kwargs": {"alpha": 1, "beta": "x"}})
    assert call_binding(b, e) == 41.0
    assert e.instrument.calls == [((), {"alpha": 1, "beta": "x"})]  # nothing injected: not the pricer, not the ctx, not the terms


def test_positional_args_and_keyword_args_are_passed_as_written():
    e = env()
    call_binding(parse({"target": {"method": "zzz"}, "args": [1, 2], "kwargs": {"k": 3}}), e)
    assert e.instrument.calls == [((1, 2), {"k": 3})]


def test_function_target_resolves_a_dotted_path_under_the_allow_list_and_calls_it():
    b = parse({"target": {"function": "pricebt.testing.toys:years_between"}, "args": ["@ctx.ts", "@ctx.prev_pricer"]}, "value")
    e = env(ctx=SimpleNamespace(ts=pd.Timestamp("2024-01-02"), prev_pricer=pd.Timestamp("2025-01-02")))
    assert call_binding(b, e) == pytest.approx(366 / 365.25)  # 2024 is a leap year: 2024-01-02 -> 2025-01-02 is 366 days


def test_function_target_outside_the_allow_list_is_a_config_error():
    with pytest.raises(ConfigError) as ei:
        parse({"target": {"function": "os:getcwd"}})
    assert ei.value.code == "CFG-ALLOW"


def test_pricer_method_target_calls_a_method_of_the_pricer_with_explicit_arguments():
    pricer = SimpleNamespace(npv=lambda inst, scale=1.0: inst.value_attr * scale)
    b = parse({"target": {"pricer_method": "npv"}, "args": ["@instrument"], "kwargs": {"scale": 2.0}})
    assert call_binding(b, env(pricer=pricer)) == 14.0


def test_attribute_target_reads_an_attribute_and_never_calls_it():
    e = env()
    assert call_binding(parse({"target": {"attribute": "value_attr"}}), e) == 7.0
    got = call_binding(parse({"target": {"attribute": "zzz"}}), e)  # a callable attribute is returned, not invoked
    assert callable(got) and e.instrument.calls == []


def test_missing_method_is_an_error_naming_the_binding_the_target_and_the_public_names():
    with pytest.raises(MethodCallError) as ei:
        call_binding(parse({"target": {"method": "convexity"}}, "gamma"), env())
    msg = str(ei.value)
    assert "gamma" in msg and "convexity" in msg and "zzz" in msg  # binding name, requested target, what the object does offer


# -------------------------------------------------------------------------------- references
def test_references_resolve_from_pricer_ctx_instrument_terms_and_state():
    e = env()
    b = parse({"target": {"method": "zzz"}, "kwargs": {"a": "@pricer.curve", "b": "@pricer.nested.k", "c": "@ctx.ts", "d": "@terms.notional", "e": "@state.n", "f": "@instrument"}})
    call_binding(b, e)
    (_, kw), = e.instrument.calls
    assert kw == {"a": "C", "b": 3, "c": "T0", "d": 5.0, "e": 2, "f": e.instrument}


def test_whole_pricer_and_whole_ctx_references():
    e = env()
    call_binding(parse({"target": {"method": "zzz"}, "kwargs": {"p": "@pricer", "c": "@ctx"}}), e)
    (_, kw), = e.instrument.calls
    assert kw["p"] is e.pricer and kw["c"] is e.ctx


@pytest.mark.parametrize("bad", ["@market", "@pricer.", "@terms", "@state", "@ctx..ts", "@", "@Terms.x"])
def test_malformed_or_unknown_reference_roots_are_rejected_at_parse_time(bad):
    with pytest.raises(ConfigError) as ei:
        parse({"target": {"method": "zzz"}, "kwargs": {"a": bad}})
    assert ei.value.code == "CFG-REF"


def test_a_double_at_escapes_a_literal_string_that_starts_with_at():
    e = env()
    call_binding(parse({"target": {"method": "zzz"}, "kwargs": {"a": "@@notaref"}}), e)
    assert e.instrument.calls[0][1] == {"a": "@notaref"}


def test_a_reference_to_a_missing_pricer_attribute_is_an_error_not_a_silent_none():
    with pytest.raises(MethodCallError) as ei:
        call_binding(parse({"target": {"method": "zzz"}, "kwargs": {"a": "@pricer.nope"}}), env())
    assert "pricer.nope" in str(ei.value)


def test_a_reference_to_a_missing_term_or_state_key_is_an_error():
    for ref in ("@terms.absent", "@state.absent"):
        with pytest.raises(MethodCallError):
            call_binding(parse({"target": {"method": "zzz"}, "kwargs": {"a": ref}}), env())


def test_context_the_binding_did_not_ask_for_is_not_passed():
    """Z3/B7: no injection by parameter name. A method that WOULD accept `ctx` and `curves` does not receive them unless the binding names them."""

    class Greedy:
        def m(self, ctx=None, curves=None):
            return (ctx, curves)

    out = call_binding(parse({"target": {"method": "m"}}), env(instrument=Greedy()))
    assert out == (None, None)


def test_references_nested_in_lists_and_mappings_resolve():
    e = env()
    call_binding(parse({"target": {"method": "zzz"}, "kwargs": {"a": ["@terms.maturity", {"n": "@state.n"}]}}), e)
    assert e.instrument.calls[0][1] == {"a": ["10Y", {"n": 2}]}


# -------------------------------------------------------------------------------- post-processing
@pytest.mark.parametrize("raw,expect", [
    ({"scale": 100.0}, 4100.0),
    ({"scale": 100.0, "offset": 1.0}, 4101.0),
    ({"sign": -1.0}, -41.0),
    ({"scale": 2.0, "offset": 1.0, "sign": -1.0}, -83.0),
])
def test_unit_and_sign_conversion_is_sign_times_scale_x_plus_offset(raw, expect):
    assert call_binding(parse({"target": {"method": "zzz"}, **raw}), env()) == expect


def test_scale_and_sign_apply_to_every_value_of_a_dict_result_and_keep_the_keys():
    class D:
        def ladder(self):
            return {"2Y": 1.0, "10Y": -2.0}

    out = call_binding(parse({"target": {"method": "ladder"}, "scale": 10.0, "sign": -1.0}), env(instrument=D()))
    assert out == {"2Y": -10.0, "10Y": 20.0}


def test_reducers_float_real_and_identity():
    class Dualish:
        real = 3.5

    class R:
        def a(self):
            return Dualish()

    assert call_binding(parse({"target": {"method": "a"}, "reduce": "real"}), env(instrument=R())) == 3.5
    assert call_binding(parse({"target": {"method": "zzz"}, "reduce": "float"}), env()) == 41.0
    assert isinstance(call_binding(parse({"target": {"method": "zzz"}, "reduce": "float"}), env()), float)


def test_series_to_tenor_dict_reducer_normalises_keys_and_values():
    class S:
        def lad(self):
            return pd.Series({"2y": 1, "10Y": 2.5}, dtype=float)

    out = call_binding(parse({"target": {"method": "lad"}, "reduce": "series_to_tenor_dict"}), env(instrument=S()))
    assert out == {"2Y": 1.0, "10Y": 2.5} and all(isinstance(v, float) for v in out.values())


def test_series_to_tenor_dict_rejects_a_key_that_is_not_a_tenor():
    class S:
        def lad(self):
            return pd.Series({"2Y": 1.0, "front": 2.0})

    with pytest.raises(MethodCallError) as ei:
        call_binding(parse({"target": {"method": "lad"}, "reduce": "series_to_tenor_dict"}), env(instrument=S()))
    assert "front" in str(ei.value)


def test_dict_of_floats_reducer_coerces_values_and_rejects_non_finite():
    class S:
        def lad(self):
            return {"5Y": 1, "10Y": float("nan")}

    with pytest.raises(MethodCallError):
        call_binding(parse({"target": {"method": "lad"}, "reduce": "dict_of_floats"}), env(instrument=S()))


def test_a_dotted_path_reducer_is_allowed_under_the_allow_list_and_an_unknown_name_is_rejected():
    b = parse({"target": {"method": "zzz"}, "reduce": "pricebt.contracts.evaluate:scalar_of"})
    assert b.reducer is not None
    with pytest.raises(ConfigError):
        parse({"target": {"method": "zzz"}, "reduce": "no_such_reducer"})


# -------------------------------------------------------------------------------- structure
@pytest.mark.parametrize("raw", [
    {},  # no target
    {"target": {}},  # empty target
    {"target": {"method": "a", "function": "b:c"}},  # two kinds
    {"target": {"attribute": "a"}, "args": [1]},  # an attribute is not called: args make no sense
    {"target": {"method": "a"}, "surprise": 1},  # unknown key
    {"target": {"method": "a"}, "scale": "x"},
    {"target": {"method": "a"}, "args": "nope"},
    {"target": {"method": "a"}, "kwargs": [1]},
])
def test_malformed_bindings_are_config_errors_naming_the_binding(raw):
    with pytest.raises(ConfigError) as ei:
        parse(raw, "dv01")
    assert "dv01" in str(ei.value)


def test_a_bare_string_is_shorthand_for_a_method_target_with_no_arguments():
    b = parse("zzz")
    assert b.kind == "method" and b.target == "zzz" and b.args == () and dict(b.kwargs) == {}


def test_bindings_are_immutable():
    b = parse({"target": {"method": "zzz"}, "kwargs": {"a": 1}})
    with pytest.raises(Exception):
        b.target = "other"  # frozen dataclass
    assert isinstance(b, Binding)


# -------------------------------------------------------------------------------- validation (B4: validation, not injection)
def test_signature_validation_accepts_a_call_the_callable_can_bind_and_rejects_one_it_cannot():
    def fn(self_less, *, alpha, beta=2):
        return alpha

    good = parse({"target": {"function": "pricebt.testing.toys:years_between"}, "args": [1, 2]})
    assert validate_signature(good, good.fn) is None
    ok = Binding("m", "method", "zzz", (), {"alpha": 1})
    assert validate_signature(ok, lambda *, alpha, beta=2: alpha) is None
    msg = validate_signature(Binding("m", "method", "zzz", (), {"gamma_": 1}), lambda *, alpha, beta=2: alpha)
    assert msg is not None and "gamma_" in msg


def test_signature_validation_treats_references_as_opaque_placeholders():
    b = Binding("m", "method", "zzz", (), {"curves": "@pricer.curve"})
    assert validate_signature(b, lambda *, curves: curves) is None
    assert validate_signature(b, lambda *, other: other) is not None


# ------------------------------------------------------------------------------ adversarial-review findings (cycle 1)
@pytest.mark.parametrize("bad", ["@instrument.__class__", "@instrument.__class__.__init__.__globals__", "@pricer._private", "@pricer\n", "@ctx.nope", "@ctx.__dict__", "@pricer.1x", "@terms.__class__"])
def test_the_reference_grammar_is_closed_no_private_names_no_trailing_newline_no_unknown_ctx_field(bad):
    with pytest.raises(ConfigError) as ei:
        parse({"target": {"method": "zzz"}, "kwargs": {"a": bad}})
    assert ei.value.code == "CFG-REF"


@pytest.mark.parametrize("field", ["ts", "pricer", "pricers", "prev_ts", "prev_pricer", "entry_ts", "entry_pricer", "state", "cache", "params"])
def test_every_mark_context_field_is_referenceable(field):
    assert parse({"target": {"method": "zzz"}, "kwargs": {"a": f"@ctx.{field}"}}).kwargs["a"] == f"@ctx.{field}"


@pytest.mark.parametrize("kind", ["method", "attribute", "pricer_method"])
@pytest.mark.parametrize("name", ["__init__", "_private", "a.b", "1x", "x y", ""])
def test_a_target_name_must_be_a_public_identifier_so_no_dunder_can_be_called(kind, name):
    with pytest.raises(ConfigError) as ei:
        parse({"target": {kind: name}})
    assert ei.value.code == "CFG-BINDING"


def test_a_reference_to_a_root_the_call_does_not_provide_is_an_error_not_a_none():
    with pytest.raises(MethodCallError) as ei:
        call_binding(parse({"target": {"method": "zzz"}, "kwargs": {"c": "@ctx", "p": "@pricer"}}), Env(instrument=Recorder()))
    assert "does not provide" in str(ei.value)


def test_keys_declares_the_exact_result_keys_of_a_ladder_and_must_be_canonical_unique_tenors():
    assert parse({"target": {"method": "zzz"}, "keys": ["2Y", "10Y"]}).keys == ("2Y", "10Y")
    for bad in (["2Y", "2Y"], ["2y"], ["banana"], "2Y", None, [2]):
        with pytest.raises(ConfigError) as ei:
            parse({"target": {"method": "zzz"}, "keys": bad})
        assert ei.value.code == "CFG-BINDING"


def test_a_loaded_binding_is_isolated_from_the_dict_it_was_parsed_from_and_read_only():
    raw = {"target": {"method": "zzz"}, "kwargs": {"buckets": ["2Y", "10Y"]}, "args": [["x"]]}
    b = parse(raw)
    raw["kwargs"]["buckets"].append("JUNK")
    raw["args"][0].append("JUNK")
    assert b.kwargs["buckets"] == ["2Y", "10Y"] and b.args == (["x"],)
    with pytest.raises(TypeError):
        b.kwargs["injected"] = "@pricer"
    with pytest.raises(TypeError):
        b.kwargs.update(x=1)


def test_a_loaded_binding_pickles_and_deep_copies():
    import pickle

    b = parse({"target": {"method": "zzz"}, "kwargs": {"a": 1}})
    for c in (pickle.loads(pickle.dumps(b)), copy.deepcopy(b)):
        assert c.kwargs["a"] == 1 and c.target == "zzz"
        with pytest.raises(TypeError):
            c.kwargs["b"] = 2


@pytest.mark.parametrize("over", [{"scale": float("nan")}, {"scale": float("inf")}, {"scale": 0}, {"offset": float("nan")}, {"sign": 0}, {"sign": 100}, {"sign": 0.5}])
def test_unit_conversion_numbers_must_be_finite_scale_non_zero_sign_plus_or_minus_one(over):
    with pytest.raises(ConfigError) as ei:
        parse({"target": {"method": "zzz"}, **over})
    assert ei.value.code == "CFG-BINDING"


@pytest.mark.parametrize("red", ["float", "real"])
def test_float_and_real_reducers_reject_non_finite_results(red):
    class N:
        real = float("nan")

        def __float__(self):
            return float("nan")

        def a(self):
            return self

    with pytest.raises(MethodCallError) as ei:
        call_binding(parse({"target": {"method": "a"}, "reduce": red}), env(instrument=N()))
    assert "dv01" in str(ei.value) and "not finite" in str(ei.value)


def test_reducers_must_take_exactly_one_argument_and_be_callable():
    for path in ("pricebt.testing.toys:years_between", "pricebt.contracts.evaluate:vector_scale"):
        with pytest.raises(ConfigError) as ei:
            parse({"target": {"method": "zzz"}, "reduce": path})
        assert ei.value.code == "CFG-REDUCER"
    with pytest.raises(ConfigError) as ei:
        parse({"target": {"method": "zzz"}, "reduce": "pricebt.contracts.binding:KINDS"})
    assert ei.value.code == "CFG-REDUCER"


def test_a_reducer_failing_at_call_time_names_the_binding_and_the_error():
    class Bad:
        def a(self):
            return "not a number"

    with pytest.raises(MethodCallError) as ei:
        call_binding(parse({"target": {"method": "a"}, "scale": 2.0}, "gamma"), env(instrument=Bad()))
    assert "gamma" in str(ei.value) and "post-processing" in str(ei.value)


def test_series_to_tenor_dict_rejects_duplicates_instead_of_collapsing_them():
    class S:
        def dup_case(self):
            return pd.Series({"2y": 1.0, "2Y": 9.0})

        def dup_label(self):
            return pd.Series([1.0, 9.0], index=["2Y", "2Y"])

    for m in ("dup_case", "dup_label"):
        with pytest.raises(MethodCallError) as ei:
            call_binding(parse({"target": {"method": m}, "reduce": "series_to_tenor_dict"}), env(instrument=S()))
        assert "duplicate" in str(ei.value)


def test_dict_of_floats_rejects_a_duplicate_series_label():
    class S:
        def a(self):
            return pd.Series([1.0, 9.0], index=["x", "x"])

    with pytest.raises(MethodCallError):
        call_binding(parse({"target": {"method": "a"}, "reduce": "dict_of_floats"}), env(instrument=S()))


def test_series_to_tenor_dict_only_accepts_a_mapping_or_a_series():
    class S:
        def a(self):
            return [1.0, 2.0]

    with pytest.raises(MethodCallError):
        call_binding(parse({"target": {"method": "a"}, "reduce": "series_to_tenor_dict"}), env(instrument=S()))


def test_kwargs_keys_must_be_strings():
    with pytest.raises(ConfigError):
        parse({"target": {"method": "zzz"}, "kwargs": {1: "x"}})
