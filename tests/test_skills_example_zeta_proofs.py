"""The service example (`skills/pricebt-wire-external-library/example-service/`): the two proofs of the zeta adapter in one file (`pricebt-conformance-and-tieout`, adapted) and its NEGATIVE CONTROLS
(`pricebt-debug-tieout-differences`):

* the layer conformance kit on a fresh swap and on a seasoned swap across a payment date, and the proof that the kit has teeth on this adapter;
* the tie-out against the reference stack with the SHIPPED tolerances, nothing declared, ladder audited through the Python API, the same through the CLI;
* the pillar-basis note: against the reference's DEFAULT eleven pillars the one row that moves is L2.dv01 (and what reads it), by the size the README of the example gives (a pinned gap, not a tolerance);
* the negative controls (eight overlays through the tie-out, one that must stop the run, one that only `client.stats` can see), each showing the correct wiring passing the row the mistake fails;
* the known-answer scripts of `pricebt-layers-and-ladder` (`probes/p6..p8`) run through this adapter's BOUND bindings.

REFERENCE: `--stack reference=configs/adapters/refstack_swap.yaml,<example-service>/zeta_adapter/config/refstack_zeta_pillars.yaml`: the shipped reference overlay plus the one that hands the reference
zeta's TWELVE risk pillars (zeta's par-swap risk curve is fixed; the reference's default has eleven). Needs no pricing library (zeta is fictional): marker `core`."""
import datetime as dt
import os
import subprocess
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
CHILD_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(["src", "tests", str(EXAMPLE)])}  # exactly what the README documents: the child finds zeta through the adapter, not through this test's sys.path

import zeta_adapter as A  # noqa: E402
from zeta_adapter import _compat as C  # noqa: E402
from pricebt import api  # noqa: E402
from pricebt.contracts.schema import SchemaRegistry  # noqa: E402
from pricebt.contracts.spec import build_spec  # noqa: E402
from pricebt.errors import MarketDataUnavailable  # noqa: E402
from pricebt.testing.layer_conformance import Setup, flat_swap_world, run_kit  # noqa: E402
from pricebt.tieout import run_stack, run_tieout  # noqa: E402

CONFIG = EXAMPLE / "zeta_adapter/config"
MISTAKES = CONFIG / "mistakes"
BASE, MY_OVERLAY = CONFIG / "zeta_tieout_base.yaml", CONFIG / "zeta_swap.yaml"
REF_DEFAULT = ROOT / "configs/adapters/refstack_swap.yaml"
REF_ZETA_PILLARS = [REF_DEFAULT, CONFIG / "refstack_zeta_pillars.yaml"]
AUDIT = ("dv01", "gamma", "rate", "delta_ladder")
HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
CONV = {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}  # the calendar NAME of the world below
ALLOW = ("pricebt", "zeta_adapter")


@pytest.fixture(autouse=True)
def own_client():
    C.set_client(zeta.Client())
    C.UPLOADS.update(snapshot=0, derived=0)
    yield
    C.set_client(None)


# ================================================================================== A. the layer conformance kit
def spec_with(bind=None):
    return build_spec("ois", {"factory": A.swap, "conventions": CONV, **({"bind": bind} if bind else {})}, schemas=SchemaRegistry.default(), allow=ALLOW)


def setup(spec=None, **kw):
    terms = {"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2}
    args = dict(spec=spec or spec_with(), wrap=A.wrap, terms=terms, mirror_terms={**terms, "side": "receive"}, world=flat_swap_world("cal", holidays=HOL),
                t0=dt.date(2024, 3, 4), t1=dt.date(2024, 3, 5))
    return Setup(**{**args, **kw})


def test_the_layers_conform():
    rep = run_kit(setup())
    rep.assert_ok()
    assert {r.check for r in rep.rows} >= {"fd_vs_delta_convexity", "mirror", "unexplained_share"}


def test_the_layers_conform_on_a_seasoned_swap_across_a_payment_date():
    terms = {"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}
    rep = run_kit(setup(terms=terms, mirror_terms={**terms, "side": "receive"}, birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 21),
                        payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23))))
    rep.assert_ok()
    assert any(r.check == "cash_sweep" and r.quantity.startswith("value drop") for r in rep.rows), "the cash sweep really ran (it is skipped without payment_window)"


def test_the_layers_conform_over_a_coarse_step_where_a_flow_can_depend_on_a_fixing_published_inside_the_interval():
    """t1 is more than two business days after t0 (`_PAY_LAG_BD`): the cash of the interval is then read from a market rebuilt with the t1 fixings (a derived world). Known answer: the same kit."""
    terms = {"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}
    rep = run_kit(setup(terms=terms, mirror_terms={**terms, "side": "receive"}, birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 29), payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23))))
    rep.assert_ok()


@pytest.mark.parametrize("layer", ["delta", "convexity"])
def test_the_kit_has_teeth_on_this_adapter(layer):
    """Known answer first (the shipped block passes above), then a layer bound with a sign it must not have must fail: zeta is holder-signed, a layer needs none."""
    bad = spec_with({layer: {**A.SWAP_BIND[layer], "sign": -1.0}})
    rep = run_kit(setup(bad))
    assert not rep.ok and "fd_vs_delta_convexity" in {r.check for r in rep.failures()}


# ================================================================================== B. the tie-out
@pytest.fixture(scope="module")
def tieout():
    # delta_ladder is NOT in the default audit measures: without it the ladder is not compared at all
    return run_tieout(BASE, {"reference": REF_ZETA_PILLARS, "zeta": [MY_OVERLAY]}, audit_measures=AUDIT)


def row(rep, level, quantity):
    (r,) = [x for x in rep.rows if x.level == level and x.quantity == quantity]
    return r


def test_the_stack_ties_out_with_the_shipped_tolerances(tieout):
    rep = tieout.reports["reference_vs_zeta"]
    assert tieout.header["selftest"] == "passed" and rep.passed
    assert {r.level for r in rep.rows} == {"L0", "L1", "L2", "L3", "L4"}
    assert rep.header["n_positions"] >= 4 and rep.header["n_points"] >= 15, "it compared something: a tie-out over zero positions, or over tiny sizes below the floors, PASSES"
    assert {r.status for r in rep.rows} <= {"exact", "noise"}, [(r.level, r.quantity, r.status) for r in rep.rows if r.status not in ("exact", "noise")]
    assert sorted(r.quantity for r in rep.rows if r.quantity.startswith("delta_ladder.")) == sorted(f"delta_ladder.{t}" for t in A.ZETA_TENORS), "the ladder was compared, bucket by bucket"
    assert not rep.header["declared_tolerances"], "nothing declared: the shipped defaults passed"
    for q in ("snapshot_digests", "resolved_terms", "conventions_digest"):
        assert row(rep, "L0", q).status == "exact"


def test_the_measured_size_of_the_agreement_is_far_inside_the_tolerances(tieout):
    """The shipped tolerances are the goal, and zeta is far inside them (a tolerance that a run sits on proves little): pv 1e-9, dv01 4e-13 relative, the ladder 1e-8 in currency."""
    rep = tieout.reports["reference_vs_zeta"]
    assert row(rep, "L1", "pv").max_rel < 1e-9 and row(rep, "L2", "dv01").max_rel < 1e-10 and row(rep, "L2", "gamma").max_rel < 1e-8 and row(rep, "L2", "rate").max_rel < 1e-12
    assert max(r.max_abs for r in rep.rows if r.quantity.startswith("delta_ladder.")) < 1e-6
    assert max(row(rep, "L3", q).max_abs for q in ("carry", "roll", "delta", "convexity", "unexplained")) < 1e-4


def test_a_payment_falls_inside_the_window_so_the_cash_path_was_compared(tieout):
    marks = tieout.results["reference"].record.audit["marks"]
    assert (marks["cash"].abs() > 100).any(), "no cash flow in the window: L1.cash compared zeros (add a trade that pays or matures inside the grid)"


def test_the_base_exercises_every_state_of_the_dv01_definition(tieout):
    """dv01 is the annuity for an unstarted swap and the ladder sum for a started one, and the two differ off par: the base must hold an unstarted OFF-PAR swap AND a started one."""
    res = tieout.results["zeta"]
    trades = res.record.trades if hasattr(res.record, "trades") else None
    opens = trades[trades["kind"] == "open"].set_index("position")
    eff, rate = opens["term_effective"], opens["term_fixed_rate"]
    ref_date = dt.date(2024, 5, 24)
    assert any(dt.date.fromisoformat(str(e)[:10]) < ref_date for e in eff), "a swap that has STARTED at inception (the seasoned 2Y)"
    assert any(dt.date.fromisoformat(str(e)[:10]) > dt.date(2024, 6, 21) for e in eff), "a swap that stays UNSTARTED for the whole window (the forward start)"
    assert (rate.astype(float) - 4.0).abs().max() > 0.5, "a strike well away from the market"


def test_every_declared_tolerance_carries_a_reason():
    """The loader accepts a bare number or a mapping without `reason`: the rule is policy, so this test is what enforces it."""
    declared = (api.load(BASE).get("tieout") or {}).get("tolerances") or {}
    assert all(isinstance(v, dict) and str(v.get("reason", "")).strip() for v in declared.values()), f"declared without a reason: {[k for k, v in declared.items() if not (isinstance(v, dict) and str(v.get('reason', '')).strip())]}"


def _cli_tieout(*stacks):
    cmd = [sys.executable, "-m", "pricebt", "tieout", str(BASE)]
    for s in stacks:
        cmd += ["--stack", s]
    return subprocess.run(cmd + ["--reference", "reference"], capture_output=True, text=True, cwd=ROOT, env=CHILD_ENV)


def test_the_cli_ties_out_with_exit_zero():
    out = _cli_tieout(f"reference={REF_DEFAULT},{CONFIG / 'refstack_zeta_pillars.yaml'}", f"zeta={MY_OVERLAY}")
    assert out.returncode == 0 and "TIE-OUT PASSED" in out.stdout and "harness self-test: passed" in out.stdout, out.stdout[-1500:] + out.stderr[-800:]


def test_the_cli_says_failed_with_exit_one_on_the_default_pillars_and_on_a_negative_control():
    """Non-vacuity of the CLI check above: the same command with the reference on its DEFAULT pillars, and with the stack of a mistake, exits 1 and names the row."""
    gap = _cli_tieout(f"reference={REF_DEFAULT}", f"zeta={MY_OVERLAY}")
    assert gap.returncode == 1 and "TIE-OUT FAILED" in gap.stdout and "L2.dv01" in gap.stdout, gap.stdout[-800:] + gap.stderr[-800:]
    bad = _cli_tieout(f"reference={REF_DEFAULT},{CONFIG / 'refstack_zeta_pillars.yaml'}", f"zeta={MISTAKES / 'zeta_swap_no_percent.yaml'}")
    assert bad.returncode == 1 and "TIE-OUT FAILED" in bad.stdout and "L2.rate" in bad.stdout, bad.stdout[-800:] + bad.stderr[-800:]


def test_against_the_references_default_eleven_pillars_only_dv01_and_what_reads_it_move():
    """THE PILLAR BASIS (README of the example, "The pillar-set caveat"): zeta's par-swap risk curve has twelve pillars (1M ... 30Y), the reference's default eleven (3M ... 30Y). Two risk numbers on different
    pillar sets are two measures: on the seasoned 2Y swap dv01 differs by ~1e-4 relative, just past the shipped 1e-4. Nothing else moves (value, cash, rate, the layers) and nothing is declared."""
    res = run_tieout(BASE, {"reference": [REF_DEFAULT], "zeta": [MY_OVERLAY]}, audit_measures=("dv01", "gamma", "rate"))
    rep = res.reports["reference_vs_zeta"]
    assert not rep.passed
    failing = sorted((r.level, r.quantity) for r in rep.failures())
    assert failing == [("L2", "dv01"), ("L3", "tay_delta"), ("L3", "tay_delta/unit"), ("L3", "tay_unexplained"), ("L3", "tay_unexplained/unit")], failing
    r = row(rep, "L2", "dv01")
    assert "P000003" in r.where and 1.0e-4 < r.max_rel < 3e-4, (r.where, r.max_rel)  # the seasoned 2Y: the near-dated flows sit between the 1M and 3M pillars
    for q in ("snapshot_digests", "resolved_terms", "conventions_digest"):
        assert row(rep, "L0", q).status == "exact"
    assert {row(rep, "L1", q).status for q in ("pv", "cash", "financing")} <= {"exact", "noise"} and {row(rep, "L3", q).status for q in ("carry", "roll", "delta", "convexity", "unexplained")} <= {"exact", "noise"}


# ================================================================================== C. negative controls
CONTROLS = {  # name: (level, quantity, status, located text)
    "no_percent": ("L2", "rate", "exceeds", "position="),
    "dv01_per_10bp": ("L2", "dv01", "exceeds", "position="),
    "gamma_per_10bp_sq": ("L2", "gamma", "exceeds", "position="),
    "ladder_no_sign": ("L2", "delta_ladder.5Y", "exceeds", "position=P000002"),
    "delta_layer_wrong_sign": ("L3", "delta", "exceeds", "layer=delta"),
    "wrong_calendar": ("L0", "resolved_terms", "input", "effective: 2024-05-29 vs 2024-05-30"),
    "fixings_in_percent": ("L1", "pv", "exceeds", "position=P000003"),
    "dv01_ladder_sum": ("L2", "dv01", "exceeds", "position=P000004"),
}


@pytest.fixture(scope="module")
def controls():
    stacks = {"reference": REF_ZETA_PILLARS, "good": [MY_OVERLAY], **{n: [MISTAKES / f"zeta_swap_{n}.yaml"] for n in CONTROLS}}
    return run_tieout(BASE, stacks, audit_measures=AUDIT, selftest=False)


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_negative_control_the_harness_catches_the_mistake_at_the_expected_level_and_quantity(controls, name):
    level, quantity, status, where = CONTROLS[name]
    good, bad = controls.reports["reference_vs_good"], controls.reports[f"reference_vs_{name}"]
    assert good.passed and row(good, level, quantity).status in ("exact", "noise"), "known answer first: the correct wiring passes this very row"
    r = row(bad, level, quantity)
    assert r.status == status and where in r.where and not bad.passed, (r.status, r.where)
    if status == "exceeds":
        assert r.max_rel > 1.0 or name in ("wrong_calendar", "dv01_ladder_sum"), "a wiring mistake is a factor, not noise"


def test_the_l0_control_leaves_the_inputs_exact_and_attributes_the_cascade_to_the_dates(controls):
    bad = controls.reports["reference_vs_wrong_calendar"]
    assert row(bad, "L0", "snapshot_digests").status == "exact" and row(bad, "L0", "conventions_digest").status == "exact", "the inputs are identical: only the wrap's calendar differs"
    assert "effective: 2024-05-29 vs 2024-05-30" in row(bad, "L0", "resolved_terms").where


def test_a_forgotten_ladder_sign_touches_only_the_ladder(controls):
    bad = controls.reports["reference_vs_ladder_no_sign"]
    moved = {r.quantity.split(".")[0] for r in bad.failures()}
    assert moved == {"delta_ladder"}, f"dv01 sums the RAW ladder in code, so the sign of the binding reaches nothing but the ladder: {sorted(r.quantity for r in bad.failures())}"
    assert row(bad, "L2", "dv01").status in ("exact", "noise")


def test_a_wrong_scale_on_a_measure_reaches_the_baseline_layers_that_read_it(controls):
    assert {r.quantity for r in controls.reports["reference_vs_no_percent"].failures()} >= {"rate", "tay_delta", "tay_convexity", "tay_unexplained"}
    assert {r.quantity for r in controls.reports["reference_vs_dv01_per_10bp"].failures()} >= {"dv01", "tay_delta", "tay_unexplained"}
    assert row(controls.reports["reference_vs_dv01_per_10bp"], "L2", "gamma").status in ("exact", "noise")


def test_the_dv01_definition_control_is_largest_on_the_unstarted_off_par_swap_and_zero_on_the_started_one(controls):
    """The annuity/ladder-sum mistake is exactly zero on a STARTED swap (both definitions are the ladder sum), 5e-4..1e-3 on an unstarted swap struck at par (the annuity keeps a term the ladder
    does not: pricebt-layers-and-ladder) and 1e-2 on the forward-starting swap struck 60bp off the market: the off-par swap is what locates it."""
    a = controls.results["dv01_ladder_sum"].record.audit["marks"].set_index(["ts", "position"])["measure_dv01"]
    b = controls.results["reference"].record.audit["marks"].set_index(["ts", "position"])["measure_dv01"]
    rel = ((a - b).abs() / b.abs().clip(lower=1.0)).groupby(level="position").max()
    assert rel["P000003"] < 1e-12 and 1e-4 < rel[["P000001", "P000002"]].max() < 2e-3 and rel["P000004"] > 5e-3 and rel["P000004"] > 10 * rel[["P000001", "P000002"]].max(), rel.to_dict()


def test_a_wrap_that_forgets_the_holidays_STOPS_the_run_it_does_not_price_on_invented_data():
    with pytest.raises(MarketDataUnavailable, match=r"Z530.*no SOFR fixing dated 2024-05-27"):
        run_stack(BASE, [MISTAKES / "zeta_swap_no_holidays.yaml"], audit_measures=AUDIT)


def _run_uploads(overlay, layers):
    C.set_client(zeta.Client())
    C.UPLOADS.update(snapshot=0, derived=0)
    cfg = api.load(BASE)
    if not layers:
        cfg["backtest"]["attribution"] = {"layers": []}
    b = api.build(cfg, stack=[api.load(overlay)])
    b.run()
    return b.market.n_wraps, C.client().stats["markets_uploaded"]


def test_negative_control_a_wrap_that_reuploads_gives_the_same_numbers_and_only_client_stats_shows_it():
    n, good = _run_uploads(MY_OVERLAY, layers=False)
    n2, chatty = _run_uploads(MISTAKES / "zeta_swap_reuploading.yaml", layers=False)
    assert n == n2 == 19 and good == 19, "known answer first: the shipped wrap uploads one market per snapshot"
    assert chatty > 3 * good, (good, chatty)  # every request re-uploads: the tie-out passes (numbers identical), the stats do not
    res = run_tieout(BASE, {"reference": REF_ZETA_PILLARS, "chatty": [MISTAKES / "zeta_swap_reuploading.yaml"]}, audit_measures=AUDIT)
    assert res.reports["reference_vs_chatty"].passed, "the numbers are identical: no tie-out row can see this mistake"


# ================================================================================== D. the known-answer scripts of pricebt-layers-and-ladder, through the BOUND measures and layers
@pytest.mark.parametrize("script,ok_rows", [("p6_golden_control.py", 9), ("p7_dv01_consistency.py", 14), ("p8_risk_known_answers.py", 2)])
def test_the_layers_skill_known_answer_scripts_pass_on_this_adapter(script, ok_rows):
    """golden case (the four layers, nine numbers to 0.011), dv01 in every state against the reference, dv01 and gamma against full revaluation. Each script runs its own known answer through the
    adapter's BOUND bindings the way the engine does; `probes/p6..p8` of the example are the skill's blocks with the `LIB` lines changed."""
    out = subprocess.run([sys.executable, str(EXAMPLE / "probes" / script)], capture_output=True, text=True, cwd=ROOT, env=CHILD_ENV)
    assert out.returncode == 0 and out.stdout.count("\nok ") + out.stdout.startswith("ok ") == ok_rows and "BAD" not in out.stdout, out.stdout[-1500:] + out.stderr[-800:]


# ================================================================================== E. the Python facade (method-only default bindings: no allow-list needed)
def test_the_gs_style_facade_builds_an_irswap_on_the_zeta_stack_object():
    """`PricebtSession(market=..., stack=<a Stack OBJECT>)` works for an adapter outside `pricebt` because the default block holds method targets and a reducer by registered name (a dotted
    `stack="zeta_adapter:STACK"` would be refused: the facade resolves strings under `pricebt` only)."""
    import pandas as pd

    from pricebt.common import Currency, PayReceive
    from pricebt.instrument import IRSwap
    from pricebt.pricable import MarkContext
    from pricebt.session import PricebtSession
    from pricebt.testing.synthetic import SyntheticMarket
    from pricebt.timeutil import Calendar

    hol = [dt.date(2024, 5, 27), dt.date(2024, 6, 19), dt.date(2024, 7, 4)]
    mdp = SyntheticMarket("2024-01-02", "2024-12-31", calendar=Calendar(hol), seed=7)
    PricebtSession.reset()
    try:
        with PricebtSession(market=mdp, stack=A.STACK, calendar=Calendar(hol)):
            atm = IRSwap(PayReceive.Pay, "10y", Currency.USD, notional_amount=1e8, name="10y_swap")
            ts = pd.Timestamp("2024-05-24 17:00", tz="America/New_York")
            p = A.wrap(mdp.get_pricer(ts))
            b = atm.build(p, ts)
            c = MarkContext.standalone(p)
            assert isinstance(b.obj, A.ZetaSwap) and b.terms["direction"] == 1 and b.terms["effective"] == dt.date(2024, 5, 29)
            assert abs(b.obj.value(ctx=c).pv) < 1e-6 * 1e8 and b.obj.dv01(ctx=c) * 0.1 > 0, "ATMF: worth zero at inception; a payer's dv01 is positive"
    finally:
        PricebtSession.reset()
    with pytest.raises(Exception, match="CFG-ALLOW"):
        PricebtSession(market=mdp, stack="zeta_adapter:STACK")
