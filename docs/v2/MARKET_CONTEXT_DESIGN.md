# Market providers and the gs market-data contexts

Status: implemented on branch `v2-market-context`. Code: `src/pricebt/assets/market.py`,
`src/pricebt/assets/pricing.py`, `src/pricebt/session.py`, `src/pricebt/markets/__init__.py`,
`src/pricebt/data/__init__.py`. Tests: `tests/test_market_provider.py`,
`tests/test_market_context.py`, `tests/test_data_context.py`. This document is the design and the
decisions behind it; [`DESIGN.md`](DESIGN.md) holds the contract text it changes (§3.2, §4.2–§4.4,
§4.6, §6.1–§6.3, §6.5, §10, §11) and [`ASSET_CONFIG_GUIDE.md`](ASSET_CONFIG_GUIDE.md) the how-to.

## 1. The problem

An asset config did three jobs: say **how to obtain the dated market object** (`market.expr`), how
to **resolve and build** the library's trade, and how to **price and risk** it. The first job is
not about the instrument at all. Two assets on one curve each carried a copy of the same market
expression, and could share its result only if their `imports`, `code` and `market.expr` were
byte-identical (the `market.key` rule); a swaption that wanted the swap's curve under its own
pricing code had to load that curve again. There was no first-class place for "the USD SOFR curve
of date d".

On the context side, `PricingContext` and `HistoricalPricingContext` existed with gs's signatures,
but only `pricing_date`, `csa_term` and `market` did anything: `market_data_location`, the
parameter gs uses to say *which market data*, was accepted and ignored, most gs properties were
missing, and `gs_quant.data.DataContext` (the date window of gs's data API) had no counterpart.

## 2. What was built

| Piece | gs counterpart | pricebt |
|---|---|---|
| **Market provider config** (`configs/markets/*.yaml`) | the server-side market GS builds for a `CloseMarket(date, location)` | one YAML file per market: `imports`, `code`, one `expr` → the dated market object; `requires:` builds on other providers |
| **`MarketRegistry`** (`session.markets`) | - | name → provider; `PricebtSession(markets=[...])`, `session.add_market(...)` |
| **`market: {provider: <name>}`** in an asset config | - | the asset references a provider instead of carrying `market.expr`; the inline form still loads |
| **`PricingContext.market_data_location`** | same | reaches every market as `pricebt_location`; one `PricingService` per location (DEV-M3) |
| **`PricingContext` properties and methods** | same names | all of gs's: `is_batch`, `use_cache`, `show_progress`, `provider`, `active_context`, `is_current`, `clone()`, `calc()`, ... (the parity file needs no exception for them any more) |
| **`show_progress`** | tqdm bar on context exit | one bar per outermost context, advanced as each date is priced |
| **`pricebt.data.DataContext`** | `gs_quant.data.DataContext` | a 1:1 port; `measure_series` takes its window from it (DEV-M4) |

Not built, on purpose: §8.

## 3. Market providers

### 3.1 The config

```yaml
schema_version: 1
market: usd_sofr_ois                 # the provider's name; defaults to the file stem
description: "..."                   # optional
imports: |                           # optional Python source, exec'd once (lazily) into a private namespace
  import my_lib
code: |                              # optional Python source, exec'd once after imports, same namespace
  def load_curve(d, location, csa): ...   # returns None when there is no market for d
requires: [other_provider]           # optional: providers whose market objects this one builds on
expr: 'load_curve(pricebt_date, pricebt_location, pricebt_csa)'   # required: the dated market object; None = unavailable
```

It follows the FX config (`assets/fx.py`) and the asset config in every respect: the hardened YAML
loader, unknown keys rejected with a did-you-mean, everything compiled at load, `imports`/`code`
executed lazily and once per process, `None` meaning "no market for this date", any exception a
real error wrapped as `AssetEvaluationError` (which names the provider), and configs being trusted
code. `load_market(path_or_mapping)` returns a frozen `MarketConfig`.

`expr` sees exactly these names:

| Name | Meaning |
|---|---|
| `pricebt_date`, `pricebt_timestamp`, `pricebt_datetime` | the market's date (at the session's EOD time, tz-aware and naive) |
| `pricebt_csa` | the CSA in force, opaque to pricebt |
| `pricebt_location` | the market data location of the pricing context (§5): `'LDN'` unless set |
| `markets` | `{name: market object}` of the providers listed under `requires:` (empty without one) |

These are the names an inline `market.expr` sees, plus `markets`. So moving a market out of an
asset config is cut and paste: the expression, and the part of `imports`/`code` it uses.

### 3.2 The registry and the session

```python
session = PricebtSession.use(
    markets=["configs/markets"],          # paths, directories (every *.yaml), mappings or MarketConfig objects
    assets=["configs/assets"],
    fx="configs/fx/my_fx.yaml",
)
session.markets                # MarketRegistry: names(), [name], in, len, add(...), add_one(...)
session.add_market(source)     # one more provider, later
```

Providers are registered **before** assets, and an asset naming a provider that is not registered
fails when the asset registers (`market.provider 'usd_sofr_oi' is not a registered market provider
(registered: [...]); did you mean 'usd_sofr_ois'?`), the way an unknown `pricebt_asset` does. A
provider's `requires:` must already be registered too, so providers always form a DAG; a directory
is registered in dependency order whatever its file names, and a circular `requires:` never loads.

`MarketRegistry` is standalone: it knows nothing about assets, and `AssetRegistry(markets=...)`
only asks it whether a name exists. `PricingService` reaches it as `registry.markets`, so its
constructor signature is unchanged.

### 3.3 Evaluation and sharing

`PricingService._provider_market(name, date, csa)` evaluates a provider **once per (provider,
date, csa)** per service, in the provider's own namespace, whichever assets ask. Consequences:

- Assets on one provider get the *same object*. They need not share `imports` or `code`: the
  byte-identical rule of inline `market.key` sharing does not apply.
- A provider with `requires:` receives the required providers' objects. A swap asset on a curve
  provider and a swaption asset on a vol provider that requires the curve price on **one** curve
  object per date (`tests/markets/`; inline, each asset loaded its own copy).
- If a required market is unavailable, the derived market is unavailable **without being
  evaluated**.
- A provider and an asset may have the same name; their namespaces are separate.

`has_market`, `market`, the missing-market handling of the engine (§9.5 of DESIGN) and every
downstream cache are unchanged: a provider is one more way `_get_market_raw(asset, date, csa)`
obtains the object.

## 4. The asset side

```yaml
market: {provider: usd_sofr_ois}     # a registered market provider
# or, as before:
market: {expr: 'load(pricebt_date)', key: my_market}   # an inline market
```

Exactly one form: `provider` together with `expr` or `key` is a load error, and so is neither.
`AssetConfig.market_provider` holds the name (`market_expr` is then `None`, and `market_key` is the
provider's name). Everything else in the asset config is untouched: `market` in `resolve`,
`trade`, `functions`, `portfolio_functions` and `attributes` is the provider's object.

**Why the inline form stays.** It is the smallest possible config (one file, one asset), every
existing config and skill uses it, and it prices identically. The provider form is what to use as
soon as a market is shared, composed, or worth naming.

**The shipped ARBS example is still inline.** `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`
is verified only by the opt-in live tier, which this work did not run (running it needs the
maintainer's go-ahead). Splitting it is mechanical (move `load_market` and what it needs into
`configs/markets/usd_sofr_eris_rlbasic.yaml`, keep the pricing helpers in the asset), but a split
that has not been run against the library is not shipped. `configs/markets/README.md` shows the
shape.

## 5. The pricing contexts

`pricebt.markets.PricingContext` and `HistoricalPricingContext` keep gs's constructor signatures
(pinned by the parity snapshot) and now have **every** gs property and method.

| Parameter | Effect in pricebt |
|---|---|
| `pricing_date`, `csa_term` | as before |
| `market` | None, or a `CloseMarket` (DEV-M1): its date overrides the market date; its location becomes the context's `market_data_location` (gs) |
| `market_data_location` | the location whose market every config is asked for: `pricebt_location` (DEV-M3) |
| `show_progress` | one progress bar per outermost context |
| `is_async` | read back; values are computed inside the block either way |
| `is_batch`, `use_cache`, `visible_to_gs`, `request_priority`, `timeout`, `use_server_cache`, `market_behaviour`, `set_parameters_only`, `use_historical_diddles_only`, `provider` | kept and read back through gs's property, inherited as in gs; they change nothing (there is no server to batch, cache or prioritise on) |

`active_context`, `is_current`, `default_value()`, `clone(**kwargs)` and `calc(instrument,
risk_measure)` are gs's. `market_location(location=None)` is exported next to `close_market_date`.

### 5.1 `market_data_location`

gs: the location for sourcing market data, `'LDN'` by default, inherited from the enclosing
context, and equal to the `market`'s location when a market is given (a mismatch raises
`ValueError`). pricebt does the same, and hands the code string to the configs:

```python
with PricingContext(pricing_date=d, market_data_location='NYC'):
    price = swap.price()                 # every market provider is evaluated with pricebt_location == 'NYC'
PricingContext.current = PricingContext(market_data_location='NYC')   # for the whole session (gs idiom)
```

`pricebt_location` is injected into **every** evaluation (provider expressions, inline
`market.expr`, `resolve`, `trade`, functions), next to `pricebt_date` and `pricebt_csa`. A config
that has one close ignores it.

**No value is shared between two locations.** Rather than thread a location through every cache
key, resolution key and instrument, `PricebtSession.pricing` is a property that returns **one
`PricingService` per location** (`PricingService.for_location`): the same registry, FX config and
once-per-process namespaces, its own caches. The engine, `measure_series` and the skills scripts
read `session.pricing` and therefore price on the location of the context around them; a lazy
bucketed value keeps the service it was requested under; `session.reset_caches()` clears every
location's.

Deviations (DEV-M3): the location is a code string, not a `PricingLocation` enum, and any string
is accepted (a config may define its own snapshots; gs logs a warning for a value outside the
enum). A `CloseMarket` given an explicit location still needs one pricebt knows the timezone of
(`NYC`, `LDN`, `HKG`, `TKO`), as in gs; one that takes the context's location rolls its close in
that location's timezone, or in London's when the location is a config-defined one.

### 5.2 `show_progress`

gs draws a tqdm bar when the context exits, counting the pending calculations. pricebt computes
inside the block, so the bar advances there: one bar belongs to the context's `active_context`
(the outermost entered context), is opened by the first calculation under it, ticks once per
date priced or resolved, and closes when that context exits. A context's bar inside a running
backtest's bar stays silent.

## 6. `DataContext` and time series

`pricebt.data.DataContext(start=None, end=None, interval=None)` is gs's class: `start_date`,
`end_date` (default: the last 30 days), `start_time`, `end_time` (default: the last day, UTC),
`interval` (validated `[1-9]\d{0,2}[a-z]`), `DataContext.current` (the innermost entered context,
else the assigned default, else a fresh one), usable as a `with` block. Nothing is inherited from
an enclosing context, as in gs.

In gs it is the window `Dataset` and `DataCoordinate` requests default to. In pricebt
`measure_series(instrument, measure, start=None, end=None, ...)` reads it the same way:

```python
with DataContext(date(2024, 1, 2), date(2024, 12, 31)):
    dv01 = measure_series(IRSwap('Pay', '10y', 'USD'), IRDelta(aggregation_level='Type'))
    vega = measure_series(IRSwaption('Pay', '10y', 'USD', expiration_date='1y'), IRVega(aggregation_level='Type'))
```

Deviation (DEV-M4): an `interval` that is not a string raises `TypeError` (gs: `MqTypeError`, which
is not a `TypeError`; pricebt has no `Mq*` error classes), and there is no async form.

### 6.1 Two ways to a time series

| | `HistoricalPricingContext` + `calc` | `measure_series` |
|---|---|---|
| gs counterpart | the same | `Dataset` / a timeseries measure under `DataContext` |
| instrument | resolved: one fixed trade through time; unresolved: re-resolved on each date (a rolling par swap) | always re-resolved on each date |
| measures | any, several at once, bucketed too (`DataFrameWithInfo` indexed by date) | one scalar measure or config function name |
| a date with no market | raises `MarketDataUnavailable`: pass `dates=` you know exist | missing, forward-filled (`fill='ffill'`) or dropped |
| result | `SeriesWithInfo` / `MultipleRiskMeasureResult` / `PortfolioRiskResult` | `pandas.Series` with `attrs['unit']` |

## 7. The gs manual backtest

`gs_quant/content/made_with_gs_quant/4-Delta Hedging.ipynb` builds its backtest by hand:

```python
with HistoricalPricingContext(start=start_date, end=end_date, show_progress=True):
    f = IRSwaption(PayReceive.Straddle, '10y', Currency.USD, expiration_date='1m',
                   notional_amount=1e8, buy_sell='Sell').resolve(in_place=False)
portfolio = Portfolio([v[1] for v in sorted(f.result().items())])

with PricingContext(is_batch=True, show_progress=True):
    for inst, row in frame.iterrows():
        with HistoricalPricingContext(start=row.trade_date, end=row.expiration_date, is_async=True):
            so_price = inst.price()
            so_delta = inst.calc(IRDeltaParallel)
```

Cells 4 to 15 of that notebook (resolve a new straddle on every date, the portfolio as a frame,
price and delta of each trade from its trade date to its expiry, a new ATM swap resolved each day,
the lagged unwind under a per-date `PricingContext`) run on pricebt with the imports changed, on a
swap asset and a swaption asset backed by two market providers:
`tests/test_market_context.py::test_the_gs_delta_hedging_notebook_pattern_runs_on_market_providers`.
The differences in that test are not pricebt's: the toy swaption config resolves no
`effective_date` and its swap knows `'ATM'` rather than `'ATMF'` (a config decides its own resolved
terms), and pandas 2 removed `Series.iteritems()` and shifts an object column with `None`.

The `GenericEngine` path is unchanged, and a backtest on a provider-backed asset equals the same
backtest on the inline config (`tests/test_market_provider.py`).

## 8. Deliberately not built

| Not built | Why | When to add it |
|---|---|---|
| A real `Dataset` / `DataCoordinate` over the provider registry | no present use: instrument measures cover the series asked for, and a market observable can be read through an instrument (`measure_series(swap, 'par_rate')`) | when a market series is needed that no instrument expresses; a provider would then declare named `fields:` |
| `LiveMarket`, `TimestampedMarket`, `OverlayMarket`, `RefMarket` as `PricingContext(market=...)` | a provider answers "the market of a date"; a live or bumped market needs a second kind of expression and uncached evaluation (DEV-M1 still raises `NotSupportedError`) | when a library with a live market is connected |
| `PricingContext(provider=...)` choosing who prices | gs's provider is a risk API class; in pricebt the asset config prices | - |
| Splitting the ARBS config | §4: it cannot be verified without the live tier | on the maintainer's go-ahead |
| Holiday-tolerant `HistoricalPricingContext` | gs semantics kept: the dates given are the dates priced | use `dates=` or `measure_series` |

## 9. Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | A provider is a config file of its own, loaded like the FX config, not a section of the session or Python code | "following all patterns currently implemented": YAML, expression strings, lazy namespace, no pricing-library name in `src/` (MUST-1) |
| D2 | `market: {provider: name}`, a mapping, not a bare string | explicit, and the section keeps one shape for both forms |
| D3 | The inline form stays | §4 |
| D4 | `requires:` on the provider, not several providers on the asset | a derived market is evaluated once and shared; the asset still sees one `market` |
| D5 | Requirements must already be registered | no cycle detection needed at evaluation time; the error is at load |
| D6 | One `PricingService` per location | §5.1: correct cache isolation with no change to any cache key, resolution key or the engine |
| D7 | Location default `'LDN'`, as gs | parity; a config with one close ignores it, and `PricingContext.current = PricingContext(market_data_location=...)` sets it once |
| D8 | `DataContext` lives in `pricebt.data` with its own small stack | gs's import path; `markets` must not import upwards (import DAG), and the PricingContext stack is not shared |
| D9 | The server-side context parameters are stored, not validated | they are read back by ported code; giving them meaning would be inventing behaviour |
