"""dv01_consistency.py: the sign and ONE definition of dv01, checked through an adapter's BOUND measures on payers that age from unstarted to started, struck at par and off par, and against the reference stack.
Run from the repository root with PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example-service". Exit 0 = every row ok."""
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
import zeta_adapter as ADAPTER                                                                               # LIB
KIT, WRAP, ALLOW = ADAPTER.swap, ADAPTER.wrap, ("pricebt", "zeta_adapter")                                   # LIB
CONV = {**ADAPTER.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}                                               # LIB
# the pillar list of the library's risk curve when it is FIXED and differs from the reference's default eleven (zeta: twelve, 1M to 30Y). The reference is asked for its dv01 on that list too, which is what the
# tie-out compares (the second overlay `refstack_zeta_pillars.yaml`); a library on the reference's own pillars sets None
LIB_TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]                    # LIB
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
        rc = ctx(R.wrap(SnapshotPricer(world(ref, 0.0))))
        ref_dv01 = ref_swap.dv01(ctx=rc, tenors=LIB_TENORS) if LIB_TENORS else ref_swap.dv01(ctx=rc)  # the reference on the LIBRARY's pillars: the basis the tie-out compares
        ref_default = ref_swap.dv01(ctx=rc)  # the reference on its own default pillars: printed to show the basis gap, never gated
        started = built.terms["effective"] < ref
        gap = abs(dv01 - ladder) / abs(ladder)
        vs_ref = abs(dv01 - ref_dv01) / abs(ref_dv01)
        vs_default = abs(dv01 - ref_default) / abs(ref_default)
        # a PAYER's dv01 is positive; the same number as the reference stack on the same pillars (L2.dv01 is compared at rel 1e-4); started: dv01 IS the ladder sum; unstarted AT PAR the annuity and the ladder sum agree to ~1e-3
        ok = dv01 > 0 and vs_ref < 1e-4 and (gap < 1e-12 if started else (fixed != "par" or gap < 1e-3))
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} fixed {fixed!s:<4} age {days:>3} d  started {started!s:<5}  dv01 {dv01:>9.2f}  ladder sum {ladder:>9.2f}  gap to ladder {gap:.1e}  reference dv01 {ref_dv01:>9.2f}  vs reference {vs_ref:.1e}  (vs its default pillars {vs_default:.1e})")
sys.exit(1 if bad else 0)
