---
name: pricebt-instrument-kit
description: "Use when pricebt must build trades on an external library, writing the Kit (factory plus default bindings), resolving par, effective date and maturity at the fill pricer, honouring or refusing convention keys, writing the Stack for the gs-style facade, naming the Kit in a config with registry.allow, or adding a bond kit."
---

# pricebt-instrument-kit

## Purpose

A **Kit** is what an adapter ships for one instrument: a factory `(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)`, the default bindings that say which method of the
object provides each schema name, and the class those methods live on. pricebt supplies neutral TERMS and CONVENTIONS; your library resolves dates and `par` and prices. A **Stack** bundles
a Kit and a `wrap` for the gs-style facade.

## Prerequisites

* `wrap` and the pricer exist (skill `pricebt-wrap-and-pricer`), snapshots flow (`pricebt-market-data-snapshots`). `<LIB>` = the external library; "Q" items are yours to answer from its documentation.
* Read once: `src/pricebt/contracts/schemas/swap.yaml` (and `bond.yaml`, `generic.yaml`: names, units, signs, the conventions vocabulary), `src/pricebt/contracts/spec.py` (`Kit`, `Stack`,
  `build_spec`, `TradeTemplate`), `docs/design/adr/001-bindings.md`, `003-instrument-spec-and-terms.md`, `006-stacks-and-tieout.md`, and the models
  `skills/pricebt-wire-external-library/example/acme_adapter/{swap,conventions,__init__}.py`, `src/pricebt/contrib/quantlib/{swap,conventions,bond}.py`.
* References: needed are `references/terms-and-conventions.md` (sections 1 and 4) and `references/kit-tests.md` (step 9). Optional: `references/kit-skeleton.md` (skip it when you copy `example/acme_adapter/swap.py`; for a service
  `example-service/zeta_adapter/swap.py`), `references/facade-and-config.md` (only for the gs-style facade, an extension term or a bond kit).
* Runtime: the repository root as working directory; `PYTHONPATH=src;tests;<parent directory of your adapter package>` (PowerShell: `$env:PYTHONPATH = "src;tests;..."`; `:` separators on Linux;
  pricebt adds no path itself: `python -m pricebt` puts the working directory on it, a script does not, [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)); a Python 3.11+ that has pricebt's dependencies. `python` below means that interpreter (in this checkout: `C:/Users/chris/anaconda3/envs/stir/python.exe`).
  Run single test files only (the whole suite takes about 12 minutes).

## Steps

1. **List what the schema requires.** `SchemaRegistry.default().get("swap")`: `.required_names()` (methods and measures), `.required_layers()`, `.terms`, `.conventions`. For the swap: `value`
   (a `Valuation` or a float, per unit as built), `dv01`, `gamma`, `rate` (PERCENT), `delta_ladder` (dict keyed by tenors), layers `carry`, `roll`, `delta`, `convexity`. Strict mode: a Kit that leaves one
   unbound does not load, and a name is never resolved by a same-named method. Signs and units: `dv01`/`gamma`/`delta_ladder` are DOLLAR deltas, a payer swap positive, a long bond negative.
2. **Decide every convention key** (`references/terms-and-conventions.md`, section 4): for each key of the vocabulary, which tokens does `<LIB>` express, verified against the reference stack on one
   snapshot? Write `<lib>_adapter/conventions.py`: `AssetSchema.check_conventions(raw, require_all=True)` (unknown key, wrong type, unknown token, missing key: `[CFG-CONVENTION]`), then your
   `_SUPPORTED` table (EVERY key gets a decision) and, for a vocabulary token you cannot honour, `ConfigError(..., code="CFG-CONVENTION-UNSUPPORTED")` naming the reason and the supported set.
   Never a silent default. Ship a complete block (`USD_SOFR_OIS_CONVENTIONS`); `calendar` is the NAME of a calendar in the snapshot the provider serves.
3. **Write the instrument class** in `<lib>_adapter/swap.py`: PLAIN DATA (the library contract + `direction`), keyword-only methods taking `ctx` (`MarkContext`: `ts`, `pricer`, `prev_pricer`,
   `cache`, `state`), it deep-copies and pickles, and it never keeps the pricer. Unit and sign conversion split: a binding's `sign`/`scale` post-process a number or every value of a dict but CANNOT
   touch a `Valuation` and `scale` is a constant that cannot read the trade's notional, so `value` (pv and cash) and any notional-dependent factor are code (skill `pricebt-bindings-cookbook`).
   `references/kit-skeleton.md` is the starting file. Its `value` (of a FRESH swap), `rate` and `dv01` are complete; its `gamma`, `delta_ladder`, the four layers and the value of a SEASONED swap are STUBS that
   raise `NotImplementedError` naming what to do: implement them (steps 4-6 and `pricebt-layers-and-ladder`) before the step-9 tests can all pass.
4. **Write the factory** (skeleton: `references/kit-skeleton.md`, executed). The contract:
   * signature exactly `(pricer, ts, *, terms, conventions)` (`[CFG-FACTORY] ... the factory must accept (pricer, ts, *, terms, conventions)`), returns `Built(obj, terms)` (else
     `[CFG-FACTORY] factory of instrument 'ois' must return Built(obj, resolved_terms), got dict`);
   * first line: check the pricer is YOURS (`_lib(p)`), naming the fix (`set wrap on the pricer role`); then validate conventions (step 2) and `extras` (an unknown extra is YOUR `ConfigError` code `CFG-EXTRAS`);
   * `terms` arrive normalised (`side` canonical, `direction` +1/-1 added, `effective` default `spot`, `fixed_rate` default `par`), but dates and tenors UNTOUCHED (`'10Y'` or `'10y'`, an ISO string, a `dt.date`),
     notional unsigned, `fixed_rate` in PERCENT: `references/terms-and-conventions.md`, section 1;
   * resolve at the FILL pricer, on the SNAPSHOT's holidays: `effective` (`spot` = reference date + spot lag business days; a tenor = spot + tenor, adjusted; a date = adjusted), `maturity` (tenor from
     effective, or date), `fixed_rate` (`par` = the par rate of this snapshot, plus `par_spread_bp / 100`);
   * return `{effective: dt.date, maturity: dt.date, fixed_rate: float PERCENT, notional: float}` (bond: `security` id, `coupon`, `maturity`, `notional`), the SAME keys as the reference stack: never an ISO
     string (`check_resolved` accepts one silently), never an extra key (a key one stack lacks is an `L0.resolved_terms input` difference), never a changed `side` or size (`the factory changed term 'notional'...`).
5. **Write the default binding block** `SWAP_BIND` (a dict: schema name -> `{target: {method: ...}, kwargs: {ctx: "@ctx", ...}, scale, sign, reduce, keys}`), one entry for EVERY required name (and
   your extension names). Put every unit/sign conversion that a binding CAN express in the block (visible, overridable, testable), not in a base config: an overlay's `bind` replaces the base's
   whole block.
6. **Create the Kit**: `Kit(factory=swap_factory, asset_class="swap", default_bind=SWAP_BIND, cls=LibSwap, extra={"<lib>_zero_dv01": "measure"}, schema=None, doc="...")`. `cls` makes pricebt check at
   load that every method target exists and can bind exactly its declared kwargs. `extra` names are plain identifiers with your prefix (the collision message asks for `lib.name`, but shipped kits
   and the tie-out use identifiers); an extension term needs `schema=` (`references/facade-and-config.md`, section 1). Name the module attribute like the shipped ones (`swap`, `bond`) knowing the
   package re-export shadows the submodule (`import <lib>_adapter.swap as m` is the Kit).
7. **Create the `Stack`** in `<lib>_adapter/__init__.py` for the facade: `Stack(name, wrap, instruments={"usd_sofr_ois": {"factory": swap, "conventions": dict(BLOCK)}}, defaults={"swap:USD": "usd_sofr_ois"}, accepts_gs={"floating_rate_option": (...)})`. The facade loads an adapter outside `pricebt` only from a `Stack` OBJECT with a default block of method targets and registered reducers: [CE-FACADE](../pricebt-wire-external-library/references/common-errors.md#ce-facade); the other limits: `references/facade-and-config.md`, section 4.
8. **Name the Kit in the config** (BASE: `instruments.<name>: {asset_class, factory: "<lib>_adapter:swap", conventions: {...}}`; conventions written ONCE, shared by every stack) and add
   `registry: {allow: [<lib>_adapter]}` to the BASE (dotted paths outside `pricebt` are imported only under an allowed prefix; an overlay may not set `registry`; the error does not say where). The stack
   OVERLAY sets only `instruments.<name>.factory` (and optionally `bind`) and `market.pricers.<role>.wrap`. Merge rules: an overlay's `bind` replaces the base's whole `bind`; the Kit's
   `default_bind` merges with the surviving `bind` BY NAME. Worked files: `skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml` and `acme_swap.yaml`.
9. **Test it**: copy `references/kit-tests.md` (executed, mutation-checked) to `tests/test_<lib>_swap.py`; run the layer conformance kit (`pricebt-layers-and-ladder`); write one conventions test per
   key decision (`tests/test_quantlib_conventions.py` is the model). On the bare skeleton exactly two test cases of the template are red by design (`...[gamma]` and `test_every_default_binding_runs...`: the
   stubs of step 3); `[value]`, `[rate]`, `[dv01]` and the factory, dates and conventions tests must be green first. Green means done only when the stubs are gone.
10. **If `<LIB>` prices bonds**, add the bond kit (`references/facade-and-config.md`, section 5): terms `side buy/sell`, `security` token resolved through the snapshot's `QuoteSet` (aliases such as `CT10`),
    `rate` bound to the method `ytm`, long bond `dv01` NEGATIVE, `extras.repo`, yield-space layers, the `PRICE-CONVENTION` rule on a coupon date. Models: `contrib/quantlib/bond.py`, `contrib/rateslib/bond.py`.
11. **Record the convention decisions** in `docs/design/11-<lib>-conventions.md` in the shape of `docs/design/11-quantlib-conventions.md` (headline, method and controls, per item evidence, process-global state,
    remaining gaps, the vocabulary decision per key). The tie-out (`pricebt-conformance-and-tieout`) then compares stacks; a widened tolerance needs a written `reason`.

## Checks

```powershell
$env:PYTHONPATH = "src;tests"
python -m pytest tests/test_spec.py tests/test_conventions_vocab.py tests/test_stacks.py -q -o addopts= -p no:cacheprovider
# expected: all passed (the Kit/spec/terms/conventions/overlay contracts your kit is validated against)
python -m pytest tests/test_skills_example_acme.py -q -o addopts= -p no:cacheprovider -k "conforms_to_the_swap_schema or every_default_binding or effective_and_maturity or par_and_the_spread or an_unknown_key"
# expected: all passed (the worked example's kit tests; no external library)
python -m pytest tests/test_quantlib_swap.py tests/test_quantlib_conventions.py -q -o addopts= -p no:cacheprovider     # expected: all passed (needs the QuantLib extra)
python -m pytest tests/test_<lib>_swap.py -q -o addopts= -p no:cacheprovider                                          # yours: all passed once the stubs are implemented, then the mutants of references/kit-tests.md all fail
```

Load your Kit against the REAL config and allow-list. Expected `OK`. Two things must hold, and each has its own failure with the message and the fix: the adapter's parent directory is on `PYTHONPATH` (pricebt never adds a path: [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import))
and the BASE config carries `registry.allow` ([CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow)):

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example"     # your adapter: "src;tests;<parent directory of <lib>_adapter>"
$ex = "skills/pricebt-wire-external-library/example"
python -m pricebt validate $ex/config/acme_tieout_base.yaml --stack $ex/config/acme_swap.yaml        # OK
```

And the first checkpoint in Python (the whole default block loads before any method body exists; expected: the names of the block, no exception):

```python
from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import build_spec
spec = build_spec("ois", {"factory": swap, "conventions": CONV}, schemas=SchemaRegistry.default(), allow=("pricebt", "<lib>_adapter"))
print(sorted(spec.bindings))   # ['carry', 'convexity', 'delta', 'delta_ladder', 'dv01', 'gamma', 'rate', 'roll', 'value', ...your extensions]
```

## Common failures

| message (real) | cause | fix |
|---|---|---|
| `[CFG-ALLOW] instruments.ois.factory: [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']` | the adapter package is not on the allow-list; the message does not say where | [CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow): `registry.allow` in the BASE |
| `error: [CFG-IMPORT] market.pricers.primary.wrap: [CFG-IMPORT] cannot import 'acme_adapter': No module named 'acme_adapter'` (for a factory: `instruments.<name>.factory`) | the adapter's parent directory is not on `PYTHONPATH` | [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import) |
| `[CFG-STACK] registry: a stack may not set 'registry': only instruments.<name>.factory / .bind and market.pricers.<role>.wrap (...)` | `registry.allow` put in an overlay | [CE-STACK-REGISTRY](../pricebt-wire-external-library/references/common-errors.md#ce-stack-registry): move it to the base |
| `[CFG-ALLOW] bind.carry.target.function: ... module 'acme_adapter.swap' ...` or `RegistryError [CFG-ALLOW] module 'acme_adapter' ...` from `PricebtSession` | the facade builds specs without `allow=` and resolves a dotted `stack=` under `pricebt` only | [CE-FACADE](../pricebt-wire-external-library/references/common-errors.md#ce-facade): a method-only default block and a `Stack` OBJECT, or use configs |
| `[CFG-FACTORY] the factory must accept (pricer, ts, *, terms, conventions): got an unexpected keyword argument 'conventions'` | wrong signature | `def f(pricer, ts, *, terms, conventions)` |
| `[CFG-FACTORY] factory of instrument 'ois' must return Built(obj, resolved_terms), got dict` | returned a dict/object | `return Built(obj, {...})` |
| `[CFG-TERMS-UNRESOLVED] term 'fixed_rate' came back unresolved ('par')` / `'effective' ... ('spot')` / `'maturity' ... ('5Y')` | the factory passed a token or tenor back | resolve to a number / `dt.date` |
| `[CFG-TERMS] ... the factory changed term 'notional' from 10000000.0 to 1.0; only relative dates and tokens are resolved` | side or size altered | return the terms unchanged |
| the tie-out row `L0 resolved_terms` is `input`: `3 differ ... P000001 curve_id: None vs USD-SOFR` | one stack returns a resolved key the others do not | return exactly `effective, maturity, fixed_rate, notional` (bond: `security, coupon, maturity, notional`) |
| resolved dates are `str` in the trade ledger; date logic misbehaves | an ISO string returned (accepted silently) | `C.to_date(...)` in the factory |
| `[CFG-REQUIRED-BINDING] required names of asset class 'swap' are not bound: ['dv01'] (dv01: did you mean ['dv01']?) ...` | a required name missing from the block | one entry per required name; pricebt never resolves by same-named method |
| `[CFG-UNKNOWN-BINDING] binding 'dv01_' is not a name of asset class 'swap'; bindable: [...]; did you mean ['dv01']?` | typo in `bind:` | fix the name |
| `[CFG-BINDING] binding 'dv01': AcmeSwap has no method 'dv0l'; it has [...]` / `... cannot take args=0 kwargs=['ctx', 'tenors']` | `cls` check: method missing or kwargs the method cannot take | fix the method name or signature |
| `[CFG-UNKNOWN-BINDING] kit extension 'dv01' collides with a reserved row or a name of asset class 'swap'; extension names must be namespaced (for example 'lib.name')` | `Kit.extra` reuses a schema name | a new identifier with your prefix (`<lib>_zero_dv01`) |
| `[CFG-UNKNOWN-KEY] instruments.ois.foo: instrument 'ois': unknown key 'foo'; allowed [...]` | key outside `asset_class, bind, conventions, doc, factory, layers, params, pricer, pricers` | remove or fix |
| `[CFG-CONVENTION] unknown convention keys [...]` / `missing convention keys [...]: there are no implicit defaults` / `convention 'day_count' must be one of [...]` | block does not match the vocabulary | complete block, exact keys and tokens |
| `[CFG-CONVENTION-UNSUPPORTED] convention day_count='thirty360' is in the vocabulary but this adapter does not support it: <reason>; supported: [...]` | correct refusal of what `<LIB>` cannot express | choose a supported value or extend the adapter after a probe |
| `[CFG-STACK] instruments.ois.conventions: a stack may not set ...: only ['factory', 'bind']` | conventions in an overlay | conventions belong to the base |
| `KeyError: 'stub'` (a vocabulary key) at the first build | a key of the schema's vocabulary has no entry in your `_SUPPORTED` table | give EVERY key a decision (test: every key of `SchemaRegistry.default().get('swap').conventions` has one) |
| numbers differ by the notional / by 100 / by a sign only in the tie-out | a conversion missing in the block (`scale: 100` on `rate`, `sign: -1` on receiver-signed risk) or in code (per-million, `Valuation`) | fix, then add the mutant to your kit tests |
| `IRSwap(...)` -> `NotSupportedError: IRSwap: ['floating_rate_option'] are not terms of the registered instrument spec: its conventions block defines them ...` | a gs convention field the stack does not declare | list the values your default block implies in `accepts_gs` |
| `[CFG-REDUCER] reducer 'x' is already registered` on reload | `register_reducer` refuses a different function under a taken name | do not `importlib.reload` the module; restart the process |
| `import <lib>_adapter.swap as m` is the Kit, not the module | the package re-exports the Kit over the submodule | `sys.modules["<lib>_adapter.swap"]` |
| the CLI tie-out never shows the ladder | the CLI audits `dv01`, `gamma`, `rate` only | [CE-LADDER-CLI](../pricebt-wire-external-library/references/common-errors.md#ce-ladder-cli) |

## Related skills

`pricebt-wrap-and-pricer` (the pricer the factory checks), `pricebt-bindings-cookbook` (target kinds, `@ctx`, `sign`, `scale`, `reduce`, `keys`, what a binding cannot do), `pricebt-map-library-to-schemas`
(deciding what each schema name means for `<LIB>`), `pricebt-layers-and-ladder` (the four layers, the ladder reducer, `run_kit`), `pricebt-conformance-and-tieout` (L0-L4, tolerances),
`pricebt-run-config-and-reports` (configs, overlays, CLI), `pricebt-guards-and-packaging` (banned imports, markers, the optional extra), `pricebt-discover-the-library`,
`pricebt-market-data-snapshots`, `pricebt-wire-external-library` (the master playbook).
