"""PricingService core contract: resolution, the calc return convention, the measure -> function
mapping, and cache reset (IMPLEMENTATION_PLAN.md P2.2; DESIGN.md sections 6.2, 6.5, 8.1)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from dateutil.relativedelta import relativedelta

import toylib.rates as tr
from pricebt.errors import NotSupportedError
from pricebt.instrument import ConfigInstrument, IRSwap
from pricebt.markets import HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRDelta, IRDeltaParallel, IRFwdRate, IRGamma, Price
from pricebt.risk.results import FloatWithInfo, LazyFuture, PortfolioRiskResult, PricingFuture
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"


def _session():
    return PricebtSession.use(
        assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_eur_irs.yaml", ASSETS / "toy_usd_swaption.yaml"],
        fx=ASSETS / "toy_fx.yaml",
    )


def _swap(name="s", quantity_=1.0):
    return IRSwap("Pay", "10y", "USD", 1_000_000, name=name, quantity_=quantity_)


# ------------------------------------------------------------------------------------ resolution


def test_resolve_pins_dates():
    session = _session()
    d0 = date(2024, 1, 2)
    resolved = session.pricing.resolve(_swap(), d0, None)
    assert resolved.resolved_terms["effective_date"] == d0
    assert resolved.resolved_terms["termination_date"] == d0 + relativedelta(years=10)
    # not the tenor string: a later use of this SAME resolved instrument must not re-derive it
    d1 = date(2030, 6, 1)
    assert resolved.resolved_terms["termination_date"] != d1 + relativedelta(years=10)


def test_resolved_instrument_reresolved_under_a_later_context_keeps_original_terms():
    session = _session()
    d0, d1 = date(2024, 1, 2), date(2028, 7, 1)
    once = session.pricing.resolve(_swap(), d0, None)
    again = session.pricing.resolve(once, d1, "CSA_LATER")
    assert again.resolved_terms == once.resolved_terms
    assert again.resolution_key.date == d0
    assert again.resolution_csa == once.resolution_csa


def test_instrument_resolve_in_place_under_later_context_is_a_no_op():
    session = _session()  # noqa: F841 -- constructs PricebtSession.current, used implicitly by .resolve
    d0, d1 = date(2024, 1, 2), date(2028, 7, 1)
    swap = _swap()
    with PricingContext(d0):
        swap.resolve(in_place=True)
    original = dict(swap.resolved_terms)
    with PricingContext(d1):
        swap.resolve(in_place=True)  # DESIGN.md section 6.2 rule 1: changes nothing
    assert swap.resolved_terms == original
    assert swap.resolution_key.date == d0


# ------------------------------------------------------------------------------------ calc == resolve + price


def test_unresolved_calc_price_equals_price_of_its_own_resolution():
    session = _session()
    d = date(2024, 3, 4)
    swap = _swap()
    with PricingContext(d):
        future = swap.calc(Price)
    resolved = session.pricing.resolve(swap, d, None)
    expected = session.pricing.unit_value(resolved, d, "npv", None)
    assert float(future.result()) == pytest.approx(expected)


# ------------------------------------------------------------------------------------ measure -> function (8.1)


def test_irdelta_scalar_type_is_float_with_info_bare_is_six_column_frame():
    session = _session()
    d = date(2024, 3, 4)
    swap = _swap()

    scalar = session.pricing.value(swap, d, IRDelta(aggregation_level="Type"), None)
    assert isinstance(scalar, FloatWithInfo)

    bucketed = session.pricing.value(swap, d, IRDelta, None)
    assert isinstance(bucketed, LazyFuture)
    df = bucketed.result()
    assert list(df.columns) == ["mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style", "value"]
    assert len(df) > 0


def test_irdeltaparallel_falls_back_through_base_name_to_irdelta_mapping():
    session = _session()
    d = date(2024, 3, 4)
    value = session.pricing.value(_swap(), d, IRDeltaParallel, None)
    assert isinstance(value, FloatWithInfo)  # asset config has no "IRDeltaParallel:" entry at all


def test_irdelta_bump_size_raises_not_supported():
    """DEV-I8 as narrowed by DEV-I10: bump_size passes through only to a function that reads
    `pricebt_bump_size`; the toy ladder does not, so the request is refused, never ignored."""
    session = _session()
    d = date(2024, 3, 4)
    with pytest.raises(NotSupportedError, match="function 'delta_ladder' does not reference pricebt_bump_size"):
        session.pricing.value(_swap(), d, IRDelta(bump_size=5), None)


def test_plain_measure_with_bucketed_only_mapping_selects_bucketed_not_sum():
    """DESIGN.md section 8.1 rule 5's THIRD bullet: a measure with NO aggregation_level parameter
    at all (a plain RiskMeasure, e.g. IRGamma) mapped only via {bucketed: ...} selects the
    bucketed form. This differs from the FIRST bullet's fallback (an aggregation_level measure
    EXPLICITLY resolved to scalar form -- e.g. IRDelta(aggregation_level='Type') -- with only a
    bucketed function mapped), which DOES sum the buckets. Same config, same bucketed function,
    both measures mapped bucketed-only: this is the discriminator between the two bullets."""
    cfg = {
        "schema_version": 1,
        "asset": "bucket_only_plain_measure",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"price_fn": {"expr": "1.0", "unit": "ccy"}},
        "portfolio_functions": {"bucket_fn": {"expr": "{'a': 1.0, 'b': 2.0}", "unit": "ccy", "returns": "buckets"}},
        "risk_measures": {
            "Price": "price_fn",
            "IRGamma": {"bucketed": "bucket_fn"},  # plain RiskMeasure: no aggregation_level concept
            "IRDelta": {"bucketed": "bucket_fn"},  # has aggregation_level; here resolved to scalar
        },
    }
    session = PricebtSession.use(assets=[cfg])
    inst = ConfigInstrument(pricebt_asset="bucket_only_plain_measure", name="x")
    d = date(2024, 1, 2)

    # bullet 3: no aggregation_level concept at all -> select the bucketed form, don't sum.
    result = session.pricing.value(inst, d, IRGamma, None)
    assert isinstance(result, LazyFuture)  # bucketed form selected, NOT summed into a scalar
    df = result.result()
    assert set(df["mkt_point"]) == {"a", "b"}
    assert sorted(df["value"]) == [1.0, 2.0]

    # bullet 1 (unchanged, still correct): aggregation_level explicitly resolved to Type (scalar
    # form) but only a bucketed function is mapped -> sum the buckets into the scalar.
    summed = session.pricing.value(inst, d, IRDelta(aggregation_level="Type"), None)
    assert isinstance(summed, FloatWithInfo)
    assert float(summed) == pytest.approx(3.0)


def test_intensive_unit_ignores_quantity_extensive_unit_scales():
    session = _session()
    d = date(2024, 3, 4)
    unit_rate = session.pricing.value(_swap(), d, IRFwdRate, None)
    unit_price = session.pricing.value(_swap(), d, Price, None)

    scaled = _swap(quantity_=-3.0)
    scaled_rate = session.pricing.value(scaled, d, IRFwdRate, None)
    scaled_price = session.pricing.value(scaled, d, Price, None)

    assert float(scaled_rate) == pytest.approx(float(unit_rate))
    assert float(scaled_price) == pytest.approx(float(unit_price) * -3.0)


def test_resolved_variable_is_available_in_functions_entries():
    """DESIGN.md section 4.3's table lists `resolved` as available in `trade`, `functions` AND
    `attributes` -- `trade`/`attributes` already got it; this asset's `echo:` function is what
    exercises `functions:`. Also covers section 4.2's `number` unit, whose `FloatWithInfo.unit`
    must be `{}` (empty), not `{"number": 1}`."""
    cfg = {
        "schema_version": 1,
        "asset": "resolved_in_functions",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "resolve": {"expr": "{'notional': float(kwargs['notional'])}"},
        "functions": {"echo": {"expr": "resolved['notional']", "unit": "number"}},
        "risk_measures": {"Price": "echo"},
    }
    session = PricebtSession.use(assets=[cfg])
    inst = ConfigInstrument(pricebt_asset="resolved_in_functions", name="x", notional=500_000.0)

    val = session.pricing.value(inst, date(2024, 1, 2), Price, None)
    assert float(val) == pytest.approx(500_000.0)  # round-tripped through `resolved`, not a re-read of kwargs
    assert val.unit == {}


def test_kwargs_injected_into_trade_and_functions_when_resolve_absent():
    """DESIGN.md section 4.3's injected-variables table: `kwargs` is available in trade/functions
    evaluation WHEN `resolve:` is absent (it is unconditionally available inside `resolve` itself,
    already covered by other tests). Both `trade.expr` and a `functions:` entry read `kwargs`
    directly here; before the fix this raised NameError."""
    cfg = {
        "schema_version": 1,
        "asset": "kwargs_no_resolve",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "trade": {"expr": "kwargs['notional']"},  # would NameError before the fix
        "functions": {"echo_kwargs": {"expr": "float(kwargs['notional'])", "unit": "number"}},
        "risk_measures": {"Price": "echo_kwargs"},
    }
    session = PricebtSession.use(assets=[cfg])
    inst = ConfigInstrument(pricebt_asset="kwargs_no_resolve", name="x", notional=250_000.0)

    val = session.pricing.value(inst, date(2024, 1, 2), Price, None)
    assert float(val) == pytest.approx(250_000.0)


def test_number_unit_bucketed_value_reports_empty_unit_dict_lazy_and_group():
    """The lazy per-instrument path (`_lazy_value`) and the `group_aggregate` path must apply the
    SAME unit logic as the scalar path (DESIGN.md section 4.2): `number` -> `{}`."""
    cfg = {
        "schema_version": 1,
        "asset": "number_unit_bucketed",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"scalar_fn": {"expr": "1.0", "unit": "number"}},
        "portfolio_functions": {"bucket_fn": {"expr": "{'b': float(sum(weights))}", "unit": "number", "returns": "buckets"}},
        "risk_measures": {"Price": "scalar_fn", "IRDelta": {"scalar": "scalar_fn", "bucketed": "bucket_fn"}},
    }
    session = PricebtSession.use(assets=[cfg])
    d = date(2024, 1, 2)
    inst = ConfigInstrument(pricebt_asset="number_unit_bucketed", name="x", quantity_=3.0)

    lazy = session.pricing.value(inst, d, IRDelta, None).result()  # single-instrument -> _lazy_value's thunk
    assert lazy.unit == {}
    assert lazy["value"].sum() == pytest.approx(3.0)  # number is extensive: scaled by quantity_

    with PricingContext(d):
        prr = Portfolio([inst]).calc(IRDelta)
    aggregated = prr.aggregate()  # -> group_aggregate
    assert aggregated.unit == {}
    assert aggregated["value"].sum() == pytest.approx(3.0)


def test_intensive_unit_bucketed_value_agrees_lazy_and_group_p6_2_finding_3():
    """DESIGN.md section 5.4: an intensive unit (bp/pct/decimal) is NOT multiplied by quantity_ --
    unconditionally, not just on the lazy per-instrument path. The lazy thunk (`_lazy_value` ->
    `_scale_bucket`) already gated this correctly; `_portfolio_value_from_entries` (the group path,
    via `group_aggregate`) used to bake the real quantity_ into `weights` regardless of unit, so an
    intensive-unit ladder disagreed between the two access paths (this test's twin above,
    `test_number_unit_bucketed_value_reports_empty_unit_dict_lazy_and_group`, only covers the
    extensive `number` unit, where the two routes coincidentally agree either way)."""
    cfg = {
        "schema_version": 1,
        "asset": "bp_unit_bucketed",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"scalar_fn": {"expr": "1.0", "unit": "number"}},
        "portfolio_functions": {"bucket_fn": {"expr": "{'b': float(sum(weights))}", "unit": "bp", "returns": "buckets"}},
        "risk_measures": {"Price": "scalar_fn", "IRDelta": {"scalar": "scalar_fn", "bucketed": "bucket_fn"}},
    }
    session = PricebtSession.use(assets=[cfg])
    d = date(2024, 1, 2)
    inst = ConfigInstrument(pricebt_asset="bp_unit_bucketed", name="x", quantity_=3.0)

    lazy = session.pricing.value(inst, d, IRDelta, None).result()  # single-instrument -> _lazy_value's thunk
    assert lazy["value"].sum() == pytest.approx(1.0)  # bp is intensive: NOT scaled by quantity_

    with PricingContext(d):
        prr = Portfolio([inst]).calc(IRDelta)
    aggregated = prr.aggregate()  # -> group_aggregate, even for a portfolio of one
    assert aggregated["value"].sum() == pytest.approx(1.0)  # must agree with the lazy path, not 3.0


def test_bucketed_and_group_aggregate_fill_risk_key_risk_measure():
    """DESIGN.md section 8.2: 'pricebt fills date and risk_measure' -- the scalar path already did;
    the lazy per-instrument path and group_aggregate must too."""
    session = _session()
    d = date(2024, 3, 4)
    swap = _swap()

    lazy = session.pricing.value(swap, d, IRDelta, None)
    assert lazy.result().risk_key.risk_measure == IRDelta

    with PricingContext(d):
        prr = Portfolio([swap]).calc(IRDelta)
    assert prr.aggregate().risk_key.risk_measure == IRDelta


# ------------------------------------------------------------------------------------ return convention (6.5)


def test_calc_returns_prr_for_portfolio_future_for_instrument_inside_context_plain_outside():
    _session()
    d = date(2024, 3, 4)
    swap = _swap()
    port = Portfolio([swap])

    with PricingContext(d):
        port_result = port.calc(Price)
        inst_result = swap.calc(Price)
    assert isinstance(port_result, PortfolioRiskResult)
    assert isinstance(inst_result, PricingFuture)
    assert float(inst_result.result()) == pytest.approx(float(port_result[swap]))

    plain = swap.calc(Price)  # no context entered now
    assert not isinstance(plain, PricingFuture)
    assert isinstance(plain, FloatWithInfo)

    plain_port = port.calc(Price)  # Portfolio.calc ALWAYS returns a PRR, context or not
    assert isinstance(plain_port, PortfolioRiskResult)


def test_nested_historical_resolve_keeps_inner_name_and_gives_distinct_objects_per_date():
    _session()
    d0, d1 = date(2024, 1, 2), date(2024, 6, 3)
    swap = _swap(name="s")
    outer = Portfolio(Portfolio([swap], name="hedge"))

    with HistoricalPricingContext(dates=[d0, d1]):
        future = outer.resolve(in_place=False)
    per_date = future.result()
    assert set(per_date) == {d0, d1}

    inner0 = per_date[d0].priceables[0]
    inner1 = per_date[d1].priceables[0]
    assert isinstance(inner0, Portfolio) and inner0.name == "hedge"
    assert isinstance(inner1, Portfolio) and inner1.name == "hedge"
    assert inner0 is not inner1
    assert inner0.priceables[0] is not inner1.priceables[0]
    assert inner0.priceables[0].resolved_terms["effective_date"] == d0
    assert inner1.priceables[0].resolved_terms["effective_date"] == d1


def test_historical_multi_measure_calc_by_date_preserves_unit_and_indexes_by_measure():
    """Regression for two pre-existing bugs (out of P3.5's nominal file ownership -- assets/pricing.py,
    risk/results.py -- but confirmed blocking, found wiring GenericEngine's HedgeActionImpl, whose
    `p.results[d][p.risk]` needs both): a HistoricalPricingContext.calc() with more than one risk
    measure, on a plain Instrument, must (1) keep each date's `.unit` (a bare
    `pd.Series({date: FloatWithInfo, ...})` silently downcasts every element to a plain float64, so
    the per-instrument SeriesWithInfo's constructor must be given `unit`/`risk_key` explicitly), and
    (2) be indexable by date (`PortfolioRiskResult._by_date` used to KeyError: the per-instrument
    future holds a MultipleRiskMeasureResult keyed by RiskMeasure, not by date).
    """
    session = _session()
    d0, d1 = date(2024, 1, 2), date(2024, 6, 3)
    swap = _swap()
    port = Portfolio([swap])

    with HistoricalPricingContext(dates=[d0, d1]):
        result = port.calc((Price, IRDelta))

    by_date = result[d0]  # used to raise RuntimeError("Can only index by date...") via KeyError
    value = by_date[swap][Price]
    assert isinstance(value, FloatWithInfo)
    assert value.unit is not None

    resolved = session.pricing.resolve(_swap(), d0, None)
    expected = session.pricing.value(resolved, d0, Price, None)
    assert float(value) == pytest.approx(float(expected))
    assert value.unit == expected.unit


def test_attribute_cache_key_includes_resolution_date_and_csa_p6_2_finding_4():
    """DESIGN.md section 4.3/6.3: attribute()'s injected env (pricebt_date/pricebt_csa/market) is
    resolution-date/csa-dependent, so its cache key must be too. Without `resolve:`, resolved_terms
    is kwargs verbatim (section 4.4) -- independent of the resolution date -- so two instruments
    resolved on different dates with the SAME kwargs collide on every other key component, and
    (before this fix) the second call silently returned the first call's cached, wrong-date value."""
    cfg = {
        "schema_version": 1,
        "asset": "no_resolve_date_reader",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"scalar_fn": {"expr": "1.0", "unit": "number"}},
        "risk_measures": {"Price": "scalar_fn"},
        "attributes": {"seen_date": "pricebt_date"},
    }
    session = PricebtSession.use(assets=[cfg])
    d0, d1 = date(2024, 1, 2), date(2024, 6, 3)
    template = ConfigInstrument(pricebt_asset="no_resolve_date_reader", name="x", quantity_=1.0)

    resolved0 = session.pricing.resolve(template, d0, None)
    resolved1 = session.pricing.resolve(template, d1, None)
    assert resolved0.resolved_terms == resolved1.resolved_terms  # confirms the collision setup is real

    assert session.pricing.attribute(resolved0, "seen_date") == d0
    assert session.pricing.attribute(resolved1, "seen_date") == d1  # must not be d0's cached value


# ------------------------------------------------------------------------------------ reset()


def test_reset_clears_every_cache():
    session = _session()
    d = date(2024, 3, 4)
    swap = _swap()

    resolved = session.pricing.resolve(swap, d, None)
    session.pricing.unit_value(swap, d, "npv", None)
    session.pricing.attribute(resolved, "notional_amount")
    session.pricing.fx("EUR", "USD", d)
    session.pricing.portfolio_value(session.registry["toy_usd_irs"], d, "delta_ladder", [swap], None)

    caches = [
        session.pricing._market_cache,
        session.pricing._resolve_cache,
        session.pricing._trade_cache,
        session.pricing._unit_value_cache,
        session.pricing._portfolio_value_cache,
        session.pricing._attribute_cache,
        session.pricing._fx_cache,
    ]
    assert all(caches), "every cache row must have at least one entry before reset() to be a real test"

    counts_before = dict(tr.EVAL_COUNTS)
    session.pricing.unit_value(swap, d, "npv", None)
    assert dict(tr.EVAL_COUNTS) == counts_before  # fully cached: no new market/resolve/trade/npv calls

    session.pricing.reset()
    assert not any(caches)  # same dict objects, now empty -- proves reset() clears them in place

    session.pricing.unit_value(swap, d, "npv", None)
    for key in ("market", "resolve", "trade", "npv"):
        assert tr.EVAL_COUNTS[key] == counts_before[key] * 2  # re-evaluated after the cache clear
