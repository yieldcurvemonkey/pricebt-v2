"""Requesting a measure an asset config declares under `unsupported_measures:` (docs/v2/IR_RISK_DESIGN.md
sections 0 R2-9/R2-13, 2.4 and 3.1; pricebt DEV-I11): `PricingService.value` raises
`UnsupportedMeasureError` -- a `ConfigError` and a `NotSupportedError` naming the measure and the
reason -- but only when the requested mapping slot is empty (a mapping always wins)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pricebt.errors import ConfigError, NotSupportedError, UnsupportedMeasureError
from pricebt.instrument import ConfigInstrument, IRSwap
from pricebt.risk import (
    Cashflows,
    IRDelta,
    IRDeltaLocalCcy,
    IRDeltaParallel,
    IRDiscountDeltaParallelLocalCcy,
    IRGamma,
    IRGammaParallel,
    IRVanna,
    IRVega,
    IRVegaParallel,
    Theta,
)
from pricebt.risk.results import FloatWithInfo, LazyFuture
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
D = date(2024, 3, 4)


def _toy_session():
    return PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_usd_swaption.yaml"])


def _swap():
    return IRSwap("Pay", "10y", "USD", 1_000_000, name="s")


def _asset(risk_measures, unsupported, **extra):
    """A ConfigInstrument asset (no measure contract), so any mapping/declaration combination loads."""
    return {
        "schema_version": 1,
        "asset": "decl",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"one": {"expr": "1.0", "unit": "ccy_per_bp"}},
        "portfolio_functions": {"ladder": {"expr": "{'1y': 1.0, '2y': 2.0}", "unit": "ccy_per_bp", "returns": "buckets"}},
        "risk_measures": {"Price": "one", **risk_measures},
        "unsupported_measures": unsupported,
        **extra,
    }


def _inst():
    return ConfigInstrument(pricebt_asset="decl", name="x")


# ------------------------------------------------------------------------------------ the shipped toy declarations


def test_declared_whole_measure_raises_naming_measure_and_reason():
    session = _toy_session()
    with pytest.raises(UnsupportedMeasureError) as info:
        session.pricing.value(_swap(), D, Theta, None)
    err = info.value
    assert isinstance(err, ConfigError) and isinstance(err, NotSupportedError)
    assert (err.measure, err.form, err.asset, err.key) == ("Theta", "*", "toy_usd_irs", "unsupported_measures.Theta")
    assert "Theta (per day) = IRTheta / 365 is not wired" in str(err)  # the config's own reason


def test_declared_fd_measure_raises_in_its_scalar_form_too():
    session = _toy_session()
    with pytest.raises(UnsupportedMeasureError, match="'IRVanna' is declared unsupported"):
        session.pricing.value(_swap(), D, IRVanna(aggregation_level="Type"), None)


def test_declaration_is_found_through_base_name():
    """IRDiscountDeltaParallelLocalCcy has base_name IRDiscountDeltaParallel (DEV-I16); the toy swap
    declares the base. (Not IRGammaParallel: the v2-pnl-explain branch maps it on toy_usd_irs.)"""
    session = _toy_session()
    with pytest.raises(UnsupportedMeasureError) as info:
        session.pricing.value(_swap(), D, IRDiscountDeltaParallelLocalCcy, None)
    assert info.value.measure == "IRDiscountDeltaParallel"


# ------------------------------------------------------------------------------------ mapping wins; form rules


def test_bucketed_vega_is_declared_scalar_vega_is_mapped():
    """(Was on toy_usd_swaption, which maps the whole contract since IR_RISK_DESIGN Phase C.)"""
    session = PricebtSession.use(assets=[_asset({"IRVega": {"scalar": "one"}}, {"IRVega": {"bucketed": "one flat vol per day, no cube"}})])
    with pytest.raises(UnsupportedMeasureError) as info:
        session.pricing.value(_inst(), D, IRVega, None)  # bare FD measure = bucketed form
    assert info.value.form == "bucketed"
    assert isinstance(session.pricing.value(_inst(), D, IRVegaParallel, None), FloatWithInfo)


def test_mapping_wins_over_a_stale_whole_measure_declaration():
    with pytest.warns(UserWarning, match="declared unsupported"):
        session = PricebtSession.use(assets=[_asset({"IRVega": {"scalar": "one"}}, {"IRVega": "no vol model"})])
    assert float(session.pricing.value(_inst(), D, IRVegaParallel, None)) == 1.0  # scalar slot mapped
    with pytest.raises(UnsupportedMeasureError) as info:  # bucketed slot empty: the declaration applies
        session.pricing.value(_inst(), D, IRVega, None)
    assert info.value.form == "*"


def test_declared_scalar_form_beats_the_bucket_sum_fallback():
    """An explicit scalar request (aggregation_level Type) of an FD measure mapped only bucketed
    would sum the buckets (DESIGN 8.1 rule 5) -- producing exactly the form the author declared
    unsupported, so the declaration wins. The bucketed form itself is mapped and still works."""
    session = PricebtSession.use(assets=[_asset({"IRDelta": {"bucketed": "ladder"}}, {"IRDelta": {"scalar": "no own-rate delta"}})])
    with pytest.raises(UnsupportedMeasureError) as info:
        session.pricing.value(_inst(), D, IRDelta(aggregation_level="Type"), None)
    assert (info.value.form, info.value.key) == ("scalar", "unsupported_measures.IRDelta.scalar")
    assert isinstance(session.pricing.value(_inst(), D, IRDelta, None), LazyFuture)


def test_plain_measure_selects_its_mapped_bucketed_form_despite_a_scalar_declaration():
    """A plain measure names no form: with a bucketed slot mapped, rule 5 selects it (mapped, so
    the mapping wins)."""
    session = PricebtSession.use(assets=[_asset({"IRGamma": {"bucketed": "ladder"}}, {"IRGamma": {"scalar": "no scalar gamma"}})])
    assert isinstance(session.pricing.value(_inst(), D, IRGamma, None), LazyFuture)


@pytest.mark.parametrize("form", ["scalar", "bucketed", "frame"])
def test_unmapped_plain_measure_raises_for_any_declared_form(form):
    session = PricebtSession.use(assets=[_asset({}, {"Cashflows": {form: "no schedule"}})])
    with pytest.raises(UnsupportedMeasureError) as info:
        session.pricing.value(_inst(), D, Cashflows, None)
    assert info.value.form == form


def test_unmapped_fd_measure_raises_only_for_its_requested_form():
    session = PricebtSession.use(assets=[_asset({}, {"IRVanna": {"scalar": "no vanna"}})])
    with pytest.raises(UnsupportedMeasureError):
        session.pricing.value(_inst(), D, IRVanna(aggregation_level="Type"), None)
    with pytest.raises(ConfigError, match="has no mapping for risk measure IRVanna") as info:
        session.pricing.value(_inst(), D, IRVanna, None)  # the bucketed form is not declared
    assert not isinstance(info.value, UnsupportedMeasureError)


def test_undeclared_unmapped_measure_keeps_the_generic_no_mapping_error():
    session = PricebtSession.use(assets=[_asset({}, {})])
    with pytest.raises(ConfigError, match="has no mapping for risk measure Theta; add it under risk_measures:") as info:
        session.pricing.value(_inst(), D, Theta, None)
    assert not isinstance(info.value, UnsupportedMeasureError)


# ------------------------------------------------------------------------------------ preset keys serve their base (R2-10)


def test_a_preset_key_serves_its_base_in_the_form_the_contract_counts():
    """The contract counts an IRDeltaParallel key toward IRDelta's scalar form, so a scalar IRDelta
    request prices from it (DESIGN 8.1 rule 3, last step); its bucketed form stays unmapped."""
    session = PricebtSession.use(assets=[_asset({"IRDeltaParallel": "one"}, {})])
    assert float(session.pricing.value(_inst(), D, IRDelta(aggregation_level="Type"), None)) == 1.0
    assert float(session.pricing.value(_inst(), D, IRDeltaLocalCcy(aggregation_level="Type"), None)) == 1.0
    with pytest.raises(ConfigError, match="has no mapping for risk measure IRDelta"):
        session.pricing.value(_inst(), D, IRDelta, None)  # bare = bucketed: not provided


def test_a_local_ccy_key_serves_both_forms_of_its_base():
    session = PricebtSession.use(assets=[_asset({"IRDeltaLocalCcy": {"scalar": "one", "bucketed": "ladder"}}, {})])
    assert isinstance(session.pricing.value(_inst(), D, IRDelta, None), LazyFuture)
    assert float(session.pricing.value(_inst(), D, IRDeltaParallel, None)) == 1.0
    bucketed_only = PricebtSession.use(assets=[_asset({"IRDeltaLocalCcy": {"bucketed": "ladder"}}, {})])
    assert float(bucketed_only.pricing.value(_inst(), D, IRDelta(aggregation_level="Type"), None)) == 3.0  # rule 5: the buckets summed


def test_an_aggregation_level_scalar_uses_the_preset_scalar_before_summing_a_ladder():
    """IRDelta maps only a ladder (summing to 3.0, not the scalar: R2-2 allows it) and IRDeltaParallel
    the scalar (1.0). The contract counts IRDeltaParallel as IRDelta's scalar, so every scalar
    request agrees with it; summing the ladder would give HedgeAction(IRDelta(aggregation_level=
    'Type')) a different number from ir_pnl_definition's IRDeltaParallel."""
    session = PricebtSession.use(assets=[_asset({"IRDelta": {"bucketed": "ladder"}, "IRDeltaParallel": "one"}, {})])
    for risk in (IRDelta(aggregation_level="Type"), IRDelta(aggregation_level="Asset"), IRDeltaParallel):
        assert float(session.pricing.value(_inst(), D, risk, None)) == 1.0, risk
    assert isinstance(session.pricing.value(_inst(), D, IRDelta, None), LazyFuture)  # bare: the ladder


def test_an_alias_mapping_wins_over_a_declaration_of_its_base():
    """The load warning says the mapping is used (R2-9); the request agrees."""
    with pytest.warns(UserWarning, match=r"IRGammaParallel is declared unsupported \(every form\) but mapped \(scalar via IRGammaParallelLocalCcy\)"):
        session = PricebtSession.use(assets=[_asset({"IRGammaParallelLocalCcy": "one"}, {"IRGammaParallel": "old"})])
    assert float(session.pricing.value(_inst(), D, IRGammaParallel, None)) == 1.0


# ------------------------------------------------------------------------------------ R2-13 hint


def test_bare_fd_measure_with_only_a_scalar_mapping_hints_at_the_scalar_request():
    session = PricebtSession.use(assets=[_asset({"IRVanna": {"scalar": "one"}}, {})])
    with pytest.raises(ConfigError, match=r"has no bucketed mapping; request IRVanna\(aggregation_level='Type'\) for the scalar form") as info:
        session.pricing.value(_inst(), D, IRVanna, None)
    assert not isinstance(info.value, UnsupportedMeasureError)
    assert float(session.pricing.value(_inst(), D, IRVanna(aggregation_level="Type"), None)) == 1.0
