# pricebt error catalogue (asset configs, sessions, pricing)

The message fragments below are copied from `src/pricebt/assets/*.py`, `src/pricebt/risk/contracts.py`,
`src/pricebt/errors.py`, `src/pricebt/markets/*.py` and `src/pricebt/risk/__init__.py`. Every
`ConfigError` carries `asset` and `key` attributes and, where possible, a did-you-mean suggestion.
`tests/skills/test_skill_asset_config_cookbook.py` raises the error behind each literal fragment and
checks the text still matches.

## At load (`load_asset`, `PricebtSession.use`)

| Message contains | Cause | Fix |
|---|---|---|
| `unknown key 'X'` | a typo, or a key from another schema (e.g. `pricing:` instead of `functions:`) | use the suggested key; the schema is in `docs/v2/ASSET_CONFIG_GUIDE.md` |
| `syntax error at line N` | an expression is not valid Python | fix the quoted snippet; YAML quoting often eats `\` or `'`, so prefer single-quoted YAML strings |
| `unit 'X' is not one of [...]` | a unit outside the closed set | `ccy`, `ccy_per_bp`, `ccy_per_bp2`, `bp`, `pct`, `decimal`, `number`, `date` |
| `must include 'Price'` | `risk_measures` lacks `Price` | map `Price: npv` (the engine books cash with it) |
| `references unknown function 'f'` | a `risk_measures` entry names a function that does not exist | declare it under `functions:` or `portfolio_functions:` |
| `collide with the reserved keys` | a function or attribute is named `market`, `trade`, `kwargs`, … | rename it |
| `declared in both functions and portfolio_functions` | the same name in both | keep one |
| `size_attribute 'x' is not a key of attributes` | `size_attribute` does not name an attribute | add the attribute or fix the name |
| `must be 1, got` (schema_version) | wrong or missing version | `schema_version: 1` |
| `duplicate mapping key` | a repeated YAML key (the loader is strict) | delete the duplicate |
| `duplicate asset name` | two configs with the same `asset:` id | make the ids unique |
| `market key K: ... differs between assets` | two assets share `market.key` but their imports, code or market expression differ | give each asset its own key (a swaption never shares a swap's key) |
| `is not a class exported by pricebt.instrument` | `instrument:` is misspelt, or names a class pricebt does not generate | use the suggested class (`IRSwap`, `IRSwaption`, `Bond`, ...) or `ConfigInstrument` |

### The measure contract (IRSwap, IRSwaption, Bond; DEV-I11, amended by `docs/v2/IR_STRICT_CONTRACT.md`)

`IRSwap` and `IRSwaption` are the **strict** classes: only a mapping satisfies a contract row, and
declaring a contract measure is itself an error. `Bond` keeps map-or-declare.

| Message contains | Cause | Fix |
|---|---|---|
| `measure-contract problem(s) for instrument` | one `ConfigError` listing every contract problem of the config | fix each listed line; the message ends with a paste-ready mapping skeleton (IRSwap, IRSwaption) or declaration block (Bond) for the missing measures |
| `not mapped (IRSwap/IRSwaption require a mapping for every contract measure)` | a strict-class contract measure (or one form) has no mapping | map it (cookbook patterns 14-29, or the connect skill's templates); there is no declaration route. A literal constant is honest only for `contracts.ZERO_BY_CONVENTION` (the checker FAILs `ir_fake_constant` otherwise) |
| `IRSwap/IRSwaption configs must map every contract measure; unsupported_measures cannot satisfy them` | an IRSwap/IRSwaption config declares a contract measure (mapped or not) under `unsupported_measures:` | map it and delete the declaration |
| `(a preset or fallback of IRDelta)` | the same, for a declared preset of a contract measure (`IRDeltaParallel`, `IRGammaParallelLocalCcy`, `PnlExplainClose`, ...) | map the base measure, delete the declaration |
| `Map each missing measure (merge into functions:, portfolio_functions: and risk_measures:` | the header of the strict classes' paste-ready **mapping skeleton** (`contracts.mapping_skeleton`): a stub per missing form with an allowed unit | merge each section into the config's own one, write every `expr`, and pick the unit you return |
| `neither mapped nor declared under unsupported_measures` | a Bond contract measure (or one form: `scalar`, `bucketed`, `frame`) has no mapping and no declaration | map it, or paste its line from the block |
| `Map each missing measure, or declare what the library cannot compute` | the header of a Bond's paste-ready `unsupported_measures:` block | paste the block and replace every `"TODO: ..."` with a specific, true reason |
| `has unit 'X'; allowed [...]` | a mapped function's unit is outside the measure kind's units (e.g. a vega in `bp`) | convert to the allowed unit: `ccy_per_bp` for first-order, `ccy_per_bp2` for second-order, `ccy` for Price/Theta/Annuity, `bp`/`pct`/`decimal` for levels |
| `level must be intensive (set scale_with_quantity: false)` | a rate, vol, time or probability function scales with quantity (e.g. `unit: number` for `ExpiryInYears`) | use `bp`/`pct`/`decimal` (intensive by default), or set `scale_with_quantity: false` |
| `this measure is a table: map a functions: entry with` | `Cashflows` is mapped to a scalar function | a `functions:` entry with `returns: frame` (pattern 23) |
| `returns a frame; this measure needs a number` | a scalar measure is mapped to a `returns: frame` function | map a scalar function |
| `must include ['payment_amount']` | the `Cashflows` function's `scale_columns` leave out `payment_amount` | `scale_columns: [payment_amount]` (plus any other amount columns) |
| `its amounts must scale with the position` | the `Cashflows` function has `scale_with_quantity: false` | remove it: cash amounts scale with the position |
| `syntax error at line 1: invalid syntax (in '... TODO')` | a mapping-skeleton stub pasted unchanged: its expression (`contracts.SKELETON_EXPR`) is a syntax error on purpose, so a skeleton never loads unfinished | write the computation in place of `... TODO` |
| `must be a non-empty reason string (why the library cannot compute it)` | an `unsupported_measures:` value is empty | give a specific, true reason |
| `must give a reason, or at least one of` | an `unsupported_measures:` value is an empty mapping | a reason string (the whole measure), or `{scalar: ..., bucketed: ...}` |

Warnings (`UserWarning`; the config still loads, and `python -W error` turns them into failures). On
an IRSwap/IRSwaption config the first two are load errors instead (a declared contract measure or
preset, above); the last two (names outside the contract) stay warnings there too:

| Message contains | Cause | Fix |
|---|---|---|
| `the mapping is used -- remove or narrow the stale declaration` | a measure (form) is both mapped and declared; the mapping wins (R2-9) | delete the declaration, or narrow it to the form that is really missing |
| `is a preset or fallback of IRDelta; declare IRDelta instead` | a preset (`IRDeltaParallel`, `IRVegaParallel`, ...) is declared; it declares nothing the contract counts | declare the base measure |
| `contract row has only` | a declaration names a form the contract row does not have (e.g. `IRVanna: {bucketed: ...}`) | remove the extra form |
| `nor a pricebt.risk measure` | a declared name is neither in the contract nor a gs measure: probably misspelt | use the did-you-mean name |

### Schema of `functions:` / `portfolio_functions:`

| Message contains | Cause | Fix |
|---|---|---|
| `is allowed only with returns: frame` | `scale_columns:` on a scalar function | remove it, or add `returns: frame` |
| `must list the columns that scale with quantity` | an extensive `returns: frame` function without `scale_columns` | list the amount columns (`[payment_amount]`) |
| `returns 'frame' is not one of ['buckets', 'scalar']` | `returns: frame` on a portfolio function | frames are per-trade `functions:` only (R2-15) |

## At first evaluation (`AssetEvaluationError`)

`asset 'A' key 'K' expr '...' date=D csa=C: <OriginalError>: <message>`. The key tells you where to look:

| key | Typical cause |
|---|---|
| `imports` | the library is not installed or not on `sys.path`; a missing environment variable. The error is cached, so fix it and restart the process. |
| `code` | an exception in module-level code (client connection, file not found) |
| `market` | the market loader raised instead of returning `None` (holiday, no data) |
| `resolve` | kwargs the parser does not accept (`'100k'`, `'=solvefor(...)'`, `'25d'`, a `Straddle` your library cannot price), a missing default |
| `trade` | the resolved terms don't match the library's builder |
| `functions.<name>` / `portfolio_functions.<name>` | a wrong method name or argument order, or a measure the library does not support |
| `attributes.<name>` | the attribute reads a key that `resolve` does not produce |

| Message contains | Cause | Fix |
|---|---|---|
| `NotImplementedError: TODO (asset-config template)` | a primitive of a connect-skill template is still a stub | implement that `lib_*` primitive with your library's call |

## At pricing

| Message contains | Cause | Fix |
|---|---|---|
| `no asset matches IRSwap({...})` | no registered asset's `match:` fits these kwargs | register the right config, fix `match:`, or pass `pricebt_asset=` |
| `ambiguous: IRSwap matches [...]` | two configs match | tighten `match:` or pass `pricebt_asset=` |
| `unknown asset 'x'; registered: [...]` | `pricebt_asset=` names an unregistered config | add it to `PricebtSession.use(assets=[...])` |
| `has no mapping for risk measure X` | the strategy requests a measure the config does not map (on a class without a contract, or a measure outside it) | add `X:` under `risk_measures` (presets such as `IRDeltaParallel` fall back to `IRDelta`) |
| `is declared unsupported (every form)` (or `(scalar)`, `(bucketed)`, `(frame)`) | `UnsupportedMeasureError` (a `ConfigError` and a `NotSupportedError`): the config (a Bond, or a class without a contract: an IRSwap/IRSwaption cannot declare) declares the measure, and the message quotes its reason | map it once your library can compute it; otherwise the strategy must not ask for it (e.g. use `ir_pnl_definition(vega=False, ...)`) |
| `has no bucketed mapping; request` | a bare finite-difference measure (`IRVanna`, `IRDelta`, ...) asks for the bucketed form, and only the scalar is mapped | request `X(aggregation_level='Type')` for the scalar, or map a ladder |
| `has no scalar mapping` | the measure has no scalar function to answer a scalar request | map a scalar function |
| `does not reference pricebt_bump_size` | `bump_size` (or `finite_difference_method`, `scale_factor`, `local_curve`) was requested, and the chosen function's expression does not name `pricebt_<parameter>` (DEV-I10) | name the variable in the expression and honour it (pattern 24), or drop the parameter from the request |
| `it is honoured GS server-side and pricebt cannot pass it to an asset config` | `mkt_marking_options` was set (DEV-I8) | drop it |
| `has unit bp; it cannot be converted to USD` | a currency conversion was requested for a rate measure | request it without `currency=`, or give the function a currency unit |
| `returns a frame (a table); it cannot be converted to` | a table measure (`Cashflows`) under `result_ccy` or a currency parameter | ask for it in its own currency |
| `frame is missing required column(s)` | a `returns: frame` function's rows lack `payment_date`, `payment_amount`, `currency` or `payment_type` | return every required column (`pd.DataFrame(columns=[...])` when there are no rows) |
| `are not columns of the frame` | the rows lack a `scale_columns` column | add the column, or fix `scale_columns` |
| `a frame result must be a DataFrame or a list of dicts` | a `returns: frame` function returned a number or another type | return a DataFrame or a list of row dicts (`[]` is fine) |
| `no FX config: cannot convert` | a mixed-currency book or `result_ccy` without an FX config | `PricebtSession.use(..., fx=...)` |
| `no FX rate` (`MarketDataUnavailable`) | the FX config returned `None` or a value ≤ 0 | fix the FX data for that date |
| `MarketDataUnavailable(asset, date)` | a market was required on a date with `None` | check the envelope and holidays; the engine's `missing_market='drop'` covers grid dates |
| `is not hashable plain data` / `is not plain data` | `resolve` returned a library object, a list or a dict | return plain values (str/int/float/bool/None/date/tuple) |
| gs `Cannot aggregate cash in multiple currencies` | a mixed-currency book without `result_ccy` | `run_backtest(result_ccy="USD")` plus an FX config |
| gs `cannot hedge in a different currency` | the hedge risk measure has no `currency` for a cross-currency hedge | `IRDelta(aggregation_level='Type', currency='USD')` |

## Contexts, PnlExplain, portfolios and P&L

| Message contains | Cause | Fix |
|---|---|---|
| `is not supported: pass a CloseMarket(date=...)` | `PricingContext(market=...)` with anything but a `CloseMarket` (DEV-M1) | `PricingContext(pricing_date=d, market=CloseMarket(date=t))`, or no `market` |
| `references neither market_to nor pricebt_to_date` | `PnlExplain` is mapped to a function that does not read the target market (DEV-M2) | a buckets portfolio function whose expression names `market_to` (pattern 22) |
| `explains to the pricing date's own close` | `PnlExplainClose()` (or `PnlExplain(CloseMarket())`) priced with no `CloseMarket` override: from and to are the same market | price under `PricingContext(market=CloseMarket(date=...))` of another date, or pass `PnlExplain(CloseMarket(date=...))` |
| `explains to the live market, which is GS server-side` | `PnlExplainLive()` (or `PnlPredictLive`) | `PnlExplain(CloseMarket(date=...))` |
| `is GS server-side (portfolio persistence)` | a server-only `Portfolio` method (`save`, `get`, `from_portfolio_id`, `from_book`, ...) | build the `Portfolio` in memory (`Portfolio([...])`, `from_frame`, `from_csv`) |
| `the definition expects` | `pnl_explain` with an `ir_pnl_definition(rate_unit=..., vol_unit=...)` whose units differ from a held asset's `IRFwdRate` / vol unit (DEV-E21) | pass the units your configs declare (`'bp'`, `'pct'`, `'decimal'`), and give every asset in the book the same units |
| `every held asset must map every measure the definition reads` | `attribution.definition_for` (`skills/pricebt-pnl-attribution/scripts/attribution.py`): an asset does not map a measure the chosen definition prices -- a Bond that declares it (`a Bond's declaration does not count`), or a class without a contract. An IRSwap/IRSwaption config with a gap does not load at all | map it (the `'0.0'` zeros for a bond's vol measures), drop the attribute (`kind='bond'`, `vanna=False`, ...), or pass `assets=[...]` to leave out configs the book never trades |
| `Cannot aggregate results with different units on` | `BackTest.pnl_explain_table()` on a step whose held book mixes `Price` currencies, or pays `Cashflows` in another currency (the `result_summary` rule; `Portfolio(...).calc(...).aggregate()` says `... different units for`) | attribute each currency's book in its own run |
| `PnlAttribute names must be unique` | two attributes share an `attribute_name`, or one is named like a fixed column (`actual_pnl`, `cashflow_pnl`, `economic_pnl`, `explained_pnl`, `residual_pnl`) | rename the attribute |
| `rate_unit must be one of` | `ir_pnl_definition(rate_unit=...)` (or `vol_unit=`) given anything but `'bp'`, `'pct'` or `'decimal'` | pass the unit your configs declare; `attribution.definition_for` reads it for you |
| `second_order cannot be combined with cross_market_data_metric` | a `PnlAttribute` with `second_order=True` and a cross level | a cross term is `k·R·Δm₁·Δm₂`, first order in each: drop `second_order` |

## Checker rows of the strict contract (`check_asset.py`, IRSwap / IRSwaption)

The loader checks units and shapes; these rows of
[`check_asset_ir.py`](../../pricebt-verify-asset-config/scripts/check_asset_ir.py) check the values
(each one has a broken fixture in `tests/skills/fixtures/check_asset/`).

| Row and status | Typical cause | Fix |
|---|---|---|
| `ir_fake_constant` FAIL | a contract measure mapped to a literal (`'0.0'`, `'{}'`, `float('nan')`, `math.nan`) outside `contracts.ZERO_BY_CONVENTION`, e.g. `IRDiscountDeltaParallel: zero_per_bp` "because there is one curve"; or a zero-by-convention measure mapped to a non-zero constant (`IRVega: '5.0'`) | compute it (pattern 14); only vol measures on a swap, `IRBasis` on one curve and `IRXccyDelta` in one currency may be 0 |
| `ir_premium_cents` FAIL | `PremiumCents` in percent of notional under `bp`, divided by the signed notional, or `abs(Price)` (unsigned: the row checks both directions) | `Price / abs(notional) * 1e4`, unit `bp` (pattern 29) |
| `ir_local_annuity` FAIL | `LocalAnnuityInCents` from the pv01 (Annuity x 1e-4), the signed notional, or `abs(Annuity)` | `Annuity / abs(notional)`, unit `decimal` |
| `ir_forward_price` FAIL | `ForwardPrice` = Price x DF instead of Price / DF (caught at any rate level by the sign of the implied rate), or the wrong date | `Price / DF(expiry)`: a swaption's expiration date, a swap's termination date; Price from then on |
| `ir_fair_premium` FAIL | `FairPremium` discounted to the expiry or final date (that is `ForwardPrice`), or Price x DF(spot) instead of Price / DF(spot) | `Price / DF(premium settlement)`: spot, or the premium payment date |
| `ir_par_spread` FAIL | forward - K instead of K - forward, a holder-signed spread (flips with direction), or a unit slip | K - `IRFwdRate` in bp when the legs share a curve and schedule, the same for both directions |
| `ir_compounded_fixed_rate` FAIL | a de-compounded or continuous restatement (below K), a market level that moves daily, or K as-is for a semiannual/quarterly fixed leg (when the leg frequency is knowable) | `(1 + K/f)^f - 1` from the resolved fixed rate or strike; K itself for an annual leg |
| `ir_crif` FAIL | `Label1` `'10Y'` (SIMM tenors are lower case), `Label2` `'SOFR'` (the SIMM sub-curve is `'OIS'`), an int `Bucket` (`'1'`), an `AmountCurrency` other than the `Qualifier`, a wrong `RiskType` or `Qualifier`, or sum(`Amount`) != the `IRDelta` ladder (a sign or unit) | build the rows from the trade's own `IRDelta` ladder (pattern 29) |
