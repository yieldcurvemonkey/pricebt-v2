"""dv01 of an aged swap = sum of the ladder over the bound tenors: how much does the pillar set change it? 4 pillars vs 11 pillars (scratch QL ladder, not the adapter)."""
import sys
sys.path.insert(0, "tools/ql_evidence")
from p1_ladder import *

refs = sample_dates(dt.date(2019, 9, 2), dt.date(2026, 9, 4), n=8, seed=33)
worst, rows = 0.0, []
for ref in refs:
    nodes = list(zip(*load_row(ref)))
    with ql_state(ref):
        l11, l4 = QLLadder(nodes, cal, T11), QLLadder(nodes, cal, T4)
        for back, ten in ((250, "5Y"), (900, "10Y"), (500, "3Y"), (400, "2Y"), (150, "30Y")):
            e = ref - dt.timedelta(days=back)
            while not cal.isBusinessDay(qd(e)):
                e += dt.timedelta(days=1)
            if e < dt.date(2018, 4, 3):
                continue

            def mk(h, e=e, ten=ten):
                idx = make_index(cal, h)
                ql_install(idx, ref, e)
                sw = make_ois2(e, ten, 1, 1e7, 3.6, cal, idx)
                sw.setPricingEngine(ql.DiscountingSwapEngine(h, True))
                return sw

            a, b = sum(l11.ladder(mk)), sum(l4.ladder(mk))
            rows.append(abs(a - b) / abs(a))
print(f"aged swaps {len(rows)}: |sum(ladder 4 pillars) - sum(ladder 11 pillars)| / sum(11): max {max(rows):.2e}, median {sorted(rows)[len(rows)//2]:.2e}")
