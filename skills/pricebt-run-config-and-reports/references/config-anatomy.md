# Anatomy of a base config, and every config error code

Source: `src/pricebt/config/loader.py` (`TOP_KEYS`, `BACKTEST_KEYS`, `MARKET_KEYS`, `MDP_KEYS`, `PRICER_KEYS`, `_Builder`), `src/pricebt/contracts/spec.py` (`_SPEC_KEYS`, `build_spec`), `src/pricebt/config/stacks.py`.
Shipped examples: `configs/synthetic_swap_carry.yaml` (library-free, one file), `configs/suite/_swap_base.yaml` + `_swap_base_eod.yaml` + `s1_swap_carry_eod.yaml` (a base with `extends:`), and the worked example base
`skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml`. A config is YAML or JSON with the same model; unknown keys are errors with a did-you-mean.

## 1. Top-level keys (`TOP_KEYS`)

`name`, `doc`, `meta` (free text), `extends` (a path or list of paths, base first), `registry`, `backtest`, `market`, `instruments`, `trades`, `params`, `signals`, `strategy`, `outputs`, `tieout`.
`backtest`, `market`, `instruments`, `trades`, `params`, `signals`, `strategy`, `registry`, `outputs` must be mappings (`[CFG-TYPE] <key> must be a mapping, got list`).

## 2. `registry`: what may be imported and set up

```yaml
registry:
  allow: [acme_adapter, support]      # dotted-path prefixes that may be imported, on top of `pricebt` (always allowed)
  import: [acme_adapter.plugins]      # modules imported for their side effects (allow-listed prefixes only)
  setup: [{call: "pkg.mod:fn", kwargs: {}}]   # run before any foreign code, e.g. install a guard
  schemas: [schemas/my_asset.yaml]    # extra asset-class schema files, relative to the config file
```

`allow` is read from the BASE config after overlays are applied; an overlay cannot set `registry`. A dotted path (`package.module:Attr.sub`) is the ONLY thing in a config that imports anything (a `factory`, a pricer `wrap`, a mdp or action `type`,
a binding `target: {function: ...}`, a dotted `reduce:`, `registry.import`, `{$ref: ...}` / `{$call: ..., kwargs: ...}`). The module part must equal an allowed prefix or start with `<prefix>.`, and it must be importable (pricebt adds no path: `PYTHONPATH`).
Every attribute on the path is fetched, refused if it starts with an underscore, and its OWNER (`__module__`) must also be allowed: a `Kit` instance reports `pricebt.contracts.spec`, so `acme_adapter:swap` passes; a function reports its own module (`acme_adapter.swap`), and `acme_adapter` covers it.

## 3. `backtest`

| key | values (defaults) |
|---|---|
| `name` | the run label (default: the top-level `name`, else `backtest`); the full key set is `BACKTEST_KEYS` |
| `tz` | `America/New_York` |
| `calendar` | `weekends` (default), a JSON file path (relative to the config), `{file: ...}`, or `{holidays: [dates], weekmask: [1,1,1,1,1,0,0]}` |
| `session` | `{open: "08:00", close: "17:00"}` |
| `date_policy` | `open`, `close` (default), `midnight` |
| `grid` (required) | `{start, end, freq: 1b}` (`1b`, `1w`, `1m`, `1min`, `15min`, `1h`), `{explicit: [ts...]}`, or `{from_mdp: <role>, start, end, every, business_only, session_only}` (the provider's own timestamps) |
| `initial_capital` | number, default 0 (equity is then the cumulative P&L) |
| `fill_lag`, `fill_price`, `exit_policy` | whole number >= 0 (0); `decision` or `next`; `own_time` or `next_grid` |
| `on_error` | `raise` (default), `record`, `skip` |
| `progress` | `{show: true, desc: "..."}` (one tqdm bar) |
| `attribution` | `{layers: [carry, roll, delta, convexity], cadence: each|eod|every_n:<k>, strict, tol, baseline}`; the tie-out compares the layers you list here |
| `measures`, `vector_measures` | names recorded as `measure_<name>` columns (scalar) or as a per-bucket frame (`delta_ladder`) |
| `audit` | `true`, `false` or `{measures: [...]}`; `{}` is ON; the tie-out sets it itself |
| `record_signals`, `cash_accrual` | signal names to record; a cash accrual component |

Measure, layer and audit names are checked against every instrument's schema AND what the kit binds beyond it (`[CFG-REF] backtest.measures: measures ['dv0l'] not defined by any instrument (known: [...]); 'dv0l': did you mean 'dv01'?`).

## 4. `market`

```yaml
market:
  mdps:                                       # market data providers, each emits SNAPSHOTS
    rates:
      type: "pricebt.testing.synthetic:SyntheticMarket"     # a registry name or a dotted path (allow-listed)
      kwargs: {start: 2024-01-02, end: 2024-12-31, seed: 7}
  pricers:                                    # named ROLES: which provider, which request, which wrap
    primary: {mdp: rates, wrap: "pricebt.testing.refstack:wrap"}
```

**`time:` is accepted only by a provider that can take it.** The loader (`_Builder.mdp`) passes an mdp's `time:` block on when the provider's constructor has a `time` parameter (it parses the block itself), a `mapping` parameter (the loader builds a `TimeMapping` from
`{mode, max_staleness, eod_visible_at, unbounded_ok, tz}`; modes `asof | exact | date`; `asof` needs `max_staleness` or `unbounded_ok: true`) or `**kwargs`. `SyntheticMarket` has none of them, so this is a config error there (executed):
`[CFG-UNKNOWN-KEY] market.mdps.rates.time: pricebt.testing.synthetic:SyntheticMarket does not accept a time mapping`. `pricebt.testing.toys:ToyMDP` (parameter `mapping`) accepts it; so does a snapshot provider of your own that declares `mapping` or `time` (`pricebt-market-data-snapshots`):

```yaml
    rates:
      type: "pricebt.testing.toys:ToyMDP"                    # or your own provider class with a `mapping` / `time` parameter
      time: {mode: asof, max_staleness: 12h}                 # executed: `pricebt validate` prints OK with ToyMDP here; without max_staleness it is CFG-REQUIRED (section 7)
```

`wrap` (an overlay may replace it) is the adapter function `SnapshotPricer -> library pricer`. An instrument reads the role `primary` unless it says `pricer:` / `pricers:`. `market.mdps` may be given without `pricers` (a role per mdp). `{$call: "pkg.mod:Attr", kwargs: {...}}`
builds a Python object inside a value (the example base builds a `pricebt.timeutil:Calendar` for the synthetic market from the same holiday list as `backtest.calendar`, with a YAML anchor `&holidays` / alias `*holidays`).

## 5. `instruments` (the instrument SPEC; trades supply the TERMS)

Keys (`_SPEC_KEYS`): `asset_class`, `factory` (a Kit or a dotted path to one), `conventions` (an opaque block, hashed into the manifest, validated by the adapter), `bind` (schema name -> binding, merged over the Kit's defaults by name), `pricer`, `pricers`, `layers`, `params`, `doc`.
What is checked WHEN: at load (`pricebt validate` already fails) the factory signature (`[CFG-FACTORY] ... the factory must accept (pricer, ts, *, terms, conventions)`), every binding's shape and its signature against the callable, unknown binding names and missing required names
(`CFG-BINDING`, `CFG-UNKNOWN-BINDING`, `CFG-REQUIRED-BINDING`). The CONTENT of the conventions block is checked by the adapter when the first trade is BUILT, so `pricebt validate` passes a config whose conventions block is empty and `pricebt run` then fails with
`[CFG-CONVENTION] missing convention keys [...] for asset class 'swap': there are no implicit defaults`, or `[CFG-CONVENTION] convention 'day_count' must be one of ['act360', 'act365f', ...], got 'banana'`.

## 6. `trades`, `strategy`, `signals`, `params`, `outputs`, `tieout`

* `strategy.triggers[*]`: `{type: periodic|date|..., frequency, actions: [{type: add_trade, priceables: {instrument, name, terms}, trade_duration}]}`; terms of a swap: `side` (`pay`/`receive`), `maturity` (`10Y` or a date), `notional`, `fixed_rate` (`par` or a percent number).
  `trades:` names reusable `{instrument, terms, name, legs}` templates; `params:` values are referenced as `@param.<name>` inside terms. The trigger and action types are in `docs/DESIGN.md` section 8.
* `outputs: {dir: <path>, parquet: true, tearsheet: false}`: `dir` is relative to the CONFIG FILE's directory (executed: the run wrote to `<config dir>/out/...`), not to the working directory. The CLI `--out` overrides it.
* `tieout: {tolerances: {<level>.<quantity>[.<asset class>]: number | {rel, floor, expected, strict, reason}}}`: validated at load (`pricebt-conformance-and-tieout`, `skills/pricebt-conformance-and-tieout/references/tolerances.md`).
* Relative paths in path-like keys (`root`, `path`, `file`, `dir`, `directory`, `fixings_path`, `calendars_root`, `prices`, `reference`, `quotes`) and `backtest.calendar` resolve against the config file's directory when they exist there.

## 7. Error codes (all are `ConfigError`, printed as `error: [CODE] <path>: <message>` and exit status 2)

Every message is real; the ones marked (code) were read from the source and not executed, all the others were produced by running the CLI on the worked example or the recipe base (the `time:` rows on `ToyMDP` and on a scratch provider that parses its own `time` kwarg).

| code | when | example |
|---|---|---|
| `CFG-FILE` | a config path does not exist | `[CFG-FILE] config file not found: C:\...\nope.yaml` |
| `CFG-YAML` | invalid YAML/JSON, a DUPLICATE key | `[CFG-YAML] <file>:2:1: duplicate mapping key 'backtest' (first at line 1)` |
| `CFG-ENV` | `${VAR}` unset and no default | `[CFG-ENV] environment variable 'NOPE_VAR' is not set and has no default` |
| `CFG-SET` | `--set` without `=` | `[CFG-SET] --set expects key=value, got 'backtest.tz'` |
| `CFG-EXTENDS` | an `extends:` cycle | `[CFG-EXTENDS] extends cycle: <path of a.yaml> -> <path of b.yaml> -> <path of a.yaml>` |
| `CFG-UNKNOWN-KEY` | a key that is not allowed there (loader, actions, instruments) | `[CFG-UNKNOWN-KEY] backtest.gird: unknown key 'gird' (did you mean 'grid'?)`; `... strategy.triggers[0].actions[0].trade_durations: unknown key 'trade_durations' for AddTradeAction (did you mean 'trade_duration'?)` |
| `CFG-REQUIRED` | a required block or key is missing | `[CFG-REQUIRED] market: `market:` with at least one mdp is required`; `[CFG-REQUIRED] backtest.grid: backtest.grid is required` |
| `CFG-TYPE` | a wrong type or value | `[CFG-TYPE] backtest.on_error: backtest.on_error must be one of ['raise', 'record', 'skip'], got 'skipp'; did you mean 'skip'?`; a bool written as a string: `backtest.progress.show must be true or false, got 'false' (a string such as "false" would read as true)` |
| `CFG-REF` | a reference to something undefined | `[CFG-REF] strategy.triggers[0].actions[0].priceables.instrument: unknown instrument 'usd_sofr' (did you mean 'usd_sofr_ois'?)` |
| `CFG-UNKNOWN-TYPE` | an unknown trigger, action, signal or cost type | `unknown trigger 'perodic'; did you mean ['periodic']? known: [...]` |
| `CFG-FORMAT` | a `wrap`, `factory` or `type` that is neither a registry name nor `pkg.mod:Attr` | `'acme_wrap' is neither a registry name nor a dotted path 'package.module:Attr'` |
| `CFG-ALLOW` | the module or the owner is not under an allowed prefix, or a private name | `[CFG-ALLOW] market.pricers.primary.wrap: [CFG-ALLOW] module 'acme_adapter' is not under an allowed prefix ['pricebt']`; `'np' is owned by 'numpy', which is not under an allowed prefix [...]`; `private names ('_x') are not reachable from config` |
| `CFG-IMPORT` | the module or attribute cannot be imported | `[CFG-IMPORT] ... cannot import 'acme_adapter': No module named 'acme_adapter'`; `'pricebt.testing.refstack' has no attribute path 'nope'` |
| `CFG-STACK` | an overlay sets more than factory / bind / wrap, or adds an instrument or role | `[CFG-STACK] registry: a stack may not set 'registry': only instruments.<name>.factory / .bind and market.pricers.<role>.wrap (...)` |
| `CFG-TIEOUT` | an invalid `tieout.tolerances` declaration | `unknown quantity 'pvv' for level L1 (it compares quantity, pv, cash, financing); did you mean L1.pv?` |
| `CFG-BINDING` | a binding does not fit its callable | `[CFG-BINDING] instruments.usd_sofr_ois.bind.dv01: instrument 'usd_sofr_ois': binding 'dv01': 'dv01'(*, ctx: 'MarkContext', tenors: ...) -> 'float' cannot take args=0 kwargs=[]: missing a required keyword-only argument: 'ctx'` |
| `CFG-UNKNOWN-BINDING`, `CFG-REQUIRED-BINDING`, `CFG-FACTORY` | a bound name is not in the schema; a required schema name is unbound; the factory signature is wrong | `binding 'dvo1' is not a name of asset class 'swap'; bindable: [...]; did you mean ['dv01']?`; `required names of asset class 'swap' are not bound: [...]. Bind each in `bind:` (pricebt never resolves a schema name by a same-named method)`; `the factory must accept (pricer, ts, *, terms, conventions): too many positional arguments` |
| `CFG-CONVENTION`, `CFG-CONVENTION-UNSUPPORTED` | a conventions block that is incomplete, has an unknown token, or asks for what the library cannot do | `convention stub='long_front' is in the vocabulary but the reference stack does not implement it; it implements ['short_front']` |
| `CFG-BASELINE` (code) | `attribution.baseline: true` but an instrument does not bind `dv01`, `gamma`, `rate` | `attribution.baseline needs the instrument 'x' to bind [...] (the engine builds tay_delta and tay_convexity from dv01, gamma and rate)` |
| `CFG-GRID`, `CFG-STRATEGY`, `CFG-SIGNAL-NAME`, `CFG-ENUM`, `CFG-CALL` (code) | a `from_mdp` grid with no timestamps; an invalid strategy; a signal renamed; a bad enum; `$call` raised | `pkg.mod:Attr(**kwargs) failed: <type>: <message>` |
| `CFG-REQUIRED` (time) | a provider's `time:` block of mode `asof` with neither `max_staleness` nor `unbounded_ok: true`, on a provider that takes a `mapping` (`ToyMDP`) | `[CFG-REQUIRED] market.mdps.rates.time: an asof time mapping needs max_staleness (or unbounded_ok: true)` |
| `CFG-UNKNOWN-KEY` (time) | a `time:` block on a provider that takes no `time`, `mapping` or `**kwargs` (`SyntheticMarket`) | `[CFG-UNKNOWN-KEY] market.mdps.rates.time: pricebt.testing.synthetic:SyntheticMarket does not accept a time mapping` |
| `TIME` | `TimeMapping.__post_init__`: an unknown `mode` in a `time:` block (config route, `ToyMDP`), or the asof mistake above reached through `TimeMapping(...)` itself (a provider with a `time` parameter that builds its own mapping, or Python code; no shipped provider does, executed on a scratch one) | `[TIME] unknown time mode 'bogus'`; `[TIME] asof mapping needs max_staleness (or unbounded_ok: true)` |

Not config errors but common at run time: `MarketDataUnavailable` (`market data unavailable at <ts>: <reason>`: a missing snapshot, stale data or a missing fixing), `LookAheadError` (a snapshot stamped after the clock), `MethodCallError`, `MeasureError`
(`src/pricebt/errors.py`); the CLI prints them as `error: [<code or type>] <message>`, exit 2.

## 8. What `validate`, `describe` and `run --dry-run` do NOT check

`validate` loads everything and builds the object graph (providers, instruments with their factory signature and bindings, strategy) and runs nothing. It does not call the factory and fetches no snapshot, so it cannot see the CONTENT of a conventions block (section 5), a provider that fails at its first fetch, or a `wrap` that raises.
`run --dry-run` also fetches the FIRST pricer (`OK: first pricer AcmePricer at 2024-05-24 17:00:00-04:00 (reference_date 2024-05-24)`), which runs the provider and your `wrap` once. Neither builds a trade: use a short `run` (`--set backtest.grid.end=<3 days later>`) to exercise the factory and the conventions.
