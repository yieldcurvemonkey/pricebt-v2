"""The service example (`skills/pricebt-wire-external-library/example-service/`): the FACTS about zeta that the adapter's numbers rest on, one test per row of the facts table (the probe-to-test step
of `pricebt-discover-the-library`): each asserts the FACT (a sign, a ratio, an error code), never a market number, on a market whose answer is known. A zeta upgrade that changes a convention fails a
NAMED test here instead of silently moving every number. The probe they come from is `probes/p1_units_signs.py` of the example. Needs no pricing library (zeta is fictional): marker `core`."""
import datetime as dt
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "skills" / "pricebt-wire-external-library" / "example-service"
for p in (str(EXAMPLE), str(EXAMPLE / "zeta_lib"), str(EXAMPLE / "probes")):  # not on pytest.ini's pythonpath: from this file's location, never from the working directory
    if p not in sys.path:
        sys.path.insert(0, p)

import zeta  # noqa: E402  (the fictional service client: a wrong path fails loudly here instead of skipping every test)

pytestmark = pytest.mark.core

import p1_units_signs as P  # noqa: E402  (the probe's own helpers: the snapshot -> market conversion, price(), trade())
from pricebt.pricable import MarkContext  # noqa: E402
from pricebt.snapshot import SnapshotPricer  # noqa: E402
from pricebt.testing import refstack as R  # noqa: E402
from pricebt.testing.layer_conformance import flat_swap_world  # noqa: E402

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
T0 = dt.date(2024, 3, 4)  # a Monday
ALL = ["NPV", "RISK_10BP", "CONVEXITY_10BP", "PAR_BP", "LADDER_10BP"]


@pytest.fixture()
def svc():
    c = zeta.Client()
    snap = flat_swap_world("cal", holidays=HOL)(T0, 0.0)
    mid = c.upload_market(P.market_of(snap))
    yield c, mid, snap
    c.close()


def price(svc, trades, measures=ALL, as_of=T0, mid=None, scenario=None):
    c, m, _ = svc
    return P.price(c, mid or m, as_of.isoformat(), trades, measures, scenario)


def test_fact_a_par_swap_is_worth_zero_and_a_mirror_trade_is_the_exact_opposite(svc):
    (par,) = price(svc, [P.trade("PAY", rate="PAR")])
    assert par["measures"]["NPV"] == pytest.approx(0.0, abs=1e-6)
    k = par["resolved"]["fixed_rate_bp"] + 25.0
    a, b = price(svc, [P.trade("PAY", rate=k), P.trade("RECEIVE", rate=k)], ["NPV", "RISK_10BP", "CONVEXITY_10BP"])
    for m in ("NPV", "RISK_10BP", "CONVEXITY_10BP"):
        assert a["measures"][m] == pytest.approx(-b["measures"][m], abs=1e-9), m


def test_fact_zeta_is_holder_signed_a_payer_above_par_loses_and_has_positive_risk(svc):
    (par,) = price(svc, [P.trade("PAY", rate="PAR")])
    (a,) = price(svc, [P.trade("PAY", rate=par["resolved"]["fixed_rate_bp"] + 25.0)])
    assert a["measures"]["NPV"] < 0 and a["measures"]["RISK_10BP"] > 0


def test_fact_the_ladder_has_the_OPPOSITE_sign_to_risk_10bp_and_sums_to_minus_it(svc):
    (a,) = price(svc, [P.trade("PAY", rate="PAR")])
    lad = a["measures"]["LADDER_10BP"]
    assert [x["pillar_months"] for x in lad] == [1, 3, 6, 12, 24, 36, 60, 84, 120, 180, 240, 360], "twelve pillars, always, in this order"
    main = max(lad, key=lambda x: abs(x["risk"]))
    assert main["pillar_months"] == 60 and main["risk"] < 0, "a payer's 5Y swap at par: its whole risk is in the 5Y pillar and NEGATIVE (small entries next to it can have either sign)"
    assert sum(x["risk"] for x in lad) / a["measures"]["RISK_10BP"] == pytest.approx(-1.0, abs=1e-5)


def test_fact_units_par_in_bp_notional_in_millions_risk_per_10bp(svc):
    (a,) = price(svc, [P.trade("PAY", 10.0, rate="PAR")])
    assert 380.0 < a["measures"]["PAR_BP"] < 420.0, "basis points, not percent and not decimal"
    lo, hi = price(svc, [P.trade("PAY", 1.0, rate=450.0), P.trade("PAY", 10.0, rate=450.0)], ["NPV", "RISK_10BP"])
    assert hi["measures"]["NPV"] / lo["measures"]["NPV"] == pytest.approx(10.0, rel=1e-12) and hi["measures"]["RISK_10BP"] / lo["measures"]["RISK_10BP"] == pytest.approx(10.0, rel=1e-12)
    k0, k1 = price(svc, [P.trade("PAY", 10.0, rate=a["resolved"]["fixed_rate_bp"]), P.trade("PAY", 10.0, rate=a["resolved"]["fixed_rate_bp"] - 1.0)], ["NPV"])
    annuity_per_bp = k1["measures"]["NPV"] - k0["measures"]["NPV"]
    assert a["measures"]["RISK_10BP"] / 10.0 == pytest.approx(annuity_per_bp, rel=1e-5), "risk per 10bp / 10 = the fixed leg's PV01 for a spot-start swap at par"


def test_fact_the_fixed_leg_is_linear_in_the_strike_so_the_annuity_by_a_strike_bump_is_exact(svc):
    ks = [300.0, 350.0, 400.0]
    v = [r["measures"]["NPV"] for r in price(svc, [P.trade("PAY", 10.0, rate=k) for k in ks], ["NPV"])]
    assert (v[0] - v[1]) == pytest.approx(v[1] - v[2], rel=1e-12)


def test_fact_the_scenario_moves_ZERO_rates_and_risk_10bp_moves_the_PAR_curve_so_they_differ_by_percents(svc):
    (a,) = price(svc, [P.trade("PAY", 10.0, rate="PAR")])
    k = a["resolved"]["fixed_rate_bp"]
    (s,) = price(svc, [P.trade("PAY", 10.0, rate=k)], ["NPV"], scenario={"parallel_bp": 1.0})  # a NUMERIC strike: "PAR" would be re-resolved on the shocked market and the shock would price to 0
    (par_again,) = price(svc, [P.trade("PAY", 10.0, rate="PAR")], ["NPV"], scenario={"parallel_bp": 1.0})
    assert par_again["measures"]["NPV"] == pytest.approx(0.0, abs=1e-6), "fact: 'PAR' means the par rate as of the request, ON THE SHOCKED market too: the adapter always sends a number"
    ratio = s["measures"]["NPV"] / (a["measures"]["RISK_10BP"] / 10.0)
    assert 1.01 < ratio < 1.10, ratio


def test_fact_scenario_takes_exactly_one_key_and_only_parallel_or_roll(svc):
    c, m, _ = svc
    r = c.price({"market_id": m, "as_of": T0.isoformat(), "scenario": {"parallel_bp": 1.0, "roll": "1M"}, "trades": [P.trade("PAY")], "measures": ["NPV"]})
    assert r["status"] == "ERROR" and r["code"] == "Z101", "the layers along the realised (non-parallel) move therefore need markets of their own"


def test_fact_a_service_error_is_an_error_dict_never_an_exception(svc):
    c, m, _ = svc
    r = c.price({"market_id": m, "as_of": T0.isoformat(), "trades": [P.trade("pay")], "measures": ["NPV"]})
    assert r["status"] == "ERROR" and r["code"] == "Z101" and "upper case" in r["message"]
    assert c.price({"market_id": "mkt-9999", "as_of": T0.isoformat(), "trades": [P.trade("PAY")], "measures": ["NPV"]})["code"] == "Z412"


def test_fact_dates_are_iso_strings_only_and_as_of_may_not_precede_the_market(svc):
    c, m, _ = svc
    assert c.price({"market_id": m, "as_of": T0, "trades": [P.trade("PAY")], "measures": ["NPV"]})["code"] == "Z101"
    assert c.price({"market_id": m, "as_of": (T0 - dt.timedelta(days=1)).isoformat(), "trades": [P.trade("PAY")], "measures": ["NPV"]})["code"] == "Z101"


def test_fact_spot_lag_and_dates_roll_modified_following_on_the_markets_holidays(svc):
    # 2024-03-27 (Wed): spot_lag 2 = Fri 03-29 is a holiday (Good Friday in this world) -> Mon 04-01; a tenor is measured from the ADJUSTED start
    c, _, snap = svc
    d = dt.date(2024, 3, 27)
    mid = c.upload_market(P.market_of(flat_swap_world("cal", holidays=HOL)(d, 0.0)))
    (r,) = P.price(c, mid, d.isoformat(), [P.trade("PAY", 1.0, end={"tenor_years": 1})], ["NPV"])
    assert r["resolved"]["start"] == "2024-04-01" and r["resolved"]["end"] == "2025-04-01"
    (r2,) = P.price(c, mid, d.isoformat(), [P.trade("PAY", 1.0, start="2024-03-29", end={"tenor_years": 1})], ["NPV"])
    assert r2["resolved"]["start"] == "2024-03-28", "an explicit start on a holiday is adjusted MODIFIED following: the next business day (04-01) is in the next month, so the PREVIOUS one; spot_lag counts business days instead"


def test_fact_a_flow_paid_on_as_of_is_not_in_npv_it_is_paid_on_as_of_and_cash_to_date_covers_asof_exclusive_to_as_of_inclusive(svc):
    # a swap started 2023-06-12 (a Monday) pays its first coupon 2 business days after 2024-06-12; price it AS OF that payment date on a market of that date
    c, _, _ = svc
    d = dt.date(2024, 6, 14)
    mid = c.upload_market(P.market_of(flat_swap_world("cal", holidays=HOL)(d, 0.0)))
    (r,) = P.price(c, mid, d.isoformat(), [P.trade("PAY", 10.0, start="2023-06-12", rate=350.0)], ["NPV", "CASH_TO_DATE"])
    assert r["paid_on_as_of"] != 0.0 and r["measures"]["CASH_TO_DATE"] == 0.0, "as_of == asof: nothing 'to date', the flow is reported apart"
    d0 = dt.date(2024, 6, 13)
    mid0 = c.upload_market(P.market_of(flat_swap_world("cal", holidays=HOL)(d0, 0.0)))
    (r0,) = P.price(c, mid0, d.isoformat(), [P.trade("PAY", 10.0, start="2023-06-12", rate=350.0)], ["NPV", "CASH_TO_DATE"])
    assert r0["paid_on_as_of"] == pytest.approx(r["paid_on_as_of"], rel=1e-6) and r0["measures"]["CASH_TO_DATE"] == pytest.approx(r0["paid_on_as_of"], rel=1e-12), "as_of > asof: the flow paid ON as_of is in CASH_TO_DATE too"


def test_fact_fixings_dated_on_or_after_asof_are_used_when_present_and_implied_by_the_curve_when_absent(svc):
    c, _, snap = svc
    m = P.market_of(snap)
    tr = P.trade("PAY", 10.0, start="2024-02-20", rate=400.0)
    later = (T0 + dt.timedelta(days=3)).isoformat()  # Thursday: the fixing dated Monday T0 and Tuesday are in the elapsed period
    (implied,) = price(svc, [tr], ["NPV"], as_of=T0 + dt.timedelta(days=3))
    m["fixings"]["SOFR"][T0.isoformat()] = 1000.0  # a realised fixing dated ON asof: 10%, absurd on purpose
    mid = c.upload_market(m)
    (realised,) = price(svc, [tr], ["NPV"], as_of=T0 + dt.timedelta(days=3), mid=mid)
    assert realised["measures"]["NPV"] != implied["measures"]["NPV"], f"an entry dated on asof is USED at {later}: the layers' roll needs the t1 fixings in a market of its own"


def test_fact_zeta_wants_a_fixing_for_every_business_day_from_the_start_even_after_the_swap_has_ended(svc):
    c, _, snap = svc
    m = P.market_of(snap)
    del m["fixings"]["SOFR"]["2022-03-15"]  # a hole in the history (a Tuesday)
    mid = c.upload_market(m)
    ended = P.trade("PAY", 1.0, start="2022-12-01", end="2023-06-01", rate=400.0)  # ended long before asof
    r = c.price({"market_id": mid, "as_of": T0.isoformat(), "trades": [ended], "measures": ["NPV"]})
    assert r["status"] == "OK", "the hole is BEFORE this swap's start: it needs no fixing of a date it never accrued"
    started_before_the_hole = P.trade("PAY", 1.0, start="2021-12-01", end="2022-06-01", rate=400.0)  # ended two years before asof
    r = c.price({"market_id": mid, "as_of": T0.isoformat(), "trades": [started_before_the_hole], "measures": ["NPV"]})
    assert r["status"] == "ERROR" and r["code"] == "Z530" and "2022-03-15" in r["message"], "an ENDED swap still needs every fixing from its start to the day before as_of"


def test_fact_the_reference_stack_agrees_with_zeta_on_value_par_and_annuity_and_on_risk_only_on_the_same_pillars(svc):
    """The pillar basis, as a FACT: the same short started swap on the same snapshot, the reference on zeta's twelve pillars and on its default eleven."""
    c, m, snap = svc
    d = dt.date(2024, 3, 18)
    world = flat_swap_world("cal", holidays=HOL)(d, 0.0)
    mid = c.upload_market(P.market_of(world))
    sp = SnapshotPricer(world)
    p = R.wrap(sp)
    sw = R.swap_factory(p, p.ts, terms={"side": "pay", "direction": 1, "effective": dt.date(2024, 3, 4), "maturity": dt.date(2024, 4, 4), "notional": 1e7, "fixed_rate": 4.0}, conventions={**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}).obj
    (z,) = P.price(c, mid, d.isoformat(), [P.trade("PAY", 10.0, start="2024-03-04", end="2024-04-04", rate=400.0)], ALL)
    ctx = MarkContext.standalone(p)
    twelve = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]
    zeta_dv01 = -sum(x["risk"] for x in z["measures"]["LADDER_10BP"]) / 10.0
    assert zeta_dv01 == pytest.approx(sw.dv01(ctx=ctx, tenors=twelve), rel=1e-9), "same pillars: the same number"
    assert abs(zeta_dv01 / sw.dv01(ctx=ctx) - 1) > 3e-3, "the default eleven pillars (no 1M): a different measure for a swap whose flows sit inside the first three months"
    assert z["measures"]["NPV"] + z["paid_on_as_of"] == pytest.approx(sw.value(ctx=ctx).pv, abs=1e-6)


def test_fact_the_simulated_cost_is_120ms_per_upload_and_25ms_plus_3ms_per_trade_per_request(svc):
    c, mid, _ = svc
    before = dict(c.stats)
    price(svc, [P.trade("PAY"), P.trade("RECEIVE"), P.trade("PAY", rate=300.0)], ["NPV"])
    after = c.stats
    assert after["latency_ms"] - before["latency_ms"] == 25 + 3 * 3 and after["requests"] - before["requests"] == 1 and before["latency_ms"] == 120
    assert math.isfinite(after["latency_ms"])
