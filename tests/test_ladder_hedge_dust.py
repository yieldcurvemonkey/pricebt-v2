"""`ladder_hedge_quantities(min_trade_risk=)`: a leg whose own trade risk is at most the minimum is not traded (its share stays in the residual).

Least squares over hedge legs whose ladders leak a little into each other's buckets returns numerically tiny quantities for the legs the target does not need; without this filter
each of them is a trade in the ledger (a 1e-7 unit of a 1mm swap). The total trade is still skipped when its whole risk is below the minimum (unchanged)."""
import pytest

from pricebt.strategy.sizing import ladder_hedge_quantities

pytestmark = pytest.mark.core

TARGET = {"10Y": 1_000_000.0}
# the 10Y leg leaks into the 7Y bucket (as a solver-noisy ladder does), which the other legs then try to soak up with dust-sized quantities
U2, U5, U10, U30 = {"2Y": 190.0, "7Y": 0.01}, {"5Y": 450.0, "7Y": 0.02}, {"10Y": 810.0, "7Y": 0.5}, {"30Y": 1900.0, "7Y": -0.03}


def solve(units=(U2, U5, U10, U30), **kw):
    return ladder_hedge_quantities({}, list(units), target=TARGET, **kw)


def test_without_the_filter_the_legs_the_target_does_not_need_get_dust_quantities():
    q = solve().quantities
    assert q[2] == pytest.approx(1_000_000.0 / 810.0, rel=1e-6)
    assert all(0.0 < abs(x) < 1e-3 for x in (q[0], q[1], q[3])), q


def test_the_filter_drops_exactly_those_legs_and_leaves_the_others_untouched():
    plain, dusted = solve().quantities, solve(min_trade_risk=100.0).quantities
    assert dusted[0] == dusted[1] == dusted[3] == 0.0
    assert dusted[2] == plain[2], "a leg that is kept is not touched"


def test_the_residual_reported_after_the_hedge_is_the_one_of_the_quantities_actually_returned():
    res = solve(min_trade_risk=100.0)
    assert res.after["10Y"] == pytest.approx(res.before["10Y"] + 810.0 * res.quantities[2], abs=1e-9)
    assert res.after["7Y"] == pytest.approx(0.5 * res.quantities[2], rel=1e-9), "the dropped legs no longer soak up the 7Y leak"
    assert res.after["2Y"] == 0.0 and res.after["30Y"] == 0.0, "nothing was traded in the dropped legs, so their buckets did not move"


def test_a_leg_with_real_risk_survives_the_filter():
    net = {"2Y": -50_000.0}  # the book has 2Y risk that the 2Y leg is needed for
    res = ladder_hedge_quantities(net, [U2, U5, U10, U30], target=TARGET, min_trade_risk=100.0)
    assert res.quantities[0] == pytest.approx(50_000.0 / 190.0, rel=1e-4)
    assert res.quantities[1] == 0.0 and res.quantities[3] == 0.0


def test_the_threshold_is_inclusive_and_is_applied_to_the_scaled_quantity():
    q = solve().quantities[2]
    risk = (810.0 + 0.5) * q  # the trade risk of the 10Y leg: its 10Y bucket plus its 7Y leak
    assert solve(min_trade_risk=risk * 1.01).quantities[2] == 0.0, "at most the minimum: dropped"
    assert solve(min_trade_risk=risk * 0.99).quantities[2] == pytest.approx(q, rel=1e-9)
    half = solve(min_trade_risk=risk * 0.6, risk_percentage=50.0).quantities[2]
    assert half == 0.0, "the leg's risk at 50% (0.5e6) is below 0.6e6 although the unscaled risk is above it"


def test_the_whole_trade_is_still_skipped_when_its_total_risk_is_below_the_minimum():
    res = ladder_hedge_quantities({}, [U10], target={"10Y": 5.0}, min_trade_risk=100.0)
    assert res.skip == "zero_residual_risk" and res.quantities == (0.0,)


def test_no_filter_by_default():
    assert solve(min_trade_risk=0.0).quantities == solve().quantities


def test_the_threshold_is_inclusive_exactly_at_the_leg_risk():
    unit = {"10Y": 810.0}  # one bucket: the leg's risk is a single product, so it can be matched to the last bit
    q = ladder_hedge_quantities({}, [unit], target=TARGET).quantities[0]
    risk = abs(810.0 * q)
    assert ladder_hedge_quantities({}, [unit], target=TARGET, min_trade_risk=risk).quantities == (0.0,), "at most the minimum, exactly equal included: dropped"
    import numpy as np

    assert ladder_hedge_quantities({}, [unit], target=TARGET, min_trade_risk=float(np.nextafter(risk, 0.0))).quantities == (q,), "one ulp below: kept"


def test_a_curve_leg_is_sized_by_the_sum_of_its_absolute_bucket_risks_not_by_their_sum_or_their_largest():
    curve = {"2Y": 100.0, "5Y": -100.0}  # signed buckets cancel (sum 0), the largest is 100, the absolute total is 200
    res = ladder_hedge_quantities({"2Y": -100.0, "5Y": 100.0}, [curve], min_trade_risk=150.0)
    assert res.quantities[0] == pytest.approx(1.0, rel=1e-9), "200 > 150: kept"
    res = ladder_hedge_quantities({"2Y": -100.0, "5Y": 100.0}, [curve], min_trade_risk=250.0)
    assert res.quantities == (0.0,), "200 <= 250: dropped"
