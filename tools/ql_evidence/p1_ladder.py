"""Phase 1 item 5: par-swap pillar ladder, QuantLib vs rateslib on real curves, fresh and aged swaps, 4-pillar and 11-pillar sets, both pillar-date choices."""
import sys

sys.path.insert(0, "tools/ql_evidence")
from rlladder import *  # noqa
from qlswap import *  # noqa
from qlladder import QLLadder
from p1_started import rl_install, ql_install

hol = load_cal("nyc")
cal = ql_calendar(hol)
rcal = rl.Cal(holidays=[fdt(h) for h in hol], week_mask=[5, 6])
T11 = ["3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]
T4 = ["2Y", "5Y", "10Y", "30Y"]

# ------------------------------------------------ control (known answer): a pillar par swap has its whole risk in its own bucket
ref = dt.date(2024, 6, 12)
nodes = list(zip(*load_row(ref)))
with ql_state(ref):
    lad = QLLadder(nodes, cal, T11)
    pil = lad.ladder(lambda h: (lambda s: (s.setPricingEngine(ql.DiscountingSwapEngine(h, True)), s)[1])(make_ois2(lad.spot, "5Y", 1, 1e7, lad.pars[T11.index("5Y")] * 100, cal, make_index(cal, h))))
    assert abs(pil[T11.index("5Y")]) > 1000 and max(abs(x) for i, x in enumerate(pil) if i != T11.index("5Y")) < 1e-3 * abs(pil[T11.index("5Y")]), pil
print("control ok: 5Y pillar swap sits in the 5Y bucket only; bucket:", round(pil[T11.index('5Y')], 2), "; others max", max(abs(x) for i, x in enumerate(pil) if i != T11.index("5Y")))

results = []


def compare(ref, tenors, pillar, position):
    """position: (eff, tenor, sign, notional, rate_pct) ; fixings installed for started swaps."""
    eff, ten, sign, notional, rate = position
    nodes = list(zip(*load_row(ref)))
    started = eff < ref
    curve, solver, dense, pars, terms = rl_build_ladder(nodes, rcal, tenors)
    if started:
        rl_install(ref, eff)
    kwf = {"leg2_rate_fixings": "PBT_SOFR"} if started else {}
    irs = rl.IRS(effective=fdt(eff), termination=ten.lower(), spec="usd_irs", notional=sign * notional, fixed_rate=rate, calendar=rcal, **kwf)
    rlad = rl_ladder_of(irs, curve, solver, tenors)
    with ql_state(ref):
        lad = QLLadder(nodes, cal, tenors, pillar=pillar)

        def mk(h):
            idx = make_index(cal, h)
            if started:
                ql_install(idx, ref, eff)
            sw = make_ois2(eff, ten, sign, notional, rate, cal, idx)
            sw.setPricingEngine(ql.DiscountingSwapEngine(h, True))
            return sw

        qlad = np.array(lad.ladder(mk))
        nd = lad.node_dates()
        del mk
        res = lad.resid
    rlv = rlad.values
    scale = np.abs(rlv).max()
    return rlv, qlad, scale, res, [t.date() for t in terms], nd[1:]


if __name__ == "__main__":
    refs = sample_dates(dt.date(2019, 9, 2), dt.date(2026, 9, 4), n=int(sys.argv[1]) if len(sys.argv) > 1 else 10, seed=21)
    for pillar in ("custom", "last"):
        for tenors, tname in ((T11, "11"), (T4, "4")):
            worst_rel_max, worst_sum, worst_res, node_mis = 0.0, 0.0, 0.0, 0
            worst_case = None
            n = 0
            for ref in refs:
                spot = pd_(cal.advance(qd(ref), 2, ql.Days))
                fresh = [(spot, t, 1, 1e7, 3.9) for t in ("2Y", "5Y", "10Y", "30Y", "4Y")]
                aged = []
                for back, ten in ((250, "5Y"), (900, "10Y"), (500, "3Y")):
                    e = ref - dt.timedelta(days=back)
                    while not cal.isBusinessDay(qd(e)):
                        e += dt.timedelta(days=1)
                    if e >= dt.date(2018, 4, 3):
                        aged.append((e, ten, -1, 2e7, 3.6))
                for pos in fresh + aged:
                    rlv, qlv, scale, res, terms, nd = compare(ref, tenors, pillar, pos)
                    n += 1
                    rel = float(np.abs(qlv - rlv).max() / scale)
                    srel = abs(qlv.sum() - rlv.sum()) / max(abs(rlv.sum()), 1.0)
                    worst_res = max(worst_res, res)
                    if pillar == "custom":
                        node_mis += int(terms != nd)
                    if rel > worst_rel_max:
                        worst_rel_max, worst_case = rel, (ref, pos[1], pos[2], "started" if pos[0] < ref else "fresh")
                    worst_sum = max(worst_sum, srel)
            print(f"pillar={pillar:6s} tenors={tname:3s} positions={n:3d}  max |bucket diff|/max|bucket| = {worst_rel_max:.3e} ({worst_case}); sum-of-ladder rel diff = {worst_sum:.3e}; "
                  f"QL bootstrap residual {worst_res:.1e}bp; node-date mismatches vs rateslib terminations {node_mis}")
