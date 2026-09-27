"""The weighted least-squares ladder hedge sizing on plain dicts (spec S4: bucket -> currency per +1bp; no pandas type, no adapter). Migrated from `test_ladder.py`, where the
same maths ran on `pd.Series`; the numbers are unchanged."""
import pytest

from pricebt.errors import SizingError
from pricebt.strategy import ladder_hedge_quantities

pytestmark = pytest.mark.core

NET = {"2Y": 100.0, "5Y": -40.0, "10Y": 60.0}
UNITS = [{"2Y": 10.0}, {"5Y": 20.0}, {"10Y": -5.0}]


def worst(d):
    return max(abs(v) for v in d.values())


def test_exact_fit_with_as_many_legs_as_buckets():
    r = ladder_hedge_quantities(NET, UNITS)
    assert r.quantities == pytest.approx((-10.0, 2.0, 12.0)) and worst(r.after) < 1e-9 and r.skip is None
    assert r.before == NET and set(r.after) == set(NET)


def test_fewer_legs_than_buckets_leave_the_least_squares_residual():
    r2 = ladder_hedge_quantities(NET, UNITS[:1])  # one leg cannot flatten three buckets: LS residual, L1 falls only in that bucket
    assert r2.after["2Y"] == pytest.approx(0.0, abs=1e-9) and r2.after["5Y"] == -40.0 and r2.skip is None


def test_risk_percentage_weights_ridge_and_target():
    r3 = ladder_hedge_quantities(NET, UNITS, risk_percentage=50.0)
    assert r3.quantities == pytest.approx((-5.0, 1.0, 6.0)) and r3.after["2Y"] == pytest.approx(50.0)
    r4 = ladder_hedge_quantities(NET, UNITS, weights={"2Y": 0.0, "5Y": 1.0, "10Y": 1.0}, ridge=0.0)
    assert max(abs(r4.after["5Y"]), abs(r4.after["10Y"])) < 1e-9
    r5 = ladder_hedge_quantities(NET, UNITS, ridge=1e6)
    assert max(abs(x) for x in r5.quantities) < 1e-2 and r5.skip is None
    tgt = ladder_hedge_quantities(NET, UNITS, target={"2Y": 100.0})
    assert tgt.quantities[0] == pytest.approx(0.0, abs=1e-9)


def test_a_bucket_missing_from_a_dict_is_zero_and_the_union_of_buckets_is_fitted():
    r = ladder_hedge_quantities({"2Y": 100.0}, [{"2Y": 10.0, "5Y": 1.0}, {"5Y": 4.0}])
    assert set(r.after) == {"2Y", "5Y"} and worst(r.after) < 1e-9 and r.quantities == pytest.approx((-10.0, 2.5))


def test_skip_and_guards():
    assert ladder_hedge_quantities({"2Y": 1e-15}, UNITS).skip == "zero_residual_risk"
    assert ladder_hedge_quantities(NET, UNITS, min_trade_risk=1e6).skip == "zero_residual_risk"
    with pytest.raises(SizingError, match="zero risk"):
        ladder_hedge_quantities(NET, [{"2Y": 0.0}])
    with pytest.raises(SizingError, match="at least one"):
        ladder_hedge_quantities(NET, [])
    with pytest.raises(SizingError, match="non-finite"):
        ladder_hedge_quantities({"2Y": float("nan")}, UNITS)
    with pytest.raises(SizingError, match="typo"):
        ladder_hedge_quantities(NET, UNITS, target={"10y": 5.0})  # a target bucket nothing touches (a lower-case tenor is a typo, not a bucket)
