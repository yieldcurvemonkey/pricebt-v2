import sys
sys.path.insert(0, "tools/ql_evidence")
from p1_ladder import *
ref = dt.date(2024, 2, 27)
assert load_row(ref) is not None
spot = pd_(cal.advance(qd(ref), 2, ql.Days)); print("spot", spot)
for tenors in (T11, T4):
    worst = 0
    for ten in ("2Y", "5Y", "10Y", "30Y", "4Y"):
        rlv, qlv, scale, res, terms, nd = compare(ref, tenors, "custom", (spot, ten, 1, 1e7, 3.9))
        worst = max(worst, float(np.abs(qlv - rlv).max() / scale))
    print(len(tenors), "pillars, Feb-29 spot: worst relative bucket diff", f"{worst:.3e}")
