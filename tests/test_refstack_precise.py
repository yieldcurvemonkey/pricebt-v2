"""The golden swap of `test_refstack_golden` to six decimals: the rateslib adapter's automatic-differentiation values (frozen here as numbers) against the reference stack's
directional finite differences with Richardson extrapolation. Agreement to 1e-4 currency on 70,302 pins the definition, not just the ballpark."""
import datetime as dt

import pytest

from test_refstack import ctx
from test_refstack_golden import golden_pricer
from pricebt.testing import refstack as R

pytestmark = pytest.mark.core

PRECISE = {"carry": -561.821381, "roll": 1532.069756, "delta": 70302.165974, "convexity": -50.233472, "residual": 0.023966,
           "V0": -369827.42601, "V1": -212114.85814, "cash": -86490.363026}


def test_the_golden_swap_to_six_decimals():
    p0, p1 = golden_pricer(dt.date(2025, 1, 2), "c0"), golden_pricer(dt.date(2025, 1, 16), "c1")
    terms = {"side": "pay", "direction": 1, "effective": dt.date(2024, 1, 8), "maturity": dt.date(2027, 1, 8), "notional": 50e6, "fixed_rate": 4.20}
    sw = R.swap_factory(p0, p0.ts, terms=terms, conventions={**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}).obj
    d = sw.decomposition(ctx(p1, prev=p0))
    for k, v in PRECISE.items():
        assert d[k] == pytest.approx(v, abs=2e-6 if k == "convexity" else 1e-4), k
