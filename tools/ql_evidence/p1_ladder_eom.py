import sys
sys.path.insert(0, "tools/ql_evidence")
import qlladder
from p1_ladder import *
ref = dt.date(2025, 2, 26)
spot = pd_(cal.advance(qd(ref), 2, ql.Days)); print("spot", spot, "isEndOfMonth(spot):", cal.isEndOfMonth(qd(spot)))
for eom in (None, False):
    qlladder.EOM = eom
    if eom is None:
        # the original default: do not pass the argument
        import types
    for tenors in (T11,):
        worst = 0; wt=None
        for ten in ("2Y", "3Y", "4Y", "5Y", "7Y", "10Y", "12Y", "30Y"):
            rlv, qlv, scale, res, terms, nd = compare(ref, tenors, "custom", (spot, ten, 1, 1e7, 3.9))
            r = float(np.abs(qlv - rlv).max() / scale)
            if r > worst: worst, wt = r, ten
        print("endOfMonth =", eom, ": worst relative bucket diff", f"{worst:.3e}", "at", wt)
