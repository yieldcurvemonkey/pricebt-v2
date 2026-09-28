# Writing an asset config

An **asset config** is one self-contained YAML file. It is the only place pricebt talks to a
pricing or market-data library: it names which gs instrument class it prices, holds the import
lines and helper code that reach that library, and maps gs risk measures onto Python expressions
evaluated against that code. Full contract: [`DESIGN.md`](DESIGN.md) §4. This guide is a
practical walkthrough of the same material.

## Schema

```yaml
schema_version: 1                     # required, must be 1
asset: my_asset                       # unique id; defaults to the file stem
description: "..."                    # optional
instrument: IRSwap                    # required: a class name in pricebt.instrument
match: {notional_currency: USD}       # optional: equality rules on kwargs that select this asset
currency: USD                         # required ISO-4217: default currency of every ccy-unit function
defaults:                             # optional: fill kwargs that are absent/None, at resolve time
  pay_or_receive: Receive
imports: |                            # optional Python source, exec'd once (lazily) into a private namespace
  import os
code: |                               # optional Python source, exec'd once after imports, same namespace
  def helper(m): ...
market:
  expr: 'load_market(pricebt_date)'   # required: evaluated once per (market key, date, csa); None = unavailable
  key: my_market                      # optional sharing key (default: the asset name)
resolve:
  expr: 'resolve_swap(market, kwargs)'  # optional: (market at the TRADE date, kwargs) -> dict of plain terms
trade:
  expr: 'build_swap(market, resolved)'  # optional: (market, resolved) -> the library's trade object
  build_on: each_market                 # each_market (default) | resolve_date
functions:                            # required: per-trade functions, evaluated for ONE unit (quantity 1)
  npv: {expr: 'market.npv(trade)', unit: ccy}
portfolio_functions:                  # optional: functions over a COLLECTION of this asset's trades on one market
  delta_ladder:
    expr: 'delta_ladder(market, trades, weights, ("2Y","5Y","10Y"))'
    unit: ccy_per_bp
    returns: buckets                  # buckets -> dict[str, float] | scalar -> float
    labels: {mkt_type: IR}
attributes:                           # optional: values the gs engine reads with getattr() on a resolved instrument
  termination_date: 'resolved["termination_date"]'
size_attribute: termination_date      # optional: the attribute RebalanceAction(size_parameter=...) may name
risk_measures:                        # required: gs risk-measure NAME -> function
  Price: npv                          #   Price is REQUIRED (the engine books cash with it)
  IRDelta: {scalar: dv01, bucketed: delta_ladder}
```

An unknown key at any level is a `ConfigError` naming the key and, via `difflib`, a did-you-mean
suggestion. Duplicate YAML keys are also an error (`pricebt.assets.yamlio`). Every expression
string is compiled at load time (`compile(src, ..., "eval")`), so a typo fails the load with a
line number — see "Evaluation rules" below for what does *not* happen at load time.

## Injected variables

The only names pricebt puts into an evaluation (DESIGN §4.3):

| Name | Type | Available in | Meaning |
|---|---|---|---|
| `pricebt_date` | `date` | all | the pricing date; in `resolve` this is the **trade date** |
| `pricebt_timestamp` | tz-aware `Timestamp` | all | `pricebt_date` at the session's EOD time |
| `pricebt_datetime` | naive `datetime` | all | `pricebt_timestamp` without tz |
| `pricebt_csa` | `str` or `None` | all | the CSA in force (opaque to pricebt) |
| `pricebt_asset` | `str` | all except `market.expr` | the asset name |
| `pricebt_currency` | `str` | all except `market.expr` | the asset currency |
| `market` | whatever `market.expr` returned | resolve, trade, functions, portfolio_functions, attributes | the market object |
| `kwargs` | `dict` (fresh copy) | resolve (and trade/functions if `resolve` is absent) | the instrument's kwargs after `defaults` |
| `resolved` | `dict` (fresh copy) | trade, functions, attributes | `resolve`'s output |
| `trade` | whatever `trade.expr` returned | functions | the library trade object, for ONE unit |
| `trades`, `weights` | `list`, `list[float]` | portfolio_functions | this asset's trade objects on this market, and their weights |
| `pricebt_quantity` | `float` | attributes | the instrument's signed quantity multiplier |
| `base`, `quote` | `str` | FX config `rate` only | ISO codes |

Injected names **shadow** config names of the same spelling — do not define a helper named
`market`, `trade`, `kwargs`, `resolved`, `trades` or `weights`. `market.expr` itself only ever
sees `pricebt_date`/`pricebt_timestamp`/`pricebt_datetime`/`pricebt_csa`: no asset name or currency,
because a shared market (see below) has no single owning asset.

## Units: extensive vs. intensive

| unit | meaning | × quantity by default | FX-convertible |
|---|---|---|---|
| `ccy` | amount in the function's currency | yes (extensive) | yes |
| `ccy_per_bp` | currency per +1bp | yes (extensive) | yes |
| `ccy_per_bp2` | currency per bp² | yes (extensive) | yes |
| `bp` | basis points | **no** (intensive) | no |
| `pct` | percent | **no** (intensive) | no |
| `decimal` | plain rate (0.0425) | **no** (intensive) | no |
| `number` | dimensionless | yes (extensive) | no |
| `date` | a date (attributes only) | no | no |

An **extensive** measure scales with position size (`npv`, `dv01`): pricebt multiplies the unit
value by `quantity_` before returning it. An **intensive** measure is a *rate*, independent of
position size (`par_rate`, `carry_1m`): a 1mm and a 10mm swap on the same market have the same par
rate, so pricebt never scales it. Override the default with an explicit `scale_with_quantity:` on
the `functions:`/`portfolio_functions:` entry, if a measure's unit doesn't already say the right
thing. Requesting an FX conversion of a non-currency unit is a `ConfigError`.

## Evaluation rules

- **Loading a config never touches the external library.** `imports` and `code` are compiled at
  load time but **executed lazily** — once per process per asset, on the first evaluation of any
  of that asset's expressions. A config that imports ARBS is therefore safe to `load_asset()` in a
  unit test with no ARBS installed; only actually *pricing* something runs `imports`/`code`. If
  `imports` or `code` raises, the error is wrapped, cached, and re-raised on every later call
  without re-running it.
- **`market.expr` result means "no market" only via `None`.** Any exception is a real error and
  propagates (wrapped as `AssetEvaluationError`); a config that wants "an exception means
  unavailable" must catch it in a `code` helper and return `None` itself. See Appendix A's
  `load_market`, which does exactly this for a US holiday that ARBS raises on.
- **Resolve must pin every relative term at the trade date.** `resolve` runs once, at the
  instrument's resolution date, and its output is cached as the instrument's resolved terms for
  the rest of its life. A `'10y'` tenor or an `'ATM'` rate left as a *string* in the resolved dict
  is not "10 years from today" on every later pricing date — it is a literal string. `resolve`
  must convert every such term into an absolute `date` and a numeric rate before returning it.
  Get this wrong and P&L is silently corrupted (a swap that "grows" back to par every day it is
  priced). This is why `resolve`'s injected `pricebt_date` **is the trade date**, not whatever date
  a later `functions:` expression happens to run on — `resolve` only ever sees the one date its
  pinning decisions must be correct for.
- **Functions return a `float`.** `NaN` is allowed and propagates (used here for "undefined on a
  dead trade", e.g. `par_rate` after maturity). Portfolio functions with `returns: buckets` return
  `dict[str, float]`.
- **Security: configs are trusted code.** Both `imports` and `code` are `exec`'d, and every
  expression is `eval`'d, in a plain Python namespace with no sandboxing. Only load a config whose
  contents you trust — pricebt loads a config only from a path (or in-memory mapping) the calling
  code hands it (`PricebtSession.use(assets=[...])`, `load_asset(...)`); it never fetches one on
  its own.

## `build_on` and market sharing

`trade.build_on` controls when a trade object is (re)built from the market:

- **`each_market` (default).** The trade object is rebuilt on every date's market. Use this when
  the library's trade object is cheap and market-dependent (the toy `ToySwap` is a plain
  dataclass; see `tests/assets/toy_usd_irs.yaml`).
- **`resolve_date`.** The trade object is built **once**, on the resolution market, and reused —
  functions that need it on a later date must remark it themselves. Use this when a heavier
  library object stays valid but is expensive or unsafe to rebuild every day: ARBS's `RLIRSwapCurve`
  keeps stale fixings if you call `fair_rate` on a trade built from an earlier market, so Appendix A
  uses `build_on: resolve_date` and a `remark(market, trade)` helper (memoised per market) for the
  functions — `par_rate`, `carry_1m`, `roll_1m` — that need the *current* market's fixings.

**Market sharing.** Two assets with the same `market.key` share one evaluation: the market is
computed once per (key, date, csa), in the namespace of whichever asset registered that key first.
This only works if their `imports`, `code` and `market.expr` are byte-identical — pricebt checks
this at registration and raises `ConfigError` otherwise. A later asset that wants the *same curve*
under different pricing logic (e.g. a swaption sharing a swap's discount curve) gives its market
its **own** key instead, and builds on the shared curve from within its own `code`.

## FX configs

Multi-currency conversion (`currency='USD'` on a risk measure, or `run_backtest(result_ccy=...)`)
goes through a separate, equally self-contained FX config (DESIGN §4.5):

```yaml
schema_version: 1
fx: my_fx_table
code: |
  def rate(base, quote, d):
      ...  # QUOTE units per 1 BASE unit, or None if unavailable
rate: 'rate(base, quote, pricebt_date)'
```

`PricingService.fx(from_ccy, to_ccy, date)` returns `1.0` without evaluating anything when the two
currencies are equal; otherwise it evaluates `rate` with `base=from_ccy, quote=to_ccy`. A `None` or
non-positive result raises `MarketDataUnavailable`.

One FX config can serve **several currency pairs**, the way one asset config serves several
matching instruments: [`tests/assets/toy_fx.yaml`](../../tests/assets/toy_fx.yaml) answers both
`EURUSD` and its reciprocal `USDEUR` from a single `rate` function. Write a separate FX config only
when different pairs genuinely need different sourcing (a different data file, a different
provider). This mirrors how **asset** configs are matched: one instrument class can be served by
several asset configs distinguished by `match:` (e.g. `match: {notional_currency: USD}` vs. `EUR`
on two otherwise-identical `IRSwap` configs — see `tests/assets/toy_usd_irs.yaml` and
`toy_eur_irs.yaml`), and `AssetRegistry.match()` picks the one whose `match` rules fit the
instrument's kwargs, or raises if none or several do.

## Walkthrough: the toy USD swap

[`tests/assets/toy_usd_irs.yaml`](../../tests/assets/toy_usd_irs.yaml) is a real, already-working
config, pricing against the deterministic closed-form world in `tests/toylib/rates.py`:

- `imports: |  import toylib.rates as tr` — the one import; `tr` is then visible to every
  expression below.
- `market: {expr: 'tr.market(pricebt_date, "USD", pricebt_csa)', key: toy_usd_ois}` — `tr.market`
  returns a frozen `ToyCurve`, or `None` for a date in `tr.HOLES`.
- `resolve: {expr: 'tr.resolve_swap(market, kwargs)'}` — pins `effective_date`, `termination_date`
  and the fixed rate (parsing `'ATM'`/`'ATM+x'`) as of the trade date.
- `trade: {expr: 'tr.build_swap(market, resolved)', build_on: each_market}` — a plain
  `ToySwap` dataclass, cheap enough to rebuild on every market.
- `functions.npv/dv01/par_rate` call `tr.npv`/`tr.pv01`/`tr.par_rate`, with units `ccy`,
  `ccy_per_bp` and `bp` respectively — the last one intensive, so a 10x bigger notional gives the
  same par rate.
- `portfolio_functions.delta_ladder` buckets every trade's PV01 onto the nearest of four pillar
  tenors; `risk_measures.IRDelta` maps to `{scalar: dv01, bucketed: delta_ladder}` so a scalar
  `IRDelta(aggregation_level='Type')` request and a bucketed `IRDelta()` request both work off one
  config.

Load it and try it: `load_asset("tests/assets/toy_usd_irs.yaml")`, or run the whole notebook,
[`notebooks/040304_mean_reversion_toy.ipynb`](../../notebooks/040304_mean_reversion_toy.ipynb).

## Walkthrough: extending to a swaption (DESIGN §13)

DESIGN §13 is the design's own proof that adding an asset class needs no pricebt code change, only
a new config — the CI toy swaption
([`tests/assets/toy_usd_swaption.yaml`](../../tests/assets/toy_usd_swaption.yaml),
`tests/toylib/swaption.py`) is that proof at toy scale:

1. **The class.** `IRSwaption` already exists in `pricebt.instrument`, generated from the gs
   snapshot. No code change.
2. **The market** returns the curve *and* a vol surface as one object — the toy config's
   `market.expr` is `'ts.market(pricebt_date, "USD", pricebt_csa)'`, where `ts.market` returns
   `SimpleNamespace(curve=ToyCurve, sigma=float)`, under its **own** `market.key`
   (`toy_usd_swaption_vol`), distinct from a swap asset's key even though both curves come from the
   same underlying rates world.
3. **`resolve` pins `expiration_date`, `termination_date` and the strike**, and — because pricebt
   never reads `buy_sell` itself — folds `buy_sell` × `sign(notional_amount)` into one signed
   resolved notional (`toylib.swaption.resolve_swaption`); it also rejects an un-priceable
   `pay_or_receive` at resolve time, not later.
4. **Functions** `npv` (Bachelier/normal price) and `vega` (`ccy_per_bp`, per bp of normal vol);
   `attributes: {expiration_date: 'resolved["expiration_date"]'}` so
   `AddTradeAction(swaption, 'expiration_date')` can read its exit date; `risk_measures: {Price:
   npv, IRVega: {scalar: vega}}`.

Nothing in the engine, the pricing layer or the result objects changed to make this work — the
asset-agnostic guard (`tests/guards/test_asset_agnostic_scan.py`) fails the build if a later change
introduces a swap- or swaption-specific token into those layers.

## Security note

**Asset and FX configs are trusted code, not data.** `imports`/`code` are `exec`'d and every
expression is `eval`'d with no sandbox, no import allow-list and no resource limits inside the
config's own namespace — only load configs you trust, from paths you control.

## ARBS safety rules

[`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`](../../configs/assets/usd_sofr_ois_interest_rate_swap.yaml)
is the worked real-world example, reading ARBS's Eris store. Full evidence:
[`research/06-arbs-pricing-api.md`](research/06-arbs-pricing-api.md) §6. The rules it follows, and
that any new ARBS-backed config must also follow:

- **`os.environ["ARBS_SUPABASE_ENABLED"] = "0"` before any ARBS import, ever.** ARBS reads this
  variable once, at `import Caching.supabase_engine`, and defaults it to `True` — meaning a
  production Postgres database — if it is unset. Setting it after the first ARBS import is too
  late; the config's own `imports:` block raises `RuntimeError` if it detects this happened.
- **Only `ERIS_EOD_LIVE-RL_BASIC`, read store-only** (`bulk_get_data(..., ignore_cache_miss=True)`).
  The commented-out `-NOJUMPS` variant in the config is a hazard, not a convenience: unlike
  `RL_BASIC`, it hits the network for almost every date, disables TLS verification for the rest of
  the process, and **overwrites** the shared curve-store partition `USD-SOFR-1D/date=<d>` for
  whatever it fetches. Never enable it without the user's explicit, per-run approval.
- **`_LAST_SAFE = date(2026, 8, 20)`** is the boundary of what this config will ever price.
  `bulk_get_data` reads a SOFR fixing for the *previous* business day before it even looks in the
  curve store, and `ignore_cache_miss` does not protect that read: once a needed fixing falls
  outside ARBS's cached range, the call downloads from the NY Fed/FRED and **rewrites** the fixings
  cache. `load_market` returns `None` for any `d >= date.today()` or `d > _LAST_SAFE` specifically
  to stay inside the safe, already-cached range. Do not raise `_LAST_SAFE` without deliberately
  refreshing (and re-verifying) that range with the user's approval first.
- **No other ARBS source, ever.** `IRSwapsMDP(source=...)` variants beyond `ERIS_EOD_LIVE-RL_BASIC`
  (Citi/Excel sources in particular) can launch Excel via COM automation or reach other production
  systems; see research/06 §6.1's hazard table. A pricebt config for a new ARBS-backed asset starts
  from this file's `imports:`/`code:` pattern, not from a different ARBS source.
- A weekend or a US holiday (which ARBS's own calendar raises on, not returns `None` for) is caught
  in `load_market` and turned into a `None` return, per the "market unavailable" evaluation rule
  above — it is not an error condition for pricebt.
