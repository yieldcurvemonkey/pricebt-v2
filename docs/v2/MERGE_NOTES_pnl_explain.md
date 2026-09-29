# Merge notes: `v2-pnl-explain` into `v2-ir-risk`

How to merge the concurrent swap P&L-explain branch (`v2-pnl-explain`, worktree `pricebt-pnl`; its plan is `docs/v2/PNL_EXPLAIN_PLAN.md` on that branch; research note R14) into this branch. Design context: [`IR_RISK_DESIGN.md`](IR_RISK_DESIGN.md) decision 0.12 and R2-20.

**State these notes were written against (2026-09-29):** `v2-ir-risk` at `86dfeae` plus the phase-F working tree; `v2-pnl-explain` at `c8b3178` (T1, T2 and T3 landed: toy and ARBS gamma/theta/year-fraction/cash functions, `swap_pnl.py`, checker rows, the spec flag, the tearsheet section, the spot check); merge base `75abedd`. If either branch has moved, re-run the dry run in "Verification" first.

**Verified:** `git merge-tree --write-tree v2-ir-risk v2-pnl-explain` (tree `3557d6f`), extracted to a scratch directory, with the phase-F working tree overlaid, resolved exactly as below. The targeted tests (the verification list) pass, 396 of 396. The full suite passed 1895 with 27 skipped, and failed only the 2 tests step 3.6 fixes; after step 3.6 `tests/skills` passes 477 of 477, and `tools/sync_agent_skills.py --check` is in sync. (Steps 2.7 and 3.7 and the LF note are text edits added by the final review, after that run; they change no code and no test.)

## 1. Files both branches touch

| File | Merge result | What to do |
|---|---|---|
| `skills/pricebt-asset-config-cookbook/references/patterns.md` | **conflict** at the end | step 2.1 |
| `skills/pricebt-spot-checks/scripts/spot_check.py` | **conflict** (docstring, check list) | step 2.2 |
| `skills/pricebt-strategy-recipes/scripts/recipes.py` | **conflict** (imports) | step 2.3 |
| `skills/pricebt-tearsheet-report/scripts/tearsheet.py` | **conflict** (`build_tearsheet` signature and body) | step 2.4, plus a rename |
| `skills/pricebt-verify-asset-config/SKILL.md` | **conflict** (the rows table) | step 2.5 |
| `tests/skills/test_skill_spec.py` | **conflict** (both appended tests) | step 2.6 |
| `docs/v2/DECISIONS_LOG.md` | **conflict** (append/append at the end) | keep both blocks: theirs (2026-09-28) first, then this branch's `2026-09-29 — v2-ir-risk` block |
| `tests/assets/toy_usd_irs.yaml` | clean; **semantically wrong** | step 3.3 |
| `configs/assets/usd_sofr_ois_interest_rate_swap.yaml` | clean; **semantically wrong** | step 3.4 |
| `tests/skills/fixtures/check_asset/bad_{half_gamma,identity_par_pct,theta_per_day,year_fraction_extensive}.yaml` | theirs only; **fail to load** | step 3.1 |
| `tests/skills/test_skill_swap_pnl.py` (`_SLOPED_YAML`) | theirs only; **fails to load** | step 3.2 |
| `skills/pricebt-verify-asset-config/scripts/check_asset.py` | clean; **one probe is wrong for contract configs** | step 3.5 |
| `docs/v2/ASSET_CONFIG_GUIDE.md` | clean (their "P&L explain functions" section sits after the swaption walkthrough; this branch's "Measure contracts" section sits before "`build_on` and market sharing"; this branch rewrote the "Functions return a `float`" bullet: keep ours) | step 2.7; check the generated tables test still passes |
| `tests/skills/test_skill_tearsheet.py` | clean; **fails** after the tearsheet merge | step 2.4 |
| `tests/skills/test_skill_pnl_attribution.py` (this branch's) | clean; **fails** after step 3.3 | step 3.6 |
| `skills/pricebt-strategy-recipes/references/construct-cheatsheet.md` | clean; cites "pattern 14" | step 2.1 |
| `skills/pricebt-strategy-intake/scripts/spec.py`, `templates/strategy_spec.yaml` | clean | nothing (the `pnl_explain:` spec key is theirs, see §4) |
| `skills/pricebt-strategy-workflow/scripts/run_study.py` | clean | nothing (their `swap_pnl.explain_table` wiring and this branch's `_rate_series` both stay) |
| `skills/pricebt-strategy-recipes/SKILL.md`, `skills/pricebt-adversarial-review/references/checklist.md`, `skills/pricebt-tearsheet-report/references/metrics-definitions.md`, `tests/skills/test_skill_{check_asset,recipes,spot_checks,tearsheet}.py` | clean | nothing |

Never edit `tests/toylib/rates.py` on this side (decision 0.12): it is theirs.

## 2. Resolving the textual conflicts

Keep LF line endings on every file you write (they are LF in both branches). Python's `Path.write_text` on Windows writes CRLF: use `write_bytes`, or `open(..., newline="")`.

2.1 **`patterns.md`.** Keep this branch's patterns 14-27, then append theirs, renumbered: `## 14. P&L explain functions (gamma, theta, year_fraction, cash_paid_to_date)` becomes `## 28. P&L explain functions ...`. Then, in `skills/pricebt-strategy-recipes/references/construct-cheatsheet.md`, change "`patterns.md` pattern 14" to "pattern 28". In pattern 28, right after its `gamma` recipe, add: "`IRGammaParallel` must be the chain-rule second derivative of pattern 17; this `(n₊ + n₋ − 2n₀)/((p₊ − p₋)/2)²` gamma leaves out the par rate's own convexity and is about 10% low at 10y ATM until the chain-rule term lands (§4)." Their DECISIONS_LOG entry citing `patterns.md:272-275` is history: leave it.

2.2 **`spot_check.py`.** Keep both check rows in `run_spot_checks`: this branch's `("attribution residual", lambda: check_pnl_attribution_generic(backtest))` (for `backtest.pnl_explain_table()`; it calls `attribution.grade_reason` from `skills/pricebt-pnl-attribution/scripts/attribution.py`) **and** theirs, `("P&L attribution", lambda: check_pnl_attribution(pnl_stats))` (for `swap_pnl.explain_stats`). The docstring is this branch's `rate_measure` sentence, followed by their `pnl_stats` paragraph. The signature already has their `pnl_stats=None` (it merged cleanly).

2.3 **`recipes.py`.** Keep both imports: `import instrument_terms as terms` (this branch) and `import swap_pnl` (theirs).

2.4 **`tearsheet.py`.** Both branches add a P&L attribution section and both call its figure `pnl_attribution`, so the two must be told apart:
- `build_tearsheet(..., spot_checks=None, caveats=None, notes=None, attribution=None, pnl_table=None, pnl_stats=None)`. The docstring names both: `attribution` is a `backtest.pnl_explain_table()`, rendered as "P&L attribution by greek"; `pnl_table`/`pnl_stats` are their `swap_pnl` outputs.
- Body: their `blocks = _blocks(..., generated_at, pnl_table, pnl_stats)`, then this branch's `_add_attribution(blocks, pngs, attribution)`.
- In this branch's `_add_attribution`, rename the heading `("h2", "P&L attribution")` to `("h2", "P&L attribution by greek")`, and the image key `pnl_attribution` to `greek_attribution` (both the `pngs[...]` key and the `("img", ...)` block). Their section is always rendered (it says "not enabled" without `pnl_stats`), so without the rename the page has two identical headings and one PNG overwrites the other.
- In this branch's `tests/skills/test_skill_tearsheet.py::test_attribution_section_only_when_supplied`, follow the rename: `"P&amp;L attribution by greek" not in plain`, `greek_attribution.png` (twice), and `page.index("<h2>P&amp;L attribution by greek</h2>")`.

2.5 **`skills/pricebt-verify-asset-config/SKILL.md`.** Both sides rewrote the rows table. Make one table from theirs (the generic rows, then the swap rows through `cash_paid_to_date`), and change it in four ways:
- Move this branch's `quantity_scaling[M bucketed]` and `quantity_scaling[Cashflows frame]` rows in after `quantity_scaling[M]`.
- Replace their `measure_series` row with this branch's (it tracks the `IRFwdRate` function and skips literals).
- Make `swap_bucket_sum`'s symptom "FAIL / WARN 2-10% same sign": an own-rate scalar and a curve ladder differ by dr/ds (R2-2).
- Drop this branch's copies of `notional_linearity`, `risk_measures`, `smoke_backtest`, `fx_round_trip` and `performance`.

Then keep this branch's "Contract and IR semantics rows" table and its swaption and bond pack table. Its closing sentence "The swap pack rows (...) are catalogued in the docstring ..." shrinks to "`swap_annuity_sign` (above) runs with the IR rows.", because their table now lists the swap rows.

2.6 **`tests/skills/test_skill_spec.py`.** Keep both: this branch's swaption/bond tests, then their `test_pnl_explain_cash_accepts_all_valid_forms`.

2.7 **`docs/v2/ASSET_CONFIG_GUIDE.md`, their "P&L explain functions" section** (merges cleanly, but contradicts this branch's rules):
- In their NaN bullet ("None of the four may return `NaN` ..."), replace "if the library's own `par_rate` goes `NaN` post-maturity, either fix that at the source or add a separate NaN-safe rate function for the P&L explain path and keep the original for anything else that depends on the `NaN`" with "a level such as `par_rate` stays finite and continuous with its last live value (R2-7; see Dead instruments): fix a `NaN` at the source". A NaN level fails `check_asset_ir`'s `ir_dead_levels` row.
- After their `gamma` bullet ("`gamma` is the second derivative of `npv`, never `dv01` differenced"), add the same line as step 2.1: `IRGammaParallel` must follow the chain rule of the "Half gamma" trap (Traps, below "Dead instruments"); their formula is about 10% low at 10y ATM until the term lands.

## 3. Resolving what merges cleanly but fails

Decision 0.2 (measure contracts) means every `IRSwap` config must map or declare each contract measure: an in-flight `IRSwap` config with no `unsupported_measures:` block fails at load ("N measure-contract problem(s)") before it reaches the row or assertion it was written for. R2-9 lets the in-flight **mappings** win over this branch's **declarations**, but a stale declaration raises a `UserWarning`, and `tests/test_contracts.py::test_every_shipped_ir_config_is_contract_valid_without_warnings` turns that into a failure.

3.1 **Declare the unmapped measures in the four in-flight fixtures** `tests/skills/fixtures/check_asset/{bad_half_gamma,bad_identity_par_pct,bad_theta_per_day,bad_year_fraction_extensive}.yaml`. Load each one: the `ConfigError` ends with a paste-ready `unsupported_measures:` block (`risk.contracts.unsupported_block`). Paste it right after `currency:`, and replace every `TODO` reason with a real one (e.g. "check_asset fixture: IRVega is not wired in this one-error copy of toy_usd_irs"; the checker flags `TODO` reasons). Each fixture declares 15 measures: every contract measure except `Price`, `IRDelta`, `IRFwdRate` and `IRGammaParallel`, which they map. Without it, 5 tests fail at `config_loads`: four `test_skill_check_asset.py::test_broken_fixture_fails_its_check[...]` cases and `::test_bad_theta_per_day_slips_past_swap_theta_but_t_theta_1_catches_it`.

3.2 **Declare the unmapped measures in `_SLOPED_YAML`** (`tests/skills/test_skill_swap_pnl.py`, about line 115), right after `currency: USD`. The string goes through `str.format`, so double the braces of a flow map. The config maps `Price`, `IRDelta` (scalar only), `IRFwdRate` and `IRGammaParallel`, so it declares:
   ```yaml
   unsupported_measures:
     IRDelta: {{bucketed: "the sloped variant maps only the scalar dv01"}}
     IRDiscountDeltaParallel: "not wired in the sloped-world test variant"
     # ... the same reason for IRGamma, IRVega, IRVanna, IRVolga, IRBasis, IRXccyDelta, IRSpotRate,
     # IRAnnualImpliedVol, IRAnnualATMImpliedVol, IRDailyImpliedVol, Theta, ExpiryInYears, Annuity, Cashflows
   ```
   Without it, `::test_t_slope_1_...` and `::test_t_slope_2_...` fail with "16 measure-contract problem(s)".

3.3 **`tests/assets/toy_usd_irs.yaml`: delete the `IRGammaParallel:` line** of its `unsupported_measures:` block (they map `IRGammaParallel: gamma`). Keep every other declaration. `IRTheta`, `YearFraction` and `CashPaidToDate` are custom names outside the contract (unrestricted), and `Theta` stays declared: `IRTheta` is per year, `Theta` per day (§4). After the merge, `check_asset_ir` on `toy_usd_irs` reports `ir_gamma_ratio` **WARN 1.775**: their `tr.gamma` lacks the chain-rule term (§4). Expected until that lands; it is not a merge error.

3.4 **ARBS config (`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`): delete the `IRGammaParallel:` line** of its declaration block. T2 maps `IRGammaParallel: gamma`, and nothing else in the contract (its `IRTheta`, `YearFraction`, `CashPaidToDate` are custom names). Keep the `Theta` declaration, and optionally reword its reason from "not wired for ARBS yet: PNL_EXPLAIN_PLAN.md T2 adds a per-year IRTheta" to "the per-year IRTheta is mapped; Theta (per day) = IRTheta / 365 is not wired". T2 also fixes the dead-trade `par_rate` (`fixed_rate × 1e4` instead of `NaN`), which was this branch's one recorded R2-7 non-compliance. After the merge, `check_asset_ir`'s `ir_gamma_ratio` row reports **WARN "unverifiable: map Annuity"** on this config. That is intended: without `Annuity` the checker cannot tell a fixed-annuity delta with a half gamma from a true pair.

3.5 **`check_asset.py` `swap_gamma` half-gamma probe.** Their probe estimates `Gamma_est = 2·(dv01(t_j) − dv01(t_i))/Δpar`, where `dv01` is the `IRDelta` scalar. The factor 2 holds only when that scalar is a fixed-annuity pv01 (`d pv01/dpar = Γ/2`). Under the contract (R2-1) the scalar is the **total** own-rate derivative, whose difference already is Γ, so the probe FAILs a correct config at ratio ≈ 0.5: `tests/skills/test_skill_check_asset_ir.py::test_full_contract_toys_have_no_fail[toy_usd_irs_full.yaml]` fails with "Gamma/Gamma_est=0.572: looks like gamma from dv01 differences". Fix, just before `gamma_est` is computed:
   ```python
   k = payer.resolved_terms.get('fixed_rate')
   off = svc.resolve(ctx.inst.clone(pay_or_receive='Pay', fixed_rate=k + 0.01), d1, None) if isinstance(k, (int, float)) else None
   annuity_dv01 = off is None or math.isclose(dv01(off, d1), dv01_1, rel_tol=1e-6)   # the swap_pv_identity test
   gamma_est = (2.0 if annuity_dv01 else 1.0) * (dv01(payer, dj) - dv01(payer, di)) / dpar_probe
   ```
   Their `bad_half_gamma` fixture has an annuity dv01, so it still FAILs.

3.6 **This branch's `tests/skills/test_skill_pnl_attribution.py`.** After step 3.3, `IRGammaParallel` is mapped on `toy_usd_irs`, so a definition's first gap there is `Theta`. In `test_definition_for_refuses_a_book_it_cannot_attribute`, match `r"toy_usd_irs: Theta declared unsupported"` in place of `IRGammaParallel`, and fix its comment to "declares theta and vol unsupported". In `test_cli_definition_and_demo`, assert `"Theta declared unsupported" in err`.

3.7 **Text that goes stale on the merge** (no test catches it). Reword:
- `tests/assets/toy_usd_irs.yaml`, the `unsupported_measures:` comment "R2-20 keeps this config declaration-only": "R2-20 kept this config declaration-only until the v2-pnl-explain merge; it maps Price, IRDelta, IRFwdRate and IRGammaParallel and declares the rest".
- `tests/assets/toy_usd_irs.yaml`, the `Theta` reason "toylib.rates has no one-day translated-curve carry function" (false once `tr.theta` exists): "the per-year IRTheta (tr.theta) is mapped; Theta (per day) = IRTheta / 365 is not wired in this config".
- `skills/pricebt-verify-asset-config/SKILL.md`, "(a declaration-only swap)": "(a swap mapping Price, IRDelta, IRFwdRate and IRGammaParallel, declaring the rest)".
- `skills/pricebt-risk-measures/references/capability-matrix.md`, "maps `Price`, `IRDelta` and `IRFwdRate` and declares the rest": add `IRGammaParallel` to the mapped list.
- `skills/pricebt-pnl-attribution/references/definitions.md`, "`tests/assets/toy_usd_irs.yaml` and the shipped ARBS config declare them unsupported": "map `IRGammaParallel` (the in-flight gamma, without the chain-rule term) but declare `Theta` and `ExpiryInYears`, so they are still refused".
Then run `python tools/sync_agent_skills.py`.

Also keep, from this branch, the two in-function edits in `check_asset.py` (`check_measure_series` prefers the `IRFwdRate` function and skips literal expressions; `swap_bucket_sum` WARNs from 2% to 10% with the same sign) and its dispatch `checks = check_asset_ir.with_ir_checks(checks, ctx, pack, swap_pack)` (the `--pack` option). Theirs are new rows inside `swap_pack`, so the union keeps both.

**No action (checked):**
- `toy_eur_irs.yaml` stays declaration-only: it is their negative case (decision 0.12). `test_skill_swap_pnl.py::test_t_missing_toy_eur_has_no_gamma_or_theta_mapping` matches `no mapping for risk measure (IRGammaParallel|IRTheta|YearFraction)`, and `UnsupportedMeasureError`'s message keeps that phrase ("asset A has no mapping for risk measure X: 'X' is declared unsupported ..."; IR_RISK_DESIGN §2.4). The test passes on the union.
- `PricingService.unit_value(inst, d, function, csa, *, params=())` keeps its four positional arguments; this branch only appended the keyword-only `params`. Their calls (`check_asset.py`, `test_skill_connect_example.py`, `test_live_arbs_pnl.py`) are unchanged.
- Their `ToyCurve.slope: float = 0.0` is a trailing default field. This branch builds `ToyCurve(d, ccy, z, csa)` positionally and bumps with `dataclasses.replace(curve, zero_rate=...)`, which keeps the slope. Their `rates.TranslatedCurve` and this branch's `irrisk._TranslatedCurve` implement the same translation `DF(x)/DF(t+1d)`: an optional cleanup can drop ours for theirs.
- Tests on this branch never rely on `IRGammaParallel` being declared on `toy_usd_irs`: `test_unsupported_measures.py::test_declaration_is_found_through_base_name` uses `IRDiscountDeltaParallelLocalCcy`, whose base the in-flight branch does not map.

## 4. Conventions to relay to the in-flight branch (not merge blockers)

- **Chain-rule gamma.** Their `gamma` (toy `tr.gamma` and the ARBS config's `gamma`) is `(n₊ + n₋ − 2n₀) / ((p₊ − p₋)/2)²`. The contract's `IRGammaParallel` (R2-1, `src/pricebt/risk/contracts.py`) is the chain-rule second derivative: `Γ = [n₊ + n₋ − 2n₀ − ((n₊ − n₋)/(p₊ − p₋))·(p₊ + p₋ − 2p₀)] / ((p₊ − p₋)/2)²`. The missing term is the par rate's own convexity in the curve shift. On the toy it is about 10% at 10y ATM: a 1mm payer on 2024-01-03 has `tr.gamma` −0.738 against −0.817 per bp² (`toylib.irrisk.ir_gamma`). Add the term to both functions, then re-baseline T-GAMMA-1's hand-rolled check to the same formula. Until then, `IRGammaParallel` on `toy_usd_irs` and ARBS carries this bias into `PNL_gamma`.
- **Two carry conventions, both correct, never mixed.** Theirs: `PNL_carry = IRTheta × YearFraction`, scaling 1. `IRTheta` is ccy **per year** and `YearFraction` rises by Δdays/365. This branch: `PNL_theta = Theta × ExpiryInYears`, scaling −365. `Theta` is ccy **per calendar day** (DEV-I15) and `ExpiryInYears` falls by Δdays/365. Both give `Theta·Δdays` while the trade is alive. `IRTheta = 365 × Theta`: **never map `Theta` to a per-year function** (365 times too big; `check_asset_ir`'s `ir_theta` row FAILs it as "looks per year").
- **`explain_table` versus `pnl_explain_table`.** `BackTest.pnl_explain_table()` (in `src`, a pricebt addition, IR_RISK_DESIGN §7.3) is **canonical**: it takes any `PnlDefinition` (swaption vanna/volga, bond), has columns `actual_pnl, cashflow_pnl, economic_pnl, <one per attribute>, explained_pnl, residual_pnl`, and takes cash from the `Cashflows` table (flows due in `(t−1, t]`). Their `swap_pnl.explain_table(bt, cash=True)` stays as the swap recipe's table. Its columns are `actual_dpv, cash, economic, PNL_delta, PNL_gamma, PNL_carry, explained, residual`, and its cash is the step change of `CashPaidToDate`. The two walk the same held set and give the same numbers when the cash sources agree. On the union, a monthly-rolled toy payer (`swap_pnl_definition()`, `CashPaidToDate` requested, 23 steps) matched column for column to 1.4e-12. They differ only in cash. On ARBS today, `CashPaidToDate` is mapped but `Cashflows` is declared, so only `explain_table` sees coupons. To make them agree, map an ARBS `Cashflows` that lists exactly the flows `npv` will drop. Their T2 note says ARBS `npv` keeps a coupon **on** its payment date and drops it the day after, whereas R2-6's `Cashflows` counts a flow in the step that ends on its `payment_date`. Shift accordingly, and let `check_asset_ir`'s `ir_cashflow_drop` row confirm it. A later cleanup may make `explain_table` a column rename of `pnl_explain_table` when `Cashflows` is mapped.
- **The spec's `pnl_explain:` key is theirs** (`{enabled, gamma, carry, cash}`, swap-only in `recipes.build`). This branch adds no spec key for swaption or bond attribution. Extending it (for example `swaption_pnl_definition()` when the primary is an `IRSwaption`, `bond_pnl_definition()` plus `risks=[Cashflows]` for a `Bond`, reported through `pnl_explain_table` and the tearsheet's `attribution=`) is left to them after the merge.
- **After the merge,** add a link to `skills/pricebt-strategy-recipes/scripts/swap_pnl.py` in `skills/pricebt-pnl-attribution/SKILL.md` (and `references/instrument-level.md`), where the swap definition is mentioned as living on the in-flight branch. Then run `python tools/sync_agent_skills.py`.

## 5. Verification

Dry run, before merging (it touches no ref and no worktree):
```bash
git -C <pricebt-ir> merge-tree --write-tree --name-only --messages v2-ir-risk v2-pnl-explain
```
It should list exactly the seven conflicted files of §1: six in `skills/**` and `tests/**`, plus `docs/v2/DECISIONS_LOG.md` once this branch's block is committed.

After merging and applying §2-§3, from the worktree root:
```bash
PYTHONPATH="src;tests" C:/Users/chris/anaconda3/envs/stir/python.exe -m pytest -o addopts= -p no:cacheprovider -q \
  tests/test_contracts.py tests/test_arbs_config_static.py tests/test_unsupported_measures.py \
  tests/skills/test_skill_swap_pnl.py tests/skills/test_skill_check_asset.py tests/skills/test_skill_check_asset_ir.py \
  tests/skills/test_skill_spot_checks.py tests/skills/test_skill_spot_checks_hedge.py tests/skills/test_skill_tearsheet.py \
  tests/skills/test_skill_recipes.py tests/skills/test_skill_spec.py tests/skills/test_skill_run_study.py \
  tests/skills/test_skill_run_study_ir.py tests/skills/test_skill_asset_config_cookbook.py \
  tests/skills/test_skill_pnl_attribution.py tests/test_docs_links.py tests/test_docs_contract_tables.py
grep -rn "^<<<<<<<\|^>>>>>>>" skills tests docs src configs             # nothing
git -C <pricebt-ir> grep -n "IRGammaParallel: \"" -- tests/assets/toy_usd_irs.yaml configs/assets/   # nothing
C:/Users/chris/anaconda3/envs/stir/python.exe tools/sync_agent_skills.py --check
PYTHONPATH="src;tests" C:/Users/chris/anaconda3/envs/stir/python.exe -m pytest tests -o addopts= -p no:cacheprovider -q
```
Never set `PRICEBT_LIVE_ARBS` for this: `tests/test_live_arbs_pnl.py` (theirs) is opt-in, and its first run needs the user's approval.
