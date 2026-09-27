"""The worked example of wiring an EXTERNAL pricing library into pricebt: `skills/pricebt-wire-external-library/example/` (acmelib + acme_adapter).

Needs no pricing library (acmelib is fictional and reuses the reference stack's arithmetic), so every test is `core`. What is proven, in order:

A  acmelib really has the foreign API the adapter must convert (receiver's sign, decimals, per-million risk, a pandas ladder with an extra bucket, a global valuation date)
B  the layer conformance kit passes on the adapter, and the kit itself catches a missing sign
C  the Kit conforms to the swap schema with ONLY its default block, every default binding returns its declared type, the extension measure works, the allow-list rule
D  conventions: the shared vocabulary maps to acmelib's codes, an unsupported value is a ConfigError naming the supported set, supported variants agree with the reference stack
E  wrap: memoised, discount factors -> zero rates round trip, the snapshot's holidays are what acmelib sees (two different calendars alive at once), the process-global valuation
   date is restored on every path, acmelib's exceptions become pricebt's, plain-data objects deep-copy and pickle
F  the tie-out of the reference stack against the acme stack passes with the SHIPPED tolerances, and four NEGATIVE CONTROLS fail it: each is first shown NOT to fire on the correct
   wiring, then to fire at the expected level and quantity with a located difference
G  the stack overlay obeys the overlay rules, and the CLI runs the same tie-out (exit 0 passed, 1 failed)
H  the files of the example name no vendor library (a real adapter's guard, with its own non-vacuity twin)
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib
import math
import pickle
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "skills" / "pricebt-wire-external-library" / "example"
for _p in (str(EXAMPLE),):  # the example is NOT on pytest.ini's pythonpath: add it from this file's location, never from the working directory
    if _p not in sys.path:
        sys.path.insert(0, _p)

import acme_adapter as A  # noqa: E402
import acmelib as acme  # noqa: E402
from acme_adapter import _compat as C  # noqa: E402
from guards import scan  # noqa: E402

from pricebt.config import cli  # noqa: E402
from pricebt.config.stacks import apply_stack, validate_overlay  # noqa: E402
from pricebt.config import yamlio  # noqa: E402
from pricebt.contracts.binding import REDUCERS, Env, call_binding  # noqa: E402
from pricebt.contracts.evaluate import evaluate_layer, evaluate_measure, evaluate_value  # noqa: E402
from pricebt.contracts.schema import SchemaRegistry  # noqa: E402
from pricebt.contracts.spec import TradeTemplate, build_spec  # noqa: E402
from pricebt.errors import ConfigError, MarketDataUnavailable, MeasureError, MethodCallError, RegistryError  # noqa: E402
from pricebt.pricable import MarkContext, Valuation  # noqa: E402
from pricebt.registry import resolve_dotted  # noqa: E402
from pricebt.snapshot import CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer  # noqa: E402
from pricebt.testing import refstack as R  # noqa: E402
from pricebt.testing.layer_conformance import Setup, flat_swap_world, run_kit  # noqa: E402
from pricebt.tieout import run_stack, run_tieout  # noqa: E402

pytestmark = pytest.mark.core

CONFIG = EXAMPLE / "config"
BASE = CONFIG / "acme_tieout_base.yaml"
REF_OVERLAY = PROJECT / "configs" / "adapters" / "refstack_swap.yaml"
ACME_OVERLAY = CONFIG / "acme_swap.yaml"
MISTAKES = CONFIG / "mistakes"
ALLOW = ("pricebt", "acme_adapter")  # what a base config's `registry.allow` gives the loader (`pricebt` is always there)
SCHEMAS = SchemaRegistry.default()
HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
CONV = {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
REF_CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
WORLD = flat_swap_world("cal", holidays=HOL)
REF = dt.date(2024, 6, 12)
TENORS = list(A.DEFAULT_TENORS)


@pytest.fixture(autouse=True)
def clean_valuation_date():
    """acmelib's valuation date is one process-global: every test starts and ends with it unset."""
    acme.set_valuation_date(None)
    yield
    acme.set_valuation_date(None)


# ------------------------------------------------------------------------------ helpers
def snap(ref=REF, shock=0.0):
    return SnapshotPricer(WORLD(ref, shock))


def pricer(ref=REF, shock=0.0):
    return A.wrap(snap(ref, shock))


def make_spec(bind=None, conv=CONV, allow=ALLOW):
    raw = {"factory": A.swap, "conventions": conv, **({"bind": bind} if bind else {})}
    return build_spec("ois", raw, schemas=SCHEMAS, allow=allow)


SPEC = make_spec()


def build(p, spec=None, **terms):
    tpl = TradeTemplate("t", spec or SPEC, {"side": "pay", "maturity": "5Y", "notional": 1e7, **terms})
    return tpl.build(p, p.ts)


def ctx(p, prev=None):
    return MarkContext.standalone(p, prev=prev)


def env(built, c, spec=None):
    return Env(pricer=c.pricer, ctx=c, instrument=built.obj, terms=built.terms, state={})


def ref_side(sp, conv=REF_CONV, **terms):
    """The reference stack's object for the SAME snapshot pricer and terms (the independent answer)."""
    rp = R.wrap(sp)
    side = terms.get("side", "pay")
    t = {"maturity": "5Y", "notional": 1e7, **terms, "direction": {"pay": 1, "receive": -1}[side]}
    return rp, R.swap_factory(rp, rp.ts, terms=t, conventions=conv)


def spot_of(ref, hol, lag=2):
    """Plain python: `lag` business days after `ref` (weekends and `hol` are not business days)."""
    d, n = ref, 0
    while n < lag:
        d += dt.timedelta(days=1)
        n += d.weekday() < 5 and d not in hol
    return d


def market_of(sp):
    return A.wrap(sp).need_market()


# ================================================================================ A. acmelib's foreign API
def test_acmelib_reports_the_receivers_numbers_in_decimals_per_million_with_a_pandas_ladder():
    sp = snap()
    p = A.wrap(sp)
    m, cal = p.need_market(), p.calendar("cal")
    acme.set_valuation_date("2024-06-12")
    eff = acme.add_business_days("2024-06-12", 2, cal)
    assert eff == "2024-06-14" and isinstance(eff, str), "ISO strings in and out"
    par = acme.par_rate(acme.Swap(eff, "5y", 1e7, 0.0, calendar=cal), m)
    assert 0.039 < par < 0.043, "a DECIMAL (0.04...), where pricebt's `rate` is percent"
    at_par = acme.Swap(eff, "5y", 1e7, par, calendar=cal)
    assert abs(acme.pv(at_par, m)) < 1e-6, "known answer: a swap struck at its own par is worth zero"
    off_par = acme.Swap(eff, "5y", 1e7, 0.05, calendar=cal)
    assert acme.pv(off_par, m) > 0 and acme.pv01(off_par, m) < 0, "the RECEIVER of a fixed rate above par gains, and loses when rates rise (pricebt: payer-positive)"
    small, large = acme.Swap(eff, "5y", 1e6, 0.05, calendar=cal), acme.Swap(eff, "5y", 5e7, 0.05, calendar=cal)
    assert acme.pv(large, m) == pytest.approx(50 * acme.pv(small, m), rel=1e-12)
    assert acme.pv01(large, m) == acme.pv01(small, m), "risk is per ONE MILLION whatever the notional is"
    lad = acme.bucket_risk(at_par, m, ["3m", "6m", "1y", "2y", "5y", "10y"])
    assert isinstance(lad, pd.Series) and list(lad.index) == ["on", "3m", "6m", "1y", "2y", "5y", "10y"] and lad["on"] == 0.0, "lower-case labels and a bucket pricebt does not have"
    assert lad.sum() == pytest.approx(acme.pv01(at_par, m), rel=2e-3), "at par the ladder sums to the annuity dv01 (off par they differ by the (S - K) dA/dS term), per million"
    assert lad["5y"] == lad.min() and lad.max() <= 0.0, "a 5y swap's risk sits in its own bucket and has the receiver's (negative) sign"
    with pytest.raises(acme.BadInput, match="lower-case"):
        acme.bucket_risk(large, m, ["5Y"])
    with pytest.raises(acme.BadInput, match="ISO"):
        acme.add_business_days("12/06/2024", 2, cal)


def test_acmelib_needs_its_global_valuation_date_and_a_curve_anchored_on_it():
    p = pricer()
    m = p.need_market()
    sw = acme.Swap("2024-06-14", "5y", 1e7, 0.04, calendar=p.calendar("cal"))
    with pytest.raises(acme.NoValuationDate):
        acme.pv(sw, m)
    acme.set_valuation_date("2024-06-13")
    with pytest.raises(acme.CurveError, match="anchored at 2024-06-12"):
        acme.pv(sw, m)
    acme.set_valuation_date("2024-06-12")
    assert acme.pv(sw, m) != 0.0


def test_acmelib_calendars_are_a_global_registry_that_refuses_a_conflicting_registration():
    acme.register_calendar("TEST-CAL", ["2024-05-27"])
    acme.register_calendar("TEST-CAL", ["2024-05-27"])  # the same content twice: a no-op
    with pytest.raises(acme.CalendarError, match="already registered"):
        acme.register_calendar("TEST-CAL", [])
    with pytest.raises(acme.CalendarError, match="no calendar"):
        acme.add_business_days("2024-05-24", 2, "NOT-REGISTERED")
    assert acme.add_business_days("2024-05-24", 2, "TEST-CAL") == "2024-05-29"


def test_the_ladder_reducer_upper_cases_drops_an_empty_extra_bucket_and_refuses_a_loaded_one():
    assert "acme_ladder_to_tenor_dict" in REDUCERS and REDUCERS["acme_ladder_to_tenor_dict"] is A.ladder_to_tenor_dict
    got = A.ladder_to_tenor_dict(pd.Series([0.0, 1.0, 2.5], index=["on", "3m", "10y"]))
    assert got == {"3M": 1.0, "10Y": 2.5} and list(got) == ["3M", "10Y"]
    with pytest.raises(MethodCallError, match="cannot be dropped silently"):
        A.ladder_to_tenor_dict(pd.Series([0.3, 1.0], index=["on", "3m"]))
    with pytest.raises(MethodCallError, match="not tenors"):
        A.ladder_to_tenor_dict(pd.Series([0.0, 1.0], index=["on", "front"]))


# ================================================================================ B. the layer conformance kit
def kit_setup(**kw):
    args = dict(spec=SPEC, wrap=A.wrap, terms={"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2},
                mirror_terms={"side": "receive", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2}, world=WORLD, t0=dt.date(2024, 3, 4), t1=dt.date(2024, 3, 5))
    args.update(kw)
    return Setup(**args)


def test_the_layer_conformance_kit_passes_on_the_adapter():
    rep = run_kit(kit_setup())
    rep.assert_ok()
    assert {r.check for r in rep.rows} >= {"fd_vs_delta_convexity", "mirror", "unexplained_share"} and len(rep.rows) >= 20, "the kit ran every check"


def test_the_seasoned_swap_layers_conform_across_a_payment_date():
    setup = kit_setup(terms={"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}, mirror_terms={"side": "receive", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0},
                      birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 21), payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23)))
    rep = run_kit(setup)
    rep.assert_ok()
    assert any(r.check == "cash_sweep" and r.quantity.startswith("value drop") for r in rep.rows)


def test_the_kit_itself_catches_a_layer_bound_without_the_receiver_to_payer_sign():
    """Known answer first: the shipped block passes (above). Then one layer forgets `sign: -1`: full revaluation no longer equals delta + convexity."""
    bad = make_spec(bind={"delta": {**A.SWAP_BIND["delta"], "sign": 1.0}})
    rep = run_kit(kit_setup(spec=bad))
    assert not rep.ok and {r.check for r in rep.failures()} >= {"fd_vs_delta_convexity"}
    with pytest.raises(AssertionError, match="layer conformance failed"):
        rep.assert_ok()


# ================================================================================ C. the Kit and its bindings
def test_conforms_to_the_swap_schema_with_only_the_kits_default_block():
    spec = build_spec("s", {"factory": A.swap, "conventions": CONV, "layers": ["carry", "roll", "delta", "convexity"]}, schemas=SCHEMAS, allow=ALLOW)
    required = SCHEMAS.get("swap").required_names()
    for name in (*required["method"], *required["measure"], *SCHEMAS.get("swap").required_layers()):
        assert name in spec.bindings or SCHEMAS.get("swap").spec_of(name).derived, name
    assert spec.asset_class == "swap" and spec.layers == ("carry", "roll", "delta", "convexity") and "acme_zero_dv01" in spec.bindings
    assert A.swap.extra == {"acme_zero_dv01": "measure"} and A.swap.cls is A.AcmeSwap and A.swap.default_bind is A.SWAP_BIND


def test_every_default_binding_runs_and_returns_its_declared_type():
    p, prev = pricer(), pricer(dt.date(2024, 6, 11))
    built = build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
    c = ctx(p, prev)
    e = env(built, c)
    out = {name: call_binding(b, e) for name, b in SPEC.bindings.items()}
    assert isinstance(out["value"], Valuation) and math.isfinite(out["value"].pv)
    for k in ("dv01", "gamma", "rate", "carry", "roll", "delta", "convexity", "acme_zero_dv01"):
        assert isinstance(out[k], float) and math.isfinite(out[k]), k
    assert isinstance(out["delta_ladder"], dict) and list(out["delta_ladder"]) == list(A.DEFAULT_TENORS) == list(SPEC.bindings["delta_ladder"].keys)
    assert all(isinstance(v, float) for v in out["delta_ladder"].values())
    # and through pricebt's own evaluators, which enforce the schema (a ladder is checked against its declared keys, a measure must be finite)
    assert evaluate_measure(SPEC, "delta_ladder", e) == out["delta_ladder"] and isinstance(evaluate_value(SPEC, e), Valuation)
    assert evaluate_layer(SPEC, "carry", e) == out["carry"]


def test_units_and_signs_agree_with_the_reference_stack_on_the_same_snapshot():
    """The conversions in one place: `rate` percent (scale 100), risk positive for the payer (sign -1), per-million risk scaled by the notional, the ladder a dict of upper-case tenors."""
    sp = snap()
    p = A.wrap(sp)
    for side in ("pay", "receive"):
        for notional in (1e7, 3.3e7):
            built = build(p, side=side, notional=notional, fixed_rate=4.2)
            rp, rb = ref_side(sp, side=side, notional=notional, fixed_rate=4.2)
            c, rc = ctx(p), ctx(rp)
            e = env(built, c)
            assert built.terms["effective"] == rb.terms["effective"] and built.terms["maturity"] == rb.terms["maturity"] and built.terms["fixed_rate"] == 4.2
            assert evaluate_value(SPEC, e).pv == pytest.approx(rb.obj.value(ctx=rc).pv, abs=1e-6)
            assert evaluate_measure(SPEC, "rate", e) == pytest.approx(rb.obj.rate(ctx=rc), abs=1e-12) and 3.9 < evaluate_measure(SPEC, "rate", e) < 4.3
            dv01 = evaluate_measure(SPEC, "dv01", e)
            assert dv01 == pytest.approx(rb.obj.dv01(ctx=rc), rel=1e-11) and (dv01 > 0) == (side == "pay") and 4000 * notional / 1e7 < abs(dv01) < 5000 * notional / 1e7
            assert evaluate_measure(SPEC, "gamma", e) == pytest.approx(rb.obj.gamma(ctx=rc), rel=1e-9)
            lad, rlad = evaluate_measure(SPEC, "delta_ladder", e), rb.obj.delta_ladder(ctx=rc)
            assert list(lad) == list(rlad) and all(lad[k] == pytest.approx(rlad[k], abs=1e-8) for k in lad)
            assert evaluate_measure(SPEC, "acme_zero_dv01", e) == pytest.approx(rb.obj.dv01_zero(ctx=rc), rel=1e-9), "the extension measure against the reference's zero-rate dv01"


def test_layers_on_a_seasoned_swap_agree_with_the_reference_stack_and_the_market_move_is_visible():
    sp0, sp1 = snap(dt.date(2024, 6, 11)), snap(dt.date(2024, 6, 12), 5.0)
    p0, p1 = A.wrap(sp0), A.wrap(sp1)
    for side in ("pay", "receive"):
        terms = dict(side=side, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2, notional=5e7)
        built = build(p0, **terms)
        rp0, rb = ref_side(sp0, **terms)
        rp1 = R.wrap(sp1)
        c, rc = ctx(p1, p0), ctx(rp1, rp0)
        e = env(built, c)
        for layer in ("carry", "roll", "delta", "convexity"):
            assert evaluate_layer(SPEC, layer, e) == pytest.approx(getattr(rb.obj, layer)(ctx=rc), rel=1e-8, abs=1e-6), (side, layer)
        assert abs(evaluate_layer(SPEC, "delta", e)) > 100, "a +5bp market move on 50mm is not zero"


def test_the_extension_name_cannot_shadow_a_schema_name():
    """`Kit.extra` names must not collide with a schema or reserved name (spec.py): declaring `dv01` as an extension is refused."""
    bad = A.swap.__class__(factory=A.swap.factory, asset_class="swap", default_bind=A.SWAP_BIND, cls=A.AcmeSwap, extra={"dv01": "measure"})
    with pytest.raises(ConfigError, match="collides"):
        build_spec("x", {"factory": bad, "conventions": CONV}, schemas=SCHEMAS, allow=ALLOW)


def test_a_user_binding_replaces_one_default_by_name_and_the_rest_stay():
    """The known answer first (percent), then the same binding WITHOUT `scale` gives the library's decimal: 0.04 where the schema says percent."""
    p = pricer()
    built = build(p)
    good = evaluate_measure(SPEC, "rate", env(built, ctx(p)))
    unscaled = make_spec(bind={"rate": {"target": {"method": "rate"}, "kwargs": {"ctx": "@ctx"}}})
    assert set(unscaled.bindings) == set(SPEC.bindings) and unscaled.bindings["dv01"] == SPEC.bindings["dv01"]
    assert evaluate_measure(unscaled, "rate", env(built, ctx(p))) == pytest.approx(good / 100.0, rel=1e-12)


def test_a_ladder_that_is_not_what_the_binding_declares_is_refused_by_pricebt_not_guessed():
    p = pricer()
    built = build(p)
    lowercase = make_spec(bind={"delta_ladder": {**A.SWAP_BIND["delta_ladder"], "reduce": "acme_mistakes:lowercase_last_key"}}, allow=(*ALLOW, "acme_mistakes"))
    with pytest.raises(MeasureError, match="not a canonical tenor"):
        evaluate_measure(lowercase, "delta_ladder", env(built, ctx(p)))


def test_dv01_is_the_annuity_before_the_start_and_the_ladder_sum_after_it():
    p = pricer()
    fresh, aged = build(p, maturity="10Y"), build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2034, 1, 8), fixed_rate=3.9)
    for built, exact in ((fresh, False), (aged, True)):
        e = env(built, ctx(p))
        lad = evaluate_measure(SPEC, "delta_ladder", e)
        dv01 = evaluate_measure(SPEC, "dv01", e)
        assert sum(lad.values()) == pytest.approx(dv01, rel=1e-12 if exact else 2e-3)
    assert not fresh.obj.started(p) and aged.obj.started(p)


def test_a_function_target_in_the_default_block_needs_the_package_on_the_allow_list():
    """The kit's layers are `function` targets (dotted paths): without `registry.allow` naming `acme_adapter`, building the spec fails at load, naming the prefix."""
    with pytest.raises(ConfigError, match="allowed prefix") as ei:
        make_spec(allow=("pricebt",))
    assert ei.value.code == "CFG-ALLOW" and "acme_adapter.swap" in str(ei.value)
    assert resolve_dotted("acme_adapter:swap", allow=ALLOW) is A.swap and resolve_dotted("acme_adapter:wrap", allow=ALLOW) is A.wrap
    with pytest.raises(RegistryError, match="allowed prefix"):
        resolve_dotted("acme_adapter:wrap")


def test_the_submodule_swap_is_shadowed_by_the_kit_of_the_same_name_but_dotted_paths_still_work():
    """`acme_adapter.swap` is the Kit as an attribute and the module in sys.modules: `from acme_adapter import swap` gives the Kit, `import_module` the module."""
    assert A.swap.__class__.__name__ == "Kit" and importlib.import_module("acme_adapter.swap").__name__ == "acme_adapter.swap"
    assert resolve_dotted("acme_adapter.swap:carry", allow=ALLOW) is importlib.import_module("acme_adapter.swap").carry
    assert resolve_dotted("acme_adapter.wrap:wrap", allow=ALLOW) is A.wrap


# ================================================================================ D. conventions
def test_the_shipped_block_is_complete_and_accepted_and_is_not_mutated():
    before = copy.deepcopy(dict(A.USD_SOFR_OIS_CONVENTIONS))
    s = A.swap_conventions(A.USD_SOFR_OIS_CONVENTIONS)
    assert (s.basis, s.freq, s.bdc, s.spot_lag, s.pay_lag) == ("A360", "1y", "MF", 2, 2), "pricebt's act360 / annual / modified_following are acmelib's A360 / 1y / MF"
    assert dict(A.USD_SOFR_OIS_CONVENTIONS) == before and set(A.SWAP_KEYS) == set(A.USD_SOFR_OIS_CONVENTIONS)


@pytest.mark.parametrize("conv, match", [
    ({"day_count": "thirty360"}, r"does not support it: acmelib has only ACT/360 and ACT/365 bases; supported: \['act360', 'act365f'\]"),
    ({"compounding": "daily_average"}, r"acmelib's overnight leg only compounds; supported: \['daily_compounded'\]"),
    ({"end_of_month": True}, r"no end-of-month roll; supported: \[False\]"),
    ({"business_day_convention": "modified_preceding"}, r"supported: \['unadjusted', 'following', 'modified_following', 'preceding'\]"),
    ({"time_accrual": "linear"}, r"supported: \['lump'\]"),
])
def test_a_vocabulary_token_acmelib_cannot_express_is_a_config_error_naming_the_reason_and_the_supported_set(conv, match):
    with pytest.raises(ConfigError, match=match) as ei:
        A.swap_conventions({**A.USD_SOFR_OIS_CONVENTIONS, **conv})
    assert ei.value.code == "CFG-CONVENTION-UNSUPPORTED"


def test_an_unknown_key_a_missing_key_a_wrong_type_and_an_unknown_token_are_errors_never_defaults():
    with pytest.raises(ConfigError, match="unknown convention keys"):
        A.swap_conventions({**A.USD_SOFR_OIS_CONVENTIONS, "daycount": "act360"})
    with pytest.raises(ConfigError, match="missing convention keys"):
        A.swap_conventions({k: v for k, v in A.USD_SOFR_OIS_CONVENTIONS.items() if k != "frequency"})
    with pytest.raises(ConfigError, match="must be a non-negative integer"):
        A.swap_conventions({**A.USD_SOFR_OIS_CONVENTIONS, "spot_lag_days": "2"})
    with pytest.raises(ConfigError, match="one of"):
        A.swap_conventions({**A.USD_SOFR_OIS_CONVENTIONS, "day_count": "act/360"})


D0 = dt.date(2024, 3, 22)  # a Friday before the 2024-03-29 holiday
VARIANTS = [({"day_count": "act365f"}, D0), ({"frequency": "semiannual"}, D0), ({"frequency": "quarterly"}, D0), ({"payment_lag_days": 0}, D0), ({"payment_lag_days": 1}, D0),
            ({"spot_lag_days": 0}, D0), ({"spot_lag_days": 1}, D0),
            ({"business_day_convention": "following"}, dt.date(2024, 7, 29)),  # spot 2024-07-31: the 3Y end 2027-07-31 is a Saturday: following 08-02, modified following 07-30
            ({"business_day_convention": "preceding"}, dt.date(2024, 4, 29))]  # spot 2024-05-01: the 3Y end 2027-05-01 is a Saturday: preceding 04-30, modified following 05-03


@pytest.mark.parametrize("variant, ref", VARIANTS, ids=[next(iter(v)) + "=" + str(next(iter(v.values()))) for v, _ in VARIANTS])
def test_every_supported_non_default_convention_reaches_acmelib_and_agrees_with_the_reference_stack(variant, ref):
    sp = snap(ref)
    p = A.wrap(sp)
    default = build(p, maturity="3Y", fixed_rate=4.0)
    built = build(p, make_spec(conv={**CONV, **variant}), maturity="3Y", fixed_rate=4.0)
    rp, rb = ref_side(sp, conv={**REF_CONV, **variant}, maturity="3Y", fixed_rate=4.0)
    assert built.terms["effective"] == rb.terms["effective"] and built.terms["maturity"] == rb.terms["maturity"]
    pv = built.obj.value(ctx=ctx(p)).pv
    assert pv == pytest.approx(rb.obj.value(ctx=ctx(rp)).pv, abs=1e-6)
    changed = (built.terms["effective"], built.terms["maturity"], pv) != (default.terms["effective"], default.terms["maturity"], default.obj.value(ctx=ctx(p)).pv)
    assert changed, "the variant must change something, or agreeing with the reference proves nothing"


# ================================================================================ E. wrap
def test_wrap_is_memoised_per_snapshot_pricer_and_idempotent():
    sp = snap()
    a = A.wrap(sp)
    assert A.wrap(sp) is a and A.wrap(a) is a and A.wrap(snap()) is not a
    with pytest.raises(ConfigError, match="needs a SnapshotPricer"):
        A.AcmePricer(object())
    assert a.describe()["digest"] == sp.digest and a.digest == sp.digest and a.ts == sp.ts


def test_discount_factors_become_zero_rates_and_come_back_to_round_off_between_and_beyond_the_nodes():
    sp = snap()
    c = sp.snapshot.curves["curve"]
    zc = A.wrap(sp).need_market().curve
    assert zc.anchor == "2024-06-12" and zc.pillars[0] == c.node_dates[1].isoformat() and 0.039 < zc.zeros[0] < 0.041, "zero rates in decimals, ACT/365"
    for d, v in zip(c.node_dates, c.values):
        assert zc.df(d.isoformat()) == pytest.approx(v, abs=1e-14)
    rc = R.RefCurve(c.reference_date, c.node_dates, c.values)  # the snapshot's tag `log_linear_df`: between and beyond the nodes
    for days in (3, 45, 400, 4000, 12000, 20000):
        d = c.reference_date + dt.timedelta(days=days)
        assert zc.df(d.isoformat()) == pytest.approx(rc.df(d), abs=1e-13), days


def test_only_the_log_linear_discount_factor_tag_is_honoured():
    sp = snap()
    curve = sp.snapshot.curves["curve"]
    object.__setattr__(curve, "interpolation", "cubic_spline")  # a frozen dataclass: the snapshot itself refuses to be built with another tag
    with pytest.raises(ConfigError, match="honours only 'log_linear_df'"):
        A.AcmePricer(sp)
    object.__setattr__(curve, "interpolation", "log_linear_df")


def test_several_curves_need_a_name_and_an_unknown_name_lists_what_there_is():
    s = snap().snapshot
    c = s.curves["curve"]
    two = SnapshotPricer(MarketSnapshot(s.ts, s.reference_date, {"curve": c, "other": CurveSnapshot("other", c.reference_date, c.node_dates, c.values)}, s.fixings, None, s.calendars))
    with pytest.raises(ConfigError, match="several curves"):
        A.AcmePricer(two)
    assert A.AcmePricer(two, curve="other").curve_name == "other"
    with pytest.raises(ConfigError, match=r"no curve 'nope'; it has \['curve', 'other'\]"):
        A.AcmePricer(two, curve="nope")


def test_the_snapshots_holidays_are_what_acmelib_sees_and_the_calendar_name_is_content_addressed():
    ref = dt.date(2024, 5, 24)  # a Friday; Monday 2024-05-27 is a holiday in HOL
    p = pricer(ref)
    name = p.calendar("cal")
    assert name.startswith("PBT.cal.") and not acme.is_business_day("2024-05-27", name) and acme.is_business_day("2024-05-28", name)
    assert build(p).terms["effective"] == spot_of(ref, HOL) == dt.date(2024, 5, 29), "spot skips the snapshot's holiday: plain python agrees"
    # a snapshot of the SAME name without that holiday is a different calendar under a different acmelib name; nothing is replaced
    p_free = A.wrap(SnapshotPricer(flat_swap_world("cal", holidays=())(ref, 0.0)))
    assert p_free.calendar("cal") != name and build(p_free, make_spec()).terms["effective"] == dt.date(2024, 5, 28)
    assert p.calendar("cal") == name and not acme.is_business_day("2024-05-27", name), "the first calendar is untouched"
    with pytest.raises(ConfigError, match=r"the snapshot has no calendar 'nyse'; it has \['cal'\]"):
        p.calendar("nyse")


def test_two_wrapped_snapshots_with_different_holidays_give_order_independent_numbers():
    ref = dt.date(2024, 5, 24)
    pa = A.wrap(SnapshotPricer(flat_swap_world("cal", holidays=HOL)(ref, 0.0)))
    pb = A.wrap(SnapshotPricer(flat_swap_world("cal", holidays=())(ref, 0.0)))

    def numbers(order):
        out = {}
        for tag in order:
            p = pa if tag == "a" else pb
            b = build(p, maturity="2Y", fixed_rate=4.0)
            out[tag] = (b.terms["effective"], b.obj.value(ctx=ctx(p)).pv, b.obj.dv01(ctx=ctx(p)))
        return out

    ab, ba = numbers("ab"), numbers("ba")
    assert ab == ba and ab["a"] != ab["b"], "different calendars, and the same numbers whichever was wrapped or priced last"


def all_operations(p, prev):
    """Every public path that sets acmelib's valuation date."""
    built = build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
    c = ctx(p, prev)
    o = built.obj
    o.value(ctx=c), o.rate(ctx=c), o.dv01(ctx=c), o.gamma(ctx=c), o.delta_ladder(ctx=c), o.acme_zero_dv01(ctx=c)
    for layer in ("carry", "roll", "delta", "convexity"):
        evaluate_layer(SPEC, layer, env(built, c))
    build(p, maturity="5Y")  # `par` resolution
    build(p, maturity="5Y").obj.dv01(ctx=c)  # a fresh swap: the analytic path


def test_the_valuation_date_is_set_inside_the_guard_and_restored_after_every_operation():
    acme.set_valuation_date("2031-03-03")  # somebody else's date: must survive
    p, prev = pricer(), pricer(dt.date(2024, 6, 11))
    with C.guard(REF) as d:
        assert d == REF and acme.valuation_date() == "2024-06-12"
    assert acme.valuation_date() == "2031-03-03"
    all_operations(p, prev)
    assert acme.valuation_date() == "2031-03-03"


def test_a_call_that_starts_with_no_valuation_date_ends_with_none_even_when_it_raises():
    p = pricer(dt.date(2024, 6, 12))
    aged = build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2).obj
    assert acme.valuation_date() is None
    aged.value(ctx=ctx(p))
    assert acme.valuation_date() is None
    # a snapshot that lacks the fixings a started swap needs: the call raises, and the date is still restored
    s = p.snapshot
    bare = A.wrap(SnapshotPricer(MarketSnapshot(s.ts, s.reference_date, s.curves, {}, None, s.calendars)))
    with pytest.raises(MarketDataUnavailable, match="no fixings"):
        aged.value(ctx=ctx(bare))
    assert acme.valuation_date() is None


def test_acmelibs_own_exceptions_never_reach_pricebt_they_are_translated():
    p = pricer()
    aged = build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
    s = p.snapshot
    fx = s.fixings["fixings"]  # a hole of three business days just before the reference date
    holes = A.wrap(SnapshotPricer(MarketSnapshot(s.ts, s.reference_date, s.curves, {"fixings": FixingsSeries("fixings", fx.dates[:-5] + fx.dates[-2:], fx.values[:-5] + fx.values[-2:])},
                                                 None, s.calendars)))
    no_curve = A.wrap(SnapshotPricer(MarketSnapshot(s.ts, s.reference_date, {}, {}, None, s.calendars)))
    other_cal = make_spec(conv={**CONV, "calendar": "elsewhere"})
    cases = [
        (MarketDataUnavailable, lambda: aged.obj.value(ctx=ctx(holes)), "fixings missing"),
        (MarketDataUnavailable, lambda: build(no_curve), "no discount curve"),
        (ConfigError, lambda: build(p, other_cal), "no calendar 'elsewhere'"),
        (ConfigError, lambda: build(p, maturity="5D"), "year or month tenors"),
        (ConfigError, lambda: build(p, extras={"repo": {}}), "not supported"),
        (ConfigError, lambda: build(p, extras={"par_spread_bp": 5}, fixed_rate=4.0), "only to fixed_rate 'par'"),
        (ConfigError, lambda: build(p, fixed_rate="mid"), "fixed_rate"),
        (ConfigError, lambda: aged.obj.value(ctx=MarkContext.standalone(snap())), "not an AcmePricer"),
    ]
    for exc, call, text in cases:
        with pytest.raises(exc, match=text) as ei:
            call()
        assert not isinstance(ei.value, acme.AcmeError) and isinstance(ei.value.__cause__, (acme.AcmeError, type(None), ValueError, TypeError, ConfigError)), text
    assert acme.valuation_date() is None
    with pytest.raises(MethodCallError, match="SwapExpired"):  # an expired swap has no par rate in acmelib: the adapter's `rate` answers NaN before asking, so ask acmelib directly
        with C.translate():
            with C.guard(dt.date(2030, 1, 2)):
                acme.par_rate(build(p, maturity="1Y").obj.contract, acme.Market(acme.ZeroCurve("2030-01-02", ["2030-06-03"], [0.04])))


def test_the_swap_is_plain_data_it_deep_copies_and_pickles_and_the_pricer_reregisters_its_calendars(monkeypatch):
    p = pricer()
    built = build(p, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
    v = built.obj.value(ctx=ctx(p)).pv
    for clone in (copy.deepcopy(built.obj), pickle.loads(pickle.dumps(built.obj))):
        assert clone.value(ctx=ctx(p)).pv == v and clone is not built.obj
    blob = pickle.dumps(p)
    monkeypatch.setattr(importlib.import_module("acmelib.calendars"), "_REGISTRY", {})  # a fresh process: acmelib's global registry starts empty
    q = pickle.loads(blob)
    assert q.calendar("cal") == p.calendar("cal") and acme.is_business_day("2024-06-12", q.calendar("cal"))
    fresh = build(q, maturity="5Y")
    assert fresh.terms["effective"] == build(pricer(), maturity="5Y").terms["effective"]


def test_par_and_the_spread_over_par_are_resolved_to_percent_and_a_numeric_rate_is_percent_too():
    p = pricer()
    par = build(p, fixed_rate="par")
    assert abs(par.obj.value(ctx=ctx(p)).pv) < 1e-6 and 3.9 < par.terms["fixed_rate"] < 4.3
    assert par.terms["fixed_rate"] == pytest.approx(evaluate_measure(SPEC, "rate", env(par, ctx(p))), abs=1e-12), "the resolved par rate is the `rate` measure, in percent"
    wide = build(p, fixed_rate="par", extras={"par_spread_bp": 25})
    assert wide.terms["fixed_rate"] == pytest.approx(par.terms["fixed_rate"] + 0.25, abs=1e-12), "25bp is 0.25 percent"
    four = build(p, fixed_rate=4.0)
    assert four.terms["fixed_rate"] == 4.0 and four.obj.contract.fixed == pytest.approx(0.04, abs=1e-15), "percent in pricebt, a decimal in acmelib"
    assert four.obj.value(ctx=ctx(p)).pv == pytest.approx(ref_side(snap(), fixed_rate=4.0)[1].obj.value(ctx=ctx(R.wrap(snap()))).pv, abs=1e-6)


@pytest.mark.parametrize("terms", [{"effective": "spot", "maturity": "10Y"}, {"effective": "1Y", "maturity": "5Y"}, {"effective": "6M", "maturity": "18M"}, {"effective": "2W", "maturity": "3M"},
                                   {"effective": dt.date(2024, 6, 20), "maturity": dt.date(2029, 3, 15)}, {"effective": dt.date(2024, 5, 27), "maturity": "2Y"}, {"maturity": dt.date(2025, 2, 28)}])
def test_effective_and_maturity_resolve_like_the_reference_stack_on_the_snapshots_holidays(terms):
    sp = snap()
    p = A.wrap(sp)
    got, want = build(p, fixed_rate=4.0, **terms), ref_side(sp, fixed_rate=4.0, **terms)[1]
    assert (got.terms["effective"], got.terms["maturity"]) == (want.terms["effective"], want.terms["maturity"])
    assert isinstance(got.terms["effective"], dt.date) and not isinstance(got.terms["effective"], str), "concrete dates, not acmelib's ISO strings"


# ================================================================================ F. the tie-out and the negative controls
STACKS = {
    "reference": [REF_OVERLAY], "acme": [ACME_OVERLAY],
    "no_percent": [MISTAKES / "acme_swap_no_percent.yaml"], "wrong_sign": [MISTAKES / "acme_swap_wrong_sign.yaml"],
    "lower_key": [MISTAKES / "acme_swap_lower_key.yaml"], "wrong_calendar": [MISTAKES / "acme_swap_wrong_calendar.yaml"],
}
AUDIT = ("dv01", "gamma", "rate", "delta_ladder")  # the ladder is NOT in the harness's default audit measures: it is compared only when asked for


@pytest.fixture(scope="module")
def tieout():
    return run_tieout(BASE, STACKS, audit_measures=AUDIT, selftest=True)


def row(rep, level, quantity):
    (r,) = [x for x in rep.rows if x.level == level and x.quantity == quantity]
    return r


def test_the_acme_stack_ties_out_with_the_reference_at_every_level_with_the_shipped_tolerances(tieout):
    rep = tieout.reports["reference_vs_acme"]
    assert tieout.header["selftest"] == "passed" and rep.passed and tieout.selftest is not None
    assert {r.level for r in rep.rows} == {"L0", "L1", "L2", "L3", "L4"}
    assert {r.status for r in rep.rows} <= {"exact", "noise"}, [(r.level, r.quantity, r.status) for r in rep.rows if r.status not in ("exact", "noise")]
    assert not rep.header["declared_tolerances"], "nothing is declared: the shipped defaults are what passed"
    assert [row(rep, "L0", q).status for q in ("snapshot_digests", "resolved_terms", "conventions_digest")] == ["exact"] * 3
    assert max(row(rep, "L1", q).max_rel for q in ("pv", "cash", "financing")) < 1e-9 and row(rep, "L4", "equity").max_rel < 1e-9
    assert row(rep, "L2", "dv01").max_rel < 1e-9 and row(rep, "L2", "rate").max_rel < 1e-12 and row(rep, "L2", "gamma").max_rel < 1e-6
    for layer in ("carry", "roll", "delta", "convexity", "unexplained", "tay_delta"):
        assert row(rep, "L3", layer).max_rel < 1e-5, layer


def test_the_tie_out_is_not_vacuous(tieout):
    """It compared real things: three positions of two directions and two notionals, a flow paid inside the window, a swap that matured, every ladder bucket, every layer."""
    rep, ref = tieout.reports["reference_vs_acme"], tieout.results["reference"]
    assert len(ref.positions) == 3 and rep.header["n_points"] >= 15
    assert sorted(ref.trades["term_direction"]) == [-1, 1, 1] and sorted(ref.trades["term_notional"]) == [5e6, 1e7, 2.5e7]
    marks = ref.record.audit["marks"]
    assert (marks["cash"].abs() > 100).any(), "the short swap pays a coupon inside the window: the cash sweep is compared"
    assert (marks.loc[marks["position"] == "P000003", "pv"].iloc[-3:] == 0.0).all(), "and it has matured by the end (value 0)"
    buckets = [r.quantity for r in rep.rows if r.level == "L2" and r.quantity.startswith("delta_ladder.")]
    assert sorted(buckets) == sorted(f"delta_ladder.{t}" for t in A.DEFAULT_TENORS) and all(row(rep, "L2", b).n >= 50 and row(rep, "L2", b).status in ("exact", "noise") for b in buckets)
    assert all(row(rep, "L3", layer).n >= 30 for layer in ("carry", "roll", "delta", "convexity", "unexplained", "tay_delta", "tay_convexity"))
    assert tieout.results["acme"].reconcile().ok and ref.reconcile().ok


def test_the_report_discloses_what_the_acme_stack_bound(tieout):
    disc = tieout.reports["reference_vs_acme"].disclosure
    b = disc["bindings"]["acme"]["usd_sofr_ois"]
    assert b["rate"]["scale"] == 100.0 and b["dv01"]["sign"] == -1.0 and b["delta_ladder"]["reduce"] == "acme_ladder_to_tenor_dict" and b["carry"]["target"] == "function:acme_adapter.swap:carry"
    assert disc["factories"]["acme"]["usd_sofr_ois"] == "acme_adapter:swap" and disc["wraps"]["acme"]["primary"] == "acme_adapter:wrap"
    assert disc["bindings"]["reference"]["usd_sofr_ois"]["rate"]["scale"] == 1.0, "the unit conversions are the stack's own: the base digest cannot see them, the report can"


# each control: (overlay name, level, quantity, status it must have, what the located difference must contain)
CONTROLS = {
    "no_percent": ("L2", "rate", "exceeds", "position="),
    "wrong_sign": ("L2", "dv01", "exceeds", "position="),
    "lower_key": ("L2", "delta_ladder.30Y", "structure", "position="),
    "wrong_calendar": ("L0", "resolved_terms", "input", "effective: 2024-05-29 vs 2024-05-30"),
}


@pytest.mark.parametrize("name", list(CONTROLS))
def test_negative_control_the_harness_catches_the_mistake_at_the_expected_level_and_quantity(tieout, name):
    level, quantity, status, located = CONTROLS[name]
    good, bad = tieout.reports["reference_vs_acme"], tieout.reports[f"reference_vs_{name}"]
    known = row(good, level, quantity)
    assert known.status in ("exact", "noise") and good.passed, "known answer first: the correct wiring passes this very check"
    r = row(bad, level, quantity)
    assert r.status == status and not bad.passed and located in r.where, (r.status, r.where)
    assert row(bad, "L0", "snapshot_digests").status == "exact" and row(bad, "L0", "conventions_digest").status == "exact", "the inputs and the shared conventions are identical: only the wiring differs"


def test_the_forgotten_percent_scale_is_off_by_a_factor_of_100_and_touches_only_what_reads_rate(tieout):
    bad = tieout.reports["reference_vs_no_percent"]
    assert row(bad, "L2", "rate").max_rel == pytest.approx(0.99, abs=1e-9), "0.0403 against 4.03"
    failing = {(r.level, r.quantity) for r in bad.failures()}
    assert ("L2", "rate") in failing and {("L3", "tay_delta"), ("L3", "tay_unexplained")} <= failing, "the baseline decomposition turns the rate move into bp"
    for level, q in (("L1", "pv"), ("L1", "cash"), ("L2", "dv01"), ("L2", "gamma"), ("L4", "equity"), ("L3", "carry"), ("L3", "delta")):
        assert row(bad, level, q).status in ("exact", "noise"), (level, q)


def test_the_forgotten_sign_flips_dv01_exactly_and_touches_only_what_reads_dv01(tieout):
    bad = tieout.reports["reference_vs_wrong_sign"]
    assert row(bad, "L2", "dv01").max_rel == pytest.approx(2.0, abs=1e-9), "|dv01 - (-dv01)| / |dv01|: every position has the opposite sign"
    failing = {(r.level, r.quantity) for r in bad.failures()}
    assert ("L2", "dv01") in failing and ("L3", "tay_delta") in failing
    for level, q in (("L1", "pv"), ("L2", "rate"), ("L2", "gamma"), ("L4", "equity"), ("L3", "carry"), ("L3", "delta")):
        assert row(bad, level, q).status in ("exact", "noise"), (level, q)


def test_the_lowercased_ladder_key_makes_every_bucket_a_structure_failure_and_counts_the_errors(tieout):
    bad = tieout.reports["reference_vs_lower_key"]
    buckets = [row(bad, "L2", f"delta_ladder.{t}") for t in A.DEFAULT_TENORS]
    assert all(r.status == "structure" and "exists in reference only" in r.note for r in buckets)
    assert row(bad, "L1", "pv").status == "exact" and row(bad, "L2", "dv01").status in ("exact", "noise"), "value and dv01 are fine: only the ladder measure is refused"
    errs = tieout.results["lower_key"].errors
    assert errs["error"].str.contains("not a canonical tenor").sum() >= 50, "one recorded error per audited mark"
    assert len(tieout.results["acme"].errors) == len(tieout.results["reference"].errors) < 10, "the correct wiring records exactly what the reference records (the `rate` of a matured swap)"


def test_the_wrong_calendar_shows_first_as_a_date_difference_at_l0_and_everything_after_is_attributed_to_it(tieout):
    bad = tieout.reports["reference_vs_wrong_calendar"]
    assert "effective: 2024-05-29 vs 2024-05-30" in row(bad, "L0", "resolved_terms").where, "dates first: the spot date moved by the extra holiday"
    assert row(bad, "L1", "pv").status == "input" and row(bad, "L4", "equity").status == "input", "the L0 difference explains the PV difference downstream"
    assert row(bad, "L0", "snapshot_digests").status == "exact", "the snapshots are the same: the library's use of the calendar is what differs"


def test_a_wrap_that_forgets_the_holidays_stops_the_run_instead_of_pricing_on_invented_fixings():
    with pytest.raises(MarketDataUnavailable, match=r"1 overnight fixings missing between 2024-05-28 and 2024-06-19; first 2024-06-19"):
        run_stack(BASE, [MISTAKES / "acme_swap_no_holidays.yaml"], audit_measures=AUDIT)
    good_built, good = run_stack(BASE, [ACME_OVERLAY], audit_measures=AUDIT)
    assert len(good.positions) == 3 and good_built.base_hash


# ================================================================================ G. overlay rules and the CLI
def test_the_overlay_sets_only_factory_bind_and_wrap_and_registry_belongs_to_the_base():
    overlay = yamlio.load_file(ACME_OVERLAY)
    validate_overlay(overlay)
    base = yamlio.load_file(BASE)
    assert set(base["registry"]["allow"]) >= {"acme_adapter"}
    merged = apply_stack(base, overlay)
    assert merged["instruments"]["usd_sofr_ois"]["factory"] == "acme_adapter:swap" and merged["market"]["pricers"]["primary"]["wrap"] == "acme_adapter:wrap"
    assert merged["instruments"]["usd_sofr_ois"]["conventions"] == base["instruments"]["usd_sofr_ois"]["conventions"], "the conventions block is shared by every stack"
    with pytest.raises(ConfigError, match="a stack may not set 'registry'"):
        apply_stack(base, {"registry": {"allow": ["acme_adapter"]}})
    with pytest.raises(ConfigError, match="conventions"):
        apply_stack(base, {"instruments": {"usd_sofr_ois": {"conventions": {"day_count": "act365f"}}}})


def cli_run(capsys, *stacks):
    args = ["tieout", str(BASE)]
    for s in stacks:
        args += ["--stack", s]
    code = cli.main(args)
    return code, capsys.readouterr().out


def test_the_cli_tie_out_passes_for_the_correct_wiring_and_fails_for_a_mistake(capsys):
    code, out = cli_run(capsys, f"reference={REF_OVERLAY}", f"acme={ACME_OVERLAY}")
    assert code == 0 and "TIE-OUT PASSED" in out and "harness self-test: passed" in out
    code, out = cli_run(capsys, f"reference={REF_OVERLAY}", f"acme={ACME_OVERLAY}", f"acme_wrong_sign={MISTAKES / 'acme_swap_wrong_sign.yaml'}")
    assert code == 1 and "TIE-OUT FAILED" in out and "reference_vs_acme_wrong_sign: L2.dv01" in out and "reference_vs_acme:" not in out.split("TIE-OUT FAILED")[1]


# ================================================================================ H. no vendor library is named
def example_files():
    return [p for p in EXAMPLE.rglob("*") if p.is_file() and p.suffix in (".py", ".yaml") and "__pycache__" not in p.parts]


def violations(files, root):
    hits = []
    for f in files:
        if f.suffix == ".py":
            hits += scan.scan_imports(f, root) + [h for h in scan.scan_names(f, root)] + scan.scan_prose(f, root)
        else:
            hits += scan.scan_yaml(f, root)
    return hits


def test_no_file_of_the_example_imports_or_names_a_pricing_library_or_the_banned_tokens():
    files = example_files()
    assert len(files) >= 14 and any(f.name == "swap.py" for f in files), "the scan sees the whole example"
    assert violations(files, EXAMPLE) == []


def test_that_guard_is_not_vacuous_it_reports_a_planted_import_a_planted_token_and_a_planted_yaml_value(tmp_path):
    lib_a, lib_b, prefix = "rate" + "slib", "Quant" + "Lib", "r" + "l_"  # the tokens are assembled here so that this file names none of them
    root = tmp_path / "ex"
    root.mkdir()
    (root / "bad.py").write_text(f"import {lib_a}\nx = '{lib_b.lower()}'\n# a comment naming {'AR' + 'BS'}\n", encoding="utf8")
    (root / "bad.yaml").write_text("bind: {rate: {target: {method: " + prefix + "par}}}\n", encoding="utf8")
    got = {h.kind for h in violations([root / "bad.py", root / "bad.yaml"], root)}
    assert {"import", "literal", "prose", "yaml-value"} <= got
