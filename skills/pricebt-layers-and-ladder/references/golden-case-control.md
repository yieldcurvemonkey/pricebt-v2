# The golden case as a control, and the step experiment

A checking tool that is itself wrong reports success and hides what it was built to find. The golden case is the KNOWN ANSWER for the layers: a seasoned 3-year payer of 50mm
(`docs/DESIGN.md` section 7, `docs/design/adr/005-attribution.md`), two solver-fitted curves, seeded fixings and holidays frozen as plain numbers in `tests/data_golden_swap.json`
(so the case needs no library), coupon paid inside `[2025-01-02, 2025-01-16)`. Two independent implementations produced the numbers; `tests/test_refstack_golden.py` pins the reference stack to them.
Run your adapter through the same world and demand the same numbers.

| quantity | value | note |
|---|---|---|
| `V0` (2025-01-02) | -369,827.43 | payer, holder-signed |
| `V1` (2025-01-16) | -212,114.86 | |
| `cash` in `[t0, t1)` | -86,490.36 | the coupon paid in the window |
| total `V1 - V0 + cash` | 71,222.20 | |
| `carry` | -561.82 | forwards realised in date space, including the flow paid in the window |
| `roll` | 1,532.07 | folded: 12,159.98 tenor-space roll-down plus -10,627.91 realised-versus-assumed fixings |
| `delta` | 70,302.17 | folded: 70,190.64 first directional derivative plus 111.52 resampling onto the new curve's nodes. ADR 005 and `docs/DESIGN.md` section 7 say 70,302.17; unrounded it is 70302.165974 (`PRECISE` in `tests/test_refstack_precise.py`); the older tests assert 70,302.16 within 0.01 to 0.02, hence the script's tolerance of 0.011 |
| `convexity` | -50.23 | |
| remainder (the engine's `unexplained`) | 0.02 | `(V1 - V_base) - g.dz - 1/2 dz'H dz` |

"Raw" and "folded" are two bookkeepings of the same P&L: an adapter that reports the RAW pair (roll 12,159.98, delta 70,190.64) is not wrong arithmetic, it is a different definition and will
fail L3 against every other stack. ADR 005 fixes the folded one.

## The control script (executed; both the worked-example adapter and the reference stack print `ok` on every row)

The block goes through the adapter the way the ENGINE does: a spec built from the adapter's Kit, the neutral terms, `evaluate_value` and `evaluate_layer` with the bindings' `sign` and
`scale` applied. So it checks the bindings too, not only the arithmetic. Change the two lines marked `LIB`.

```python
"""golden_control.py: the seasoned 3y payer of docs/DESIGN.md section 7, through the BOUND layers of an adapter (the way the engine calls them). Run from the repository root."""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import evaluate_layer, evaluate_value
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.pricable import MarkContext, as_valuation
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer

# ---- LIB: your adapter package: its `swap` Kit, its `wrap`, its conventions block, the dotted-path prefix its function bindings need --------------------------------------
import acme_adapter as ADAPTER                                                                               # LIB
KIT, WRAP, CONV, ALLOW = ADAPTER.swap, ADAPTER.wrap, {**ADAPTER.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}, ("pricebt", "acme_adapter")   # LIB
# ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

GOLD = {"V0": -369827.43, "V1": -212114.86, "cash": -86490.36, "carry": -561.82, "roll": 1532.07, "delta": 70302.17, "convexity": -50.23, "residual": 0.02, "total": 71222.20}
DATA = json.loads(Path("tests/data_golden_swap.json").read_text(encoding="utf8"))  # two solver-fitted curves, seeded fixings and holidays, frozen as plain numbers
iso = dt.date.fromisoformat


def pricer(ref, key):
    c = DATA[key]
    curve = CurveSnapshot("c", ref, tuple(iso(d) for d in c["dates"]), tuple(c["dfs"]))
    fx = [(iso(d), v) for d, v in zip(DATA["fixings"]["dates"], DATA["fixings"]["percent"]) if iso(d) < ref]  # only fixings strictly before the reference date
    fixings = FixingsSeries("f", tuple(d for d, _ in fx), tuple(v for _, v in fx), "percent")
    hol = CalendarData("nyc", tuple(iso(h) for h in DATA["holidays"]))
    return WRAP(SnapshotPricer(MarketSnapshot(pd.Timestamp(f"{ref} 17:00", tz="America/New_York"), ref, {"c": curve}, {"f": fixings}, None, {"nyc": hol})))


d0, d1 = dt.date(2025, 1, 2), dt.date(2025, 1, 16)
p0, p1 = pricer(d0, "c0"), pricer(d1, "c1")
spec = build_spec("golden", {"factory": KIT, "conventions": CONV}, schemas=SchemaRegistry.default(), allow=ALLOW)
terms = {"side": "pay", "effective": dt.date(2024, 1, 8), "maturity": dt.date(2027, 1, 8), "notional": 50e6, "fixed_rate": 4.20}  # NEUTRAL terms: percent, unsigned notional, side
built = TradeTemplate("golden", spec, terms).build(p0, p0.ts)  # struck at the t0 pricer, exactly as the engine does


def ctx(p, prev=None):
    return MarkContext(p.ts, p, {"primary": p}, None if prev is None else prev.ts, prev, p.ts if prev is None else prev.ts, p if prev is None else prev, {}, {})


def env(c):
    return Env(pricer=c.pricer, ctx=c, instrument=built.obj, terms=built.terms, state={})


c0, c1 = ctx(p0), ctx(p1, prev=p0)
v0, v1 = as_valuation(evaluate_value(spec, env(c0)), p0.ts), as_valuation(evaluate_value(spec, env(c1)), p1.ts)
got = {"V0": v0.pv, "V1": v1.pv, "cash": v1.cash}
got.update({n: float(evaluate_layer(spec, n, env(c1))) for n in ("carry", "roll", "delta", "convexity")})
got["total"] = v1.pv - v0.pv + v1.cash + v1.financing
got["residual"] = got["total"] - sum(got[n] for n in ("carry", "roll", "delta", "convexity"))  # what the ENGINE books as `unexplained`
bad = 0
for k, g in GOLD.items():
    ok = abs(got[k] - g) <= 0.011
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {k:<10} {got[k]:>14,.2f}   golden {g:>14,.2f}")
sys.exit(1 if bad else 0)
```

Output for the worked example (`PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example"`, run from the repository root):

```
ok  V0            -369,827.43   golden    -369,827.43
ok  V1            -212,114.86   golden    -212,114.86
ok  cash           -86,490.36   golden     -86,490.36
ok  carry             -561.82   golden        -561.82
ok  roll             1,532.07   golden       1,532.07
ok  delta           70,302.17   golden      70,302.17
ok  convexity         -50.23   golden         -50.23
ok  residual             0.02   golden           0.02
ok  total           71,222.20   golden      71,222.20
```

The same block with `import pricebt.testing.refstack as ADAPTER` and `KIT, WRAP, CONV, ALLOW = ADAPTER.swap, ADAPTER.wrap, {...ADAPTER.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}, ("pricebt",)`
prints the same nine rows: that is your second known answer, an implementation that shares no code with yours.

**The control has teeth.** Two mutants of the Kit's default bindings were run through it: the `carry` binding with `sign: 1.0` instead of `-1.0` printed `BAD carry 561.82` and
`BAD residual -1,123.62`; the `delta` binding with `scale: 0.01` printed `BAD delta 703.02` and `BAD residual 69,599.17`. The residual row is computed against the KNOWN total, so a wrong layer shows in it; the engine's own `unexplained` is
whatever is left over and proves completeness, not correctness (ADR 005, Consequences), which is why the golden numbers are the check.

**What it does not see.** The golden case evaluates the four layers, not the risk measures: with `dv01`, `gamma` or `delta_ladder` left without their `sign` the script still prints nine `ok` rows (measured). Those are
covered by `dv01_consistency.py` (`dv01-one-definition.md`: `dv01` and the ladder) and `risk_known_answers.py` (`kit-blind-spots.md`: `dv01` and `gamma`); the table of which check sees which mistake is there.

## The step experiment (section 5 of `layer-definitions-and-assembly.md`)

```python
"""step_experiment.py: the step of the QuantLib adapter's directional derivatives against the reference stack's Richardson remainder, on the golden case. Needs QuantLib."""
import datetime as dt
import importlib
import json
from pathlib import Path

import pandas as pd

import pricebt.contrib.quantlib as Q
from pricebt.pricable import MarkContext
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer
from pricebt.testing import refstack as R

QS = importlib.import_module("pricebt.contrib.quantlib.swap")  # the MODULE: `pricebt.contrib.quantlib.swap` as an attribute is the Kit
DATA = json.loads(Path("tests/data_golden_swap.json").read_text(encoding="utf8"))
iso = dt.date.fromisoformat


def snap(ref, key):
    c = DATA[key]
    curve = CurveSnapshot("c", ref, tuple(iso(d) for d in c["dates"]), tuple(c["dfs"]))
    fx = [(iso(d), v) for d, v in zip(DATA["fixings"]["dates"], DATA["fixings"]["percent"]) if iso(d) < ref]
    fixings = FixingsSeries("f", tuple(d for d, _ in fx), tuple(v for _, v in fx), "percent")
    return SnapshotPricer(MarketSnapshot(pd.Timestamp(f"{ref} 17:00", tz="America/New_York"), ref, {"c": curve}, {"f": fixings}, None, {"nyc": CalendarData("nyc", tuple(iso(h) for h in DATA["holidays"]))}))


def ctx(p, prev):
    return MarkContext(p.ts, p, {"primary": p}, prev.ts, prev, prev.ts, prev, {}, {})


d0, d1 = dt.date(2025, 1, 2), dt.date(2025, 1, 16)
terms = {"side": "pay", "direction": 1, "effective": dt.date(2024, 1, 8), "maturity": dt.date(2027, 1, 8), "notional": 50e6, "fixed_rate": 4.20}
r0, r1 = R.wrap(snap(d0, "c0")), R.wrap(snap(d1, "c1"))
ref = R.swap_factory(r0, r0.ts, terms=terms, conventions={**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}).obj.decomposition(ctx(r1, r0))
again = R.swap_factory(r0, r0.ts, terms=terms, conventions={**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}).obj.decomposition(ctx(r1, r0))
assert ref["residual"] == again["residual"]  # known answer first: the same code gives the same number
print("reference (Richardson, exact): remainder %.8f  delta %.6f  convexity %.6f" % (ref["residual"], ref["delta"], ref["convexity"]))
q0, q1 = Q.wrap(snap(d0, "c0")), Q.wrap(snap(d1, "c1"))
for h in (0.2, 0.1, 0.05, 0.01):
    QS._H = h
    q = Q.swap.factory(q0, q0.ts, terms=terms, conventions={**Q.USD_SOFR_OIS_CONVENTIONS, "calendar": "nyc"}).obj.decomposition(ctx(q1, q0))
    print("h=%-5g remainder %.8f  ratio %.5f  (1-h^2 = %.5f)  delta-ref %+.1e  convexity-ref %+.1e" % (h, q["residual"], q["residual"] / ref["residual"], 1 - h * h, q["delta"] - ref["delta"], q["convexity"] - ref["convexity"]))
```

Output:

```
reference (Richardson, exact): remainder 0.02396611  delta 70302.165974  convexity -50.233472
h=0.2   remainder 0.02300775  ratio 0.96001  (1-h^2 = 0.96000)  delta-ref +9.6e-04  convexity-ref -6.3e-07
h=0.1   remainder 0.02372662  ratio 0.99001  (1-h^2 = 0.99000)  delta-ref +2.4e-04  convexity-ref -3.3e-07
h=0.05  remainder 0.02390638  ratio 0.99751  (1-h^2 = 0.99750)  delta-ref +6.0e-05  convexity-ref -2.4e-07
h=0.01  remainder 0.02392468  ratio 0.99827  (1-h^2 = 0.99990)  delta-ref +2.3e-06  convexity-ref +3.9e-05
```

## When your numbers differ

| symptom | first suspect | look at |
|---|---|---|
| `V0`, `V1` or `cash` differ | calendar or fixings in `wrap`; the cash window `[t0, t1)`; a flow paid ON `t1` (it is in `V1`, not in `cash`) | `pricebt-wrap-and-pricer`, `pricebt-debug-tieout-differences` |
| all four layers flip sign | a receiver's-view library without `sign: -1` on the layer bindings | `pricebt-bindings-cookbook` |
| `carry` right, `roll` off by a fixings-sized amount (thousands) | X_roll uses the t0 fixings instead of the realised t1 fixings (the RAW roll) | section 1 of `layer-definitions-and-assembly.md` |
| `delta` off by about 111 | `V_base` skipped: the rolled curve was not resampled onto the t1 curve's nodes | same |
| `convexity` off | the second derivative is taken along a parallel shift instead of along the realised move `dz` | same |
| all right but the remainder is 0.99 x | a single central difference with `h = 0.1` (declare it, section 5) | `pricebt-conformance-and-tieout` |
