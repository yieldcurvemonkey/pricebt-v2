"""One-off: freeze the verified golden swap world (spec A2: seasoned 3y payer 50mm, coupon paid inside [2025-01-02, 2025-01-16)) as plain data for the library-free tests.

Uses rateslib ONCE, here, to build the two solver-fitted curves; the test data holds only dates and numbers (no library object)."""
import datetime as dt
import json
import sys

sys.path.insert(0, "tests")
from test_rl_common import par_curve, seeded_fixings  # noqa: E402
from pricebt.contrib.rateslib import _compat as C  # noqa: E402

D = dt.datetime
c0 = par_curve(D(2025, 1, 2), 0, 0, "c0")
c1 = par_curve(D(2025, 1, 16), 3, 15, "c1")
fx = seeded_fixings()
cal = C.calendar()
hol = sorted({h.date().isoformat() for h in cal.holidays if h.weekday() < 5 and D(2022, 12, 1) <= h <= D(2027, 12, 31)})


def nodes(c):
    return {"dates": [d.date().isoformat() for d in c.nodes.nodes.keys()], "dfs": [float(v) for v in c.nodes.nodes.values()]}


out = {
    "doc": "Golden seasoned 3y payer 50mm (fixed 4.20, effective 2024-01-08, termination 2027-01-08): curves solved by rateslib once, frozen as data. Holidays are rateslib's nyc weekday holidays 2023-2027.",
    "c0": nodes(c0), "c1": nodes(c1),
    "fixings": {"dates": [d.date().isoformat() for d in fx.index], "percent": [round(float(v), 12) for v in fx.values]},
    "holidays": hol,
}
with open("tests/data_golden_swap.json", "w", encoding="utf8") as f:
    json.dump(out, f, indent=None, separators=(",", ":"))
print(len(out["fixings"]["dates"]), "fixings;", len(hol), "holidays;", len(out["c0"]["dates"]), "nodes")
