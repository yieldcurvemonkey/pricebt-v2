import math

import pytest

from pricebt.errors import SizingError
from pricebt.strategy import ScalingActionType, hedge_quantity, nav_scale, rebalance_delta, risk_scale, weighted_split

pytestmark = pytest.mark.core


def test_risk_scale_signed_and_guarded():
    assert risk_scale(100.0, 4.0) == 25.0
    assert risk_scale(100.0, -4.0) == -25.0  # a negative-risk template flips its legs
    for bad in (0.0, 1e-15, math.nan, math.inf):
        with pytest.raises(SizingError):
            risk_scale(1.0, bad)


def test_nav_hand_case_constant_plus_scaled_cost():
    # pot 100, unit price 2, cost = 1 (fixed) + 0.5*s  ->  2s + 1 + 0.5 s = 100  ->  s = 39.6
    s = nav_scale(100.0, 2.0, lambda s: 1.0 + 0.5 * s)
    assert s == pytest.approx(39.6, abs=1e-9)


@pytest.mark.parametrize("agg", ["sum", "max", "min"])
@pytest.mark.parametrize("pot", [50.0, 1000.0])
def test_nav_identity_spends_exactly_the_pot_for_any_aggregation(agg, pot):
    fixed, k1, k2, price = 3.0, 0.02, 0.05, 1.7
    comps = lambda s: [fixed + 0.0 * s, k1 * price * s, k2 * price * s]
    pick = {"sum": sum, "max": max, "min": min}[agg]
    cost = lambda s: pick(comps(s))
    s = nav_scale(pot, price, cost)
    assert s * price + cost(s) == pytest.approx(pot, rel=1e-9)
    assert (s * 1.001) * price + cost(s * 1.001) > pot


def test_nav_pot_below_fixed_cost_gives_no_order_and_zero_price_raises():
    assert nav_scale(5.0, 1.0, lambda s: 10.0) == 0.0  # Constant cost 10 > pot 5: the guard, not a tiny order
    with pytest.raises(SizingError):
        nav_scale(5.0, 0.0, lambda s: 0.0)
    with pytest.raises(SizingError):
        nav_scale(5.0, -1.0, lambda s: 0.0)


def test_weighted_split_sums_to_total_and_weights():
    q = weighted_split(100.0, [1.0, -3.0])  # gs: |r_i| / sum |r|
    assert q == pytest.approx((25.0, 75.0)) and sum(q) == pytest.approx(100.0)
    inv = weighted_split(100.0, [1.0, 4.0], weighting="inverse_risk")
    assert inv[0] * 1.0 == pytest.approx(inv[1] * 4.0) and sum(inv) == pytest.approx(100.0)  # equal risk contribution
    assert weighted_split(90.0, [1.0, 7.0, 2.0], weighting="equal") == pytest.approx((30.0, 30.0, 30.0))
    with pytest.raises(SizingError):
        weighted_split(1.0, [0.0, 0.0])
    with pytest.raises(SizingError):
        weighted_split(1.0, [0.0, 1.0], weighting="inverse_risk")


def test_hedge_quantity_postcondition_net_plus_q_u_equals_target():
    for net, u, tgt in [(225.9, 0.45, 0.0), (-16.4, 0.45, 25.0), (100.0, -2.0, -10.0)]:
        q, why = hedge_quantity(net, u, target=tgt)
        assert why is None and net + q * u == pytest.approx(tgt)
    q, _ = hedge_quantity(100.0, 2.0, risk_percentage=50.0)
    assert q == pytest.approx(-25.0)  # 50% hedge


def test_hedge_quantity_zero_residual_and_zero_unit_risk():
    q, why = hedge_quantity(0.8 - 0.8 + 5e-16, 2.0)
    assert why == "zero_residual_risk"
    q, why = hedge_quantity(10.0, 1.0, min_trade_risk=20.0)
    assert why == "zero_residual_risk"
    with pytest.raises(SizingError):
        hedge_quantity(10.0, 0.0)
    with pytest.raises(SizingError):
        hedge_quantity(math.nan, 1.0)


def test_rebalance_delta():
    assert rebalance_delta(400.0, 0.0, 100.0) == -4.0
    with pytest.raises(SizingError):
        rebalance_delta(1.0, 2.0, 0.0)


def test_scaling_type_coercion_case_insensitive():
    assert ScalingActionType.coerce("nav") is ScalingActionType.NAV
    assert ScalingActionType.coerce("Risk_Measure") is ScalingActionType.risk_measure
    with pytest.raises(ValueError):
        ScalingActionType.coerce("bogus")
