"""Scenario contexts (docs/v2/SCENARIOS.md): the gs scenario classes, the `scenarios:` asset-config
section, pricing under scenarios (shocks, curve shapes, overlays, rolls, measure overrides,
composites, MultiScenario) and the MultipleScenarioResult shapes, on the toy library.

Every expected number is built here from the toy library on a hand-made curve (toylib.rates /
toylib.swaption called directly), never read back through pricebt; the shapes the use cases care
about (a straddle's V, a fly's tent) are asserted as shapes too. The gs notebooks these mirror are
under gs_quant/documentation/02_pricing_and_risk/01_scenarios_and_contexts/examples.
"""
from __future__ import annotations

import dataclasses
import math
import warnings
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import pricebt.risk as risk
import toylib.rates as tr
import toylib.scenarios as tsc
import toylib.swaption as ts
from pricebt.assets import load_asset, yamlio
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.base import Scenario
from pricebt.data import measure_series
from pricebt.errors import ConfigError, NotSupportedError
from pricebt.instrument import IRSwap, IRSwaption
from pricebt.markets import CloseMarket, HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import (
    CarryScenario,
    CompositeScenario,
    CurveOverlay,
    CurveScenario,
    IndexCurveShift,
    LiborFallbackScenario,
    MarketDataPattern,
    MarketDataPatternAndShock,
    MarketDataScenario,
    MarketDataShock,
    MarketDataShockBasedScenario,
    MarketDataShockType,
    MarketDataVolShockScenario,
    MeasureScenario,
    MultipleScenarioResult,
    MultiScenario,
    RollFwd,
)
from pricebt.risk.results import MultipleRiskMeasureResult, PortfolioRiskResult
from pricebt.risk.scenarios import scenario_dict, scenario_specs
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
SWAPTION = ASSETS / "toy_usd_swaption.yaml"
SWAP = ASSETS / "toy_usd_irs_full.yaml"
D = date(2024, 3, 4)  # a Monday
D2 = date(2024, 7, 15)
N = 1e6


# ------------------------------------------------------------------------------------ helpers


@pytest.fixture
def session():
    s = PricebtSession.use(assets=[SWAPTION, SWAP])
    PricingContext.current = PricingContext(D)
    return s


def shock(bp, kind="Absolute", mkt_type="IR", asset="USD", name=None):
    value = bp / 1e4 if kind == "Absolute" else bp
    return MarketDataShockBasedScenario({MarketDataPattern(mkt_type, asset): MarketDataShock(kind, value)}, name=name or f"{mkt_type} {bp:+g}")


def swaption(pay_or_receive="Pay", strike="ATM", exp="1y", term="10y", name=None):
    inst = IRSwaption(pay_or_receive, term, "USD", expiration_date=exp, strike=strike, notional_amount=N, name=name)
    inst.resolve()
    return inst


def swap(pay_or_receive="Pay", term="10y", fixed_rate="ATM", name=None):
    inst = IRSwap(pay_or_receive, term, "USD", fixed_rate=fixed_rate, notional_amount=N, name=name)
    inst.resolve()
    return inst


def base_market(d=D):
    return ts.market(d, "USD")


def toy_npv(inst, curve, sigma=None, value_date=None):
    """The toy swaption's PV on a hand-built market (toylib.swaption directly)."""
    m = SimpleNamespace(curve=curve, sigma=base_market().sigma if sigma is None else sigma)
    return ts.npv(m, inst.resolved_terms, value_date)


def curve_at(d, dz=0.0, **kw):
    return dataclasses.replace(base_market().curve, ref_date=d, zero_rate=base_market().curve.zero_rate + dz, **kw)


def intrinsic(inst, curve):
    """N * A * max(+-(F - K), 0) summed over the legs, on `curve` at expiry (the terminal payoff)."""
    t = inst.resolved_terms
    ann = tr._annuity(curve, t["expiration_date"], t["termination_date"])
    f, k = tr._par_rate(curve, t["expiration_date"], t["termination_date"]), t["strike"]
    legs = {"pay": (1,), "receive": (-1,), "straddle": (1, -1)}[str(t["pay_or_receive"]).lower()]
    return t["notional"] * ann * sum(max(sign * (f - k), 0.0) for sign in legs)


# ==================================================================================== the classes


def test_scenarios_are_gs_context_managers_innermost_first():
    a, b = RollFwd(date="1m", name="a"), shock(1, name="b")
    assert Scenario.path == () and Scenario.current is None
    with a:
        with b:
            assert Scenario.path == (b, a)  # gs ContextBase.path: innermost first
            assert Scenario.current is b and a.is_entered and b.is_entered
        assert Scenario.path == (a,) and not b.is_entered
    with pytest.raises(RuntimeError):
        with a:
            raise RuntimeError("boom")
    assert Scenario.path == ()


def test_lists_become_tuples_strings_become_enums_and_scenarios_hash():
    s = CurveScenario(MarketDataPattern("IR", "USD"), parallel_shift=5, shock_type="absolute")
    assert s.shock_type is MarketDataShockType.Absolute
    m = MultiScenario(scenarios=[s, RollFwd(date="1m")])
    assert isinstance(m.scenarios, tuple)
    o = CurveOverlay(dates=["2025-01-02", date(2026, 1, 2)], discount_factors=[0.97, 0.94])
    assert o.dates == (date(2025, 1, 2), date(2026, 1, 2)) and o.discount_factors == (0.97, 0.94)
    assert {s: 1, m: 2, o: 3}[CurveScenario(MarketDataPattern("IR", "USD"), parallel_shift=5, shock_type="Absolute")] == 1
    assert LiborFallbackScenario().fallback_type.value == "RFR" and RollFwd().realise_fwd is True and CarryScenario().roll_to_fwds is True
    with pytest.raises(ValueError, match="2 dates but 1 discount"):
        CurveOverlay(dates=[date(2025, 1, 2), date(2026, 1, 2)], discount_factors=[0.97])


def test_shock_based_scenario_takes_a_mapping_or_items():
    p, k = MarketDataPattern("IR", "USD"), MarketDataShock(MarketDataShockType.Absolute, 1e-4)
    a = MarketDataShockBasedScenario({p: k})
    b = MarketDataShockBasedScenario((MarketDataPatternAndShock(p, k),))
    assert a == b and a.shocks == (MarketDataPatternAndShock(p, k),) and a.scenario_type == "MarketDataShockBasedScenario"
    with pytest.raises(TypeError, match="mapping"):
        MarketDataShockBasedScenario([(p, k)])
    assert a.clone(name="x").name == "x" and a.clone(name="x").shocks == a.shocks
    assert a.as_dict()["shocks"] == a.shocks and "name" not in a.as_dict()


def test_repr_is_the_name_or_the_fields_and_the_order_is_total():
    assert repr(shock(5, name="up 5")) == "up 5"
    assert repr(RollFwd(date="1m")) == "RollFwd(date:'1m', realise_fwd:True)"
    unnamed = [RollFwd(date="2m"), RollFwd(date="1m"), CarryScenario(date="1m")]
    assert [repr(s) for s in sorted(unnamed)] == sorted(repr(s) for s in unnamed)  # pricebt DEV-S6 (gs: TypeError)


def test_scenario_specs_flatten_outermost_first_and_expand_the_multiscenario_in_place():
    roll, up, dn, vol = RollFwd(date="1m"), shock(10), shock(-10), shock(1, mkt_type="IR Vol")
    multi = MultiScenario([up, CompositeScenario([dn, vol])])
    assert scenario_specs((vol, CompositeScenario((roll, up)))) == [(None, (roll, up, vol))]
    specs = scenario_specs((vol, multi, roll))  # path innermost first: roll outermost
    assert specs == [(up, (roll, up, vol)), (multi.scenarios[1], (roll, dn, vol, vol))]
    with pytest.raises(NotSupportedError, match="more than one MultiScenario"):
        scenario_specs((multi, MultiScenario([roll])))
    with pytest.raises(NotSupportedError, match="inside a MultiScenario"):
        scenario_specs((MultiScenario([MultiScenario([up])]),))
    with pytest.raises(ValueError, match="no scenarios"):
        scenario_specs((MultiScenario([]),))
    with pytest.raises(ValueError, match="duplicate members"):
        scenario_specs((MultiScenario([shock(10), shock(10)]),))  # pricebt DEV-S3 (gs keeps one, silently)


def test_the_transform_sees_plain_data():
    from pricebt.common import Currency

    d = scenario_dict(MarketDataShockBasedScenario({MarketDataPattern("IR", Currency.USD): MarketDataShock("Proportional", 0.1)}))
    assert d["scenario_type"] == "MarketDataShockBasedScenario"
    (item,) = d["shocks"]
    assert item["pattern"]["mkt_type"] == "IR" and item["pattern"]["mkt_asset"] == "USD"
    assert item["shock"] == {"shock_type": "Proportional", "value": 0.1, "precision": None, "cap": None, "floor": None, "coordinate_cap": None, "coordinate_floor": None, "name": None}
    m = scenario_dict(MeasureScenario(risk.IRFwdRate, 400.0))
    assert m["risk_measure"] == "IRFwdRate" and m["value"] == 400.0


def test_roll_targets():
    assert RollFwd(date=date(2024, 6, 3))._roll_target(D) == date(2024, 6, 3)
    assert RollFwd(date="2024-06-03")._roll_target(D) == date(2024, 6, 3)
    assert RollFwd(date="1m")._roll_target(D) == date(2024, 4, 4)
    assert RollFwd(date="5b")._roll_target(D) == date(2024, 3, 11)  # weekends skipped
    assert RollFwd(date="5b", holiday_calendar=[date(2024, 3, 6)])._roll_target(D) == date(2024, 3, 12)
    assert CarryScenario(date="1m", roll_to_fwds=False)._roll_target(D) == date(2024, 4, 4)
    with pytest.raises(ValueError, match="rolls back"):
        RollFwd(date=date(2024, 1, 2))._roll_target(D)
    with pytest.raises(ValueError, match="needs a date"):
        RollFwd()._roll_target(D)
    with pytest.raises(NotSupportedError, match="time_shift"):
        CarryScenario(time_shift=3)._roll_target(D)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        import pricebt.risk.scenarios as rs

        rs._LOCATION_WARNED = False
        assert RollFwd(date="5b", holiday_calendar="NYC")._roll_target(D) == date(2024, 3, 11)
    assert any("weekends only" in str(x.message) for x in w)


def test_gs_import_paths():
    import pricebt.common as common
    import pricebt.risk.scenarios as scenarios
    import pricebt.target.common as target_common

    assert common.MeasureScenario is MeasureScenario is target_common.MeasureScenario
    assert scenarios.MarketDataShockBasedScenario is MarketDataShockBasedScenario
    assert issubclass(RollFwd, Scenario) and risk.Scenario is Scenario


# ==================================================================================== the config section


def _swaption_mapping(**changes):
    raw = dict(yamlio.load_file(SWAPTION))
    raw.update(changes)
    return raw


def test_the_scenarios_section_loads_and_compiles():
    cfg = load_asset(SWAPTION)
    assert cfg.scenarios["RollFwd"].scope == "market" and cfg.scenarios["MeasureScenario"].scope == "trade"
    assert cfg.code("scenarios.RollFwd") is not None


@pytest.mark.parametrize(
    "scenarios, match",
    [
        ({"RollFwds": "market"}, "unknown scenario type 'RollFwds'.*did you mean 'RollFwd'"),
        ({"MultiScenario": "market"}, "needs no entry"),
        ({"RollFwd": {"expr": "market", "scope": "book"}}, "scope 'book'"),
        ({"RollFwd": {"expr": "market", "when": "always"}}, "unknown key 'when'"),
        ({"RollFwd": "market +"}, "syntax error"),
        ({"RollFwd": {"scope": "trade"}}, "non-empty string"),
    ],
)
def test_a_bad_scenarios_section_is_a_load_error(scenarios, match):
    with pytest.raises(ConfigError, match=match):
        load_asset(_swaption_mapping(asset="bad", scenarios=scenarios))


def test_a_user_scenario_subclass_can_be_mapped(session):
    @dataclasses.dataclass(unsafe_hash=True, repr=False)
    class TwistScenario(Scenario):
        bp: float = 0.0
        scenario_type: str = dataclasses.field(init=False, default="TwistScenario")
        name: str = None

    cfg = load_asset(_swaption_mapping(asset="twisty", scenarios={"TwistScenario": "type(market)(curve=dataclasses.replace(market.curve, zero_rate=market.curve.zero_rate + scenario['bp'] / 1e4), sigma=market.sigma)"}, code="import dataclasses\n"))
    s = PricebtSession.use(assets=[cfg])
    PricingContext.current = PricingContext(D)
    inst = swaption()
    with TwistScenario(bp=25):
        got = inst.price()
    assert got == pytest.approx(toy_npv(inst, curve_at(D, 25e-4)), rel=1e-12)
    assert s.pricing._scenario_cache


# ==================================================================================== shocks


def test_a_rate_shock_reprices_on_the_shocked_market(session):
    pay, sw = swaption(), swap()
    with shock(50):
        got_pay, got_swap = pay.price(), sw.price()
    assert got_pay == pytest.approx(toy_npv(pay, curve_at(D, 50e-4)), rel=1e-12)
    import toylib.irrisk as tri

    assert got_swap == pytest.approx(tri.npv(curve_at(D, 50e-4), tr.build_swap(None, sw.resolved_terms), D), rel=1e-12)
    assert got_pay > pay.price()  # a payer gains when rates rise


def test_delta_shock_equivalent_010204(session):
    """gs: "We calculate delta as a 2-sided 1bp bump": the scenario prices recover the own-rate
    IRDelta exactly (the toy delta is the same two bumps over the own-rate move, DEV-I12)."""
    inst = swaption("Straddle")
    with shock(1):
        up, f_up = inst.price(), inst.calc(risk.IRFwdRate)
    with shock(-1):
        dn, f_dn = inst.price(), inst.calc(risk.IRFwdRate)
    assert (up - dn) / (f_up - f_dn) == pytest.approx(inst.calc(risk.IRDelta(aggregation_level="Type")), rel=1e-9)


def test_vega_shock_equivalent_010205(session):
    inst = swaption("Receive", strike="ATM-25")
    with shock(1, mkt_type="IR Vol", asset=None):
        up = inst.price()
    with shock(-1, mkt_type="IR Vol", asset=None):
        dn = inst.price()
    assert (up - dn) / 2 == pytest.approx(inst.calc(risk.IRVegaParallel), rel=1e-5)  # central difference vs analytic: O(h^2)


def test_proportional_and_override_shocks(session):
    inst = swaption()
    z, s = base_market().curve.zero_rate, base_market().sigma
    with shock(0.1, kind="Proportional"):
        assert inst.price() == pytest.approx(toy_npv(inst, curve_at(D, z * 0.1)), rel=1e-12)
    with shock(0.05, kind="Override"):
        assert inst.price() == pytest.approx(toy_npv(inst, curve_at(D, 0.05 - z)), rel=1e-12)
    with shock(0.012, kind="Override", mkt_type="IR Vol", asset=None):
        assert inst.price() == pytest.approx(toy_npv(inst, curve_at(D), sigma=0.012), rel=1e-12)
        assert inst.calc(risk.IRAnnualImpliedVol) == pytest.approx(120.0)
    assert s != 0.012


def test_data_the_market_does_not_hold_is_left_alone(session):
    pay, sw = swaption(), swap()
    base = (pay.price(), sw.price())
    for other in (shock(50, asset="EUR"), shock(0.1, kind="Proportional", mkt_type="FX", asset="EUR/USD"), shock(5, mkt_type="Eq", asset=".SPX")):
        with other:
            assert (pay.price(), sw.price()) == pytest.approx(base, rel=1e-15)
    with shock(1, mkt_type="IR Vol", asset=None):
        assert sw.price() == pytest.approx(base[1], rel=1e-15)  # a swap holds no vol


def test_composites_apply_outermost_first(session):
    """Proportional and Absolute shocks do not commute, so the order shows."""
    inst = swaption()
    z = base_market().curve.zero_rate
    prop, absolute = shock(0.1, kind="Proportional"), shock(50)
    with prop:
        with absolute:
            nested = inst.price()
    with CompositeScenario([prop, absolute]):
        explicit = inst.price()
    with absolute, prop:
        reverse = inst.price()
    assert nested == explicit == pytest.approx(toy_npv(inst, curve_at(D, z * 1.1 + 50e-4 - z)), rel=1e-12)
    assert reverse == pytest.approx(toy_npv(inst, curve_at(D, (z + 50e-4) * 1.1 - z)), rel=1e-12)
    assert abs(nested - reverse) > 1.0


# ==================================================================================== curve shapes


def test_curve_scenario_parallel_shift_is_a_rate_shock(session):
    inst = swaption()
    with CurveScenario(MarketDataPattern("IR", "USD"), parallel_shift=10):
        a = inst.price()
    with shock(10):
        b = inst.price()
    assert a == pytest.approx(b, rel=1e-14)


@pytest.mark.parametrize(
    "curve_shift, pivot, at15, at25",
    [
        (10, 15, 0.0, 10.0),     # 010402 bear steepener: the long end up
        (10, 25, -10.0, 0.0),    # 010403 bull steepener: the short end down
        (-10, 25, 10.0, 0.0),    # 010404 bear flattener: the short end up
        (-10, 15, 0.0, -10.0),   # 010405 bull flattener: the long end down
        (10, None, -5.0, 5.0),   # 010401 no pivot: the midpoint
    ],
)
def test_curve_scenario_shapes(session, curve_shift, pivot, at15, at25):
    s = CurveScenario(MarketDataPattern("IR", "USD"), curve_shift=curve_shift, tenor_start=15, tenor_end=25, pivot_point=pivot)
    curve = tsc.curve_scenario(curve_at(D), scenario_dict(s))
    for years, bp in ((5, at15), (15, at15), (20, (at15 + at25) / 2), (25, at25), (40, at25)):
        assert tr.shape_value(curve.shape, years) * 1e4 == pytest.approx(bp, abs=1e-9)
    sw = swap(term="30y")
    with s:
        got = sw.price()
    import toylib.irrisk as tri

    hand = dataclasses.replace(curve_at(D), shape=((15.0, at15 / 1e4), (25.0, at25 / 1e4)))
    assert got == pytest.approx(tri.npv(hand, tr.build_swap(None, sw.resolved_terms), D), rel=1e-12)


def test_index_curve_shift_parallel_plus_slope_to_a_cutoff(session):
    s = IndexCurveShift(MarketDataPattern("IR", "USD"), annualised_parallel_shift=5, annualised_slope_shift=1, cutoff=10)
    sw = swap(term="30y")
    with s:
        got = sw.price()
    import toylib.irrisk as tri

    hand = dataclasses.replace(curve_at(D, 5e-4), shape=((0.0, 0.0), (10.0, 10e-4)))
    assert got == pytest.approx(tri.npv(hand, tr.build_swap(None, sw.resolved_terms), D), rel=1e-12)
    with IndexCurveShift(MarketDataPattern("IR", "USD"), bucket_shift=1, bucket_start=date(2030, 1, 1), bucket_end=date(2031, 1, 1)):
        with pytest.raises(Exception) as err:
            sw.price()
    assert "bucket_shift" in str(err.value.__cause__)  # the toy library's own refusal, surfaced


def _overlay(z):
    dates = [date(2024 + i, 3, 4) for i in range(1, 45)]
    return dates, [math.exp(-z * (d - D).days / 365.0) for d in dates]


def test_curve_overlay_replaces_the_curve(session):
    """010700: the discount factors of a flat 4.5% curve reproduce that curve exactly."""
    inst = swaption()
    dates, dfs = _overlay(0.045)
    with CurveOverlay(dates=dates, discount_factors=dfs, denominated="USD", csa_term="USD-SOFR", curve_type="Discount Curve"):
        got = inst.price()
    assert got == pytest.approx(toy_npv(inst, curve_at(D, 0.045 - base_market().curve.zero_rate)), rel=1e-10)
    base = inst.price()
    with CurveOverlay(dates=dates, discount_factors=dfs, denominated="EUR"):
        assert inst.price() == pytest.approx(base, rel=1e-15)  # another currency's curve


def test_subtract_base_gives_the_change(session):
    """pricebt DEV-S5: a scalar is overlay minus base; a bucketed ladder subtracts per bucket."""
    inst = swaption()
    dates, dfs = _overlay(0.045)
    with CurveOverlay(dates=dates, discount_factors=dfs, denominated="USD"):
        full, full_ladder = inst.price(), inst.calc(risk.IRDelta)
    with CurveOverlay(dates=dates, discount_factors=dfs, denominated="USD", subtract_base=True):
        change, ladder_change = inst.price(), inst.calc(risk.IRDelta)
    base, base_ladder = inst.price(), inst.calc(risk.IRDelta)
    assert change == pytest.approx(full - base, rel=1e-12)
    assert change.risk_key.scenario.scenario.subtract_base is True
    assert list(ladder_change["value"]) == pytest.approx(list(full_ladder["value"] - base_ladder["value"]), rel=1e-12)


# ==================================================================================== rolls


def test_toy_roll_realises_forwards_exactly_on_a_sloped_curve():
    """The checker before the check: the toy's forward roll equals the translated curve's DFs, a
    spot roll the re-anchored curve's, and on a sloped curve the two differ."""
    import toylib.irrisk as tri

    c = tr.ToyCurve(D, "USD", 0.03, None, 0.004)
    t1 = date(2024, 9, 4)
    fwd = tsc.roll(c, {"realise_fwd": True}, D, t1)
    spot = tsc.roll(c, {"realise_fwd": False}, D, t1)
    translated = tri._TranslatedCurve(c, (t1 - D).days)
    for x in (date(2025, 1, 2), date(2034, 3, 4), date(2054, 3, 4)):
        assert fwd.discount_factor(x) == pytest.approx(translated.discount_factor(x), rel=1e-13)
        assert spot.discount_factor(x) == pytest.approx(dataclasses.replace(c, ref_date=t1).discount_factor(x), rel=1e-15)
        assert fwd.discount_factor(x) != pytest.approx(spot.discount_factor(x), rel=1e-6)
    assert fwd.path_start == spot.path_start == D


def test_rollfwd_moves_the_valuation_date(session):
    inst = swaption(exp="1y")
    exp = inst.resolved_terms["expiration_date"]
    with RollFwd(date="6m"):
        assert inst.calc(risk.ExpiryInYears) == pytest.approx((exp - date(2024, 9, 4)).days / 365.0)
    with RollFwd(date="6m"), RollFwd(date="6m"):  # rolls compose
        assert inst.calc(risk.ExpiryInYears) == pytest.approx((exp - date(2025, 3, 4)).days / 365.0)
    with CarryScenario(date="6m"):
        carry = inst.price()
    with RollFwd(date="6m"):
        assert inst.price() == pytest.approx(carry, rel=1e-15)
    with CarryScenario(time_shift=1):
        with pytest.raises(NotSupportedError, match="time_shift"):
            inst.price()


def test_rollfwd_lifecycles_a_swaption_03(session):
    """gs 03 lifecycling notebook: past expiry the ITM swaption is the underlying swap, the OTM one
    is worth nothing."""
    itm, otm = swaption("Receive", strike="ATM+20", exp="1m"), swaption("Receive", strike="ATM-20", exp="1m")
    t1 = date(2024, 6, 4)
    with RollFwd(date=t1):
        got_itm, got_otm = itm.price(), otm.price()
    t = itm.resolved_terms
    rolled = curve_at(t1)  # flat curve: realising the forwards keeps the zero rate
    swap_value = t["notional"] * tr._annuity(rolled, t["expiration_date"], t["termination_date"]) * (t["strike"] - tr._par_rate(rolled, t["expiration_date"], t["termination_date"]))
    assert got_itm == pytest.approx(swap_value, rel=1e-12) and got_itm > 0
    assert got_otm == 0.0


# ==================================================================================== terminal payoffs (the use case)

GRID = (-100, -50, -25, -10, 0, 10, 25, 50, 100)


def test_terminal_payoff_of_a_straddle(session):
    """Roll to expiry, then shock: the straddle's value is N * A * |F - K| on each shocked
    curve -- a V with its point at the strike."""
    straddle = swaption("Straddle", name="straddle")
    exp = straddle.resolved_terms["expiration_date"]
    members = [shock(bp, name=f"{bp:+d}bp") for bp in GRID]
    with RollFwd(date=exp):
        with MultiScenario(members):
            payoff = straddle.price()
            fwd = straddle.calc(risk.IRFwdRate)
    assert isinstance(payoff, MultipleScenarioResult) and list(payoff) == members
    for bp, member in zip(GRID, members):
        assert payoff[member] == pytest.approx(intrinsic(straddle, curve_at(exp, bp / 1e4)), rel=1e-12, abs=1e-6)
    values = [payoff[m] for m in members]
    assert payoff[members[GRID.index(0)]] == pytest.approx(0.0, abs=1e-6)  # ATM: the forward realises at the strike
    assert all(a > b for a, b in zip(values[:4], values[1:5])) and all(a < b for a, b in zip(values[4:], values[5:]))
    k = straddle.resolved_terms["strike"] * 1e4
    assert [fwd[m] - k for m in members] == pytest.approx([bp * 1.0 for bp in GRID], rel=0.06, abs=1e-6)  # F moves ~1:1 with the shock


def test_straddle_is_payer_plus_receiver_under_every_member(session):
    pay, rec, straddle = swaption("Pay", name="pay"), swaption("Receive", name="rec"), swaption("Straddle", name="straddle")
    exp = straddle.resolved_terms["expiration_date"]
    members = [shock(bp) for bp in GRID]
    with RollFwd(date=exp), MultiScenario(members):
        legs = Portfolio([pay, rec]).price()
        whole = straddle.price()
    total = legs.aggregate()
    assert isinstance(total, MultipleScenarioResult)
    for m in members:
        assert total[m] == pytest.approx(whole[m], rel=1e-12, abs=1e-6)


def test_terminal_payoff_of_a_fly_is_a_tent(session):
    """+1 payer K-50, -2 payers K, +1 payer K+50: zero beyond +-50bp, peak N * A * 50bp at K."""
    lo, mid, hi = (swaption("Pay", strike=s, name=n) for s, n in (("ATM-50", "lo"), ("ATM", "mid"), ("ATM+50", "hi")))
    fly = Portfolio([lo, mid.clone(quantity_=-2), hi], name="fly")
    exp = mid.resolved_terms["expiration_date"]
    members = [shock(bp, name=f"{bp:+d}bp") for bp in GRID]
    with RollFwd(date=exp):
        with MultiScenario(members):
            result = fly.price()
    payoff = result.aggregate()
    for bp, m in zip(GRID, members):
        c = curve_at(exp, bp / 1e4)
        assert payoff[m] == pytest.approx(intrinsic(lo, c) - 2 * intrinsic(mid, c) + intrinsic(hi, c), rel=1e-10, abs=1e-6)
        if abs(bp) >= 50:
            assert payoff[m] == pytest.approx(0.0, abs=1e-6)
    peak = payoff[members[GRID.index(0)]]
    t = mid.resolved_terms
    assert peak == pytest.approx(t["notional"] * tr._annuity(curve_at(exp), t["expiration_date"], t["termination_date"]) * 50e-4, rel=1e-9)
    assert peak == max(payoff.values())
    frame = payoff.to_frame()
    assert list(frame.columns) == members and frame.loc["value", members[GRID.index(0)]] == pytest.approx(peak)
    by_leg = result.to_frame()
    assert list(by_leg.index) == ["lo", "mid", "hi"] and list(by_leg.columns) == members


# ==================================================================================== MultiScenario shapes


def test_multiscenario_result_shapes(session):
    pay, rec = swaption("Pay", name="pay"), swaption("Receive", name="rec")
    up, dn = shock(10, name="up"), shock(-10, name="dn")
    port = Portfolio([pay, rec])
    with MultiScenario([up, dn]):
        one = pay.price()
        many = pay.calc((risk.Price, risk.IRFwdRate))
        prr = port.calc((risk.Price, risk.IRFwdRate))
        prr1 = port.price()
    with up:
        pay_up = pay.price()
    assert isinstance(one, MultipleScenarioResult) and tuple(one.scenarios) == (up, dn) and one[up] == pay_up
    assert isinstance(many, MultipleRiskMeasureResult) and isinstance(many[risk.Price], MultipleScenarioResult)
    sliced = many[up]  # gs: each measure keeps a one-member scenario result
    assert isinstance(sliced, MultipleRiskMeasureResult) and list(sliced[risk.Price]) == [up]
    assert prr._multi_scen_key == (up, dn)
    assert isinstance(prr[up], PortfolioRiskResult) and list(prr[up][pay][risk.Price]) == [up]
    assert list(prr[[up, dn]][pay][risk.Price]) == [up, dn]
    with pytest.raises(ValueError, match="not computed"):
        prr[shock(5)]
    with pytest.raises(ValueError, match="multiple scenario"):
        pay.calc((risk.Price, risk.IRFwdRate))[up]
    frame = prr1.to_frame()
    assert list(frame.columns) == [up, dn] and list(frame.index) == ["pay", "rec"]
    assert frame.loc["pay", up] == pytest.approx(pay_up)
    multi_frame = prr.to_frame()
    assert list(multi_frame.columns) == [(risk.Price, up), (risk.Price, dn), (risk.IRFwdRate, up), (risk.IRFwdRate, dn)]
    raw = prr1.to_frame(None, None, None)
    assert set(raw.columns) >= {"instrument_name", "risk_measure", "scenario", "value"} and len(raw) == 4
    with up:
        rec_up = rec.price()
    totals = prr.aggregate()
    assert isinstance(totals, MultipleRiskMeasureResult) and isinstance(totals[risk.Price], MultipleScenarioResult)
    assert totals[risk.Price][up] == pytest.approx(pay_up + rec_up, rel=1e-14)
    assert one.to_frame().loc["value", up] == pytest.approx(pay_up)


def test_multiscenario_ladders_aggregate_per_member(session):
    pay, rec = swaption("Pay", name="pay"), swaption("Receive", term="5y", name="rec")
    port = Portfolio([pay, rec])
    up, dn = shock(25, name="up"), shock(-25, name="dn")
    with MultiScenario([up, dn]):
        ladders = port.calc(risk.IRDelta)
    agg = ladders.aggregate()
    assert isinstance(agg, MultipleScenarioResult)
    for m in (up, dn):
        with m:
            direct = port.calc(risk.IRDelta).aggregate()
        assert list(agg[m]["value"]) == pytest.approx(list(direct["value"]), rel=1e-12)
    assert list(agg[up]["value"]) != pytest.approx(list(agg[dn]["value"]), rel=1e-6)


def test_multiscenario_over_a_historical_context(session):
    inst = swaption()
    up, dn = shock(10, name="up"), shock(-10, name="dn")
    days = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]
    with HistoricalPricingContext(dates=days), MultiScenario([up, dn]):
        fut = inst.price()
    res = fut.result()
    assert isinstance(res, MultipleScenarioResult) and list(res[up].index) == days
    for d in days:
        with PricingContext(d), up:
            want = inst.price()
        assert res[up][d] == pytest.approx(want.result(), rel=1e-12)
    on_day = res[days[1]]
    assert isinstance(on_day, MultipleScenarioResult) and float(on_day[dn]) == pytest.approx(res[dn][days[1]])


def test_a_historical_context_applies_the_scenario_per_date(session):
    inst = swaption()
    days = [date(2024, 3, 4), date(2024, 3, 11)]
    with HistoricalPricingContext(dates=days), shock(20):
        series = inst.price().result()
    for d in days:
        m = ts.market(d, "USD")
        hand = SimpleNamespace(curve=dataclasses.replace(m.curve, zero_rate=m.curve.zero_rate + 20e-4), sigma=m.sigma)
        assert series[d] == pytest.approx(ts.npv(hand, inst.resolved_terms, d), rel=1e-12)


def test_fn_under_a_multiscenario_gets_the_scenario_result(session):
    inst = swaption()
    up, dn = shock(10, name="up"), shock(-10, name="dn")
    with MultiScenario([up, dn]):
        spread = inst.calc(risk.Price, fn=lambda r: r[up] - r[dn])
    assert spread > 0


def test_mixed_scenario_types_in_one_multiscenario(session):
    """pricebt DEV-S3 (gs: one type per MultiScenario)."""
    inst = swaption(exp="1m")
    members = [shock(10, name="shock"), RollFwd(date="1m", name="roll"), CompositeScenario([RollFwd(date="1m"), shock(10)], name="both")]
    with MultiScenario(members):
        res = inst.price()
    with RollFwd(date="1m"), shock(10):
        both = inst.price()
    assert res[members[2]] == pytest.approx(both, rel=1e-15)
    assert len({round(float(v), 6) for v in res.values()}) == 3


# ==================================================================================== resolution, caching, keys


def test_values_never_share_a_cache_with_the_base_or_each_other(session):
    inst = swaption()
    base = inst.price()
    with shock(10):
        a = inst.price()
    with shock(-10):
        b = inst.price()
    assert inst.price() == base and a != base and b != base and a != b
    with PricingContext(is_async=True):
        lad = inst.calc(risk.IRDelta)
        with shock(10):
            lad_a = inst.calc(risk.IRDelta)
    assert lad.group_key != lad_a.group_key and lad.group_key[2] == lad_a.group_key[2] == D and lad.group_key[-1] == lad_a.group_key[-1] == ()


def test_results_under_different_scenarios_never_aggregate(session):
    inst = swaption()
    base = inst.price()
    up = shock(10)
    with up:
        shocked = inst.price()
    assert base.risk_key.scenario is None and shocked.risk_key.scenario == MarketDataScenario(scenario=up)
    with pytest.raises(ValueError, match="different pricing keys"):
        risk.aggregate_results([base, shocked])


def test_an_unresolved_instrument_resolves_under_the_scenario(session):
    """gs RollFwd notebook: "if you don't resolve the trade, the resolution ... will be done with
    reference to the active pricing context" (pricebt DEV-S1)."""
    fresh = IRSwaption("Pay", "10y", "USD", expiration_date="1y", strike="ATM", notional_amount=N)
    with shock(50):
        pinned = fresh.resolve(in_place=False)
    shocked = curve_at(D, 50e-4)
    exp = pinned.resolved_terms["expiration_date"]
    assert pinned.resolved_terms["strike"] == pytest.approx(tr._par_rate(shocked, exp, pinned.resolved_terms["termination_date"]), rel=1e-14)
    with RollFwd(date="6m"):
        rolled = fresh.resolve(in_place=False)
    assert rolled.resolved_terms["expiration_date"] == date(2025, 9, 4)  # 1y from the rolled date
    assert fresh.resolved_terms is None


def test_resolve_under_a_multiscenario(session):
    fresh = IRSwaption("Pay", "10y", "USD", expiration_date="1y", notional_amount=N)
    up, dn = shock(10), shock(-10)
    with MultiScenario([up, dn]):
        with pytest.raises(RuntimeError, match="in place under a MultiScenario"):
            fresh.resolve()
        by_member = fresh.resolve(in_place=False)
    assert isinstance(by_member, MultipleScenarioResult)
    assert by_member[up].resolved_terms["strike"] > by_member[dn].resolved_terms["strike"]


def test_futures_taken_inside_the_context_keep_their_scenario(session):
    """gs notebooks collect futures under PricingContext(is_async=True) inside the scenario."""
    inst = swaption()
    with PricingContext(is_async=True), shock(30):
        price, ladder = inst.price(), inst.calc(risk.IRDelta)
    with shock(30):
        want_price, want_ladder = inst.price(), inst.calc(risk.IRDelta)
    assert price.result() == want_price and list(ladder.result()["value"]) == list(want_ladder["value"])


def test_a_portfolio_under_one_scenario_matches_its_instruments(session):
    pay, sw = swaption(name="pay"), swap(name="swap")
    with RollFwd(date="3m"), shock(15):
        prr = Portfolio([pay, sw]).price()
        each = (pay.price(), sw.price())
    assert (prr[pay], prr[sw]) == each and float(prr.aggregate()) == pytest.approx(sum(each))


# ==================================================================================== MeasureScenario (scope: trade)


def _fwd_shift_for(inst, target_bp, sigma=None):
    """Bisection (independent of the toy's secant): the parallel zero shift putting F at target."""
    t = inst.resolved_terms
    lo, hi = -0.05, 0.05
    for _ in range(200):
        mid = (lo + hi) / 2
        f = tr._par_rate(curve_at(D, mid), t["expiration_date"], t["termination_date"]) * 1e4
        lo, hi = (mid, hi) if f < target_bp else (lo, mid)
    return (lo + hi) / 2


def test_measure_scenario_sets_the_trades_own_rate_010902(session):
    a, b = swaption(name="a"), swaption(term="5y", exp="2y", name="b")
    target = a.calc(risk.IRFwdRate) + 1.0
    with MeasureScenario(risk.IRFwdRate, target):
        fa, fb, pa = a.calc(risk.IRFwdRate), b.calc(risk.IRFwdRate), a.price()
    assert fa == pytest.approx(target, abs=1e-8) and fb == pytest.approx(target, abs=1e-8)  # each trade its own market
    assert pa == pytest.approx(toy_npv(a, curve_at(D, _fwd_shift_for(a, target))), rel=1e-7)
    with MeasureScenario(risk.IRFwdRate, target), MeasureScenario(risk.IRAnnualImpliedVol, 90.0):
        assert a.calc(risk.IRAnnualImpliedVol) == pytest.approx(90.0)
        assert a.price() == pytest.approx(toy_npv(a, curve_at(D, _fwd_shift_for(a, target)), sigma=0.009), rel=1e-7)


def test_measure_scenario_groups_each_trade_on_its_own_market(session):
    a, b = swaption(name="a"), swaption(term="5y", exp="2y", name="b")
    target = 450.0
    with PricingContext(is_async=True), MeasureScenario(risk.IRFwdRate, target):
        both = Portfolio([a, b]).calc(risk.IRDelta)
        la, lb = a.calc(risk.IRDelta), b.calc(risk.IRDelta)
    assert la.group_key != lb.group_key  # one group per trade
    total = both.aggregate()
    want = risk.aggregate_risk([la.result(), lb.result()])
    assert list(total["value"]) == pytest.approx(list(want["value"]), rel=1e-12)


def test_resolution_skips_a_per_trade_transform(session):
    """pricebt DEV-S1: resolving needs the market before the trade exists, so scope: trade
    transforms do not apply to it."""
    fresh = IRSwaption("Pay", "10y", "USD", expiration_date="1y", notional_amount=N)
    with MeasureScenario(risk.IRFwdRate, 500.0):
        pinned = fresh.resolve(in_place=False)
    base = swaption()
    assert pinned.resolved_terms["strike"] == pytest.approx(base.resolved_terms["strike"], rel=1e-15)


# ==================================================================================== refusals


def test_an_asset_without_the_scenario_type_raises_naming_it(session):
    cfg = load_asset(_swaption_mapping(asset="plain_swaption", scenarios={"RollFwd": "market"}))
    PricebtSession.use(assets=[cfg])
    PricingContext.current = PricingContext(D)
    inst = swaption()
    with shock(10):
        with pytest.raises(NotSupportedError, match="plain_swaption has no scenarios: entry for MarketDataShockBasedScenario"):
            inst.price()
    vol = MarketDataVolShockScenario.from_dataframe(".SPX", pd.DataFrame({"expirationDate": [pd.Timestamp("2025-01-17")], "absoluteStrike": [5000.0], "impliedVolatility": [0.2]}, index=[pd.Timestamp("2024-03-04")]))
    with vol, pytest.raises(NotSupportedError, match="MarketDataVolShockScenario"):
        inst.price()


def test_a_book_with_an_unmapped_asset_raises(session):
    PricebtSession.use(assets=[SWAPTION, ASSETS / "toy_usd_irs.yaml"])
    PricingContext.current = PricingContext(D)
    book = Portfolio([swaption(), swap()])
    with shock(10), pytest.raises(NotSupportedError, match="toy_usd_irs has no scenarios"):
        book.price()


def test_vol_shock_scenario_from_dataframe():
    idx = [pd.Timestamp("2024-03-01 10:00")] * 2 + [pd.Timestamp("2024-03-01 11:00")] * 3
    df = pd.DataFrame(
        {"expirationDate": pd.to_datetime(["2024-06-21", "2024-06-21", "2024-06-21", "2024-06-21", "2024-09-20"]), "absoluteStrike": [1.0, 2.0, 110.0, 90.0, 100.0], "impliedVolatility": [9.0, 9.0, 0.21, 0.23, 0.2]},
        index=idx,
    )
    s = MarketDataVolShockScenario.from_dataframe(".SPX", df, ref_spot=100.0, name="vols")
    assert s.pattern == MarketDataPattern("Eq Vol", ".SPX") and s.shock_type is MarketDataShockType.Override and s.ref_spot == 100.0
    assert [(v.date, v.strikes, v.levels) for v in s.vol_levels] == [(date(2024, 6, 21), (90.0, 110.0), (0.23, 0.21)), (date(2024, 9, 20), (100.0,), (0.2,))]


def test_backtests_and_measure_series_refuse_an_active_scenario(session):
    with shock(10):
        with pytest.raises(NotSupportedError, match="run_backtest under an active scenario"):
            GenericEngine().run_backtest(None, start=D, end=D)
        with pytest.raises(NotSupportedError, match="measure_series under an active scenario"):
            measure_series(IRSwaption("Pay", "10y", "USD"), "npv", D, D)


def test_pnl_explain_under_a_scenario_raises(session):
    inst = swaption()
    with shock(10), pytest.raises(NotSupportedError, match="under a scenario is not supported"):
        inst.calc(risk.PnlExplain(CloseMarket(date=D2)))


def test_a_transform_must_return_a_market(session):
    cfg = load_asset(_swaption_mapping(asset="none_swaption", scenarios={"MarketDataShockBasedScenario": "None"}))
    PricebtSession.use(assets=[cfg])
    PricingContext.current = PricingContext(D)
    inst = swaption()
    with shock(10), pytest.raises(ConfigError, match="returned None"):
        inst.price()


def test_the_transform_receives_the_dates_and_the_plain_scenario(session):
    cfg = load_asset(_swaption_mapping(asset="spy_swaption", scenarios={"RollFwd": "tr.CSA_SEEN.append(('roll', pricebt_from_date, pricebt_date, scenario)) or tsc.roll(market, scenario, pricebt_from_date, pricebt_date)"}, imports="import toylib.swaption as ts\nimport toylib.scenarios as tsc\nimport toylib.rates as tr\n"))
    PricebtSession.use(assets=[cfg])
    PricingContext.current = PricingContext(D)
    inst = swaption()
    tr.CSA_SEEN.clear()
    with RollFwd(date="1m", realise_fwd=False, name="1m spot"):
        inst.price()
    (call,) = [c for c in tr.CSA_SEEN if c[0] == "roll"]
    assert call[1:3] == (D, date(2024, 4, 4))
    assert call[3] == {"date": "1m", "realise_fwd": False, "holiday_calendar": None, "scenario_type": "RollFwd", "name": "1m spot"}


def test_a_close_market_override_composes_with_a_scenario(session):
    inst = swaption()
    with PricingContext(D, market=CloseMarket(date=D2)):
        with shock(10):
            fut = inst.price()
    m2 = ts.market(D2, "USD")
    hand = SimpleNamespace(curve=dataclasses.replace(m2.curve, zero_rate=m2.curve.zero_rate + 10e-4), sigma=m2.sigma)
    assert fut.result() == pytest.approx(ts.npv(hand, inst.resolved_terms, D), rel=1e-12)
    assert fut.result().risk_key.market == D2


def test_libor_fallback_is_the_identity_on_an_overnight_curve(session):
    inst = swaption()
    base = inst.price()
    with LiborFallbackScenario(date=date(2023, 6, 30)):
        assert inst.price() == pytest.approx(base, rel=1e-15)


def test_currency_conversion_uses_the_unshocked_fx_of_the_pricing_date():
    """pricebt DEV-S8: the FX config has no scenarios, even under a roll."""
    PricebtSession.use(assets=[SWAPTION], fx=ASSETS / "toy_fx.yaml")
    PricingContext.current = PricingContext(D)
    inst = swaption()
    usdeur = 1.0 / (1.10 + 0.01 * math.sin(2 * math.pi * tr.business_day_index(D) / 252))
    with RollFwd(date="3m"), shock(25, mkt_type="FX", asset="EUR/USD"), shock(25):
        usd, eur = inst.price(), inst.calc(risk.Price(currency="EUR"))
    assert float(eur) == pytest.approx(float(usd) * usdeur, rel=1e-12)
