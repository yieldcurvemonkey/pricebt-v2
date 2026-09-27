# Proving the mapping: the default block conforms, every binding runs, the known answers hold

Two scripts, both executed from the repository root with `PYTHONPATH=src;tests` (plus your adapter's directory when you check your own kit). As printed they run against the reference stack and need none of your code
(do that first, to see correct output). To check YOUR mapping, substitute your kit, `wrap`, conventions and a snapshot "world" for the reference stack's: that needs your `wrap` and your Kit, which exist after phase 6 of
`pricebt-wire-external-library`. A world is `world(reference_date, shock_bp) -> MarketSnapshot`; `flat_swap_world` and `flat_bond_world` (`pricebt.testing.layer_conformance`, library-free) are
the ones the shipped adapters use, so YOUR adapter meets exactly the snapshots the reference stack meets.

## 1. `check_default_block`: the Kit conforms with ONLY its default block, and every binding returns its declared type

What it proves, in order: `build_spec` (strict: every required name bound, every method and signature checked, function targets allow-listed); `TradeTemplate.build` (the factory returns concrete
resolved terms); then EVERY binding is called at a real mark through pricebt's own evaluators (a ladder is checked against its declared `keys`, a measure must be a finite float, a layer a finite float).
It also covers what `build_spec` cannot: `pricer_method` and `attribute` targets, and `method` targets of a Kit without `cls`, are only proved by being called (`function` targets are validated at load).

```python
import datetime as dt
import math

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import evaluate_layer, evaluate_measure, evaluate_value, is_vector
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Kit, TradeTemplate, build_spec
from pricebt.pricable import MarkContext, Valuation
from pricebt.snapshot import Security, SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.layer_conformance import flat_bond_world, flat_swap_world

SCHEMAS = SchemaRegistry.default()
HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))


def check_default_block(kit, wrap, conventions, terms, world, t0, t1, allow=("pricebt",)):
    """Build the spec from the kit's DEFAULT block alone, then call every binding at a real mark and check what pricebt checks. Returns {name: value}."""
    spec = build_spec("probe", {"factory": kit, "conventions": conventions}, schemas=SCHEMAS, allow=allow)   # strict: every required name bound, every method/signature checked
    p0, p1 = wrap(SnapshotPricer(world(t0, 0.0))), wrap(SnapshotPricer(world(t1, 0.0)))                        # two snapshots: a previous mark and this one
    built = TradeTemplate("t", spec, terms).build(p0, p0.ts)                                                   # resolved terms are checked here (concrete dates, a number for `par`)
    ctx = MarkContext.standalone(p1, prev=p0)
    env = Env(pricer=p1, ctx=ctx, instrument=built.obj, terms=built.terms, state={})
    out = {"value": evaluate_value(spec, env)}
    assert isinstance(out["value"], Valuation) or math.isfinite(float(out["value"])), "value"
    for name in spec.bindings:
        if name == "value":
            continue
        out[name] = evaluate_layer(spec, name, env) if name in spec.schema.layers else evaluate_measure(spec, name, env)
        ms = spec.schema.measures.get(name)
        if ms is not None and is_vector(ms):
            assert isinstance(out[name], dict) and list(out[name]) == list(spec.bindings[name].keys or out[name]), name
        else:
            assert isinstance(out[name], float) and math.isfinite(out[name]), name
    return out


swap_terms = {"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2}
out = check_default_block(R.swap, R.wrap, {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}, swap_terms, flat_swap_world("cal", holidays=HOL), dt.date(2024, 3, 4), dt.date(2024, 3, 5))
print("swap:", {k: round(v, 4) for k, v in out.items() if isinstance(v, float)}, "| ladder buckets:", len(out["delta_ladder"]))

sec = Security("KIT001", 4.0, dt.date(2023, 11, 15), dt.date(2033, 11, 15))
out = check_default_block(R.bond, R.wrap, {**R.UST_CONVENTIONS, "calendar": "cal"}, {"side": "buy", "security": sec.id, "notional": 1e7}, flat_bond_world(sec, "cal", holidays=HOL), dt.date(2024, 6, 3), dt.date(2024, 6, 4))
print("bond:", {k: round(v, 4) for k, v in out.items() if isinstance(v, float)})

# negative controls: the check must be able to fail (each is a real mistake)
bad = Kit(factory=R.swap.factory, asset_class="swap", default_bind={k: v for k, v in R.SWAP_BIND.items() if k != "gamma"}, cls=R.RefSwap, extra=R.swap.extra)
try:
    check_default_block(bad, R.wrap, {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}, swap_terms, flat_swap_world("cal", holidays=HOL), dt.date(2024, 3, 4), dt.date(2024, 3, 5))
except Exception as e:
    print("gamma left unbound ->", type(e).__name__, str(e)[:150], "...")
bad = Kit(factory=R.swap.factory, asset_class="swap", default_bind={**R.SWAP_BIND, "delta_ladder": {"target": {"method": "delta_ladder"}, "kwargs": {"ctx": "@ctx", "tenors": ["2Y", "10Y"]}, "reduce": "float"}}, cls=R.RefSwap, extra=R.swap.extra)
try:
    check_default_block(bad, R.wrap, {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}, swap_terms, flat_swap_world("cal", holidays=HOL), dt.date(2024, 3, 4), dt.date(2024, 3, 5))
except Exception as e:
    print("ladder through a scalar reducer ->", type(e).__name__, str(e))
```

Output:

```
swap: {'dv01': 4503.6683, 'gamma': -2.5829, 'rate': 4.0252, 'carry': -8.6259, 'roll': 0.0, 'delta': 0.0, 'convexity': 0.0, 'dv01_zero': 4646.4447, 'gamma_zero': -2.2571} | ladder buckets: 11
bond: {'dv01': -7454.312, 'gamma': 6.8125, 'rate': 4.5, 'ytm': 4.5, 'duration': 7.7327, 'accrued': 0.2174, 'carry': 1155.4886, 'roll': -0.0, 'delta': -0.0, 'convexity': 0.0}
gamma left unbound -> ConfigError [CFG-REQUIRED-BINDING] instruments.probe.bind: instrument 'probe': required names of asset class 'swap' are not bound: ['gamma'] (gamma: did you mean  ...
ladder through a scalar reducer -> MeasureError binding 'delta_ladder': post-processing (reduce='float', scale=1.0, offset=0.0, sign=1.0) failed: TypeError: float() argument must be a string or a real number, not 'dict'
```

Read the signs against the cheat-sheet: the payer's `dv01` is positive, the long bond's negative; `rate` is percent (4.03, 4.5), never 0.04. The extra keys `dv01_zero` and `gamma_zero` are the reference stack's
extension measures (declared in `Kit.extra`).

For a kit with `function` targets outside `pricebt` (the worked example's layers) pass `allow=("pricebt", "<your_pkg>")`. Without it the FIRST line of the failure is
`[CFG-ALLOW] bind.carry.target.function: [CFG-ALLOW] module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']`.

## 2. Known answers first: the checks that need no second library

A checking tool that has never failed proves nothing, and a number with no known answer proves nothing either. These three hold for ANY correct adapter on a flat world:

```python
import datetime as dt

from pricebt.contracts.binding import Env
from pricebt.contracts.evaluate import evaluate_measure, evaluate_value
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import TradeTemplate, build_spec
from pricebt.pricable import MarkContext
from pricebt.snapshot import SnapshotPricer
from pricebt.testing import refstack as R
from pricebt.testing.layer_conformance import flat_swap_world

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
spec = build_spec("s", {"factory": R.swap, "conventions": {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}}, schemas=SchemaRegistry.default())
p = R.wrap(SnapshotPricer(flat_swap_world("cal", holidays=HOL)(dt.date(2024, 3, 4), 0.0)))


def mark(**terms):
    built = TradeTemplate("t", spec, {"maturity": "5Y", "notional": 1e7, **terms}).build(p, p.ts)
    env = Env(pricer=p, ctx=MarkContext.standalone(p), instrument=built.obj, terms=built.terms, state={})
    return built, evaluate_value(spec, env).pv, evaluate_measure(spec, "dv01", env), evaluate_measure(spec, "rate", env)


par, pv, dv01, rate = mark(side="pay", fixed_rate="par")
print("par swap: pv", round(pv, 9), "| resolved fixed_rate == rate:", abs(par.terms["fixed_rate"] - rate) < 1e-9, "| resolved effective:", par.terms["effective"])
_, pv_p, dv_p, _ = mark(side="pay", fixed_rate=4.2)
_, pv_r, dv_r, _ = mark(side="receive", fixed_rate=4.2)
print("payer above par:", round(pv_p, 2), round(dv_p, 2), "| mirror sum:", round(pv_p + pv_r, 9), round(dv_p + dv_r, 9))
print("percent, not decimal: rate", round(rate, 4), "(a decimal library must be scaled by 100)")
```

```
par swap: pv 0.0 | resolved fixed_rate == rate: True | resolved effective: 2024-03-06
payer above par: -78706.98 4503.17 | mirror sum: 0.0 0.0
percent, not decimal: rate 4.0252 (a decimal library must be scaled by 100)
```

1. A swap struck at `par` is worth zero at the pricer that filled it, and its resolved `fixed_rate` equals its own `rate` measure (the factory resolved `par` in percent).
2. A payer struck ABOVE par has a negative value and a POSITIVE `dv01` (it pays too much and gains when rates rise): the sign convention in one line.
3. The mirror trade (`side: receive`) has exactly the opposite `pv` and `dv01`, with everything else equal (`gamma` and each layer too: the layer kit checks them).

The full identities (a parallel shock equals `delta + convexity` by revaluation, cash-sweep continuity across a payment date, a bounded `unexplained`) are the layer conformance kit: skill
`pricebt-conformance-and-tieout`. The tie-out against the reference stack then compares every quantity level by level.
