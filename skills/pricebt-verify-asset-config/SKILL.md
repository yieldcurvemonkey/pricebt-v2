---
name: pricebt-verify-asset-config
description: Checker for a new or changed pricebt asset config. It evaluates market, resolve, every function and measure, and runs a smoke backtest. For IRSwap, IRSwaption and Bond (or a ConfigInstrument with --pack) it checks the measure contract (every row mapped, no placeholder constants) and contract semantics from evaluated values - one-day Taylor P&L, Theta vs implied carry, the half-gamma ratio, ExpiryInYears, dead levels, ladders, vega-cube keys, Cashflows and coupon drop, bump_size, and the strict identities (PremiumCents, LocalAnnuityInCents, ForwardPrice, FairPremium, ParSpread, CompoundedFixedRate, CRIF) - and runs the swap, swaption (fold, straddle, strike, parity, units, signs, expiry) or bond pack (fold, signs, yield unit, clean/dirty/accrued identities, duration, convexity, settlement, repo, FinancingToDate, forward parity, carry and roll-down, holding cash). Prints a PASS/WARN/FAIL/INFO table. Use after writing or editing an asset config, before strategy or P&L work, or when a config's numbers look wrong.
---

# Verify an asset config

An asset config is trusted code that turns your pricing library into pricebt numbers. One wrong sign, unit or unpinned tenor corrupts every backtest built on it, silently. `check_asset.py` registers the config in its own `PricebtSession`, probes it the way the engine will, and reports one row per check. For an interest-rate asset it also checks the **measure contract** (`docs/v2/IR_RISK_DESIGN.md` §2, §00) and the relations the contract implies between measures, using only values your config produces, so it works with any library. It proves the config is *internally consistent and wired the way pricebt expects*. It cannot prove the library's numbers are *right* (see [What the checker cannot prove](#what-the-checker-cannot-prove)).

## When to use / not use

- **Use** after writing a new asset config, after any edit to one, after upgrading the pricing library, and before any strategy or P&L-attribution work on that asset.
- **Use** when a backtest number or a P&L explain looks wrong: rerun the checker first, so you know whether the config is to blame.
- **Use** `--pack IRSwaption` (or `IRSwap`, `Bond`) as a **diagnostic** on a `ConfigInstrument` asset for a product gs has no class for, to hold it to the nearest contract. A real swap, swaption or bond is never a `ConfigInstrument`: its config says `instrument: IRSwap` / `IRSwaption` / `Bond`, so the loader enforces the contract, and the loader rejects routing a gs-class instrument (`pricebt_asset=`) to a config of another class.
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

   On POSIX use `PYTHONPATH=src:tests`. Try it first on the toys, which pass: `tests/assets/toy_usd_irs.yaml` (the whole strict IRSwap contract with an annuity-pv01 `IRDelta`, plus the swap P&L recipe's extras), and `tests/assets/toy_usd_irs_full.yaml`, `tests/assets/toy_usd_swaption.yaml` and `tests/assets/toy_usd_bond.yaml` (the full contract on the closed-form libraries in `tests/toylib/`).

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

3. **Fix every FAIL** with the tables below, then rerun. **Explain every WARN** in one line, in your notes or the config's `description:`; a WARN you cannot explain is a FAIL. No contract class (`Bond`, `IRSwap`, `IRSwaption`) can declare a contract measure unsupported, so a `contract[M]` row is PASS or FAIL, never a declaration WARN.

4. **For an IR asset, clear the P&L-critical rows before any P&L work**: `contract[...]` for `IRDelta`, `IRGammaParallel`, `IRFwdRate`, `Theta`, `ExpiryInYears` and `Cashflows` (plus the vol measures for a swaption, and `FinancingToDate` for a bond), then `ir_taylor`, `ir_theta` and `ir_gamma_ratio`, and for a bond `bond_financing` and `bond_holding_cash`. [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md) relies on exactly these: the engine books a bond's `Cashflows` and the change of its `FinancingToDate` as cash (DEV-E22), so an error in either reaches `Total`, not only the attribution.

5. **Do the independent validation once per asset**: [`references/independent-validation.md`](references/independent-validation.md), with the probes in [`references/known-answer-probes.md`](references/known-answer-probes.md).

## How to read the IR rows

The contract and IR rows run when a pack applies: auto for `IRSwap`, `IRSwaption` and `Bond`, or `--pack` for a `ConfigInstrument`. Every row is catalogued, with the mistake it catches, in the docstring of `skills/pricebt-verify-asset-config/scripts/check_asset_ir.py`.

- **Contract rows** list, per contract measure, what serves each form. Mapped is PASS. Every class with a contract is strict (`contracts.STRICT_CLASSES`: `Bond`, `IRSwap`, `IRSwaption`; `docs/v2/IR_STRICT_CONTRACT.md` R3-0, `docs/v2/BOND_DESIGN.md` decision 4.1): not mapped is FAIL, and so is a declaration of the measure, a form or a preset of it, mapped or not (a declaration cannot satisfy a contract row); each declared contract measure is also a `contract_declarations` FAIL. The Bond contract has 40 rows, including the bond analytics (`CleanPrice`, `DirtyPrice`, `AccruedInterest`, `ModifiedDuration`, `Convexity`, `DaysToSettlement`, DEV-I20) and the financing contract (`RepoRate`, `RepoHaircut`, `FinancingToDate`, `Carry`, `RollDown`, DEV-I21). `unsupported_measures:` is legal only for a class without a contract (a `ConfigInstrument`), where a declared measure raises `UnsupportedMeasureError` on request. A declaration of a name outside the contract that is also mapped (stale: the mapping wins), a declared preset name and an unknown name are `contract_declarations` WARNs. The loader already refuses every contract FAIL, so you see them only with `--pack` on a `ConfigInstrument` (the diagnostic for a product gs has no class for; a swap, swaption or bond config always uses its gs class, and the loader rejects routing a gs-class instrument to a config of another class).
- **`ir_fake_constant`** (every contract class) FAILs a contract measure mapped to a literal constant (`'0.0'`, `'{}'`, `float('nan')`, `math.nan`, `math.inf`) outside `contracts.ZERO_BY_CONVENTION` (the vol measures on a swap or a bullet bond, `IRBasis` on one curve, `IRXccyDelta` in one currency; each entry carries its reason, e.g. "a bullet bond has no optionality: vol exposure and vol levels are 0 (R2-8)"): a placeholder that silently zeroes (or NaNs) risk and P&L. A zero-by-convention measure must be exactly 0 (or `{}`): `IRVega: '5.0'` FAILs too. The loader does not inspect expressions; this row does.
- **`ir_taylor`** compares the Price change plus cash paid over one business day after `d2` with `Δ·dr + ½Γ·dr² + ν·dσ + vanna·dr·dσ + ½volga·dσ² + Θ·days`. Greeks are at the start, dr is in bp of `IRFwdRate`, and dσ is in bp of `IRAnnualImpliedVol`. The residual is a share of the explained size: PASS ≤ 5%, WARN ≤ 20%, FAIL above. Measures a `ConfigInstrument` declares drop out and are listed. A bond's financing is not in `Theta` (its contract text: "Financing is not in Theta (FinancingToDate)"), so the step's Price change is compared without it. A wrong unit, sign or scale in any greek shows here first.
- **`ir_theta`** compares `Theta` with the implied one-day carry (the step's Price change with every other term removed). A ratio of about 365 is FAIL "looks per year". PASS is 0.8 to 1.25. Both near zero (an at-the-money swap on a translated curve) is an inconclusive PASS. Because the implied carry is what every other term leaves over, a WARN here with nothing else flagged can also be an `IRDelta` that is not the total own-rate derivative (an annuity pv01 off-market), or a wrong gamma or vega: clear `ir_taylor`, `ir_gamma_ratio` and `swap_annuity_sign` before you change `Theta`.
- **`ir_gamma_ratio`** compares `IRGammaParallel` with `d(IRDelta)/dr`, fitted over the 20 business-day steps after `d1` as `ΔIRDelta = slope·dr + drift·days` (vanna·dσ removed), so a delta that drifts with time (a bond's charm) does not bias the slope. About 1 is PASS (0.7 to 1.4). About 0.5 is FAIL: the half-gamma trap. About 2 is WARN: either `IRDelta` is a fixed-annuity pv01 (at-the-money-exact, R2-1) with a true gamma, or gamma is doubled. When `|IRDelta|` equals `|Annuity| × 1e-4` off-market, the reference is `2 × d(IRDelta)/dr` and 1 is again PASS. On an `IRSwap` with **no** `Annuity` mapped (or no off-market `d2`), a PASS becomes WARN "unverifiable": a fixed-annuity `IRDelta` with a half gamma also reads about 1, and only `Annuity` tells them apart. Rates never moving 0.5bp in a day is SKIP. The fit costs 21 evaluations of `IRDelta`, `IRGammaParallel` and `IRFwdRate`.
- **INFO rows** (`ir_ladder_sum[...]`, `fd_params[...]`) report numbers. An own-rate scalar and a curve ladder may differ by dr/ds (R2-2), so a gap under 5% is not a finding.

## Symptom, cause, fix

Generic rows (every asset):

| check | symptom | likely cause | fix |
|---|---|---|---|
| `config_loads` | FAIL `ConfigError [...]` | unknown key, bad `unit:`, missing function, no `Price`, syntax error, or "N measure-contract problem(s)" | follow the message; for the contract, merge the printed mapping skeleton and replace each `'... TODO'` with the computation (there is no declaration route for `Bond`, `IRSwap` or `IRSwaption`) |
| `match` | WARN "finds no asset" | `match:` rule the config's own `defaults`/kwargs do not satisfy | fix `match:` or `defaults`; users otherwise need `pricebt_asset=` |
| `imports_execute` | FAIL `key 'imports'` / `'code'` | module not importable, a login call raising | add `--sys-path`; check the import in a bare Python shell |
| `market_available` | FAIL "returned None" or `key 'market'` | wrong curve id, wrong date type, CSA not handled, no data | print the market expression's inputs; pick dates the library has |
| `market_weekend` | FAIL "raised on Saturday" / WARN "returned an object" | library raises on a non-trading day / serves Friday's data | catch and return `None` in a `code:` helper / usually fine; not stale data on a real holiday |
| `resolve_pins_terms` | FAIL "still relative" / WARN "identical dates" | `resolve:` returns `'10y'` or `'ATM'` / ignores `pricebt_date` | convert every relative term to a date or number / use the injected `market`/`pricebt_date` |
| `functions_finite[f]` | FAIL exception / WARN NaN | wrong attribute or argument order / undefined on a live trade | fix the expression; NaN is for dead trades only |
| `quantity_scaling[M]` | FAIL | `scale_with_quantity:` contradicts the unit | remove the override, or fix the unit |
| `quantity_scaling[M bucketed]` | FAIL | a ladder in ccy per bp that ignores `weights` | return per-unit buckets times `weights` |
| `quantity_scaling[Cashflows frame]` | FAIL | a level (`rate`, `spread`, `discount_factor`, a date) in `scale_columns`, or `notional` missing | `scale_columns` = the amount columns: `payment_amount` (and `notional`) |
| `notional_linearity[f]` | FAIL "it is a rate" / "it is an amount" | wrong `unit:` (a par rate declared `ccy`, a PV declared `bp`) | declare `bp`/`pct`/`decimal` for rates, `ccy`/`ccy_per_bp` for amounts |
| `notional_linearity[f]` | WARN "neither linear nor constant" | library applies notional-dependent logic, or the size kwarg is not reaching the trade | check the trade builder uses `resolved` notional |
| `risk_measures[M]` | FAIL | mapped function fails in scalar or bucketed form | fix the function, or the `{scalar, bucketed}` mapping |
| `risk_measures[M]` | WARN "not a pricebt.risk measure" | typo in the measure name | use the gs name (`IRDelta`, `IRFwdRate`, ...) |
| `measure_series` | WARN "constant" | the tracked function (the one mapped to `IRFwdRate`, else the first non-literal rate) ignores `pricebt_date` | pass `pricebt_date` to the market loader; literal expressions such as a `'0.0'` vol are never tracked |
| `smoke_backtest` | FAIL non-finite Price | NaN or inf on some grid date | find the date in the detail and price it by hand |
| `smoke_backtest` | FAIL identity / exception | engine could not run the asset (message says why) | fix per message; rerun with `--no-backtest` to isolate |
| `fx_round_trip` | FAIL | FX config returns the same quote both ways | return the reciprocal for the reverse pair |
| `performance` | WARN SLOW | one evaluation over 1s: curve rebuilt per call, no caching in the library session | build the curve once in `market:`, reuse it in every function |
| `swap_atm_npv` | FAIL/WARN | ATM strike computed on a different curve, date or convention than valuation | strike and value off the same `market` object |
| `swap_dv01_sign` | FAIL payer <= 0 | library reports risk as PV change per -1bp, or receiver-positive | negate in the function; pricebt wants payer dv01 > 0 per +1bp |
| `swap_dv01_sign` | FAIL receiver != -payer | `pay_or_receive` not reaching the trade builder | map it in `resolve:`/`trade:` |
| `swap_dv01_band` | FAIL | dv01 per 1% (x100), per unit rate (x1e4), or per unit notional | rescale to currency per 1bp on the full notional |
| `swap_par_rate_unit` | FAIL "looks like decimal" | library returns 0.0425 and the function is declared `bp` | multiply by 1e4, or declare `decimal` |
| `swap_par_rate_unit` | WARN "could be percent" | 4.25 declared `bp` | multiply by 100, or declare `pct` |
| `swap_par_rate_atm` | FAIL | an ATM trade's par rate on its trade date != `resolved["fixed_rate"] * 1e4` (unit wrong, or resolve and par_rate use different curves) | fix the unit of `par_rate`, or price par and strike off the same market |
| `swap_bucket_sum` | FAIL / WARN 2-10% same sign | ladder in a different unit/sign than the scalar, or missing pillars; a WARN: an own-rate scalar and a curve ladder differ by dr/ds (R2-2) | same convention as the scalar; include every pillar |
| `swap_pnl_explain` | FAIL negative ratio | npv and dv01 use opposite sign conventions | make npv payer-positive when rates rise |
| `swap_pnl_explain` | WARN ratio outside [0.5, 1.5] | large carry/roll between the dates, or a scale error | try closer dates; if it persists, check units |
| `swap_pv_identity` | FAIL | `npv`/`dv01`/`par`/`fixed_rate` disagree on sign or unit (`PV != dv01*(par-K)`), at the ATM date or the off-market one | fix whichever of the four is wrong; the two FAIL details show exactly which date broke |
| `swap_pv_identity` | WARN "dv01 depends on the strike" | `dv01` is a realistic full-curve PV sensitivity, not the fixed-leg annuity pv01 (PNL_EXPLAIN_PLAN.md 2.1) — a legitimate convention difference, not a bug | nothing to fix; explain the WARN in the config's `description:` if it is expected |
| `swap_gamma` | FAIL sign/band | payer gamma >= 0, receiver != -payer, or `abs(gamma)/(abs(dv01)*T*1e-4)` outside [0.2, 2] | fix the second-npv-difference formula or its sign |
| `swap_gamma` | FAIL half-gamma probe | gamma computed from `dv01` differences instead of the true second difference of `npv` (PNL_EXPLAIN_PLAN.md 2.1's "half-gamma trap") | use `npv(up)+npv(down)-2*npv(mid)`, never `dv01(up)-dv01(down)` |
| `swap_gamma` | SKIP half-gamma probe | no pair of business days within 30 (up to 10 apart) moved the trade's par by >= 3bp | pass `--date`s further apart, or on a more volatile market; never lower the 3bp threshold |
| `swap_theta` | FAIL | receiver `theta` != `-payer`, or `abs(theta) > 1000*abs(dv01)` (a unit-magnitude check — commonly theta computed per day instead of per year) | fix the sign convention or the time unit (PNL_EXPLAIN_PLAN.md 2.2) |
| `year_fraction` | FAIL | declared `unit: number` (extensive) instead of `decimal`, so pricebt multiplies it by trade size, or the d1->d2 delta != `(d2-d1).days/365` | declare `decimal`; `year_fraction` must be an intensive time coordinate (PNL_EXPLAIN_PLAN.md 2.3) |
| `cash_paid_to_date` | FAIL | a fresh ATM trade already shows nonzero cash at d1, or receiver != `-payer` at d2 | fix the sign convention, or the cumulative-cash calculation itself |

Contract and IR semantics rows (IRSwap, IRSwaption, Bond, or `--pack`):

| check | symptom | likely cause | fix in your library's terms |
|---|---|---|---|
| `contract[M]` | FAIL "not mapped" (Bond/IRSwap/IRSwaption, only with `--pack`) | a contract measure has no mapping | map it with the contract unit (`measures.py block <config>` prints the mapping skeleton); there is no declaration route |
| `contract[M]` | FAIL "a declaration cannot satisfy it" (Bond/IRSwap/IRSwaption, only with `--pack`) | the measure, a form or a preset is declared | map it and delete the declaration |
| `contract[M]` | FAIL wrong unit or shape | e.g. `DaysToSettlement` declared `decimal` (its `days` kind allows only `number`), or `number` without `scale_with_quantity: false` (a level must be intensive) | declare the contract unit; `number` is extensive by default, so set `scale_with_quantity: false` on an intensive count |
| `contract_declarations` | FAIL (Bond/IRSwap/IRSwaption, only with `--pack`) | `unsupported_measures declares <name>, a <class> contract measure` | delete the declaration |
| `ir_fake_constant` | FAIL (Bond/IRSwap/IRSwaption) | a literal constant (`'0.0'`, `'{}'`, `float('nan')`, `math.nan`) for a measure outside `contracts.ZERO_BY_CONVENTION`, or a non-zero constant for one inside it | compute it (cookbook pattern 14); a zero-by-convention measure is exactly `'0.0'` / `'{}'` |
| `contract_declarations` | WARN stale, preset or unknown name | a measure outside the contract is mapped *and* declared, a preset name is declared, or a misspelt name declares nothing | delete or fix the declaration (the mapping already wins) |
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
| `ir_cashflows` | FAIL | a paid flow still listed, or amounts not holder-signed | list flows with `payment_date > pricebt_date`, signed by the folded direction (a Bond's `payment_date` is the trade date on which `Price` drops the flow) |
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
| `ir_cashflow_drop` | FAIL > 20% of the flow | `Price` does not drop the flow `Cashflows` lists on its payment date, or drops another amount | drop exactly the listed flows on their payment date (R2-6). The rate move is `IRFwdRate`'s. For an `IRSwap` that is not a PASS, the delta term is re-taken on the market part of that move: `IRFwdRate` on the step's end date minus the same priced on the start date's market (a `CloseMarket` override), because a seasoned swap's par rate also jumps when the paid period rolls off the remaining schedule; `Theta` holds the own par fixed across that roll (DEV-I15), so the roll part is explained at the remaining `Annuity` × 1e-4 |
| `bond_price_yield` | FAIL | `IRFwdRate` sign or definition wrong | the yield to maturity, rising when the price falls |
| `bond_cashflows_bound` | FAIL | principal or coupons missing from `Cashflows` | list every future flow, holder-signed |
| `bond_expiry` | WARN | `ExpiryInYears` not to maturity | years to the final date (DEV-I17) |

`swap_annuity_sign` (above) runs with the IR rows.

Bond identities and financing (bond pack; `docs/v2/BOND_DESIGN.md` §3; the contract text in `src/pricebt/risk/contracts.py` is the definition). A bond's `Price` is its **settlement-date market value**: (clean + accrued at standard settlement) × face / 100, holder-signed, not discounted to the pricing date. Each row SKIPs when its measures are not mapped (a loaded `Bond` config maps them all):

| check | relation it checks | likely cause of a FAIL | fix in your library's terms |
|---|---|---|---|
| `bond_clean_dirty` | `CleanPrice + 100 × AccruedInterest / face = DirtyPrice` and `DirtyPrice × face / 100 = Price` (signed face; `CleanPrice` and `DirtyPrice` are the same for a long and a short) | clean and dirty swapped; accrued per 100 instead of in currency; `Price` discounted to the pricing date | `DirtyPrice = 100 × Price / face`; `AccruedInterest` in currency, holder-signed |
| `bond_fair_premium` | `FairPremium = Price` | `FairPremium` discounted or forwarded (the swap/swaption meaning) | map `FairPremium` to the same function as `Price`: the settlement-date value is what the holder pays |
| `bond_premium_cents` | `PremiumCents = Price / \|face\|` in the declared unit, so (pct) `= sign(face) × DirtyPrice`: the dirty price for a long, its negative for a short | signed face, a clean price, a wrong unit factor | `Price / abs(face)` × the unit factor (pct 100) |
| `bond_duration` | `ModifiedDuration ≈ −1e4 × IRDelta / Price` (the IRDelta scalar per +1bp of yield) | duration per 100bp, Macaulay instead of modified, a yield convention other than `IRFwdRate`'s | `−(1/P) dP/dy` in years per unit decimal yield, P the dirty price |
| `bond_convexity` | `Convexity` ≈ the finite-difference second derivative of price in yield, `1e8 × IRGammaParallel / Price` | convexity per (1%)², halved (a Taylor coefficient), or on the clean price | `(1/P) d²P/dy²` in years² |
| `bond_settlement` | `DaysToSettlement` = calendar days from the pricing date to standard settlement (T+1: 1 on a weekday, 3 on a Friday), an intensive `number` | business days instead of calendar days; scaling with size | count calendar days to the settlement date your library uses; `unit: number`, `scale_with_quantity: false` |
| `bond_accrued_over_coupon` | across the first coupon date: `AccruedInterest` resets to about 0 when settlement reaches the coupon date, `Price` drops the coupon on the `Cashflows.payment_date` (the trade date whose settlement is on or after the coupon date: T+1, the business day before a business-day coupon date) | the drop on the coupon date itself (settlement ignored), ex-coupon or record-date rules not applied, accrual convention not ACT/ACT ICMA for a Treasury | list each flow with `payment_date` = the trade date `Price` drops it |
| `bond_repo` | `RepoRate` finite on every held date, in its declared unit; `RepoHaircut` in [0, 1) as a decimal | repo in pct under `decimal`, a haircut of 2 meaning 2% | declare the unit you return; a haircut of 2% is `0.02` decimal |
| `bond_financing` | `FinancingToDate` is 0 on the trade date, ≤ 0 for a long and ≥ 0 for a short, and a one-day change `= −(1 − h) × Price(t₀) × RepoRate × days / basis` (h the haircut, days the calendar days between the two settlement dates, basis the repo day count, ACT/360 for USD) | principal not pinned at resolve (re-read each day), compounding instead of simple interest, trade-date instead of settlement-date day counts, a sign flip | pin `(1 − RepoHaircut) × Price(trade date)` in `resolve`; simple interest at each calendar day's `RepoRate` |
| `bond_forward_parity` | `ForwardPrice = Price × (1 + RepoRate × τ(s, H)) − Σ C × (1 + RepoRate × τ(c, H))` over coupons c in (s, H], H = settlement + 1 calendar month (following business day) | forward to maturity or to expiry (the swap meaning), coupons not netted, τ in the bond's day count instead of the repo day count | the contract formula with `RepoRate` held flat to H |
| `bond_carry_roll` | `Carry = (Price − AccruedInterest) − (forward − accrued at H)`, with the forward recomputed by the parity formula and the accrued at H from the `Cashflows` accrual columns; `Carry` and `RollDown` negate with the direction and are finite. (The design's flat-curve identity, `Carry + RollDown ≈ 0` when the repo matches the yield, is a toy test, not a checker row) | carry on the dirty price, roll-down on a re-fitted instead of a rolled-down curve, the horizon not H | both clean values; `RollDown` on the library's reference curve, unchanged in time to maturity, spread held |
| `bond_holding_cash` | a `GenericEngine` backtest of a short (sold) position held across the first coupon drop date: `ΔTotal = ΔPrice + coupons dropped + ΔFinancingToDate` on every step, and `backtest.holding_cash[d][position]` holds `(ccy, cashflow, financing)` | `Cashflows.payment_date` not the drop date (coupons booked on the wrong step), `FinancingToDate` not cumulative | fix `Cashflows` or `FinancingToDate`; `tests/test_holding_cash.py` shows the engine side |

Strict-contract identities (IRSwap, IRSwaption; `docs/v2/IR_STRICT_CONTRACT.md` R3-1). A Bond's `FairPremium`, `ForwardPrice` and `PremiumCents` have Bond contract text (the settlement-date value, the financed forward at H, the dirty price per face), so the bond rows above check them. Each SKIPs when its measures are not mapped, and runs on `d2` (off-market) when it has a market:

| check | symptom | likely cause | fix in your library's terms |
|---|---|---|---|
| `ir_premium_cents` | FAIL (WARN within 1%) | `PremiumCents` != Price / \|notional_amount\| × the unit factor (bp 1e4, pct 100, decimal/number 1): a percent of notional under `bp`, the signed notional, an unsigned `abs(Price)` (checked on both directions) | `Price / abs(notional) * 1e4`, unit `bp` |
| `ir_local_annuity` | FAIL (WARN within 1%) | `LocalAnnuityInCents` != Annuity / \|notional_amount\|: an annuity per bp (× 1e-4), the signed notional, an unsigned `abs(Annuity)` (checked on both directions) | `Annuity / abs(notional)`, unit `decimal` |
| `ir_forward_price` | FAIL / WARN | opposite sign to Price (FAIL); the implied rate ln(ForwardPrice / Price) / `ExpiryInYears` of the opposite sign to `IRFwdRate` once \|r\| ≥ 10bp (FAIL: Price × DF, at any rate level); far from `IRFwdRate` (WARN beyond max(r/2, 25bp), FAIL beyond max(r, 50bp)): the wrong date; != Price from expiry on | `Price / DF(expiry)` (a swaption's expiration date, a swap's termination date) |
| `ir_fair_premium` | FAIL / WARN | opposite sign (FAIL); a ratio discounting the wrong way for the own rate's sign (FAIL: Price × DF(spot)); more than ~10 days of discounting at the own rate (WARN up to 1%: a far premium date; FAIL beyond: the expiry or final date, i.e. `ForwardPrice`) | `Price / DF(premium settlement)`: spot, or the premium payment date |
| `ir_par_spread` | FAIL / WARN | changes with direction, reversed sign (forward − K), or off by a scale factor of 2 or more (FAIL); more than max(0.5bp, 1%) from K − `IRFwdRate` (WARN: legs on different schedules or curves) | K − `IRFwdRate` in bp, the same for payer and receiver |
| `ir_compounded_fixed_rate` | FAIL | outside [K, e^K − 1] (a de-compounded or continuous restatement), moving with the pricing date, or, when the fixed-leg frequency f is knowable (a `fixed_rate_frequency` kwarg or resolved term, else the spacing of the fixed leg's `Cashflows`), != (1 + K/f)^f − 1: a semiannual leg returned as K. With f unknowable only the bounds are checked, and the row says so | `(1 + K/f)^f − 1` from the resolved fixed rate / strike |
| `ir_crif` | FAIL (WARN within 1%) | `RiskType` not `Risk_IRCurve`, `Qualifier` not the config currency, `Bucket` not a SIMM volatility-group string (`'1'`, `'2'`, `'3'`; an int FAILs), `Label1` not a SIMM tenor (`'10Y'`), `Label2` not a SIMM sub-curve (`OIS`, `Libor1m/3m/6m/12m`, `Prime`, `Municipal`; a SOFR curve is `OIS`), `AmountCurrency` not the `Qualifier`'s, or sum(`Amount`) != the `IRDelta` ladder's sum | rows from the trade's own `IRDelta` ladder, lower-case SIMM tenors |

## What the checker cannot prove

- **That the library's numbers are right.** A consistently wrong curve passes every check, and so do greeks that agree with each other and are all wrong. Compare two or three trades with the desk's own risk system: [`references/independent-validation.md`](references/independent-validation.md).
- **Bumps inside your library.** Every IR row uses evaluated values across dates. It never bumps your curve, so a delta can pass `ir_taylor` on one day and still be a curve delta with dy/dz ≠ 1. The bump probes in [`references/known-answer-probes.md`](references/known-answer-probes.md) close that gap.
- **Holiday behaviour.** One Saturday is probed, not the holiday calendar (nor a settlement date or repo accrual across a holiday).
- **That the repo is the right repo.** `bond_repo` and `bond_financing` check the shape and the arithmetic of the financing, not whether `RepoRate` is general collateral when the bond trades special, or whether the haircut is the one your desk gets. Compare with the desk's repo source: [`references/independent-validation.md`](references/independent-validation.md).
- **Other trades.** One set of kwargs is probed (plus the pack's clones). Rerun with `--kwargs` for each shape you trade: short and long tenors, off-market strikes, forward starts, seasoned bonds.
- **Classes without a pack.** FX, equity, inflation and cash assets get the generic rows only.

## Checks

You are done when:

- the checker exits 0 on every asset config the strategy will use, with the dates and kwargs it will trade;
- every WARN has a one-line explanation, no contract class declares anything, and no `ConfigInstrument` declaration reason starts with `TODO`;
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
- **One fixture per mistake** lives in `tests/skills/fixtures/check_asset/` (for example `bad_half_gamma_swaption.yaml`, `bad_swaption_theta_per_year.yaml`, `bad_bond_dv01_positive.yaml`; for the strict rows `bad_premium_cents_pct.yaml`, `bad_local_annuity_per_bp.yaml`, `bad_forward_price_times_df.yaml`, `bad_fair_premium_final_date.yaml`, `bad_par_spread_reversed.yaml`, `bad_compounded_rate_decompounded.yaml`, `bad_crif_upper_tenors.yaml` and `bad_fake_constant.yaml`, each failing only its own row). Run one to see a FAIL before you trust a PASS.

## Related skills

- [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md): what each measure means and how to compute it with your library (bump recipes, units; a bond, swap or swaption config maps every contract measure).
- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): writing the config this skill checks.
- [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md): patterns for fixing what this skill finds.
- [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md): the P&L decomposition that the P&L-critical rows protect.
- [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md): numeric checks on a finished backtest.
- [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md): reviewing the strategy design.
