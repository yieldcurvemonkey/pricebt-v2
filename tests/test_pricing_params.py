"""Measure parameter pass-through (docs/v2/IR_RISK_DESIGN.md sections 0.5 and 3.2; pricebt DEV-I10,
narrowing DEV-I8): `bump_size`, `finite_difference_method`, `local_curve` and `scale_factor` reach a
config function as `pricebt_<name>` (always injected, None when unset); a function that does not
name the variable refuses the parameter; `mkt_marking_options` always raises; and the parameters
are part of every cache and group key, so two bump sizes never share a value."""
from __future__ import annotations

from datetime import date

import pytest

from pricebt.assets.pricing import _measure_params
from pricebt.common import FiniteDifferenceMethod
from pricebt.errors import NotSupportedError
from pricebt.instrument import ConfigInstrument
from pricebt.markets import PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRBasis, IRDelta, IRVega, IRXccyDelta
from pricebt.risk.results import LazyFuture
from pricebt.session import PricebtSession

D = date(2024, 3, 4)

CFG = {
    "schema_version": 1,
    "asset": "params",
    "instrument": "ConfigInstrument",
    "currency": "USD",
    "market": {"expr": "1"},
    "functions": {
        "price_fn": {"expr": "1.0", "unit": "ccy"},
        "echo_bump": {"expr": "0.0 if pricebt_bump_size is None else float(pricebt_bump_size)", "unit": "ccy_per_bp"},
        "fd_method": {
            "expr": "2.0 if pricebt_finite_difference_method is None else (1.0 if pricebt_finite_difference_method == 'Centered' else 3.0)",
            "unit": "ccy_per_bp",
        },
        "scaled": {"expr": "float(pricebt_scale_factor or 1.0) * (10.0 if pricebt_local_curve else 1.0)", "unit": "ccy_per_bp"},
        "plain": {"expr": "7.0", "unit": "ccy_per_bp"},
    },
    "portfolio_functions": {
        "ladder": {"expr": "{'1y': sum(weights) * (pricebt_bump_size or 1.0)}", "unit": "ccy_per_bp", "returns": "buckets"},
    },
    "risk_measures": {
        "Price": "price_fn",
        "IRDelta": {"scalar": "echo_bump", "bucketed": "ladder"},
        "IRBasis": {"scalar": "fd_method"},
        "IRXccyDelta": {"scalar": "scaled"},
        "IRVega": {"scalar": "plain"},
    },
}


def _session():
    return PricebtSession.use(assets=[CFG])


def _inst(name="x", quantity_=1.0):
    return ConfigInstrument(pricebt_asset="params", name=name, quantity_=quantity_)


def _type(measure, **params):
    return measure(aggregation_level="Type", **params)


# ------------------------------------------------------------------------------------ injection


def test_measure_params_is_a_sorted_tuple_of_the_set_pass_through_parameters():
    assert _measure_params(IRDelta) == ()
    assert _measure_params(IRDelta(bump_size=2, finite_difference_method="up", aggregation_level="Type")) == (
        ("bump_size", 2),
        ("finite_difference_method", FiniteDifferenceMethod.Up),
    )


def test_names_are_always_injected_none_when_unset():
    session = _session()
    assert float(session.pricing.value(_inst(), D, _type(IRDelta), None)) == 0.0  # no NameError
    assert session.pricing.unit_value(_inst(), D, "echo_bump", None) == 0.0  # positional form unchanged
    assert session.pricing.unit_value(_inst(), D, "echo_bump", None, params=(("bump_size", 3.0),)) == 3.0


def test_bump_size_reaches_the_function_and_is_part_of_the_unit_value_cache_key():
    session = _session()
    assert float(session.pricing.value(_inst(), D, _type(IRDelta, bump_size=2), None)) == 2.0
    assert float(session.pricing.value(_inst(), D, _type(IRDelta, bump_size=5), None)) == 5.0
    assert float(session.pricing.value(_inst(), D, _type(IRDelta), None)) == 0.0


def test_finite_difference_method_is_injected_as_the_enum_which_equals_its_string():
    session = _session()
    assert float(session.pricing.value(_inst(), D, _type(IRBasis), None)) == 2.0
    assert float(session.pricing.value(_inst(), D, _type(IRBasis, finite_difference_method="centered"), None)) == 1.0
    assert float(session.pricing.value(_inst(), D, _type(IRBasis, finite_difference_method="Up"), None)) == 3.0


def test_scale_factor_and_local_curve_pass_through():
    session = _session()
    assert float(session.pricing.value(_inst(), D, _type(IRXccyDelta, scale_factor=3.0, local_curve=True), None)) == 30.0
    assert float(session.pricing.value(_inst(), D, _type(IRXccyDelta, scale_factor=3.0), None)) == 3.0


# ------------------------------------------------------------------------------------ refusals


def test_a_parameter_the_function_does_not_reference_is_refused_naming_the_variable():
    session = _session()
    with pytest.raises(NotSupportedError, match="sets bump_size; function 'plain' does not reference pricebt_bump_size"):
        session.pricing.value(_inst(), D, _type(IRVega, bump_size=1), None)
    # fd_method reads pricebt_finite_difference_method, not pricebt_bump_size
    with pytest.raises(NotSupportedError, match="function 'fd_method' does not reference pricebt_bump_size"):
        session.pricing.value(_inst(), D, _type(IRBasis, bump_size=1, finite_difference_method="Up"), None)


def test_the_bucketed_function_is_the_one_checked_for_a_bucketed_request():
    session = _session()
    assert isinstance(session.pricing.value(_inst(), D, IRDelta(bump_size=2), None), LazyFuture)  # ladder reads it
    with pytest.raises(NotSupportedError, match="function 'ladder' does not reference pricebt_scale_factor"):
        session.pricing.value(_inst(), D, IRDelta(scale_factor=2.0), None)


def test_mkt_marking_options_always_raises():
    session = _session()
    with pytest.raises(NotSupportedError, match="sets mkt_marking_options; it is honoured GS server-side"):
        session.pricing.value(_inst(), D, _type(IRXccyDelta, mkt_marking_options="Mode"), None)


# ------------------------------------------------------------------------------------ bucketed / group paths


def test_lazy_group_key_and_member_carry_the_params():
    session = _session()
    inst = _inst(quantity_=2.0)
    lazy2 = session.pricing.value(inst, D, IRDelta(bump_size=2), None)
    lazy5 = session.pricing.value(inst, D, IRDelta(bump_size=5), None)
    assert lazy2.group_key[2] == D  # the date stays at index 2 (R2-26)
    assert lazy2.group_key != lazy5.group_key
    assert lazy2.group_key[-1] == (("bump_size", 2),) and lazy5.member[-1] == (("bump_size", 5),)
    assert float(lazy5.result()["value"].sum()) == 10.0  # per-instrument thunk: 5 x quantity 2
    grouped = session.pricing.group_aggregate(lazy5.group_key, [lazy5.member])
    assert float(grouped["value"].sum()) == 10.0  # group path: weights [2.0] x 5


def test_portfolio_aggregate_with_two_bump_sizes_never_shares_the_portfolio_value_cache():
    session = _session()  # noqa: F841 -- PricebtSession.current drives Portfolio.calc
    portfolio = Portfolio([_inst("a", 1.0), _inst("b", 2.0)])
    with PricingContext(D):
        r2 = portfolio.calc(IRDelta(bump_size=2))
    with PricingContext(D):
        r5 = portfolio.calc(IRDelta(bump_size=5))
    assert float(r2.aggregate()["value"].sum()) == 6.0  # (1 + 2) x 2
    assert float(r5.aggregate()["value"].sum()) == 15.0  # (1 + 2) x 5
