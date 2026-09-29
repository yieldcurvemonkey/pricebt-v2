---
name: pricebt-verify-asset-config
description: Automated "zero to confidence" checker for a new or changed pricebt asset config. It loads the config, evaluates market, resolve, every function and risk measure, and runs a smoke backtest. For IRSwap, IRSwaption and Bond (or a ConfigInstrument with --pack) it also checks the measure contract (mapped, declared, TODO or stale) and contract semantics from evaluated values - a one-day Taylor P&L check, Theta against implied carry (per-year trap), the half-gamma ratio, ExpiryInYears decay, dead-instrument levels, ladders, vega-cube keys, Cashflows sign and coupon drop, bump_size pass-through - and runs the swap (plus annuity sign), swaption (buy/sell fold, straddle, strike pinning, parity, forward and vol units, greek signs, expiry) or bond pack (fold, dv01/convexity signs, LightningDV01, yield unit, coupon bound). Prints a PASS/WARN/FAIL/INFO table. Use after writing or editing any asset config, before strategy or P&L work, or when a config's numbers look wrong.
---

# Verify an asset config

An asset config is trusted code that turns your pricing library into pricebt numbers. One wrong sign, unit or unpinned tenor corrupts every backtest built on it, silently. `check_asset.py` registers the config in its own `PricebtSession`, probes it the way the engine will, and reports one row per check. For an interest-rate asset it also checks the **measure contract** (`docs/v2/IR_RISK_DESIGN.md` §2, §00) and the relations the contract implies between measures, using only values your config produces, so it works with any library. It proves the config is *internally consistent and wired the way pricebt expects*. It cannot prove the library's numbers are *right* (see [What the checker cannot prove](#what-the-checker-cannot-prove)).

## When to use / not use

- **Use** after writing a new asset config, after any edit to one, after upgrading the pricing library, and before any strategy or P&L-attribution work on that asset.
- **Use** when a backtest number or a P&L explain looks wrong: rerun the checker first, so you know whether the config is to blame.
- **Use** `--pack IRSwaption` (or `IRSwap`, `Bond`) on a `ConfigInstrument` asset that is really a swaption, swap or bond. The loader does not enforce a contract on `ConfigInstrument`; the checker does.
- **Do not** use it as the only validation of a config going into production research. Do the independent validation in [`references/independent-validation.md`](references/independent-validation.md) once per asset.
- **Do not** use it to learn how to compute a measure with your library. That is [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md).
- **Do not** use it to review a strategy. That is [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md).

## Inputs

- The asset config YAML (see `docs/v2/ASSET_CONFIG_GUIDE.md` for the schema).
- The directory holding the pricing library or helper modules, if it is not already importable (`--sys-path`).
- Two sample business dates the library has data for (`--date`, twice). Default: about 120 days ago and one month later. The IR rows also price the next business day after `d2`, the 20 business days after `d1`, the first coupon date after `d1`, and the final/expiry date plus a week. A row whose dates have no market SKIPs.
- Optionally: `--pack`, an FX config (`--fx`), instrument kwargs as JSON (`--kwargs`), and a smoke-backtest window (`--start`, `--end`).

## Outputs

- A markdown table on stdout (`| check | status | detail |`), then a `PASS=.. WARN=.. FAIL=.. SKIP=.. INFO=..` line.
- **PASS** means the relation held. **WARN** means explain it or fix it. **FAIL** means it is wrong. **SKIP** means not applicable or inconclusive; it is never "passed". **INFO** is a reported number, never a verdict.
- Exit code **1** if any row is FAIL, else 0. With `--json OUT`: the rows as a JSON list of `{name, status, detail}`.
- In-process: `run_checks(...)` returns a list of `CheckResult(name, status, detail)`.

## Procedure

1. **Run the checker from the repository root.**

   ```powershell
   $env:PYTHONPATH = "src;tests"
   python skills/pricebt-verify-asset-config/scripts/check_asset.py configs/assets/<your_asset>.yaml --sys-path <dir with your library or helper modules> --date 2024-01-03 --date 2024-02-05
   ```

   On POSIX use `PYTHONPATH=src:tests`. Try it first on the toys, which pass: `tests/assets/toy_usd_irs.yaml` (a declaration-only swap), and `tests/assets/toy_usd_irs_full.yaml`, `tests/assets/toy_usd_swaption.yaml` and `tests/assets/toy_usd_bond.yaml` (the full contract on the closed-form libraries in `tests/toylib/`).

   | option | effect |
   |---|---|
   | `--pack auto` (default), `none`, `IRSwap`, `IRSwaption`, `Bond` | contract and pack: auto uses the config's `instrument:`; name one for a `ConfigInstrument` |
   | `--kwargs '{"termination_date": "5y"}'` | probe a different trade than the config's `defaults` |
   | `--fx <fx config yaml>` | also check the FX round trip for a non-USD asset |
   | `--start 2024-01-02 --end 2024-06-28` | smoke-backtest window (must have market data) |
   | `--no-backtest` | skip the smoke backtest (fast iteration on one function) |
   | `--json OUT` | machine-readable rows |

   In-process (for example from a notebook):

   ```python
   import sys; sys.path.insert(0, "skills/pricebt-verify-asset-config/scripts")
   import check_asset
   from datetime import date
   rows = check_asset.run_checks("tests/assets/toy_usd_swaption.yaml", dates=[date(2024, 1, 3), date(2024, 2, 5)], pack="auto")
   print(check_asset.to_markdown(rows))
   ```

2. **Read the table top to bottom.** Rows run in this order: loading, market, resolve, the generic rows, the shape rows, `contract[...]`, `ir_*`, `fd_params`, the pack (`swap_*`, `swaption_*` or `bond_*`), and `performance`. If `config_loads`, `imports_execute` or `market_available` FAILs, the rest is a single `remaining | SKIP` row: fix that first.

3. **Fix every FAIL** with the tables below, then rerun. **Explain every WARN** in one line, in your notes or the config's `description:`; a WARN you cannot explain is a FAIL. A `contract[M]` WARN for a declared measure is explained by its declared reason, so make that reason specific.

4. **For an IR asset, clear the P&L-critical rows before any P&L work**: `contract[...]` for `IRDelta`, `IRGammaParallel`, `IRFwdRate`, `Theta`, `ExpiryInYears` and `Cashflows` (plus the vol measures for a swaption), then `ir_taylor`, `ir_theta` and `ir_gamma_ratio`. [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md) relies on exactly these.

5. **Do the independent validation once per asset**: [`references/independent-validation.md`](references/independent-validation.md), with the probes in [`references/known-answer-probes.md`](references/known-answer-probes.md).

## How to read the IR rows

The contract and IR rows run when a pack applies: auto for `IRSwap`, `IRSwaption` and `Bond`, or `--pack` for a `ConfigInstrument`. Every row is catalogued, with the mistake it catches, in the docstring of `skills/pricebt-verify-asset-config/scripts/check_asset_ir.py`.

- **Contract rows** list, per contract measure, what serves each form. Mapped is PASS. Declared under `unsupported_measures:` is WARN, never FAIL: an honest declaration is the contract working, and the P&L code raises `UnsupportedMeasureError` loudly on a declared measure it needs. The row says when a declaration is P&L-critical. A reason starting with `TODO` is WARN "declaration reason is a TODO". This is the pasted loader block, never edited. Neither mapped nor declared is FAIL. The loader already refuses that for the three classes, so you only see it with `--pack` on a `ConfigInstrument`. A stale declaration (mapped *and* declared) is a `contract_declarations` WARN; the mapping wins.
- **`ir_taylor`** compares the Price change plus cash paid over one business day after `d2` with `Δ·dr + ½Γ·dr² + ν·dσ + vanna·dr·dσ + ½volga·dσ² + Θ·days`. Greeks are at the start, dr is in bp of `IRFwdRate`, and dσ is in bp of `IRAnnualImpliedVol`. The residual is a share of the explained size: PASS ≤ 5%, WARN ≤ 20%, FAIL above. Declared measures drop out and are listed. A wrong unit, sign or scale in any greek shows here first.
- **`ir_theta`** compares `Theta` with the implied one-day carry (the step's Price change with every other term removed). A ratio of about 365 is FAIL "looks per year". PASS is 0.8 to 1.25. Both near zero (an at-the-money swap on a translated curve) is an inconclusive PASS. Because the implied carry is what every other term leaves over, a WARN here with nothing else flagged can also be an `IRDelta` that is not the total own-rate derivative (an annuity pv01 off-market), or a wrong gamma or vega: clear `ir_taylor`, `ir_gamma_ratio` and `swap_annuity_sign` before you change `Theta`.
- **`ir_gamma_ratio`** compares `IRGammaParallel` with `d(IRDelta)/dr`, fitted over the 20 business-day steps after `d1` as `ΔIRDelta = slope·dr + drift·days` (vanna·dσ removed), so a delta that drifts with time (a bond's charm) does not bias the slope. About 1 is PASS (0.7 to 1.4). About 0.5 is FAIL: the half-gamma trap. About 2 is WARN: either `IRDelta` is a fixed-annuity pv01 (at-the-money-exact, R2-1) with a true gamma, or gamma is doubled. When `|IRDelta|` equals `|Annuity| × 1e-4` off-market, the reference is `2 × d(IRDelta)/dr` and 1 is again PASS. On an `IRSwap` with **no** `Annuity` mapped (or no off-market `d2`), a PASS becomes WARN "unverifiable": a fixed-annuity `IRDelta` with a half gamma also reads about 1, and only `Annuity` tells them apart. Rates never moving 0.5bp in a day is SKIP. The fit costs 21 evaluations of `IRDelta`, `IRGammaParallel` and `IRFwdRate`.
- **INFO rows** (`ir_ladder_sum[...]`, `fd_params[...]`) report numbers. An own-rate scalar and a curve ladder may differ by dr/ds (R2-2), so a gap under 5% is not a finding.

## Symptom, cause, fix

Generic rows (every asset):

| check | symptom | likely cause | fix |
|---|---|---|---|
| `config_loads` | FAIL `ConfigError [...]` | unknown key, bad `unit:`, missing function, no `Price`, syntax error, or "N measure-contract problem(s)" | follow the message; for the contract, map each measure or paste the printed `unsupported_measures:` block and write honest reasons |
| `match` | WARN "finds no asset" | `match:` rule the config's own `defaults`/kwargs do not satisfy | fix `match:` or `defaults`; users otherwise need `pricebt_asset=` |
| `imports_execute` | FAIL `key 'imports'` / `'code'` | module not importable, a login call raising | add `--sys-path`; check the import in a bare Python shell |
| `market_available` | FAIL "returned None" or `key 'market'` | wrong curve id, wrong date type, CSA not handled, no data | print the market expression's inputs; pick dates the library has |
| `market_weekend` | FAIL "raised on Saturday" / WARN "returned an object" | library raises on a non-trading day / serves Friday's data | catch and return `None` in a `code:` helper / usually fine; not stale data on a real holiday |
| `resolve_pins_terms` | FAIL "still relative" / WARN "identical dates" | `resolve:` returns `'10y'` or `'ATM'` / ignores `pricebt_date` | convert every relative term to a date or number / use the injected `market`/`pricebt_date` |
| `functions_finite[f]` | FAIL exception / WARN NaN | wrong attribute or argument order / undefined on a live trade | fix the expression; NaN is for dead trades only |
| `quantity_scaling[M]` | FAIL | `scale_with_quantity:` contradicts the unit | remove the override, or fix the unit |
| `quantity_scaling[M bucketed]` | FAIL | a ladder in ccy per bp that ignores `weights` | return per-unit buckets times `weights` |
| `quantity_scaling[Cashflows frame]` | FAIL | a level (`rate`, `spread`, `discount_factor`, a date) in `scale_columns`, or `notional` missing | `scale_columns` = the amount columns: `payment_amount` (and `notional`) |
| `notional_linearity[f]` | FAIL "it is a rate" / "an amount" | wrong `unit:` | `bp`/`pct`/`decimal` for rates, `ccy`/`ccy_per_bp` for amounts |
| `risk_measures[M]` | FAIL / WARN "not a pricebt.risk measure" | mapped function fails in one form / typo | fix the function or `{scalar, bucketed}` mapping / use the gs name |
| `measure_series` | WARN "constant" | the tracked function (the one mapped to `IRFwdRate`, else the first non-literal rate) ignores `pricebt_date` | pass `pricebt_date` to the market loader; literal expressions such as a `'0.0'` vol are never tracked |
| `smoke_backtest` | FAIL non-finite / identity | NaN on a grid date / engine could not run | price that date by hand / rerun with `--no-backtest` to isolate |
| `fx_round_trip` | FAIL | reverse pair returns the same quote | return the reciprocal |
| `performance` | WARN SLOW | curve rebuilt per call | build it once in `market:`, reuse it |

Contract and IR semantics rows (IRSwap, IRSwaption, Bond, or `--pack`):

| check | symptom | likely cause | fix in your library's terms |
|---|---|---|---|
| `contract[M]` | WARN declared | library cannot compute M | keep it if true and specific; for a swap or bond, prefer mapping vol measures to `0.0` (R2-8) so mixed books work |
| `contract[M]` | WARN "reason is a TODO" | pasted block never edited | write why *your* library cannot compute it, or map it |
| `contract[M]` | FAIL (only with `--pack`) | neither mapped nor declared, or wrong unit/shape | map it with the contract unit, or declare it |
| `contract_declarations` | WARN stale | the measure is mapped and declared | delete the declaration (the mapping already wins) |
| `ir_expiry_in_years` | FAIL | business-day or ACT/365.25 year fraction, or a frozen date | `max(final − pricebt_date, 0).days / 365` with the final or expiry date from `resolved` |
| `ir_taylor` | WARN/FAIL | one greek off by a unit (x100, x1e4), sign, or per-day/per-year | print the listed terms; the one that dwarfs the Price change is wrong |
| `ir_theta` | FAIL "looks per year" | the library's theta is per year | divide by 365, or reprice at t+1 calendar day on the curve translated `DF(x)/DF(t+1d)` with the own rate and vol held fixed |
| `ir_theta` | WARN ratio | curve rolled instead of translated, business-day theta, cash term missing; **or** `IRDelta` not the total own-rate derivative, or gamma/vega wrong (the carry is implied from the other terms) | same recipe; add flows paid in (t, t+1d] when `Price` drops them; clear `ir_taylor`, `ir_gamma_ratio` and `swap_annuity_sign` first |
| `ir_gamma_ratio` | FAIL ~0.5 | gamma is `d(pv01)/dr` of an annuity pv01, a Taylor coefficient Γ/2, or a missing chain-rule term | from ±h bumps of your curve primitive: `Γ = [n₊+n₋−2n₀ − ((n₊−n₋)/(r₊−r₋))·(r₊+r₋−2r₀)] / ((r₊−r₋)/2)²`, r in bp |
| `ir_gamma_ratio` | WARN ~2 | fixed-annuity `IRDelta` with a true gamma (at-the-money exact), or a doubled gamma | map `Annuity` so the checker can tell, or make `IRDelta` the total `[PV(+h)−PV(−h)]/[r(+h)−r(−h)]` |
| `ir_gamma_ratio` | WARN "unverifiable" (`IRSwap`) | `Annuity` not mapped, so a fixed-annuity delta with a half gamma cannot be told from a true pair | map `Annuity` (`−[PV(K+1bp) − PV(K−1bp)] / 2e-4` needs only your PV) |
| `swap_annuity_sign` | FAIL (`IRSwap`) | `Annuity` not payer-positive: a QuantLib-style `fixedLegBPS` (the fixed leg's own sign, negative for a payer) mapped as is | `Annuity = −1e4 × fixedLegBPS`: pay-fixed > 0, receive-fixed < 0, the sign of `IRDelta` |
| `ir_dead_levels` | FAIL | a function raises or returns NaN once the trade has matured or expired | return the last live level and 0.0 sensitivities; never NaN (R2-7) |
| `ir_ladder_sum[M]` | WARN | missing pillars, or a ladder in another unit or sign | same unit and sign as the scalar; include every pillar |
| `ir_vega_cube_keys` | FAIL / WARN | keys not `'<tail>;<expiry>'` / reversed, or `mkt_type` not `IR VOL` | build `f"{tail};{expiry}"` (e.g. `'10Y;1Y'`) and label `mkt_type: IR VOL` |
| `ir_cashflows` | FAIL | a paid flow still listed, or amounts not holder-signed | list flows with `payment_date > pricebt_date`, signed by the folded direction |
| `ir_cashflow_drop` | FAIL | `Price` does not drop the flow `Cashflows` lists, or drops another amount | make `Price` and `Cashflows` agree (R2-6); a total-return `Price` returns an empty frame |
| `fd_params` | FAIL "silently ignored" | should not happen (pricebt raises); report it | - |
| `fd_params[M]` | WARN identical | expression names `pricebt_bump_size` but never passes it to the library bump | pass it through, or stop naming it (then requests raise `NotSupportedError`) |

Swaption pack (`IRSwaption`) and bond pack (`Bond`):

| check | symptom | likely cause | fix |
|---|---|---|---|
| `swaption_buy_sell_fold` | FAIL | `resolve` ignores `buy_sell` (pricebt never reads it) | fold `buy_sell` × sign(`notional_amount`) into one signed notional |
| `swaption_straddle` | FAIL | Straddle resolves but prices as one leg, or fails to price | price payer + receiver, or raise a clear error **in resolve** |
| `swaption_strike_pinned` | FAIL | `'ATM'`/`'A-50'`/`'ATM+25'` left as strings, strike in percent or bp, offsets not bp | parse the grammar in `resolve` and pin a **decimal** strike |
| `swaption_parity` | FAIL/WARN | strike not decimal, `Annuity` not holder-signed N·A, `IRFwdRate` not the underlying forward | fix the unit of whichever side is off; payer − receiver = N·A·(F − K) |
| `swaption_fwd_unit` | FAIL | an ATM clone's `IRFwdRate` on its trade date is not its resolved strike × 1e4 bp: the forward in pct or decimal under a `bp` declaration | convert in the function, or declare the unit you return |
| `swaption_vol_unit` | WARN under 5bp / FAIL under 0.2bp or above 1000bp | a normal vol in pct (WARN) or decimal (FAIL) under `bp`, or a lognormal vol | return the normal vol in the declared unit; `VegaPnL` is ~100× too small otherwise |
| `swaption_vega_sign`, `_delta_sign`, `_gamma_sign` | FAIL | vega per -1bp, receiver-positive delta, gamma sign | holder-signed per +1bp: bought vega > 0, payer delta > 0, receiver delta < 0, bought gamma > 0 |
| `swaption_prob_exercise` | FAIL / WARN | percent instead of 0..1 / payer + receiver ≠ 1 | return a probability in `decimal` |
| `swaption_expiry` | FAIL / WARN | raising or NaN on/after expiry / vega after expiry | physically settled: an exercised leg is the swap, an unexercised one 0; levels stay finite |
| `bond_buy_sell_fold`, `bond_size_linearity` | FAIL | `buy_sell` or the sign of `size` not folded; size not reaching the face | fold both into one signed face amount in `resolve` |
| `bond_dv01_sign`, `bond_gamma_sign` | FAIL | risk per -1bp (long bond > 0) / convexity sign | long bond `IRDelta` < 0 per +1bp of yield; `IRGammaParallel` > 0 |
| `bond_lightning_dv01` | FAIL | yield DV01 in another scale, or IRDelta a curve delta with dy/dz ≠ 1 | both are Price per +1bp of **yield**; use the total own-rate derivative |
| `bond_yield_unit` | FAIL / WARN | yield in decimal or percent while declared `bp` | multiply, or declare the unit you return |
| `bond_price_yield` | FAIL | `IRFwdRate` sign or definition wrong | the yield to maturity, rising when the price falls |
| `bond_cashflows_bound` | FAIL | principal or coupons missing from `Cashflows` | list every future flow, holder-signed |
| `bond_expiry` | WARN | `ExpiryInYears` not to maturity | years to the final date (DEV-I17) |

The swap pack rows (`swap_atm_npv`, `swap_dv01_sign`, `swap_dv01_band`, `swap_par_rate_unit`, `swap_par_rate_atm`, `swap_bucket_sum`, `swap_pnl_explain`) are catalogued in the docstring of `skills/pricebt-verify-asset-config/scripts/check_asset.py`; `swap_annuity_sign` (above) runs with the IR rows. `swap_par_rate_atm` FAILs a par rate left in percent; `swap_par_rate_unit` only WARNs "could be percent". `swap_bucket_sum` WARNs a ladder 2-10% from the scalar with the same sign: an own-rate `IRDelta` and a zero- or par-pillar ladder differ by dr/ds (R2-2); explain it. It FAILs beyond 10% or on opposite signs.

## What the checker cannot prove

- **That the library's numbers are right.** A consistently wrong curve passes every check, and so do greeks that agree with each other and are all wrong. Compare two or three trades with the desk's own risk system: [`references/independent-validation.md`](references/independent-validation.md).
- **Bumps inside your library.** Every IR row uses evaluated values across dates. It never bumps your curve, so a delta can pass `ir_taylor` on one day and still be a curve delta with dy/dz ≠ 1. The bump probes in [`references/known-answer-probes.md`](references/known-answer-probes.md) close that gap.
- **Holiday behaviour.** One Saturday is probed, not the holiday calendar.
- **Other trades.** One set of kwargs is probed (plus the pack's clones). Rerun with `--kwargs` for each shape you trade: short and long tenors, off-market strikes, forward starts, seasoned bonds.
- **Classes without a pack.** FX, equity, inflation and cash assets get the generic rows only.

## Checks

You are done when:

- the checker exits 0 on every asset config the strategy will use, with the dates and kwargs it will trade;
- every WARN has a one-line explanation, and no declared reason starts with `TODO`;
- for P&L attribution, the P&L-critical `contract[...]` rows are PASS (mapped) and `ir_taylor`, `ir_theta` and `ir_gamma_ratio` are PASS;
- the independent validation is recorded for each asset;
- after any later edit to the config, the checker has been rerun.

## Pitfalls

- **Default dates may not have data.** Always pass `--date` twice for a real library. The IR rows also need the business day after `d2`, a week after the final date, and the dates around the first coupon.
- **SKIP is not PASS.** A swaption with no `IRGammaParallel` skips `ir_gamma_ratio`, and a real library whose data ends today skips `ir_dead_levels` for a 10y trade. Rerun with `--kwargs` for a trade that ends inside your data.
- **The toys return a market on weekends**, so they show a `market_weekend` WARN. That is expected for the toys.
- **The checker imports your library in-process.** A config that logs in does so on the first evaluation.
- **At-the-money probes hide errors.** An ATM swap's npv and theta are about 0, so the checker scales, folds and runs Taylor on `d2`, where the `d1` trade is off-market.
- **A fixed-annuity swap dv01 is at-the-money-exact only** (R2-1). Off-market it leaves `N·(F−K)·ΔA` in `ir_taylor`'s residual. Map the total own-rate derivative for P&L attribution.
- **One fixture per mistake** lives in `tests/skills/fixtures/check_asset/` (for example `bad_half_gamma_swaption.yaml`, `bad_swaption_theta_per_year.yaml`, `bad_bond_dv01_positive.yaml`). Run one to see a FAIL before you trust a PASS.

## Related skills

- [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md): what each measure means and how to compute it with your library (bump recipes, units, when to declare).
- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): writing the config this skill checks.
- [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): patterns for fixing what this skill finds.
- [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md): the P&L decomposition that the P&L-critical rows protect.
- [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md): numeric checks on a finished backtest.
- [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md): reviewing the strategy design.
