# Error catalogue for bindings and instrument specs

Messages are quoted from the code (`src/pricebt/contracts/binding.py`, `spec.py`, `evaluate.py`, `registry.py`) and from runs; `...` abridges a long list. Codes are stable identifiers
(`ConfigError.code`); the message starts with `[CODE] <yaml path>:`. Four phases, in the order they happen: PARSE (one binding), LOAD (one instrument spec), CALL (one mark), CHECK (what pricebt
verifies about the returned value).

## 1. Parse: one `bind:` entry (`parse_binding`)

| message | cause | fix |
|---|---|---|
| `[CFG-BINDING] bind.x: binding 'x': `target` must be a mapping with exactly one of ['method', 'function', 'pricer_method', 'attribute'], got {...}` | no target, or two kinds | one kind per binding |
| `... method target '_x' must be a public name (letters, digits, underscores; no leading underscore)` | private or dunder target | wrap it in a public method of your adapter class |
| `[CFG-UNKNOWN-KEY] bind.x: binding 'x': unknown keys ['sigm']; allowed ['args', 'doc', 'keys', 'kwargs', 'offset', 'reduce', 'scale', 'sign', 'target']` | a typo | fix the key |
| `... an `attribute` target is read, not called: it takes no args/kwargs` | args or kwargs with `attribute` | drop them, or use `method` |
| `[CFG-REF] bind.x: binding 'x': '@curve' is not a valid reference (allowed: @pricer[.attr], @ctx[.ts\|pricer\|pricers\|prev_ts\|prev_pricer\|entry_ts\|entry_pricer\|state\|cache\|params], @instrument[.attr], @terms.<name>, @state.<key>; segments start with a letter; write @@ for a literal '@')` | root or `@ctx` field outside the closed grammar; bare `@terms` or `@state` | see the table in SKILL.md step 2 |
| `[CFG-ALLOW] bind.x.target.function: [CFG-ALLOW] module 'os' is not under an allowed prefix ['pricebt']` | `function` target outside the allow-list. The prefix is doubled in the text: it is one error | the module must be under `pricebt` or under `registry.allow` of the BASE config |
| `[CFG-ALLOW] bind.x.target.function: [CFG-ALLOW] 'pricebt.contracts.binding:pd': 'pd' is owned by 'pandas', which is not under an allowed prefix ['pricebt']` | every object on the attribute path must be OWNED (`__module__`) by an allowed prefix: a library function re-exported by your module, or a `functools.partial` (owner `functools`), is refused | define a plain function in your package that calls the library |
| `[CFG-ALLOW] bind.x.target.function: [CFG-ALLOW] 'pricebt.testing.toys:_RATE': private names ('_RATE') are not reachable from config` | a leading underscore on a `function` path segment. (On a `method`, `pricer_method` or `attribute` target the same mistake is `[CFG-BINDING]`) | make it public |
| `[CFG-ALLOW] bind.dv01.reduce: [CFG-ALLOW] module 'acme_adapter.swap' is not under an allowed prefix ['pricebt']` | a dotted `reduce:` outside the allow-list | the same; or register the reducer by name (`register_reducer`) |
| `[CFG-REDUCER] bind.x: binding 'x': unknown reducer 'series_to_tenor_dic'; known ['dict_of_floats', 'float', 'identity', 'real', 'series_to_tenor_dict'] (did you mean ['series_to_tenor_dict']?)` | a name that is neither registered nor a dotted path (a registered adapter reducer is unknown until the adapter module is imported) | fix the name; import the adapter first (the `factory` path does) |
| `[CFG-REDUCER] ... reducer 'pricebt.contracts.evaluate:vector_add' must take exactly one argument (the callable's result): missing a required argument: 'b'` | the reducer's signature | a one-argument function |
| `[CFG-BINDING] ... `sign` must be +1 or -1, got 2.0` | `sign` is a direction flip, not a factor | use `scale` for factors |
| `[CFG-BINDING] ... `scale` must not be 0` | zero scale | remove it |
| `[CFG-BINDING] ... `keys` must be unique canonical tenors (upper-case <int><D\|W\|M\|Y>); got ['2y']` | lower-case or duplicate ladder keys | `2Y`, `10Y`; the reducer upper-cases the RESULT, `keys` is the declaration |

## 2. Load: one instrument spec (`build_spec`)

| message | cause | fix |
|---|---|---|
| `[CFG-FACTORY] instruments.x.factory: instrument 'x': the factory must accept (pricer, ts, *, terms, conventions): missing a required argument: 'extra'` | wrong factory signature | `def factory(pricer, ts, *, terms, conventions) -> Built` |
| `[CFG-UNKNOWN-KEY] instruments.x.bindd: instrument 'x': unknown key 'bindd' (did you mean 'bind'?)` | a typo in an instrument entry | allowed keys: `asset_class factory conventions bind pricer pricers layers params doc` |
| `[CFG-REQUIRED] ... a plain-callable factory needs `asset_class`` | a bare function instead of a `Kit` | `Kit(factory, asset_class, ...)` |
| `[CFG-SCHEMA] instruments.x: ... asset_class 'bond' does not match the kit's 'swap'` | the config says another asset class than the Kit | delete `asset_class` from the config or fix it |
| `[CFG-REQUIRED-BINDING] instruments.ois.bind: instrument 'ois': required names of asset class 'swap' are not bound: ['gamma'] (gamma: did you mean ['convexity', 'gamma']?). Bind each in `bind:` (pricebt never resolves a schema name by a same-named method)` | strict mode. Required = methods, measures AND layers | bind every required name; the swap schema requires `value dv01 gamma rate delta_ladder carry roll delta convexity` |
| `[CFG-UNKNOWN-BINDING] instruments.ois.bind.vega: ... binding 'vega' is not a name of asset class 'swap'; bindable: [...]` | a name the schema does not declare; an extension name missing from `Kit.extra` | declare it in `Kit.extra` |
| `[CFG-UNKNOWN-BINDING] ... 'pv' is derived by pricebt (value.pv) and takes no binding` (also `'notional' is derived by pricebt (terms.notional)`) | binding a derived name | delete it |
| `[CFG-UNKNOWN-BINDING] instruments.x: ... kit extension 'dv01' collides with a reserved row or a name of asset class 'swap'; extension names must be namespaced (for example 'lib.name')` | `Kit.extra` reuses a schema or engine name (`total transactions cash_interest unexplained financing tay_*`). The message advises a dotted name; a dot makes a tie-out quantity ambiguous (see SKILL.md), so prefer `<lib>_zero_dv01` | rename to a prefixed identifier |
| `[CFG-UNKNOWN-BINDING] instruments.x.layers: ... layer 'nope' is not a layer of asset class 'swap' (layers: ['carry', 'convexity', 'delta', 'roll'])` | `layers:` names something that is not a layer | fix the list |
| `[CFG-BINDING] instruments.ois.bind.rate: instrument 'ois': binding 'rate': Mismatched has no method 'level_off'; it has [...]; did you mean ['level_of']?` | misspelt `method` target (Kit with `cls`) | fix the name |
| `[CFG-BINDING] ... 'bump'(*, curve, bp) cannot take args=0 kwargs=['bps', 'curve']: missing a required argument: 'bp'` | kwargs do not fit the signature: validation of `function` targets and of `method` targets of a Kit with `cls` | fix the kwargs |
| `[CFG-ALLOW] instruments.x.factory: [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']` | the factory path is outside the allow-list | `registry.allow` in the BASE config (an overlay may not carry it: `[CFG-STACK] registry: a stack may not set 'registry': ...`); in Python pass `allow=("pricebt", "<your_pkg>")` to `build_spec` |
| `[CFG-IMPORT] instruments.x.factory: [CFG-IMPORT] cannot import 'pricebt.nothing': No module named 'pricebt.nothing'` and `'<module>' has no attribute path '<attr>'` | wrong module or attribute; the module must be on `sys.path` (pricebt never adds a path: `PYTHONPATH`) | fix the path |
| `[CFG-FORMAT] ... 'refstack_swap' is neither a registry name nor a dotted path 'package.module:Attr'` | there are no short adapter names in core | `pkg.module:attr` |

## 3. Call: one mark (`call_binding`)

`call_binding` raises `MethodCallError`. Through `evaluate_measure` and `evaluate_layer` (every mark of a run, and `check_default_block`) the SAME text arrives as `MeasureError` (`except MethodCallError as e: raise MeasureError(str(e))`);
only `value` (also through the derived `pv`) and a direct `call_binding` keep `MethodCallError`. When you search a traceback, search for the text after the exception name, not for the class.

| message | cause | fix |
|---|---|---|
| `MethodCallError binding 'dv01': instrument Lib has no method 'pxx'; public names: [...]; did you mean ['px']?` (`pricer Foo has no pricer method ...`, `instrument Foo has no attribute ...`) | the target is missing on the object that this mark holds (the instrument your factory built, or the pricer your `wrap` returned) | fix the name, or the factory/`wrap` that built the wrong object |
| `MethodCallError binding 'x': reference @state.zzz - state has no key 'zzz'; available: ['n']` (a missing KEY of a mapping lists the available keys; a missing attribute lists nothing: `... reference @pricer.foo - pricer (SimpleNamespace) has no attribute 'foo'`) | a reference that does not resolve | fix the path; a silent `None` never happens |
| `MethodCallError binding 'x': reference @ctx needs 'ctx', which this call does not provide` | a bare `Env` with no context (tests) | pass the context |
| `MethodCallError binding 'value': method 'boom' called with args=[] kwargs=['ctx']: object of type 'int' has no len()` | `call_binding` turns EVERY `TypeError` into this text, including one raised INSIDE your callable | the real cause is the tail; test the callable directly |
| `MethodCallError binding 'value': method 'val' called with args=[] kwargs=[]: Lib.val() missing 1 required keyword-only argument: 'ctx'` | the binding does not pass what the method needs | add `kwargs: {ctx: "@ctx"}` |
| `MethodCallError binding 'value': post-processing (reduce=None, scale=1.0, offset=0.0, sign=-1.0) failed: TypeError: unsupported operand type(s) for *: 'float' and 'Valuation'` | `sign` or `scale` on a `Valuation` or a tuple | remove them from `value`; convert in code |
| `MethodCallError ... post-processing (reduce='float', ...) failed: TypeError: float() argument must be a string or a real number, not 'dict'` | a scalar reducer on a ladder | use `series_to_tenor_dict` or `dict_of_floats` |
| `MethodCallError series_to_tenor_dict: ['front'] are not tenors (expected <int><D\|W\|M\|Y>, e.g. '3M', '10Y')` and `duplicate tenor '2Y' (labels differ only by case or repeat)` | the library's labels | a library reducer that maps them (or drops an EMPTY extra bucket, as the example does) |

## 4. Check: what pricebt verifies about the returned value (`evaluate_measure`, `evaluate_layer`, `evaluate_value`)

`value` may return a `Valuation`, a tuple `(pv[, cash[, financing]])` or a bare number (`pricable.as_valuation`). Measures and layers are errors, never silently coerced:

| message | cause |
|---|---|
| `MeasureError measure 'dv01' is not finite (nan)` / `must be a number, got NoneType: None` / `got str: 'abc'` | the callable returned NaN, `None` or a string |
| `MeasureError layer 'carry' of instrument 'x' returned None` / `... is not finite (nan)` | the same for a layer |
| `MeasureError measure 'delta_ladder' must return a dict[str, float] keyed by tenor, got Series (bind a reducer such as series_to_tenor_dict)` | no `reduce` |
| `MeasureError measure 'delta_ladder' must return exactly the bound tenors ['2Y', '10Y']: missing ['10Y']; extra []` | `keys` declared and the result differs |
| `MeasureError measure 'delta_ladder': key '2y' is not a canonical tenor (upper-case <int><D\|W\|M\|Y>, e.g. '3M', '10Y')` | lower-case key that no reducer normalised |
| `MeasureNotBound ... is not bound for instrument 'x' (asset class 'swap'); bound names: [...]` | a measure or layer that the config asks for but the spec does not bind (a recorded portfolio measure counts such a position as 0; anything else is a failure) |

An engine-owned row (`total transactions cash_interest unexplained financing`, and the baseline `tay_delta tay_convexity tay_unexplained`) is never a layer:
`[CFG-UNKNOWN-BINDING] ... binding 'unexplained' is not a name of asset class 'swap'`.
