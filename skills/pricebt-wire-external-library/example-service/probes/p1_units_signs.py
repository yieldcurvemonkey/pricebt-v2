"""p1_units_signs.py: hypothesis -> probe -> number, for the zeta service (docs: ZETA_DOCS.md). Run from the repository root:
    PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example-service/zeta_lib" python skills/pricebt-wire-external-library/example-service/probes/p1_units_signs.py
(this probe imports the library itself, so it needs the library's own directory on PYTHONPATH; the tests import its helpers `market_of`, `price`, `trade`)
The control runs first: a swap struck at PAR is worth 0 (and a mirror trade is the exact opposite). Only the CONTROL is a gate."""
import datetime as dt
import math
import sys

import zeta  # LIB
from pricebt.testing.layer_conformance import flat_swap_world

ROWS = []


def fact(topic, hypothesis, got, expected=None, tol=0.0, note=""):
    ok = True if expected is None else abs(got - expected) <= tol
    ROWS.append((topic, hypothesis, got, expected, ok, note))
    print(f"{'ok ' if ok else 'BAD'} {topic:<12} {hypothesis:<64} got {got:>16.10g}" + ("" if expected is None else f"  expected {expected:.10g}") + (f"   {note}" if note else ""))


def market_of(snap, curve="curve", fixings="fixings"):
    """Snapshot -> zeta market (the conversion the wrap will do): DF at node dates -> continuously compounded zero rates in bp on ACT/365."""
    c = snap.curves[curve]
    ref = c.reference_date
    nodes = [[(d - ref).days, -math.log(v) * 365.0 / (d - ref).days * 1e4] for d, v in zip(c.node_dates[1:], c.values[1:])]
    f = snap.fixings[fixings]
    cal = next(iter(snap.calendars.values()))
    return {"asof": ref.isoformat(), "calendar": {"name": cal.name, "holidays": [h.isoformat() for h in cal.holidays]},
            "curve": {"name": "SOFR", "day_count": "ACT/365", "nodes": nodes},
            "fixings": {"SOFR": {d.isoformat(): v * 100.0 for d, v in zip(f.dates, f.values)}}}  # percent -> bp


def price(c, mid, as_of, trades, measures, scenario=None):
    req = {"market_id": mid, "as_of": as_of, "trades": trades, "measures": measures}
    if scenario:
        req["scenario"] = scenario
    r = c.price(req)
    assert r["status"] == "OK", r
    return r["results"]


def trade(leg="PAY", mm=10.0, end=None, rate="PAR", start=None):
    return {"product": "OIS", "leg_fixed": leg, "notional_mm": mm, "start": start or {"spot_lag": 2}, "end": end or {"tenor_years": 5}, "fixed_rate_bp": rate}


def main():
    world = flat_swap_world("cal")
    t0 = dt.date(2024, 3, 4)
    snap = world(t0, 0.0)
    c = zeta.Client()
    mid = c.upload_market(market_of(snap))
    asof = t0.isoformat()
    M = ["NPV", "RISK_10BP", "CONVEXITY_10BP", "PAR_BP", "LADDER_10BP"]
    # ---- CONTROL: a swap struck at PAR is worth 0; the mirror trade is the exact opposite
    (rp,) = price(c, mid, asof, [trade("PAY", rate="PAR")], M)
    fact("control", "a PAR payer is worth 0", rp["measures"]["NPV"], 0.0, tol=1e-6)
    par = rp["resolved"]["fixed_rate_bp"]
    (a, b) = price(c, mid, asof, [trade("PAY", rate=par + 25.0), trade("RECEIVE", rate=par + 25.0)], ["NPV", "RISK_10BP"])
    fact("control", "payer + receiver at the same strike sum to 0 (NPV)", a["measures"]["NPV"] + b["measures"]["NPV"], 0.0, tol=1e-6)
    fact("control", "payer + receiver at the same strike sum to 0 (RISK_10BP)", a["measures"]["RISK_10BP"] + b["measures"]["RISK_10BP"], 0.0, tol=1e-6)
    if not all(r[4] for r in ROWS):
        sys.exit("the control failed: fix the probe before believing any number")
    print("control ok")
    # ---- signs
    fact("sign", "H1 a PAYER above par (strike par+25bp) has negative NPV (pays too much)", 1.0 if a["measures"]["NPV"] < 0 else -1.0, 1.0, note="+1: payer-signed like pricebt")
    fact("sign", "H2 a payer's RISK_10BP is positive", 1.0 if rp["measures"]["RISK_10BP"] > 0 else -1.0, 1.0, note="+1: payer-positive like pricebt's dv01")
    fact("sign", "H3 payer's LADDER_10BP main entry is NEGATIVE (opposite to RISK_10BP)", 1.0 if min(x["risk"] for x in rp["measures"]["LADDER_10BP"]) < 0 else -1.0, 1.0, note="+1: pricebt's ladder needs sign -1")
    lad = sum(x["risk"] for x in rp["measures"]["LADDER_10BP"])
    fact("sign", "H3b sum(LADDER_10BP) / RISK_10BP", lad / rp["measures"]["RISK_10BP"], -1.0, tol=1e-5)
    # ---- units
    fact("unit", "H4 PAR_BP is basis points: par 5Y on a flat 4% ACT/365 curve is ~ 4.03-4.1% -> bp", par, 405.0, tol=15.0, note="/100 for pricebt's percent")
    (a1, a10) = price(c, mid, asof, [trade("PAY", 1.0, rate=par + 25.0), trade("PAY", 10.0, rate=par + 25.0)], ["NPV", "RISK_10BP"])
    fact("unit", "H5 notional_mm: 10x notional -> 10x NPV", a10["measures"]["NPV"] / a1["measures"]["NPV"], 10.0, tol=1e-9, note="notional / 1e6 in the factory")
    fact("unit", "H5b RISK_10BP scales with notional (per trade as built)", a10["measures"]["RISK_10BP"] / a1["measures"]["RISK_10BP"], 10.0, tol=1e-9)
    # strike bump: lowering the strike by 1bp on a payer raises NPV by exactly the fixed leg PV01 (a known answer that pins the annuity)
    (k0, k1) = price(c, mid, asof, [trade("PAY", 10.0, rate=par), trade("PAY", 10.0, rate=par - 1.0)], ["NPV"])
    ann = k1["measures"]["NPV"] - k0["measures"]["NPV"]
    fact("unit", "H6 RISK_10BP/10 vs the fixed-leg PV01 (strike bump) of an unstarted par payer", rp["measures"]["RISK_10BP"] / 10.0 / (ann * 10.0 / 10.0), 1.0, tol=5e-3, note="par-curve risk ~ annuity at par (1e-4..1e-3)")
    fact("unit", "H6b annuity per bp of the 10mm 5Y payer (strike bump)", ann, note="the analytic annuity: what the reference answers for an unstarted swap")
    # ---- gamma
    fact("unit", "H7 CONVEXITY_10BP / 100 = gamma per bp^2 (second difference for +-10bp = gamma * 100)", rp["measures"]["CONVEXITY_10BP"] / 100.0, note="the reference's gamma is compared in the swap tests")
    # ---- scenario: zero-rate shock vs par-curve risk
    (s1,) = price(c, mid, asof, [trade("PAY", 10.0, rate=par)], ["NPV"], {"parallel_bp": 1.0})
    fact("variable", "H8 NPV(parallel_bp=+1) - NPV vs RISK_10BP/10 (zero space vs par space)", s1["measures"]["NPV"] / (rp["measures"]["RISK_10BP"] / 10.0), note="docs: about 1.05 for a 5Y: two different variables")
    # ---- dates and fixings
    (o,) = price(c, mid, asof, [trade("PAY", 10.0, rate=par)], ["NPV"])
    fact("dates", "H9 spot_lag 2 on 2024-03-04 (Mon) resolves to 2024-03-06", 1.0 if o["resolved"]["start"] == "2024-03-06" else 0.0, 1.0, note=f"start {o['resolved']['start']} end {o['resolved']['end']}")
    r = c.price({"market_id": mid, "as_of": asof, "trades": [trade("PAY", 10.0, start="2024-02-20", rate=400.0)], "measures": ["NPV"]})
    fact("fixings", "H10 a swap started before the market asof needs fixings: present -> OK", 1.0 if r["status"] == "OK" else 0.0, 1.0, note=str(r.get("message", ""))[:60])
    m2 = market_of(snap)
    m2["fixings"] = {"SOFR": {}}
    mid2 = c.upload_market(m2)
    r = c.price({"market_id": mid2, "as_of": asof, "trades": [trade("PAY", 10.0, start="2024-02-20", rate=400.0)], "measures": ["NPV"]})
    fact("fixings", "H11 the same without fixings answers Z530 (an ERROR dict, not an exception)", 1.0 if r.get("code") == "Z530" else 0.0, 1.0, note=r.get("message", "")[:60])
    # ---- statistics
    fact("cost", "H12 stats.markets_uploaded counts every upload_market call", c.stats["markets_uploaded"], 2.0, note=str(c.stats))
    c.close()


if __name__ == "__main__":
    main()
