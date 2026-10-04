---
name: pricebt-pnl-attribution
description: Decompose a pricebt backtest's P&L into greeks times market moves (delta, gamma, vega, vanna, volga, theta) for swaps, swaptions and bonds with gs-style PnlDefinition/PnlAttribute, read BackTest.pnl_explain_table (actual, coupons, financing, economic, explained, residual), and diagnose a large residual; also instrument-level PnlExplain(CloseMarket) between two dates. Tells you, per attribute, what your own pricing library must return (unit, sign, bump recipe) and how to verify it. Use when asked to "explain the P&L", "attribute P&L to greeks", "why does this make money", "P&L explain/attribution/residual", or after wiring a library's greeks, to prove they explain the P&L.
---

# P&L attribution: greeks × market moves

A backtest's P&L is only understood once it is split into what the book was paid for: rate moves (delta, gamma), vol moves (vega, vanna, volga) and time (theta), plus known cash (coupons, and repo interest on a financed bond) and what nothing explains (the residual).

pricebt ports gs's machinery (`PnlAttribute`, `PnlDefinition`, `BackTest.pnl_explain()`) and adds IR definitions and a per-step table (`BackTest.pnl_explain_table()`). The numbers come from **your** pricing library, through the asset configs. This skill tells you:

- what each attribute multiplies;
- what your library must return for each measure (unit, sign, recipe);
- how to run and grade the attribution;
- how to find the cause of a residual.

## When to use / not use

- **Use** after a swaption, bond or swap backtest runs, before the tearsheet: attribute, grade, then report.
- **Use** right after wiring a library's greeks. A small residual on a daily run is the strongest end-to-end test that delta, gamma, vega and theta are right in unit, sign and definition, for every term that is material on the book: a greek whose P&L is tiny here (gamma on a short run) can be half its size and still PASS. `check_asset.py` (verify-asset-config) tests each greek on its own.
- **Use** `PnlExplain(CloseMarket(...))` for a what-if between two closes, or to confirm one suspicious step ([references/instrument-level.md](references/instrument-level.md)).
- **Do not use** as a substitute for [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md). Attribution explains P&L; it does not prove the bookkeeping.
- **Do not use** on an FX book with these IR definitions. Use gs's `fx_pnl_definition()` (same module) or a custom `PnlDefinition`; the table, stats and spot check work for any definition.

## Inputs

- A `PricebtSession` holding the asset configs of every instrument the book will hold.
- The strategy, and `GenericEngine().run_backtest` arguments.
- For coupon-paying instruments whose `Price` drops paid flows: `Cashflows` must be mapped, and passed in `risks=`. A financed position (its asset maps `FinancingToDate`: every `Bond`) does not need it: the engine records its coupons itself (DEV-E22, below).

## Outputs

- **`bt.pnl_explain_table()`**: per step, `actual_pnl`, `cashflow_pnl`, `financing_pnl`, `economic_pnl`, one column per attribute, `explained_pnl`, `residual_pnl`.
- **`bt.pnl_explain()`**: gs's `{attribute: {date: cumulative}}`.
- **`attribution.explain_stats(table)`**: component totals, r2, the residual variance share, the residual ratios, the graded `unexplained` share, worst date, the residual's correlation with each attribute, and the `signatures` (attributes the residual names).
- **`attribution.grade(stats)`**: PASS, WARN, FAIL or INFO; **`attribution.grade_reason(stats)`** says why in one line.
- **Downstream:** a "P&L attribution" section in the tearsheet, and an "attribution residual" row in the spot checks.

## What pricebt multiplies

Per step t−1 → t, per instrument held at t−1: `k · R(t−1) · Δm` (first order), `½ · k · R(t−1) · Δm²` (second order) or `k · R(t−1) · Δm₁ · Δm₂` (cross). A risk of exactly 0 is skipped; there is no NaN guard. Full semantics are in [references/definitions.md](references/definitions.md).

| attribute | risk R at t−1 | level m | k (from your declared units) |
|---|---|---|---|
| `PNL_delta` | `IRDeltaParallel` = the `IRDelta` scalar | `IRFwdRate` | f_r |
| `PNL_gamma` | `IRGammaParallel` (second order) | `IRFwdRate` | f_r² |
| `VegaPnL` | `IRVegaParallel` = the `IRVega` scalar | `IRAnnualImpliedVol` | f_v |
| `PNL_vanna` | `IRVanna(aggregation_level=Type)` | `IRFwdRate` × `IRAnnualImpliedVol` (cross) | f_r·f_v |
| `PNL_volga` | `IRVolga(aggregation_level=Type)` (second order) | `IRAnnualImpliedVol` | f_v² |
| `PNL_theta` | `Theta` (ccy **per calendar day**) | `ExpiryInYears` | −365 (so R × days elapsed) |

- **f** = 1 for a level declared in `bp`, 100 in `pct`, 1e4 in `decimal`. `attribution.definition_for` reads them from the configs' `unit:`.
- **Unit check.** A level read in a different unit raises `ValueError` (DEV-E21). Theta has no unit check.
- **After expiry.** `ExpiryInYears` stays 0 from expiry on, so `PNL_theta` is 0 then. An exercised (physically settled) swaption still carries like its underlying swap (non-zero `Theta`, R2-7): that carry lands in the residual.

**The fixed columns.** `economic_pnl = actual_pnl + cashflow_pnl + financing_pnl`; `explained_pnl = Σ attributes + financing_pnl`; `residual_pnl = economic − explained`.

- `cashflow_pnl` holds the coupons. For a financed position they come from the engine's record (`bt.holding_cash`); otherwise from the `Cashflows` frame held at t−1 (paid in (t−1, t]), and 0.0 when `Cashflows` is not in `risks=`.
- `financing_pnl` is the change of a financed position's `FinancingToDate` that the engine booked as cash on t (≤ 0 for a long: the repo interest paid). It is 0.0 for every other position, so swap and swaption books are unchanged (gs parity: no holding cash).
- Financing is known cash, not a market move, so it counts as explained: it never reaches the residual. The contract keeps it out of `Theta` ("Financing is not in Theta"), so `PNL_theta` and `financing_pnl` never overlap.
- For a book of financed positions with no transaction costs, `economic_pnl` sums to the change in `result_summary`'s `Total`.

## What your library must return, per measure

Say it in your library's terms before you write the YAML. "Own rate" r = swap par rate, swaption forward swap rate, bond yield to maturity; h = your library's ±1bp bump. Details, library shapes and verification probes: [references/definitions.md](references/definitions.md) §3. The full recipe for every contract measure, with a capability-matrix worksheet, is [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md).

| Measure | Contract (holder-signed, one unit trade) | Recipe when your library lacks it |
|---|---|---|
| `IRFwdRate` | r in a declared unit; finite on every held date, **including the exit date and after death** | solve for par (swap), price → yield (bond) |
| `IRDelta` scalar | **total** own-rate derivative `[PV(+h) − PV(−h)] / [r(+h) − r(−h)]`, vol held; payer > 0, long bond < 0 | three valuations on your parallel bump. Not an annuity pv01 (at-the-money exact only) |
| `IRGammaParallel` | `∂²PV/∂r²` per bp² | chain rule: `[n₊ + n₋ − 2n₀ − Δ·(r₊ + r₋ − 2r₀)] / ((r₊ − r₋)/2)²`. Never `d(pv01)/dr` (half gamma) |
| `IRVega` scalar | per +1bp of **normal** vol; swaps/bonds 0.0 | lognormal library: Bachelier-implied normal vol as the level, bumped through Bachelier. Never rescale a Black vega per 1% |
| `IRVanna`, `IRVolga` | `d(Δ)/dσ` per bp·bp; `∂²PV/∂σ²` per bp²; swaps/bonds 0.0 | the delta at σ ± 1bp; `PV(σ+h) + PV(σ−h) − 2PV(σ)` |
| `IRAnnualImpliedVol` | normal vol at the strike; swaps/bonds 0.0 | Bachelier-implied vol of your price |
| `Theta` | one calendar day, total return, r and σ held: `Price(t+1d) + cash paid in (t, t+1d] − Price(t)` | a **translated** curve `DF(x)/DF(t+1d)` (never rolled), same vol. Never per year. **Bond:** the same yield over the step to the next business day nb, spread per calendar day: `[Price(nb) + flows dropped in (t, nb] − Price(t)] / (nb − t).days`; no financing |
| `ExpiryInYears` | `max(expiry or final − t, 0).days / 365` | date arithmetic in the config |
| `Cashflows` | frame of the flows `Price` will drop, `payment_date > t`; empty for a total-return `Price`. **Bond:** `payment_date` is the trade date on which `Price` drops the flow (T+1: the business day before a business-day coupon date) | from the schedule |
| `FinancingToDate` (Bond) | cumulative repo interest since the trade date's settlement, holder-signed (a long ≤ 0), 0 on the trade date | principal `(1 − RepoHaircut) · Price(trade date)` pinned at resolve, simple interest at each calendar day's `RepoRate` |

**Cannot compute one?** Every class with a contract (`IRSwap`, `IRSwaption`, `Bond`) maps every contract measure or does not load (`docs/v2/IR_STRICT_CONTRACT.md`, `docs/v2/BOND_DESIGN.md`): derive it from PV, a curve or yield shift and a date (the recipes above). A literal `0.0` is honest only for `contracts.ZERO_BY_CONVENTION` (for a bond: the vol measures, `IRBasis`, `IRXccyDelta`). No contract class can declare a measure under `unsupported_measures:`, so every attribute of every definition is priceable on these books.

The runnable reference is the closed-form toy library: `tests/toylib/swaption.py` and `tests/toylib/bond.py`, wired in `tests/assets/toy_usd_swaption.yaml` and `tests/assets/toy_usd_bond.yaml`.

## Procedure

1. **Check the book can be attributed**, from the configs alone. This prints the definition the book gets, or every gap: an unmapped measure (on a `ConfigInstrument`, which has no contract, also a declared-unsupported one), or mixed level units.

   ```powershell
   $env:PYTHONPATH = "src;tests"
   python skills/pricebt-pnl-attribution/scripts/attribution.py --definition configs/assets/<a>.yaml configs/assets/<b>.yaml
   ```

   Fix a gap in the config (map the measure; for a bond's vol rows, map the 0.0 convention), or drop the attribute (`--kind bond`, or flags in-process). Try it on the toys first: `tests/assets/toy_usd_swaption.yaml`.

2. **Choose the definition.** `attribution.definition_for(session)` picks one:

   | Book | Definition |
   |---|---|
   | any swaption (with swaps or bonds) | all six: `swaption_pnl_definition(rate_unit, vol_unit)` |
   | bonds, swaps, or both | delta, gamma, theta: `bond_pnl_definition(rate_unit)` = `ir_pnl_definition(vega=False, vanna=False, volga=False)` |
   | swaps on configs that map the swap P&L recipe's `IRTheta` and `YearFraction` | `swap_pnl_definition` ([`swap_pnl.py`](../pricebt-strategy-recipes/scripts/swap_pnl.py), merged from the swap P&L-explain branch; per-year `IRTheta × YearFraction`) |
   | anything else | `ir_pnl_definition(..., delta=, gamma=, vega=, vanna=, volga=, theta=)`, or your own `PnlDefinition` |

   Keyword flags override the kind: `definition_for(session, volga=False)`. Pass `assets=[names]` when the session holds configs the book never trades.

3. **Run the backtest with the definition.**

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-pnl-attribution/scripts")
   import attribution
   from pricebt.risk import Cashflows
   definition = attribution.definition_for(session)
   bt = GenericEngine().run_backtest(strategy, start=start, end=end, frequency="1b",
                                     risks=[Cashflows], pnl_explain=definition)   # Cashflows: coupon-dropping Price that is not financed
   ```

   **From a strategy spec** (the workflow skills): the spec's `pnl_explain:` block (`enabled`, `gamma`, `carry`, `cash`) wires `swap_pnl_definition` into the run, for an **IRSwap primary only** (`recipes.build`; any other primary is skipped with a note). For a swaption or bond book, or for this skill's six-attribute definitions, add the definition to the recipe's run arguments yourself:

   ```python
   built = recipes.build(spec)
   built.run_kwargs["pnl_explain"] = attribution.definition_for(session)
   bt = GenericEngine().run_backtest(built.strategy, **built.run_kwargs)
   ```

   Use a **daily** grid. Every attribute is a Taylor term per step, so a weekly or monthly grid lets the residual grow with the step.

4. **Read the table and grade it.**

   ```python
   table, cumulative = attribution.attribution_frames(bt)   # cumulative: attributes + residual + economic
   stats = attribution.explain_stats(table)
   print(attribution.grade(stats), attribution.grade_reason(stats))
   print(stats["totals"], stats["worst_date"], stats["signatures"])
   ```

   To see healthy output first, run the toy demo: `python skills/pricebt-pnl-attribution/scripts/attribution.py --demo swaption` (or `--demo bond`).

5. **Diagnose anything not PASS** with [references/diagnosing-residuals.md](references/diagnosing-residuals.md). Two numbers name most causes, and `stats["signatures"]` holds them already:
   - the attribute the residual co-moves with (`stats["residual_corr"]`, |corr| ≥ 0.9);
   - its implied scale `1 + residual total / attribute total`, the factor that would absorb the residual: −1 a sign error, 2 half gamma, about 0 a per-year theta or a ×100 unit, 20 or more a level in pct or decimal declared bp.

   A residual on one date is an event (coupon, exit, data); one proportional to the step's day count is a time term.

6. **Confirm one step by full revaluation** when the cause is not obvious. Under `PricingContext(pricing_date=t−1)`, calc `PnlExplain(CloseMarket(date=t))` on the instruments held at t−1, and compare its rows with that step's attributes ([references/instrument-level.md](references/instrument-level.md)).

7. **Report.**
   - `tearsheet.build_tearsheet(bt, ..., attribution=table)` adds the section (totals, graded unexplained share and its reason, stacked chart).
   - `spot_check.run_spot_checks(bt)` adds the "attribution residual" row.
   - State in the report every attribute you dropped, and why.

## Checks

- `stats["finite"]` is True: no NaN anywhere. A NaN poisons every later cumulative value.
- `grade(stats)` is PASS: the `unexplained` share, the worst of the residual variance share, 1 − r2 and `|Σ residual| / Σ|economic|`, is ≤ 5%. The variance share alone misses a steady bias: a sign-flipped `Theta` leaves it near 1.7%. Or it is WARN with a named cause from the taxonomy, written into the report. FAIL (above 25%, a NaN, or a residual signature on a material residual) blocks the report. A book with almost no P&L (an option expiring worthless) can FAIL on cents: read the totals before you chase it.
- The component signs make sense for the book: a long option has positive gamma P&L and negative theta. A long bond has positive theta (it accrues at its yield) and negative `financing_pnl` (it pays repo).
- On a book bought at PV with no costs, `stats["totals"]["economic_pnl"]` equals `Total(end) − Total(start)` of `result_summary` when the book has no coupons, or when every coupon payer is financed (the engine books its coupons and repo interest as cash). A swap that pays coupons breaks the equality by its coupons (gs parity: the engine does not book them).
- On a coupon step, `cashflow_pnl` equals face × coupon / frequency, and the residual stays small.
- For a financed bond, `financing_pnl` over one step equals `−(1 − haircut) · Price(trade date) · RepoRate · days / basis`, with days counted between settlement dates (ACT/360 for USD).

## Pitfalls

- **An annuity pv01 as the `IRDelta` scalar** is exact only at the money. Off-market, the residual is `N·(F−K)·ΔA`. The shipped swap configs are at-the-money exact.
- **Half gamma**: `d(pv01)/dr`, or a finite-difference gamma without the chain-rule term.
- **Theta per year, or on a rolled curve.** Per year inflates `PNL_theta` 365-fold. The loader and the DEV-E21 unit check cannot see it; `check_asset.py`'s `ir_theta` ("looks per year") and this grade (FAIL) catch it. A rolled curve double counts the roll-down with delta.
- **Coupons.** A `Price` that drops coupons needs `Cashflows` in `risks=`, or every coupon date shows a −coupon residual. The engine books coupons only for a financed position (an asset that maps `FinancingToDate`, DEV-E22); swaps and swaptions keep gs parity (no coupon cash).
- **Financing double counted.** A financed bond's repo interest is already in `financing_pnl` and `Total`. A `cash_accrual` model on the same run charges the funding loan again; the engine warns once (`UserWarning`, "the funding is counted twice"). Drop the cash accrual, or model the haircut capital's funding inside `FinancingToDate`.
- **Financing in Theta.** A bond `Theta` that nets repo interest double counts it with `financing_pnl` (the residual then mirrors `financing_pnl`). The contract's `Theta` is at constant yield, with no financing.
- **Mixed units, or a bp definition on a pct config.** `definition_for` refuses the first; the unit check raises on the second. A level *declared* bp that returns pct or decimals is invisible to both. [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md) catches it: `swap_par_rate_atm` and `swaption_fwd_unit` FAIL it; `bond_yield_unit` and `swaption_vol_unit` WARN on a percent and FAIL on a decimal. Here the residual's signature names the attribute (implied scale about 100).
- **Near expiry**, and on long steps (weekends, weekly grids), time cross terms (charm, veta) and third-order terms grow. That residual is real, not a bug to tune away.
- **A swaption held past its expiry.** An exercised leg is the underlying swap, but `PNL_theta` stops at expiry (`ExpiryInYears` floors at 0), so the swap's carry goes to the residual (about +11 a day on a 1mm 1m ITM payer, toy). Exit at expiry (`AddTradeAction(..., 'expiration_date')`), or book the exercised swap as its own trade.
- **Hedged books across instrument types**: own-rate deltas are not strictly additive (IR_RISK_DESIGN R2-2). The attribution is exact per leg; the hedge is approximate.
- **Do not edit `src/pricebt` to shrink a residual.** Fix the config's measure definitions, or report the term.

## Related skills

- [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md): the measure contract and how to compute each measure with your library; start there if a config does not load.
- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md) and [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): wire the measures this skill multiplies (the measure contract: `src/pricebt/risk/contracts.py`, `docs/v2/ASSET_CONFIG_GUIDE.md`).
- [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md): prove units, signs and bands before attributing.
- [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md): its `check_pnl_attribution_generic` grades the residual.
- [`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md): `attribution=` renders the section.
- [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md): section E of its checklist attacks the attribution.
- [`pricebt-port-gs-notebook`](../pricebt-port-gs-notebook/SKILL.md): port gs code that uses `pnl_explain=` or `PnlExplain` (gs notebook 030007).
