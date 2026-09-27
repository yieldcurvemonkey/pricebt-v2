# Executed examples for the binding grammar

Every script below was run from the repository root with `PYTHONPATH=src;tests` (section 3 adds the worked example's directory) and printed exactly the output shown. Re-run them after any
change to `src/pricebt/contracts/binding.py` or `spec.py`: if the output differs, the output is right and this file is stale.

## 1. The tour: every target kind, every reference root, post-processing

```python
from types import SimpleNamespace

import pandas as pd

from pricebt.contracts.binding import Env, call_binding, parse_binding
from pricebt.pricable import Valuation


class Lib:                                    # a foreign object: no name matches a schema name
    level = 7.0

    def px(self, *args, **kwargs):            # records what it receives, returns a DECIMAL
        self.seen = (args, kwargs)
        return 0.0425

    def lad(self):                            # a pandas Series with lower-case labels
        return pd.Series({"2y": 1.0, "10Y": 2.5})

    def dct(self):
        return {"2Y": 1.0, "10Y": 2.5}

    def val(self, *, ctx):
        return Valuation(ctx.ts, 10.0, 1.0)


lib = Lib()
pricer = SimpleNamespace(curve="CURVE", npv=lambda inst, scale=1.0: inst.level * scale)
ctx = SimpleNamespace(ts=pd.Timestamp("2024-06-12"), prev_ts=pd.Timestamp("2024-06-11"))
env = Env(pricer=pricer, ctx=ctx, instrument=lib, terms={"notional": 5e6, "direction": -1}, state={"n": 2})


def run(raw, name="x"):
    return call_binding(parse_binding(name, raw), env)


# 1. targets and references: the callable receives EXACTLY what is written
print(run({"target": {"method": "px"}, "args": ["@terms.direction"], "kwargs": {"curve": "@pricer.curve", "when": "@ctx.ts", "n": "@state.n"}}), lib.seen)
print(run({"target": {"method": "px"}, "kwargs": {"size": "@terms.notional", "tag": "@@literal"}}), lib.seen)
print(run({"target": {"pricer_method": "npv"}, "args": ["@instrument"], "kwargs": {"scale": 2.0}}))
print(run({"target": {"function": "pricebt.testing.toys:years_between"}, "args": ["@ctx.prev_ts", "@ctx.ts"]}))
print(run({"target": {"attribute": "level"}}))
# 2. post-processing is sign * (scale * reduce(x) + offset)
print(run({"target": {"method": "px"}, "scale": 100.0}))
print(run({"target": {"method": "px"}, "scale": 2.0, "offset": 1.0, "sign": -1.0}))
print(run({"target": {"method": "lad"}, "reduce": "series_to_tenor_dict", "scale": 10.0, "sign": -1.0}))
print(run({"target": {"method": "dct"}, "reduce": "pricebt.contracts.evaluate:scalar_of"}, "dv01"))   # a dotted reducer (allow-listed prefix)
# 3. what sign and scale cannot do
for raw in ({"sign": -1.0}, {"scale": 2.0}):
    try:
        run({"target": {"method": "val"}, "kwargs": {"ctx": "@ctx"}, **raw}, "value")
    except Exception as e:
        print(type(e).__name__, e)
```

```
0.0425 ((-1,), {'curve': 'CURVE', 'when': Timestamp('2024-06-12 00:00:00'), 'n': 2})
0.0425 ((), {'size': 5000000.0, 'tag': '@literal'})
14.0
0.0027378507871321013
7.0
4.25
-1.085
{'2Y': -10.0, '10Y': -25.0}
3.5
MethodCallError binding 'value': post-processing (reduce=None, scale=1.0, offset=0.0, sign=-1.0) failed: TypeError: unsupported operand type(s) for *: 'float' and 'Valuation'
MethodCallError binding 'value': post-processing (reduce=None, scale=2.0, offset=0.0, sign=1.0) failed: TypeError: unsupported operand type(s) for *: 'float' and 'Valuation'
```

Reading it: line 1 shows the positional `@terms.direction` and the three keyword references arrive resolved; line 2 shows `@@literal` arrives as the string `@literal` and `@terms.notional` as the
RESOLVED notional; `4.25` is a decimal turned into a percent by `scale: 100`; `-1.085` is `-1 * (2 * 0.0425 + 1)`; the ladder Series came back as upper-case tenor keys, scaled and negated per bucket.
A `Valuation` cannot go through `sign` or `scale`, so bind `value` without them and convert pv and cash in your method.

## 2. Strict mode, the did-you-mean errors, and the two merge rules

`tests/guards/zero_toys.py` is a swap whose method names CROSS the schema's (its `convexity` method is the schema's `gamma`, its `gamma` method is the schema's `convexity` layer) and whose
schema-named methods are decoys that raise if reached. It loads on its default block alone.

```python
import copy

from guards import zero_toys as Z            # tests/guards/zero_toys.py: a swap whose names cross the schema's
from pricebt.config.stacks import apply_stack
from pricebt.contracts.binding import register_reducer
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Kit, build_spec
from pricebt.testing import refstack as R

S = SchemaRegistry.default()
CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "wk"}


def load(bind, cls=Z.Mismatched, extra=None):
    kit = Kit(factory=Z.mismatched_factory, asset_class="swap", default_bind=bind, cls=cls, extra=extra or {})
    return build_spec("ois", {"factory": kit, "conventions": CONV}, schemas=S)


def fails(title, fn):
    try:
        fn()
        print(title, "-> loaded")
    except Exception as e:
        print(title, "->", type(e).__name__, e)


# 1. strict mode: the toy loads on its default block ALONE, although its names cross the schema's
print(sorted(load(Z.MISMATCHED_BIND).bindings))
# 2. every way the block can be wrong is a load error that says what to do
fails("gamma unbound", lambda: load({k: v for k, v in Z.MISMATCHED_BIND.items() if k != "gamma"}))
fails("unknown name", lambda: load({**Z.MISMATCHED_BIND, "vega": {"target": {"method": "price_it"}}}))
fails("derived name", lambda: load({**Z.MISMATCHED_BIND, "pv": {"target": {"method": "price_it"}}}))
fails("misspelt method", lambda: load({**Z.MISMATCHED_BIND, "rate": {"target": {"method": "level_off"}, "kwargs": {"zzz": "@ctx"}}}))
fails("extension without Kit.extra", lambda: load({**Z.MISMATCHED_BIND, "zero_dv01": {"target": {"method": "level_of"}, "kwargs": {"zzz": "@ctx"}}}))
print("extension with Kit.extra ->", "zero_dv01" in load({**Z.MISMATCHED_BIND, "zero_dv01": {"target": {"method": "level_of"}, "kwargs": {"zzz": "@ctx"}}}, extra={"zero_dv01": "measure"}).bindings)
print("cls=None disables the method check ->", "rate" in load({**Z.MISMATCHED_BIND, "rate": {"target": {"method": "level_off"}, "kwargs": {"zzz": "@ctx"}}}, cls=None).bindings)
# 3. two merge rules
raw = {"factory": R.swap, "conventions": CONV, "bind": {"rate": {"target": {"method": "rate"}, "kwargs": {"ctx": "@ctx"}, "scale": 0.5}}}
spec = build_spec("s", raw, schemas=S)
print("Kit default + ONE user binding, merged BY NAME ->", len(spec.bindings), "bindings, rate scale", spec.bindings["rate"].scale, "| dv01 kept", "dv01" in spec.bindings)
base = {"instruments": {"ois": {"factory": "pkg:a", "bind": {"rate": {"target": {"method": "r"}}, "dv01": {"target": {"method": "d"}}}}}}
overlay = {"instruments": {"ois": {"factory": "pkg:b", "bind": {"rate": {"target": {"method": "r2"}}}}}}
print("overlay `bind` REPLACES the base's whole block ->", sorted(apply_stack(base, overlay)["instruments"]["ois"]["bind"]))
# 4. a reducer name is registered once per function
register_reducer("my_lib_ladder", lambda s: dict(s))
fails("same name, other function", lambda: register_reducer("my_lib_ladder", lambda s: dict(s)))
```

```
['carry', 'convexity', 'delta', 'delta_ladder', 'dv01', 'gamma', 'rate', 'roll', 'value']
gamma unbound -> ConfigError [CFG-REQUIRED-BINDING] instruments.ois.bind: instrument 'ois': required names of asset class 'swap' are not bound: ['gamma'] (gamma: did you mean ['convexity', 'gamma']?). Bind each in `bind:` (pricebt never resolves a schema name by a same-named method)
unknown name -> ConfigError [CFG-UNKNOWN-BINDING] instruments.ois.bind.vega: instrument 'ois': binding 'vega' is not a name of asset class 'swap'; bindable: ['carry', 'convexity', 'delta', 'delta_ladder', 'dv01', 'gamma', 'rate', 'roll', 'value']
derived name -> ConfigError [CFG-UNKNOWN-BINDING] instruments.ois.bind.pv: instrument 'ois': 'pv' is derived by pricebt (value.pv) and takes no binding
misspelt method -> ConfigError [CFG-BINDING] instruments.ois.bind.rate: instrument 'ois': binding 'rate': Mismatched has no method 'level_off'; it has ['buckets', 'carry', 'convexity', 'delta', 'delta_ladder', 'dv01', 'gamma', 'lay_a', 'lay_b', 'lay_c', 'level_of', 'one_bp', 'price_it', 'rate', 'roll', 'value']; did you mean ['level_of']?
extension without Kit.extra -> ConfigError [CFG-UNKNOWN-BINDING] instruments.ois.bind.zero_dv01: instrument 'ois': binding 'zero_dv01' is not a name of asset class 'swap'; bindable: ['carry', 'convexity', 'delta', 'delta_ladder', 'dv01', 'gamma', 'rate', 'roll', 'value']; did you mean ['dv01']?
extension with Kit.extra -> True
cls=None disables the method check -> True
Kit default + ONE user binding, merged BY NAME -> 11 bindings, rate scale 0.5 | dv01 kept True
overlay `bind` REPLACES the base's whole block -> ['rate']
same name, other function -> ConfigError [CFG-REDUCER] reducer 'my_lib_ladder' is already registered
```

Note the hint `gamma: did you mean ['convexity', 'gamma']`: `convexity` is the class's real gamma (a schema synonym), `gamma` is the decoy of the same name. A hint is a lead, never the answer.
(The 11 in "11 bindings" is the reference stack's nine schema names plus its two extension measures; do not copy the number.)

## 3. Overriding ONE binding from config, and what the loader says when it is wrong

The stack overlay of the worked example (`skills/pricebt-wire-external-library/example/config/acme_swap.yaml`) plus one `bind:` entry. Save the YAML below as `override_rate.yaml` anywhere.

```yaml
instruments:
  usd_sofr_ois:
    factory: "acme_adapter:swap"
    bind:
      rate: {target: {method: rate}, kwargs: {ctx: "@ctx"}, scale: 100.0}     # replaces the Kit's `rate`; every other default stays
market:
  pricers:
    primary: {wrap: "acme_adapter:wrap"}
```

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example"
$ex = "skills/pricebt-wire-external-library/example"
python -m pricebt validate $ex/config/acme_tieout_base.yaml --stack override_rate.yaml
```

| overlay | printed | exit code |
|---|---|---|
| the YAML above | `OK` | 0 |
| `rate: {target: {method: rat}, ...}` | `error: [CFG-BINDING] instruments.usd_sofr_ois.bind.rate: instrument 'usd_sofr_ois': binding 'rate': AcmeSwap has no method 'rat'; it has ['acme_zero_dv01', 'delta_ladder', 'dv01', 'expired', 'gamma', 'rate', 'started', 'value']; did you mean ['rate']?` | 2 |
| `rate: {target: {method: rate}, kwargs: {context: "@ctx"}, ...}` | `error: [CFG-BINDING] instruments.usd_sofr_ois.bind.rate: instrument 'usd_sofr_ois': binding 'rate': 'rate'(*, ctx: 'Any') -> 'float' cannot take args=0 kwargs=['context']: missing a required keyword-only argument: 'ctx'` | 2 |
| an overlay that adds `registry: {allow: [acme_adapter]}` | `error: [CFG-STACK] registry: a stack may not set 'registry': only instruments.<name>.factory / .bind and market.pricers.<role>.wrap (everything else is shared by every stack)` | 2 |

The unit of a binding is part of what a tie-out compares: an overlay whose `rate` binding forgets `scale: 100` is the negative control `config/mistakes/acme_swap_no_percent.yaml` of the example.

## 4. Load-time validation has limits; a module that registers a reducer cannot be reloaded

```python
import importlib
import pathlib
import sys
import tempfile
from types import SimpleNamespace

from pricebt.contracts.binding import Env, call_binding, parse_binding
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import Built, Kit, build_spec
from pricebt.testing import refstack as R

S = SchemaRegistry.default()
CONV = {**R.USD_SOFR_OIS_CONVENTIONS, "calendar": "wk"}


class Foreign:                                    # FIXED signatures (no **kwargs), so load-time validation can see them
    def npv(self, *, curve):
        return 1.0

    def bump(self, *, curve, bp):
        return 2.0


def entry(**kw):
    return {"target": {"method": "bump"}, "kwargs": {"curve": "@pricer.curve", "bp": 1}, **kw}


BLOCK = {"value": {"target": {"method": "npv"}, "kwargs": {"curve": "@pricer.curve"}},
         **{n: entry() for n in ("dv01", "gamma", "rate", "delta_ladder", "carry", "roll", "delta", "convexity")}}


def load(bind, cls=Foreign):
    kit = Kit(factory=lambda p, t, *, terms, conventions: Built(Foreign(), {}), asset_class="swap", default_bind={**BLOCK, **bind}, cls=cls)
    return build_spec("x", {"factory": kit, "conventions": CONV}, schemas=S)


def report(title, fn):
    try:
        print(title, "->", fn())
    except Exception as e:
        print(title, "->", type(e).__name__, e)


# 1. load-time validation: for `function` targets, and for `method` targets of a Kit that has `cls`
report("method, kwarg the method cannot take", lambda: load({"rate": entry(kwargs={"curve": "@pricer.curve", "bps": 1})}))
report("method, positional args for keyword-only", lambda: load({"rate": entry(args=["@pricer.curve"], kwargs={"bp": 1})}))
report("pricer_method to a name nothing has", lambda: "loads: " + str("rate" in load({"rate": {"target": {"pricer_method": "no_such"}}}).bindings))
report("method with cls=None", lambda: "loads: " + str("rate" in load({"rate": {"target": {"method": "no_such"}}}, cls=None).bindings))
env = Env(pricer=SimpleNamespace(curve="C"), ctx=None, instrument=Foreign(), terms={}, state={})
report("...and at the first call", lambda: call_binding(parse_binding("rate", {"target": {"pricer_method": "no_such"}}), env))

# 2. a module that registers a reducer cannot be reloaded
folder = pathlib.Path(tempfile.mkdtemp())
(folder / "regmod.py").write_text("from pricebt.contracts.binding import register_reducer\n\ndef r(x):\n    return x\n\nregister_reducer('regmod_red', r)\n", encoding="utf8")
sys.path.insert(0, str(folder))
import regmod
report("importlib.reload", lambda: importlib.reload(regmod))
```

```
method, kwarg the method cannot take -> ConfigError [CFG-BINDING] instruments.x.bind.rate: instrument 'x': binding 'rate': 'bump'(*, curve, bp) cannot take args=0 kwargs=['bps', 'curve']: missing a required argument: 'bp'
method, positional args for keyword-only -> ConfigError [CFG-BINDING] instruments.x.bind.rate: instrument 'x': binding 'rate': 'bump'(*, curve, bp) cannot take args=1 kwargs=['bp']: too many positional arguments
pricer_method to a name nothing has -> loads: True
method with cls=None -> loads: True
...and at the first call -> MethodCallError binding 'rate': pricer SimpleNamespace has no pricer method 'no_such'; public names: ['curve']
importlib.reload -> ConfigError [CFG-REDUCER] reducer 'regmod_red' is already registered
```

Consequence: a binding to a `pricer_method` or `attribute`, or a `method` on a Kit without `cls`, is only proved by CALLING it (a `function` target is validated at load). Run every default binding at a real mark (`skills/pricebt-map-library-to-schemas/references/conformance-snippet.md`).
