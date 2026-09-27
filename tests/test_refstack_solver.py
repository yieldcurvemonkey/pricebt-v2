"""The node solver of the reference stack's risk-curve bootstrap: Newton from the seed, bisection as the safety net."""
import math

import pytest

from pricebt.errors import ConfigError
from pricebt.testing import refstack as R

pytestmark = pytest.mark.core


def test_newton_finds_a_smooth_root_from_a_nearby_seed_in_few_evaluations():
    calls = []

    def f(x):
        calls.append(x)
        return math.exp(x) - 0.75

    root = R._solve_node(f, math.log(0.75) + 1e-3)
    assert root == pytest.approx(math.log(0.75), abs=1e-15) and len(calls) <= 10, "quadratic convergence with an accurate slope: eight evaluations here, not dozens"


def test_newton_gives_up_on_a_far_root_and_the_bisection_finds_it():
    f = lambda x: math.exp(x) - 0.75  # noqa: E731
    assert R._newton_node(f, math.log(0.75) + 5.0) is None, "a root more than 1.0 away from the seed is not Newton's business"
    assert R._solve_node(f, math.log(0.75) + 5.0) == pytest.approx(math.log(0.75), abs=1e-14)


def test_a_flat_slope_makes_newton_decline_and_bisection_answers():
    f = lambda x: 0.0 if abs(x - 0.3) > 1e9 else (x - 0.3) ** 3  # noqa: E731  # the slope at the seed is ~0
    assert R._newton_node(f, 0.3) is None or abs(R._newton_node(f, 0.3) - 0.3) < 1e-3
    assert R._solve_node(f, 0.3) == pytest.approx(0.3, abs=1e-4)


def test_a_function_without_a_root_is_a_load_error_not_a_wrong_curve():
    with pytest.raises(ConfigError, match="could not bracket"):
        R._solve_node(lambda x: x * x + 1.0, 0.1)
    with pytest.raises(ConfigError, match="could not bracket"):
        R._solve_node(lambda x: 1.0, 0.1)  # a zero slope: Newton declines, and the bracketing finds nothing to bracket


def test_bisection_alone_reaches_machine_precision_on_a_monotone_function():
    assert R._bisect_node(lambda x: x ** 3 - 0.5, 0.9) == pytest.approx(0.5 ** (1 / 3), abs=1e-14)
    assert R._bisect_node(lambda x: 0.0, 0.2) == 0.2, "an exact zero at the first midpoint is returned as it is"


def test_the_bootstrap_repriced_pillars_are_par_to_machine_precision_with_the_newton_solver():
    from test_refstack import CONV, REF, build, pricer

    p = pricer(rate=0.037)
    rc = R._risk_curves(p, R.swap_conventions(CONV), R.DEFAULT_TENORS)
    for tenor, par in zip(rc.tenors, rc.pars):
        s, _ = build(p, maturity=tenor, fixed_rate=par * 100.0)
        assert s.npv(p, rc.base, REF) == pytest.approx(0.0, abs=1e-6), tenor
