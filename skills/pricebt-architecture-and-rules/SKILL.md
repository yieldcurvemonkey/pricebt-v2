---
name: pricebt-architecture-and-rules
description: Use when you are new to pricebt and must connect an external pricing library, or before you edit any file under src/pricebt. Gives the mental model (schema, binding, snapshot, wrap; spec, terms, conventions), the zero-dependence rule and its guards, what belongs where, and the do and do-not list.
---

# pricebt-architecture-and-rules

## Purpose

pricebt is a config-driven, event-driven backtester whose CORE has zero dependence on any pricing library. An external library (yours) is reached only through
schema name -> binding -> callable, and market data reaches it as plain snapshots. This skill gives you the model, the placement rules and the guards that enforce them,
so that you put every piece in the right place the first time.

## Prerequisites

- The repository root as working directory; `PYTHONPATH=src;tests` (PowerShell: `$env:PYTHONPATH = "src;tests"`; POSIX uses `:`); Python 3.11+ with numpy, pandas, pyyaml, tqdm, pytest.
- You have your library's documentation, code and examples at hand. You need NOT know pricebt: this skill is the entry point. It runs no library.
- Run single test files only: the whole suite takes about twelve minutes.

## Steps

1. **Read the one rule.** Core = everything under `src/pricebt` EXCEPT `contrib/`. Core has zero dependence on any pricing library, market-data infrastructure or vendor SDK at four levels
   (import, name, shape, semantics). `docs/DESIGN.md` section 1; requirement ids Z1-Z6 in `docs/design/11-refactor-spec.md` section 2. Consequence: NEVER edit core to make your library work.

2. **Learn the four ideas.**

   | idea | what it is | where |
   |---|---|---|
   | **schema** | the neutral names pricebt expects (`value dv01 gamma rate delta_ladder carry roll delta convexity`, terms, conventions vocabulary) with unit, sign and return type. Data, never a callable | `src/pricebt/contracts/schemas/{generic,swap,bond}.yaml`; `SchemaRegistry.default().get("swap")` |
   | **binding** | one schema name -> one callable and exactly its arguments, with `scale sign reduce keys` for units, signs and shapes | `contracts/binding.py`; written as `bind:` in config or `Kit.default_bind` in your adapter (skill `pricebt-bindings-cookbook`) |
   | **snapshot** | plain immutable market data at one timestamp (discount factors at node dates, fixings, calendars as holiday sets, quotes) with a `digest()` proving what a run consumed. Emitted by a PROVIDER, never built from library objects | `snapshot.py`: `MarketSnapshot`, `CurveSnapshot`, `FixingsSeries`, `CalendarData`, `SnapshotPricer` |
   | **wrap** | your function `wrap(SnapshotPricer) -> your pricer`: the ONE place a snapshot becomes your library's objects. `MarketData` applies it once per `(role, digest, stamp)` | `market.py::MarketData._wrap`; config `market.pricers.<role>.wrap: "<pkg>:wrap"` |

3. **Separate instrument spec, terms and conventions.**

   | | what it holds | supplied by | where |
   |---|---|---|---|
   | **instrument spec** | asset class, `factory` (your `Kit`), `conventions`, `bind` | config `instruments.<name>`; ONE per kind of instrument (`usd_sofr_ois`), not one per tenor or direction | `contracts/spec.py`: `Kit`, `build_spec`, `InstrumentSpec` |
   | **terms** | per trade: swap `side effective maturity notional fixed_rate extras`; bond `side security notional extras`. `fixed_rate` is PERCENT; `notional` unsigned; direction is `side` | the action: `{instrument: usd_sofr_ois, terms: {side: receive, maturity: 10Y, notional: 1e7, fixed_rate: par}}` | the factory receives them normalised plus `direction` (+1 pay/buy) and returns the RESOLVED terms |
   | **conventions** | calendar NAME, spot lag, day count, frequency, ... (vocabulary in the schema) | the base config, shared by every stack | your adapter maps each value to the library or refuses it (`CFG-CONVENTION-UNSUPPORTED`); pricebt applies none |

   pricebt computes no date, no par rate and no accrual: your library does, inside the factory and the bound callables.

4. **Place your code** (which directories may import a library).

   | directory | imports a pricing library? | holds |
   |---|---|---|
   | `src/pricebt/{contracts,engine,strategy,results,tieout,config,backtests}` and the top-level modules (`snapshot.py market.py pricer.py pricable.py registry.py session.py ...`) | NO | schemas, bindings, engine, tie-out, facade |
   | `src/pricebt/testing/` | NO | toys, `synthetic` market, the reference stack `refstack.py`, `layer_conformance.py` |
   | `src/pricebt/contrib/<lib>/` | YES (only here) | the maintainer's adapters: `_compat.py wrap.py conventions.py swap.py bond.py __init__.py` (`swap`, `bond` Kits, `wrap`, `STACK`) |
   | your adapter package (outside `src/pricebt`; model: `skills/pricebt-wire-external-library/example/acme_adapter/`) | YES | the same files, plus your provider |
   | `tests/support/` | NO | fixture-backed providers (test code, never shipped) |
   | `tests/` | only tests marked `adapter_<lib>` | tests; every test carries a partition marker |
   | `configs/adapters/*.yaml` | n/a | stack overlays (`factory`, `bind`, `wrap`); base configs in `configs/` |

5. **Know the levels and the guards** (detail, real outputs and fixes: `references/guards-in-practice.md`).

   | level | forbidden in core | enforced by |
   |---|---|---|
   | Z1 import | importing a library, at any depth, at import time or lazily | `tests/guards/blocker.py` (`sys.meta_path`), `scan.scan_imports`, `test_guards.py::test_gt_z1*` |
   | Z2 name | a vendor token in identifiers, string literals, schema keys, non-`doc` YAML values, shipped configs; a library kwarg name under a schema `args` | `scan.scan_names/scan_yaml/scan_schema_args`, `banned.yaml` `executable_tokens`, `schema_arg_names` |
   | Z3 shape | resolving by name (`getattr(obj, "gamma")`), injecting arguments by parameter name, sniffing a config text to pick an adapter | `test_semantics.py` with the crossed-names toy `zero_toys.py` (decoys raise if reached by name) |
   | Z4 semantics | any convention: day count, divisor 360/365, compounding, lags, units of a library, its sign convention | `scan.scan_conventions`, `banned.yaml` `convention_tokens`, `convention_divisors`, `convention_allow` |
   | Z5 provenance | the maintainer's own infrastructure anywhere but as a test subject | `scan.scan_prose`, `banned.yaml` `prose_tokens` |
   | Z6 extras | a core that does not work without an adapter library | `import pricebt` loads no `pricebt.contrib` and no library; adapters raise `OptionalDependencyError` |

   Every guard has a twin (`tests/guards/test_twins.py`) that plants ONE violation in a temporary tree and must find it: a guard that cannot fail is a defect.
   Your adapter package is not scanned by them: it is where the library belongs.

6. **Put each piece where it belongs.**

   | piece | belongs in | contains | never contains |
   |---|---|---|---|
   | fetching market data | a PROVIDER: class with `get_pricer(ts, request) -> SnapshotPricer` in your package; config `market.mdps.<n>.type` | calls to the source, conversion to discount factors and holiday sets, the stamp | library curve objects, pricing, conventions |
   | snapshot -> library objects | `wrap` (`<pkg>/wrap.py`), memoised, pure | curve construction, calendar registration, fixings, the interpolation-tag check | trade logic, conventions |
   | building an instrument | the `Kit` factory `(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)` | resolving `par`, `effective`, `maturity` at the fill pricer; the conventions mapping | market fetching |
   | values, risks, layers | methods of your instrument class, named by `default_bind` | unit, sign and shape conversion into pricebt's contracts | anything about the engine |
   | convention values -> library codes | `<pkg>/conventions.py` | the map, and the refusal of what the library cannot express | a default for a missing key |
   | which library, which values | config: the BASE holds `conventions`, terms, strategy, `registry.allow`; an OVERLAY only `factory`, `bind`, `wrap` | | conventions in an overlay (`[CFG-STACK]`) |
   | proof | tests: layer conformance kit, tie-out against the reference stack, negative controls, a scan of your package | | |

7. **Decide which shape your library has.** Both shapes reach pricebt at the same four places.

   | | in-process Python API | remote service |
   |---|---|---|
   | provider | fetches data through the library's data API (in-process) | fetches curves, fixings and calendars through the service |
   | `wrap` | builds the library's curve and calendar objects from the snapshot | returns a client-holding pricer (session, endpoint, request builder), still memoised per snapshot |
   | factory and bindings | `method` targets on your instrument class | `method` targets that call the service, or `pricer_method` targets on the client (`args: ["@instrument"]`) |
   | globals to guard | valuation date, calendar registries, fixings stores (`_compat.py`) | sessions, tokens, rate limits, caches |

   The library may ALSO be the market-data source: the provider then calls its data API, converts the answer into a `MarketSnapshot` (curves are discount factors at node dates with an interpolation TAG: see `snapshot.VALUE_KINDS` and `snapshot.INTERPOLATIONS` for what exists) and
   returns `SnapshotPricer(snapshot)`. It must not return library curve objects (spec D2). Questions only YOUR library's documentation can answer: does it price from data you send or from data it holds
   server-side? which unit, sign, date type and per-notional basis does each call use? is it deterministic and thread-safe? what does it raise? Skills `pricebt-discover-the-library`, `pricebt-market-data-snapshots`, `pricebt-enterprise-platform-patterns`.

8. **Apply the rules.**

   DO
   - Reach the library only through schema name -> binding -> callable. Convert units, signs and shapes in the binding (`scale sign reduce keys`) or in code the binding names.
   - Let providers emit snapshots; wrap once per snapshot (`MarketData` memoises on `(role, digest, stamp)`); keep `wrap` pure and never share state between two snapshots.
   - Keep ONE conventions block, shared by every stack: an overlay may set only `factory`, `bind` and `wrap` (`config/stacks.py::validate_overlay`). If your library cannot honour a value, raise `CFG-CONVENTION-UNSUPPORTED`: that error is itself a tie-out finding.
   - Honour the dollar-delta contract: `dv01`, `gamma` and every ladder value are currency per +1bp (per bp squared) for ONE unit as built with its notional, holder-signed (payer positive, long bond negative). `rate` is percent.
   - Honour the ladder contract: `delta_ladder` is `dict[str, float]` with upper-case tenor keys `<int><D|W|M|Y>`, exactly the bound `keys`, summing to `dv01` within tolerance.
   - Return RESOLVED terms from the factory as `dt.date` and a percent rate; convert your library's date strings yourself (`AssetSchema.check_resolved` accepts an ISO string silently).
   - Own every library global in one module (`_compat.py`): set and restore the valuation date per call, translate exceptions, register calendars under content-addressed names.
   - Run every checking tool on an input whose answer you know before you trust it (a par swap is worth 0; a mirror trade has the opposite sign).

   DO NOT
   - Infer a binding by name, and do not follow a "did you mean" hint blindly: it can point at a decoy (`gamma: did you mean ['convexity', 'gamma']`).
   - Put a convention, a unit conversion or a vendor name in core, in a schema or in a shared config. Do not add a name to `banned.yaml` `convention_allow` to make a guard pass.
   - Import your library outside `contrib/` or your own package; do not import your adapter from core (only a dotted path in a config does, under `registry.allow`).
   - Put `registry.allow` in an overlay (`[CFG-STACK] registry: a stack may not set 'registry'`): it belongs in the BASE config.
   - Let a provider build library objects, mutate a snapshot, read the wall clock (runs are deterministic) or return a snapshot stamped after the requested `ts` (`LookAheadError`).
   - Widen a tolerance to turn a tie-out green without a written `reason` (`tasks/tolerance_ledger.yaml`).

9. **Read the repository in order:** `references/reading-order.md` (what to read, in which order, what to skip). Terms: `references/glossary.md`.

## Checks

```powershell
$env:PYTHONPATH = "src;tests"
python -c "import sys, pricebt, pricebt.api, pricebt.tieout; print([m for m in sys.modules if m.split('.')[0] in ('rateslib','QuantLib','gs_quant') or m.startswith('pricebt.contrib')])"   # expect: []
python -c "from pricebt.contracts.schema import SchemaRegistry as R; s = R.default().get('swap'); print(s.required_names(), s.required_layers())"   # expect: {'method': ['value'], 'measure': ['delta_ladder', 'dv01', 'gamma', 'pv', 'rate']} ['carry', 'convexity', 'delta', 'roll']  (pv is listed but derived: bind nothing for it)
python -m pytest tests/guards -q -o addopts= -p no:cacheprovider -k "not gt_z1b"        # expect: all pass (about half a minute)
python -m pytest tests/guards/test_semantics.py -q -o addopts= -p no:cacheprovider       # expect: all pass: the crossed-names toy runs a real backtest with exactly the reference numbers
python tests/guards/blocker.py tests/test_binding.py -q -o addopts= -p no:cacheprovider  # expect: all pass with every banned import root impossible
```

## Common failures

| message or symptom | cause | fix |
|---|---|---|
| `No module named 'rateslib' (blocked by the pricebt import blocker)` (any banned root) | a core module or a `core`-marked test imports a library | move the import to `contrib/` or your package; mark the test `adapter_<lib>` |
| guard hit `<file>:<line> [import] 'rateslib' in 'import rateslib'`, `[literal] 'rl_' in 'rl_swap'`, `[convention] '/360' in 'x * days / 360'`, `[convention] 'day_count' in 'DAY_COUNT'` | Z1, Z2 and Z4 violations in core (real hit formats) | `references/guards-in-practice.md`, section "How to fix each violation" |
| `every test must carry one of the partition markers core, adapter_rateslib, ... (spec G-5); unmarked: <node ids>` | a test without a partition marker: a collection error | add `pytestmark = pytest.mark.core` (or the adapter marker). A NEW marker is three edits: `pytest.ini`, `PARTITIONS` in `tests/guards/partition.py` AND the tuple pinned by `tests/guards/test_partition.py::test_the_five_partitions_are_the_ones_of_the_spec`: skill `pricebt-guards-and-packaging` |
| `a test marked `core` must not also need a library, a fixture or a live checkout (partition conflict)` | a `core` test that needs your library | it is an adapter test: change the marker |
| `[CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']` | a dotted path outside `pricebt` (a `factory`, `wrap`, `function` target, dotted `reduce`) and no allow-list | [CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow) |
| `[CFG-IMPORT] ... cannot import '<your_pkg>': No module named '<your_pkg>'` (a `factory` or a `wrap`) | pricebt never adds a path: your package is not importable | [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import) |
| `[CFG-STACK] registry: a stack may not set 'registry'...` and `... may not set instruments.<n>.conventions` | an overlay carries something that belongs to the shared base | [CE-STACK-REGISTRY](../pricebt-wire-external-library/references/common-errors.md#ce-stack-registry); conventions and terms go in the base config too |
| `[CFG-REQUIRED-BINDING] ... required names of asset class 'swap' are not bound: [...] ... pricebt never resolves a schema name by a same-named method` | strict mode | bind every required name (skill `pricebt-bindings-cookbook`) |
| `AssertionError: the decoy 'value' was called: a method is reached only through a binding, never because its name matches a schema name` | (in the toy) something resolved a method by its schema name | fix the caller; the guard did its job |
| `ConfigError [CFG-ALLOW] bind.carry.target.function: ...` or `RegistryError [CFG-ALLOW] ...` from a `PricebtSession(market=..., stack=<external stack>)` | the Python facade builds specs without an allow-list | [CE-FACADE](../pricebt-wire-external-library/references/common-errors.md#ce-facade) |
| `OptionalDependencyError: pricebt.contrib.<lib> needs the optional extra ...` | an adapter imported without its library | install the extra; core must not care |

## Related skills

- `pricebt-wire-external-library`: the master playbook that orders all of this, with the tested worked example (`example/`).
- `pricebt-discover-the-library`: probe your library's units, signs, dates, globals and errors on known answers before mapping it.
- `pricebt-map-library-to-schemas`: the mapping worksheet, one row per schema name.
- `pricebt-bindings-cookbook`: the binding grammar with executed examples.
- `pricebt-market-data-snapshots`, `pricebt-wrap-and-pricer`, `pricebt-instrument-kit`, `pricebt-layers-and-ladder`: the four places a library touches pricebt.
- `pricebt-conformance-and-tieout`, `pricebt-debug-tieout-differences`: prove it.
- `pricebt-guards-and-packaging`: register your library with the guards, markers and extras.
- `pricebt-run-config-and-reports`: the config and the CLI. `pricebt-enterprise-platform-patterns`: remote or service-shaped platforms.
