"""Known answers for the reference stack, LIBRARY-FREE: two numbers-only worlds frozen from independent research runs (spec A2 golden cases).

* swap: the seasoned 3y payer 50mm of the verified rateslib research (`tests/data_golden_swap.json`: two solver-fitted curves, seeded fixings and holidays frozen as
  data), coupon paid inside [2025-01-02, 2025-01-16);
* bond: a 10mm long 4.25 Nov-34 note, 2025-05-12 y 4.55 -> 2025-05-20 y 4.62, coupon paid 05-15, repo 4.30 (the independent calculation of the QuantLib adapter tests).

Every layer definition of the reference stack is pinned here by a number a second, unrelated implementation produced.
"""
import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, Quote, QuoteSet, Security, SnapshotPricer
from pricebt.testing import refstack as R
from test_refstack import ctx

pytestmark = pytest.mark.core

NY = "America/New_York"
DATA = json.loads((Path(__file__).parent / "data_golden_swap.json").read_text(encoding="utf8"))
iso = dt.date.fromisoformat


def golden_pricer(ref, key):
    c = DATA[key]
    curve = CurveSnapshot("c", ref, tuple(iso(d) for d in c["dates"]), tuple(c["dfs"]))
    fx = [(iso(d), v) for d, v in zip(DATA["fixings"]["dates"], DATA["fixings"]["percent"]) if iso(d) < ref]
    fixings = FixingsSeries("f", tuple(d for d, _ in fx), tuple(v for _, v in fx), "percent")
    snap = MarketSnapshot(pd.Timestamp(f"{ref} 17:00", tz=NY), ref, {"c": curve}, {"f": fixings}, None, {"nyc": CalendarData("nyc", tuple(iso(h) for h in DATA["holidays"]))})
    return R.wrap(SnapshotPricer(snap))


@pytest.fixture(scope="module")
def world():
    p0, p1 = golden_pricer(dt.date(2025, 1, 2), "c0"), golden_pricer(dt.date(2025, 1, 16), "c1")
    terms = {"side": "pay", "direction": 1, "effective": dt.date(2024, 1, 8), "maturity": dt.date(2027, 1, 8), "notional": 50e6, "fixed_rate": 4.20}
    sw = R.swap_factory(p0, p0.ts, terms=terms, conventions={**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}).obj
    return sw, p0, p1


def test_the_swap_schedule_is_the_three_year_annual_schedule(world):
    sw, _, _ = world
    assert sw.schedule.unadjusted == (dt.date(2024, 1, 8), dt.date(2025, 1, 8), dt.date(2026, 1, 8), dt.date(2027, 1, 8))
    assert sw.schedule.pays[0] == dt.date(2025, 1, 10), "the first coupon is paid inside the test window"


def test_marks_and_the_cash_paid_in_the_window_are_the_verified_numbers(world):
    sw, p0, p1 = world
    v0, v1 = sw.value(ctx=ctx(p0)), sw.value(ctx=ctx(p1, prev=p0))
    assert v0.pv == pytest.approx(-369827.43, abs=0.01) and v1.pv == pytest.approx(-212114.86, abs=0.01)
    assert v0.cash == 0.0 and v1.cash == pytest.approx(-86490.36, abs=0.01)
    assert v1.pv - v0.pv + v1.cash == pytest.approx(71222.20, abs=0.01)


def test_the_four_layers_and_the_residual_are_the_verified_numbers(world):
    sw, p0, p1 = world
    d = sw.decomposition(ctx(p1, prev=p0))
    assert d["carry"] == pytest.approx(-561.82, abs=0.01), "forwards realised in date space, incl. the flow paid in the window"
    assert d["roll"] == pytest.approx(1532.07, abs=0.01), "roll (12159.98) + realised-versus-assumed fixings (-10627.91)"
    assert d["delta"] == pytest.approx(70302.16, abs=0.01), "delta (70190.64) + resampling onto the new curve's nodes (111.52)"
    assert d["convexity"] == pytest.approx(-50.23, abs=0.01)
    assert d["residual"] == pytest.approx(0.02, abs=0.01)
    assert d["V0"] == pytest.approx(-369827.43, abs=0.01) and d["V1"] == pytest.approx(-212114.86, abs=0.01) and d["cash"] == pytest.approx(-86490.36, abs=0.01)
    assert d["carry"] + d["roll"] + d["delta"] + d["convexity"] + d["residual"] == pytest.approx(71222.20, abs=0.01)


def test_the_receiver_is_the_exact_mirror_on_value_and_every_layer(world):
    sw, p0, p1 = world
    terms = {"side": "receive", "direction": -1, "effective": dt.date(2024, 1, 8), "maturity": dt.date(2027, 1, 8), "notional": 50e6, "fixed_rate": 4.20}
    rec = R.swap_factory(p0, p0.ts, terms=terms, conventions={**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}).obj
    a, b = sw.decomposition(ctx(p1, prev=p0)), rec.decomposition(ctx(p1, prev=p0))
    for k in a:
        assert b[k] == pytest.approx(-a[k], rel=1e-12, abs=1e-9), k
    assert rec.dv01(ctx=ctx(p1)) == pytest.approx(-sw.dv01(ctx=ctx(p1)), rel=1e-12)


def test_the_started_swaps_dv01_and_ladder_are_consistent_at_the_new_snapshot(world):
    sw, _, p1 = world
    lad = sw.delta_ladder(ctx=ctx(p1))
    assert sum(lad.values()) == pytest.approx(sw.dv01(ctx=ctx(p1)), rel=1e-14) and sw.dv01(ctx=ctx(p1)) > 0
    assert all(abs(v) < 1e-6 * abs(lad["1Y"] + lad["2Y"] + lad["3Y"]) for k, v in lad.items() if k in ("5Y", "7Y", "10Y", "15Y", "20Y", "30Y")), "a 2y-left swap has no risk beyond its maturity"


# ------------------------------------------------------------------ the bond
BOND = Security("TEST00001", 4.25, dt.date(2024, 11, 15), dt.date(2034, 11, 15))
BOND_CONV = dict(R.UST_CONVENTIONS, calendar="wk")


def bond_pricer(ref, y):
    qs = QuoteSet(ref, {BOND.id: Quote(ytm=y)}, {BOND.id: BOND}, {}, "market")
    return R.wrap(SnapshotPricer(MarketSnapshot(pd.Timestamp(f"{ref} 17:00", tz=NY), ref, {}, {}, qs, {"wk": CalendarData("wk")})))


@pytest.fixture(scope="module")
def bond_world():
    p0, p1 = bond_pricer(dt.date(2025, 5, 12), 4.55), bond_pricer(dt.date(2025, 5, 20), 4.62)
    b = R.bond_factory(p1, p1.ts, terms={"security": BOND.id, "direction": 1, "notional": 10e6, "extras": {"repo": {"gc_rate": 4.30}}}, conventions=BOND_CONV).obj
    return b, p0, p1


def test_the_bond_marks_flows_and_financing_are_the_independent_numbers(bond_world):
    b, p0, p1 = bond_world
    v0, v1 = b.value(ctx=ctx(p0)), b.value(ctx=ctx(p1, prev=p0))
    assert v0.pv == pytest.approx(9979417.302, abs=0.01) and v1.pv == pytest.approx(9724039.126, abs=0.01)
    assert v1.cash == pytest.approx(212500.0, abs=1e-6) and v0.cash == 0.0 and v0.financing == 0.0
    assert v1.financing == pytest.approx(-9535.8876, abs=0.001)
    assert v1.pv - v0.pv + v1.cash + v1.financing == pytest.approx(-52414.064, abs=0.01)


def test_the_bond_yield_space_layers_are_the_independent_numbers(bond_world):
    b, p0, p1 = bond_world
    c = ctx(p1, prev=p0)
    L = {k: getattr(b, k)(ctx=c) for k in ("carry", "roll", "delta", "convexity")}
    assert L["carry"] == pytest.approx(136.580, abs=0.01) and L["delta"] == pytest.approx(-52718.946, abs=0.01) and L["convexity"] == pytest.approx(168.692, abs=0.01)
    assert L["roll"] == 0.0, "no on-the-run curve in this snapshot"
    total = b.value(ctx=c).pv - b.value(ctx=ctx(p0)).pv + b.value(ctx=c).cash + b.value(ctx=c).financing
    assert total - sum(L.values()) == pytest.approx(-0.3896, abs=0.01)


def test_carry_is_the_whole_pnl_when_the_yield_does_not_move_and_includes_cash_and_financing(bond_world):
    b, p0, _ = bond_world
    same_y = bond_pricer(dt.date(2025, 5, 20), 4.55)
    c = ctx(same_y, prev=p0)
    v = b.value(ctx=c)
    assert v.cash == pytest.approx(212500.0) and v.financing != 0.0
    total = v.pv - b.value(ctx=ctx(p0)).pv + v.cash + v.financing
    assert b.carry(ctx=c) == pytest.approx(total, rel=1e-12), "carry = cash + pull to par at the old yield + funding, and nothing else moved"
    assert b.delta(ctx=c) == 0.0 and b.convexity(ctx=c) == 0.0 and b.roll(ctx=c) == 0.0


def test_delta_is_measured_from_the_rolled_yield_not_from_the_old_one():
    """With a quoted yield curve the roll layer is nonzero and delta starts from the rolled yield: delta = k * dP/dy * (y1 - (y0 + pulldown))."""
    import numpy as np

    secs = [Security("A2", 4.0, dt.date(2022, 5, 15), dt.date(2026, 5, 15)), Security("A10", 4.5, dt.date(2022, 5, 15), dt.date(2034, 5, 15))]

    def pricer_at(ref, ya, yb):
        qs = QuoteSet(ref, {"A2": Quote(ytm=ya), "A10": Quote(ytm=yb)}, {s.id: s for s in secs}, {"CT2": "A2", "CT10": "A10"}, "market")
        return R.wrap(SnapshotPricer(MarketSnapshot(pd.Timestamp(f"{ref} 17:00", tz=NY), ref, {}, {}, qs, {"wk": CalendarData("wk")})))

    p0, p1 = pricer_at(dt.date(2024, 6, 3), 4.5, 4.3), pricer_at(dt.date(2024, 6, 4), 4.52, 4.36)
    b = R.bond_factory(p0, p0.ts, terms={"security": "A10", "direction": 1, "notional": 2e7}, conventions=BOND_CONV).obj
    c = ctx(p1, prev=p0)
    y0, y1 = b.mark(p0)["ytm"], b.mark(p1)["ytm"]
    ttm0 = (dt.date(2034, 5, 15) - dt.date(2024, 6, 3)).days / 365.25
    ttm_a2 = (dt.date(2026, 5, 15) - dt.date(2024, 6, 3)).days / 365.25
    xs, ys = [ttm_a2, ttm0], [4.5, 4.3]
    pull = float(np.interp(ttm0 - 1 / 365.25, xs, ys) - np.interp(ttm0, xs, ys))
    d1, d2 = b._derivs(y0, dt.date(2024, 6, 4))
    k = 2e7 / 100
    assert list(R.ytm_curve(p0)[0]) == pytest.approx(xs) and list(R.ytm_curve(p0)[1]) == pytest.approx(ys), "the curve is the on-the-run (CT) points only"
    assert b.roll(ctx=c) == pytest.approx(k * d1 * pull, rel=1e-12) and b.delta(ctx=c) == pytest.approx(k * d1 * (y1 - (y0 + pull)), rel=1e-12)
    assert b.convexity(ctx=c) == pytest.approx(0.5 * k * d2 * (y1 - y0) ** 2, rel=1e-12)
