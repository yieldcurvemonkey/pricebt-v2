"""The layer conformance kit (spec L4) on the library-free reference stack, and the kit itself checked on SABOTAGED specs: a checking tool is run on inputs whose answer is
known (a correct stack passes; each deliberately broken layer, sign or accrual is caught by the check meant to catch it).

The sabotage uses the binding's own post-processing knobs (`scale`, `offset`, `sign`) on the reference stack's layer bindings: no test double, one line per fault."""
import copy
import datetime as dt

import pytest

from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Kit, build_spec
from pricebt.snapshot import Security
from pricebt.testing import refstack as R
from pricebt.testing.layer_conformance import KitReport, Setup, flat_bond_world, flat_swap_world, run_kit

pytestmark = pytest.mark.core

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
SWAP_CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
BOND_CONV = {**R.UST_CONVENTIONS, "calendar": "cal"}
SEC = Security("KIT001", 4.0, dt.date(2023, 11, 15), dt.date(2033, 11, 15))
SEASONED = dict(birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 21), payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23)))


def tweak(name, **post):
    """The reference stack's own binding of `name` with a fault dialled in (`offset=`, `scale=`, `sign=`)."""
    return {name: {**copy.deepcopy(R.SWAP_BIND[name]), **post}}


def swap_spec(bind=None, factory=R.swap):
    return build_spec("ois", {"factory": factory, "conventions": SWAP_CONV, "bind": bind or {}}, schemas=SchemaRegistry.default())


def bond_spec(bind=None):
    return build_spec("ust", {"factory": R.bond, "conventions": BOND_CONV, "bind": bind or {}}, schemas=SchemaRegistry.default())


def swap_setup(spec=None, **kw):
    args = dict(spec=spec or swap_spec(), wrap=R.wrap, terms={"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2},
                mirror_terms={"side": "receive", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2},
                world=flat_swap_world("cal", holidays=HOL), t0=dt.date(2024, 3, 4), t1=dt.date(2024, 3, 5))
    args.update(kw)
    return Setup(**args)


def seasoned_swap_setup(spec=None, **kw):
    return swap_setup(spec, terms={"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0},
                      mirror_terms={"side": "receive", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}, **{**SEASONED, **kw})


def bond_setup(spec=None, **kw):
    args = dict(spec=spec or bond_spec(), wrap=R.wrap, terms={"side": "buy", "security": SEC.id, "notional": 1e7}, mirror_terms={"side": "sell", "security": SEC.id, "notional": 1e7},
                world=flat_bond_world(SEC, "cal", holidays=HOL), t0=dt.date(2024, 6, 3), t1=dt.date(2024, 6, 4))
    args.update(kw)
    return Setup(**args)


def failed(rep, check, quantity=""):
    return [r for r in rep.failures() if r.check == check and r.quantity.startswith(quantity)]


# ------------------------------------------------------------------ a correct stack passes every check
def test_the_reference_swap_conforms():
    rep = run_kit(swap_setup())
    rep.assert_ok()
    assert {r.check for r in rep.rows} == {"fd_vs_delta_convexity", "mirror", "unexplained_share"}
    assert rep.unexplained_share["moved"] < 1e-3 and rep.unexplained_share["static"] < 1e-3


def test_the_reference_swap_conforms_when_seasoned_and_across_a_payment_date():
    # struck 2023-05-15 (effective 2023-05-17), 3Y: an annual payment falls on 2024-05-20 (Mon) + 2 business days = 2024-05-22
    rep = run_kit(seasoned_swap_setup())
    rep.assert_ok()
    assert any(r.check == "cash_sweep" and r.quantity.startswith("value drop") for r in rep.rows)


def test_the_reference_bond_conforms_including_the_coupon_sweep():
    rep = run_kit(bond_setup(payment_window=(dt.date(2024, 5, 14), dt.date(2024, 5, 16)), birth=dt.date(2024, 3, 4)))
    rep.assert_ok()
    assert any(r.check == "cash_sweep" and r.quantity.startswith("value drop") for r in rep.rows)


# ------------------------------------------------------------------ each broken thing is caught by the check meant for it
def test_a_delta_layer_that_is_almost_zero_is_caught_by_the_full_revaluation_check():
    rep = run_kit(swap_setup(swap_spec(tweak("delta", scale=1e-6))))
    assert failed(rep, "fd_vs_delta_convexity", "shock")


def test_a_delta_layer_of_the_wrong_sign_is_caught_by_the_full_revaluation_check_but_not_by_the_mirror():
    rep = run_kit(swap_setup(swap_spec(tweak("delta", sign=-1))))
    assert failed(rep, "fd_vs_delta_convexity", "shock") and not failed(rep, "mirror")


def test_a_carry_layer_that_accrues_when_no_time_passes_is_caught():
    rep = run_kit(swap_setup(swap_spec(tweak("carry", offset=1000.0))))
    assert failed(rep, "fd_vs_delta_convexity", "carry")


def test_a_roll_layer_that_moves_when_no_time_passes_is_caught():
    rep = run_kit(swap_setup(swap_spec(tweak("roll", offset=1000.0))))
    assert failed(rep, "fd_vs_delta_convexity", "roll")


def test_a_layer_that_is_not_antisymmetric_is_caught_by_the_mirror_check():
    rep = run_kit(swap_setup(swap_spec(tweak("convexity", offset=50.0))))
    assert failed(rep, "mirror", "convexity")


def test_a_gamma_measure_that_is_not_antisymmetric_is_caught_by_the_mirror_check():
    rep = run_kit(swap_setup(swap_spec(tweak("gamma", offset=1.0))))
    assert failed(rep, "mirror", "gamma")


def test_a_dv01_measure_that_is_not_antisymmetric_is_caught_by_the_mirror_check():
    rep = run_kit(swap_setup(swap_spec(tweak("dv01", offset=10.0))))
    assert failed(rep, "mirror", "dv01")


def test_a_mirror_that_is_not_the_mirror_is_caught_by_the_value_check():
    rep = run_kit(swap_setup(mirror_terms={"side": "receive", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.3}))
    assert failed(rep, "mirror", "value")


def test_a_carry_that_does_not_match_the_cash_booked_is_caught_across_the_payment_date():
    rep = run_kit(seasoned_swap_setup(swap_spec(tweak("carry", offset=500.0))))
    assert failed(rep, "cash_sweep", "value drop")


def test_a_market_move_layer_that_is_not_zero_on_an_unchanged_market_is_caught_across_the_payment_date():
    rep = run_kit(seasoned_swap_setup(swap_spec(tweak("delta", offset=300.0))))
    assert failed(rep, "cash_sweep", "delta")


def test_a_cash_sweep_that_double_counts_the_cash_is_caught_across_the_payment_date():
    class DoubleCash(R.RefSwap):
        def value(self, *, ctx):
            v = super().value(ctx=ctx)
            return type(v)(v.ts, v.pv, 2.0 * v.cash, v.financing, v.meta)

    def factory(pricer, ts, *, terms, conventions):
        built = R.swap_factory(pricer, ts, terms=terms, conventions=conventions)
        s = built.obj
        return type(built)(DoubleCash(effective=s.effective, schedule=s.schedule, sign=s.sign, notional=s.notional, fixed_rate=s.fixed_rate, conv=s.conv), built.terms)

    kit = Kit(factory=factory, asset_class="swap", default_bind=R.SWAP_BIND, cls=DoubleCash, extra=dict(R.swap.extra))
    rep = run_kit(seasoned_swap_setup(swap_spec(factory=kit)))
    assert failed(rep, "cash_sweep", "value drop")


def test_a_window_without_a_payment_is_a_failure_not_a_pass():
    rep = run_kit(swap_setup(payment_window=(dt.date(2024, 3, 5), dt.date(2024, 3, 6))))
    assert failed(rep, "cash_sweep", "a payment falls")


def test_a_layer_that_leaves_most_of_a_market_move_unexplained_breaks_the_bound():
    rep = run_kit(swap_setup(swap_spec(tweak("delta", scale=0.5)), move_bp=5.0))
    assert failed(rep, "unexplained_share", "share (moved") and rep.unexplained_share["moved"] > 0.3


def test_the_report_frames_and_asserts():
    rep = run_kit(swap_setup())
    assert isinstance(rep, KitReport) and len(rep.frame()) == len(rep.rows) and rep.failures() == []
    bad = run_kit(swap_setup(swap_spec(tweak("delta", sign=-1))))
    with pytest.raises(AssertionError, match="layer conformance failed"):
        bad.assert_ok()


# ------------------------------------------------------------------ the kit's own arithmetic
def test_a_delta_layer_that_is_one_percent_off_is_caught():
    assert failed(run_kit(swap_setup(swap_spec(tweak("delta", scale=0.99)))), "fd_vs_delta_convexity", "shock")


def test_a_convexity_layer_that_is_dropped_is_caught_by_the_large_shocks():
    rep = run_kit(swap_setup(swap_spec(tweak("convexity", scale=1e-9))))
    bad = {r.quantity for r in failed(rep, "fd_vs_delta_convexity", "shock")}
    assert bad == {"shock -25bp", "shock +25bp"}, "invisible at +-3bp, visible at +-25bp"


def test_the_reference_swap_conforms_on_the_payment_date_itself():
    rep = run_kit(seasoned_swap_setup(t0=dt.date(2024, 5, 22), t1=dt.date(2024, 5, 23), payment_window=None))
    rep.assert_ok()


def test_a_nan_is_a_failure_not_a_pass():
    from pricebt.testing.layer_conformance import _add

    rep = KitReport()
    _add(rep, "c", "nan", float("nan"), 0.0, 1e9)
    _add(rep, "c", "inf", float("inf"), 0.0, 1e9)
    _add(rep, "c", "ok", 1e-12, 0.0, 1e-9)
    assert [r.passed for r in rep.rows] == [False, False, True]
