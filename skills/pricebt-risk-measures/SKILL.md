---
name: pricebt-risk-measures
description: What an asset config must compute for each interest-rate risk measure pricebt serves, and how to get each one from YOUR pricing library. Covers the gs catalogue and the IRSwap, IRSwaption and Bond contracts (own-rate IRDelta and ladder, chain-rule gamma, normal vega and cube, vanna, volga, rates, vols, per-day Theta, ExpiryInYears, Annuity, Cashflows, ParSpread, FairPremium, ForwardPrice, PremiumCents, LocalAnnuityInCents, CompoundedFixedRate, CRIFIRCurve, PnlExplain, bond extras). IRSwap and IRSwaption configs must map every contract measure; only a Bond may declare one unsupported. Units, signs, dead instruments, bump-and-reprice recipes, bump_size, Portfolio, CloseMarket. measures.py prints a contract, a capability matrix, and the paste-ready mapping skeleton or Bond declaration block. Use when a config fails to load with "measure-contract problem(s)", a request raises UnsupportedMeasureError, you wire a swap, swaption or bond to a library, or you price and risk a portfolio.
---

# IR risk measures: the contract, and how your library meets it

pricebt serves gs_quant's risk measures by name (`IRDelta`, `IRVega`, `Theta`, ...) but computes none of them: every number comes from a function in an asset config. For `IRSwap`, `IRSwaption` and `Bond`, pricebt goes further and **requires** the config to answer the whole IR measure contract when it loads. For **`IRSwap` and `IRSwaption`** (the *strict classes*, `docs/v2/IR_STRICT_CONTRACT.md`) every measure and form must be **mapped** to a function with an allowed unit: declaring a contract measure, one of its forms, or a preset of it under `unsupported_measures:` is itself a load error. A **`Bond`** may still map a row or declare it with a reason. This skill tells you, in terms of your own library, what each measure must mean, which unit and sign pricebt expects, how to derive a measure your library lacks (bump-and-reprice always works), when a Bond declaration is the honest answer, and how to check the numbers.

## When to use / not use

- **Use** when you write or extend a config for an `IRSwap`, `IRSwaption` or `Bond` asset, and when you decide per measure whether to map, convert or derive (or, for a Bond only, declare).
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
- A config that loads with **no warnings**: every contract row is mapped (or zero by convention); a Bond row may instead be declared with a specific reason.
- Numbers that pass the identities in [`references/implementing-measures.md`](references/implementing-measures.md) section 4.

## The contract in one screen

Every value is **holder-signed** and computed **per unit trade**: one unit of the resolved trade, with `buy_sell` folded into a signed notional or face by your `resolve`. pricebt applies `quantity_` to extensive units and never to intensive ones. **r** is the instrument's own rate (`IRFwdRate`) and **σ** is the normal implied vol at its strike.

| Measure (forms) | Unit | Meaning, sign |
|---|---|---|
| `Price` | ccy | PV, holder-signed; paid flows either drop (listed in `Cashflows`) or never drop (total return) |
| `IRDelta` (scalar, bucketed) | ccy_per_bp | scalar: **total** dPV/dr per bp of r, strike vol fixed (payer or long option > 0, long bond < 0); bucketed: curve ladder |
| `IRDiscountDeltaParallel` | ccy_per_bp | +1bp shift of the discount curve only (≠ the IRDelta scalar in general) |
| `IRGammaParallel` | ccy_per_bp2 | chain-rule d²PV/dr² per bp², never d(annuity pv01)/dr |
| `IRGamma` (bucketed) | ccy_per_bp2 | diagonal gamma ladder |
| `IRVega` (scalar, bucketed) | ccy_per_bp | per +1bp **normal** vol; cube `'<tail>;<expiry>'`; swaps and bonds 0.0 / `{}` |
| `IRVanna`, `IRVolga` | ccy_per_bp2 | d(Δ)/dσ per bp·bp and d²PV/dσ² per bp²; request with `aggregation_level='Type'` |
| `IRBasis`, `IRXccyDelta` | ccy_per_bp | basis and xccy deltas; 0.0 when single-curve or single-currency |
| `IRFwdRate`, `IRSpotRate` | bp, pct or decimal | own rate (par, forward swap rate, YTM) and spot-start par; intensive; **finite after death** |
| `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol` | bp, pct or decimal | normal vols (daily = annual/√252); swaps and bonds 0.0 |
| `Theta` | ccy | one **calendar day**, r and σ fixed, curve **translated**, total return |
| `ExpiryInYears` | decimal | `max(end − t, 0).days / 365` |
| `Annuity` | ccy | N·A = 1e4 × fixed-leg pv01, with the signed notional: pay-fixed > 0, receive-fixed < 0, bought swaption > 0, long bond > 0 |
| `Cashflows` (frame) | ccy | flows `Price` will still drop; `returns: frame`, `scale_columns: [payment_amount, ...]` |
| `ParSpread` (IRSwap, IRSwaption) | bp, pct or decimal | floating-leg spread making Price 0; one curve and matching schedules: `K − IRFwdRate` (swaption: strike − forward); same for both directions; intensive |
| `FairPremium` (IRSwap, IRSwaption) | ccy | `Price / DF(premium settlement)`: the premium payment date if set, else spot; no spot lag: = Price |
| `ForwardPrice` (IRSwap, IRSwaption) | ccy | `Price / DF(expiry)`, the date `ExpiryInYears` counts to; Price on or after it |
| `PremiumCents` (IRSwap, IRSwaption) | notional_level | `Price / \|notional_amount\|` in the unit (bp: gs's premium in cents); intensive |
| `LocalAnnuityInCents` (IRSwap, IRSwaption) | notional_level | `Annuity / \|notional_amount\|`, holder-signed (10y payer ≈ +8.5 decimal); intensive |
| `CompoundedFixedRate` (IRSwap, IRSwaption) | bp, pct or decimal | fixed rate (strike) as `(1 + K/f)^f − 1`; annual leg: K; a trade term; intensive |
| `CRIFIRCurve` (frame; IRSwap, IRSwaption) | frame | SIMM CRIF rows `RiskType, Qualifier, Bucket, Label1, Label2, Amount, AmountCurrency`; Label1 a lower-case SIMM tenor; Σ Amount = Σ `IRDelta` ladder; `scale_columns: [Amount]`; dead: empty |
| `PnlExplain` (bucketed; IRSwap, IRSwaption) | ccy | a `returns: buckets` portfolio function reading `market_to`: value change by risk factor (`mkt_type` IR, IR VOL, ...), no time component |
| `ProbabilityOfExercise` (IRSwaption) | decimal | probability of finishing in the money |
| `LightningDV01`, `LightningOAS`, `ParSpread` (Bond) | ccy_per_bp / rate | yield DV01, OAS (Z-spread), par ASW spread |

`notional_level` (a new kind) allows bp, pct, decimal or number and must be intensive (`scale_with_quantity: false`; bp, pct and decimal are by default).

**Rules across the table.**

- **Map, never declare, on a swap or swaption.** A literal constant is honest only for `contracts.ZERO_BY_CONVENTION`: the vol measures (`IRVega`, `IRVanna`, `IRVolga`, the three vol levels), `IRBasis` and `IRXccyDelta` on an `IRSwap`; `IRBasis` and `IRXccyDelta` on an `IRSwaption`. The checker FAILs any other literal (`ir_fake_constant`).
- **Presets count toward their base** (R2-10): an `IRDeltaParallel` key serves the `IRDelta` scalar, `IRGammaParallelLocalCcy` serves `IRGammaParallel`, `PnlExplainClose` resolves to `PnlExplain`. Declaring a preset of a strict contract measure is a load error too.
- A **dead instrument** (matured, expired OTM, fully paid) returns 0.0 for every sensitivity, finite levels continued from the last live value, and an empty `Cashflows` / `CRIFIRCurve`. One NaN poisons every later `pnl_explain` total.
- The **units of each level must agree** across a book. The toy and shipped configs use bp.
- Names outside the contract are unrestricted.

**Excluded, with reasons** (`contracts.EXCLUDED`): the IR-relevant catalogue measures deliberately outside the strict contract. A test keeps this list exact.

| Name(s) | Reason |
|---|---|
| `DollarPrice` | the engine maps it to Price(currency='USD') |
| `ResolvedInstrumentValues` | the engine answers it (resolution), not a config function |
| `PricePips` | FX quoting (pips) |
| `Description`, `Market`, `MarketData`, `MarketDataAssets` | non-numeric gs server metadata |
| `LightningDV01`, `LightningOAS` | bond analytics (the Bond contract) |
| `BaseCPI`, `InflMaturityCPI`, `Infl_CompPeriod`, `InflationDelta`, `InflationDeltaParallel`, `InflDeltaParallelLocalCcyInBps` | inflation instruments |
| `PnlExplainLive`, `PnlPredictLive` | the live market: NotSupportedError (DEV-M1) |
| `FairVarStrike`, `FairVolStrike` | variance swaps |
| `CrossMultiplier` | FX |
| any preset or fallback of a contract measure (it has a base name) | resolves to its base (R2-10) |

The full catalogue (with presets and classes) is in [`references/measure-catalogue.md`](references/measure-catalogue.md). The authoritative contract text is `src/pricebt/risk/contracts.py`, and the design is `docs/v2/IR_RISK_DESIGN.md` (§00 overrides the rest of that file) plus `docs/v2/IR_STRICT_CONTRACT.md` (the strict rule and the eight added rows).

## Procedure

1. **Print the contract for your class.**

   ```powershell
   $env:PYTHONPATH = "src;tests"   # POSIX: PYTHONPATH=src:tests
   python skills/pricebt-risk-measures/scripts/measures.py contract IRSwaption
   ```

2. **Fill the capability matrix** ([`references/capability-matrix.md`](references/capability-matrix.md)). For each row, write down your library's call, its native unit and sign, and a *how*: native, convert, derive (bump), AD, zero (only `ZERO_BY_CONVENTION`), or declare (Bond only). Also settle the book-wide decisions: level units, the `Price` convention (total return or drop paid flows), the vol model and its normal conversion, the bond yield convention, and settlement after expiry.

3. **Write the functions.** Map native and converted measures directly. Derive the rest with one primitive, "reprice one unit trade on a shifted market and return (PV, own rate)", placed in the config's `code:` block or your helper module:

   ```python
   def _own_rate_greeks(market, trade, h=1.0, vol_bp=0.0):      # _reprice: YOUR library on a shifted market
       (n_dn, r_dn), (n_0, r_0), (n_up, r_up) = (_reprice(market, trade, s, vol_bp) for s in (-h, 0.0, h))
       delta = (n_up - n_dn) / (r_up - r_dn)                                                  # IRDelta scalar
       gamma = (n_up + n_dn - 2 * n_0 - delta * (r_up + r_dn - 2 * r_0)) / ((r_up - r_dn) / 2) ** 2   # IRGammaParallel
       return delta, gamma
   ```

   The per-measure recipes are in [`references/implementing-measures.md`](references/implementing-measures.md) section 2: vanna and volga from vol bumps, the translated-curve theta, bond same-yield theta, key-rate and diagonal gamma ladders, the vol cube, frames, the unit and sign conversion tables, and shape-specific advice for object libraries, platforms, in-house functions and bond packages. The runnable reference is `tests/toylib/irrisk.py`, `tests/toylib/swaption.py` and `tests/toylib/bond.py`. Starting from nothing? Copy the tested template for your class (`skills/pricebt-connect-pricing-library/references/config-template.yaml`, `config-template-swaption.yaml`, `config-template-bond.yaml`): it already maps every contract row onto about 15 `lib_*` primitives you fill from your library.

4. **Map zero where zero is true, and nowhere else.** A swap or bond has no vol exposure under any model: map `IRVega`, `IRVanna`, `IRVolga` and the three vol levels to `'0.0'` (the cube to `'{}'`). A single-curve model maps `IRBasis` to `'0.0'`, and a single-currency one maps `IRXccyDelta` to `'0.0'`. Any other constant (a `'0.0'` discount delta, an empty CRIF) is a placeholder, not a measure.

5. **Fill the gaps.** Print what to paste for everything still missing:

   ```powershell
   python skills/pricebt-risk-measures/scripts/measures.py block configs/assets/<asset>.yaml
   ```

   - **`IRSwap` / `IRSwaption`:** the **mapping skeleton** (`contracts.mapping_skeleton`, the same text the load error ends with): a `functions:` stub per scalar or frame form, a `portfolio_functions:` stub per bucketed form, each with an allowed unit, and the `risk_measures:` lines. Merge each section into the config's own (a second key is a duplicate-key error), then write every `expr`: the stub `contracts.SKELETON_EXPR` (`... TODO`) does not compile on purpose, so the config loads only once each stub computes its measure. Pick the unit you return. Every measure is computable from "reprice one unit trade on a shifted market" (step 3 and [`references/implementing-measures.md`](references/implementing-measures.md)).
   - **`Bond`:** the paste-ready `unsupported_measures:` block. Replace every `TODO` with a specific, checkable reason ("no key-rate bump: the analytics package prices off one yield"). If the config already has `unsupported_measures:`, merge the lines under it. Declare the **base** measure (`IRDelta`), never a preset (`IRDeltaParallel`); one form with `IRDelta: {bucketed: "..."}`.

6. **Audit the config against the contract.** This does not execute your library.

   ```powershell
   python skills/pricebt-risk-measures/scripts/measures.py matrix configs/assets/<asset>.yaml
   ```

   It prints one row per (measure, form): `MAPPED` (function, unit, and `via` a preset key), `DECLARED` (reason; Bond only), `TODO` (a Bond reason still starting with TODO), or `MISSING`. On a swap or swaption a declared row stays `MISSING` (its reason shown) and the declaration itself is a problem. Under the table come the problems `load_asset` would raise (wrong unit, a level that scales with quantity, an unknown function, a strict declaration) and its warnings (a Bond's stale declarations). A hint column says how any library computes a missing row (the zeros, `ExpiryInYears`, a swap's `Annuity` and `IRSpotRate`, the eight strict additions). The command exits 1 while anything is MISSING or TODO, or has a problem; `--strict` also exits 1 on a hinted Bond declaration. In-process: `measures.capability_matrix(path_or_mapping)`.

7. **Verify the numbers.**
   - In your library: reprice each derived measure with a second step size or method.
   - Through pricebt: adapt and run the block in [`references/implementing-measures.md`](references/implementing-measures.md) section 4 (buy = −sell, signs, straddle = payer + receiver, the ATM vega band, parity, a one-day Taylor explain, ladder sums, dead instruments).
   - Run `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>`.

8. **Optional: honour finite-difference parameters.** A function that names `pricebt_bump_size`, `pricebt_finite_difference_method`, `pricebt_local_curve` or `pricebt_scale_factor` receives the requested value, e.g. `IRDelta(aggregation_level='Type', bump_size=5)`. A function that names none of them makes the request raise `NotSupportedError`, so nothing is silently ignored. See [`references/portfolio-and-results.md`](references/portfolio-and-results.md) section 5.

9. **Use the measures on a book.** `Portfolio`, `calc` under `PricingContext` or `HistoricalPricingContext`, result indexing, `aggregate`, `to_frame`, `CloseMarket` and `PnlExplain` are in [`references/portfolio-and-results.md`](references/portfolio-and-results.md), with a runnable tour:

   ```python
   with PricingContext(pricing_date=d):
       book.resolve()                                   # pin ATM strikes and dates first
       res = book.calc((Price, IRDeltaParallel, IRDelta))
   res[Price]["10y"]; res[IRDeltaParallel].aggregate(); res[IRDelta].aggregate(); res[Price].to_frame()
   ```

## Checks (you are done when)

- `measures.py matrix --strict <config>` exits 0: no MISSING, no TODO, no problems, and (Bond) no declaration a hint says every library can avoid.
- The config loads under `warnings.simplefilter("error")`: an `IRSwap`/`IRSwaption` config has no `unsupported_measures:` at all; a Bond has no stale or uncountable declarations.
- The contract identities hold in your library and through pricebt ([`references/implementing-measures.md`](references/implementing-measures.md) section 4).
- No literal constant outside `contracts.ZERO_BY_CONVENTION`; every Bond declaration names what the library lacks.
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
| Clean or per-100 bond `Price` | PV off by accrued, or 100× | dirty PV of the face held |
| Declaring any measure of a swap or swaption | load error: `IRSwap/IRSwaption configs must map every contract measure; unsupported_measures cannot satisfy them` | map it (vega: `'0.0'`, R2-8); derive the rest by bump-and-reprice |
| A `'0.0'` placeholder for a real measure (discount delta, spot rate, swaption vega) | loads, silently zeroes risk; `check_asset.py` `ir_fake_constant` FAILs | compute it; constants only for `ZERO_BY_CONVENTION` |
| `PremiumCents` / `LocalAnnuityInCents` on the signed notional, or scaling with quantity | sign flips with direction; ×quantity | divide by `\|notional_amount\|`; intensive unit |
| `ForwardPrice = Price × DF` | below Price in a positive-rate world | `Price / DF(expiry)` |
| CRIF `Label1` `'10Y'` | not a SIMM tenor | lower case `'10y'`, one of `contracts.SIMM_IR_TENORS` |
| Declaring a preset name (Bond) | a load warning; the contract still counts the base as missing | declare `IRDelta`, not `IRDeltaParallel` |
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
