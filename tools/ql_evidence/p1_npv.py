"""Phase 1 item 2/4: at-inception swap on real fixture curves. NPV at a fixed rate, at par, par rate, leg PVs, PV01. Same holidays both sides."""
import sys
import time

sys.path.insert(0, "tools/ql_evidence")
from qlswap import *  # noqa

hol = load_cal("nyc")
cal = ql_calendar(hol)
rcal = rl_calendar(hol)
refs = sample_dates(dt.date(2018, 4, 2), dt.date(2026, 9, 4), n=40, seed=3)
tenors = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
if len(sys.argv) > 1 and sys.argv[1] == "quick":
    refs, tenors = refs[:5], ["5Y", "10Y"]

# ---- control: a flat 4% (act360 continuously... simple) curve, 1Y payer at its own par: NPV must be exactly 0 on both sides and par = OIS rate implied
d0 = dt.date(2024, 1, 4)
nodes = [(d0, 1.0), (d0 + dt.timedelta(days=400), 1.0 / (1 + 0.04 * 400 / 360)), (d0 + dt.timedelta(days=4000), 0.7)]
with ql_state(d0):
    qc = ql_curve(nodes)
    h = ql.YieldTermStructureHandle(qc)
    idx = make_index(cal, h)
    sw = make_ois2(dt.date(2024, 1, 8), "1Y", 1, 1e7, 4.0, cal, idx)
    sw.setPricingEngine(ql.DiscountingSwapEngine(h, True))
    par = sw.fairRate()
    sw2 = make_ois2(dt.date(2024, 1, 8), "1Y", 1, 1e7, par * 100, cal, idx)
    sw2.setPricingEngine(ql.DiscountingSwapEngine(h, True))
    assert abs(sw2.NPV()) < 1e-6, sw2.NPV()
print("control ok (npv at own par = 0)")

W = {k: 0.0 for k in ["npv4", "par", "fixed", "float", "pv01", "npv_par"]}
Wrel = dict(W)
n = 0
tq = tr = 0.0
for ref in refs:
    row = load_row(ref)
    nodes = list(zip(*row))
    spot = rcal.add_bus_days(fdt(ref), 2, True).date()
    rc = rl.Curve(nodes={fdt(d): v for d, v in nodes}, id="c", convention="act360", calendar=rcal, modifier="MF", interpolation="log_linear")
    with ql_state(ref):
        qc = ql_curve(nodes)
        h = ql.YieldTermStructureHandle(qc)
        idx = make_index(cal, h)
        for t in tenors:
            t0 = time.perf_counter()
            irs = rl.IRS(effective=fdt(spot), termination=t.lower(), spec="usd_irs", notional=1e7, fixed_rate=4.0, calendar=rcal)
            r_npv = float(irs.npv(curves=rc))
            r_par = float(irs.rate(curves=rc))
            r_fixed = float(irs.leg1.npv(rate_curve=rc, disc_curve=rc))
            r_float = float(irs.leg2.npv(rate_curve=rc, disc_curve=rc))
            r_pv01 = float(irs.analytic_delta(curves=rc, leg=1))
            irs_par = rl.IRS(effective=fdt(spot), termination=t.lower(), spec="usd_irs", notional=1e7, fixed_rate=r_par, calendar=rcal)
            r_npv_par = float(irs_par.npv(curves=rc))
            t1 = time.perf_counter()
            sw = make_ois2(spot, t, 1, 1e7, 4.0, cal, idx)
            sw.setPricingEngine(ql.DiscountingSwapEngine(h, True))
            q = {"npv4": sw.NPV(), "par": sw.fairRate() * 100, "fixed": sw.fixedLegNPV(), "float": sw.overnightLegNPV(), "pv01": -sw.fixedLegBPS()}
            swp = make_ois2(spot, t, 1, 1e7, q["par"], cal, idx)
            swp.setPricingEngine(ql.DiscountingSwapEngine(h, True))
            q["npv_par"] = swp.NPV()
            t2 = time.perf_counter()
            tr += t1 - t0
            tq += t2 - t1
            rr = {"npv4": r_npv, "par": r_par, "fixed": r_fixed, "float": r_float, "pv01": r_pv01, "npv_par": r_npv_par}
            n += 1
            for k in W:
                a = abs(q[k] - rr[k])
                W[k] = max(W[k], a)
                Wrel[k] = max(Wrel[k], a / max(abs(rr[k]), 1.0))
print(f"{n} swaps ({len(refs)} dates x {len(tenors)} tenors); sign convention: rateslib payer fixed leg PV {r_fixed:.2f} / QL {q['fixed']:.2f}; float {r_float:.2f} / {q['float']:.2f}")
print("max abs diff:", {k: f"{v:.3e}" for k, v in W.items()})
print("max rel diff:", {k: f"{v:.3e}" for k, v in Wrel.items()})
print(f"time per swap: rateslib {tr/n*1e3:.1f} ms (incl 2 swaps), QL {tq/n*1e3:.1f} ms")
