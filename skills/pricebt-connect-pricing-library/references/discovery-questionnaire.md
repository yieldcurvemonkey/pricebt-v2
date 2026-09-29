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

An `IRSwap`, `IRSwaption` or `Bond` config must, for **every** row of its class's measure contract,
either map the measure to a function with an allowed unit or declare it under
`unsupported_measures:` with a reason (DEV-I11). The contract is code, not prose. Print it for
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

Declare a measure unsupported only when the library has no primitive to derive it from. That is
honest: a request for a declared measure raises `UnsupportedMeasureError` with your reason. A fake
`0.0` or a NaN is never acceptable in its place. The exceptions are the rows that are zero *by
convention* (R2-8, marked "swaps and bonds 0.0" below), because a swap's or a bond's vega really is 0.

| Measure (forms) | pricebt expects (unit, sign, convention) | Derive it from (template recipe) | Verify with |
|---|---|---|---|
| `Price` (s) | `ccy`, holder-signed PV of one unit of the resolved signed notional. It either drops flows paid on or before the pricing date, **or** never drops them (total return): say which | `lib_pv` (native in every library) | ATM swap PV about 0; payer = -receiver; a bought option > 0; a long bond > 0 |
| `IRDelta` (s) | `ccy_per_bp`: the **total** derivative of Price w.r.t. the own rate r (swap par rate, swaption forward, bond yield) along a parallel shift, `[PV(+h) - PV(-h)] / [r(+h) - r(-h)]` per bp of r (DEV-I12). Payer swap > 0, payer swaption > 0, long bond < 0 | `own_rate_delta` (swap, swaption) or `yield_delta` (bond), from `lib_shift`, `lib_pv` and `lib_own_rate`. A native par-quote DV01 is close. A fixed-annuity pv01 is exact **only at the money** | the recipe with a 0.5bp and a 2bp bump agrees to about 1e-4 |
| `IRDelta` (b) | `ccy_per_bp` at each curve pillar, keys plain tenors (`"2Y"`), same sign as the scalar. It sums to the **parallel** curve delta, which differs from the own-rate scalar by dr/ds (R2-2) | `key_rate_ladder`, from `lib_pillars` and `lib_shift_pillar` | on a single-curve library the sum equals `IRDiscountDeltaParallel` |
| `IRDiscountDeltaParallel` (s) | `ccy_per_bp` for +1bp on the discount curve only | `discount_only_delta`, from `lib_shift_discount` | single curve: close to a swap's scalar delta |
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
| `Theta` (s) | `ccy` **per calendar day**, total return: `PV(t+1d) + flows paid in (t, t+1d] - PV(t)`, with the own rate and vol held fixed: the curve is **translated**, never rolled (DEV-I15). A per-year theta is 365 x this | `theta_one_day`, from `lib_translate` and `lib_cashflows`; a bond: the same yield one day later | in a frozen world the sum of daily Theta equals the PV drift |
| `ExpiryInYears` (s) | `max(final_or_expiry - t, 0).days / 365`, `decimal` (DEV-I17) | `years_to` over the resolved dates | needs no library call |
| `Annuity` (s) | `ccy`, PV of 1.0 per annum on the fixed schedule (1e4 x the fixed-leg pv01) times the **signed notional**: pay-fixed > 0, receive-fixed < 0, bought swaption > 0, long bond > 0. A QuantLib-style `fixedLegBPS` has the opposite sign for a payer | `lib_annuity` (`-1e4 x fixedLegBPS`); else `-[PV(K+1bp) - PV(K-1bp)] / 2e-4` | an ATM swap's `IRDelta` equals `Annuity x 1e-4`, same sign |
| `Cashflows` (frame) | the flows Price still includes and will drop, one row each; columns `payment_date, payment_amount, currency, payment_type`; `scale_columns: [payment_amount]`; empty for a total-return Price | `lib_cashflows` | the coupons equal what Price drops on each payment date |
| `ProbabilityOfExercise` (s, swaption) | 0..1 under the annuity measure, `decimal` | `lib_prob_exercise`, or the strike bump of PV / annuity | payer + receiver = 1 |
| `LightningDV01` (s, bond) | the yield DV01, which is the bond's IRDelta scalar | map the same function as `IRDelta` | minus modified duration x dirty PV x 1e-4 |
| `LightningOAS`, `ParSpread` (s, bond) | spreads in the declared rate unit | `lib_oas`, `lib_par_spread` | a bullet bond: OAS equals the Z-spread |

Record the answers in a worksheet before writing YAML, one line per row above:

| Measure (form) | Native call or recipe | Library unit and sign | Conversion on the config line | Verified by | Status |
|---|---|---|---|---|---|
| e.g. `IRVega` (s) | `yourlib.risk(t, "VEGA_LN_1PCT")` | per 1% lognormal, holder view | pattern 18 | `vol_bump_vega` on a normal re-quote | mapped |
| e.g. `IRGamma` (b) | none | none | none | none | declared: "yourlib bumps its curve only in parallel" |

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

| Question | How to find out | Goes into |
|---|---|---|
| Which identifier types (ISIN, CUSIP, ticker), and which static-data call? Is it point in time (no survivorship)? | resolve a matured bond as of a past date | `lib_bond_static` |
| How are bonds marked: from a curve plus a spread, or from quoted clean prices? | docs, data source | `lib_market`; with quoted prices only, see the curve question below |
| Price basis: clean or dirty? Per 100 or per unit of face? | price one bond | `lib_pv` returns dirty PV x face / 100, in ccy |
| Accrued-interest convention, ex-coupon period, settlement lag? | docs; price around a coupon date | `lib_pv`, `lib_cashflows` and Theta's cash term |
| Yield convention: compounding, day count, street or true yield? | invert a price yourself | `lib_yield` and `lib_pv_at_yield` must use the same one |
| Is there a discount curve to shift under the bond while holding its spread? | docs | `lib_shift_discount` and `lib_shift_pillar`. If there is none, declare `IRDiscountDeltaParallel`, `IRDelta` bucketed and `IRGamma` with that reason |
| Is `size` a face amount or a number of bonds? `buy_sell`? | gs `Bond` has `size`, not `notional_amount` | `resolve_bond` folds `buy_sell` x sign(size) into a signed face |
| A repo or financing rate for carry? | docs | not in the contract: `Theta` is the constant-yield carry, and financing is a backtest cost |
