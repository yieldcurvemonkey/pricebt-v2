---
name: pricebt-bindings-cookbook
description: Use when you must connect a pricebt schema name (value, dv01, gamma, rate, delta_ladder, carry, roll, delta, convexity) to a callable of an external library, or when a binding fails to load or run (CFG-BINDING, CFG-REF, CFG-REDUCER, CFG-REQUIRED-BINDING). Gives the binding grammar with executed examples and a binding-versus-code decision table.
---

# pricebt-bindings-cookbook

## Purpose

A binding is DATA that says which callable provides one schema name and exactly what it receives: `{target, args, kwargs, reduce, scale, offset, sign, keys}`.
One function executes every binding (`pricebt.contracts.binding.call_binding`). This skill shows each part of the grammar with executed output, when a binding is
enough and when the conversion must be code in your adapter class, and the pitfalls.

## Prerequisites

- You know the four ideas (schema, binding, snapshot, wrap): skill `pricebt-architecture-and-rules`. You have a filled mapping worksheet (which library callable provides which name): skill `pricebt-map-library-to-schemas`.
- Sources to read: `src/pricebt/contracts/binding.py` (the grammar), `src/pricebt/contracts/spec.py` (`Kit`, `build_spec`), `src/pricebt/contracts/evaluate.py` (what is checked after the call), `docs/design/adr/001-bindings.md`.
- References: all three are optional. `references/binding-templates.md` (ready entries: skip it when you copy `example/acme_adapter/swap.py` or `example-service/zeta_adapter/swap.py`), `references/executed-examples.md` (the grammar's
  executed output: the truth if this text and `binding.py` seem to differ) and `references/error-catalogue.md` (a lookup by message).
- Run from the repository root with `PYTHONPATH=src;tests` (PowerShell: `$env:PYTHONPATH = "src;tests"`; POSIX uses `:`). Python 3.11+ with numpy, pandas, pyyaml, pytest.

## Steps

1. **Pick the target kind.** Exactly one key under `target:`.

   | kind | calls | must exist | choose it when |
   |---|---|---|---|
   | `method: <name>` | `getattr(built_instrument, name)(*args, **kwargs)` | a public method of the object your factory returned (`Built.obj`) | the default: your adapter class owns the method |
   | `function: "pkg.mod:fn"` | the dotted callable (`registry.resolve_dotted`) | module under an allowed prefix (`registry.allow`, BASE config); the object must be DEFINED there (its `__module__`), not imported into it from a library (a `functools.partial` is owned by `functools`); no leading underscore on any segment (all `[CFG-ALLOW]`) | the conversion needs the instrument AND the context, or several calls (the four layers) |
   | `pricer_method: <name>` | `getattr(market_pricer, name)(*args, **kwargs)` | a public method of what your `wrap` returned | the library's pricing call lives on the pricer/service: `npv(instrument)` |
   | `attribute: <name>` | reads `getattr(built_instrument, name)`, never calls it; no args | a public attribute | a stored number |

   A bare string is shorthand for `{target: {method: <string>}}`. Names must be public identifiers: a leading underscore on a `method`, `pricer_method` or `attribute` target is `[CFG-BINDING]`, on a `function` path it is `[CFG-ALLOW]`.

2. **Write the arguments.** The callable receives exactly `args` and `kwargs` as written; nothing is injected by parameter name (guard Z3). Strings that start with `@` are
   references from a CLOSED grammar (no eval); anything else is a literal, also inside lists and mappings.

   | reference | resolves to |
   |---|---|
   | `@pricer`, `@pricer.a.b` | the market pricer at this mark (what `wrap` returned) and its attributes |
   | `@ctx`, `@ctx.<field>` | the `MarkContext`; fields `ts pricer pricers prev_ts prev_pricer entry_ts entry_pricer state cache params` (anything else is `[CFG-REF]`) |
   | `@instrument`, `@instrument.a` | the built object (`Built.obj`) |
   | `@terms.<name>` | the RESOLVED terms of the trade, including `direction` (+1 pay/buy, -1 receive/sell); a bare `@terms` is refused |
   | `@state.<key>` | the position's engine-owned scratch dict; a bare `@state` is refused |
   | `@@x` | the literal string `@x` |

   Path segments start with a letter. A missing key of a mapping (`@terms`, `@state`, `@ctx.params`, `@ctx.cache`) lists the available keys; a missing attribute (`@pricer.x`, `@instrument.x`) names the object's type and the attribute but lists nothing; neither is ever a silent `None`.

3. **Post-process.** The result is `sign * (scale * reduce(x) + offset)`; a dict result is converted value by value. `sign` is +1 or -1, `scale` is a finite non-zero constant, `offset`
   a finite number. `reduce` names a registered reducer or a dotted path to a one-argument function:

   | reducer | does |
   |---|---|
   | `float` | `float(x)`, must be finite |
   | `real` | `float(x.real)`, for dual-number types |
   | `identity` | nothing |
   | `dict_of_floats` | mapping or Series -> `{str: float}`, finite, no duplicate key |
   | `series_to_tenor_dict` | mapping or Series labelled by tenor -> `{'2Y': ...}`: upper-cases, checks `<int><D|W|M|Y>`, refuses duplicates |

4. **Declare the ladder.** `delta_ladder` needs `keys: [3M, 6M, ...]` (unique upper-case tenors), the same tenors in the kwarg your method takes, and a `reduce` if the library returns a
   Series, DataFrame or object. pricebt then requires EXACTLY those keys (`evaluate.check_ladder`); without `keys` it only checks that every key is a tenor.

5. **Assemble the block.** One entry per required name (swap: `value`, `dv01`, `gamma`, `rate`, `delta_ladder`, and the four layers `carry roll delta convexity`; a bond has no `delta_ladder`
   schema name (binding it is `[CFG-UNKNOWN-BINDING]`): it needs `value dv01 gamma rate` plus the four layers and may bind `ytm duration accrued`), as the `default_bind`
   of a `Kit`. Set `cls=<your instrument class>` (load-time method and signature checks need it) and `extra={"<name>": "measure"|"layer"}` for every name beyond the schema. Model:
   `_method`, `_layer`, `SWAP_BIND` and `swap = Kit(...)` in `skills/pricebt-wire-external-library/example/acme_adapter/swap.py`; templates in `references/binding-templates.md`.

6. **Override ONE binding from config.** Add `bind:` with just that name. In a single run put it in the instrument; in a TIE-OUT put a library-specific override (a unit, a sign) in the stack overlay, never in the base
   config: the base is shared by every stack, the reference included, `bind:` there reaches every stack whose overlay has no `bind:`, and `config/stacks.py::base_digest` excludes `bind`, so nothing flags it. Two DIFFERENT merge rules apply:
   the Kit's default block merges with the instrument's `bind:` BY NAME (`spec.build_spec`); a stack overlay's `bind:` REPLACES the base config's whole `bind` block (`config/stacks.py::apply_stack`).
   Check with `python -m pricebt validate <base.yaml> --stack <overlay.yaml>` (prints `OK`, exit 0; a config error prints `error: [CODE] ...`, exit 2). Executed in `references/executed-examples.md` section 3.

7. **Register a library reducer** for a shape pricebt has no reducer for: `register_reducer("<lib>_ladder_to_tenor_dict", fn)` at import of your adapter module (worked example: `ladder_to_tenor_dict` in `acme_adapter/swap.py`);
   convert with the built-ins inside it (`REDUCERS["series_to_tenor_dict"](...)`). The same function may be registered again; a DIFFERENT function under a taken name is `[CFG-REDUCER]`.
   The name only exists once your module is imported, which the `factory: "<pkg>:swap"` path does. A dotted `reduce: "pkg.mod:fn"` needs `registry.allow` instead.

8. **Decide binding or code.**

   | the mismatch | binding | code in the adapter class |
   |---|---|---|
   | decimals vs percent (or any constant unit factor) | `scale: 100` | |
   | opposite sign (library is the receiver's point of view) on a float or a dict | `sign: -1` | |
   | constant shift | `offset` (never on a ladder: it shifts every bucket) | |
   | Series / DataFrame / dual number to the schema's type | `reduce` (registered or dotted) | the reducer function |
   | factor that depends on the trade (per-million risk needs `notional / 1e6`) | | multiply in the method; or pass `notional: "@terms.notional"` if the library function takes it |
   | `value` returns a `Valuation` (pv, cash, financing) | | build it in the method: `sign`/`scale` cannot touch it |
   | the library needs a global set (valuation date) or raises its own exceptions | | wrap every call (`_compat.guard`, `_compat.translate`) |
   | several library calls combine into one number (each layer) | | a `function` target with `@instrument`, `@ctx`, or a method |
   | the call lives on the pricer/service | `pricer_method` + `args: ["@instrument"]` | |
   | a call needs the previous market | `kwargs: {ctx: "@ctx"}` and read `ctx.prev_pricer` | |

   Rule of thumb: one visible, overridable, testable line of config for a CONSTANT conversion; code for anything that reads the trade, the market or a global.

9. **Prove it before trusting it.** Now: run the model (the mismatched-names toy: names cross the schema's, arguments are `zzz`/`qqq`, decoys raise if reached by name):
   `tests/guards/zero_toys.py` (`MISMATCHED_BIND`, `mismatched_swap`) and `tests/test_binding.py`; `parse_binding("<name>", {...})` checks one entry of your worksheet on its own (no `wrap`, no factory needed).
   Later, once phases 5-6 of `pricebt-wire-external-library` have produced your `wrap` and your Kit (factory and class): run your own default block through `build_spec`, and every binding once at a real mark
   (`check_default_block` in `skills/pricebt-map-library-to-schemas/references/conformance-snippet.md`; it needs both).

## Checks

```powershell
$env:PYTHONPATH = "src;tests"
python -m pytest tests/test_binding.py -q -o addopts= -p no:cacheprovider                # expect: all pass, a fraction of a second
python -m pytest tests/guards/test_semantics.py -q -o addopts= -p no:cacheprovider       # expect: all pass (~15 s): the crossed-names toy runs a real backtest with the reference numbers
python tests/guards/blocker.py tests/test_binding.py -q -o addopts= -p no:cacheprovider  # the same with every banned library import made impossible
python -m pricebt validate <base.yaml> --stack <your overlay.yaml>                       # expect: OK (needs your Kit and `registry.allow`: phase 6 of the playbook)
```

Expected outputs of every snippet of the grammar are in `references/executed-examples.md` (run them again after any change of `binding.py`; they are the truth, this text is the guide).

## Common failures

Real messages (full catalogue with causes: `references/error-catalogue.md`).

| message (abridged) | cause | fix |
|---|---|---|
| `[CFG-REQUIRED-BINDING] ... required names of asset class 'swap' are not bound: ['gamma'] (gamma: did you mean ['convexity', 'gamma']?)` | strict mode: nothing is resolved by a same-named method. The hint is drawn from the class's public names and the schema synonyms and may point at a decoy | bind each name in `bind:`; treat "did you mean" as a hint, never as the answer |
| `[CFG-UNKNOWN-BINDING] ... binding 'zero_dv01' is not a name of asset class 'swap'; bindable: [...]` | an extension name missing from `Kit.extra`, or a typo | `extra={"zero_dv01": "measure"}` (identifier form, no dot) |
| `[CFG-UNKNOWN-BINDING] ... 'pv' is derived by pricebt (value.pv) and takes no binding` | `pv` and `notional` are derived | delete the entry |
| `[CFG-BINDING] ... Mismatched has no method 'level_off'; it has [...]; did you mean ['level_of']?` | misspelt target. Checked at load for `function` targets and for `method` targets of a Kit that has `cls`; a `pricer_method` or `attribute` target (and a `method` target when `cls=None`) fails at the first mark | set `cls`; run every binding once (Step 9) |
| `[CFG-BINDING] ... 'bump'(*, curve, bp) cannot take args=0 kwargs=['bps', 'curve']: missing a required argument: 'bp'` | the declared kwargs do not fit the signature (load-time validation, not injection) | fix the kwargs in the binding |
| `[CFG-REF] ... '@curve' is not a valid reference (allowed: @pricer[.attr], @ctx[.ts\|pricer\|...], ...; write @@ for a literal '@')` | reference outside the closed grammar | use a root of step 2, or `@@` for a literal |
| `[CFG-ALLOW] bind.carry.target.function: [CFG-ALLOW] module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']` | a `function` target or dotted `reduce:` outside `pricebt` | [CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow) (`registry.allow` in the BASE); `PricebtSession` cannot resolve it at all: [CE-FACADE](../pricebt-wire-external-library/references/common-errors.md#ce-facade), ship method-only default bindings if the facade must work |
| `[CFG-ALLOW] bind.x.target.function: [CFG-ALLOW] 'pricebt.contracts.binding:pd': 'pd' is owned by 'pandas', which is not under an allowed prefix ['pricebt']` | the `function` target is a library object re-exported by an allowed module (owner = its `__module__`), or a `functools.partial` | define a plain function in your own package that calls the library and point the binding at it |
| `MethodCallError binding 'value': method 'boom' called with args=[] kwargs=['ctx']: object of type 'int' has no len()` | a TypeError raised INSIDE your callable is reported as if the arguments did not fit; the same text arrives as `MeasureError` for a measure or a layer (`evaluate_measure`, `evaluate_layer`) | read the END of the message: [CE-TYPEERROR](../pricebt-wire-external-library/references/common-errors.md#ce-typeerror) |
| `... post-processing (reduce=None, scale=1.0, offset=0.0, sign=-1.0) failed: TypeError: unsupported operand type(s) for *: 'float' and 'Valuation'` | `sign`/`scale` on a `Valuation` (or a tuple) | convert pv and cash in the method; bind `value` without `sign` |
| `[CFG-REDUCER] reducer 'x' is already registered` | a different function under a taken name; `importlib.reload` of the module that registers it does the same | register once; do not reload adapter modules |
| `MeasureError measure 'delta_ladder' must return a dict[str, float] keyed by tenor, got Series (bind a reducer such as series_to_tenor_dict)` | missing `reduce` | add the reducer |

Pitfalls (each one hit or executed in this repository; symbols, not line numbers):

- **What a binding cannot do.** `binding._post` multiplies numbers and dict values; it cannot post-process a `Valuation` or a tuple, and `scale` is a constant that cannot read the trade. New agents try both in the binding first:
  the receiver-to-payer flip of `value` (pv AND cash) and a `notional / 1e6` factor are code. Put `sign` on the risk names and the layers, never on `value`.
- **The TypeError mislabel.** `binding.call_binding` reports any `TypeError` as "called with args=[] kwargs=[...]"; the real cause is the tail of the message ([CE-TYPEERROR](../pricebt-wire-external-library/references/common-errors.md#ce-typeerror)).
- **Reducer names are process-wide.** `binding.register_reducer` refuses a different function under a taken name: never `importlib.reload` an adapter module. `import <pkg>.swap as m` returns the Kit when your package exports an attribute `swap`:
  use `importlib.import_module("<pkg>.swap")` to reach the module (the shipped adapters have the same shadowing).
- **Two merge rules, not stated in ADR 006.** Kit default block + instrument `bind:` merge BY NAME (`spec.build_spec`); a stack overlay's `bind:` REPLACES the base's whole `bind` block (`config.stacks.apply_stack`).
  An overlay that wants to keep a base entry must repeat it.
- **Extension names.** The collision message of `spec.build_spec` advises a namespaced name "for example 'lib.name'", but the shipped kits use identifiers (`dv01_zero`, `acme_zero_dv01`) and the tie-out
  reads a dot as structure (`tieout.compare` splits an L2 quantity on `.` to find its tolerance; `tieout.tolerances._check_key` parses `<level>.<quantity>[.<asset class>]`). A dotted name loads but has never been run through a tie-out: use an identifier.
- **The Python facade.** A Kit whose DEFAULT block has `function` targets or dotted reducers fails in `PricebtSession` with `[CFG-ALLOW]`; config runs are unaffected ([CE-FACADE](../pricebt-wire-external-library/references/common-errors.md#ce-facade)). If the facade must work, ship method-only default bindings (register reducers by name, put layer maths in methods).
- **`registry.allow` lives in the BASE config**, never in an overlay ([CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow), [CE-STACK-REGISTRY](../pricebt-wire-external-library/references/common-errors.md#ce-stack-registry)).
- **`sign` +1/-1 only.** A magnitude belongs in `scale`; `scale: -1` is legal but `sign` is what a reader looks for.

## Related skills

- `pricebt-architecture-and-rules`: the rules a binding must respect (units, signs, dollar delta, ladder contract).
- `pricebt-map-library-to-schemas`: decides WHICH callable goes with which name; this skill writes the entry.
- `pricebt-wrap-and-pricer`, `pricebt-instrument-kit`: what `@pricer` and `@instrument` are (the wrapped pricer, `Built.obj`) and how the `Kit` is assembled.
- `pricebt-layers-and-ladder`: the definitions behind the four layer bindings and the ladder `keys`.
- `pricebt-conformance-and-tieout`, `pricebt-debug-tieout-differences`: prove a binding's scale and sign against the reference stack (a forgotten `scale` or `sign` shows as one root failure, `L2.rate` or `L2.dv01`, plus the L3 baseline rows that read it).
- `pricebt-wire-external-library`: the master playbook and the worked example (`example/acme_adapter/swap.py`, `example/config/mistakes/`).
