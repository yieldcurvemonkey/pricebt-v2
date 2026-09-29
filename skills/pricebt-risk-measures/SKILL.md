---
name: pricebt-risk-measures
description: What an asset config must compute for each interest-rate risk measure pricebt serves, and how to get each one from YOUR pricing library. Covers the gs measure catalogue and the per-instrument contracts for IRSwap, IRSwaption and Bond (Price, own-rate IRDelta and ladder, chain-rule IRGammaParallel, normal-vol IRVega and its '<tail>;<expiry>' cube, IRVanna, IRVolga, IRFwdRate, vol levels, per-day Theta, ExpiryInYears, Annuity, Cashflows frames, bond extras). Also covers units, signs, dead instruments, bump-and-reprice recipes, when to declare unsupported_measures honestly, bump_size pass-through, and Portfolio, PortfolioRiskResult, CloseMarket and PnlExplain. measures.py prints a class's contract, a config's capability matrix, and the paste-ready unsupported block. Use when a config fails to load with "measure-contract problem(s)", a request raises UnsupportedMeasureError, you wire a swap, swaption or bond to a library, or you price and risk a portfolio.
---

# IR risk measures: the contract, and how your library meets it

pricebt serves gs_quant's risk measures by name (`IRDelta`, `IRVega`, `Theta`, ...) but computes none of them: every number comes from a function in an asset config. For `IRSwap`, `IRSwaption` and `Bond`, pricebt goes further and **requires** the config to answer the whole IR measure contract when it loads. Each measure and form must be either mapped to a function with an allowed unit, or declared under `unsupported_measures:` with a reason. This skill tells you, in terms of your own library, what each measure must mean, which unit and sign pricebt expects, how to derive a measure your library lacks, when declaring it is the honest answer, and how to check the numbers.

## When to use / not use

- **Use** when you write or extend a config for an `IRSwap`, `IRSwaption` or `Bond` asset, and when you decide per measure whether to map, convert, derive or declare.
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
- A config that loads with **no warnings**: every contract row is mapped, zero-by-convention, or declared with a specific reason.
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
| `ProbabilityOfExercise` (IRSwaption) | decimal | probability of finishing in the money |
| `LightningDV01`, `LightningOAS`, `ParSpread` (Bond) | ccy_per_bp / rate | yield DV01, OAS (Z-spread), par ASW spread |

**Rules across the table.**

- A **dead instrument** (matured, expired OTM, fully paid) returns 0.0 for every sensitivity, finite levels continued from the last live value, and an empty `Cashflows`. One NaN poisons every later `pnl_explain` total.
- The **units of each level must agree** across a book. The toy and shipped configs use bp.
- Names outside the contract are unrestricted.

The full catalogue (124 measures, with presets and classes) is in [`references/measure-catalogue.md`](references/measure-catalogue.md). The authoritative contract text is `src/pricebt/risk/contracts.py`, and the design is `docs/v2/IR_RISK_DESIGN.md` (§00 overrides the rest of that file).

## Procedure

1. **Print the contract for your class.**

   ```powershell
   $env:PYTHONPATH = "src;tests"   # POSIX: PYTHONPATH=src:tests
   python skills/pricebt-risk-measures/scripts/measures.py contract IRSwaption
   ```

2. **Fill the capability matrix** ([`references/capability-matrix.md`](references/capability-matrix.md)). For each row, write down your library's call, its native unit and sign, and a *how*: native, convert, derive (bump), AD, zero, or declare. Also settle the book-wide decisions: level units, the `Price` convention (total return or drop paid flows), the vol model and its normal conversion, the bond yield convention, and settlement after expiry.

3. **Write the functions.** Map native and converted measures directly. Derive the rest with one primitive, "reprice one unit trade on a shifted market and return (PV, own rate)", placed in the config's `code:` block or your helper module:

   ```python
   def _own_rate_greeks(market, trade, h=1.0, vol_bp=0.0):      # _reprice: YOUR library on a shifted market
       (n_dn, r_dn), (n_0, r_0), (n_up, r_up) = (_reprice(market, trade, s, vol_bp) for s in (-h, 0.0, h))
       delta = (n_up - n_dn) / (r_up - r_dn)                                                  # IRDelta scalar
       gamma = (n_up + n_dn - 2 * n_0 - delta * (r_up + r_dn - 2 * r_0)) / ((r_up - r_dn) / 2) ** 2   # IRGammaParallel
       return delta, gamma
   ```

   The per-measure recipes are in [`references/implementing-measures.md`](references/implementing-measures.md) section 2: vanna and volga from vol bumps, the translated-curve theta, bond same-yield theta, key-rate and diagonal gamma ladders, the vol cube, frames, the unit and sign conversion tables, and shape-specific advice for object libraries, platforms, in-house functions and bond packages. The runnable reference is `tests/toylib/irrisk.py`, `tests/toylib/swaption.py` and `tests/toylib/bond.py`. Starting from nothing? Copy the tested template for your class (`skills/pricebt-connect-pricing-library/references/config-template.yaml`, `config-template-swaption.yaml`, `config-template-bond.yaml`): it already maps every contract row onto about 15 `lib_*` primitives you fill from your library.

4. **Map zero where zero is true.** A swap or bond has no vol exposure under any model: map `IRVega`, `IRVanna`, `IRVolga` and the three vol levels to `'0.0'` (the cube to `'{}'`). A single-curve model maps `IRBasis` to `'0.0'`, and a single-currency one maps `IRXccyDelta` to `'0.0'`.

5. **Declare what your library genuinely cannot produce.** Print the paste-ready block for everything still missing, then replace every `TODO` with a specific, checkable reason ("no vol cube: the platform prices off one ATM vol per expiry"):

   ```powershell
   python skills/pricebt-risk-measures/scripts/measures.py block configs/assets/<asset>.yaml
   ```

   If the config already has `unsupported_measures:`, merge the lines under it: a second key is a duplicate-key error. Declare the **base** measure (`IRDelta`), never a preset (`IRDeltaParallel`). Declare a single form with `IRDelta: {bucketed: "..."}`.

6. **Audit the config against the contract.** This does not execute your library.

   ```powershell
   python skills/pricebt-risk-measures/scripts/measures.py matrix configs/assets/<asset>.yaml
   ```

   It prints one row per (measure, form): `MAPPED` (function, unit, and `via` a preset key), `DECLARED` (reason), `TODO` (a reason still starting with TODO), or `MISSING`. Under the table come the problems `load_asset` would raise (wrong unit, a level that scales with quantity, an unknown function) and its warnings (stale declarations). A hint column flags declarations every library can avoid (the zeros, `ExpiryInYears`, a swap's `Annuity` and `IRSpotRate`). The command exits 1 while anything is MISSING or TODO, or has a problem; `--strict` also exits 1 on a hinted declaration. In-process: `measures.capability_matrix(path_or_mapping)`.

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

- `measures.py matrix --strict <config>` exits 0: no MISSING, no TODO, no problems, and no declaration a hint says every library can avoid.
- The config loads under `warnings.simplefilter("error")`: no stale or uncountable declarations.
- The contract identities hold in your library and through pricebt ([`references/implementing-measures.md`](references/implementing-measures.md) section 4).
- Every declaration names what the library lacks, and no zero-exposure measure is declared.
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
| Declaring a swap's vega | a mixed book with vol attribution raises | map `'0.0'` (R2-8) |
| Declaring a preset name | a load warning; the contract still counts the base as missing | declare `IRDelta`, not `IRDeltaParallel` |
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
