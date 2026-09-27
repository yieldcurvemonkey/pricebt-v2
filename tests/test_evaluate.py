"""Evaluating a spec's bindings for one position (spec S4, S7, L1, B1): value, derived measures, scalar and dict-ladder measures, layers, vector maths."""
from __future__ import annotations


import pandas as pd
import pytest

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import (
    check_ladder, evaluate_layer, evaluate_measure, evaluate_value, is_vector, scalar_of, tenor_sort_key, vector_add, vector_of, vector_scale,
)
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Kit, TradeTemplate, build_spec, Built
from pricebt.errors import ConfigError, MeasureError, MethodCallError
from pricebt.pricable import Valuation

import spec_helpers as H

pytestmark = pytest.mark.core

SCHEMAS = SchemaRegistry.default()
TS = pd.Timestamp("2024-01-02 17:00", tz="America/New_York")
TERMS = {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": 4.0}


def build(bind_over=None, layers=(), kit=None):
    sp = build_spec("s", {"factory": kit or H.weird_kit, "bind": bind_over or {}, "layers": list(layers)}, schemas=SCHEMAS, allow=("pricebt", "spec_helpers"))
    t = TradeTemplate("s", sp, TERMS)
    b = t.build(H.Market(4.0), TS)
    return sp, Env(pricer=H.Market(4.0), ctx=None, instrument=b.obj, terms=b.terms, state={}), b


# ------------------------------------------------------------------------------ value and derived
def test_value_runs_the_bound_callable_and_returns_its_raw_result():
    sp, env, _ = build()
    assert evaluate_value(sp, env) == pytest.approx(0.0)  # market level == fixed rate


def test_pv_is_derived_from_value_and_a_valuation_or_a_bare_number_both_work():
    sp, env, _ = build()
    assert evaluate_measure(sp, "pv", env) == pytest.approx(0.0)
    val = build({"value": {"target": {"function": "spec_helpers:as_valuation_fn"}, "kwargs": {"pv": 5.0, "ts": TS}}})
    assert evaluate_measure(val[0], "pv", val[1]) == 5.0
    tup = build({"value": {"target": {"function": "spec_helpers:as_tuple_fn"}}})
    assert evaluate_measure(tup[0], "pv", tup[1]) == 7.0


def test_notional_is_derived_from_the_notional_term_for_an_asset_class_with_terms():
    sp, env, _ = build()
    assert evaluate_measure(sp, "notional", env) == 1e7


def test_a_measure_without_a_binding_raises_and_names_what_is_bound():
    sp, env, _ = build()
    with pytest.raises(MeasureError) as ei:
        evaluate_measure(sp, "vega", env)
    assert "vega" in str(ei.value) and "dv01" in str(ei.value)  # names the missing measure and what the instrument does offer


def test_scalar_measures_go_through_their_bindings_with_no_same_name_fallback():
    sp, env, b = build()
    assert evaluate_measure(sp, "dv01", env) == pytest.approx(1e7 * 1e-4)
    assert evaluate_measure(sp, "rate", env) == 4.0
    assert b.obj.calls == ["pp01", "parlevel"]  # never Weird.dv01, which raises if touched


def test_a_measure_is_memoised_in_the_supplied_cache_per_name():
    sp, env, b = build()
    cache = {}
    a = evaluate_measure(sp, "dv01", env, cache=cache)
    c = evaluate_measure(sp, "dv01", env, cache=cache)
    assert a == c and b.obj.calls == ["pp01"]


def test_non_finite_scalar_measures_are_errors():
    sp, env, _ = build({"rate": {"target": {"function": "spec_helpers:nan_fn"}}})
    with pytest.raises(MeasureError):
        evaluate_measure(sp, "rate", env)


# ------------------------------------------------------------------------------ dict ladders (S4)
def test_is_vector_comes_from_the_schema_return_type_not_the_name():
    sp, _, _ = build()
    assert is_vector(sp.schema.measures["delta_ladder"]) and not is_vector(sp.schema.measures["dv01"])


def test_vector_ness_is_declared_by_the_return_type_whatever_the_measure_is_called():
    reg = SchemaRegistry()
    reg.register({"schema_version": 1, "asset_class": "x", "measures": {"buckets": {"returns": "dict[str, float]", "unit": "u"}, "fake_ladder": {"returns": "float", "unit": "u"}, "plain": {"returns": "float", "unit": "u"}}})
    m = reg.get("x").measures
    assert is_vector(m["buckets"]) and not is_vector(m["fake_ladder"]) and not is_vector(m["plain"])


def test_the_result_of_a_ladder_is_in_tenor_order_whatever_order_the_library_returned():
    ok = {"delta_ladder": {"target": {"function": "spec_helpers:unsorted_ladder"}, "kwargs": {"tenors": ["10Y", "2Y"]}, "keys": ["10Y", "2Y"]}}
    sp, env, _ = build(ok)
    lad = evaluate_measure(sp, "delta_ladder", env)
    assert list(lad) == ["2Y", "10Y"]  # sorted by length of tenor, not in the order the library returned them


def test_two_measures_share_one_cache_without_colliding_and_a_cached_dict_is_never_shared():
    sp, env, b = build()
    cache = {}
    assert evaluate_measure(sp, "dv01", env, cache=cache) == pytest.approx(1e3) and evaluate_measure(sp, "rate", env, cache=cache) == 4.0
    first = evaluate_measure(sp, "delta_ladder", env, cache=cache)
    second = evaluate_measure(sp, "delta_ladder", env, cache=cache)  # served from the cache
    second["2Y"] = -1.0
    third = evaluate_measure(sp, "delta_ladder", env, cache=cache)
    assert first == third and third["2Y"] == pytest.approx(500.0)


def test_an_optional_measure_the_object_happens_to_have_a_same_named_method_for_is_still_unbound_and_never_called():
    reg = SchemaRegistry()
    reg.register({"schema_version": 1, "asset_class": "opt", "extends": None, "methods": {"value": {"required": True, "returns": "float"}}, "measures": {"theta": {"returns": "float", "unit": "u"}}})

    class Obj:
        def val(self):
            return 1.0

        def theta(self):
            raise AssertionError("theta() must not be called: nothing binds the schema name `theta`")

    sp = build_spec("o", {"factory": Kit(factory=lambda p, t, *, terms, conventions: Built(Obj(), {}), asset_class="opt", default_bind={"value": {"target": {"method": "val"}}}, cls=Obj)},
                    schemas=reg)
    env = Env(pricer=None, ctx=None, instrument=Obj(), terms={}, state={})
    with pytest.raises(MeasureError) as ei:
        evaluate_measure(sp, "theta", env)
    assert "not bound" in str(ei.value)


def test_the_delta_ladder_is_a_plain_float_dict_with_exactly_the_bound_tenors():
    sp, env, _ = build()
    lad = evaluate_measure(sp, "delta_ladder", env)
    assert lad == {"2Y": pytest.approx(500.0), "10Y": pytest.approx(500.0)} and all(type(v) is float for v in lad.values())


def test_a_missing_bucket_is_an_error_naming_the_difference():
    bad = {"delta_ladder": {"target": {"function": "spec_helpers:short_ladder"}, "kwargs": {"tenors": ["2Y", "10Y", "30Y"]}, "keys": ["2Y", "10Y", "30Y"]}}
    sp, env, _ = build(bad)
    with pytest.raises(MeasureError) as ei:
        evaluate_measure(sp, "delta_ladder", env)
    assert "30Y" in str(ei.value) and "missing" in str(ei.value)


def test_an_extra_bucket_beyond_the_declared_tenors_is_an_error():
    bad = {"delta_ladder": {"target": {"function": "spec_helpers:long_ladder"}, "kwargs": {"tenors": ["2Y", "10Y"]}, "keys": ["2Y", "10Y"]}}
    sp, env, _ = build(bad)
    with pytest.raises(MeasureError) as ei:
        evaluate_measure(sp, "delta_ladder", env)
    assert "extra" in str(ei.value) and "30Y" in str(ei.value)


def test_without_declared_tenors_every_key_must_still_parse_as_a_tenor():
    bad = {"delta_ladder": {"target": {"function": "spec_helpers:junk_ladder"}}}
    sp, env, _ = build(bad)
    with pytest.raises(MeasureError) as ei:
        evaluate_measure(sp, "delta_ladder", env)
    assert "front" in str(ei.value)


def test_a_non_mapping_ladder_is_an_error_not_a_flat_zero():
    bad = {"delta_ladder": {"target": {"function": "spec_helpers:scalar_fn"}}}
    sp, env, _ = build(bad)
    with pytest.raises(MeasureError):
        evaluate_measure(sp, "delta_ladder", env)


def test_check_ladder_helper_accepts_only_finite_float_values():
    assert check_ladder({"2Y": 1, "10Y": 2.5}, None, "x") == {"2Y": 1.0, "10Y": 2.5}
    with pytest.raises(MeasureError):
        check_ladder({"2Y": float("nan")}, None, "x")
    with pytest.raises(MeasureError) as ei:
        check_ladder({"2y": 1.0}, None, "x")  # keys are canonical upper-case tenors
    assert "canonical tenor" in str(ei.value)


def test_returned_ladders_are_copies_so_a_cached_one_cannot_be_mutated_by_a_caller():
    sp, env, _ = build()
    cache = {}
    a = evaluate_measure(sp, "delta_ladder", env, cache=cache)
    a["2Y"] = 1e9
    assert evaluate_measure(sp, "delta_ladder", env, cache=cache)["2Y"] == pytest.approx(500.0)


# ------------------------------------------------------------------------------ vector maths
def test_scalar_of_a_dict_is_the_sum_of_its_buckets_and_of_a_number_is_itself():
    assert scalar_of({"2Y": 1.5, "10Y": -0.5}) == 1.0 and scalar_of(3) == 3.0 and scalar_of({}) == 0.0


def test_vector_of_validates_and_copies():
    d = {"2Y": 1.0}
    out = vector_of(d, "delta_ladder")
    out["2Y"] = 5.0
    assert d["2Y"] == 1.0
    with pytest.raises(MeasureError):
        vector_of(3.0, "dv01")


def test_vector_add_keeps_first_seen_order_and_treats_missing_buckets_as_zero():
    a = {"10Y": 1.0, "2Y": 2.0}
    b = {"5Y": 1.0, "2Y": 1.0}
    out = vector_add(a, b, 2.0)
    assert list(out) == ["10Y", "2Y", "5Y"] and out == {"10Y": 1.0, "2Y": 4.0, "5Y": 2.0} and a == {"10Y": 1.0, "2Y": 2.0}


def test_vector_scale():
    assert vector_scale({"2Y": 2.0}, -0.5) == {"2Y": -1.0}


def test_tenor_sort_key_orders_by_length_of_time():
    keys = ["30Y", "3M", "10Y", "1Y", "2W", "5D", "6M", "2Y", "9M"]
    assert sorted(keys, key=tenor_sort_key) == ["5D", "2W", "3M", "6M", "9M", "1Y", "2Y", "10Y", "30Y"]
    with pytest.raises(MeasureError):
        tenor_sort_key("front")


# ------------------------------------------------------------------------------ layers (L1)
def test_a_layer_runs_its_binding_and_returns_a_float():
    bind = {"carry": {"target": {"method": "zz_value"}, "kwargs": {"market": "@pricer"}}}
    sp, env, b = build(bind, layers=["carry"])
    assert evaluate_layer(sp, "carry", env) == pytest.approx(0.0) and b.obj.calls == ["zz_value"]


def test_an_unbound_optional_layer_is_an_error_and_a_reserved_name_is_refused():
    reg = SchemaRegistry()
    reg.register({"schema_version": 1, "asset_class": "lay", "methods": {"value": {"required": True, "returns": "float"}},
                  "layers": {"extra": {"id": "lay.extra", "version": 1, "returns": "float", "unit": "u"}}})
    sp = build_spec("l", {"factory": Kit(factory=lambda p, t, *, terms, conventions: Built(object(), {}), asset_class="lay", default_bind={"value": {"target": {"attribute": "real"}}}, cls=None)},
                    schemas=reg)
    env = Env(pricer=None, ctx=None, instrument=1.0, terms={}, state={})
    with pytest.raises(MeasureError) as ei:
        evaluate_layer(sp, "extra", env)
    assert "not bound" in str(ei.value)
    with pytest.raises(MeasureError) as ei:
        evaluate_layer(sp, "unexplained", env)
    assert "engine-owned" in str(ei.value)


def test_a_layer_returning_none_or_non_finite_is_an_error():
    for fn, word in (("none_fn", "returned None"), ("nan_fn", "not finite")):
        sp, env, _ = build({"carry": {"target": {"function": f"spec_helpers:{fn}"}}}, layers=["carry"])
        with pytest.raises(MeasureError) as ei:
            evaluate_layer(sp, "carry", env)
        assert word in str(ei.value)
