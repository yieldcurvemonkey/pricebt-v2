# Discovery questionnaire: an unknown pricing library

Answer every question **with evidence** before you write the config: a code path, a docstring, or
a REPL transcript. A guess becomes a silent P&L bug. Where to look: the library's docstrings and
`help()`, its own unit tests (they show real call sequences and expected units), example
notebooks, internal wiki pages, and `dir()` on the objects the library returns. The last column
says where the answer goes in the config.

## 1. Session and auth

| Question | How to find out | Goes into |
|---|---|---|
| How do I open a session (client, context, login)? Is it expensive? | Look for `connect`, `login`, `Session`, `Context` or `init` in the package's top level and its quick-start. Time it. | `code:`, module-level, **once** |
| Does it need credentials, a VPN, an env var, or a licence file? | Read the quick-start and the exceptions it raises when unset. | `imports:` (set env vars *before* importing, like the ARBS config) |
| Does it have side effects (writes a cache, opens Excel, hits production)? | Read the source of the calls you will make. Run under a network monitor or with the network off. | A safety comment, plus a guard in `code:` |
| Is it thread-safe or process-safe? | Docs. Assume **no**. | One client per process |

## 2. Market handle by date

| Question | How to find out | Goes into |
|---|---|---|
| Which call gives a curve or market for a historical as-of date? | Look for `market`, `curve`, `snapshot`, `env`, `scenario` or `as_of`. | `load_market(d)` |
| What date type does it take: `date`, `datetime`, ISO string, Excel serial, tz-aware timestamp? | Signature and docstring. Pass each type and see which one raises. | the conversion in `load_market` |
| Is "as of" an end-of-day close, and in **which timezone** (NY 17:00, London 16:00, Tokyo)? | Docs, or compare with a known published close. | `PricebtSession.use(..., tz=, eod_time=)`, or use `pricebt_timestamp` |
| Which curve or market id do I pass for my trade (e.g. `USD.SOFR`, `USD-SOFR-1D`)? | List the available ids. | a `_CURVE` constant |
| Is the returned object live (re-fetches) or a frozen snapshot? | Price twice, minutes apart. | Must be frozen for a backtest |

## 3. Holidays, weekends, missing data

| Question | How to find out | Goes into |
|---|---|---|
| What happens on a weekend? A holiday? A data gap? Before the history starts? | Call it on a known Saturday, 25 Dec, and a date 20 years ago. | `load_market` returns **`None`** for every "no data" case. It catches the exception type, and catches it narrowly |
| Does it silently return the **previous** day's curve instead (stale roll-back)? | Compare the returned object's own reference date with the date you asked for. | Validate `ref_date == d`, else raise or return `None` |
| Which holiday calendar does it use for spot, schedules and rolls? | Docs, or `spot_date` around a holiday. | Nothing: the library owns it. Just never compute dates yourself |

## 4. Trade construction

| Question | How to find out | Goes into |
|---|---|---|
| How do I build the trade: a constructor, a dict, a builder? | Examples, tests. | `build_swap(resolved)` |
| Side: signed notional, a direction flag (`PAY`/`RECEIVE`, `BUY`/`SELL`), or a sign on the rate? Pay/receive **of which leg**? | Price a payer and a receiver. Their PVs must be exact negatives. | `build_*` maps pricebt's signed notional (payer > 0) |
| Fixed rate: decimal, percent or bp? Can it be "at par" (`None`/`0`/`"ATM"`)? Does par re-strike on each market? | Build with 3.0 and with 0.03, and compare the PV to the par rate. | `resolve` pins a **decimal** strike. `build` converts to the vendor unit |
| Dates: can `end` be a tenor? Is a tenor counted from start, from spot, or from the pricing date? | Build a `"10Y"` trade and price it on two dates. Does the maturity move? | `resolve` pins **absolute** dates using the library's own date helpers |
| Is the trade object market-independent (safe to reuse on any date)? | Does building it need a market? Does it hold fixings? | `trade.build_on: resolve_date` (independent) or `each_market` |

## 5. Measures: codes, units, signs

For **each** measure code your library returns (PV, DV01, par rate, vega, ...). Which measures pricebt
needs, and in which unit and sign, is the contract: go through [section 9](#9-contract-driven-capability-discovery)
for that, and use this table for the mechanics of each code:

| Question | How to find out | Goes into |
|---|---|---|
| What is the measure code or method name? | Look for a measure enum, a `measures=` list, or the method names. | `functions:` |
| Currency: trade currency, a reporting currency, or a CSA currency? | Price a EUR trade: which currency comes back? | `currency:`, or a conversion |
| **Unit**: per 1bp, per 1% or per unit rate (1.0)? Per unit notional or total? | A 10y 1mm swap has dv01 of roughly 850–950 USD per bp. A number near 0.09 is per unit notional; one near 90,000 is per 1%. | the scale factor |
| **Sign**: PV change for rates **up** or **down**? Payer-positive or receiver-positive? | Bump the curve yourself: `PV(up) - PV(base)` for a payer must be > 0 in pricebt. | the sign flip |
| Rates: decimal, percent or bp? | An ATM 10y par rate of ~0.04, ~4 or ~400. | `* 1e4`, `* 100` or nothing, and `unit: bp` |
| Is `dv01` a zero-rate or a par-rate sensitivity? | Docs. Either is fine, but be consistent with the ladder. | a comment |

## 6. Bucketed risk

| Question | How to find out | Goes into |
|---|---|---|
| Which call gives bucketed delta? What are the key formats (`"USD.SOFR:2Y"`, `("USD", "2Y")`, a DataFrame index)? | Print one result. | `delta_ladder` maps the keys to plain tenors `"2Y"` |
| Same sign and unit as the scalar? Does the ladder sum to the scalar dv01? | Sum it. | Flip or scale it the same way as the scalar |
| Fixed pillars, or pillars that depend on the trade? | Price a 3y and a 30y swap. | fixed output keys, and **raise** on an unknown key |

## 7. Batching and performance

| Question | How to find out | Goes into |
|---|---|---|
| Can one call price many trades or many measures? | Look for list arguments. | `portfolio_functions:` send the whole book in ONE call; `_risk()` requests all measures at once |
| What does one call cost (latency, rate limits)? | Time 10 calls. | memoise on `market.__dict__`, keyed by `(pricebt_date, trade)` (never by date alone: `CloseMarket` hands one market to two dates) |
| Is building a market the expensive step? | Time it. | pricebt already caches one market per (key, date, csa). Never rebuild inside functions |

## 8. Fixings and seasoned trades

| Question | How to find out | Goes into |
|---|---|---|
| Does a seasoned trade (start < as-of) need a fixings history? Where does it come from? | Price a trade that started 3 months ago. | the market object must carry fixings as of `d` (no look-ahead) |
| Does the trade object cache fixings from the market it was built on? | Build on d1, price on d2, then compare with a rebuild on d2. | `build_on: each_market`, or a `remark()` helper (see the ARBS config) |
| What happens after maturity (or expiry): 0, NaN, or an error? | Price a matured trade and an expired option. | guard in the functions (dead-instrument rule, R2-7): `npv` and every sensitivity `0.0`; every level (`IRFwdRate`, vols, `ExpiryInYears`) **finite**, e.g. its last live value. Never NaN: `pnl_explain` has no NaN guard, so one NaN poisons every later cumulative value |

## 9. Contract-driven capability discovery

An `IRSwap`, `IRSwaption` or `Bond` config must **map every row** of its class's measure contract
to a function with an allowed unit: declaring a contract measure under `unsupported_measures:` is a
load error (DEV-I11 amended, [`docs/v2/IR_STRICT_CONTRACT.md`](../../../docs/v2/IR_STRICT_CONTRACT.md),
[`docs/v2/BOND_DESIGN.md`](../../../docs/v2/BOND_DESIGN.md)). `unsupported_measures:` is legal only
for a class without a contract (`ConfigInstrument`). The contract is code, not prose. Print it for
your instrument before you start:

```powershell
python -c "from pricebt.risk import contracts; [print(f'{r.measure:24} {r.kind:7} {r.forms}  {r.doc}') for r in contracts.contract_for('IRSwaption')]"
```

For each row, answer four questions **about your library**, with evidence:

1. **Native?** Does a call return this measure directly? Which call, which measure code?
2. **Convention?** In what unit (per 1bp, per 1% or per unit rate; decimal, percent or bp; per day
   or per year)? With what sign (PV change for rates up or down; holder or counterparty view)?
   Against which bump (zero rates or par quotes; parallel or key rate; normal or lognormal vol)?
3. **If not native, which primitives derive it?** Each template
   (`references/config-template*.yaml`) derives every measure from about fifteen primitives: a PV,
   the own rate, a curve shift, a vol shift, a one-day translation, and so on. Name the library call
   behind each primitive.
4. **How will you verify it?** Use a known answer, or an independent bump in your own library (the
   last column below).

There is no "not supported" answer: every row is derived from primitives you can name. A fake
`0.0` or a NaN is never acceptable. The exceptions are the rows that are zero *by convention*
(`contracts.ZERO_BY_CONVENTION`, R2-8, marked "swaps and bonds 0.0" below): a swap's or a bullet
bond's vega really is 0. The checker's `ir_fake_constant` FAILs a literal constant on any other row.

The table below is written for swaps and swaptions. A `Bond` gives several rows its own text
(`Price`, `Theta`, `Cashflows`, `FairPremium`, `ForwardPrice`, ...) and adds the bond analytics and
the repo financing: use the 40-row worksheet in [section 14](#14-the-bond-contract-worksheet-40-rows).

| Measure (forms) | pricebt expects (unit, sign, convention) | Derive it from (template recipe) | Verify with |
|---|---|---|---|
| `Price` (s) | `ccy`, holder-signed PV of one unit of the resolved signed notional. It either drops flows paid on or before the pricing date, **or** never drops them (total return): say which | `lib_pv` (native in every library) | ATM swap PV about 0; payer = -receiver; a bought option > 0; a long bond > 0 |
| `IRDelta` (s) | `ccy_per_bp`: the **total** derivative of Price w.r.t. the own rate r (swap par rate, swaption forward, bond yield) along a parallel shift, `[PV(+h) - PV(-h)] / [r(+h) - r(-h)]` per bp of r (DEV-I12). Payer swap > 0, payer swaption > 0, long bond < 0 | `own_rate_delta` (swap, swaption) or `yield_delta` (bond), from `lib_shift`, `lib_pv` and `lib_own_rate`. A native par-quote DV01 is close. A fixed-annuity pv01 is exact **only at the money** | the recipe with a 0.5bp and a 2bp bump agrees to about 1e-4 |
| `IRDelta` (b) | `ccy_per_bp` at each curve pillar, keys plain tenors (`"2Y"`), same sign as the scalar. It sums to the **parallel** curve delta, which differs from the own-rate scalar by dr/ds (R2-2) | `key_rate_ladder`, from `lib_pillars` and `lib_shift_pillar` | the sum equals a whole-curve parallel bump `(PV(lib_shift(+h)) - PV(lib_shift(-h)))/2`, not `IRDiscountDeltaParallel` |
| `IRDiscountDeltaParallel` (s) | `ccy_per_bp` for +1bp on the discount curve only, projection forwards held | `discount_only_delta`, from `lib_shift_discount` (single curve: a market that discounts on the shifted curve and projects on the base one) | an at-the-money swap: ≈0; never the parallel DV01 |
| `IRGammaParallel` (s) | `ccy_per_bp2`: the chain-rule second derivative on the IRDelta bumps. **Never** d(pv01)/dr, which is half the gamma at the money | `own_rate_gamma` / `yield_gamma` | swap at the money: about 2 x the change in annuity pv01 per bp; a long option and a long bond > 0 |
| `IRGamma` (b) | `ccy_per_bp2` per pillar, diagonal only (DEV-I13) | `diagonal_gamma_ladder` | finite, one row per pillar |
| `IRVega` (s) | `ccy_per_bp` of **normal** implied vol (`IRAnnualImpliedVol`); swaps and bonds 0.0 | `vol_bump_vega`, from `lib_vol_shift` (a 1bp normal vol bump = 1e-4). A lognormal vega is not a rescale: convert with the cookbook's pattern 18 | ATM normal: about N x A x sqrt(T) x 0.3989 x 1e-4 |
| `IRVega` (b) | `ccy_per_bp` with `mkt_point = "<tail>;<expiry>"` (gs order: `"10Y;1Y"`); swaps and bonds `{}` | `vega_by_expiry_tail`, or a native bucketed vega with its keys mapped | the cube sums to the scalar |
| `IRVanna` (s) | `ccy_per_bp2`: d(IRDelta scalar)/d(normal vol), per bp x bp; swaps and bonds 0.0 | `vol_bump_vanna` | changes sign across the ATM strike |
| `IRVolga` (s) | `ccy_per_bp2`: the second derivative in normal vol; swaps and bonds 0.0 | `vol_bump_volga` | about 0 at the money for a normal-model option |
| `IRBasis`, `IRXccyDelta` (s) | `ccy_per_bp` for the basis or cross-currency spread | a shift of that spread; a single-curve, single-currency library: 0.0 | 0.0 unless the library has that spread |
| `IRFwdRate` (s) | the own rate, intensive (`bp`, `pct` or `decimal`; the shipped configs use `bp`). **Finite on every held date, including the exit date** | `lib_own_rate` / `lib_fwd_rate` / `lib_yield` | ATM: equals the resolved strike x 1e4 in bp |
| `IRSpotRate` (s) | par rate of the spot-starting swap with the same final date; a bond: its yield | `lib_spot_rate` | equals `IRFwdRate` for a spot-starting swap |
| `IRAnnualImpliedVol` (s) | **normal** vol at the strike, intensive; swaps and bonds 0.0; after expiry the last live value | `lib_normal_vol`; a lognormal or SABR library: `bachelier_implied_vol` of its own premium | reprices the option through Bachelier |
| `IRAnnualATMImpliedVol` (s) | normal vol at the money forward, same expiry and tail; swaps and bonds 0.0 | `lib_atm_normal_vol` | equals `IRAnnualImpliedVol` for an ATM strike |
| `IRDailyImpliedVol` (s) | `IRAnnualImpliedVol / sqrt(252)` | the expression itself | the ratio is exactly sqrt(252) |
| `Theta` (s) | `ccy` **per calendar day**, total return: `PV(t+1d) + flows paid in (t, t+1d] - PV(t)`, with the own rate and vol held fixed: the curve is **translated**, never rolled (DEV-I15). A per-year theta is 365 x this | `theta_one_day`, from `lib_translate` and `lib_cashflows`; a bond: the same yield over the step to the next business day (section 14) | in a frozen world the sum of daily Theta equals the PV drift |
| `ExpiryInYears` (s) | `max(final_or_expiry - t, 0).days / 365`, `decimal` (DEV-I17) | `years_to` over the resolved dates | needs no library call |
| `Annuity` (s) | `ccy`, PV of 1.0 per annum on the fixed schedule (1e4 x the fixed-leg pv01) times the **signed notional**: pay-fixed > 0, receive-fixed < 0, bought swaption > 0, long bond > 0. A QuantLib-style `fixedLegBPS` has the opposite sign for a payer | `lib_annuity` (`-1e4 x fixedLegBPS`); else `-[PV(K+1bp) - PV(K-1bp)] / 2e-4` | an ATM swap's `IRDelta` equals `Annuity x 1e-4`, same sign |
| `Cashflows` (frame) | the flows Price still includes and will drop, one row each; columns `payment_date, payment_amount, currency, payment_type`; `scale_columns: [payment_amount]`; empty for a total-return Price | `lib_cashflows` | the coupons equal what Price drops on each payment date |
| `ParSpread` (s, swap, swaption) | the floating-leg spread making Price 0, rate unit, intensive, the same for both directions; one curve and matching schedules: `K - IRFwdRate` | `par_spread_bp` | equal for payer and receiver; 0 at the money |
| `FairPremium` (s, swap, swaption) | `ccy`: `Price / DF(premium settlement)` (spot, or the swaption's premium payment date; no spot lag: Price) | `fair_premium`, from `lib_discount_factor` (+ `lib_premium_date`) | `FairPremium x DF(settlement) == Price` |
| `ForwardPrice` (s, swap, swaption) | `ccy`: `Price / DF(expiry)` to the date `ExpiryInYears` counts to; Price on or after it | `forward_value`, from `lib_discount_factor` | `ForwardPrice x DF(expiry) == Price` |
| `PremiumCents` (s, swap, swaption) | `Price / abs(notional_amount)` in the declared unit (`bp`: 1e4 x the ratio), intensive | the expression itself | the identity, exactly |
| `LocalAnnuityInCents` (s, swap, swaption) | `Annuity / abs(notional_amount)`, `decimal`, holder-signed like Annuity (10y payer about +8.5) | the expression itself | the identity, exactly |
| `CompoundedFixedRate` (s, swap, swaption) | the fixed rate (strike) as `(1 + K/f)^f - 1`, rate unit, intensive; an annual leg: K | `compounded_rate_bp`, from `lib_fixed_frequency` | within `[K, e^K - 1]`; constant over time |
| `CRIFIRCurve` (frame, swap, swaption) | SIMM CRIF rows `RiskType, Qualifier, Bucket, Label1, Label2, Amount, AmountCurrency`, `Label1` a lower-case SIMM tenor, `scale_columns: [Amount]`; empty when dead | `crif_frame`, from the trade's key-rate ladder | `sum(Amount)` == the `IRDelta` ladder's sum |
| `PnlExplain` (b, swap, swaption) | a buckets portfolio function reading `market_to`: rows by `mkt_type` (`IR`, `IR VOL`, `CROSSES`), both markets seen from the pricing date (no time passes) | `pnl_explain`, from `lib_at` (+ `lib_with_vols` for the vol row) | the rows sum to `Price` under `CloseMarket(date=...)` minus `Price` |
| `ProbabilityOfExercise` (s, swaption) | 0..1 under the annuity measure, `decimal` | `lib_prob_exercise`, or the strike bump of PV / annuity | payer + receiver = 1 |

Record the answers in a worksheet before writing YAML, one line per row above:

| Measure (form) | Native call or recipe | Library unit and sign | Conversion on the config line | Verified by | Status |
|---|---|---|---|---|---|
| e.g. `IRVega` (s) | `yourlib.risk(t, "VEGA_LN_1PCT")` | per 1% lognormal, holder view | pattern 18 | `vol_bump_vega` on a normal re-quote | mapped |
| e.g. `IRGamma` (b) | recipe: `diagonal_gamma_ladder` | n/a | none | finite per pillar, sum near the parallel gamma | mapped |
| e.g. `IRVega` (s), a Bond | literal `'0.0'` | n/a | none | `ZERO_BY_CONVENTION["Bond"]` lists it | zero by convention |

## 10. Finite-difference controls (measure parameters)

gs lets a caller pass `bump_size`, `finite_difference_method`, `scale_factor` and `local_curve` on
`IRDelta`, `IRVega`, `IRVanna`, `IRVolga`, `IRBasis` and `IRXccyDelta`. pricebt hands each one to
your function as `pricebt_<name>`, **but only if the function's expression names it**. Otherwise
the request raises `NotSupportedError` (DEV-I10). `mkt_marking_options` always raises.

| Question | How to find out | Goes into |
|---|---|---|
| Can your library bump by a given size? In which unit (bp or decimal)? | its risk API signature | the recipe's `bump_bp` argument. The templates read `bump_size` as bp; gs does not document the unit |
| One-sided (up, down) or centred? | docs, or compare with your own bumps | the recipe's `method` argument (`Up`, `Down`, `Centered`, `CenteredSecondOrder`) |
| Do `scale_factor` and `local_curve` mean anything in your library? | gs documents them only as "Scale factor" and "Change in Price (present value in the denominated currency)" | leave them out of the expression unless you can state what they do. They then raise, which is honest |

## 11. Swaptions

| Question | How to find out | Goes into |
|---|---|---|
| Vol model and quote: normal (bp), lognormal (%), shifted lognormal or SABR? | docs; price an ATM option and back out both vols | `lib_normal_vol`; a lognormal library needs the pattern-18 conversions |
| Surface keys: expiry x tail, with a strike or moneyness axis? | print the surface | `VOL_EXPIRIES`, `VOL_TAILS` and the `"<tail>;<expiry>"` cube keys |
| Is the surface point in time (as of the close)? From which source? | compare two dates | `lib_market(d)` returns `None` when the surface is missing (never a half market) |
| Can a vol bump move the **normal** vol by exactly 1bp? | bump, reprice, and back out the normal vol | `lib_vol_shift` |
| Does a curve bump hold the option's normal vol (sticky strike)? | bump the curve and back out the normal vol | `lib_shift` |
| Exercise and settlement: European? Physical or cash? What does an expired option price to? | price one day after expiry, in and out of the money | `lib_pv` after expiry: the underlying swap if exercised, else 0 |
| How do you handle `buy_sell`, `Straddle` and the strike grammar (`'ATM'`, `'ATMF'`, `'A-50'`, a decimal)? | the gs kwargs | `resolve_swaption` folds `buy_sell`; `_legs` prices or rejects `Straddle` |
| Is the premium in the PV? When is it paid? | docs | `lib_cashflows` lists a deferred premium; otherwise `[]` |

## 12. Bonds

A bond's `Price` is its **settlement-date market value**: (clean + accrued at standard settlement) x
face / 100, holder-signed, never discounted back to the pricing date (DEV-I20). Its own rate
(`IRFwdRate`) is the yield to maturity; there is no separate yield measure.

| Question | How to find out | Goes into |
|---|---|---|
| Which identifier types (ISIN, CUSIP, ticker), and which security-master call? Is it point in time (no survivorship, no reopened issue sizes from the future)? | resolve a matured bond as of a past date | the template's static-data primitive, called once in `resolve` |
| How are bonds marked: from a curve plus a spread, or from quoted clean prices? | docs, data source | the market primitive; with quoted prices only, see the curve question below |
| Price basis: clean or dirty? Per 100 or per unit of face? Valued at settlement or at the pricing date? | price one bond and compare with a published invoice | `Price` = dirty x face / 100 at standard settlement, undiscounted |
| Settlement lag and calendar (UST: T+1 on the SIFMA-style bond calendar)? | price on a Friday and before a holiday | `DaysToSettlement`, and every "at settlement" date below |
| Accrual day count (UST: ACT/ACT ICMA) and the accrued convention (to settlement, not to the trade date)? | compute the accrued of one bond by hand | `AccruedInterest`, `CleanPrice` |
| Ex-coupon rules (none for UST; some markets go ex 7 business days early)? | price across an ex-date | the contract text assumes no ex period (a flow drops when settlement reaches its payment date): for a bond with one, write down how `Cashflows`' drop date and `AccruedInterest` treat it before you map them |
| Yield convention: compounding (UST street: semiannual), day count, street or true yield? | invert a price yourself | `IRFwdRate`, `ModifiedDuration`, `Convexity`: one convention for all three |
| Is there a discount curve to shift under the bond while holding its spread? | docs | the shift primitives. With quoted prices only: solve the Z-spread over a reference curve once per date and shift the curve with the spread held; every row must still be mapped |
| Which reference curve gives the roll-down (the bond's own fitted curve, a par curve, a swap curve)? | docs | `RollDown` (section 13) |
| Is `size` a face amount or a number of bonds? `buy_sell`? | gs `Bond` has `size`, not `notional_amount` | `resolve` folds `buy_sell` x sign(size) into a signed face |

## 13. Repo and financing

A `Bond` config maps the financing contract (DEV-I21): `RepoRate`, `RepoHaircut`,
`FinancingToDate`, `Carry`, `RollDown` and the financed `ForwardPrice`. A config that does not map
them does not load. The engine books the change of `FinancingToDate` and the coupons the position
drops as cash (DEV-E22), so a financed bond's `Total` = ΔPV + coupons − repo interest.

| Question | How to find out | Goes into |
|---|---|---|
| Where do historical repo fixings come from (a repo index, a GC fixing history, a dealer feed)? Point in time? | load a past date's fixing and compare with the published value | the repo primitive; `RepoRate` must be finite on every held date |
| General collateral or special? Per identifier? Where do specials come from, and how far back? | compare an on-the-run issue's rate with GC | `RepoRate` (GC − special spread) |
| Overnight (re-fixes daily) or term (locked at the trade date)? Which term? | the desk's funding practice | a resolve kwarg pinned at the trade date (the term rate is pinned too) |
| Repo day count and compounding (USD: simple ACT/360)? | the fixing's methodology | `FinancingToDate`, `ForwardPrice` |
| Haircut: what fraction is not financed? Fixed or per collateral type? | the clearing house's or the desk's schedule | `RepoHaircut` (decimal 0.02 = 2%), and the financed principal `(1 − haircut) x Price(trade date)` |
| Which rate applies over weekends and holidays? | the fixing calendar | the last business day's fixing (the contract text) |
| How is the haircut capital funded? | the desk | inside `FinancingToDate` if you model it; never also as a `cash_accrual` model (the engine warns: funding counted twice) |
| Which currency is the funding leg in? | the trade | `FinancingToDate`'s currency (the bond's) |

## 14. The Bond contract worksheet (40 rows)

One line per row of `contracts.contract_for('Bond')`, in contract order. "ZBC" = zero by convention
(`contracts.ZERO_BY_CONVENTION["Bond"]`): map a literal 0.0 (or `{}` for the vega cube). H, the
carry horizon, is the standard settlement date s plus one calendar month, rolled to the following
business day. The runnable reference is `tests/assets/toy_usd_bond.yaml` on `tests/toylib/bond.py`.

| # | Measure (forms) | Kind: units | Bond meaning (the contract text is in `src/pricebt/risk/contracts.py`) | Verify with |
|---|---|---|---|---|
| 1 | `Price` (s) | value: `ccy` | settlement-date market value, holder-signed, undiscounted; drops a flow on the first trade date whose settlement is on or after its payment date | long > 0; `= DirtyPrice x face / 100` |
| 2 | `IRDelta` (s, b) | sens1: `ccy_per_bp` | s: dPrice/dy per +1bp of yield along the parallel curve shift; b: key-rate ladder | long < 0; ladder sums to the parallel curve delta |
| 3 | `IRDiscountDeltaParallel` (s) | sens1 | discount curve +1bp only; fixed coupons project nothing, so a single-curve bond's equals its whole-curve bump | equals the parallel curve delta on one curve |
| 4 | `IRGammaParallel` (s) | sens2: `ccy_per_bp2` | chain-rule d²Price/dy² per bp² | long > 0; ≈ `Convexity x Price x 1e-8` |
| 5 | `IRGamma` (b) | sens2 | diagonal key-rate gamma ladder | finite per pillar |
| 6-8 | `IRVega` (s, b), `IRVanna`, `IRVolga` (s) | sens1 / sens2 | ZBC: a bullet bond has no optionality | 0.0 and `{}` |
| 9 | `IRBasis` (s) | sens1 | ZBC: one discount curve | 0.0 |
| 10 | `IRXccyDelta` (s) | sens1 | ZBC: one currency | 0.0 |
| 11 | `IRFwdRate` (s) | rate: `bp`/`pct`/`decimal` | the yield to maturity, finite on every held date | price ↔ yield round trip |
| 12 | `IRSpotRate` (s) | rate | the yield | equals `IRFwdRate` |
| 13-15 | `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol` (s) | vol | ZBC | 0.0 in your vol unit |
| 16 | `Theta` (s) | theta: `ccy` | per calendar day at a constant yield over the step to the next business day nb: `[Price(nb, same y) + flows dropped in (t, nb] − Price(t)] / (nb − t).days`; no financing | about Price x y / 365 between coupons |
| 17 | `ExpiryInYears` (s) | time: `decimal` | `max(maturity − t, 0).days / 365` | no library call |
| 18 | `Annuity` (s) | annuity: `ccy` | PV of 1.0 a year on the remaining coupon schedule x signed face; long > 0 | `= LocalAnnuityInCents x abs(face)` |
| 19 | `Cashflows` (frame) | table | the flows still in Price; `payment_date` = the trade date Price drops it (T+1: the business day before a business-day coupon date) | Price falls by the coupon on that date |
| 20 | `LightningDV01` (s) | sens1 | the yield DV01 = the `IRDelta` scalar | equal within 1% |
| 21 | `LightningOAS` (s) | rate | OAS over the reference curve (bullet: Z-spread) | reprices the bond |
| 22 | `ParSpread` (s) | rate | par asset-swap spread (or the library's par spread) | intensive: the same long and short |
| 23 | `FairPremium` (s) | value | `= Price` (already the settlement-date value) | identity |
| 24 | `ForwardPrice` (s) | value | `Price x (1 + RepoRate x τ(s, H)) − Σ_{s<c≤H} C x (1 + RepoRate x τ(c, H))`, τ in the repo day count; dead: 0 | forward parity |
| 25 | `PremiumCents` (s) | notional_level | `Price / abs(face)` in the declared unit (`pct`: the dirty price) | `= DirtyPrice` in `pct` for a long (`−DirtyPrice` for a short) |
| 26 | `LocalAnnuityInCents` (s) | notional_level | `Annuity / abs(face)` | identity |
| 27 | `CompoundedFixedRate` (s) | rate | the coupon as `(1 + c/f)^f − 1` | constant over time |
| 28 | `CRIFIRCurve` (frame) | table | SIMM rows from the `IRDelta` ladder; empty when dead | Σ `Amount` = Σ ladder |
| 29 | `PnlExplain` (b) | value | `Price(market_to) − Price(market)` by factor (`IR` for the curve, e.g. `CREDIT` for the spread) | rows sum to the change |
| 30 | `CleanPrice` (s) | notional_level | quoted clean price per 100 at standard settlement, `DirtyPrice − 100 x AccruedInterest / face`; dead: 0 | identity; matches the screen |
| 31 | `DirtyPrice` (s) | notional_level | `100 x Price / face` (signed face: the same long and short); dead: 0 | identity |
| 32 | `AccruedInterest` (s) | value: `ccy` | accrued from the last coupon date to standard settlement (UST: ACT/ACT ICMA), holder-signed; 0 on a coupon settlement date and after maturity | a hand computation |
| 33 | `ModifiedDuration` (s) | time: `decimal`/`number` | `−(1/P) dP/dy`, years per unit decimal yield, P dirty, y in the `IRFwdRate` convention; dead: 0 | ≈ `−1e4 x IRDelta / Price` |
| 34 | `Convexity` (s) | time | `(1/P) d²P/dy²`, years²; dead: 0 | ≈ `1e8 x IRGammaParallel / Price` |
| 35 | `DaysToSettlement` (s) | days: `number`, `scale_with_quantity: false` | calendar days to standard settlement (UST T+1: 1, or 3 over a weekend or holiday) | 3 on a Friday |
| 36 | `RepoRate` (s) | rate | the funding rate in force (overnight GC or special, or the term rate locked at the trade date), simple, repo day count; finite on every held date | the source's fixing on a known date |
| 37 | `RepoHaircut` (s) | rate | the fraction not financed (`decimal` 0.02 = 2%) | the pinned term |
| 38 | `FinancingToDate` (s) | value: `ccy` | cumulative repo interest from the trade date's settlement to the pricing date's (or maturity); principal `(1 − RepoHaircut) x Price(trade date)` pinned at resolve; each calendar day at its `RepoRate`; long ≤ 0, short ≥ 0; 0 on the trade date | one-day change `= −(1 − h) x Price(t₀) x RepoRate x days / basis` |
| 39 | `Carry` (s) | value | `(Price − AccruedInterest) − (ForwardPrice − accrued at H)`: coupon income over (s, H] minus financing | identity |
| 40 | `RollDown` (s) | value | clean value at H on the reference curve rolled down (time to maturity unchanged, spread held) minus clean value now; dead: 0 | `Carry + RollDown = 0` on a flat curve when the repo equals the yield |

The ranges (6-8, 13-15) are one measure each, so the table covers all 40. The checker's bond pack
([`pricebt-verify-asset-config`](../../pricebt-verify-asset-config/SKILL.md)) checks these
identities and the financing on your config.
