"""GATE 1 (provider) and GATE 2 (wrap) of `arbs_adapter`, as permanent tests: opt-in, live ARBS (spec section 10, G2c pattern), never run by
default. `PRICEBT_LIVE_ARBS=1` (and, if the checkout is not at the default path, `PRICEBT_ARBS_ROOT`) to run this file.

GATE 1: `ArbsErisEodProvider` over a known EOD date agrees with itself (memoisation) and with `CurveSnapshot`'s own invariant (the first node date
IS the reference date). GATE 2: the provider + `wrap` path reproduces ARBS's OWN `RLIRSwapCurve.fair_rate` / `.npv` / `.pv01` on the SAME date, for
a short tenor to machine precision, and for a longer tenor within a measured, DECLARED bound (the long end of ARBS's real curve is a mixed
log-linear/log-cubic-spline curve; pricebt's `CurveSnapshot` vocabulary is closed to `log_linear_df`, so the wrap's rebuild cannot carry the spline
-- see `arbs_adapter/wrap.py`'s module docstring). The calendar cross-check confirms the shipped "nyc" calendar agrees with ARBS's own hard-wired
rateslib "nyc" calendar on every date this file touches, so a silent calendar drift would show up here first.
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import pytest

pytestmark = [pytest.mark.live_arbs]

LIVE_OK = os.environ.get("PRICEBT_LIVE_ARBS") == "1"
LIVE_WHY = "PRICEBT_LIVE_ARBS is not '1' (live ARBS runs are opt-in)"
if LIVE_OK:
    from arbs_adapter import _compat as C

    _root = C.arbs_root()
    if not os.path.isfile(os.path.join(_root, "MDP", "IRSwaps", "IRSwapsMDP.py")):
        LIVE_OK, LIVE_WHY = False, f"ARBS checkout incomplete or not found at {_root!r} (set PRICEBT_ARBS_ROOT)"

pytestmark.append(pytest.mark.skipif(not LIVE_OK, reason=LIVE_WHY))

DATES = [dt.date(2024, 5, 20), dt.date(2024, 5, 21), dt.date(2024, 5, 22), dt.date(2024, 5, 23), dt.date(2024, 5, 24)]
REF = dt.date(2024, 5, 24)


def ny(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="America/New_York")


@pytest.fixture(scope="module")
def provider():
    from arbs_adapter.provider import ArbsErisEodProvider

    return ArbsErisEodProvider(DATES)


@pytest.fixture(scope="module")
def raw_curve():
    """ARBS's OWN curve for REF, fetched directly (unguarded, exactly like a human's notebook cell)."""
    from arbs_adapter import _compat as C

    mdp = C.irswaps_mdp()(source="ERIS_EOD_LIVE-RL_BASIC")
    return mdp.get_pricer(request={"curve_name": "USD-SOFR-1D", "timestamp": REF})


# ------------------------------------------------------------------------------ GATE 1: the provider
def test_gate1_provider_memoises_and_the_reference_date_invariant_holds(provider):
    ts = ny("2024-05-24 17:00")
    p1 = provider.get_pricer(ts)
    p2 = provider.get_pricer(ts)
    assert p1 is p2, "get_pricer must memoise per stamp (an LRU keyed by the selected snapshot's stamp)"
    assert len(provider._pricers) == 1

    sel = provider.select(ts)
    snap_a, snap_b = provider._snapshot(sel), provider._snapshot(sel)  # two FRESH fetches, bypassing the pricer-level cache
    assert snap_a.digest() == snap_b.digest(), "two independent fetches of the same date must be byte-identical (ARBS itself is deterministic here)"

    cs = snap_a.curves["sofr"]
    assert cs.node_dates[0] == cs.reference_date == REF
    assert snap_a.ts.tzinfo is not None and snap_a.ts.tz_convert("America/New_York").date() == REF


def test_gate1_a_date_outside_the_known_list_is_market_data_unavailable_not_a_crash(provider):
    from pricebt.errors import MarketDataUnavailable

    with pytest.raises(MarketDataUnavailable):
        provider.get_pricer(ny("2024-05-10 17:00"))


# ------------------------------------------------------------------------------ calendar cross-check
def test_the_shipped_nyc_calendar_agrees_with_arbs_own_calendar_on_every_test_date(provider, raw_curve):
    from arbs_adapter.wrap import wrap

    p = wrap(provider.get_pricer(ny("2024-05-24 17:00")))
    cd = p.calendars["nyc"]
    arbs_cal = raw_curve.calendar()  # rateslib's own built-in "nyc" NamedCal, what ARBS's calendar_advance/spot_date hard-wire to
    for d in DATES + [dt.date(2024, 5, 27), dt.date(2024, 5, 25), dt.date(2024, 5, 26)]:  # Memorial Day + the surrounding weekend, just outside the window
        shipped_is_bus_day = d.weekday() < 5 and d not in cd.holidays
        arbs_is_bus_day = bool(arbs_cal.is_bus_day(dt.datetime(d.year, d.month, d.day)))
        assert shipped_is_bus_day == arbs_is_bus_day, f"{d}: shipped nyc calendar says business_day={shipped_is_bus_day}, ARBS's own nyc says {arbs_is_bus_day}"


# ------------------------------------------------------------------------------ GATE 2: wrap
def _direct(raw_curve, tenor, fixed_rate_pct):
    swap = raw_curve.build_irswap(fwd="0D", tenor=tenor, fixed_rate=fixed_rate_pct / 100.0, notional=1e7)
    return {"par": float(raw_curve.fair_rate(swap)) * 100.0, "npv": float(raw_curve.npv(swap)), "pv01": float(raw_curve.pv01(swap))}


def _via_adapter(provider, tenor, fixed_rate_pct):
    from arbs_adapter.wrap import wrap

    p = wrap(provider.get_pricer(ny("2024-05-24 17:00")))
    swap = p.curve_handle.build_irswap(fwd="0D", tenor=tenor, fixed_rate=fixed_rate_pct / 100.0, notional=1e7)
    return {"par": float(p.curve_handle.fair_rate(swap)) * 100.0, "npv": float(p.curve_handle.npv(swap)), "pv01": float(p.curve_handle.pv01(swap))}


def test_gate2_short_tenor_agrees_to_machine_precision(provider, raw_curve):
    """A 2Y swap sits entirely inside ARBS's densely-noded, plain log-linear region (no spline knot before it on this date): the wrap's log-linear
    rebuild and ARBS's own served curve must therefore agree EXACTLY, not just within a tolerance."""
    a, b = _direct(raw_curve, "2Y", 4.5), _via_adapter(provider, "2Y", 4.5)
    assert a["par"] == b["par"] and a["npv"] == b["npv"] and a["pv01"] == b["pv01"], (a, b)


def test_gate2_long_tenor_agrees_within_the_declared_spline_bound_not_machine_precision(provider, raw_curve):
    """A 10Y swap's cashflows extend past ARBS's first spline knot (measured ~2027-03-17 on this date): the wrap's log-linear rebuild differs from
    ARBS's real mixed log-linear/log-cubic curve by a genuine, measured, bounded amount (a difference of DEFINITION, not a bug -- see the module
    docstring of `arbs_adapter/wrap.py`). Measured 2024-05-24: par ~5.0e-5 relative, pv01 ~5.2e-5 relative, NPV ~$163-$167 on $10mm notional. The
    bound below (1e-3 relative, ~$5,000 absolute) is 20x the measured gap: tight enough to catch a real regression (a wrong node, a wrong day count,
    a wrong sign), loose enough to never be a tolerance fight over the spline itself."""
    a, b = _direct(raw_curve, "10Y", 4.0), _via_adapter(provider, "10Y", 4.0)
    assert a["par"] == pytest.approx(b["par"], rel=1e-3)
    assert a["pv01"] == pytest.approx(b["pv01"], rel=1e-3)
    assert abs(a["npv"] - b["npv"]) < 5000.0, (a["npv"], b["npv"])
    # and the gap is not simply absent (a regression that made the two paths identical would be suspicious, not reassuring, on a 10Y swap)
    assert abs(a["par"] - b["par"]) > 1e-6


def test_gate2_cross_check_curve_handle_pv01_matches_a_bare_rl_irs_on_the_identical_curve(provider):
    """The layer-facing bare `rl.IRS` (built by `ArbsSwap.__init__` via `pillar_swap`) and ARBS's own `curve_handle.pv01` must agree to machine
    precision on the SAME (wrap-rebuilt) curve: two constructions of the identical economic swap, priced with two different code paths."""
    from pricebt.contrib.rateslib._compat import rl
    from arbs_adapter.wrap import wrap

    p = wrap(provider.get_pricer(ny("2024-05-24 17:00")))
    sw = p.curve_handle.build_irswap(fwd="0D", tenor="10Y", fixed_rate=0.041, notional=1e7)
    bare = rl.IRS(
        effective=sw.leg1.schedule.effective, termination=sw.leg1.schedule.termination, spec="usd_irs", calendar="nyc",
        fixed_rate=float(sw.fixed_rate), notional=1e7, leg2_fixing_method="rfr_payment_delay",
    )
    a = float(p.curve_handle.pv01(sw))
    b = float(bare.analytic_delta(curves=p.curve, leg=1))
    assert a == b, (a, b)
