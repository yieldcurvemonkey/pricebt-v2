import sys
sys.path.insert(0, "tools/ql_evidence")
from p1_started import *
from p1_bond import REF, rl_bond, ql_settings
from qlbond import *

# (a) 4th-order FD of the QuantLib price vs rateslib analytic risk / convexity
rng = np.random.default_rng(1)
w_r = w_c = 0.0
for i in rng.permutation(len(REF))[:60]:
    r = REF.iloc[i]
    issue, mat, cpn = r.issue_date, r.maturity_date, float(r.cpn)
    if issue < dt.date(2012, 1, 5) or mat > dt.date(2060, 1, 1) or cpn <= 0 or mat < dt.date(2016, 1, 1):
        continue
    rb = rl_bond(issue, mat, cpn); qb, dc = make_bond(issue, mat, cpn)
    s = max(issue, dt.date(2013, 1, 7)) + dt.timedelta(days=200)
    if not (issue < s < mat - dt.timedelta(days=400)):
        continue
    y = 3.7
    with ql_settings(s):
        P = lambda yy: dirty_from_yield(qb, dc, yy, s)
        h = 0.005
        risk = -(8 * (P(y + h) - P(y - h)) - (P(y + 2 * h) - P(y - 2 * h))) / (12 * h)
        hc = 0.01
        cvx = (-P(y + 2 * hc) + 16 * P(y + hc) - 30 * P(y) + 16 * P(y - hc) - P(y - 2 * hc)) / (12 * hc**2)
    rr = float(rb.duration(y, fdt(s), "risk")); rc = float(rb.convexity(y, fdt(s), "risk"))
    w_r = max(w_r, abs(risk - rr) / abs(rr)); w_c = max(w_c, abs(cvx - rc) / abs(rc))
print(f"(a) 4th-order FD (QL) vs rateslib analytic: risk rel {w_r:.2e}, convexity rel {w_c:.2e}")

# (b) telescopic value dates on a STARTED swap
ref = dt.date(2024, 6, 12); eff = dt.date(2023, 9, 6)
nodes = list(zip(*load_row(ref)))
cal = ql_calendar(load_cal("nyc"))
out = {}
for tel in (False, True):
    with ql_state(ref):
        h = ql.YieldTermStructureHandle(ql_curve(nodes)); idx = make_index(cal, h); ql_install(idx, ref, eff)
        sw = make_ois2(eff, "5Y", 1, 1e7, 3.6, cal, idx, telescopic=tel); sw.setPricingEngine(ql.DiscountingSwapEngine(h, True)); out[tel] = sw.NPV()
        del sw, h, idx
print(f"(b) started 5Y payer NPV telescopic False {out[False]:.4f} True {out[True]:.4f}")

# (c) missing fixing error text; (d) a stored fixing dated == today is used
with ql_state(ref):
    h = ql.YieldTermStructureHandle(ql_curve(nodes)); idx = make_index(cal, h, "PBTMISS")
    ql.IndexManager.instance().clearHistory(idx.name())
    ds = [d for d, _ in FX if eff <= d < ref and d != dt.date(2024, 1, 3)]
    idx.addFixings([qd(d) for d in ds], [FXD[d] for d in ds], True)
    sw = make_ois2(eff, "5Y", 1, 1e7, 3.6, cal, idx); sw.setPricingEngine(ql.DiscountingSwapEngine(h, True))
    try:
        sw.NPV(); print("(c) no error")
    except RuntimeError as e:
        print("(c)", str(e)[:200].replace("\n", " "))
    del sw
    ql.IndexManager.instance().clearHistory(idx.name())
    ds = [d for d, _ in FX if eff <= d < ref]
    idx.addFixings([qd(d) for d in ds], [FXD[d] for d in ds], True)
    sw = make_ois2(eff, "5Y", 1, 1e7, 3.6, cal, idx); sw.setPricingEngine(ql.DiscountingSwapEngine(h, True)); v0 = sw.NPV()
    idx.addFixings([qd(ref)], [0.10], True)  # absurd fixing for today
    sw2 = make_ois2(eff, "5Y", 1, 1e7, 3.6, cal, idx); sw2.setPricingEngine(ql.DiscountingSwapEngine(h, True)); v1 = sw2.NPV()
    print(f"(d) NPV without today's fixing {v0:.2f}; with an absurd stored fixing for today {v1:.2f} (a stored fixing dated == today IS used)")
    ql.IndexManager.instance().clearHistory(idx.name())
    del sw, sw2, h, idx
