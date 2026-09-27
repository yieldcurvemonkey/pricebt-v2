import sys, time
sys.path.insert(0, "tools/ql_evidence")
from p1_ladder import *
ref = dt.date(2024, 6, 12)
nodes = list(zip(*load_row(ref)))
with ql_state(ref):
    t = time.perf_counter(); lad = QLLadder(nodes, cal, T11); t_build = time.perf_counter() - t
    eff = dt.date(2023, 9, 6)
    def mk(h):
        idx = make_index(cal, h); ql_install(idx, ref, eff)
        sw = make_ois2(eff, "10Y", 1, 1e7, 3.6, cal, idx); sw.setPricingEngine(ql.DiscountingSwapEngine(h, True)); return sw
    t = time.perf_counter(); l = lad.ladder(mk); t_lad = time.perf_counter() - t
    t = time.perf_counter(); lad.parallel(mk); t_par = time.perf_counter() - t
    t = time.perf_counter()
    h = ql.YieldTermStructureHandle(ql_curve(nodes)); sw = mk(h); v = sw.NPV(); t_one = time.perf_counter() - t
    del sw, h, mk
print(f"risk-curve build (11 pillars, 25 static scenario curves): {t_build*1e3:.0f} ms; ladder of a started 10Y swap (22 NPVs): {t_lad*1e3:.0f} ms; parallel bump (2 NPVs + build): {t_par*1e3:.0f} ms; one build+NPV: {t_one*1e3:.1f} ms")
