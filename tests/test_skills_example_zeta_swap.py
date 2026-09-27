"""The service example (`skills/pricebt-wire-external-library/example-service/`): tests that pin the zeta Kit (the Kit tests of `pricebt-instrument-kit`, adapted): the default block alone conforms, the
factory resolves like the reference stack, every unit and sign agrees with the reference for both directions and two notionals, dv01 has ONE definition in every state, the ladder reducer, the
conventions decisions, the swap is plain data. Needs no pricing library (zeta is fictional): marker `core`.

The reference stack is asked for its risk numbers on zeta's TWELVE pillars (`tenors=ZETA_TENORS`): zeta's risk curve is fixed at twelve, the reference's default has eleven, and two risk numbers on
different pillar sets are two measures (tests/test_skills_example_zeta_proofs.py pins the size of the difference)."""
import copy
import datetime as dt
import math
import pickle
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "skills" / "pricebt-wire-external-library" / "example-service"
for _p in (str(EXAMPLE), str(EXAMPLE / "zeta_lib")):  # the example is NOT on pytest.ini's pythonpath: add it from this file's location, never from the working directory
    if _p not in sys.path:
        sys.path.insert(0, _p)

import zeta  # noqa: E402  (the fictional service client: a wrong path fails loudly here instead of skipping every test)

pytestmark = pytest.mark.core

import zeta_adapter as A  # noqa: E402
from zeta_adapter import _compat as C  # noqa: E402
from zeta_adapter import conventions as CV  # noqa: E402
from pricebt.contracts.binding import Env, call_binding  # noqa: E402
from pricebt.contracts.evaluate import evaluate_measure, evaluate_value  # noqa: E402
from pricebt.contracts.schema import SchemaRegistry  # noqa: E402
from pricebt.contracts.spec import TradeTemplate, build_spec  # noqa: E402
from pricebt.errors import ConfigError, MethodCallError  # noqa: E402
from pricebt.pricable import MarkContext, Valuation  # noqa: E402
from pricebt.snapshot import SnapshotPricer  # noqa: E402
from pricebt.testing import refstack as R  # noqa: E402
from pricebt.testing.layer_conformance import flat_swap_world  # noqa: E402

SCHEMAS = SchemaRegistry.default()
ALLOW = ("pricebt", "zeta_adapter")
HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
WORLD = flat_swap_world("cal", holidays=HOL)  # a snapshot with a calendar named "cal", a flat curve and consistent fixings
REF = dt.date(2024, 6, 12)
CONV = {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
REF_CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
TEN = list(A.ZETA_TENORS)


@pytest.fixture(autouse=True)
def own_client():
    C.set_client(zeta.Client())
    yield
    C.set_client(None)


def spec(conv=CONV, **raw):
    return build_spec("ois", {"factory": A.swap, "conventions": conv, **raw}, schemas=SCHEMAS, allow=ALLOW)


def build(sp_, **terms):
    p = A.wrap(sp_)
    return p, TradeTemplate("t", spec(), {"side": "pay", "maturity": "5Y", "notional": 1e7, **terms}).build(p, p.ts)


def reference(sp_, **terms):
    """The reference stack's answer for the SAME snapshot and terms: the independent known answer."""
    p = R.wrap(sp_)
    t = {"maturity": "5Y", "notional": 1e7, **terms}
    return p, R.swap_factory(p, p.ts, terms={**t, "direction": {"pay": 1, "receive": -1}[t.get("side", "pay")]}, conventions=REF_CONV)


def test_the_kit_conforms_with_only_its_default_block():
    s = spec(layers=["carry", "roll", "delta", "convexity"])
    sch = SCHEMAS.get("swap")
    for name in (*sch.required_names()["method"], *sch.required_names()["measure"], *sch.required_layers()):
        assert name in s.bindings or sch.spec_of(name).derived, name
    assert set(s.bindings) == {"value", "dv01", "gamma", "rate", "delta_ladder", "carry", "roll", "delta", "convexity"}, "no extension names: zeta has no other risk variable to offer"


def test_the_default_block_is_method_only_so_the_python_facade_can_load_it():
    for name, b in A.SWAP_BIND.items():
        assert list(b["target"]) == ["method"], name
        assert not (isinstance(b.get("reduce"), str) and ":" in b["reduce"]), "reducers by REGISTERED name, no dotted path (the facade builds specs without an allow-list)"
    build_spec("ois", {"factory": A.swap, "conventions": CONV}, schemas=SCHEMAS)  # no allow= : must not raise


def test_every_unit_and_sign_conversion_is_one_visible_line_of_the_default_block():
    """The conversions live in bindings, not in code: a negative control can remove exactly one."""
    b = A.SWAP_BIND
    assert (b["dv01"]["scale"], b["gamma"]["scale"], b["rate"]["scale"]) == (0.1, 0.01, 0.01)
    assert (b["delta_ladder"]["scale"], b["delta_ladder"]["sign"]) == (0.1, -1.0)
    assert not any("sign" in b[n] for n in ("value", "dv01", "gamma", "rate", "carry", "roll", "delta", "convexity")), "zeta is holder-signed: the ladder is the only exception"


def test_the_factory_returns_concrete_dates_percent_rates_and_the_unsigned_notional():
    sp_ = SnapshotPricer(WORLD(REF, 0.0))
    for side in ("pay", "receive"):
        _, b = build(sp_, side=side, fixed_rate="par")
        _, r = reference(sp_, side=side, fixed_rate="par")
        assert type(b.terms["effective"]) is dt.date and type(b.terms["maturity"]) is dt.date  # not ISO strings, not Timestamps
        assert (b.terms["effective"], b.terms["maturity"]) == (r.terms["effective"], r.terms["maturity"])
        assert b.terms["fixed_rate"] == pytest.approx(r.terms["fixed_rate"], abs=1e-9) and 3.5 < b.terms["fixed_rate"] < 4.5  # PERCENT
        assert b.terms["notional"] == 1e7 and set(b.terms) - {"side", "direction"} == set(r.terms), "the same resolved keys as the reference stack (L0 compares every term_<key>)"


@pytest.mark.parametrize("terms", [{"effective": "spot", "maturity": "10Y"}, {"effective": "1Y", "maturity": "5Y"}, {"effective": "6M", "maturity": "2Y"},
                                   {"effective": dt.date(2024, 6, 20), "maturity": dt.date(2029, 3, 15)}, {"maturity": dt.date(2025, 2, 28)}, {"maturity": "18M"},
                                   {"effective": dt.date(2023, 12, 1), "maturity": "2Y"}, {"effective": "2024-05-27", "maturity": "3Y"}, {"maturity": "30Y"}])
def test_dates_resolve_like_the_reference_on_the_snapshots_holidays(terms):
    sp_ = SnapshotPricer(WORLD(REF, 0.0))
    _, b = build(sp_, fixed_rate=4.0, **terms)
    _, r = reference(sp_, fixed_rate=4.0, **({**terms, "effective": dt.date.fromisoformat(terms["effective"])} if isinstance(terms.get("effective"), str) and terms["effective"][:2] == "20" else terms))
    assert (b.terms["effective"], b.terms["maturity"]) == (r.terms["effective"], r.terms["maturity"])


def test_par_resolves_to_a_percent_rate_that_prices_at_zero_and_a_spread_is_in_basis_points():
    sp_ = SnapshotPricer(WORLD(REF, 0.0))
    p, b = build(sp_, fixed_rate="par")
    c = MarkContext.standalone(p)
    assert b.obj.value(ctx=c).pv == pytest.approx(0.0, abs=1e-6)
    assert b.terms["fixed_rate"] == pytest.approx(b.obj.rate(ctx=c) * 0.01, abs=1e-9), "resolved rate == the bp par rate / 100"
    _, wide = build(sp_, fixed_rate="par", extras={"par_spread_bp": 25.0})
    assert wide.terms["fixed_rate"] - b.terms["fixed_rate"] == pytest.approx(0.25, abs=1e-9)  # 25 bp = 0.25 percent
    _, four = build(sp_, fixed_rate=4)
    assert four.terms["fixed_rate"] == 4.0 and four.obj.rate_bp == pytest.approx(400.0), "fixed_rate: 4 is 4 percent, not 400 and not 0.04"


def test_extras_are_validated_by_the_factory():
    sp_ = SnapshotPricer(WORLD(REF, 0.0))
    with pytest.raises(ConfigError, match=r"CFG-EXTRAS.*not supported"):
        build(sp_, fixed_rate="par", extras={"repo": 1})
    with pytest.raises(ConfigError, match="applies only to fixed_rate 'par'"):
        build(sp_, fixed_rate=4.0, extras={"par_spread_bp": 5})
    with pytest.raises(ConfigError, match="finite number of basis points"):
        build(sp_, fixed_rate="par", extras={"par_spread_bp": float("nan")})


@pytest.mark.parametrize("m", ["value", "rate", "dv01", "gamma"])
def test_units_and_signs_agree_with_the_reference_for_both_directions_and_two_notionals(m):
    sp_ = SnapshotPricer(WORLD(REF, 0.0))
    s = spec()
    for side in ("pay", "receive"):
        for notional in (1e7, 3.3e7):  # two notionals: a constant `scale` standing in for notional/1e6 cannot cancel
            p, b = build(sp_, side=side, notional=notional, fixed_rate=4.2)
            rp, r = reference(sp_, side=side, notional=notional, fixed_rate=4.2)
            c, rc = MarkContext.standalone(p), MarkContext.standalone(rp)
            e = Env(pricer=p, ctx=c, instrument=b.obj, terms=b.terms, state={})
            if m == "value":
                assert evaluate_value(s, e).pv == pytest.approx(r.obj.value(ctx=rc).pv, abs=1e-6)
            elif m == "rate":
                assert evaluate_measure(s, m, e) == pytest.approx(r.obj.rate(ctx=rc), rel=1e-12, abs=1e-12), (m, side, notional)
            else:
                assert evaluate_measure(s, m, e) == pytest.approx(getattr(r.obj, m)(ctx=rc, tenors=TEN), rel=1e-6, abs=1e-6), (m, side, notional)
            if m == "dv01":
                assert evaluate_measure(s, "dv01", e) * (1 if side == "pay" else -1) > 0, "payer-positive dollar delta"


def _aged(days, **terms):
    """The swap booked at REF, valued on a later snapshot: (zeta pricer, zeta swap, reference pricer, reference swap)."""
    p0, b0 = build(SnapshotPricer(WORLD(REF, 0.0)), **terms)
    _, r0 = reference(SnapshotPricer(WORLD(REF, 0.0)), **terms)
    d = REF + dt.timedelta(days=days)
    while d.weekday() >= 5 or d in HOL:
        d += dt.timedelta(days=1)
    sp_ = SnapshotPricer(WORLD(d, 0.0))
    return A.wrap(sp_), b0.obj, R.wrap(sp_), r0.obj


@pytest.mark.parametrize("fixed", [4.2, 3.0, "par"])
def test_dv01_has_one_definition_in_every_state_and_equals_the_references(fixed):
    """Unstarted: the analytic annuity (off par it differs from the ladder sum by percents: the reference answers the annuity). Started: the ladder sum, exactly. Matured: 0."""
    for side in ("pay", "receive"):
        for days in (0, 1, 3, 30, 120, 300, 420, 500):  # a 1Y swap: unstarted, started, then matured (the test world keeps 900 days of fixings)
            p, sw, rp, rsw = _aged(days, side=side, maturity="1Y", fixed_rate=fixed)
            c, rc = MarkContext.standalone(p), MarkContext.standalone(rp)
            got, want = sw.dv01(ctx=c) * 0.1, rsw.dv01(ctx=rc, tenors=TEN)
            assert got == pytest.approx(want, rel=1e-9, abs=1e-9), (side, days, fixed)
            if sw.started(p) and got != 0.0:
                assert got == pytest.approx(-sum(x["risk"] for x in sw.delta_ladder(ctx=c)) * 0.1, rel=1e-12), "started: dv01 IS the ladder sum"
    p, sw, *_ = _aged(500, side="pay", maturity="1Y", fixed_rate=4.0)
    assert sw.dv01(ctx=MarkContext.standalone(p)) == 0.0 and math.isnan(sw.rate(ctx=MarkContext.standalone(p)))


def test_the_annuity_definition_matters_off_par_for_an_unstarted_swap():
    """Known answer: off par the ladder sum and the annuity differ by percents, RISK_10BP is a third number, and zeta's strike bump gives the reference's annuity to round-off."""
    p, sw, rp, rsw = _aged(0, side="pay", maturity="5Y", fixed_rate=3.0)
    m = sw._res(p)["measures"]
    ladder_sum = -sum(x["risk"] for x in m["LADDER_10BP"])
    annuity = 10.0 * sw._annuity(p)
    assert abs(ladder_sum / annuity - 1) > 0.02 and abs(m["RISK_10BP"] / annuity - 1) > 0.02
    assert annuity / 10.0 == pytest.approx(rsw.annuity_dv01(rp.need_curve(), rp.reference_date), rel=1e-12)


def test_every_default_binding_runs_and_returns_its_declared_type():
    sp_, prev = SnapshotPricer(WORLD(REF, 0.0)), SnapshotPricer(WORLD(dt.date(2024, 6, 11), 0.0))
    p, b = build(sp_, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
    c = MarkContext.standalone(p, prev=A.wrap(prev))
    e = Env(pricer=p, ctx=c, instrument=b.obj, terms=b.terms, state={})
    out = {n: call_binding(bd, e) for n, bd in spec().bindings.items()}
    assert isinstance(out["value"], Valuation) and math.isfinite(out["value"].pv)
    assert all(isinstance(out[k], float) and math.isfinite(out[k]) for k in ("dv01", "gamma", "rate", "carry", "roll", "delta", "convexity"))
    assert list(out["delta_ladder"]) == list(spec().bindings["delta_ladder"].keys) == TEN
    assert sum(out["delta_ladder"].values()) == pytest.approx(out["dv01"], rel=1e-12), "a started swap: the ladder sums to dv01 exactly"


def test_payer_and_receiver_are_exact_mirrors_on_value_measures_ladder_and_every_layer():
    sp_, prev = SnapshotPricer(WORLD(REF, 0.0)), SnapshotPricer(WORLD(dt.date(2024, 6, 11), 0.0))
    res = {}
    for side in ("pay", "receive"):
        p, b = build(sp_, side=side, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
        e = Env(pricer=p, ctx=MarkContext.standalone(p, prev=A.wrap(prev)), instrument=b.obj, terms=b.terms, state={})
        res[side] = {n: call_binding(bd, e) for n, bd in spec().bindings.items()}
    for n in ("dv01", "gamma", "carry", "roll", "delta", "convexity"):
        assert res["pay"][n] == pytest.approx(-res["receive"][n], rel=1e-9, abs=1e-9), n
    assert res["pay"]["rate"] == res["receive"]["rate"]
    assert res["pay"]["value"].pv == pytest.approx(-res["receive"]["value"].pv, abs=1e-9) and res["pay"]["value"].cash == pytest.approx(-res["receive"]["value"].cash, abs=1e-9)
    for k in TEN:
        assert res["pay"]["delta_ladder"][k] == pytest.approx(-res["receive"]["delta_ladder"][k], rel=1e-9, abs=1e-9), k


def test_a_pricer_that_was_not_wrapped_is_refused_with_the_fix():
    raw = SnapshotPricer(WORLD(REF, 0.0))
    with pytest.raises(ConfigError, match="set `wrap` on the pricer role"):
        TradeTemplate("t", spec(), {"side": "pay", "maturity": "5Y", "notional": 1e7}).build(raw, raw.ts)


# ---------------------------------------------------------------------------------- the ladder reducer
def test_the_ladder_reducer_maps_zetas_twelve_pillars_to_upper_case_tenors_and_folds_nothing():
    rows = [{"pillar_months": m, "risk": float(i + 1)} for i, m in enumerate((1, 3, 6, 12, 24, 36, 60, 84, 120, 180, 240, 360))]
    out = A.ladder_to_tenor_dict(rows)
    assert list(out) == TEN == ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"] and list(out.values()) == [float(i) for i in range(1, 13)]


def test_a_pillar_zeta_adds_is_dropped_only_when_it_carries_no_risk():
    rows = [{"pillar_months": m, "risk": 0.0} for m in (1, 3, 6, 12, 24, 36, 60, 84, 120, 180, 240, 360)]
    assert A.ladder_to_tenor_dict(rows + [{"pillar_months": 480, "risk": 0.0}]) == {t: 0.0 for t in TEN}
    with pytest.raises(MethodCallError, match="cannot be dropped silently"):
        A.ladder_to_tenor_dict(rows + [{"pillar_months": 480, "risk": 12.5}])


# ---------------------------------------------------------------------------------- conventions
def test_the_shipped_block_is_complete_and_accepted_and_is_not_mutated():
    before = dict(A.USD_SOFR_OIS_CONVENTIONS)
    assert CV.swap_conventions(A.USD_SOFR_OIS_CONVENTIONS).calendar == "usd_fed" and dict(A.USD_SOFR_OIS_CONVENTIONS) == before


def test_every_key_of_the_vocabulary_has_a_stated_decision_and_every_refused_token_names_the_reason_and_the_supported_set():
    assert set(CV._SUPPORTED) == set(SCHEMAS.get("swap").conventions)
    for key, tokens in SCHEMAS.get("swap").conventions.items():
        for tok in (tokens.values or ()):
            if CV._SUPPORTED[key] != "*" and tok not in CV._SUPPORTED[key]:
                with pytest.raises(ConfigError, match=rf"CFG-CONVENTION-UNSUPPORTED.*{key}={tok!r}.*does not support it: .+; supported: \["):
                    CV.swap_conventions({**A.USD_SOFR_OIS_CONVENTIONS, key: tok})


@pytest.mark.parametrize("key,value", [("day_count", "act365f"), ("frequency", "semiannual"), ("spot_lag_days", 1), ("payment_lag_days", 0), ("end_of_month", True),
                                       ("business_day_convention", "following"), ("compounding", "daily_average"), ("stub", "long_front"), ("fixing_lag_days", 2), ("time_accrual", "linear")])
def test_a_convention_zeta_cannot_express_is_refused_never_approximated(key, value):
    with pytest.raises(ConfigError, match=r"CFG-CONVENTION-UNSUPPORTED"):
        CV.swap_conventions({**A.USD_SOFR_OIS_CONVENTIONS, key: value})


def test_an_unknown_key_and_a_missing_key_are_errors_there_are_no_implicit_defaults():
    with pytest.raises(ConfigError, match="CFG-CONVENTION"):
        CV.swap_conventions({**A.USD_SOFR_OIS_CONVENTIONS, "daycount": "act360"})
    with pytest.raises(ConfigError, match="CFG-CONVENTION"):
        CV.swap_conventions({k: v for k, v in A.USD_SOFR_OIS_CONVENTIONS.items() if k != "stub"})


# ---------------------------------------------------------------------------------- plain data
def test_the_swap_is_plain_data_it_deep_copies_and_pickles():
    _, b = build(SnapshotPricer(WORLD(REF, 0.0)), fixed_rate=4.1, effective="1Y")
    for clone in (copy.deepcopy(b.obj), pickle.loads(pickle.dumps(b.obj))):
        assert clone.key == b.obj.key and clone.trade() == b.obj.trade()
    assert b.obj.trade()["start"] == b.terms["effective"].isoformat() and b.obj.trade()["end"] == {"tenor_years": 5}, "re-priced with the booked START DATE and the ORIGINAL tenor (ZETA_DOCS section 4)"
    assert b.obj.trade()["notional_mm"] == 10.0 and b.obj.trade()["leg_fixed"] == "PAY"
