---
name: pricebt-risk-measures
description: What an asset config must compute for each interest-rate risk measure pricebt serves, and how to get each one from YOUR pricing library. Covers the gs catalogue and the IRSwap, IRSwaption and Bond contracts (own-rate IRDelta and ladder, chain-rule gamma, normal vega, vanna, volga, rates, vols, per-day Theta, Annuity, Cashflows, the strict additions, PnlExplain; for a Bond also clean/dirty price, accrued, duration, convexity, settlement days and the repo financing contract RepoRate, RepoHaircut, FinancingToDate, Carry, RollDown). Every contract measure must be mapped; none may be declared unsupported. Units, signs, dead instruments, bump-and-reprice recipes, Portfolio, CloseMarket. measures.py prints a contract, a capability matrix and the paste-ready mapping skeleton. Use when a config fails to load with "measure-contract problem(s)", a request raises UnsupportedMeasureError, you wire a swap, swaption or bond (and its repo financing) to a library, or you price and risk a portfolio.
---

# IR risk measures: the contract, and how your library meets it

pricebt serves gs_quant's risk measures by name (`IRDelta`, `IRVega`, `Theta`, ...) but computes none of them: every number comes from a function in an asset config. For `IRSwap`, `IRSwaption` and `Bond`, pricebt goes further and **requires** the config to answer the whole IR measure contract when it loads. All three are *strict classes* (`contracts.STRICT_CLASSES`; `docs/v2/IR_STRICT_CONTRACT.md`, `docs/v2/BOND_DESIGN.md` decision 4.1): every measure and form must be **mapped** to a function with an allowed unit, and declaring a contract measure, one of its forms, or a preset of it under `unsupported_measures:` is itself a load error. `unsupported_measures:` stays legal only for a class without a contract (for example `ConfigInstrument`), where a request for a declared measure raises `UnsupportedMeasureError`. A `Bond` contract (40 rows) also carries the bond analytics gs lacks (DEV-I20) and the **repo financing contract** (DEV-I21), and the engine books a financed position's coupons and repo interest as cash (DEV-E22). This skill tells you, in terms of your own library, what each measure must mean, which unit and sign pricebt expects, how to derive a measure your library lacks (bump-and-reprice always works), and how to check the numbers.

## When to use / not use

- **Use** when you write or extend a config for an `IRSwap`, `IRSwaption` or `Bond` asset, and when you decide per measure whether to map, convert or derive.
- **Use** when you wire a bond's repo financing (repo rate, haircut, cumulative financing, carry, roll-down) or read a financed bond's holding cash.
- **Use** when `load_asset` fails with `N measure-contract problem(s) for instrument ...`, when a request raises `UnsupportedMeasureError`, or when `IRDelta(bump_size=...)` raises `NotSupportedError`.
- **Use** when you price or risk a book: `Portfolio.calc`, `PortfolioRiskResult` indexing, `aggregate`, `to_frame`, historical shapes, `CloseMarket`, `PnlExplain`.
- **Do not use** for the first connection of a library (market handle, `resolve`, trade build). Go to [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md) first, then come back for the measures.
- **Do not use** to prove a config end to end. That is `check_asset.py` in [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md).
- **Do not use** for P&L decomposition over a backtest. That is [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md).

## Inputs

- Your library: how it prices one trade on one date's market, which risk it returns natively (its units and signs), and whether it can reprice on a shifted market (curve, vol, valuation date).
- An asset config (it may be incomplete), or the instrument class you are about to wire.

## Outputs

- A filled capability matrix ([`references/capability-matrix.md`](references/capability-matrix.md)).
- A config that loads with **no warnings**: every contract row is mapped (a literal `0.0` only where `contracts.ZERO_BY_CONVENTION` says the value is 0).
- Numbers that pass the identities in [`references/implementing-measures.md`](references/implementing-measures.md) section 4.

## The contract in one screen

Every value is **holder-signed** and computed **per unit trade**: one unit of the resolved trade, with `buy_sell` folded into a signed notional or face by your `resolve`. pricebt applies `quantity_` to extensive units and never to intensive ones. **r** is the instrument's own rate (`IRFwdRate`) and **σ** is the normal implied vol at its strike.

| Measure (forms) | Unit | Meaning, sign |
|---|---|---|
| `Price` | ccy | PV, holder-signed; paid flows either drop (listed in `Cashflows`) or never drop (total return). **Bond:** the settlement-date market value, (clean + accrued at standard settlement) × face / 100, **not** discounted to the pricing date |
| `IRDelta` (scalar, bucketed) | ccy_per_bp | scalar: **total** dPV/dr per bp of r, strike vol fixed (payer or long option > 0, long bond < 0); bucketed: curve ladder |
| `IRDiscountDeltaParallel` | ccy_per_bp | +1bp shift of the discount curve only (≠ the IRDelta scalar in general) |
| `IRGammaParallel` | ccy_per_bp2 | chain-rule d²PV/dr² per bp², never d(annuity pv01)/dr |
| `IRGamma` (bucketed) | ccy_per_bp2 | diagonal gamma ladder |
| `IRVega` (scalar, bucketed) | ccy_per_bp | per +1bp **normal** vol; cube `'<tail>;<expiry>'`; swaps and bonds 0.0 / `{}` |
| `IRVanna`, `IRVolga` | ccy_per_bp2 | d(Δ)/dσ per bp·bp and d²PV/dσ² per bp²; request with `aggregation_level='Type'` |
| `IRBasis`, `IRXccyDelta` | ccy_per_bp | basis and xccy deltas; 0.0 when single-curve or single-currency |
| `IRFwdRate`, `IRSpotRate` | bp, pct or decimal | own rate (par, forward swap rate, YTM) and spot-start par; intensive; **finite after death** |
| `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol` | bp, pct or decimal | normal vols (daily = annual/√252); swaps and bonds 0.0 |
| `Theta` | ccy | one **calendar day**, r and σ fixed, curve **translated**, total return. **Bond:** yield held, over the step to the next business day divided by its calendar days; no financing |
| `ExpiryInYears` | decimal | `max(end − t, 0).days / 365` |
| `Annuity` | ccy | N·A = 1e4 × fixed-leg pv01, with the signed notional: pay-fixed > 0, receive-fixed < 0, bought swaption > 0, long bond > 0 |
| `Cashflows` (frame) | ccy | flows `Price` will still drop; `returns: frame`, `scale_columns: [payment_amount, ...]`. **Bond:** `payment_date` is the trade date on which `Price` drops the flow (T+1: the business day before a business-day coupon date) |
| `ParSpread` (IRSwap, IRSwaption) | bp, pct or decimal | floating-leg spread making Price 0; one curve and matching schedules: `K − IRFwdRate` (swaption: strike − forward); same for both directions; intensive |
| `FairPremium` (IRSwap, IRSwaption) | ccy | `Price / DF(premium settlement)`: the premium payment date if set, else spot; no spot lag: = Price |
| `ForwardPrice` (IRSwap, IRSwaption) | ccy | `Price / DF(expiry)`, the date `ExpiryInYears` counts to; Price on or after it |
| `PremiumCents` (IRSwap, IRSwaption) | notional_level | `Price / \|notional_amount\|` in the unit (bp: gs's premium in cents); intensive |
| `LocalAnnuityInCents` (IRSwap, IRSwaption) | notional_level | `Annuity / \|notional_amount\|`, holder-signed (10y payer ≈ +8.5 decimal); intensive |
| `CompoundedFixedRate` (IRSwap, IRSwaption) | bp, pct or decimal | fixed rate (strike) as `(1 + K/f)^f − 1`; annual leg: K; a trade term; intensive |
| `CRIFIRCurve` (frame; IRSwap, IRSwaption) | frame | SIMM CRIF rows `RiskType, Qualifier, Bucket, Label1, Label2, Amount, AmountCurrency`; Label1 a lower-case SIMM tenor; Σ Amount = Σ `IRDelta` ladder; `scale_columns: [Amount]`; dead: empty |
| `PnlExplain` (bucketed; IRSwap, IRSwaption) | ccy | a `returns: buckets` portfolio function reading `market_to`: value change by risk factor (`mkt_type` IR, IR VOL, ...), no time component |
| `ProbabilityOfExercise` (IRSwaption) | decimal | probability of finishing in the money |
| `LightningDV01`, `LightningOAS`, `ParSpread` (Bond) | ccy_per_bp / rate | yield DV01 (= the IRDelta scalar), OAS (Z-spread), par ASW spread |
| `FairPremium`, `ForwardPrice` (Bond) | ccy | `FairPremium` = `Price` (already the settlement value); `ForwardPrice` = the financed forward value at H = settlement + 1 calendar month (below) |
| `PremiumCents`, `LocalAnnuityInCents`, `CompoundedFixedRate` (Bond) | notional_level / rate | `Price / \|face\|` (pct: the dirty price per 100); `Annuity / \|face\|`; the coupon as `(1 + c/f)^f − 1` |
| `CRIFIRCurve` (frame), `PnlExplain` (bucketed) (Bond) | frame / ccy | as the swap rows; `PnlExplain` rows e.g. `IR` (curve) and `CREDIT` (the bond's spread) |
| `CleanPrice`, `DirtyPrice` (Bond, DEV-I20) | notional_level | per 100 face: `DirtyPrice = 100 × Price / face`, `CleanPrice = DirtyPrice − 100 × AccruedInterest / face` (signed face: same for long and short); dead: 0 |
| `AccruedInterest` (Bond, DEV-I20) | ccy | coupon accrued to standard settlement (UST: ACT/ACT ICMA), holder-signed; 0 on a coupon settlement date and after maturity |
| `ModifiedDuration`, `Convexity` (Bond, DEV-I20) | time (decimal, number) | `−(1/P) dP/dy` in years, `(1/P) d²P/dy²` in years², y the `IRFwdRate` yield, P dirty; 0 when dead |
| `DaysToSettlement` (Bond, DEV-I20) | days (number only) | calendar days to standard settlement (UST T+1: 1, or 3 over a weekend or holiday) |
| `RepoRate`, `RepoHaircut` (Bond, DEV-I21) | rate | the funding rate in force (overnight GC or special, or a term rate locked at the trade date), simple, repo day count (USD ACT/360); the fraction not financed (decimal 0.02 = 2%) |
| `FinancingToDate` (Bond, DEV-I21) | ccy | cumulative repo interest since the trade's settlement, principal `(1 − RepoHaircut) × Price(trade date)` pinned at resolve; long ≤ 0, short ≥ 0; 0 on the trade date. **The engine books its change as cash (DEV-E22)** |
| `Carry`, `RollDown` (Bond, DEV-I21) | ccy | `Carry` = clean value now − clean forward value at H (coupon income minus financing); `RollDown` = clean value at H on the rolled-down curve − clean value now; dead: 0 |

The Bond's yield to maturity **is** `IRFwdRate` (DEV-I12); there is no separate yield measure, by design. The whole 40-row Bond table is in [`references/measure-catalogue.md`](references/measure-catalogue.md) section 1.1; the formulas for the financing rows are there and in [`references/implementing-measures.md`](references/implementing-measures.md) section 2.

`notional_level` allows bp, pct, decimal or number and must be intensive (`scale_with_quantity: false`; bp, pct and decimal are by default). The `days` kind (`DaysToSettlement`) allows only `number`, which defaults to extensive, so set `scale_with_quantity: false` on it.

**Rules across the table.**

- **Map, never declare, on any contract class.** A literal constant is honest only for `contracts.ZERO_BY_CONVENTION`, a `{class: {measure: reason}}` dict: the vol measures (`IRVega`, `IRVanna`, `IRVolga`, the three vol levels), `IRBasis` and `IRXccyDelta` on an `IRSwap` and on a `Bond`; `IRBasis` and `IRXccyDelta` on an `IRSwaption`. The Bond reasons: "a bullet bond has no optionality: vol exposure and vol levels are 0 (R2-8)", "a bullet bond is discounted on one curve: no projection-vs-discount basis", "a bond pays in one currency: no cross-currency basis". The checker FAILs any other literal (`ir_fake_constant`).
- **Presets count toward their base** (R2-10): an `IRDeltaParallel` key serves the `IRDelta` scalar, `IRGammaParallelLocalCcy` serves `IRGammaParallel`, `PnlExplainClose` resolves to `PnlExplain`. Declaring a preset of a contract measure is a load error too.
- **pricebt-only measures** (gs has none): `pricebt.risk.PRICEBT_MEASURES` = `AccruedInterest`, `Carry`, `CleanPrice`, `Convexity`, `DaysToSettlement`, `DirtyPrice`, `FinancingToDate`, `ModifiedDuration`, `RepoHaircut`, `RepoRate`, `RollDown`. Each cites DEV-I20 or DEV-I21; the gs parity test allows exactly these extras.
- A **dead instrument** (matured, expired OTM, fully paid) returns 0.0 for every sensitivity, finite levels continued from the last live value, and an empty `Cashflows` / `CRIFIRCurve`. One NaN poisons every later `pnl_explain` total.
- The **units of each level must agree** across a book. The toy and shipped configs use bp.
- Names outside the contract are unrestricted.

**Excluded, with reasons** (`contracts.EXCLUDED`): the IR-relevant catalogue measures deliberately outside every contract (`LightningDV01` and `LightningOAS` are no longer here: they are Bond contract rows). A test keeps this list exact.

| Name(s) | Reason |
|---|---|
| `DollarPrice` | the engine maps it to Price(currency='USD') |
| `ResolvedInstrumentValues` | the engine answers it (resolution), not a config function |
| `PricePips` | FX quoting (pips) |
| `Description`, `Market`, `MarketData`, `MarketDataAssets` | non-numeric gs server metadata |
| `BaseCPI`, `InflMaturityCPI`, `Infl_CompPeriod`, `InflationDelta`, `InflationDeltaParallel`, `InflDeltaParallelLocalCcyInBps` | inflation instruments |
| `PnlExplainLive`, `PnlPredictLive` | the live market: NotSupportedError (DEV-M1) |
| `FairVarStrike`, `FairVolStrike` | variance swaps |
| `CrossMultiplier` | FX |
| any preset or fallback of a contract measure (it has a base name) | resolves to its base (R2-10) |

The full catalogue (with presets and classes) is in [`references/measure-catalogue.md`](references/measure-catalogue.md). The authoritative contract text is `src/pricebt/risk/contracts.py`, and the design is `docs/v2/IR_RISK_DESIGN.md` (§00 overrides the rest of that file) plus `docs/v2/IR_STRICT_CONTRACT.md` (the strict rule and the eight added rows) and `docs/v2/BOND_DESIGN.md` (the Bond contract, financing and holding cash).

## Procedure

1. **Print the contract for your class.**

   ```powershell
   $env:PYTHONPATH = "src;tests"   # POSIX: PYTHONPATH=src:tests
   python skills/pricebt-risk-measures/scripts/measures.py contract IRSwaption
   ```

2. **Fill the capability matrix** ([`references/capability-matrix.md`](references/capability-matrix.md)). For each row, write down your library's call, its native unit and sign, and a *how*: native, convert, derive (bump), AD, or zero (only `ZERO_BY_CONVENTION`). Also settle the book-wide decisions: level units, the `Price` convention (total return or drop paid flows; a Bond's is fixed: the settlement-date value, dropping paid flows), the vol model and its normal conversion, the bond yield convention, settlement after expiry, and for a Bond the repo source (GC or special, overnight or term), haircut and repo day count.

3. **Write the functions.** Map native and converted measures directly. Derive the rest with one primitive, "reprice one unit trade on a shifted market and return (PV, own rate)", placed in the config's `code:` block or your helper module:

   ```python
   def _own_rate_greeks(market, trade, h=1.0, vol_bp=0.0):      # _reprice: YOUR library on a shifted market
       (n_dn, r_dn), (n_0, r_0), (n_up, r_up) = (_reprice(market, trade, s, vol_bp) for s in (-h, 0.0, h))
       delta = (n_up - n_dn) / (r_up - r_dn)                                                  # IRDelta scalar
       gamma = (n_up + n_dn - 2 * n_0 - delta * (r_up + r_dn - 2 * r_0)) / ((r_up - r_dn) / 2) ** 2   # IRGammaParallel
       return delta, gamma
   ```

   The per-measure recipes are in [`references/implementing-measures.md`](references/implementing-measures.md) section 2: vanna and volga from vol bumps, the translated-curve theta, bond same-yield theta, key-rate and diagonal gamma ladders, the vol cube, frames, the unit and sign conversion tables, and shape-specific advice for object libraries, platforms, in-house functions and bond packages. The runnable reference is `tests/toylib/irrisk.py`, `tests/toylib/swaption.py` and `tests/toylib/bond.py`. Starting from nothing? Copy the tested template for your class (`skills/pricebt-connect-pricing-library/references/config-template.yaml`, `config-template-swaption.yaml`, `config-template-bond.yaml`): it already maps every contract row onto about 15 `lib_*` primitives you fill from your library.

4. **Map zero where zero is true, and nowhere else.** A swap or bullet bond has no vol exposure under any model: map `IRVega`, `IRVanna`, `IRVolga` and the three vol levels to `'0.0'` (the cube to `'{}'`). A single-curve model (and every bullet bond) maps `IRBasis` to `'0.0'`, and a single-currency one maps `IRXccyDelta` to `'0.0'`. Any other constant (a `'0.0'` discount delta, an empty CRIF, a `'0.0'` repo rate or financing) is a placeholder, not a measure.

5. **Bond: wire the financing.** `RepoRate`, `RepoHaircut`, `FinancingToDate`, `Carry`, `RollDown` and the Bond `ForwardPrice` are required rows: a Bond config that does not map them does not load. Pin the trade date, its settlement date, the financed principal per unit and (for term repo) the locked rate in `resolve`; the formulas are in [`references/implementing-measures.md`](references/implementing-measures.md) section 2, "The Bond contract". Because the asset maps `FinancingToDate`, the engine books each held position's dropped `Cashflows` plus the change of `FinancingToDate` as cash on every mark and exit (DEV-E22), recorded in `backtest.holding_cash[d][position] = (ccy, cashflow, financing)`; `Total` = ΔPV + coupons − repo interest on any grid (`tests/test_holding_cash.py`). Do not also give the strategy a `cash_accrual` model: it would charge the funding loan a second time (the engine warns).

6. **Fill the gaps.** Print what to paste for everything still missing:

   ```powershell
   python skills/pricebt-risk-measures/scripts/measures.py block configs/assets/<asset>.yaml
   ```

   It prints the **mapping skeleton** for every contract class (`contracts.mapping_skeleton`, the same text the load error ends with): a `functions:` stub per scalar or frame form, a `portfolio_functions:` stub per bucketed form, each with an allowed unit (and `scale_with_quantity: false` for an intensive kind), and the `risk_measures:` lines. Merge each section into the config's own (a second key is a duplicate-key error), then write every `expr`: the stub `contracts.SKELETON_EXPR` (`... TODO`) does not compile on purpose, so the config loads only once each stub computes its measure. Pick the unit you return. Every measure is computable from "reprice one unit trade on a shifted market" plus the trade's static and repo data (step 3 and [`references/implementing-measures.md`](references/implementing-measures.md)).

7. **Audit the config against the contract.** This does not execute your library.

   ```powershell
   python skills/pricebt-risk-measures/scripts/measures.py matrix configs/assets/<asset>.yaml
   ```

   It prints one row per (measure, form): `MAPPED` (function, unit, and `via` a preset key) or `MISSING`. A declared contract row stays `MISSING` and the declaration itself is a problem (`Bond/IRSwap/IRSwaption configs must map every contract measure; unsupported_measures cannot satisfy them`). Under the table come the problems `load_asset` would raise (wrong unit, a level that scales with quantity, an unknown function, a declaration of a contract measure) and its warnings. A hint column says how any library computes a missing row (the zeros, `ExpiryInYears`, a swap's `Annuity` and `IRSpotRate`, the strict additions). The command exits 1 while anything is MISSING or has a problem. In-process: `measures.capability_matrix(path_or_mapping)`.

8. **Verify the numbers.**
   - In your library: reprice each derived measure with a second step size or method.
   - Through pricebt: adapt and run the block in [`references/implementing-measures.md`](references/implementing-measures.md) section 4 (buy = −sell, signs, straddle = payer + receiver, the ATM vega band, parity, a one-day Taylor explain, ladder sums, dead instruments). For a Bond, also the identities of section 4.1 (clean + accrued = dirty, `FairPremium = Price`, duration and convexity vs a yield bump, forward parity, one day of financing).
   - Run `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>`.

9. **Optional: honour finite-difference parameters.** A function that names `pricebt_bump_size`, `pricebt_finite_difference_method`, `pricebt_local_curve` or `pricebt_scale_factor` receives the requested value, e.g. `IRDelta(aggregation_level='Type', bump_size=5)`. A function that names none of them makes the request raise `NotSupportedError`, so nothing is silently ignored. See [`references/portfolio-and-results.md`](references/portfolio-and-results.md) section 5.

10. **Use the measures on a book.** `Portfolio`, `calc` under `PricingContext` or `HistoricalPricingContext`, result indexing, `aggregate`, `to_frame`, `CloseMarket` and `PnlExplain` are in [`references/portfolio-and-results.md`](references/portfolio-and-results.md), with a runnable tour:

   ```python
   with PricingContext(pricing_date=d):
       book.resolve()                                   # pin ATM strikes and dates first
       res = book.calc((Price, IRDeltaParallel, IRDelta))
   res[Price]["10y"]; res[IRDeltaParallel].aggregate(); res[IRDelta].aggregate(); res[Price].to_frame()
   ```

## Checks (you are done when)

- `measures.py matrix <config>` exits 0: no MISSING, no problems.
- The config loads under `warnings.simplefilter("error")`, with no `unsupported_measures:` block at all (`IRSwap`, `IRSwaption` and `Bond`).
- The contract identities hold in your library and through pricebt ([`references/implementing-measures.md`](references/implementing-measures.md) section 4).
- No literal constant outside `contracts.ZERO_BY_CONVENTION`.
- Bond: a short financed backtest of a long over a coupon date has `Total` = ΔPrice + coupons − repo interest, and `backtest.holding_cash` lists the position on every marked date.
- `check_asset.py` passes on the config.

## Pitfalls

| Trap | Symptom | Fix |
|---|---|---|
| **Half gamma**: `IRGammaParallel` = d(annuity pv01)/dr | gamma P&L half the real one at the money | the chain-rule formula on the delta bumps; at the money, Γ = 2·Δpv01/Δpar |
| **Fixed-annuity delta**: annuity pv01 as the `IRDelta` scalar | right at the money, a first-order residual `N·(F−K)·ΔA` off-market | the total derivative `[PV(+h) − PV(−h)] / [r(+h) − r(−h)]` |
| **Per-year theta** mapped to `Theta` | theta attribution 365× too big | per calendar day; `IRTheta` (per year) = 365 × `Theta` is a separate custom name |
| **Rolled curve** in theta (rebuilt on t+1) | theta includes roll-down; the delta attribution double-counts it | translate: `DF'(x) = DF(x)/DF(t+1d)`; bonds: the same yield |
| Business-day or ACT/360 `ExpiryInYears` | theta P&L mis-scaled on weekends | calendar days / 365 exactly |
| Vega per 1% or lognormal | vega P&L ×100, or a model mismatch | per bp of **normal** vol; convert lognormal by a bump, not a scale |
| Cube keyed `'<expiry>;<tail>'` | vega buckets transposed | gs order: `'<tail>;<expiry>'`, e.g. `'10Y;1Y'` |
| NaN level on a dead trade | every later cumulative `pnl_explain` value is NaN | the last live value (or the live forward); sensitivities 0.0 |
| Par rate in percent next to bp elsewhere | attribution mixes units | one unit per level across the book; the P&L definition takes that unit |
| `buy_sell` ignored | a sold option has the long's sign | fold it into a signed notional in `resolve` |
| Receiver-positive or loss-positive DV01 | delta sign flipped | multiply by −1 (conversion table) |
| Clean or per-100 bond `Price` | PV off by accrued, or 100× | (clean + accrued) × face / 100 at standard settlement |
| Bond `Price` discounted from settlement to the pricing date | `Price ≠ FairPremium`, `DirtyPrice` off the quote by a day of carry | the undiscounted settlement-date value (DEV-I20) |
| Bond `Cashflows.payment_date` = the coupon date | the drop in `Price` lands a day before the booked coupon (T+1) | the trade date `Price` drops the flow: the first date whose settlement is on or after the coupon date |
| Declaring any contract measure (any of Bond, IRSwap, IRSwaption) | load error: `Bond/IRSwap/IRSwaption configs must map every contract measure; unsupported_measures cannot satisfy them` | map it (vol rows: `'0.0'`, R2-8); derive the rest by bump-and-reprice |
| `FinancingToDate` on today's `Price` | financing drifts with the market; `Total` no longer = ΔPV + coupons − repo | principal `(1 − RepoHaircut) × Price(trade date)`, pinned in `resolve` |
| Bond financed and a `cash_accrual` model on the strategy | `UserWarning`: "... so the funding is counted twice (pricebt DEV-E22)" | drop the `cash_accrual` model; fund the haircut inside `FinancingToDate` if you want it |
| `DaysToSettlement` with `unit: number` and no `scale_with_quantity: false` | load error: "scales with quantity; a days level must be intensive (set scale_with_quantity: false)" | set `scale_with_quantity: false` |
| A vendor "carry" mapped to `Theta` | financing counted in both `Theta` and `FinancingToDate` | `Theta` holds the yield and has no financing; map the vendor carry to `Carry` if it matches that row |
| A `'0.0'` placeholder for a real measure (discount delta, spot rate, swaption vega) | loads, silently zeroes risk; `check_asset.py` `ir_fake_constant` FAILs | compute it; constants only for `ZERO_BY_CONVENTION` |
| `PremiumCents` / `LocalAnnuityInCents` on the signed notional, or scaling with quantity | sign flips with direction; ×quantity | divide by `\|notional_amount\|`; intensive unit |
| `ForwardPrice = Price × DF` | below Price in a positive-rate world | `Price / DF(expiry)` |
| CRIF `Label1` `'10Y'` | not a SIMM tenor | lower case `'10y'`, one of `contracts.SIMM_IR_TENORS` |
| A memo keyed on the market alone, or on `pricebt_date` alone | under `CloseMarket` one market object serves two dates: t-valued numbers where a function values at `pricebt_date`, or d's market | key a memoised value on the market object, `pricebt_date`, the trade and every `pricebt_*` parameter it reads |
| `Annuity` from a QuantLib-style `fixedLegBPS` | negative for a payer: `swap_annuity_sign` FAILs, and the fixed-annuity delta check misreads | Annuity carries the signed notional: pay-fixed > 0, receive-fixed < 0, bought swaption > 0, long bond > 0; `−1e4 × fixedLegBPS` for a swap |
| A process-global evaluation date moved for `Theta` and not restored | every later value on the wrong date (pricebt interleaves dates in one process) | set it inside each pricing call from the market's own date; `try/finally` around any move |
| Unresolved `'ATM'` instrument priced on several dates | every date is a fresh ATM trade | call `resolve()` at the trade date first |

## Related skills

- [`pricebt-architecture`](../pricebt-architecture/SKILL.md): where contracts, parameters, frames and results sit in the model.
- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): the first working config (market, resolve, trade, Price).
- [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): patterns by library shape, and the error catalogue.
- [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md): `check_asset.py` for a whole config.
- [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md): `ir_pnl_definition` and `pnl_explain_table`, built on these measures.
- [`pricebt-port-gs-notebook`](../pricebt-port-gs-notebook/SKILL.md): gs pricing-and-risk and portfolio notebooks.
