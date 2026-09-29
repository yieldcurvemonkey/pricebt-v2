# Merge notes: `v2-pnl-explain` into `v2-ir-risk`

Steps to merge the in-flight swap P&L-explain branch (`v2-pnl-explain`; research note R14) with this branch. Started by Phase A of [`IR_RISK_DESIGN.md`](IR_RISK_DESIGN.md); Phase F completes it. A `git merge-file` union of the two branches has no textual conflicts. The steps below fix what fails *semantically* after that union.

Verified on a scratch union (Phase A, 2026-09-29): with steps 1 to 3 applied, `tests/skills/test_skill_swap_pnl.py`, `tests/skills/test_skill_check_asset.py`, `tests/test_arbs_config_static.py`, `tests/test_contracts.py` and `tests/test_unsupported_measures.py` all pass (145 passed).

## Why the merge needs steps

Decision 0.2 (measure contracts) overrides R14 §8 items #2 and #4. Every `IRSwap` config must map or declare each measure in its contract, so an in-flight `IRSwap` config with no `unsupported_measures:` block fails at load with "N measure-contract problem(s)". It never reaches the checker row or assertion it was written for. R2-9 lets the in-flight *mappings* win over our *declarations*, but a declaration that becomes stale raises a `UserWarning`. `test_contracts.py::test_every_shipped_ir_config_is_contract_valid_without_warnings` turns that warning into a failure.

## Steps

1. **Add declaration blocks to the four in-flight check_asset fixtures:** `tests/skills/fixtures/check_asset/{bad_half_gamma,bad_identity_par_pct,bad_theta_per_day,bad_year_fraction_extensive}.yaml`.
   - Paste the block that each file's load error prints (`risk.contracts.unsupported_block`) and replace each `TODO` reason with a real one.
   - Without this step, 5 tests fail at `config_loads`:
     - four `test_skill_check_asset.py::test_broken_fixture_fails_its_check[...]` cases;
     - `::test_bad_theta_per_day_slips_past_swap_theta_but_t_theta_1_catches_it`.
2. **Declare the unmapped measures in `_SLOPED_YAML`** (`tests/skills/test_skill_swap_pnl.py`, about line 115):
   - The string goes through `str.format`, so double the braces of any flow map: `IRDelta: {{bucketed: "..."}}`. The other measures take whole-measure reasons.
   - The config maps `Price`, `IRDelta` (scalar only), `IRFwdRate` and `IRGammaParallel`. It must declare:
     - `IRDelta` (bucketed);
     - `IRDiscountDeltaParallel`, `IRGamma`, `IRVega`, `IRVanna`, `IRVolga`, `IRBasis`, `IRXccyDelta`;
     - `IRSpotRate`, `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol`;
     - `Theta`, `ExpiryInYears`, `Annuity`, `Cashflows`.
   - Without this step, `::test_t_slope_1_...` and `::test_t_slope_2_...` fail with "16 measure-contract problem(s)".
3. **Delete the `IRGammaParallel:` line from the `unsupported_measures:` block of `tests/assets/toy_usd_irs.yaml`.** The in-flight branch maps `IRGammaParallel: gamma`. Keep every other declaration: the in-flight per-year `IRTheta` is a custom name, and `Theta` stays declared (DEV-I15: `IRTheta` = 365 × `Theta`; never map `Theta` to the per-year function).
4. **After T2 lands, delete the lines T2 maps from the ARBS config's declaration block** (`configs/assets/usd_sofr_ois_interest_rate_swap.yaml`), at least `IRGammaParallel`. The warnings test above covers this config too.
5. **Keep `toy_eur_irs.yaml` declaration-only.** It stays the in-flight negative case (decision 0.12).
   - `test_skill_swap_pnl.py::test_t_missing_toy_eur_has_no_gamma_or_theta_mapping` matches `no mapping for risk measure (IRGammaParallel|IRTheta|YearFraction)`.
   - `UnsupportedMeasureError`'s message keeps that phrase ("asset A has no mapping for risk measure X: 'X' is declared unsupported ..."; IR_RISK_DESIGN §2.4), so the test passes with our declarations.
6. **Do not rely on `IRGammaParallel` being declared on `toy_usd_irs`** in tests written on this branch. `test_unsupported_measures.py::test_declaration_is_found_through_base_name` uses `IRDiscountDeltaParallelLocalCcy` for that reason: the in-flight branch does not map its base.

## Known, recorded non-compliance (not load-checked)

- **ARBS `IRFwdRate` on a dead trade.** The config's `par_rate` returns `NaN` after maturity, which breaks R2-7 ("levels are finite on every held date"). The in-flight T2 "dead-trade explain rate" fixes it.
- **At-the-money-exact `IRDelta` scalar** (R2-1, DEV-I12). The toy and ARBS swap configs map the `IRDelta` scalar to a fixed-annuity pv01.
  - This matches the contract's total own-rate derivative only at the money. Off-market it misses the first-order term `N·(F−K)·ΔA` (R14).
  - The load check cannot detect this. Their `risk_measures:` are left unchanged here because T2 rewrites the ARBS ones.
