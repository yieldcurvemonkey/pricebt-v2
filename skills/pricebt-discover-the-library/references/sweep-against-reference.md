# Sweep against the reference stack: the pattern of `tools/ql_evidence/p1_npv.py`

Once the questionnaire has told you what to expect, measure it. The pattern (from `tools/ql_evidence/p1_npv.py`, `p1_dates.py`, `p1_started.py`, `docs/design/11-quantlib-conventions.md` section 1):

1. **Same inputs on both sides.** The same discount-factor node table, the same holiday set, the same fixings. Take them from a pricebt SNAPSHOT (`SyntheticMarket` needs no data;
   `tests/support` has fixture-backed providers when `data/fixtures` exists), so the reference stack and your library cannot differ in inputs. Any difference is then a convention.
2. **The independent side is the reference stack** (`pricebt.testing.refstack`: numpy only, closed form, the known answer of every tie-out), or a second library. It shares no code with yours (except in the
   worked example, where `acmelib` is a fictional library built on the reference's arithmetic: its agreement is exact BY CONSTRUCTION; a real library shows a noise floor, which you record, section 0 of
   the conventions document).
3. **A control first**, then a sweep over dates x tenors x directions, printing the WORST absolute and relative difference per quantity and the time per swap.
4. **Convert on the probe's own terms.** Here the snapshot -> library conversion is a few lines in the probe (the `LIB` block); it becomes `wrap` (`pricebt-wrap-and-pricer`) only after the numbers agree.

The block below was executed against `acmelib` (`PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example"`, repository root). Change the `LIB` lines.

```python
"""sweep_vs_reference.py: the same swaps priced by <LIB> and by the reference stack from the SAME snapshot (same discount factors, same holidays, same fixings); prints the worst
disagreement per quantity and the time per swap. The pattern of tools/ql_evidence/p1_npv.py. Run from the repository root."""
import datetime as dt
import itertools
import math
import sys
import time

import pandas as pd

from pricebt.pricable import MarkContext
from pricebt.testing import refstack as R
from pricebt.testing.synthetic import SyntheticMarket
from pricebt.timeutil import Calendar

# ---- LIB: the only lines that name your library; the conversion here is a PROBE's, not the adapter's (that is `wrap`, written later) ---------------------------------------
import acmelib as lib                                                                                                                                # LIB
def lib_setup(snap, name):                                                                                                                          # LIB snapshot -> the library's market, calendar and process-global date
    c = snap.curves["sofr"]
    days = [(d - c.reference_date).days for d in c.node_dates[1:]]
    zeros = [-math.log(v) / (n / 365.0) for v, n in zip(c.values[1:], days)]                                                                       # LIB DFs -> zero rates (acmelib's variable)
    lib.register_calendar(name, [h.isoformat() for h in snap.calendars["usd_fed"].holidays], replace=True)                                          # LIB the SNAPSHOT's holidays, never a named calendar
    lib.set_valuation_date(c.reference_date.isoformat())                                                                                            # LIB the process-global date
    return lib.Market(lib.ZeroCurve(c.reference_date.isoformat(), [d.isoformat() for d in c.node_dates[1:]], zeros))
def lib_swap(ref_iso, tenor, notional, fixed, name):                                                                                                # LIB spot-start swap; the library wants ISO dates, lower-case tenors, a decimal rate
    return lib.Swap(lib.add_business_days(ref_iso, 2, name), tenor.lower(), notional, fixed / 100.0, calendar=name)
def lib_numbers(sw, m, notional):                                                                                                                   # LIB -> (pv, par percent, dv01) in the library's own view
    return lib.pv(sw, m), lib.par_rate(sw, m) * 100.0, lib.pv01(sw, m) * notional / lib.MM
SIGN = -1.0                                                                                                                                         # LIB the library is the RECEIVER's: a PAYER is minus its numbers (probe-skeleton H1), a pricebt RECEIVER is its own numbers
# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------

CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "usd_fed"}
# a market WITH holidays: the synthetic default calendar has none, so a holiday-sensitive control on it would be vacuous (the spot dates of 2024-03-27 and 2024-05-23 land ON a holiday)
HOLIDAYS = [dt.date(2024, 3, 29), dt.date(2024, 5, 27)]
mdp = SyntheticMarket(start="2024-01-02", end="2024-06-28", seed=7, calendar=Calendar(HOLIDAYS))
REFS = ["2024-03-04", "2024-03-15", "2024-03-27", "2024-04-02", "2024-05-23", "2024-06-03"]
TENORS = ["1Y", "2Y", "5Y", "10Y", "30Y"]
worst = {"pv": [0.0, 0.0], "par": [0.0, 0.0], "dv01": [0.0, 0.0]}
t_lib = t_ref = 0.0
n = 0
for k, ref_iso in enumerate(REFS):
    snap = mdp.get_pricer(pd.Timestamp(ref_iso + " 17:00", tz="America/New_York")).snapshot
    rp = R.wrap(mdp.get_pricer(pd.Timestamp(ref_iso + " 17:00", tz="America/New_York")))
    ctx = MarkContext(rp.ts, rp, {"primary": rp}, None, None, rp.ts, rp, {}, {})
    m = lib_setup(snap, f"PROBE{k}")
    if k == 0:  # KNOWN ANSWER FIRST: a swap struck at its own par is worth zero on both sides
        p = lib.par_rate(lib_swap(ref_iso, "5Y", 1e7, 0.0, "PROBE0"), m) * 100.0
        assert abs(lib.pv(lib_swap(ref_iso, "5Y", 1e7, p, "PROBE0"), m)) < 1e-4, "control failed: the library's own par swap is not worth 0"
        rs = R.swap_factory(rp, rp.ts, terms={"side": "pay", "direction": 1, "effective": "spot", "maturity": "5Y", "notional": 1e7, "fixed_rate": "par"}, conventions=CONV).obj
        assert abs(rs.value(ctx=ctx).pv) < 1e-4, "control failed: the reference's par swap is not worth 0"
        print("controls ok: a par swap is worth 0 on both sides")
    for tenor, (side, direction) in itertools.product(TENORS, (("pay", 1), ("receive", -1))):  # BOTH directions: a receiver-only sign bug must not hide
        t0 = time.perf_counter()
        sw = lib_swap(ref_iso, tenor, 1e7, 4.0, f"PROBE{k}")
        pv, par_pct, dv01 = lib_numbers(sw, m, 1e7)
        a = [SIGN * direction * pv, par_pct, SIGN * direction * dv01]  # holder-signed; the par rate has no side
        t1 = time.perf_counter()
        rs = R.swap_factory(rp, rp.ts, terms={"side": side, "direction": direction, "effective": "spot", "maturity": tenor, "notional": 1e7, "fixed_rate": 4.0}, conventions=CONV).obj
        b = [rs.value(ctx=ctx).pv, rs.rate(ctx=ctx), rs.dv01(ctx=ctx)]
        t2 = time.perf_counter()
        t_lib, t_ref, n = t_lib + t1 - t0, t_ref + t2 - t1, n + 1
        for key, x, y in zip(("pv", "par", "dv01"), a, b):
            worst[key][0] = max(worst[key][0], abs(x - y))
            worst[key][1] = max(worst[key][1], abs(x - y) / max(abs(y), 1.0))
print(f"{n} swaps ({len(REFS)} dates x {len(TENORS)} tenors x pay and receive)")
for key, (ab, rl) in worst.items():
    print(f"  {key:<5} max abs {ab:.3e}   max rel {rl:.3e}")
print(f"  time per swap: library {t_lib / n * 1e3:.1f} ms, reference {t_ref / n * 1e3:.1f} ms")
sys.exit(0)
```

Output (real):

```
controls ok: a par swap is worth 0 on both sides
60 swaps (6 dates x 5 tenors x pay and receive)
  pv    max abs 7.858e-10   max rel 1.565e-14
  par   max abs 4.441e-16   max rel 1.144e-16
  dv01  max abs 3.638e-12   max rel 2.384e-16
  time per swap: library 0.1 ms, reference 0.1 ms
```

Read it: the control passed on both sides, and every quantity agrees to round-off (pv about `1e-14` relative, par and dv01 about `1e-16`) because acmelib reuses the reference arithmetic. For a real
library the same table is the headline of section 0 of the conventions document; a difference of 1e-13 is a floating-point ordering, 1e-9 is a solver tolerance on one side, 1e-6 and above is a
convention (a day count, a calendar, an interpolation): bisect it by changing ONE input at a time (`pricebt-debug-tieout-differences`).

**The sweep has teeth** (four mutants of the block were run; treating every swap as a payer, `direction` ignored, prints `pv` and `dv01` `max rel 2.000e+00` with `par` untouched, and only the receiver half of the sweep can show it): `SIGN = 1.0` (forgetting that acmelib is the receiver's view) prints `max rel 2.000e+00` for pv, par and dv01; dropping the
`* 100.0` on the par rate (a decimal read as percent) prints `par max rel 9.900e-01` and leaves pv and dv01 at 1e-14; giving the library NO holidays (its own weekend-only calendar) prints `pv max rel 2.2e-01`, `par 2.6e-04`, `dv01 5.6e-03`.
That last one only shows because the market HAS holidays and two reference dates have a spot date ON a holiday: on `SyntheticMarket`'s default calendar (weekends only) the same mutant is invisible, and a control that cannot fail is worth nothing.

## What to add for a real library

(The scripts named below are the models in `tools/ql_evidence/`; write yours under `tools/<lib>_evidence/`.)

* a started swap: strike a swap in the past on a snapshot that has fixings and compare NPV, every fixed and floating flow amount and the payment dates (`p1_started.py`); a missing fixing must raise;
* the calendar: business-day agreement over the whole data window and the spot date over many reference dates (`p1_dates.py`), including a date beyond the last holiday you have data for;
* schedules: an 18M or 30M swap (short front stub) and explicit maturity dates (`p1_sched.py`, `p1_explicit.py`);
* the risk: the strike bump identity for dv01, the ladder against a bump-and-reprice of the reference (`p1_dv01.py`, `p1_ladder.py`);
* time: seconds per valuation, per ladder, per curve build (`p1_timing.py`), for the cost budget of `pricebt-enterprise-platform-patterns`.

Keep every script: it is the "Script" column of the conventions document and the seed of the permanent tests (`probe-to-test.md`).
