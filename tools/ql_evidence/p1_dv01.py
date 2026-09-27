"""Phase 1 item 4: DV01. Fresh swaps: rateslib analytic PV01 vs QL -fixedLegBPS vs ladder sums vs parallel par bump. Aged swaps: market dv01 = ladder sum."""
import sys
sys.path.insert(0, "tools/ql_evidence")
from p1_ladder import *

refs = sample_dates(dt.date(2019, 9, 2), dt.date(2026, 9, 4), n=int(sys.argv[1]) if len(sys.argv) > 1 else 12, seed=5)
rows = []
for ref in refs:
    nodes = list(zip(*load_row(ref)))
    spot = pd_(cal.advance(qd(ref), 2, ql.Days))
    curve, solver, dense, pars, terms = rl_build_ladder(nodes, rcal, T11)
    positions = [(spot, t, 1, 1e7, "par") for t in ("2Y", "5Y", "10Y", "30Y")] + [(spot, t, 1, 1e7, 3.9) for t in ("5Y", "30Y")]
    for back, ten in ((250, "5Y"), (900, "10Y"), (500, "3Y"), (400, "2Y")):
        e = ref - dt.timedelta(days=back)
        while not cal.isBusinessDay(qd(e)):
            e += dt.timedelta(days=1)
        if e >= dt.date(2018, 4, 3):
            positions.append((e, ten, 1, 1e7, 3.6))
    with ql_state(ref):
        lad = QLLadder(nodes, cal, T11)
        for eff, ten, sign, notional, rate in positions:
            started = eff < ref
            if rate == "par":
                rate = float(rl.IRS(effective=fdt(eff), termination=ten.lower(), spec="usd_irs", notional=1e6, calendar=rcal).rate(curves=rl.Curve(nodes={fdt(d): v for d, v in nodes}, id="c", convention="act360", calendar=rcal, modifier="MF", interpolation="log_linear")))
                kind_ = "fresh_par"
            else:
                kind_ = "aged" if started else "fresh_offpar"
            if started:
                rl_install(ref, eff)
            kwf = {"leg2_rate_fixings": "PBT_SOFR"} if started else {}
            irs = rl.IRS(effective=fdt(eff), termination=ten.lower(), spec="usd_irs", notional=sign * notional, fixed_rate=rate, calendar=rcal, **kwf)
            rc = rl.Curve(nodes={fdt(d): v for d, v in nodes}, id="c", convention="act360", calendar=rcal, modifier="MF", interpolation="log_linear")
            r_an = float(irs.analytic_delta(curves=rc, leg=1))
            r_lad = float(rl_ladder_of(irs, curve, solver, T11).sum())

            def mk(h, eff=eff, ten=ten, sign=sign, notional=notional, rate=rate, started=started):
                idx = make_index(cal, h)
                if started:
                    ql_install(idx, ref, eff)
                sw = make_ois2(eff, ten, sign, notional, rate, cal, idx)
                sw.setPricingEngine(ql.DiscountingSwapEngine(h, True))
                return sw

            h0 = ql.YieldTermStructureHandle(ql_curve(nodes))
            sw0 = mk(h0)
            q_an = -sw0.fixedLegBPS()
            q_lad = float(sum(lad.ladder(mk)))
            q_par = lad.parallel(mk)
            rows.append(dict(ref=ref, eff=eff, ten=ten, kind=kind_, r_an=r_an, q_an=q_an, r_lad=r_lad, q_lad=q_lad, q_par=q_par))
            del sw0, h0, mk
df = pd.DataFrame(rows)
__import__("os").makedirs("results_new/ql_evidence", exist_ok=True)
df.to_csv("results_new/ql_evidence/p1_dv01.csv", index=False)
for kind, g in df.groupby("kind"):
    rel = lambda a, b: float(((g[a] - g[b]).abs() / g[b].abs()).max())
    print(f"{kind:5s} n={len(g):3d} | QL analytic vs rl analytic {rel('q_an','r_an'):.2e} | QL ladder-sum vs rl ladder-sum {rel('q_lad','r_lad'):.2e} | QL parallel bump vs QL ladder-sum {rel('q_par','q_lad'):.2e}"
          f" | rl analytic vs rl ladder {rel('r_an','r_lad'):.2e} | overstatement analytic/ladder max {float((g.r_an/g.r_lad).max()):.3f}")
