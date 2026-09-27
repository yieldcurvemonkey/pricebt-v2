"""`arbs_adapter.swap` as a permanent test: opt-in, live ARBS, never run by default (`PRICEBT_LIVE_ARBS=1`, and `PRICEBT_ARBS_ROOT` if the checkout
is not at the default path). Covers the sign/unit conversions on a known case, started-vs-unstarted `dv01`, the factory's resolved terms, the layer
conformance kit on three shapes (fresh, started, started crossing a payment date) and one `api.run` over a short historical window whose
`reconcile()` is clean.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

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

ROOT = Path(__file__).resolve().parents[1]
DATES = [dt.date(2024, 5, 20), dt.date(2024, 5, 21), dt.date(2024, 5, 22), dt.date(2024, 5, 23), dt.date(2024, 5, 24)]
CONV = {
    "calendar": "nyc", "spot_lag_days": 2, "day_count": "act360", "frequency": "annual", "business_day_convention": "modified_following",
    "payment_lag_days": 2, "compounding": "daily_compounded", "stub": "short_front", "end_of_month": False, "fixing_lag_days": 1, "time_accrual": "lump",
}


def ny(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="America/New_York")


@pytest.fixture(scope="module")
def pricers():
    from arbs_adapter.provider import ArbsErisEodProvider
    from arbs_adapter.wrap import wrap

    prov = ArbsErisEodProvider(DATES)
    return {d: wrap(prov.get_pricer(ny(f"{d.isoformat()} 17:00"))) for d in DATES}


def _spec():
    from pricebt.contracts.schema import SchemaRegistry
    from pricebt.contracts.spec import build_spec

    return build_spec("sw", {"asset_class": "swap", "factory": "arbs_adapter:swap", "conventions": CONV}, schemas=SchemaRegistry.default(), allow=["pricebt", "arbs_adapter"])


def _build(pricer, terms, spec=None):
    from pricebt.contracts.spec import TradeTemplate

    return TradeTemplate("t", spec or _spec(), terms).build(pricer, pricer.ts)


# ------------------------------------------------------------------------------ sign, units, resolved terms
def test_a_payer_struck_below_par_gains_positive_notional_matches_pricebts_payer_positive_convention(pricers):
    from pricebt.pricable import MarkContext

    p = pricers[dt.date(2024, 5, 24)]
    built = _build(p, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": "par"})
    par = built.terms["fixed_rate"]
    below = _build(p, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": par - 1.0}).obj  # struck 100bp below the market
    ctx = MarkContext.standalone(p)
    assert below.value(ctx=ctx).pv > 0, "a payer of a BELOW-market fixed rate must gain, matching ARBS's own notional-sign convention"
    above = _build(p, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": par + 1.0}).obj
    assert above.value(ctx=ctx).pv < 0


def test_rate_is_returned_in_percent_not_decimal(pricers):
    """`ArbsSwap.rate()` itself returns ARBS's DECIMAL; the schema's percent comes from the binding's `scale: 100.0` (`ARBS_SWAP_BIND["rate"]`), so
    this goes through the BOUND measure, exactly as the engine would, not the raw method."""
    from pricebt.contracts.binding import Env
    from pricebt.contracts.evaluate import evaluate_measure
    from pricebt.pricable import MarkContext

    p = pricers[dt.date(2024, 5, 24)]
    spec = _spec()
    built = _build(p, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": "par"}, spec=spec)
    ctx = MarkContext.standalone(p)
    env = Env(pricer=p, ctx=ctx, instrument=built.obj, terms={})
    r = evaluate_measure(spec, "rate", env)
    assert 0.5 < r < 15.0, f"rate() must be PERCENT (e.g. ~4.1), got {r!r}: the ARBS_SWAP_BIND scale: 100.0 line is load-bearing"
    assert r == pytest.approx(built.obj.rate(ctx=ctx) * 100.0)


def test_factory_resolves_concrete_effective_maturity_and_fixed_rate(pricers):
    p = pricers[dt.date(2024, 5, 24)]
    built = _build(p, {"side": "receive", "maturity": "5Y", "notional": 2e7, "fixed_rate": "par"})
    terms = built.terms
    assert isinstance(terms["effective"], dt.date) and terms["effective"] > p.reference_date  # spot: strictly after the reference date
    assert isinstance(terms["maturity"], dt.date) and terms["maturity"] > terms["effective"]
    assert isinstance(terms["fixed_rate"], float) and terms["fixed_rate"] != 0.0
    assert terms["notional"] == 2e7  # unsigned, matching the schema (direction is `side`)


def test_a_literal_zero_percent_fixed_rate_is_refused_not_silently_repriced_at_par(pricers):
    from pricebt.errors import ConfigError

    p = pricers[dt.date(2024, 5, 24)]
    with pytest.raises(ConfigError, match="par sentinel"):
        _build(p, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": 0.0})


# ------------------------------------------------------------------------------ started vs unstarted dv01
def test_dv01_of_a_started_swap_is_the_ladder_sum_not_the_raw_analytic_figure(pricers):
    from pricebt.pricable import MarkContext

    p0, p1 = pricers[dt.date(2024, 5, 23)], pricers[dt.date(2024, 5, 24)]
    fresh = _build(p1, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": "par"}).obj
    started = _build(p1, {"side": "pay", "effective": dt.date(2023, 5, 15), "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}).obj

    ctx = MarkContext.standalone(p1, prev=p0)
    fresh.dv01(ctx=ctx)  # `_prep` sets `._started` as a side effect
    assert fresh._started is False, "a spot-starting swap is not started yet at the reference date"

    dv01 = started.dv01(ctx=ctx)
    ladder_sum = sum(started.delta_ladder(ctx=ctx).values())
    assert started._started is True
    assert dv01 == pytest.approx(ladder_sum, rel=1e-9, abs=1e-6), (dv01, ladder_sum)


def test_dv01_of_an_unstarted_swap_is_arbs_own_pv01_and_matches_arbs_directly(pricers):
    from pricebt.pricable import MarkContext

    p = pricers[dt.date(2024, 5, 24)]
    built = _build(p, {"side": "pay", "maturity": "2Y", "notional": 1e7, "fixed_rate": 4.5})
    ctx = MarkContext.standalone(p)
    dv01 = built.obj.dv01(ctx=ctx)
    direct = p.curve_handle.build_irswap(fwd="0D", tenor="2Y", fixed_rate=0.045, notional=1e7)
    assert dv01 == float(p.curve_handle.pv01(direct))


# ------------------------------------------------------------------------------ ARBS's own extension measures (carry/roll/theta): sanity, not correctness of the required layers
def test_arbs_extension_measures_are_internally_consistent(pricers):
    from pricebt.pricable import MarkContext

    p = pricers[dt.date(2024, 5, 24)]
    sw = _build(p, {"side": "pay", "maturity": "10Y", "notional": 1e7, "fixed_rate": 4.0}).obj
    ctx = MarkContext.standalone(p)
    carry, roll = sw.arbs_carry_bps_running(ctx=ctx), sw.arbs_roll_bps_running(ctx=ctx)
    assert sw.arbs_carry_and_roll_bps_running(ctx=ctx) == pytest.approx(carry + roll, abs=1e-9)
    cf, fwd, roll_dn, theta = (sw.arbs_theta_cashflows(ctx=ctx), sw.arbs_theta_forwarding(ctx=ctx), sw.arbs_theta_rolldown(ctx=ctx), sw.arbs_theta(ctx=ctx))
    assert theta == pytest.approx(cf + fwd + roll_dn, abs=1e-6)  # option is structurally zero for a vanilla IRS


# ------------------------------------------------------------------------------ the layer conformance kit (spec L4)
def _nyc_holidays():
    data = json.loads((ROOT / "configs" / "suite" / "calendars" / "usd_fed.json").read_text(encoding="utf8"))
    return [dt.date.fromisoformat(h) for h in data["holidays"]]


def _swap_setup(**kw):
    import arbs_adapter as A
    from pricebt.contracts.schema import SchemaRegistry
    from pricebt.contracts.spec import build_spec
    from pricebt.testing.layer_conformance import Setup, flat_swap_world

    conv = {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}
    spec = build_spec("ois", {"factory": A.swap, "conventions": conv}, schemas=SchemaRegistry.default(), allow=["pricebt", "arbs_adapter"])
    args = dict(
        spec=spec, wrap=A.wrap, terms={"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2},
        mirror_terms={"side": "receive", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2},
        world=flat_swap_world("nyc", holidays=_nyc_holidays()), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 21),
    )
    args.update(kw)
    return Setup(**args)


def test_layer_conformance_fresh_swap():
    from pricebt.testing.layer_conformance import run_kit

    run_kit(_swap_setup()).assert_ok()


def test_layer_conformance_started_swap_not_crossing_a_payment_date():
    from pricebt.testing.layer_conformance import run_kit

    setup = _swap_setup(
        terms={"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}, mirror_terms={"side": "receive", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0},
        birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 21), t1=dt.date(2024, 5, 22),
    )
    run_kit(setup).assert_ok()


def test_layer_conformance_started_swap_crossing_a_payment_date():
    from pricebt.testing.layer_conformance import run_kit

    setup = _swap_setup(
        terms={"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}, mirror_terms={"side": "receive", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0},
        birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 21), payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23)),
    )
    rep = run_kit(setup)
    rep.assert_ok()
    assert any(r.check == "cash_sweep" and r.quantity.startswith("value drop") for r in rep.rows)


# ------------------------------------------------------------------------------ one api.run over the real historical window
def test_a_run_over_the_config_window_reconciles_cleanly():
    from pricebt import api

    res = api.run(str(ROOT / "configs" / "adapters" / "arbs_swap_base.yaml"))
    assert res.n_errors == 0
    assert res.reconcile().ok
    assert len(res.trades) >= 1
