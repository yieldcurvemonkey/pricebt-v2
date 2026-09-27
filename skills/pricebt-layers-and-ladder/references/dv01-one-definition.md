# dv01: one definition, and what breaks when there are two

## The contract

`dv01` (`src/pricebt/contracts/schemas/swap.yaml`, `measures.dv01`): the change in value for a +1bp move of the swap's own market rate, in currency, for ONE unit as built with its
notional (a 120mm ten-year payer is roughly +100,000), holder-signed (payer positive). The engine multiplies by the quantity held for portfolio risk, cost triggers and hedge sizing.
`gamma` is the second derivative with respect to the SAME move, per bp squared. `rate` is the par rate of the remaining swap, in PERCENT: the baseline decomposition reads the bp size of a
rate move from the unit the schema declares for `rate` (`_bp_per_unit` in `src/pricebt/engine/engine.py`), so a `rate` bound as a decimal without `scale: 100` scales `tay_delta` and `tay_convexity` wrongly.

## Two numbers that look alike

1. **The analytic annuity** (the fixed leg's PV01): the sum over the fixed leg's coupons not yet paid of `notional x accrual fraction x DF(pay date)` per basis point. Cheap, one call in most libraries.
2. **The market dv01**: the derivative of the value with respect to the par rates of the risk curve, which is the SUM OF THE DELTA LADDER (`docs/design/11-quantlib-conventions.md`, sections 5 and 6).

For a swap that has not started AND is struck at par they agree to about 1e-4 to 1e-3 (1.3e-4 on a 10Y, 9.4e-4 at worst, measured on real curves in both libraries). They do not agree in two other cases.

**Off par, unstarted.** The annuity ignores the `(S - K) dA/dS` term (`docs/design/11-quantlib-conventions.md`, section 5: "Off par (fixed 3.9% against a 4.5% market) the analytic PV01 differs from the market
dv01 by up to 34%"). Executed on the reference stack (flat 4% market, 10mm payer, unstarted; `dv01 measure` is what `RefSwap.dv01` answers, which IS the annuity for an unstarted swap):

```python
"""dv01 of an UNSTARTED payer, at par and off par: the analytic annuity against the ladder sum (reference stack, flat 4% market)."""
import datetime as dt

from pricebt.pricable import MarkContext
from pricebt.snapshot import SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.layer_conformance import flat_swap_world

world, conv, t0 = flat_swap_world("cal"), {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}, dt.date(2024, 3, 4)
p = R.wrap(SnapshotPricer(world(t0, 0.0)))
c = MarkContext(p.ts, p, {"primary": p}, None, None, p.ts, p, {}, {})
for eff, rate in (("spot", "par"), ("spot", 3.0), ("spot", 5.0), ("1Y", "par"), ("1Y", 3.0)):
    for mat in ("2Y", "10Y"):
        sw = R.swap_factory(p, p.ts, terms={"side": "pay", "direction": 1, "effective": eff, "maturity": mat, "notional": 1e7, "fixed_rate": rate}, conventions=conv).obj
        ann, lad = sw.annuity_dv01(p.need_curve(), p.reference_date), sum(sw.delta_ladder(ctx=c).values())
        print(f"effective {eff:5} fixed {str(rate):5} {mat:3} started={sw.started(t0)!s:5} annuity {ann:10.2f} ladder {lad:10.2f} annuity/ladder-1 {100 * (ann / lad - 1):+7.2f}%  dv01 measure {sw.dv01(ctx=c):10.2f}")
```

Output (real):

```
effective spot  fixed par   2Y  started=False annuity    1908.82 ladder    1908.82 annuity/ladder-1   -0.00%  dv01 measure    1908.82
effective spot  fixed par   10Y started=False annuity    8189.83 ladder    8189.83 annuity/ladder-1   -0.00%  dv01 measure    8189.83
effective spot  fixed 3.0   2Y  started=False annuity    1908.82 ladder    1880.10 annuity/ladder-1   +1.53%  dv01 measure    1908.82
effective spot  fixed 3.0   10Y started=False annuity    8189.83 ladder    7765.61 annuity/ladder-1   +5.46%  dv01 measure    8189.83
effective spot  fixed 5.0   2Y  started=False annuity    1908.82 ladder    1936.12 annuity/ladder-1   -1.41%  dv01 measure    1908.82
effective spot  fixed 5.0   10Y started=False annuity    8189.83 ladder    8593.17 annuity/ladder-1   -4.69%  dv01 measure    8189.83
effective 1Y    fixed par   2Y  started=False annuity    1838.89 ladder    1838.90 annuity/ladder-1   -0.00%  dv01 measure    1838.89
effective 1Y    fixed par   10Y started=False annuity    7868.89 ladder    7868.86 annuity/ladder-1   +0.00%  dv01 measure    7868.89
effective 1Y    fixed 3.0   2Y  started=False annuity    1838.89 ladder    1792.83 annuity/ladder-1   +2.57%  dv01 measure    1838.89
effective 1Y    fixed 3.0   10Y started=False annuity    7868.89 ladder    7382.74 annuity/ladder-1   +6.58%  dv01 measure    7868.89
```

So the two definitions differ by PERCENTS (not 1e-3) for a forward-starting swap or any swap struck away from the market, and a stack that answers the ladder sum there fails `L2.dv01` (rel 1e-4) against the
reference: the reference's answer for an unstarted swap is the annuity, whatever the strike. The check below asserts exactly that (rows `fixed 3.0`).

**Started.** The current coupon's fixed amount no longer moves with rates, but the annuity still counts it. The reference stack says so in its own code (`RefSwap.dv01`: "the analytic figure keeps the whole
accrued coupon, which no longer moves with rates: the market dv01 is the ladder's sum"). Executed on a flat 4% market, a 10mm payer struck at par:

```
2Y payer 10mm struck at par on 2024-03-04
  age   0 d  started=False annuity    1908.82  ladder sum    1908.82  annuity/ladder-1 -0.00%  dv01 measure    1908.82
  age  30 d  started=True  annuity    1915.10  ladder sum    1840.29  annuity/ladder-1 +4.07%  dv01 measure    1840.29
  age 120 d  started=True  annuity    1934.08  ladder sum    1615.49  annuity/ladder-1 +19.72%  dv01 measure    1615.49
  age 240 d  started=True  annuity    1959.69  ladder sum    1308.32  annuity/ladder-1 +49.79%  dv01 measure    1308.32
  age 330 d  started=True  annuity    1979.11  ladder sum    1072.38  annuity/ladder-1 +84.55%  dv01 measure    1072.38
  age 360 d  started=True  annuity    1985.63  ladder sum     992.73  annuity/ladder-1 +100.02%  dv01 measure     992.73
10Y payer 10mm struck at par on 2024-03-04
  age   0 d  started=False annuity    8189.83  ladder sum    8189.83  annuity/ladder-1 -0.00%  dv01 measure    8189.83
  age  30 d  started=True  annuity    8216.80  ladder sum    8142.10  annuity/ladder-1 +0.92%  dv01 measure    8142.10
  age 120 d  started=True  annuity    8298.24  ladder sum    7979.80  annuity/ladder-1 +3.99%  dv01 measure    7979.80
  age 240 d  started=True  annuity    8408.09  ladder sum    7756.90  annuity/ladder-1 +8.40%  dv01 measure    7756.90
  age 330 d  started=True  annuity    8491.43  ladder sum    7584.74  annuity/ladder-1 +11.95%  dv01 measure    7584.74
  age 360 d  started=True  annuity    8519.39  ladder sum    7526.41  annuity/ladder-1 +13.19%  dv01 measure    7526.41
```

(annuity/ladder-1 is how much the wrong definition overstates the risk: 100% on a 2Y swap a year in, 13% on a 10Y.) The script:

```python
"""dv01 of a payer swap struck at par, as it ages, on a flat 4% curve: the analytic annuity against the ladder sum (reference stack)."""
import datetime as dt

from pricebt.pricable import MarkContext
from pricebt.snapshot import SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.layer_conformance import flat_swap_world

world = flat_swap_world("cal")
conv = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
t0 = dt.date(2024, 3, 4)


def pricer(ref):
    return R.wrap(SnapshotPricer(world(ref, 0.0)))


def ctx(p):
    return MarkContext(p.ts, p, {"primary": p}, None, None, p.ts, p, {}, {})


for mat in ("2Y", "10Y"):
    p0 = pricer(t0)
    sw = R.swap_factory(p0, p0.ts, terms={"side": "pay", "direction": 1, "effective": "spot", "maturity": mat, "notional": 1e7, "fixed_rate": "par"}, conventions=conv).obj
    print(mat, "payer 10mm struck at par on", t0)
    for days in (0, 30, 120, 240, 330, 360):
        ref = t0 + dt.timedelta(days=days)
        while ref.weekday() >= 5:
            ref += dt.timedelta(days=1)
        p = pricer(ref)
        c = ctx(p)
        ann = sw.annuity_dv01(p.need_curve(), p.reference_date)
        lad = sum(sw.delta_ladder(ctx=c).values())
        print("  age %3d d  started=%-5s annuity %10.2f  ladder sum %10.2f  annuity/ladder-1 %+.2f%%  dv01 measure %10.2f" % (days, sw.started(ref), ann, lad, 100 * (ann / lad - 1), sw.dv01(ctx=c)))
```

## The story that fixes the rule (`tasks/suite_reproduction.md`, section 3)

The recorded real-data suite reported, for a swap that had already started, the analytic PV01. The pre-refactor code and both shipped adapters report the ladder sum. Nothing in a
mark, a cash flow or a layer differs; but the dv01 of an OPEN swap is read in three places, and each moved:

* the `measure_dv01` column (the only difference in two of the runs);
* the cost of every exit: a `scaled` cost with `scaling_type: "measure:dv01"` charges a fraction of |dv01| on the close, and opens (not started, so identical) matched to the last bit while closes did not;
* the rebalance trigger of a steepener: a band on the book's net dv01 reads the dv01 of the started legs; the annuity is flat until the first coupon date while the market dv01 decays with age,
  so the book left the band far more often (76 rebalances against 24) and made DIFFERENT TRADES from the first rebalance on.

Binding the recorded definition back (one overlay, one binding) reproduced the recorded run bit for bit: the whole difference had one cause. The lesson is not "the ladder sum is right and the
annuity wrong": it is that dv01 feeds decisions, so its definition is part of the strategy, and every stack of a tie-out and every state of the swap must answer the SAME question.

## The rule

1. **One definition per state, the same in every stack.** The shipped stacks answer: matured `0.0`; started (`effective < reference_date`, strictly): the MARKET dv01, the sum of the delta ladder over the bound `tenors`;
   not started: the analytic annuity (`RefSwap.dv01`, `QLSwap.dv01`, `AcmeSwap.dv01`). The annuity is the market dv01 only for a swap struck AT PAR (1e-4 to 1e-3); off par it is a different number (percents, above),
   but it is what the reference answers, so it is what your stack answers.
2. **Per state.** matured: `0.0`; started: the sum of the delta ladder over the SAME `tenors` the `delta_ladder` binding declares; not started: the analytic annuity (or the ladder sum: see 5). Never the annuity after the start.
   **Not the library's own parallel dv01 either, even when it has one and passes every `L2` row.** A +-1bp PARALLEL difference and a sum of +-0.5bp PER-PILLAR differences are two recipes for one number and differ by
   about 1e-8 to 2e-7 relative: `L2.dv01` (tolerance 1e-4) calls that `noise`, but the engine's baseline `tay_unexplained` is the interval P&L minus `tay_delta` minus `tay_convexity`, a small residual, and the same
   absolute error (8.5e-3 on a position whose `tay_delta` is about 4.5e4) is 1.17e-3 of it against the shipped tolerance of 1e-3 (`L3.tay_unexplained` `exceeds` alone, with its `/unit` twin; `L2.dv01` `noise` 1.87e-7,
   `L3.tay_delta` `noise`). Executed on the example service, binding its parallel `RISK_10BP` for started swaps. The check below catches it at the source: `gap to ladder` 7e-9 to 2e-8 against its 1e-12 limit.
3. **The same in every stack** of a tie-out. `L2.dv01` is compared at rel 1e-4 (`DEFAULT_TOLERANCES`); an adapter that answers the annuity after the start is 4% off a month in (the mutant below).
4. **The same `tenors` on `dv01`, `gamma` and `delta_ladder`** (pass one list to the three bindings): the pillar set changes an aged swap's dv01 (about 1e-4 to 4e-4 for a 2Y par payer aged 30 to 360 days when the
   pillars are {2Y, 5Y, 10Y, 30Y}, {1Y, 2Y, 3Y, 5Y} or the default set without 3M and 6M, against the default 11: a scratch measurement on the flat world; up to 9.8e-3 measured in `docs/design/11-quantlib-conventions.md`,
   section 5), which is above the shipped tolerance. `sum(delta_ladder) == dv01` then holds exactly for a started swap.
   When the LIBRARY fixes the pillar set and it differs from the reference's, the reference is moved to the library's list through a second overlay, never the other way and never a declared tolerance:
   `SKILL.md`, section "Your library's pillar set is fixed and differs from the reference's". In the check below pass that list to the reference too (`ref_swap.dv01(ctx=..., tenors=[...])`), else its `vs reference` column
   carries the basis gap (2e-5 to 8e-5 on the flat world, on the edge of the 1e-4 tolerance). The example service's copy of the block, `skills/pricebt-wire-external-library/example-service/probes/p7_dv01_consistency.py`, does this: a
   `LIB_TENORS` line, `vs reference` on the library's twelve pillars (at most 5.2e-14) and the gap to the reference's default eleven printed beside it (`vs its default pillars`, 2.0e-05 to 8.0e-05 on the started rows; the same 2e-5 to 8e-5).
5. **If you cannot compute the annuity exactly, use the ladder sum in every state.** One code path, no discontinuity; the price is that it differs from a reference that answers the annuity for unstarted swaps by
   1e-4 to 1e-3 at par and by PERCENTS off par (up to 6.6% in the table above, 34% in the QuantLib document), so declare `L2.dv01` with a `reason` (`tasks/tolerance_ledger.yaml`) sized on the OFF-PAR UNSTARTED trades
   of your base config (forward-starting swaps, swaps struck away from the market), not on the at-par gap. Computing the annuity is cheaper than that declaration: it is the fixed leg's accrual-weighted discount factors,
   one call in most libraries. The cost of the ladder-sum definition is a risk-curve build per snapshot: the suite runs were measurably slower with it (`tasks/suite_reproduction.md`, section 4, "Speed note"): cache the
   curve build per snapshot, not per position.

## The check (executed on the worked example; run it on your adapter)

The block evaluates the adapter's BOUND `dv01` and `delta_ladder` the way the engine does, on a PAYER struck at par and on one struck 100bp off the market, each ageing from unstarted to started, and demands: the payer's dv01
is positive; it equals the reference stack's dv01 on the same trade (rel 1e-4, the `L2.dv01` tolerance); started means `dv01 ==` ladder sum (to 1e-12); unstarted AND at par means within 1e-3 of the ladder sum; unstarted off par
the gap to the ladder sum is printed and is percent-level (`gap to ladder 1.5e-02`: the annuity is what the reference answers). Change the lines marked `LIB`.

```python
"""dv01_consistency.py: the sign and ONE definition of dv01, checked through an adapter's BOUND measures on payers that age from unstarted to started, struck at par and off par, and against the reference stack. Run from the repository root."""
import datetime as dt
import sys

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import evaluate_measure, scalar_of
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.pricable import MarkContext
from pricebt.snapshot import SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.layer_conformance import flat_swap_world

# ---- LIB: your adapter package: its `swap` Kit, its `wrap`, its conventions block, the dotted-path prefix its function bindings need ------------------------------------
import acme_adapter as ADAPTER                                                                               # LIB
KIT, WRAP, ALLOW = ADAPTER.swap, ADAPTER.wrap, ("pricebt", "acme_adapter")                                   # LIB
CONV = {**ADAPTER.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}                                               # LIB
# --------------------------------------------------------------------------------------------------------------------------------------------------------------------------

world = flat_swap_world("cal")  # a flat 4% market with consistent fixings, weekends only: library-free
spec = build_spec("d", {"factory": KIT, "conventions": CONV}, schemas=SchemaRegistry.default(), allow=ALLOW)
t0 = dt.date(2024, 3, 4)


def ctx(p):
    return MarkContext(p.ts, p, {"primary": p}, None, None, p.ts, p, {}, {})


bad = 0
for fixed in ("par", 3.0):  # struck at par, and 100bp off the 4% market
    terms = {"side": "pay", "effective": "spot", "maturity": "2Y", "notional": 1e7, "fixed_rate": fixed}
    p0 = WRAP(SnapshotPricer(world(t0, 0.0)))
    built = TradeTemplate("d", spec, terms).build(p0, p0.ts)
    r0 = R.wrap(SnapshotPricer(world(t0, 0.0)))
    ref_swap = R.swap_factory(r0, r0.ts, terms={**terms, "direction": 1}, conventions=CONV).obj  # the reference stack's swap, same neutral terms
    for days in (0, 1, 3, 30, 120, 240, 360):
        ref = t0 + dt.timedelta(days=days)
        while ref.weekday() >= 5:
            ref += dt.timedelta(days=1)
        p = WRAP(SnapshotPricer(world(ref, 0.0)))
        c = ctx(p)
        env = Env(pricer=p, ctx=c, instrument=built.obj, terms=built.terms, state={})
        dv01 = scalar_of(evaluate_measure(spec, "dv01", env, cache=c.cache))
        ladder = sum(evaluate_measure(spec, "delta_ladder", env, cache=c.cache).values())
        ref_dv01 = ref_swap.dv01(ctx=ctx(R.wrap(SnapshotPricer(world(ref, 0.0)))))
        started = built.terms["effective"] < ref
        gap = abs(dv01 - ladder) / abs(ladder)
        vs_ref = abs(dv01 - ref_dv01) / abs(ref_dv01)
        # a PAYER's dv01 is positive; the same number as the reference stack (L2.dv01 is compared at rel 1e-4); started: dv01 IS the ladder sum; unstarted AT PAR the annuity and the ladder sum agree to ~1e-3
        ok = dv01 > 0 and vs_ref < 1e-4 and (gap < 1e-12 if started else (fixed != "par" or gap < 1e-3))
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} fixed {fixed!s:<4} age {days:>3} d  started {started!s:<5}  dv01 {dv01:>9.2f}  ladder sum {ladder:>9.2f}  gap to ladder {gap:.1e}  reference dv01 {ref_dv01:>9.2f}  vs reference {vs_ref:.1e}")
sys.exit(1 if bad else 0)
```

Output for the worked example (exit 0):

```
ok  fixed par  age   0 d  started False  dv01   1908.82  ladder sum   1908.82  gap to ladder 2.3e-09  reference dv01   1908.82  vs reference 1.2e-16
ok  fixed par  age   1 d  started False  dv01   1909.02  ladder sum   1908.91  gap to ladder 6.2e-05  reference dv01   1909.02  vs reference 0.0e+00
ok  fixed par  age   3 d  started True   dv01   1907.11  ladder sum   1907.11  gap to ladder 0.0e+00  reference dv01   1907.11  vs reference 1.2e-16
ok  fixed par  age  30 d  started True   dv01   1840.29  ladder sum   1840.29  gap to ladder 0.0e+00  reference dv01   1840.29  vs reference 0.0e+00
ok  fixed par  age 120 d  started True   dv01   1615.49  ladder sum   1615.49  gap to ladder 0.0e+00  reference dv01   1615.49  vs reference 1.4e-16
ok  fixed par  age 240 d  started True   dv01   1308.32  ladder sum   1308.32  gap to ladder 0.0e+00  reference dv01   1308.32  vs reference 0.0e+00
ok  fixed par  age 360 d  started True   dv01    992.73  ladder sum    992.73  gap to ladder 0.0e+00  reference dv01    992.73  vs reference 1.1e-16
ok  fixed 3.0  age   0 d  started False  dv01   1908.82  ladder sum   1880.10  gap to ladder 1.5e-02  reference dv01   1908.82  vs reference 1.2e-16
ok  fixed 3.0  age   1 d  started False  dv01   1909.02  ladder sum   1880.24  gap to ladder 1.5e-02  reference dv01   1909.02  vs reference 0.0e+00
ok  fixed 3.0  age   3 d  started True   dv01   1878.53  ladder sum   1878.53  gap to ladder 0.0e+00  reference dv01   1878.53  vs reference 0.0e+00
ok  fixed 3.0  age  30 d  started True   dv01   1813.03  ladder sum   1813.03  gap to ladder 0.0e+00  reference dv01   1813.03  vs reference 4.8e-14
ok  fixed 3.0  age 120 d  started True   dv01   1592.67  ladder sum   1592.67  gap to ladder 0.0e+00  reference dv01   1592.67  vs reference 3.2e-14
ok  fixed 3.0  age 240 d  started True   dv01   1291.62  ladder sum   1291.62  gap to ladder 0.0e+00  reference dv01   1291.62  vs reference 3.9e-14
ok  fixed 3.0  age 360 d  started True   dv01    982.40  ladder sum    982.40  gap to ladder 0.0e+00  reference dv01    982.40  vs reference 7.4e-15
```

Three mutants were run through the block and all exit 1: the acme `dv01` replaced by the annuity for every state prints `BAD` from age 3 d on, at par and off par (relative differences 1.2e-03, 4.1e-02, 2.0e-01, 5.0e-01,
1.0e+00 at par); the `dv01` default binding without its `sign: -1` prints `BAD` on every row (`dv01 -1908.82`, relative difference 2.0); and the ladder sum in EVERY state (rule 5 without a declaration) prints `BAD` on exactly
the two unstarted `fixed 3.0` rows (`vs reference 1.5e-02`) while every at-par row stays `ok`: the off-par rows are what expose it. The sign row matters: the layer conformance kit does NOT see a wrong-signed `dv01`
(its mirror check is antisymmetric, so a uniform sign error cancels; measured: the kit passed with `dv01` unsigned), so this sign assertion is the check (`kit-blind-spots.md`).
