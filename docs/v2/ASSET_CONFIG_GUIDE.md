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
# unsupported_measures:               # Bond only (an IRSwap/IRSwaption config must map every contract measure, so
#   LightningOAS: "no OAS model in my_lib"    #   this block is a load error here): measure -> reason; see "Measure contracts" below
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
  cashflows: {expr: 'market.flows(trade)', unit: ccy, returns: frame, scale_columns: [payment_amount]}  # a table measure
portfolio_functions:                  # optional: functions over a COLLECTION of this asset's trades on one market
  delta_ladder:
    expr: 'delta_ladder(market, trades, weights, ("2Y","5Y","10Y"))'
    unit: ccy_per_bp
    returns: buckets                  # buckets -> dict[str, float] or a list of row dicts | scalar -> float
    labels: {mkt_type: IR}
attributes:                           # optional: values the gs engine reads with getattr() on a resolved instrument
  termination_date: 'resolved["termination_date"]'
size_attribute: termination_date      # optional: the attribute RebalanceAction(size_parameter=...) may name
risk_measures:                        # required: gs risk-measure NAME -> function
  Price: npv                          #   Price is REQUIRED (the engine books cash with it)
  IRDelta: {scalar: dv01, bucketed: delta_ladder}
  Cashflows: cashflows
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
| `pricebt_bump_size`, `pricebt_finite_difference_method`, `pricebt_local_curve`, `pricebt_scale_factor` | the value or `None` | functions, portfolio_functions | the requested measure's parameter (e.g. `IRDelta(bump_size=5)`). A function supports a parameter only if its expression names the variable; otherwise requesting it raises `NotSupportedError` (DEV-I10) |
| `market_to`, `pricebt_to_date` | the market of the target date and that date, or `None` | functions, portfolio_functions | a relative measure's target (`PnlExplain(CloseMarket(date=...))`, DEV-M2); the mapped function must name one of them. Under `PricingContext(market=CloseMarket(date=t))`, `market` is the market of `t` while `pricebt_date` stays the pricing date (DEV-M1) |
| `base`, `quote` | `str` | FX config `rate` only | ISO codes |

Injected names **shadow** config names of the same spelling — do not define a helper named
`market`, `market_to`, `trade`, `kwargs`, `resolved`, `trades` or `weights`. They are visible in
nested scopes of an expression too (a generator, comprehension or lambda:
`'sum(lib.pv(market, t) * w for t, w in zip(trades, weights))'` works), but not inside a `code:`
helper, which sees only its asset's namespace: pass what it needs as arguments. `market.expr` itself only ever
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
- **Functions return a `float`.** pricebt does not reject `NaN`, but it propagates, and on a dead
  trade the rule is sensitivities `0.0` and levels finite and continuous (R2-7; see "Dead
  instruments" below): never `NaN`, e.g. `par_rate` after maturity stays the last par rate. Portfolio functions with `returns: buckets` return
  `dict[str, float]`, or a list of row dicts with keys among `mkt_type, mkt_asset, mkt_class,
  mkt_point, mkt_quoting_style, value` (`value` required; `labels` fill the missing coordinates).
  A `functions:` entry with `returns: frame` returns a DataFrame or a list of dicts (`[]` is
  allowed); quantity scales only its `scale_columns`, and it is never FX-converted.
- **What a request returns** (DESIGN §8.2). A scalar function gives a `FloatWithInfo` (with
  `.unit` and `.risk_key`); a bucketed request a `DataFrameWithInfo` with the six columns
  `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value`; a `returns: frame`
  function a table `DataFrameWithInfo` with its own columns (DEV-R11). Under a
  `HistoricalPricingContext` a scalar becomes a `SeriesWithInfo` indexed by date, a bucketed result
  one `DataFrameWithInfo` indexed by `date` (a date with no buckets simply has no rows), and a
  table one table with a `date` column first.
- **Measure contracts.** An `IRSwap` or `IRSwaption` config must **map** every measure of its
  class's contract: declaring a contract measure under `unsupported_measures:` is a load error. A
  `Bond` config must map every measure of its contract or declare it with a reason. The load error
  lists every gap and prints a paste-ready block: a mapping skeleton for IRSwap/IRSwaption, a
  declaration block for Bond (DEV-I11; the tables are in "Measure contracts" below). A preset or
  LocalCcy key (`IRDeltaParallel`, `IRGammaParallelLocalCcy`, ...) counts toward its base measure
  and also prices the base's requests. On a Bond, declare the base measure, never a preset: a
  declared preset name, a form outside the contract row, or an unknown name loads with a warning.
- **Security: configs are trusted code.** Both `imports` and `code` are `exec`'d, and every
  expression is `eval`'d, in a plain Python namespace with no sandboxing. Only load a config whose
  contents you trust — pricebt loads a config only from a path (or in-memory mapping) the calling
  code hands it (`PricebtSession.use(assets=[...])`, `load_asset(...)`); it never fetches one on
  its own.

## Measure contracts (IRSwap, IRSwaption, Bond)

gs answers every IR measure on every IR instrument (a swap's vega is 0) and returns
`UnsupportedValue` for what its server cannot compute. pricebt has no server, so a config whose
`instrument:` has a contract must say, **for every measure and form of that contract**, how to
compute it, or, for a `Bond` only, why it cannot (DEV-I11; design:
[`IR_RISK_DESIGN.md`](IR_RISK_DESIGN.md) §2 and §00, and for the strict rule
[`IR_STRICT_CONTRACT.md`](IR_STRICT_CONTRACT.md); code: `src/pricebt/risk/contracts.py`). Classes
without a contract (`FXOption`, `EqOption`, `InflationSwap`, `Cash`, `FXForward`,
`ConfigInstrument`) keep the plain rule: only `Price` is required.

A form of a contract row is satisfied when:
- a `risk_measures:` slot maps it to a function whose unit is allowed for the row's kind (and that
  is intensive when the kind says so; a `table` row needs a `returns: frame` function), or
- **Bond only:** `unsupported_measures:` declares the whole measure or that form, with a non-empty
  reason.

**IRSwap and IRSwaption are strict** (`contracts.STRICT_CLASSES`): only a mapping satisfies a row,
and declaring a contract measure (the measure, one of its forms, or a preset/fallback resolving to
one, such as `IRDeltaParallel`, `IRGammaParallelLocalCcy` or `PnlExplainClose`) is itself a load
error, mapped or not. Declaring a name outside the contract keeps the warnings below. A measure
the contract text defines as 0 for the class (`contracts.ZERO_BY_CONVENTION`: vol measures on a
swap, `IRBasis` for a single-curve library, `IRXccyDelta` for a single-currency instrument) maps
honestly to a literal `'0.0'` function; a literal constant for any other contract measure is a
checker FAIL. Every IR-relevant `pricebt.risk` measure outside the contract is listed, with the
reason, in `contracts.EXCLUDED`.

Anything else fails the load with **one** `ConfigError` that lists every gap. For a Bond it ends
with a paste-ready `unsupported_measures:` block (`TODO` reasons; replace each with the real
reason, the `pricebt-verify-asset-config` checker flags reasons still starting with `TODO`). For
IRSwap/IRSwaption it ends with a paste-ready **mapping skeleton** (`contracts.mapping_skeleton`): a
`functions:` stub per missing scalar or frame form and a `portfolio_functions:` stub per missing
bucketed form, each with an allowed unit, then the `risk_measures:` lines. Every stub's `expr` is
`'... TODO'`, which does not compile, so the skeleton loads only once each stub computes its
measure. A preset or
fallback key counts toward its base measure (`IRDeltaParallel` → the `IRDelta` scalar,
`IRGammaParallelLocalCcy` → `IRGammaParallel`) and also prices the base's requests. Measure names
outside the contract (e.g. a custom `IRTheta`) are unrestricted.

<!-- BEGIN generated contract tables: tests/test_docs_contract_tables.py; do not edit by hand -->
| Kind | Allowed units | Must be intensive (`scale_with_quantity: false`) |
|---|---|---|
| `value` | `ccy` | - |
| `sens1` | `ccy_per_bp` | - |
| `sens2` | `ccy_per_bp2` | - |
| `theta` | `ccy` | - |
| `annuity` | `ccy` | - |
| `rate` | `bp`, `decimal`, `pct` | yes |
| `vol` | `bp`, `decimal`, `pct` | yes |
| `time` | `decimal`, `number` | yes |
| `prob` | `decimal`, `number` | yes |
| `notional_level` | `bp`, `decimal`, `number`, `pct` | yes |
| `days` | `number` | yes |
| `table` | frame (`returns: frame`) | - |

**`IRSwap`** (27 measures)

| Measure | Kind | Forms | Contract |
|---|---|---|---|
| `Price` | `value` | scalar | PV in the function currency, holder-signed; it either drops each flow on its payment date (Cashflows then lists the flows still to drop) or never drops paid flows (total return: Cashflows is empty). |
| `IRDelta` | `sens1` | scalar, bucketed | s: TOTAL derivative of Price w.r.t. the own rate r (IRFwdRate) along the library's parallel curve shift, own-strike vol fixed: [PV(+h)-PV(-h)]/[r(+h)-r(-h)] per bp of r (a fixed-annuity pv01 is exact only at the money); b: curve ladder, ccy per +1bp at each pillar, labels.mkt_type IR. Pay-fixed swap > 0, payer swaption > 0, long bond < 0. IRDeltaParallel/IRDeltaLocalCcy resolve here (DEV-I12). |
| `IRDiscountDeltaParallel` | `sens1` | scalar | PV change for a +1bp parallel shift of the discount curve only: forwards (projection) held fixed, only discount factors bumped. Not in general equal to the IRDelta scalar; for a single-curve library this is NOT the parallel dv01 (near zero for an at-the-money swap). IRDiscountDeltaParallelLocalCcy falls back here. |
| `IRGammaParallel` | `sens2` | scalar | chain-rule second derivative of Price w.r.t. the own rate r on the IRDelta bumps, per bp^2 of r: [n+ + n- - 2n0 - ((n+ - n-)/(r+ - r-))(r+ + r- - 2r0)] / ((r+ - r-)/2)^2; never d(pv01)/dr (half the gamma at the money). IRGammaParallelLocalCcy falls back here (DEV-I16). |
| `IRGamma` | `sens2` | bucketed | diagonal gamma ladder, ccy per bp^2 at each pillar, a 6-column bucketed frame (DEV-I13: gs returns a 12-column cross-gamma frame). The diagonal may be a true Hessian diagonal or the parallel gamma placed at its nearest pillar (implementations differ, DEV-I13). |
| `IRVega` | `sens1` | scalar, bucketed | s: PV change for +1bp of normal implied vol (IRAnnualImpliedVol); b: vol cube, mkt_point '<tail>;<expiry>' (e.g. '5Y;1Y'), labels.mkt_type IR VOL. Swaps/bonds: 0.0 / empty by convention (R2-8). IRVegaParallel/IRVegaLocalCcy resolve here. |
| `IRVanna` | `sens2` | scalar | d(IRDelta scalar)/d(sigma) per bp x bp (rate bp x normal-vol bp); swaps/bonds 0.0. Request as IRVanna(aggregation_level='Type') (FD measure, DEV-I9). |
| `IRVolga` | `sens2` | scalar | second derivative of Price w.r.t. normal vol, per bp^2 of vol; swaps/bonds 0.0. Request as IRVolga(aggregation_level='Type') (DEV-I9). |
| `IRBasis` | `sens1` | scalar | PV change for +1bp of the basis (projection-vs-discount) spread; single-curve libraries: 0.0. IRBasisParallel resolves here; or request IRBasis(aggregation_level='Type'). |
| `IRXccyDelta` | `sens1` | scalar | cross-currency basis delta; single-currency instruments: 0.0. IRXccyDeltaParallel resolves here; or request IRXccyDelta(aggregation_level='Type'). |
| `IRFwdRate` | `rate` | scalar | the own quoted rate: swap par rate, swaption forward rate of the underlying, bond yield to maturity (DEV-I12). Intensive; finite on every held date including the exit date (R2-7). |
| `IRSpotRate` | `rate` | scalar | the par rate of the spot-starting equivalent (swap / swaption underlying with the same final date); bond: its yield. Intensive. |
| `IRAnnualImpliedVol` | `vol` | scalar | annualised NORMAL implied vol at the instrument's strike; swaps/bonds: 0.0 by convention (R2-8). Intensive. |
| `IRAnnualATMImpliedVol` | `vol` | scalar | ATM-forward normal vol for the same expiry and tail; swaps/bonds: 0.0 (R2-8). Intensive. |
| `IRDailyImpliedVol` | `vol` | scalar | IRAnnualImpliedVol / sqrt(252); swaps/bonds: 0.0 (R2-8). Intensive. |
| `Theta` | `theta` | scalar | one calendar day of carry holding the own IRFwdRate and IRAnnualImpliedVol fixed: Price(t+1d) + cashflows Price drops in (t, t+1d] - Price(t), ccy PER DAY (DEV-I15; curve translated DF(x)/DF(t+1d), never rolled). A per-year IRTheta = 365 x Theta: never map Theta to a per-year function. A discrete own-rate jump when a paid period leaves the remaining schedule is a schedule-roll term; a config that removes it from the own-rate move (holding the own rate fixed) spreads it over the calendar days to the next business day, so Theta x step days counts it once on a business-day grid; on coarser grids the excess lands in the residual. |
| `ExpiryInYears` | `time` | scalar | max(final_or_expiry - t, 0).days / 365 (calendar days, ACT/365F): a swaption's expiry, a swap's or bond's final date (DEV-I17). Intensive. It stays 0 from expiry on, so PNL_theta (Theta x change in ExpiryInYears x -365) attributes no carry after it: an exercised swaption's Theta (the underlying swap's, R2-7) lands in the residual. |
| `Annuity` | `annuity` | scalar | PV of the fixed leg paying 1.0 per annum (1e4 x the fixed-leg pv01), ccy; bond: PV of 1.0 per annum on its schedule. Holder-signed like Price: pay-fixed swap > 0, receive-fixed < 0; bought swaption > 0, payer or receiver (the underlying's annuity on the swaption's signed notional); long bond > 0. A library whose fixed-leg bp value carries the fixed leg's own sign (negative for a payer) needs Annuity = -1e4 x that value. |
| `Cashflows` | `table` | frame | the flows still included in Price that Price will drop on their payment date (payment_date > pricing date), holder-signed, one row each; empty for a total-return Price (R2-6). Required columns payment_date, payment_amount, currency, payment_type; returns: frame with scale_columns including payment_amount. |
| `ParSpread` | `rate` | scalar | the spread, in the declared rate unit, added to the floating leg's rate that makes Price zero; independent of direction (payer and receiver of the same terms share it). Single curve with matching leg schedules: fixed_rate - IRFwdRate; swaption: the underlying swap's (strike - forward). Dead instruments: continuous with the last live value (R2-7). Intensive. |
| `FairPremium` | `value` | scalar | the premium, paid by the holder on the premium settlement date, that makes the instrument plus premium worth zero: Price / DF(settlement), ccy. Settlement: the swaption's premium_payment_date if the library supports it and it is set, else the library's spot date for the currency; a library with no spot lag uses the pricing date, so FairPremium == Price (DEV-I19). |
| `ForwardPrice` | `value` | scalar | Price forward-valued to the expiry date ExpiryInYears counts to (a swaption's expiration_date, a swap's termination date, DEV-I17): Price / DF(expiry), ccy; on or after expiry: Price. DEV-I19: gs declares the unit BPS but documents the price at expiry in the local currency; pricebt follows the docstring. |
| `PremiumCents` | `notional_level` | scalar | Price / \|notional\| in the declared unit, \|notional\| the unit trade's absolute notional_amount; in bp this is gs's premium in cents (1 cent per 100 of notional = 1bp of notional). Intensive (DEV-I19). |
| `LocalAnnuityInCents` | `notional_level` | scalar | Annuity / \|notional\|: the PV of 1.0 per annum per unit of notional, holder-signed like Annuity (a 10y pay-fixed swap is about +8.5); declare unit decimal, or number with scale_with_quantity: false. It equals the PV in cents, per 100 of notional, of 1bp per annum. Intensive (DEV-I19). |
| `CompoundedFixedRate` | `rate` | scalar | the fixed rate (swaption: the strike) restated as an annually compounded rate: (1 + K/f)^f - 1 for a fixed leg paying f times a year (an annual fixed leg: K itself). A trade term, finite on every date. Intensive (DEV-I19). |
| `CRIFIRCurve` | `table` | frame | ISDA SIMM CRIF rows for IR curve delta, one per ladder pillar. Required columns RiskType ('Risk_IRCurve'), Qualifier (the currency ISO code), Bucket (the SIMM currency volatility group as a string; '1' for regular-volatility currencies such as USD and EUR), Label1 (SIMM tenor, lower case, one of 2w 1m 3m 6m 1y 2y 3y 5y 10y 15y 20y 30y), Label2 (the ISDA SIMM sub-curve name, e.g. 'OIS'; a SOFR curve is 'OIS'), Amount (PV change for +1bp at that pillar, in AmountCurrency, holder-signed, on the basis of the config's own IRDelta bucketed ladder), AmountCurrency; returns: frame with scale_columns including Amount. Identity: sum of Amount = sum of the IRDelta bucketed ladder. Dead instrument: an empty frame with these columns. DEV-I19: gs returns the full CRIF schema; pricebt requires this subset. |
| `PnlExplain` | `value` | bucketed | the change in value from market to market_to by risk factor, no time component (IR_RISK_DESIGN section 8): a returns: buckets portfolio function (hence the bucketed form) that also receives market_to and pricebt_to_date, rows labelled by mkt_type (IR, IR VOL, ...), ccy. Swaps: one IR row = Price(market_to) - Price(market), plus an optional IR VOL row of 0. Allowed caveat: a library whose market objects carry their own valuation date (it cannot value a later market from the pricing date) includes the carry between the two dates. PnlExplainClose resolves here. |

**`IRSwaption`** (28 measures)

| Measure | Kind | Forms | Contract |
|---|---|---|---|
| `Price` | `value` | scalar | as `IRSwap` |
| `IRDelta` | `sens1` | scalar, bucketed | as `IRSwap` |
| `IRDiscountDeltaParallel` | `sens1` | scalar | as `IRSwap` |
| `IRGammaParallel` | `sens2` | scalar | as `IRSwap` |
| `IRGamma` | `sens2` | bucketed | as `IRSwap` |
| `IRVega` | `sens1` | scalar, bucketed | as `IRSwap` |
| `IRVanna` | `sens2` | scalar | as `IRSwap` |
| `IRVolga` | `sens2` | scalar | as `IRSwap` |
| `IRBasis` | `sens1` | scalar | as `IRSwap` |
| `IRXccyDelta` | `sens1` | scalar | as `IRSwap` |
| `IRFwdRate` | `rate` | scalar | as `IRSwap` |
| `IRSpotRate` | `rate` | scalar | as `IRSwap` |
| `IRAnnualImpliedVol` | `vol` | scalar | as `IRSwap` |
| `IRAnnualATMImpliedVol` | `vol` | scalar | as `IRSwap` |
| `IRDailyImpliedVol` | `vol` | scalar | as `IRSwap` |
| `Theta` | `theta` | scalar | as `IRSwap` |
| `ExpiryInYears` | `time` | scalar | as `IRSwap` |
| `Annuity` | `annuity` | scalar | as `IRSwap` |
| `Cashflows` | `table` | frame | as `IRSwap` |
| `ParSpread` | `rate` | scalar | as `IRSwap` |
| `FairPremium` | `value` | scalar | as `IRSwap` |
| `ForwardPrice` | `value` | scalar | as `IRSwap` |
| `PremiumCents` | `notional_level` | scalar | as `IRSwap` |
| `LocalAnnuityInCents` | `notional_level` | scalar | as `IRSwap` |
| `CompoundedFixedRate` | `rate` | scalar | as `IRSwap` |
| `CRIFIRCurve` | `table` | frame | as `IRSwap` |
| `PnlExplain` | `value` | bucketed | as `IRSwap` |
| `ProbabilityOfExercise` | `prob` | scalar | probability (0..1) of finishing in the money under the annuity measure. Intensive. |

**`Bond`** (40 measures)

| Measure | Kind | Forms | Contract |
|---|---|---|---|
| `Price` | `value` | scalar | the position's settlement-date market value: (clean price + accrued interest at standard settlement) per 100 face x face / 100, holder-signed (a long is positive), not discounted from the settlement date to the pricing date (DEV-I20). It drops a flow on the first trade date whose standard settlement is on or after the flow's payment date; Cashflows lists the flows still to drop. |
| `IRDelta` | `sens1` | scalar, bucketed | as `IRSwap` |
| `IRDiscountDeltaParallel` | `sens1` | scalar | as `IRSwap` |
| `IRGammaParallel` | `sens2` | scalar | as `IRSwap` |
| `IRGamma` | `sens2` | bucketed | as `IRSwap` |
| `IRVega` | `sens1` | scalar, bucketed | as `IRSwap` |
| `IRVanna` | `sens2` | scalar | as `IRSwap` |
| `IRVolga` | `sens2` | scalar | as `IRSwap` |
| `IRBasis` | `sens1` | scalar | as `IRSwap` |
| `IRXccyDelta` | `sens1` | scalar | as `IRSwap` |
| `IRFwdRate` | `rate` | scalar | as `IRSwap` |
| `IRSpotRate` | `rate` | scalar | as `IRSwap` |
| `IRAnnualImpliedVol` | `vol` | scalar | as `IRSwap` |
| `IRAnnualATMImpliedVol` | `vol` | scalar | as `IRSwap` |
| `IRDailyImpliedVol` | `vol` | scalar | as `IRSwap` |
| `Theta` | `theta` | scalar | carry per calendar day with the yield (IRFwdRate) held fixed, over the step to the next business day nb: [Price(nb, same yield) + flows Price drops in (t, nb] - Price(t)] / (nb - t).days, ccy per day (DEV-I15). Price is a settlement-date value, so one calendar day can move settlement by 0 or 3 days; spreading the next-business-day step makes Theta x step days exact on a business-day grid. Financing is not in Theta (FinancingToDate). |
| `ExpiryInYears` | `time` | scalar | as `IRSwap` |
| `Annuity` | `annuity` | scalar | as `IRSwap` |
| `Cashflows` | `table` | frame | the flows still included in Price, one row each, holder-signed; payment_date is the trade date on which Price drops the flow: the first date whose standard settlement is on or after the flow's payment date (T+1: the business day before a business-day coupon date). Required columns payment_date, payment_amount, currency, payment_type; returns: frame with scale_columns including payment_amount. A financed position's engine books these rows as cash on payment_date (DEV-E22). |
| `LightningDV01` | `sens1` | scalar | yield DV01: Price change for +1bp of yield (= the IRDelta scalar for a bond). |
| `LightningOAS` | `rate` | scalar | option-adjusted spread over the library's reference curve (a bullet bond: its Z-spread). Intensive. |
| `ParSpread` | `rate` | scalar | par asset-swap spread (or the library's par spread) in the declared unit. Intensive. |
| `FairPremium` | `value` | scalar | the amount the holder pays at standard settlement for the position: Price itself, since Price is already the settlement-date value (DEV-I20). ccy. |
| `ForwardPrice` | `value` | scalar | the forward (financed) value at the horizon H = settlement + 1 calendar month (following business day): Price x (1 + RepoRate x tau(s, H)) - sum over flows c paid in (s, H] of C x (1 + RepoRate x tau(c, H)), tau in the repo day count, RepoRate held flat to H; ccy, holder-signed. Dead (nothing left to pay): 0 (DEV-I21; the swap and swaption rows forward to expiry instead). |
| `PremiumCents` | `notional_level` | scalar | Price / \|face\| in the declared unit (pct: the dirty price per 100). Intensive (DEV-I19). |
| `LocalAnnuityInCents` | `notional_level` | scalar | Annuity / \|face\|: the PV of 1.0 per annum per unit of face. Intensive (DEV-I19). |
| `CompoundedFixedRate` | `rate` | scalar | the coupon restated as an annually compounded rate: (1 + c/f)^f - 1 for f coupons a year. A trade term, finite on every date. Intensive (DEV-I19). |
| `CRIFIRCurve` | `table` | frame | as `IRSwap` |
| `PnlExplain` | `value` | bucketed | the change in value from market to market_to by risk factor, no time component (IR_RISK_DESIGN section 8): a returns: buckets portfolio function receiving market_to and pricebt_to_date, rows labelled by mkt_type (IR for the curve, e.g. CREDIT for the bond's spread to it), summing to Price(market_to) - Price(market), ccy. PnlExplainClose resolves here. |
| `CleanPrice` | `notional_level` | scalar | the quoted clean price per 100 face for standard settlement: DirtyPrice - 100 x AccruedInterest / face (signed face). Dead (Price 0): 0. Intensive (DEV-I20). |
| `DirtyPrice` | `notional_level` | scalar | 100 x Price / face (signed face): the invoice price per 100, the same for a long and a short. Dead (Price 0): 0. Intensive (DEV-I20). |
| `AccruedInterest` | `value` | scalar | the coupon accrued from the last coupon date to the standard settlement date in the bond's accrual convention (US Treasuries: ACT/ACT ICMA), holder-signed, ccy; 0 when settlement is a coupon date and after maturity (DEV-I20). |
| `ModifiedDuration` | `time` | scalar | -(1/P) dP/dy in years per unit of decimal yield, y in the IRFwdRate convention, P the dirty price; 0 when dead. Intensive (DEV-I20). |
| `Convexity` | `time` | scalar | (1/P) d2P/dy2 in years^2, y and P as ModifiedDuration; 0 when dead. Intensive (DEV-I20). |
| `DaysToSettlement` | `days` | scalar | calendar days from the pricing date to the standard settlement date (US Treasuries T+1: 1, or 3 over a weekend or a holiday). Intensive (DEV-I20). |
| `RepoRate` | `rate` | scalar | the funding rate in force on the pricing date for this position: overnight general collateral or special, or a term rate locked at the trade date; the library or the data decides. Simple interest in the config's repo day count (USD: ACT/360). Finite on every held date. Intensive (DEV-I21). |
| `RepoHaircut` | `rate` | scalar | the fraction of the settlement value not financed, in the declared unit (decimal 0.02 = 2%). Intensive (DEV-I21). |
| `FinancingToDate` | `value` | scalar | cumulative repo interest on the position's funding leg from the settlement of its trade date to the settlement of the pricing date (or maturity, if earlier), holder-signed: a long pays (<= 0), a short lends the cash and receives (>= 0). Principal (1 - RepoHaircut) x Price on the trade date (pinned by resolve); simple interest at each calendar day's RepoRate (the last business day's fixing over weekends and holidays); 0 on the trade date. The engine books its change over each step as cash (DEV-E22). ccy (DEV-I21). |
| `Carry` | `value` | scalar | clean value now minus clean forward value at H: (Price - AccruedInterest) - (ForwardPrice - accrued at H) = coupon income over (s, H] minus financing at RepoRate; ccy, holder-signed; dead: 0 (DEV-I21). |
| `RollDown` | `value` | scalar | clean value at H on the library's reference curve rolled down (unchanged in time to maturity, spread held) minus clean value now; on a flat curve, the pull to par at constant yield. Carry + RollDown is the financed P&L to H if the curve does not move. ccy, holder-signed; dead: 0 (DEV-I21). |

Required frame columns: `Cashflows`: `payment_date`, `payment_amount`, `currency`, `payment_type` (scale columns must include `payment_amount`); `CRIFIRCurve`: `RiskType`, `Qualifier`, `Bucket`, `Label1`, `Label2`, `Amount`, `AmountCurrency` (scale columns must include `Amount`).
<!-- END generated contract tables -->

### Units and signs

Every value is for **one unit trade** and **holder-signed** (pricebt applies `quantity_`, §5.4 of
DESIGN). Rate sensitivities are per **+1bp**; rates and normal vols in the shipped and toy configs
are in **bp**. Every asset that can sit in one book must declare the **same unit** for `IRFwdRate`
and for each vol level: the `ir_pnl_definition` family checks each level's unit (DEV-E21) and
raises on a mismatch rather than mis-scaling the P&L by 10⁴.

| Position | `IRDelta` scalar | `IRGammaParallel` | `IRVega` | `Annuity` |
|---|---|---|---|---|
| pay-fixed swap | > 0 | < 0 (own-rate convexity of the annuity) | 0.0 | > 0 |
| receive-fixed swap | < 0 | > 0 | 0.0 | < 0 |
| bought payer swaption | > 0 | > 0 | > 0 | > 0 |
| bought receiver swaption | < 0 | > 0 | > 0 | > 0 |
| sold swaption | negated | negated | negated | < 0 |
| long bond | < 0 (per +1bp of yield) | > 0 | 0.0 | > 0 |

`Theta` is **ccy per calendar day** (DEV-I15); `ExpiryInYears` is `max(final_or_expiry − t,
0).days / 365` for every class (DEV-I17). A swap or bond maps its vol measures to `0.0` (R2-8)
rather than declaring them: a declaration breaks a mixed book whose P&L definition reads vega
(and on a swap or swaption a declaration does not load at all).

### `unsupported_measures:` (Bond)

Only a `Bond` (and a class without a contract) may declare a contract measure; an IRSwap or
IRSwaption config that declares one does not load, so `UnsupportedMeasureError` never arises for
them.

```yaml
unsupported_measures:                                  # top level
  IRVanna: "no vol-of-vol model in <library>"          # the whole measure (every form)
  IRDelta: {bucketed: "no curve ladder in <library>"}  # one form only: scalar | bucketed | frame
```

- Requesting a declared form raises `UnsupportedMeasureError` (a `ConfigError` and a
  `NotSupportedError`) naming the measure, the form and your reason. Its message keeps the phrase
  "no mapping for risk measure X".
- A mapping always wins: a measure both mapped and declared loads, uses the mapping, and warns
  that the declaration is stale (R2-9).
- Declare the **base** measure, never a preset (`IRDelta`, not `IRDeltaParallel`). A declared
  preset, a form outside the contract row, or a name that is neither in the contract nor in
  `pricebt.risk` loads with a `UserWarning`.

### Frames (`returns: frame`)

A `functions:` entry may return a table: a DataFrame, or a list of row dicts (`[]` is an empty
table with the required columns).

```yaml
functions:
  cashflows: {expr: 'lib.cashflows(market, trade)', unit: ccy, returns: frame, scale_columns: [payment_amount]}
risk_measures:
  Cashflows: cashflows
```

- `scale_columns` lists the columns quantity scales (the amount columns: `payment_amount`, and
  `notional` if you return it); a level (`rate`, `discount_factor`) or a date never goes there.
- A frame is never FX-converted (a currency conversion raises `ConfigError`).
- `Cashflows` lists the flows **still included in `Price` that `Price` will drop on their payment
  date** (`payment_date > pricing date`), holder-signed. A total-return `Price` that never drops
  paid flows (the toy swap) returns an **empty** frame (R2-6); this keeps `Theta`'s cash term and
  `pnl_explain_table`'s `cashflow_pnl` consistent under both conventions.
- Table measures stay in `backtest.results`; `result_summary`, `risk_summary`, `summary_stats`,
  `strategy_as_time_series` and a `to_frame(values='value')` pivot leave them out (DEV-R11).

### Per-row buckets

A `returns: buckets` portfolio function returns either `{mkt_point: value}` or a **list of row
dicts** with keys among `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value`
(`value` required; the function's `labels` fill the missing coordinates). Use rows for a ladder on
several curves and for `PnlExplain` (one row per risk factor, `mkt_type` = `IR`, `IR VOL`,
`CROSSES`, ...). Quantity and FX scale `value` only. The vega cube's `mkt_point` is
`'<tail>;<expiry>'` in gs order (e.g. `'10Y;1Y'`), labelled `mkt_type: IR VOL`.

### Measure parameters

`IRDelta(bump_size=5)`, `finite_difference_method`, `local_curve` and `scale_factor` reach a
function as `pricebt_bump_size`, `pricebt_finite_difference_method`, `pricebt_local_curve` and
`pricebt_scale_factor` (`None` when unset), and are part of every cache key (DEV-I10). A function
supports a parameter only if its expression names that variable (anywhere, a lambda or generator
included); otherwise the request raises `NotSupportedError`.
`mkt_marking_options` always raises. Nothing is silently ignored.

### Relative measures (`PnlExplain`)

`PnlExplain(CloseMarket(date=T))` maps to a `returns: buckets` portfolio function that also
receives `market_to` (the market of `T`, same csa) and `pricebt_to_date`, and must name one of
them (DEV-M2). It is in the IRSwap and IRSwaption contracts as a **bucketed** form (a
`returns: buckets` portfolio function always fills the bucketed slot; a `PnlExplainClose` key
counts too) and optional for a Bond. Under
`PricingContext(market=CloseMarket(date=t))` every function sees the market of `t` while
`pricebt_date` stays the pricing date (DEV-M1); value on `pricebt_date` if you want the time value
to stay that of the pricing date.

### Dead instruments (matured, expired, fully paid)

- Sensitivities are `0.0`; `Cashflows` is empty; `ExpiryInYears` is `0.0`.
- Levels (`IRFwdRate`, `IRSpotRate`, the vol levels) stay **finite and continuous** with their last
  live value on every held date, including the exit date (R2-7): the last par rate, or the strike.
- A swaption at or after expiry is physically settled: a leg in the money at expiry has the
  underlying swap's measures, an unexercised one is 0.
- Never `NaN`: `pnl_explain` skips a risk that is exactly 0 and has no NaN guard, so one NaN
  poisons every later cumulative value.

### Traps (each passes a casual check and breaks attribution)

- **Half gamma.** `IRGammaParallel` is the chain-rule second derivative of `Price` in the own
  rate r on ±h curve bumps: `Γ = [n₊ + n₋ − 2n₀ − ((n₊ − n₋)/(r₊ − r₋))·(r₊ + r₋ − 2r₀)] / ((r₊ −
  r₋)/2)²`, per bp². `d(pv01)/dr` of an annuity pv01 is half that at the money.
- **Per-year theta.** `Theta` is per calendar day. A library theta per year divided by 365 is
  `Theta`; a per-year `IRTheta` is a different, custom measure (`IRTheta = 365 × Theta`). Never
  map `Theta` to a per-year function.
- **Rolled curve.** `Theta` holds the own rate and vol fixed: reprice on the curve **translated**
  one day, `DF'(x) = DF(x)/DF(t+1d)`, never on a curve rolled forward in tenor space (roll-down is
  real `Δr` and belongs to delta).
- **Fixed-annuity delta.** The `IRDelta` scalar is the **total** derivative of `Price` in the own
  rate, `[PV(+h) − PV(−h)] / [r(+h) − r(−h)]`. A fixed-annuity pv01 matches it only at the money;
  off-market it misses `N·(F − K)·ΔA` (DEV-I12). The shipped toy and ARBS swap configs map the
  annuity pv01 and are at-the-money-exact only.

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
   resolved notional (`toylib.swaption.resolve_swaption`); it also rejects a `pay_or_receive` it
   cannot price at resolve time, not later. The toy prices `Pay`, `Receive` and `Straddle` (payer
   + receiver), so only an unknown value is rejected.
4. **Functions and the measure contract.** An `IRSwaption` config must map every measure of its
   contract (DEV-I11, [`IR_RISK_DESIGN.md`](IR_RISK_DESIGN.md) §2.2,
   [`IR_STRICT_CONTRACT.md`](IR_STRICT_CONTRACT.md)); it cannot declare any. The toy maps all of
   them (the R3-1 rows included): `npv` (Bachelier/normal price), own-rate `delta`/`gamma`, `vega`/`vanna`/
   `volga` (per bp of normal vol), `theta_1d` (ccy per day), the rate and vol levels in bp,
   `expiry_in_years`, `annuity`, `prob_exercise`, a `cashflows` frame (`returns: frame`), and
   three `returns: buckets` portfolio functions: the delta and diagonal gamma ladders and a vega
   cube keyed `'<tail>;<expiry>'` (e.g. `'10Y;1Y'`). `attributes: {expiration_date:
   'resolved["expiration_date"]'}` lets `AddTradeAction(swaption, 'expiration_date')` read its
   exit date.

Nothing in the engine, the pricing layer or the result objects changed to make this work — the
asset-agnostic guard (`tests/guards/test_asset_agnostic_scan.py`) fails the build if a later change
introduces a swap- or swaption-specific token into those layers.

## P&L explain functions

Four optional `functions:` entries let an asset config support `pnl_explain()`/`swap_pnl_definition()`
(`docs/v2/PNL_EXPLAIN_PLAN.md` §2 has the full derivation; this section is the config-author summary,
for any pricing library, not just ARBS). None is required — an asset with only `npv`/`dv01`/`par_rate`
still backtests fine, it just can't be given a gamma/carry attribution.

| Function | Unit | Meaning |
|---|---|---|
| `gamma` | `ccy_per_bp2` | `∂²npv/∂par²`, per bp² of this trade's own par rate |
| `theta` | `ccy` (per **year** — the value already bakes in the "per year", the unit tag is just currency) | `∂npv/∂t` at constant forward rates |
| `year_fraction` | `decimal` | an intensive time coordinate for the pricing date |
| `cash_paid_to_date` | `ccy` | cumulative holder-signed cash the trade has paid out so far |

The conventions behind them, each because the obvious alternative is wrong:

- **`gamma` is the second derivative of `npv`, never `dv01` differenced.** With `npv = dv01·(par−K)`,
  bumping the curve and differencing `dv01` gives *half* the true gamma at the money — the annuity's
  own convexity is missing. Compute it directly: shift the curve ±1bp, price `npv` at up/down/mid and
  `par` at up/down on the *same* trade, and divide by the **measured** `((par_up−par_down)/2)²`, not
  by `1bp²`. A payer's gamma is negative (short convexity); a receiver's is the negative of the payer's.
  `IRGammaParallel` must follow the chain rule of the "Half gamma" trap (Traps, below "Dead
  instruments"): this formula leaves out the par rate's own convexity and is about 10% low at 10y ATM
  until the chain-rule term lands.
- **`theta` is at constant forwards, never a curve roll.** Measure it on a *translated* curve
  (`DF'(x) = DF(x)/DF(t+1day)`, forward rates held fixed), not a *rolled* one (static shape in tenor
  space). Under a translation the trade's par rate barely moves, so delta and carry don't double-count
  the same P&L; under a roll, real roll-down leaks into both theta and delta. One calendar day forward,
  annualised: `theta = (npv(translated by 1 day) − npv(today)) × 365`.
- **`year_fraction` must be `unit: decimal` (intensive), never `unit: number` (extensive).** pricebt
  scales an extensive unit by `quantity_`; a time coordinate must not grow with position size, or
  `Δyear_fraction` — and therefore carry — comes out wrong for any scaled trade.
- **`cash_paid_to_date` exists because coupons paid between marks are not booked as cash** (the same gs
  behaviour `pricebt-architecture`'s engine-semantics table calls out): a swap's PV simply drops on its
  payment date, so without this function every coupon date leaves a residual equal to minus the coupon.
  Report the cumulative net cash the trade has paid the holder, from its effective date up to and
  including the market's reference date, holder-signed. A library whose PV convention never drops paid
  coupons (for example a toy world with no notion of settled cash) correctly reports `0.0` always —
  that is not a stub, it is the right answer for that convention.
- **None of the four may return `NaN` for a held trade.** `pnl_explain()` accumulates with no NaN
  guard, so one `NaN` poisons every later cumulative value. A trade that has matured between two marks
  must report `dv01 = gamma = theta = 0.0`; a level such as `par_rate` stays finite and continuous
  with its last live value (R2-7; see Dead instruments): fix a `NaN` at the source.

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
