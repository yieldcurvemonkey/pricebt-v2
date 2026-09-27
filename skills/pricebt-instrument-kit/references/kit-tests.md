# Tests that pin a Kit (template, executed, mutation-checked) and the repository tests to copy

Put the template in `tests/test_<lib>_swap.py` with your library's partition marker (`adapter_<lib>`, see `pricebt-guards-and-packaging`) and `pytest.importorskip("<LIB>")` at the top of a real
adapter's test. `acme_adapter` stands in for your package (`A`): the lines tagged `# <LIB>` are the ones you rewrite. It compares your kit with the reference stack
(`pricebt.testing.refstack`) on ONE snapshot (`flat_swap_world`), for both directions and two notionals, so a unit or a sign that cancels once cannot hide. A tests directory outside
`pytest.ini`'s `pythonpath = . src tests` must put its own package on `sys.path` from `Path(__file__)` (never from the working directory).

```python
"""TEMPLATE: the tests every Kit needs (tests/test_<lib>_swap.py). `A` = your adapter package; here acme_adapter stands in for it. Marker: your library's partition marker."""
import datetime as dt
import math

import pytest

import acme_adapter as A  # <LIB>: import <lib>_adapter as A
from pricebt.contracts.binding import Env, call_binding
from pricebt.contracts.evaluate import evaluate_measure, evaluate_value
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.errors import ConfigError
from pricebt.pricable import MarkContext, Valuation
from pricebt.snapshot import SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.layer_conformance import flat_swap_world

SCHEMAS = SchemaRegistry.default()
ALLOW = ("pricebt", "acme_adapter")  # <LIB>: ("pricebt", "<lib>_adapter"): what a base config's registry.allow gives the loader
HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
WORLD = flat_swap_world("cal", holidays=HOL)  # a snapshot with a calendar named "cal", a flat curve and consistent fixings
REF = dt.date(2024, 6, 12)
CONV = {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
REF_CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}


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


def test_the_factory_returns_concrete_dates_percent_rates_and_the_unsigned_notional():
    sp_ = SnapshotPricer(WORLD(REF, 0.0))
    for side in ("pay", "receive"):
        _, b = build(sp_, side=side, fixed_rate="par")
        _, r = reference(sp_, side=side, fixed_rate="par")
        assert type(b.terms["effective"]) is dt.date and type(b.terms["maturity"]) is dt.date  # not ISO strings, not Timestamps
        assert (b.terms["effective"], b.terms["maturity"]) == (r.terms["effective"], r.terms["maturity"])
        assert b.terms["fixed_rate"] == pytest.approx(r.terms["fixed_rate"], abs=1e-9) and 3.5 < b.terms["fixed_rate"] < 4.5  # PERCENT
        assert b.terms["notional"] == 1e7 and set(b.terms) - {"side", "direction"} == set(r.terms), "the same resolved keys as the reference stack (L0 compares every term_<key>)"


@pytest.mark.parametrize("terms", [{"effective": "spot", "maturity": "10Y"}, {"effective": "1Y", "maturity": "5Y"}, {"effective": dt.date(2024, 6, 20), "maturity": dt.date(2029, 3, 15)}, {"maturity": dt.date(2025, 2, 28)}])
def test_dates_resolve_like_the_reference_on_the_snapshots_holidays(terms):
    sp_ = SnapshotPricer(WORLD(REF, 0.0))
    _, b = build(sp_, fixed_rate=4.0, **terms)
    _, r = reference(sp_, fixed_rate=4.0, **terms)
    assert (b.terms["effective"], b.terms["maturity"]) == (r.terms["effective"], r.terms["maturity"])


@pytest.mark.parametrize("m", ["value", "rate", "dv01", "gamma"])  # one case per quantity: the first three go green on the skeleton before `gamma` exists
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
            else:
                assert evaluate_measure(s, m, e) == pytest.approx(getattr(r.obj, m)(ctx=rc), rel=1e-4, abs=1e-6), (m, side, notional)
            if m == "dv01":
                assert evaluate_measure(s, "dv01", e) * (1 if side == "pay" else -1) > 0, "payer-positive dollar delta"


def test_every_default_binding_runs_and_returns_its_declared_type():
    sp_, prev = SnapshotPricer(WORLD(REF, 0.0)), SnapshotPricer(WORLD(dt.date(2024, 6, 11), 0.0))
    p, b = build(sp_, effective=dt.date(2024, 1, 8), maturity=dt.date(2027, 1, 8), fixed_rate=4.2)
    c = MarkContext.standalone(p, prev=A.wrap(prev))
    e = Env(pricer=p, ctx=c, instrument=b.obj, terms=b.terms, state={})
    out = {n: call_binding(bd, e) for n, bd in spec().bindings.items()}
    assert isinstance(out["value"], Valuation) and math.isfinite(out["value"].pv)
    assert all(isinstance(out[k], float) and math.isfinite(out[k]) for k in ("dv01", "gamma", "rate", "carry", "roll", "delta", "convexity"))
    assert list(out["delta_ladder"]) == list(spec().bindings["delta_ladder"].keys)


def test_a_pricer_that_was_not_wrapped_is_refused_with_the_fix():
    raw = SnapshotPricer(WORLD(REF, 0.0))
    with pytest.raises(ConfigError, match="set `wrap` on the pricer role"):
        TradeTemplate("t", spec(), {"side": "pay", "maturity": "5Y", "notional": 1e7}).build(raw, raw.ts)


def test_a_convention_the_library_cannot_express_names_the_reason_and_the_supported_set():
    with pytest.raises(ConfigError, match=r"CFG-CONVENTION-UNSUPPORTED.*supported: \["):
        p = A.wrap(SnapshotPricer(WORLD(REF, 0.0)))
        TradeTemplate("t", spec({**CONV, "day_count": "thirty360"}), {"side": "pay", "maturity": "5Y", "notional": 1e7}).build(p, p.ts)
```

Run (PowerShell, project root; the directory that contains your package on the path): expected, every test passes once the kit is complete.

```powershell
$env:PYTHONPATH = "src;tests;<dir containing <lib>_adapter>"
python -m pytest tests/test_<lib>_swap.py -q -o addopts= -p no:cacheprovider
```

**On the skeleton of `references/kit-skeleton.md` exactly two tests are red BY DESIGN** (executed with `lib_adapter` = the skeleton: only these two fail; every case passes against `acme_adapter`): the case
`test_units_and_signs_agree...[gamma]` (the skeleton's `gamma` raises `NotImplementedError: second derivative for the SAME +1bp move as dv01, per unit as built`) and
`test_every_default_binding_runs_and_returns_its_declared_type` (`gamma`, `delta_ladder`, the four layers and the value of a seasoned swap are stubs: `NotImplementedError: seasoned swap: ...`). The cases
`[value]`, `[rate]`, `[dv01]` and every factory, dates, conventions and unwrapped-pricer test must already be green: that is the first checkpoint. The two red tests go green as you implement the stubs
(`pricebt-layers-and-ladder` for the ladder and the layers; `acme_adapter/swap.py` for the seasoned swap).

## Mutation check (executed on the template with acme_adapter)

Mutate the DEFAULT BLOCK in-process (`A.SWAP_BIND[name] = {**orig, ...}`), rerun, expect a failure, restore. The unmutated run passed first (exit 0). Every mutant was killed (exit 1, an assertion
failure, not a collection error):

| mutant | killed by |
|---|---|
| `rate` binding without `scale: 100` (percent lost) | `test_units_and_signs_agree...[rate]` |
| `dv01` binding without `sign: -1` (receiver's sign leaks) | `test_units_and_signs_agree...[dv01]` (its payer-positive assertion too) |
| `gamma` binding without `sign: -1` | `test_units_and_signs_agree...[gamma]` |

Add your own for what your kit converts in CODE (a per-million factor, the `Valuation` sign, the ISO-string dates): remove the conversion, expect a red test.

## Layer and ladder tests come from the conformance kit, not from this file

`pricebt.testing.layer_conformance.run_kit(Setup(spec=..., wrap=..., terms=..., mirror_terms=..., world=flat_swap_world("cal", holidays=HOL), t0=..., t1=...))`
(model: `tests/test_layer_conformance_adapters.py`, and the example's `kit_setup` in `tests/test_skills_example_acme.py`), plus a seasoned swap across a payment date
(`birth=`, `payment_window=`), the same over a COARSE step (`t1 = t0 + 9 days`: a flow paid inside the interval can depend on a fixing published inside it) with `cash_window_check.py`, and a test that the kit itself CATCHES a layer bound without its sign. See `pricebt-layers-and-ladder`, `kit-blind-spots.md`.

## The repository tests to read and copy

| file | what it pins (copy the pattern) |
|---|---|
| `tests/test_spec.py` | `test_the_factory_receives_terms_plus_direction_and_the_verbatim_conventions_and_nothing_else`; `test_resolved_terms_are_returned_merged_over_the_given_ones`; `test_a_factory_may_return_only_what_it_resolved_and_the_rest_is_kept_with_the_direction`; `test_an_unresolved_par_token_or_relative_tenor_in_the_returned_terms_is_an_error_T4`; `test_a_factory_that_does_not_return_built_is_an_error`; `test_the_factory_may_be_a_dotted_path_under_the_allow_list_and_is_imported_only_then`; `test_a_user_bind_overrides_one_name_of_the_kits_default_block_and_keeps_the_rest`; `test_a_method_target_missing_from_the_known_class_is_a_load_error_naming_what_exists`; `test_templates_and_specs_survive_deepcopy_and_pickle` |
| `tests/test_quantlib_swap.py` | conformance with only the kit's default block; every default binding runs and returns its contract type; spot and termination dates equal plain python over the snapshot holidays; a forward-start tenor; explicit dates pass through; only month and year tenors and `par` for unstarted swaps; `par` resolves to a percent rate that prices at zero; a spread over par is in basis points; `fixed_rate: 4` prices as 4 percent, not 400 and not 0.04; `dv01` is currency per bp and scales linearly; payer and receiver are exact mirrors on value, measures, ladder and every layer; the swap is plain data (deep-copies, pickles) |
| `tests/test_quantlib_conventions.py` | the shipped blocks are complete and accepted; an unknown key is an error, never ignored; a missing key is an error (no implicit defaults); an unknown token is rejected with the vocabulary; a vocabulary token the adapter does not support names the reason; types are checked; every key has a stated support decision; the shipped dicts are not mutated by validation |
| `tests/test_quantlib_bond.py`, `tests/test_rl_bond.py`, `tests/test_refstack_bond.py` | the bond kit: token/alias resolution, quotes, coupon sweep, financing, yield-space layers, price convention |
| `tests/test_stacks.py` | the overlay rules (what a stack may set) |
| `tests/test_layer_conformance_adapters.py` | every adapter passes the same independent layer identities (needs no allow-list only because the shipped kits' default blocks hold method targets: an external Kit with `function` targets needs its own helper with `allow=`) |
| `tests/test_skills_example_acme.py` (sections B, C, D) | the worked example's kit tests: conformance kit, default block, units and signs against the reference, extension-name collision, a user binding replaces one default, the allow-list rule, conventions (unsupported value names reason and supported set; every supported non-default value reaches the library and agrees with the reference stack) |

## Harness gaps an external adapter's tests meet

* `pytest.ini` has `pythonpath = . src tests`: a package outside them is not importable until you add it (see above). The nested core run in `tests/guards/test_guards.py` uses `cwd=PROJECT` and
  `PYTHONPATH=src`, so an example living outside `tests` cannot rely on it.
* An unmarked test is a COLLECTION ERROR (`tests/guards/partition.py: PARTITIONS`): every file needs `core`, `adapter_<lib>` (after registering it), `fixtures` or a live marker.
* `tests/test_layer_conformance_adapters.py` builds the spec without `allow=`: not usable for a Kit with dotted-path bindings.
* Run single files while working: the whole suite is slow (many minutes).
