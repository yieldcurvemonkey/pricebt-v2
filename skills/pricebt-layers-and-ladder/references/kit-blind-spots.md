# What each local check sees, and the check for `gamma`

A wrong sign or scale in one binding is one line of an adapter and can pass every check that does not look at that measure. This table was MEASURED on the worked example
(`skills/pricebt-wire-external-library/example/`): each row leaves ONE binding of the default block without its `sign: -1` (the receiver's-view library's flip) and asks which check fails.

| binding left without its `sign` | layer conformance kit (`run_kit`) | `golden_control.py` | `dv01_consistency.py` | `risk_known_answers.py` (below) | tie-out (reference vs your stack) |
|---|---|---|---|---|---|
| `carry` | 1 row: `unexplained_share (static market)` | 2 `BAD` rows | passes | passes | `L3.carry`, `L3.unexplained` |
| `roll` | passes | 2 `BAD` rows | passes | passes | `L3.roll`, `L3.unexplained` |
| `delta` | 5 rows: four `fd_vs_delta_convexity/shock` and `unexplained_share (moved market)` | 2 `BAD` rows | passes | passes | `L3.delta`, `L3.unexplained` |
| `convexity` | 2 rows: the `+-25bp` `fd_vs_delta_convexity/shock` | 2 `BAD` rows | passes | passes | `L3.convexity`, `L3.unexplained` |
| `dv01` | passes | passes | 14 `BAD` rows | 1 `BAD` row | `L2.dv01` (rel 2.0), `L3.tay_delta`, `L3.tay_unexplained` |
| `gamma` | passes | passes | passes | 1 `BAD` row | `L2.gamma`, `L3.tay_convexity`, `L3.tay_unexplained` |
| `delta_ladder` | passes | passes | 12 `BAD` rows (the ladder sum no longer equals `dv01`) | passes | `L2.delta_ladder.<bucket>` for the eight buckets that carry risk (only with the ladder audited) |

The tie-out column is the set of rows of `run_tieout(base, {"reference": ["configs/adapters/refstack_swap.yaml"], "acme": [overlay]}, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))` that leave `exact`/`noise` when the overlay restates that one binding without `sign` (executed for all seven; every run has `passed: False`). Two consequences:

* `gamma` is seen by NOTHING before the tie-out except `risk_known_answers.py`: not the kit, not the golden case, not the dv01 check. `roll`, `dv01` and the ladder are invisible to the kit.
* Run all four scripts (`golden_control.py`, `dv01_consistency.py`, `risk_known_answers.py`, `cash_window_check.py` below) and the kit over a fine AND a coarse step: each covers what the others cannot.

## `risk_known_answers.py`: sign and scale of the BOUND `dv01` and `gamma`

The known answer is full revaluation: shock the flat market by +-1bp (a parallel zero-rate move: `world(ref, shock_bp)` of `flat_swap_world`), value the same trade through the BOUND `value`, and compare
`(V(+1) - V(-1)) / 2` with `dv01` and `V(+1) + V(-1) - 2 V(0)` with `gamma`. The variables differ (the bound risk moves the PAR rates, the shock moves the ZERO rate: 2.6 percent apart on `dv01` and 14 percent on
`gamma` for this 5Y swap), so the check pins the SIGN and the SCALE (a percent, a per-million basis, a missing `sign`), not the variable: a ratio in 0.9 to 1.1 for `dv01` and 0.5 to 2.0 for `gamma`.
Payer `gamma` is NEGATIVE (a payer's value is concave in the rate: the golden `convexity` is -50.23). Change the lines marked `LIB`.

```python
"""risk_known_answers.py: the sign and scale of an adapter's BOUND dv01 and gamma against full revaluation in a flat world (a parallel zero-rate shock). Run from the repository root."""
import datetime as dt
import sys

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import evaluate_measure, evaluate_value, scalar_of
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.pricable import MarkContext, as_valuation
from pricebt.snapshot import SnapshotPricer
from pricebt.testing.layer_conformance import flat_swap_world

# ---- LIB: your adapter package: its `swap` Kit, its `wrap`, its conventions block, the dotted-path prefix its function bindings need ------------------------------------
import acme_adapter as ADAPTER                                                                               # LIB
KIT, WRAP, ALLOW = ADAPTER.swap, ADAPTER.wrap, ("pricebt", "acme_adapter")                                   # LIB
CONV = {**ADAPTER.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}                                               # LIB
# --------------------------------------------------------------------------------------------------------------------------------------------------------------------------

world = flat_swap_world("cal")
spec = build_spec("r", {"factory": KIT, "conventions": CONV}, schemas=SchemaRegistry.default(), allow=ALLOW)
t0 = dt.date(2024, 3, 4)


def bound(shock_bp):
    """the BOUND world of the flat market shocked by `shock_bp` (the engine's path: spec, env, sign and scale of the bindings)"""
    p = WRAP(SnapshotPricer(world(t0, shock_bp)))
    return p, MarkContext(p.ts, p, {"primary": p}, None, None, p.ts, p, {}, {})


p0, c0 = bound(0.0)
built = TradeTemplate("r", spec, {"side": "pay", "effective": "spot", "maturity": "5Y", "notional": 1e7, "fixed_rate": "par"}).build(p0, p0.ts)


def env(c):
    return Env(pricer=c.pricer, ctx=c, instrument=built.obj, terms=built.terms, state={})


def pv(shock_bp):
    p, c = bound(shock_bp)
    return as_valuation(evaluate_value(spec, env(c)), p.ts).pv


dv01 = scalar_of(evaluate_measure(spec, "dv01", env(c0), cache=c0.cache))
gamma = scalar_of(evaluate_measure(spec, "gamma", env(c0), cache=c0.cache))
up, mid, dn = pv(+1.0), pv(0.0), pv(-1.0)
fd_dv01, fd_gamma = (up - dn) / 2.0, up + dn - 2.0 * mid  # currency per bp and per bp^2, holder-signed, for the ZERO-rate variable
bad = 0
for name, got, fd, lo, hi in (("dv01", dv01, fd_dv01, 0.9, 1.1), ("gamma", gamma, fd_gamma, 0.5, 2.0)):
    ratio = got / fd
    ok = lo <= ratio <= hi  # par-rate space (bound) against zero-rate space (the shock): a few percent apart, so the check pins SIGN and SCALE, not the variable
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {name:<6} bound {got:>12.4f}   full revaluation {fd:>12.4f}   ratio {ratio:8.4f}   (expected within {lo} .. {hi}, sign {'+' if fd > 0 else '-'})")
sys.exit(1 if bad else 0)
```

Output for the worked example (`PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example"`, exit 0); the reference stack (`import pricebt.testing.refstack as ADAPTER`, `ALLOW = ("pricebt",)`) prints the same two rows:

```
ok  dv01   bound    4503.1748   full revaluation    4622.8563   ratio   0.9741   (expected within 0.9 .. 1.1, sign +)
ok  gamma  bound      -2.5751   full revaluation      -2.2511   ratio   1.1439   (expected within 0.5 .. 2.0, sign -)
```

Mutants run through it (each exits 1 and names its row): `gamma` without `sign` -> `BAD gamma ... ratio -1.1439`; `dv01` without `sign` -> `BAD dv01 ... ratio -0.9741`; `gamma` with `scale: 100.0` -> ratio 114.39;
`gamma` with `scale: 0.01` -> ratio 0.0114; `dv01` with `scale: 100.0` -> ratio 97.41; `dv01` with `scale: 1e-6` -> ratio 0.0000.

## Making a mutant of your own adapter (do this once per check you rely on)

A check that has never failed has not been shown to work. Apply ONE mistake to the default block BEFORE the spec is built (the Kit merges its default bindings at build time), then run the check in the same process:

```python
import acme_adapter as A          # LIB: your package
A.SWAP_BIND["gamma"].pop("sign", None)    # LIB: the module-level default block; drop one `sign`, or set one `scale`
# ... then `runpy.run_path("risk_known_answers.py", run_name="__main__")`, or exec the check
```

To run the layer conformance kit on a mutant, rebuild the Kit with a copy of the block (the same module, `Kit` from `pricebt.contracts.spec`, its fields as the shipped kits build them):

```python
"""kit_mutants.py: which layer-kit rows fail when ONE default binding loses its sign. Run from the repository root."""
import copy
import datetime as dt

import acme_adapter as A  # LIB
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Kit, build_spec
from pricebt.testing.layer_conformance import Setup, flat_swap_world, run_kit

CONV = {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
TERMS = {"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2}
HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
for name in (None, "delta", "carry", "dv01", "gamma", "roll", "convexity", "delta_ladder"):
    bind = copy.deepcopy(A.SWAP_BIND)
    if name:
        bind[name].pop("sign", None)
    kit = Kit(factory=A.swap.factory, asset_class="swap", default_bind=bind, cls=A.AcmeSwap, extra={"acme_zero_dv01": "measure"}, doc="mutant")  # LIB: your class and extension names, as your Kit declares them
    spec = build_spec("ois", {"factory": kit, "conventions": CONV}, schemas=SchemaRegistry.default(), allow=("pricebt", "acme_adapter"))
    rep = run_kit(Setup(spec=spec, wrap=A.wrap, terms=TERMS, mirror_terms={**TERMS, "side": "receive"}, world=flat_swap_world("cal", holidays=HOL), t0=dt.date(2024, 3, 4), t1=dt.date(2024, 3, 5)))
    print(f"{name or 'control':<13} failed {len(rep.failures())}  ", [f"{r.check}/{r.quantity}" for r in rep.failures()][:6])
```

Output (the column "layer conformance kit" of the table above):

```
control       failed 0   []
delta         failed 5   ['fd_vs_delta_convexity/shock -25bp', 'fd_vs_delta_convexity/shock -3bp', 'fd_vs_delta_convexity/shock +3bp', 'fd_vs_delta_convexity/shock +25bp', 'unexplained_share/share (moved market)']
carry         failed 1   ['unexplained_share/share (static market)']
dv01          failed 0   []
gamma         failed 0   []
roll          failed 0   []
convexity     failed 2   ['fd_vs_delta_convexity/shock -25bp', 'fd_vs_delta_convexity/shock +25bp']
delta_ladder  failed 0   []
```

## The tie-out sees the `gamma` mistake that the kit and the golden case miss (executed)

`gamma` bound without its `sign` (an overlay whose `bind:` restates the gamma binding without `sign`). An overlay's `bind` replaces the config's WHOLE `bind` block (`apply_stack`, `src/pricebt/config/stacks.py`); the Kit's own default bindings then fill every name that block does not list (`src/pricebt/contracts/spec.py`), so here only `gamma` differs from the correct wiring, because the Kit's defaults carry the rest:

```yaml
instruments:
  usd_sofr_ois:
    factory: "acme_adapter:swap"
    bind:
      gamma: {target: {method: gamma}, kwargs: {ctx: "@ctx", tenors: [3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y, 15Y, 20Y, 30Y]}}
market:
  pricers:
    primary: {wrap: "acme_adapter:wrap"}
```

Tied out against the reference stack (`audit_measures` as above) it reports `passed: False` with, all `exceeds`: `L2.gamma` (max rel 2), `L3.tay_convexity` (2), `L3.tay_convexity/unit`, `L3.tay_unexplained` (13.1), `L3.tay_unexplained/unit`. So the mistake does not survive the tie-out; it survives everything before it unless you run `risk_known_answers.py`.

## The coarse step and the cash window: what the kit runs, and the check that can fail

The kit's default `Setup` steps ONE day (`t1 = t0 + 1 day`). `cash` behaves differently once `t1 - t0` exceeds the payment lag: a flow paid inside the interval can depend on a fixing published inside it
(`layer-definitions-and-assembly.md` section 3). Two things to run.

**1. The kit over a coarse step** (t1 nine days after t0, a payment date inside the interval, `payment_window` set: without it the cash sweep does not run). It exercises the coarse path of your code (for a service, the
rebuilt market and its upload) and fails on any error there. Executed on the worked example (exit 0):

```python
"""coarse_step_kit.py: the layer conformance kit over a fine step and over a COARSE one (t1 nine days after t0, a payment date inside). Run from the repository root."""
import datetime as dt
import sys

from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import build_spec
from pricebt.testing.layer_conformance import Setup, flat_swap_world, run_kit

import acme_adapter as A  # LIB: your adapter package (its `swap` Kit, `wrap`, conventions block, allow-list prefix)

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
spec = build_spec("ois", {"factory": A.swap, "conventions": {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}}, schemas=SchemaRegistry.default(), allow=("pricebt", "acme_adapter"))
TERMS = {"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}
bad = 0
for label, t1 in (("fine step (t1 = t0 + 1 day)", dt.date(2024, 5, 21)), ("coarse step (t1 = t0 + 9 days)", dt.date(2024, 5, 29))):
    rep = run_kit(Setup(spec=spec, wrap=A.wrap, terms=TERMS, mirror_terms={**TERMS, "side": "receive"}, world=flat_swap_world("cal", holidays=HOL), birth=dt.date(2023, 5, 15),
                        t0=dt.date(2024, 5, 20), t1=t1, payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23))))
    sweep = [r for r in rep.rows if r.check == "cash_sweep"]
    bad += (not rep.ok) or not sweep  # without payment_window the cash sweep silently does not run: its rows must be there
    print(f"{'ok ' if rep.ok and sweep else 'BAD'} {label:<32} rows {len(rep.rows)}  cash_sweep rows {len(sweep)}  failures {[(r.check, r.quantity) for r in rep.failures()][:3]}")
sys.exit(1 if bad else 0)
```

```
ok  fine step (t1 = t0 + 1 day)      rows 25  cash_sweep rows 4  failures []
ok  coarse step (t1 = t0 + 9 days)   rows 25  cash_sweep rows 4  failures []
```

**2. `cash_window_check.py`**, because that kit run CANNOT fail on the wrong amount: the kit's `flat_swap_world` publishes exactly the fixings the old curve implies, so a `cash` read from the old market equals the
right one there (executed: the same kit run passed on the example service with its cash read from the old market, fine step and coarse step alike). The script uses a world whose published fixings from 2024-05-15 on are 5 percent
while every curve stays at 4 percent, a 3Y payer effective 2023-05-17 (its flow of 2024-05-21 needs the fixings of 05-15 and 05-16, published on 05-16 and 05-17), and compares the `cash` of your bound `value`
with the reference stack's, over a step of two days (nothing published inside) and over a step of fifteen (05-14 to 05-29, both fixings published inside). Change the lines marked `LIB`:

```python
"""cash_window_check.py: the `cash` of a COARSE interval (t1 more than the payment lag after t0) on a world whose realised fixings differ from the ones the t0 curve implies, against the reference stack. Run from the repository root."""
import datetime as dt
import math
import sys

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import evaluate_value
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.pricable import MarkContext
from pricebt.snapshot import FixingsSeries, MarketSnapshot, SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.layer_conformance import flat_swap_world
from pricebt.timeutil import Calendar

# ---- LIB: your adapter package: its `swap` Kit, its `wrap`, its conventions block, the dotted-path prefix its function bindings need ---------------------------------------
import acme_adapter as ADAPTER                                                                               # LIB
KIT, WRAP, ALLOW = ADAPTER.swap, ADAPTER.wrap, ("pricebt", "acme_adapter")                                   # LIB
CONV = {**ADAPTER.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}                                               # LIB
# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
CAL, FIX_FROM = Calendar(HOL), dt.date(2024, 5, 15)  # from FIX_FROM on the PUBLISHED overnight fixings are 5 %, the curve (and every fixing it implies) stays at 4 %
flat = flat_swap_world("cal", holidays=HOL)


def world(ref):
    s = flat(ref, 0.0)
    days = CAL.business_days(ref - dt.timedelta(days=900), ref - dt.timedelta(days=1))
    fx = tuple(100.0 * (math.exp((0.05 if d >= FIX_FROM else 0.04) * n / 365.0) - 1.0) * 360.0 / n for d in days for n in [(CAL.add_business_days(d, 1) - d).days])
    return MarketSnapshot(s.ts, ref, s.curves, {"fixings": FixingsSeries("fixings", tuple(days), fx, "percent")}, None, s.calendars)


# a 3Y payer effective 2023-05-17: its flow of 2024-05-21 accrues to 2024-05-17, so it needs the fixings of 05-15 and 05-16, published on 05-16 and 05-17
TERMS = {"side": "pay", "effective": dt.date(2023, 5, 17), "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}
SCHEMAS = SchemaRegistry.default()
MINE = build_spec("mine", {"factory": KIT, "conventions": CONV}, schemas=SCHEMAS, allow=ALLOW)
REFERENCE = build_spec("ref", {"factory": R.swap, "conventions": {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}}, schemas=SCHEMAS, allow=("pricebt",))


def cash(spec, wrap, t0, t1):
    p0, p1 = wrap(SnapshotPricer(world(t0))), wrap(SnapshotPricer(world(t1)))
    built = TradeTemplate("t", spec, TERMS).build(p0, p0.ts)
    ctx = MarkContext.standalone(p1, prev=p0)
    return evaluate_value(spec, Env(pricer=p1, ctx=ctx, instrument=built.obj, terms=built.terms, state={})).cash


bad = 0
for label, t0, t1 in (("fine step, payment 05-21 inside", dt.date(2024, 5, 20), dt.date(2024, 5, 22)), ("coarse step, its fixings are published inside", dt.date(2024, 5, 14), dt.date(2024, 5, 29))):
    mine, ref = cash(MINE, WRAP, t0, t1), cash(REFERENCE, R.wrap, t0, t1)
    ok = abs(ref) > 1000 and abs(mine - ref) < 1e-6 * abs(ref)  # the flow really is inside the interval (a cash of 0 compares nothing), and the two stacks book the same amount
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {label:<46} t0 {t0} t1 {t1}  cash {mine:12.4f}  reference {ref:12.4f}  |diff| {abs(mine - ref):.1e}")
sys.exit(1 if bad else 0)
```

Output for the worked example (exit 0; the example service gives the same two rows):

```
ok  fine step, payment 05-21 inside                t0 2024-05-20 t1 2024-05-22  cash    6304.2754  reference    6304.2754  |diff| 0.0e+00
ok  coarse step, its fixings are published inside  t0 2024-05-14 t1 2024-05-29  cash    6304.2754  reference    6304.2754  |diff| 0.0e+00
```

Mutant, executed on the example service: its cash query answered from the OLD market (`CASH_TO_DATE` on the t0 market as of t1, no rebuilt world) prints `ok` on the fine row and
`BAD ... cash 5163.5046  reference 6304.2754  |diff| 1.1e+03` on the coarse row, exit 1. A `cash` of 0 on both sides would compare nothing: the script requires `abs(reference) > 1000`.
