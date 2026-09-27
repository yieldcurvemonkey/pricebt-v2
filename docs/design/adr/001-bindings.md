# ADR 001: A binding is data executed by one function

Status: accepted, built (`src/pricebt/contracts/binding.py`, `evaluate.py`). Requirements B1-B9, Z3.

## Context

The old core called library objects by convention: a method was found by its schema name (`getattr(obj, "convexity")`), its arguments were injected by parameter
name from `pricer.api_kwargs()`, and the schemas were "modelled on rateslib". That made rateslib's names, argument names and return shapes part of pricebt's
contract, so no second library (or a foreign stack with unrelated names) could be plugged in by configuration alone.

## Decision

A **binding** maps one schema name to one callable and declares exactly what it receives:

```yaml
bind:
  dv01:  {target: {method: pv01}}                                        # a method of the built instrument
  value: {target: {method: npv}, kwargs: {curves: "@pricer.curve"}}
  carry: {target: {function: "pkg.mod:carry"}, kwargs: {ctx: "@ctx"}}    # an allow-listed dotted path
  rate:  {target: {method: par}, scale: 100.0}                           # the library's decimals -> the schema's percent
  delta_ladder: {target: {method: lad}, kwargs: {buckets: [2Y, 10Y]}, keys: [2Y, 10Y], reduce: series_to_tenor_dict}
```

* Target kinds: `method` (of the built instrument), `function` (a dotted path under the allow-list), `pricer_method`, `attribute`.
* Arguments are literals or references from a **closed grammar**, no `eval`: `@pricer[.a.b]`, `@ctx[.field]`, `@instrument[.a.b]`, `@terms.<name>`, `@state.<key>`.
  Path segments start with a letter, so private and dunder names are unreachable; `@@x` is the literal `@x`.
* The call receives exactly the declared `args`/`kwargs` and nothing else (nothing is injected by parameter name).
* Post-processing is `sign * (scale * reduce(x) + offset)`; `reduce` names a registered reducer (`float`, `real`, `identity`, `dict_of_floats`,
  `series_to_tenor_dict`; adapters add more with `register_reducer`) or a dotted path. A ladder declares its result `keys` so pricebt can check them without guessing
  which keyword carries the tenors.
* `call_binding(binding, env)` is the only call path; `value`, measures and layers all go through it.
* Strict mode: every required schema name (methods, measures **and layers**) must be bound or the load fails (`CFG-REQUIRED-BINDING`, with a did-you-mean drawn from
  the class's public names and the schema's synonyms). A target that does not exist is an error naming what the object does offer. There is no fallback to a method of the
  same name.

## Alternatives rejected

* Keep name-based lookup with a "compat" alias table: the alias table would be a library's vocabulary in core (Z3).
* Inject arguments by inspecting signatures (the old `call_method`): it silently drops arguments and lets a library's parameter names decide behaviour.
* A rich expression language in the binding: an attack surface and a second language to review.

## Enforcement

`tests/test_binding.py` (mutation-checked: 24 mutants, 4 equivalent), `tests/test_evaluate.py`, and the semantic guards `tests/guards/test_semantics.py`: a toy whose
method names cross the schema's (`gamma` is its `convexity` and the reverse) with decoys that raise if reached by name runs in an engine backtest with exactly the
reference numbers, its unbound twin is rejected, and each mutation of `call_binding` that resolves by name, injects an argument or drops the reducer is killed.
