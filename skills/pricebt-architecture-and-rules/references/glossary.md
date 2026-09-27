# Glossary of pricebt terms (alphabetical inside each group; every entry names where it lives)

## The contract

| term | meaning |
|---|---|
| **core** | everything under `src/pricebt` except `contrib/`. Zero dependence on any pricing library at import, name, shape and semantic level (spec Z1-Z4) |
| **adapter** | the code that lets ONE external library satisfy pricebt's contracts: a `wrap`, one or more `Kit`s, a conventions mapping, usually `_compat.py` and a `STACK`. Lives in `src/pricebt/contrib/<lib>/` or in a package of its own. The only place a library is imported |
| **schema** | a YAML data file per asset class (`src/pricebt/contracts/schemas/{generic,swap,bond}.yaml`, class `AssetSchema`): the names pricebt expects (terms, methods, measures, layers), with unit, sign, return type, `required`, `synonyms` and a `doc`, plus a `conventions` vocabulary and optional `pricer_capabilities`. Never says which callable implements a name |
| **asset class** | the schema an instrument follows: `swap`, `bond` (both `extends: generic`) |
| **method / measure / layer** | the three kinds of bindable schema name. Method: `value`. Measures: `dv01 gamma rate delta_ladder` (swap), plus `ytm duration accrued` (bond, optional). Layers: `carry roll delta convexity`. `pv` and `notional` are DERIVED (no binding) |
| **required** | a schema name every instrument of the class must have bound; strict mode makes an unbound one a load error |
| **binding** | one schema name -> one callable and exactly its arguments: `{target: {method|function|pricer_method|attribute: ...}, args, kwargs, reduce, scale, offset, sign, keys}` (`contracts/binding.py`, class `Binding`) |
| **reference** | an `@...` string in a binding's arguments from the closed grammar `@pricer @ctx @instrument @terms.<n> @state.<k>` |
| **reducer** | a one-argument function applied to a callable's result inside a binding (`float real identity dict_of_floats series_to_tenor_dict`, plus what an adapter registers with `register_reducer`) |
| **strict mode** | the only mode: every required name is bound at load or the load fails, and a name is never resolved by a same-named method. There is no switch to turn it off |
| **instrument spec** | one entry of the config's `instruments:`: asset class, `factory`, `conventions`, `bind`, pricer role (class `InstrumentSpec`, built by `build_spec`). Defined once per kind of instrument (`usd_sofr_ois`) |
| **terms** | the per-trade parameters, supplied by the action (`side maturity notional fixed_rate effective extras` for a swap). Validated and normalised by the schema; dates and tenors pass through untouched |
| **resolved terms** | what the factory returns in `Built.terms`: concrete dates (`dt.date`) and the resolved rate. Stored on the position and written to the trade ledger; compared first by the tie-out (L0) |
| **direction** | the schema's one sign: `+1` = payer of fixed / buyer; `-1` = receiver / seller. The factory receives it as `terms["direction"]` |
| **conventions block** | the opaque mapping in an instrument spec (calendar NAME, spot lag, day count, frequency, ...). Vocabulary in the schema (`AssetSchema.check_conventions`); pricebt applies none of it; hashed into the manifest; identical for every stack of a tie-out |
| **Kit** | what an adapter ships per instrument: `Kit(factory, asset_class, default_bind, cls, extra, schema, doc)` (`contracts/spec.py`). A spec merges the Kit's default block with the user's `bind:` by name |
| **factory** | `(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)`; resolves dates and `par` at the fill pricer |
| **extension name** | a measure or layer beyond the schema, declared in `Kit.extra` (`{"<name>": "measure"|"layer"}`) and bound like any other |
| **Stack** | an adapter's bundle for the Python facade: `wrap`, shipped instrument specs, defaults `"swap:USD" -> name`, `accepts_gs` |
| **stack overlay** | a YAML that may set ONLY `instruments.<n>.factory`, `instruments.<n>.bind` and `market.pricers.<r>.wrap` (`config/stacks.py`); the way the tie-out swaps the library under one shared base config |
| **dollar delta** | `dv01`, `gamma` and the values of `delta_ladder`: currency per +1bp (per bp squared for gamma) for ONE unit as built with its notional, holder-signed: a payer swap positive, a long bond negative. Not per unit notional |
| **ladder** | `delta_ladder`: a plain `dict[str, float]` keyed by upper-case tenor (`<int><D|W|M|Y>`), values currency per +1bp of that bucket's rate; sums to `dv01` within a declared tolerance; required on `swap` |
| **layer** | one row of the P&L decomposition, per unit, over the interval since the previous cadence point: `carry roll delta convexity`, each with an `id` and `version` (`swap.carry@1`). The engine owns `unexplained` (interval P&L minus the layers) |
| **baseline** | the engine's own library-independent Taylor decomposition (`tay_delta tay_convexity tay_unexplained`) from the bound `dv01`, `gamma` and `rate` |
| **Valuation** | what `value` returns: `Valuation(ts, pv, cash, financing, meta)`, ONE unit; `pv` the mark, `cash` flows realised in `[prev_ts, ts)`, `financing` the funding accrual. A tuple or a bare number is also accepted (`pricable.as_valuation`) |
| **MarkContext / `@ctx`** | everything a bound callable may see at a mark: `ts pricer pricers prev_ts prev_pricer entry_ts entry_pricer state cache params` (`pricable.MarkContext`) |

## Market data

| term | meaning |
|---|---|
| **snapshot** | `MarketSnapshot` (`snapshot.py`): frozen, pickle-safe plain data at one timestamp: `CurveSnapshot` (discount factors at node dates, an interpolation TAG), `FixingsSeries`, `CalendarData` (holidays), `QuoteSet` (bond quotes and reference data). No library object, no convention |
| **digest** | sha256 over a canonical encoding of the snapshot; equal digests prove two runs consumed identical inputs. Recorded BEFORE the wrap |
| **provider** | `MarketDataProvider`: `get_pricer(ts, request) -> Pricer` (`pricer.py`); user code in your package (pricebt ships none); returns a `SnapshotPricer`. A `TimeMapping` (`asof` with `max_staleness`, `exact`, `date` with `eod_visible_at`) says how a timestamp reaches a stored snapshot |
| **pricer** | one snapshot at one timestamp. `SnapshotPricer` is the library-free one; `wrap` turns it into YOUR pricer, the object bindings reach as `@pricer` and `ctx.pricer` |
| **role** | a named pricer slot in `market.pricers` (`primary` by default) with its `mdp`, `request` and `wrap` |
| **wrap** | `wrap(SnapshotPricer) -> your pricer`; the ONLY place a snapshot becomes library objects; applied once per `(role, digest, stamp)` by `MarketData` (memoised) |
| **look-ahead guard** | `MarketData.pricer(ts)` raises `LookAheadError` if `ts` exceeds the clock or the provider returns a snapshot stamped after `ts` |

## Proof

| term | meaning |
|---|---|
| **reference stack** | `pricebt.testing.refstack`: the dependency-free adapter (numpy and pandas, no pricing library), the known answer of the tie-out |
| **tie-out** | one base config run under several stacks (`python -m pricebt tieout base.yaml --stack name=overlay.yaml ...`, `pricebt.tieout.run_tieout`), compared with the reference at five levels |
| **levels L0-L4** | L0 inputs (snapshot digests, resolved terms with dates first, conventions digest), L1 marks (`pv cash financing quantity`: `quantity` is the position size, the L1 key of `src/pricebt/tieout/tolerances.py::QUANTITIES`), L2 measures (`dv01 gamma rate`, ladder buckets), L3 layers and baseline, L4 portfolio |
| **status** | per compared quantity: `exact noise expected info exceeds input structure` (`input`: an L0 difference already explains it; `structure`: different keys or NaN pattern) |
| **self-test** | the reference stack run twice must differ by exactly zero at every level; the harness is trusted only after it |
| **negative control** | an overlay that breaks exactly one thing and must fail at one located level and quantity, plus any downstream rows that read it; shown first not to fire on the correct wiring. The worked example ships five: a forgotten `scale` (root `L2.rate`, then `L3.tay_*`), a wrong `sign` (root `L2.dv01`, then `L3.tay_*`; nothing at L0, L1, L4), a lower-cased ladder key (`L2.delta_ladder.*` is `structure`), a calendar with one holiday too many (`L0.resolved_terms` is `input`, `L1.pv` follows), and a wrap with no holidays (the run STOPS with `MarketDataUnavailable`: no report) |
| **layer conformance kit** | `testing/layer_conformance.py` (`Setup`, `run_kit`): full revaluation equals `delta + convexity`, mirror symmetry, cash-sweep continuity, a bounded `unexplained` share |
| **golden case** | seasoned 3y payer 50mm reproduced by the reference stack and both real adapters; numbers in ADR 005 |
| **guard / blocker / twin** | the zero-dependence tests under `tests/guards/`; the import blocker; the test that proves a guard can fail (`tests/guards/test_twins.py`) |
| **partition marker** | `core`, `adapter_<lib>`, `fixtures` (and an opt-in live marker): every test carries one; `core` must pass with every library unimportable |
| **mismatched-names toy** | `tests/guards/zero_toys.py`: a swap whose method names cross the schema's, behind bindings; proves that binding alone conforms |
| **audit** | `backtest.audit: {measures: [...]}`: the snapshot digest per fetch and the per-position values that feed the tie-out (`audit: {}` is on) |
| **base config** | the shared config of a tie-out; the only place for `registry.allow`, `conventions`, terms, strategy and grid |
