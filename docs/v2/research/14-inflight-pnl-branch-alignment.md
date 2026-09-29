# R14 — The in-flight swap P&L-explain branch (`v2-pnl-explain`): inventory, and how `v2-ir-risk` should align with it

Research note, 2026-09-28. Read-only survey of worktree `C:\Users\chris\clee\gsquant-temp-claude\pricebt-pnl`
(branch `v2-pnl-explain`). Nothing in that worktree was modified: only `git status`/`diff`/`log`/`ls-files`
and file reads were run, and the one numeric probe (§5) ran on a **copy** of its toy library in this session's
scratchpad, with `-B`/`PYTHONDONTWRITEBYTECODE=1`, so no `__pycache__` landed in their tree.

Paths below: `PNL/` = `pricebt-pnl` worktree, `IR/` = `pricebt-ir` worktree (ours), `PLAN` =
`PNL/docs/v2/PNL_EXPLAIN_PLAN.md`, `GS154/` = `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant`,
`GS217/` = `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant`.

---

## 0. The decision-relevant facts in one screen

1. **Their branch does not touch `src/pricebt` at all, and says so as a rule:** "The engine is frozen. Do not edit
   `src/pricebt/**`" (PLAN:38-40). Every one of our `src/pricebt/risk` changes is textually conflict-free with them.
   What can break is **behaviour**: §8 lists what our catalogue and contracts must leave working.
2. **Measure names they rely on:** `IRGammaParallel` (the existing `pricebt.risk` object, `IR/src/pricebt/risk/__init__.py:252`),
   `IRDeltaParallel`, `IRFwdRate`, plus **three new custom measures, defined in a skill script, not in src**:
   `IRTheta`, `YearFraction`, `CashPaidToDate` (PLAN:236-241). None of the three exists in either gs version (grep of
   GS154 and GS217 is empty).
3. **Their `IRTheta` is currency per YEAR, at constant forwards.** gs `Theta` has no semantic docstring at all
   (`"Theta"`, GS154/target/measures.py:550-551, GS217/target/measures.py:568-569). Every gs sibling that *is*
   documented is **per one day** (EqTheta, CDTheta, FXThetaLocalCcy; §6). So `IRTheta ≠ gs Theta`: never alias them.
4. **Their gamma recipe is biased on the toy**, and their plan's claim that it works "whatever the library's shift
   primitive is" (PLAN:107-108) does not hold (§5.2). It divides a zero-rate second difference by the measured par move
   squared, which drops the `−(Δnpv/Δpar)·(par₊+par₋−2par₀)` term. On toy USD, ATM payer, 2024-03-01: **−33.6% at 2y,
   −9.7% at 10y, −4.0% at 30y**. The bias does not depend on the bump size, and the result is almost the same on 2024-09-02.
   The corrected formula passes a known-answer check exactly: dv01-difference/Γ = 0.5000 at the money.
   We should adopt their gamma *name/unit/sign/denominator convention* and use the corrected *recipe*. The fix to their files is theirs to make.
   Their session has just logged the same ratios and proposes moving T-GAMMA-2 to 30y (`PNL/docs/v2/DECISIONS_LOG.md:677-739`).
   It correctly names par's convexity in z, but calls it a finite-bump effect and "not a bug". It is a missing chain-rule term (§5.3).
5. **gs's `IRGammaParallel` docstring is a trap for pricebt configs.** gs defines it as "Change in aggregated IRDelta for a
   aggregated 1bp shift…" (GS154/target/measures.py:460-461; GS217:478-479). pricebt's shipped configs map
   `IRDelta` to the **annuity pv01** (`IR/tests/assets/toy_usd_irs.yaml:43`, `IR/configs/assets/usd_sofr_ois_interest_rate_swap.yaml:181,199`).
   Read literally on those configs, the gs text *is* their half-gamma trap. Our contract text must define it as
   ∂²Price/∂x² per bp² of the rate the delta is quoted against, and record the gs wording as a DEV.
6. **Hot files for textual conflicts:** `tests/toylib/rates.py`, `tests/assets/toy_usd_irs.yaml`,
   `docs/v2/ASSET_CONFIG_GUIDE.md` (region before "## Security note"), `check_asset.py` (allowlist near :312, the
   `swap_pack` body), `configs/assets/usd_sofr_ois_interest_rate_swap.yaml` (T2), `DECISIONS_LOG.md` (EOF appends),
   and the T3 skill scripts. **Free for us:** `tests/toylib/swaption.py`, `tests/assets/toy_usd_swaption.yaml`, any new
   `toylib/bond.py` / `toy_usd_bond.yaml`, `DESIGN.md`, and all of `src/`.

---

## 1. Snapshot (Mon 2026-09-28 23:38 EDT)

| Item | Value | Evidence |
|---|---|---|
| Branch / worktree | `v2-pnl-explain` at `C:/Users/chris/clee/gsquant-temp-claude/pricebt-pnl` | `git worktree list` |
| Base | `75abedd` (= `v2-skills` HEAD). `git merge-base v2-ir-risk v2-pnl-explain` = `75abedd` | |
| Our branch vs the same base | `v2-ir-risk` = `75abedd` + merge `a4535bd` of `f9bf071`. `git diff v2-skills v2-ir-risk` touches only 2 notebook files (`040304_mean_reversion_usd_sofr_arbs.ipynb` + its `src/*.py`) | `git -C IR diff --stat v2-skills v2-ir-risk` |
| Commits on top of `v2-skills` | `5a0c133` docs(v2): swap P&L explain (delta/gamma/carry) implementation plan; `0f8bd60` docs(v2): P&L explain plan - half-gamma probe window, diagnostic A-DAILY, FP tolerance. Both touch only `docs/v2/PNL_EXPLAIN_PLAN.md` (+502) | `git log --oneline v2-skills..HEAD`, `git diff --stat v2-skills..HEAD` |
| Uncommitted (T1, in progress) | `docs/v2/ASSET_CONFIG_GUIDE.md` +42; `tests/assets/toy_usd_irs.yaml` +8; `tests/toylib/rates.py` +68/−2; **and, appearing while this note was being written,** `docs/v2/DECISIONS_LOG.md` +66 (a "T1-A" entry, lines 677-739; see §5.3) | `git diff --stat` (re-run at the end of the survey) |
| Untracked | **none** (`git ls-files --others --exclude-standard` is empty; there are no ignored non-cache files either) | |
| Implementer | "a Sonnet session, working autonomously" (PLAN:4); one commit per tier, no push (PLAN:485-493). The DECISIONS_LOG entry refers to sub-tiers **T1-A / T1-B / T1-C** with their own file ownership ("T1-A owns no pytest files"; "`tests/skills/test_skill_swap_pnl.py` is T1-B's"), which suggests parallel sub-sessions | `PNL/docs/v2/DECISIONS_LOG.md:719-730` |

**Caveat.** Line numbers into the modified files are working-tree numbers. They can shift before their T1 commit
(`pnl-explain: T1 toy functions, swap_pnl_definition, explain_table, checker rows`, PLAN:488). Everything else in this note
marked *planned* does not exist yet.

---

## 2. Files: in the tree now, and planned

| File | Status | Tier | What |
|---|---|---|---|
| `docs/v2/PNL_EXPLAIN_PLAN.md` | committed | — | the plan (502 lines) |
| `tests/toylib/rates.py` | **modified, uncommitted** | T1 | `ToyCurve.slope`, `market_sloped`, `TranslatedCurve`, `gamma`, `theta`, `year_fraction`, `cash_paid_to_date` (§3.4) |
| `tests/assets/toy_usd_irs.yaml` | **modified, uncommitted** | T1 | 4 functions (lines 30-33) + 4 `risk_measures` (lines 49-52) |
| `docs/v2/ASSET_CONFIG_GUIDE.md` | **modified, uncommitted** | T1 | new section "## P&L explain functions" inserted at line 228, directly before "## Security note" (now line 270) |
| `tests/assets/toy_eur_irs.yaml` | **deliberately untouched** | T1 | "Leave `toy_eur_irs.yaml` without the new functions. It is the 'explain not supported' negative case (§5.1 T-MISSING)" (PLAN:232) |
| `skills/pricebt-strategy-recipes/scripts/swap_pnl.py` | planned (new) | T1 | measures + `swap_pnl_definition`, `explain_table`, `explain_stats`, `exact_split`, `rate_unit_for` (PLAN:234-268) |
| `skills/pricebt-verify-asset-config/scripts/check_asset.py` | planned | T1 | allowlist + rows `swap_pv_identity`, `swap_gamma`, `swap_theta`, `year_fraction`, `cash_paid_to_date` (PLAN:270-296) |
| `tests/skills/test_skill_swap_pnl.py` | planned (new) | T1 | T-* tests (PLAN:350-393) |
| `tests/skills/test_skill_check_asset.py` | planned | T1 | new rows (PLAN:395-408) |
| `tests/skills/fixtures/check_asset/bad_half_gamma.yaml`, `bad_theta_per_day.yaml`, `bad_year_fraction_extensive.yaml`, `bad_identity_par_pct.yaml` | planned (new) | T1 | broken fixtures (PLAN:402-407) |
| `skills/pricebt-asset-config-cookbook/references/patterns.md`, `skills/pricebt-verify-asset-config/SKILL.md`, `skills/pricebt-strategy-recipes/SKILL.md`, `.../references/construct-cheatsheet.md` | planned | T1 | docs (PLAN:455-461) |
| `configs/assets/usd_sofr_ois_interest_rate_swap.yaml` | planned | T2 | `code:` + `functions:` (gamma/theta/year_fraction/cash_paid_to_date/dead-trade explain rate) + mappings (PLAN:298-320) |
| `tests/test_live_arbs_pnl.py` | planned (new, `live_arbs`) | T2 | A-* tests (PLAN:415-433) |
| `docs/v2/LIVE_ARBS_REPORT.md` | planned | T2 | new section "P&L explain" (PLAN:418) |
| `docs/v2/DECISIONS_LOG.md` | planned (conditional appends) | T1-T3 | par_rate NaN choice (PLAN:165), fallbacks (PLAN:327), threshold calibration (PLAN:449) |
| `skills/pricebt-strategy-intake/templates/strategy_spec.yaml`, `scripts/spec.py`; `skills/pricebt-strategy-recipes/scripts/recipes.py`; `skills/pricebt-strategy-workflow/scripts/run_study.py`; `skills/pricebt-tearsheet-report/scripts/tearsheet.py`; `skills/pricebt-spot-checks/scripts/spot_check.py` | planned | T3 | spec flag, wiring, outputs, tearsheet section, spot check (PLAN:202, 467-483) |
| `skills/pricebt-tearsheet-report/references/metrics-definitions.md`, `skills/pricebt-adversarial-review/references/checklist.md` | planned | T3 | docs (PLAN:462-464) |
| `tests/skills/test_skill_run_study.py`, `test_skill_tearsheet.py`, a second example spec | planned | T3 | (PLAN:482-483) |
| `.claude/skills/*/SKILL.md` (generated) | only if frontmatter changes | any | `python tools/sync_agent_skills.py` (PLAN:465) |
| `src/pricebt/**` | **never** | — | PLAN:38-40; fallback §3.6 instead of a src change (PLAN:322-327) |

---

## 3. Names they introduce

### 3.1 Asset-config function names (`functions:`) and units

| Function | Unit (as declared) | Meaning | Evidence |
|---|---|---|---|
| `gamma` | `ccy_per_bp2` | "`∂²npv/∂par²`, per bp² of this trade's own par rate" | `PNL/tests/assets/toy_usd_irs.yaml:30`; guide `PNL/docs/v2/ASSET_CONFIG_GUIDE.md:237` |
| `theta` | `ccy` (a comment says `# ccy per YEAR`) | "`∂npv/∂t` at constant forward rates"; "the value already bakes in the 'per year', the unit tag is just currency" | yaml:31; guide:238 |
| `year_fraction` | `decimal` (intensive) | "an intensive time coordinate for the pricing date" | yaml:32; guide:239 |
| `cash_paid_to_date` | `ccy` | "cumulative holder-signed cash the trade has paid out so far" | yaml:33; guide:240 |
| *(conditional)* `explain_rate` | bp | a NaN-safe rate for dead trades, only used "If anything depends on the NaN" of ARBS `par_rate` | PLAN:163-164 |

Unit mechanics (ours, unchanged by them): `ccy` and `ccy_per_bp2` are extensive, so they are multiplied by `quantity_`. That is correct for theta and cash.
`decimal` is intensive (`IR/src/pricebt/assets/config.py:22-23`, the `scale_with_quantity` default at :164-165).

### 3.2 Risk-measure names (`risk_measures:` keys) and where each object lives

| Config key | Maps to | Measure object | Lives in |
|---|---|---|---|
| `IRGammaParallel` | `gamma` | existing `RiskMeasure(name="IRGammaParallel", asset_class=Rates, measure_type="ParallelGamma")` | `src/pricebt/risk/__init__.py:252` (unchanged) |
| `IRTheta` | `theta` | `RiskMeasure(name="IRTheta", asset_class=AssetClass.Rates, measure_type="Theta")` | skill `swap_pnl.py` (PLAN:237) |
| `YearFraction` | `year_fraction` | name only, **constructor fields not specified** | skill `swap_pnl.py` (PLAN:238) |
| `CashPaidToDate` | `cash_paid_to_date` | name only, **constructor fields not specified** | skill `swap_pnl.py` (PLAN:239) |

They use `IRDeltaParallel` (existing, `__init__.py:239`; falls back to the `IRDelta` mapping through `base_name`) and `IRFwdRate`
(existing, `__init__.py:253`) as-is. They state that the measures must be module-level singletons "because results are keyed by the measure object" (PLAN:241-242).
That is equality-keyed, not identity-keyed: `IR/src/pricebt/risk/results.py:329` (`if item not in self.risk_measures`).

### 3.3 Planned Python API (skill `skills/pricebt-strategy-recipes/scripts/swap_pnl.py`)

- `swap_pnl_definition(rate_unit="bp", gamma=True, carry=True, rate_measure=IRFwdRate) -> PnlDefinition` (PLAN:243-250):
  - `PNL_delta` = `IRDeltaParallel` × `rate_measure`. It "**must** be a scalar-form delta. Bare `IRDelta` returns the bucketed frame" (PLAN:244-245).
  - `PNL_gamma` = `IRGammaParallel` × `rate_measure`, `second_order=True`.
  - `PNL_carry` = `IRTheta` × `YearFraction`.
  - `scaling_factor`: bp → 1, pct → 100, decimal → 1e4; gamma uses the square (PLAN:248-249).
- `rate_unit_for(asset_config)` reads the rate unit from the config (PLAN:250).
- `explain_table(bt, cash=True) -> DataFrame` (PLAN:251-258). Columns: `actual_dpv, cash, economic, PNL_delta, PNL_gamma,
  PNL_carry, explained, residual`. It is rebuilt independently of `pnl_explain()`'s loop and uses `bt.trade_exit_risk_results[t]` for exits.
  **The column list names the three attributes explicitly** (relevant to swaption/bond reuse, §10.7).
- `explain_stats(table) -> dict`: component totals, `r2`, `residual_share`, `abs_residual_total/abs_economic_total`, worst date (PLAN:259-264).
- `exact_split(bt)`: the three exact terms of §2.7, from per-trade `npv`, `dv01`, `par` and the resolved strike (PLAN:265-266). **Swap-only**: it relies on `PV = pv01·(par−K)`.
- User wiring: `GenericEngine().run_backtest(..., pnl_explain=swap_pnl_definition())`, and add `CashPaidToDate` to `risks=` for the cash column (PLAN:267-268).
- Attribute names: `PNL_delta`, `PNL_gamma` (gs precedent, `GS217/backtests/backtest_objects.py:1005,1012`) and `PNL_carry` (theirs).
  gs's vega attribute is named `VegaPnL` (`GS217/.../backtest_objects.py:1019`), not `PNL_vega`.

### 3.4 Toy-library names (in tree, `PNL/tests/toylib/rates.py`)

| Name | Line | Definition |
|---|---|---|
| `ToyCurve.slope: float = 0.0` | 67, 71 | `DF(d) = exp(−(zero_rate + slope·t)·t)`, `t = days/365`. It is the last dataclass field, so the existing 4-positional construction in `IR/tests/toylib/swaption.py:32` still works |
| `market_sloped(d, ccy, csa=None, slope=0.0)` | 83-92 | same `_zero_rate` level plus a slope; counts as a `"market"` eval; "Not wired into `toy_usd_irs.yaml`" |
| `TranslatedCurve(base, days)` | 199-212 | `ref_date = base.ref_date + days`; `discount_factor(x) = base.DF(x)/base.DF(ref_date)`; forwards held fixed; it exposes only `discount_factor`, `ref_date`, `ccy`, `csa` (**no** `zero_rate`/`slope`, so `dataclasses.replace` cannot bump it) |
| `gamma(market, trade)` | 215-224 | `dataclasses.replace(market, zero_rate=±1e-4)`; `(npv_up+npv_down−2·npv_mid)/((par_up−par_down)/2)**2` |
| `theta(market, trade)` | 227-232 | `(npv(TranslatedCurve(market, 1), trade) − npv(market, trade)) * 365.0` |
| `year_fraction(market)` | 235-239 | `(market.ref_date − date(2000, 1, 1)).days / 365.0` |
| `cash_paid_to_date(market, trade)` | 242-246 | `0.0`: "correct, not a stub -- the toy's `_annuity`/`npv` never drop a past coupon out of PV" |
| `EVAL_COUNTS` keys | 219, 231, 238, 245 | `"gamma"`, `"theta"`, `"year_fraction"`, `"cash_paid_to_date"` |

### 3.5 Planned checker rows, spot check, spec and outputs

- `check_asset.py`, in the swap pack:
  - Allowlist `IRTheta`, `YearFraction` and `CashPaidToDate` in the "not a pricebt.risk measure name" WARN (`IR/skills/pricebt-verify-asset-config/scripts/check_asset.py:312`). The names are imported from `swap_pnl.py` (PLAN:272-273).
  - New rows (PLAN:274-296):
    - `swap_pv_identity`: `|npv − dv01·(par − fixed_rate·1e4)| ≤ 1e-6·|N| + 1e-3·|dv01|`.
    - `swap_gamma`: payer < 0; receiver ≈ −payer; a band; and a **half-gamma probe** `Γ/Γ_est`, where `Γ_est = 2·Δdv01/Δpar`: [0.7, 1.4] PASS, [0.4, 0.6] FAIL.
    - `swap_theta`: receiver ≈ −payer; `|θ| ≤ 1000·|dv01|`.
    - `year_fraction`: exact Δ, and intensive under notional ×3.
    - `cash_paid_to_date`: 0 at d1 ATM; receiver = −payer.
- Spot check `check_pnl_attribution` (T3): PASS/WARN/FAIL on the residual share (PLAN:480-481).
- Spec flag `pnl_explain: {enabled: false, gamma: true, carry: true, cash: auto}` (PLAN:470-473).
- Outputs `pnl_explain.csv` and `pnl_explain.json` (PLAN:476-477).
- Threshold names: `R2_TARGET` (≥ 0.9999), `RS_TARGET` (≤ 1e-3), `R2_TARGET_ARBS` (≥ 0.999), `RS_TARGET_ARBS` (≤ 1e-2) (PLAN:437-440).

### 3.6 Test IDs, which pin behaviour we must not change

- Toy: T-GAMMA-1/2/3, T-THETA-1/2, T-YF, T-CASH, T-DEF, **T-MISSING**, **T-CCY**, T-RECON (PLAN:362-374).
- Backtest level: T-FROZEN, T-SHOCK, T-SLOPE-1/2, T-MONTHLY, T-ROLL, T-LEDGER, **T-SCALE**, T-SYM, T-HOLES, T-NAN, T-CONSIST (PLAN:380-393).
- Live: A-IDENT, A-GAMMA-FD, A-THETA, A-CASH, A-DAILY, A-GAMMA-USE, A-ROLL, A-MATURE, A-FWD, A-SYM, A-CHECK, A-TIME (PLAN:420-433).

---

## 4. Their conventions, quoted

**Decomposition** (PLAN:79-88):
```
economic P&L  =  ΔPV  +  Δcash_paid_to_date
              =  PNL_delta + PNL_gamma + PNL_carry + residual
PNL_delta = dv01(t-1)  · Δpar                    [ccy/bp · bp]
PNL_gamma = ½ · Γ(t-1) · Δpar²                   [ccy/bp² · bp²]
PNL_carry = θ(t-1)     · Δyear_fraction          [ccy/yr · yr]
```
"Here `par` is the **trade's own par rate** (its `IRFwdRate` mapping, in bp)" (PLAN:90).

**Gamma** (PLAN:93-111; guide:244-248):
- "Gamma is the second derivative of **npv**, never the derivative of dv01."
- "With `PV = pv01·(par − K)`, the true `∂²PV/∂par² = 2·∂pv01/∂par + (par−K)·∂²pv01/∂par²`. At the money, this is
  **exactly twice** the change in pv01."
- Formula: `Γ = [npv(up) + npv(down) − 2·npv(0)] / ((par(up) − par(down)) / 2)²`.
- "The denominator uses the **measured** par move of this trade … whatever the library's shift primitive is (zero-rate or par shift)."
- "a payer's Γ < 0 … receiver = −payer."

**Theta** (PLAN:113-129; guide:249-253):
- "θ is `∂PV/∂t` in **currency per year**, measured with the curve **translated** (forward rates held fixed) … not the curve **rolled** (shape held fixed in tenor space)."
- "Translated: `DF'(x) = DF(x) / DF(t+δ)`. Rolled: this is ARBS's `roll_curve` / `carry_bps_running`, which are static-curve measures."
- "`θ = (npv(translated by 1 day) − npv) · 365`." One **calendar** day, annualised by 365. This pairs with the ACT/365F `year_fraction`.

**year_fraction** (PLAN:131-137; guide:254-256):
- "`year_fraction = (pricebt_date − date(2000, 1, 1)).days / 365.0`, `unit: decimal`."
- "It must be intensive … `number` is extensive (DESIGN §4.2) and would scale time by the trade size."
- "`Δyear_fraction` automatically spans weekends, holidays and dropped dates."

**cash_paid_to_date** (PLAN:139-149; guide:257-263):
- "cumulative net cash the trade has paid to the holder from its effective date up to and including the market's reference date. Unit `ccy`; holder-signed."
- "gs parity (`DEV`-documented): **coupons paid between marks are not booked as cash** … Without this function, the residual on every coupon date equals minus the coupon."
- This is a config-only use of our DESIGN §14's "Coupon cash between marks" later-work item (`IR/docs/v2/DESIGN.md:1062`). It adds no pricebt cash hook.

**NaN** (PLAN:151-165; guide:264-268):
- "`pnl_explain()` does `cum_total += metric_pnl` with no guard. One NaN poisons every later cumulative value."
- "Dead trade → `dv01`, `gamma`, `theta` = `0.0`. The explain rate on a dead trade = `fixed_rate · 1e4`."
- Preferred: change ARBS `par_rate`'s dead value from NaN (today: `IR/configs/assets/usd_sofr_ois_interest_rate_swap.yaml:182`). Otherwise add `explain_rate` + `swap_pnl_definition(rate_measure=...)`.
- Code basis:
  - gs skips only when `prev_date_risk == 0` (`GS217/backtests/backtest_objects.py:369-370`; the pricebt port is at `IR/src/pricebt/backtests/backtest_objects.py:429`). A zero risk short-circuits the market lookups, while a NaN risk (NaN == 0 is False) or a NaN market value propagates.
  - The general rule stays "NaN is allowed and propagates" (`PNL/docs/v2/ASSET_CONFIG_GUIDE.md:119`). Their rule is a narrow exception for explain functions on held trades.

**Single currency** (PLAN:167-171): the definition cannot be combined with `result_ccy`, because the plain measures raise
`Unparameterised risk` under the DEV-E15 rewrite (`IR/src/pricebt/backtests/generic_engine.py:421-431`).

**Exact identity** (PLAN:173-194):
- `ΔPV = pv01(t-1)·Δpar + (par(t-1) − K)·Δpv01 + Δpv01·Δpar`.
- "Off-market trades carry a first-order residual … Do not hide it."
- "Tight residual bounds apply only to **near-ATM books** (monthly roll)."

**Fallback** (PLAN:322-327): if `YearFraction` fights them, carry goes into `explain_table` as `IRTheta(t−1)·Δdays/365`,
and `bt.pnl_explain()` then returns delta and gamma only.

---

## 5. Numeric verification (scratchpad copy of their `rates.py`, read-only)

Probe scripts: `…/scratchpad/pnlcopy/probe.py`, `probe_gamma.py`. The world is toy USD, a 1mm payer resolved ATM on 2024-03-01.

### 5.1 What holds as they claim

| Check | Result |
|---|---|
| ATM npv ≈ 0; receiver Γ = −payer Γ; payer Γ < 0 | holds (asserted) |
| T-THETA-1 closed form, off-market payer K = 2%: `θ == npv·(exp(z/365)−1)·365` | `IRTheta = 6362.82` vs the closed form `6362.8193`. The per-calendar-day difference is `17.4324 = IRTheta/365` exactly, by construction |
| §2.2, translation vs roll (sloped world, slope 0.002) | par 589.798839 → translated **589.798839** (flat world: Δ = 2.3e-13 bp); a static-shape one-day roll gives **589.686504** (−0.112 bp/day). So translation leaks no roll-down into carry, and a roll would double-count |
| `year_fraction(2024-03-01)` / `cash_paid_to_date` | 24.1808… / 0.0 |

### 5.2 The gamma recipe is biased, and the bias is structural

The toy is a one-factor world (the zero rate `z`). The exact second derivative of npv with respect to the trade's par rate follows from the chain rule, with `n = npv(z)` and `p = par(z)`:

```
d²n/dp² = ( n'' − n'·p''/p' ) / p'²
finite difference (±h):  Γ_exact = [ n₊+n₋−2n₀ − ((n₊−n₋)/(p₊−p₋))·(p₊+p₋−2p₀) ] / ((p₊−p₋)/2)²
in-flight (rates.py:215-224):  Γ_inflight = [ n₊+n₋−2n₀ ] / ((p₊−p₋)/2)²
```

The missing term is `−(Δnpv/Δpar)·(par₊+par₋−2par₀)`. It vanishes only when par is linear in the shift primitive. So PLAN:107-108
("whatever the library's shift primitive is (zero-rate or par shift)") holds for a par-shift primitive, not a zero-rate one. The known-answer check
is the analytic identity "at the money Γ = 2·∂pv01/∂par" (PLAN:99-100): `Γ_exact` reproduces it to 4 decimals at every tenor.

| ATM payer, 2024-03-01 | Γ_inflight | Γ_exact | bias | (Δdv01/Δpar)/Γ_inflight | (Δdv01/Δpar)/Γ_exact |
|---|---|---|---|---|---|
| 2y (pv01 188.61) | −0.035916 | −0.054052 | **−33.55%** | 0.7525 | **0.5000** |
| 10y (pv01 811.47) | −0.730266 | −0.808330 | **−9.66%** | 0.5534 | **0.5000** |
| 30y (pv01 1729.89) | −4.03652 | −4.20294 | **−3.96%** | 0.5206 | **0.5000** |

The bias is the same for h = 1bp and h = 0.1bp, and on 2024-09-02 it is −33.45% (2y) and −9.39% (10y). It is structural, not finite-difference noise.
One more reference number: a gs-literal "per bp² of the curve primitive" gamma (`n₊+n₋−2n₀` over 1bp²) is −0.789879 at 10y
(`dpar/dz = 1.040015 bp/bp`). **Three different "gammas" for one trade**, so a contract must pin which one it means.

Observations to relay (these are their tests; we cannot edit them):
- **T-GAMMA-2** requires the half-gamma ratio in [0.45, 0.55] (PLAN:365). With their Γ it is 0.7525 at 2y and 0.5534 at 10y, both outside.
- **T-GAMMA-1** compares against "the test's own `(npv₊+npv₋−2npv₀)/((p₊−p₋)/2)²`" (PLAN:364). That is the same formula, so it cannot catch this.
- **T-GAMMA-3** (ATM 10y, residual with gamma ≤ 10% of delta-only, PLAN:366): the leftover second-order residual from a −9.7% Γ is ≈ 9.7% of the gamma term, which is right at the bound.
- The `swap_gamma` probe Γ/Γ_est = 1/(2·ratio) gives 0.904 at 10y (PASS) and 0.664 at 2y (WARN band).
- **A-GAMMA-FD** compares against rateslib's par-space gamma within 5% (PLAN:423). The same mechanism applies if par is nonlinear in the `Curve.shift` primitive. That is plausible but was not measured (rateslib was not run for this note).
- The corrected recipe costs one extra `par(0)` evaluation per trade-date.

### 5.3 Their session found the same numbers and concluded differently (live update)

`PNL/docs/v2/DECISIONS_LOG.md:677-739` (uncommitted, written during this survey) reports the same ratios. On their 2024-01-03 market they get
2y 0.7524, 10y 0.5533 and 30y 0.5205 (compare §5.2: 0.7525 / 0.5534 / 0.5206). Their conclusions:

- "**Diagnosis (not a bug in `gamma()`)** … the finite (not infinitesimal) 1bp bump used by both `Γ` and the 'trap' estimator picks up a chunk of that convexity" (:699-704).
- **Decision:** "(a) run T-GAMMA-2 at 30y, where the ratio (0.5205) sits comfortably inside the existing band", or "(b) … widen the *documented rationale*" (:719-727).
- Their check that "`tr.gamma` [agrees with] a from-scratch second difference … rel diff < 1e-9" (:731-733) uses the same formula, so it cannot tell the two formulas apart.

They named the right mechanism: par's convexity in z *is* the missing `n'·p''/p'` term. They drew the wrong conclusion from it on two counts:

1. **It is not a bump artefact.** The in-flight Γ does not change between h = 1bp and h = 0.1bp (−0.73027 both times at 10y), so the term survives h → 0.
   It is a chain-rule term, not finite-difference convexity.
2. **It is a bug for a measure defined as ∂²npv/∂par²** (their own definition, guide:237). The chain-rule Γ reproduces the analytic identity
   (Δdv01/Δpar)/Γ = **0.5000** at 2y, 10y and 30y.

The fix is one extra `par(0)` evaluation and the `−(Δnpv/Δpar)·(par₊+par₋−2par₀)` numerator correction (§5.2), not a tenor change.
Option (a) would make T-GAMMA-2 pass while the −4% to −34% gamma bias stays in the attribution. **This is the most important item to relay** (§10.7).
It is also the first real instance of the `DECISIONS_LOG.md` EOF-append conflict predicted in §9: their entry starts right after line 673, where ours would go.

---

## 6. gs `Theta` vs their `IRTheta`

| Measure | gs definition (verbatim) | Where |
|---|---|---|
| `Theta` | `RiskMeasure(name="Theta", measure_type=RiskMeasureType("Theta"))`; `__doc__ = "Theta"`; **no asset_class** | GS154/target/measures.py:550-551; GS217/target/measures.py:568-569 |
| `EqTheta` | `RiskMeasureWithCurrencyParameter(… asset_class Equity, measure_type "Theta")`; "Change in Dollar Price over one day" | GS154:325-326; GS217:328-329 |
| `CDTheta` | plain, Credit, "Theta"; `__doc__ = "CDTheta"`. Measures tutorial: "Change in option Dollar Price over one day"; the credit notebook: "change in option Dollar Price over one day, assuming constant vol" | GS154:274-275; GS217:274-275; `GS217/documentation/02_pricing_and_risk/00_instruments_and_measures/tutorials/Measures.ipynb` cell 2; `…/examples/04_credit/03_cdindex_option_risks.ipynb` cell 3 |
| `CommodTheta` | plain, Commod; "Commod Theta" | GS154:292-293; GS217:292-293 |
| `FXThetaLocalCcy` | currency-param, FX, "FX Theta Local Ccy"; "Quoted theta of an FX instrument in premium currency terms representing daily time decay". **2.1.17 only** | GS217:424-425 (absent from GS154) |
| `IRTheta`, `YearFraction`, `CashPaidToDate` | **do not exist** in either gs tree | `grep -rn "IRTheta\|YearFraction\|CashPaidToDate"` over GS154 and GS217: empty |
| gs usage of `Theta` | only as a server-computed measure: equity scalar calc (`GS154/test/api/test_risk.py:190`), a portfolio result (`GS154/test/risk/test_results.py:406`), a mocked flow-vol `BacktestRisk` named `'Theta'` (`GS154/test/backtest/test_backtest_flow_vol.py:105`). `fx_pnl_definition` has **no** theta/carry attribute (`GS217/backtests/backtest_objects.py:996-1026`) | |

Conclusions:
1. **gs `Theta` has no documented unit or convention.** "Per one day" is an *inference* from its documented siblings (EqTheta, CDTheta, FXThetaLocalCcy).
   gs says nothing about constant forwards vs static roll, calendar vs business day, or the vol assumption (except the CD notebook's "assuming constant vol").
2. **Their `IRTheta` is per year, ACT/365F, constant forwards, one calendar day**, and it is equal to 365 × their own one-day difference by construction
   (§5.1). `IRTheta/365 ≈ gs Theta` holds only if gs's undocumented one-day theta uses the same curve evolution. **Never alias them**
   (no `Theta: theta` mapping, no `base_name` link).
3. The objects are already distinct under pricebt identity: `Theta` has `asset_class=None, name="Theta"` and `IRTheta` has `asset_class=Rates, name="IRTheta"`.
   They share `measure_type="Theta"`, which is harmless (`__eq__`/`__hash__` compare all six fields, `IR/src/pricebt/risk/__init__.py:110-122`).
4. If our catalogue ships gs `Theta` and a toy config maps it, use a **separate per-day function** (for example
   `theta_1d: {expr: 'tr.theta(market, trade) / 365.0', unit: ccy}` on the swap toy; a swaption-local equivalent otherwise). Add a DEV row that pins the
   pricebt convention (one calendar day, constant forwards, and constant vol for options), because gs does not define it.
5. For P&L explain, keep their `IRTheta × YearFraction` pairing. A `PnlAttribute` needs a sensitivity per unit of a *measured*
   market variable (`GS217/backtests/backtest_objects.py:367-387`), and `YearFraction` supplies Δt for any step length, including 3-day weekend steps.

---

## 7. gs `IRGammaParallel` vs their gamma

- gs (both versions): `IRGammaParallel = RiskMeasure(name="IRGammaParallel", asset_class=Rates, measure_type="ParallelGamma")`,
  "Change in aggregated IRDelta for a aggregated 1bp shift in the interest rate instruments used to build the underlying
  discount curve" (GS154/target/measures.py:460-461; GS217:478-479).
- `IRGamma` is plain, "IRGamma" (GS154:457-458; GS217:475-476). `IRGammaParallelLocalCcy` is plain, "Interest Rate Parallel Gamma (Local Ccy)" (GS154:463-464; GS217:481-482).
- gs `IRDelta`: "Change in Dollar Price (USD present value) due to individual 1bp moves in the interest rate instruments used to build the underlying discount curve" (GS154:445-446).
  That is the *full* curve delta, and the gs gamma is its derivative.
- pricebt configs map `IRDelta` scalar → **annuity pv01**:
  - toy `pv01 = N·A·1e-4` (`IR/tests/toylib/rates.py:175-177`), mapped at `toy_usd_irs.yaml:43`;
  - ARBS `market.pv01(trade)` (`usd_sofr_ois_interest_rate_swap.yaml:181`), mapped at :199.

  Our DEV table has DEV-I7 (IRFwdRate unit, `IR/docs/v2/DESIGN.md:933`) but **no DEV about IRDelta being an annuity pv01**.
  Consequence: "change in aggregated IRDelta" applied to these configs is ∂pv01/∂s, which is exactly the half-gamma trap. The contract text must not use the gs phrasing.
- Their denominator is "per bp² of the trade's own par" (§4); gs's is "per bp² of a parallel curve-instrument shift". For a swap on toy USD, 10y, the two
  differ by `(dpar/dz)² = 1.0816`, on top of the §5.2 recipe issue.
- Sign conventions agree: payer < 0 in both (a payer is short the fixed bond's convexity).
- Class: plain `RiskMeasure` in gs and in pricebt. So `IRGammaParallel(currency=...)` is impossible and `result_ccy` raises `Unparameterised risk` (their T-CCY relies on it).

---

## 8. Must-not-break list for our catalogue and contracts (behavioural merge constraints)

Our src edits do not conflict textually. They can still break the pnl branch's tests after the merge. Each item cites the code path that has to keep behaving as it does now.

1. **Custom measure names resolve by name.** `PricingService.value` looks up `asset.risk_measures[risk.name]`, then `base_name`
   (`IR/src/pricebt/assets/pricing.py:398-403`). `IRTheta`, `YearFraction`, `CashPaidToDate` and a possible `explain_rate` measure are unknown to
   `pricebt.risk` and must keep working. **Do not reject unknown `risk_measures:` keys at load**, and do not reject them at use.
2. **The required set stays `{Price}`** (`IR/src/pricebt/assets/config.py:326-327`). A per-instrument contract must be "if mapped, then
   unit/form = X", never "IRSwap must map IRGammaParallel/Theta". `toy_eur_irs.yaml` (no gamma or theta) must keep loading: it is T-MISSING's negative case (PLAN:232, 372).
3. **The unmapped-measure error keeps the measure name**: `ConfigError(f"asset {a} has no mapping for risk measure {risk.name}; …")`
   (`pricing.py:403`). T-MISSING expects "clean `ConfigError` naming the missing measure".
4. **No load-time unit checks on their three custom names.** `bad_year_fraction_extensive.yaml` must *load* and then FAIL at the `year_fraction`
   checker row (PLAN:406), not at `config_loads`. The same goes for `bad_half_gamma.yaml` / `bad_theta_per_day.yaml` / `bad_identity_par_pct.yaml`: they must load.
5. **Unit rules for mapped gs measures must accept their config:**
   - `IRGammaParallel` → `ccy_per_bp2`;
   - `IRFwdRate` → any of `bp`/`pct`/`decimal` (DEV-I7; gs's is Percent). Enforcing gs Percent would break every shipped config;
   - `IRDelta` → `ccy_per_bp`;
   - `Price` → `ccy`.
6. **`IRGammaParallel` stays a plain `RiskMeasure`**, with the gs fields (`__init__.py:252`) and **no `base_name` fallback** to `IRGamma`.
7. **Bare `IRDelta` keeps selecting the bucketed form** (`pricing.py:409-411`, DEV-I4). T-DEF relies on it (PLAN:244-245, 371).
8. **DEV-E15 stays as it is**: under `result_ccy`, plain measures raise `Unparameterised risk` (`generic_engine.py:421-431`) (T-CCY, PLAN:373).
   Any gs measure we add must keep gs's class (plain or parameterised), or T-CCY's contract shifts.
9. **Keep `scale_with_quantity` defaults by unit** (`config.py:23, 164-165`). T-SCALE and T-YF depend on `decimal` being intensive and `ccy`/`ccy_per_bp2` extensive.
10. **Keep `check_asset.py:312` a WARN, not a FAIL**, and keep `_risk(name)` = `getattr(pricebt.risk, name, None)` (`check_asset.py:137-140`).
    Their allowlist edit lands in that branch of the code.
11. **Don't ban NaN globally** at load or in the pricing layer (guide:119 allows it). Their NaN rule is for explain functions only.
12. **Don't add `IRTheta`/`YearFraction`/`CashPaidToDate` to `src/pricebt/risk` with different fields.** Equality is six-field, and
    results are looked up by equality (`results.py:329`). A src `IRTheta` with, say, a `unit` set would not match the skill's object.
    Preferably leave all three out of src. `YearFraction`/`CashPaidToDate` have no specified fields yet (PLAN:238-239), so byte-identity cannot be guaranteed.

---

## 9. Conflict matrix (textual)

| File | Their edit (status) | Our likely edit | Recommendation |
|---|---|---|---|
| `tests/toylib/rates.py` | +slope, `market_sloped`, `TranslatedCurve`, 4 functions (in tree, :67-92, :199-246) | bond/swaption helpers, bumps | **Do not edit.** Put bond code in a new `tests/toylib/bond.py` and swaption additions in `tests/toylib/swaption.py`. Until their T1 commit lands, duplicate the ~6-line translation helper privately in `swaption.py`, and switch to `tr.TranslatedCurve` after the merge |
| `tests/assets/toy_usd_irs.yaml` | +4 functions (:30-33), +4 mappings (:49-52) (in tree) | contract fixtures, gs `Theta` | **Do not edit.** Build variants in `tmp_path` from a mapping (`load_asset` accepts a `Mapping`, `config.py:224-229`), or use a new file |
| `tests/assets/toy_eur_irs.yaml` | deliberately untouched (PLAN:232) | none | **Do not add** gamma or theta mappings: that would silently delete their T-MISSING negative case |
| `tests/assets/toy_usd_swaption.yaml`, `tests/toylib/swaption.py` | not touched (absent from the diff and from PLAN §3) | swaption measures and P&L | Free. Use their function/measure names and units (§10.1) |
| `docs/v2/ASSET_CONFIG_GUIDE.md` | new section at :228-268, before "## Security note" (in tree) | contract docs | Avoid lines ~220-270 (the end of the swaption walkthrough through the security note). Put the catalogue and contracts in DESIGN.md §8.1 or a new `docs/v2/` file, plus at most one link line in a distant section (for example the Units section, guide:78-97) |
| `docs/v2/DESIGN.md` | not touched (not in PLAN §6) | §3.2 skeleton, §8.1 catalogue, §11 DEVs | Free |
| `src/pricebt/**` | never (PLAN:38-40) | catalogue, contracts | Free textually; behaviour per §8 |
| `skills/.../check_asset.py` | allowlist near :312, new rows in `swap_pack` (:388+) (planned) | swaption/bond packs, contract rows | Leave `check_risk_measures` (:305-323) and `swap_pack` alone. Put new packs in new functions (or a sibling module), with a two-line dispatch after :577-578 (at worst a trivial adjacent-hunk conflict). Add tests in a new `tests/skills/test_skill_check_asset_ir.py`, not in `test_skill_check_asset.py` |
| `configs/assets/usd_sofr_ois_interest_rate_swap.yaml` | T2 rewrites `code:`, `functions:`, `risk_measures:`, maybe `par_rate`'s dead value (planned) | contract compliance | **Do not edit.** Make the contracts accept the file as it is now and as T2 will leave it. `tests/test_arbs_config_static.py:25-29` asserts only Price/IRDelta, so it is safe |
| `docs/v2/DECISIONS_LOG.md` | conditional EOF appends (planned) | our decisions | Both sides appending at EOF gives a guaranteed (trivial) conflict. Keep our entries in our own plan/research doc, or resolve by concatenation at merge time |
| `docs/v2/LIVE_ARBS_REPORT.md` | new "P&L explain" section (T2) | none expected | avoid |
| T3 skill scripts: `spec.py`, `strategy_spec.yaml`, `recipes.py`, `run_study.py`, `tearsheet.py`, `spot_check.py`; their tests | planned | swaption/bond workflow wiring | **Defer** until after the merge |
| Skill docs: cookbook `patterns.md`, verify `SKILL.md`, recipes `SKILL.md` + `construct-cheatsheet.md`, `metrics-definitions.md`, adversarial `checklist.md` | planned | swaption/bond docs | New reference files instead, linked from one line away from their edits. No frontmatter changes (these regenerate `.claude/skills` stubs via `tools/sync_agent_skills.py`) |
| `tests/skills/test_skills_library.py:29-35` | none | adding a skill dir would require editing this literal list | don't add skill dirs |

---

## 10. Recommendations for `v2-ir-risk`

### 10.1 Naming alignment (adopt verbatim)
- **Config function names:** `gamma`, `theta`, `year_fraction`, `cash_paid_to_date`, with units `ccy_per_bp2`, `ccy` (per year), `decimal`, `ccy`.
  Use the same names on the swaption and bond toy configs.
- **Measure keys:** `IRGammaParallel`, `IRTheta`, `YearFraction`, `CashPaidToDate` (plus `IRDelta`/`IRFwdRate`/`Price` as today).
  `IRTheta` is "per year, constant forwards". gs `Theta` is a separate, per-day, gs-parity measure (§6).
- **Attribute names:** `PNL_delta`, `PNL_gamma`, `PNL_carry`. For a swaption vega attribute, gs precedent is **`VegaPnL`**
  (`GS217/backtests/backtest_objects.py:1019`). Pick that for gs parity, and say so explicitly, because `PNL_vega` would read more consistently with `PNL_carry`.
- **Toy counters:** reuse the `EVAL_COUNTS` keys `"gamma"`/`"theta"` in swaption/bond. `swaption.py` already shares `"npv"`/`"market"` with rates (`swaption.py:28,91`).

### 10.2 Conventions to adopt verbatim
- Theta: one **calendar** day on a **translated** curve (`DF(x)/DF(t+1d)`), × 365. For options, also hold **implied vol constant**
  (for the toy swaption: `SimpleNamespace(curve=TranslatedCurve(c, 1), sigma=σ)`, where `TranslatedCurve` needs only `discount_factor` and `ref_date`, per `swaption.py:90-98`).
- `year_fraction = (date − 2000-01-01).days / 365.0`, `unit: decimal`, and the same epoch everywhere.
- `cash_paid_to_date`: cumulative, holder-signed, effective date through the reference date **inclusive**. This is essential for bonds, whose dirty PV drops by the coupon.
  The toy value is `0.0` when the library never drops paid cash.
- NaN rule: dead or expired trade → sensitivities `0.0` and a finite explain rate. The toy swaption `vega` already returns 0 after expiry (`swaption.py:101-109`).
  Our swaption/bond `gamma`/`theta`/`dv01` must do the same, and a bond's rate measure must stay finite after maturity.
- Rate-unit scaling for any rate market measure (bp 1 / pct 100 / decimal 1e4, squared for gamma), and reuse `rate_unit_for`.
- "Single currency only" for these definitions (DEV-E15).
- The gamma **sign** (payer/short-convexity < 0, receiver = −payer; a long swaption > 0) and the gamma **denominator unit** ("per bp² of the
  instrument's own `IRFwdRate`/rate measure", i.e. the market variable of the paired `PNL_delta`).

### 10.3 Adopt with a correction: the gamma recipe
Use `Γ = [n₊+n₋−2n₀ − ((n₊−n₋)/(p₊−p₋))·(p₊+p₋−2p₀)] / ((p₊−p₋)/2)²` (§5.2) in our swaption/bond toys and in any contract prose.
It reduces to theirs when `p₊+p₋−2p₀ = 0` (par linear in the bump). Pair our gamma test with the known-answer check
"ATM swap: (Δdv01/Δpar)/Γ = 0.5000" so it can fail.

The same mechanism applies to any config that follows their guide text (guide:244-248) verbatim with a primitive in which par is nonlinear in the bump.
How large it is on ARBS (rateslib `Curve.shift`) is **not measured here**: rateslib was not run for this note. It needs checking against A-GAMMA-FD.

### 10.4 Where our pieces go
- `src/pricebt/risk`: the gs catalogue (gs classes and fields exactly, including `Theta`, `IRGammaParallelLocalCcy`, …).
  **No** `IRTheta`/`YearFraction`/`CashPaidToDate` (§8 item 12).
- Contracts: validate mapped gs names only, as "if mapped: allowed units and forms". Required set `{Price}`. Unknown names pass (at most a checker WARN).
  If the contract table contains instrument names, keep it out of the MUST-5 asset-agnostic scan scope (`IR/docs/v2/DESIGN.md` §12.2 item 4 scans only `assets/**`, `markets/**`, `risk/results.py`, `risk/transform.py`), and update the skeleton list (§12.2 item 6) for any new src file.
- Swaption/bond P&L definitions: in **new** skill files (for example `skills/pricebt-strategy-recipes/scripts/swaption_pnl.py`, `bond_pnl.py`).
  After the merge they import `IRTheta`, `YearFraction`, `CashPaidToDate`, `swap_pnl_definition` and `explain_table` from `swap_pnl.py`.
  Before the merge, either wait for their T1 commit or define temporary module-level copies with byte-identical constructors (`IRTheta` is given at PLAN:237; the other two are not specified, so ask). A definition in src would be a pricebt extension that needs a DEV id, and it would contradict their "measures live in the skill" design.

### 10.5 Swaption and bond P&L definitions, shaped like theirs
- Swaption: `swap_pnl_definition(rate_measure=IRFwdRate)` attributes (delta/gamma per bp of the **forward swap rate**, carry via `IRTheta × YearFraction`),
  plus a vega attribute (`IRVega` scalar × a normal-vol market measure, with the scaling set by that measure's unit). The toy `vega` is "per bp of normal vol" (`toy_usd_swaption.yaml:6`).
  Today the toy swaption config maps only `Price` and `IRVega` (`toy_usd_swaption.yaml:30-32`). It needs `dv01`, `par_rate` (forward), `gamma`, `theta`, `year_fraction` and `cash_paid_to_date`.
  Also define the post-expiry behaviour explicitly: the toy prices intrinsic value off the live forward after expiry (`swaption.py:78-83`).
- Bond: the same three attributes, with `rate_measure` = the bond's yield-type measure from our catalogue and gamma = convexity per bp² of that measure. `CashPaidToDate` is mandatory for a sensible residual on coupon dates.
- `exact_split` does not apply (it is swap-only). `explain_table`/`explain_stats` are instrument-agnostic in intent, but their column list names the three swap attributes (PLAN:257). See §10.7.

### 10.6 Merge procedure
1. Before their T1 commit lands, touch none of: `tests/toylib/rates.py`, `tests/assets/toy_usd_irs.yaml`, `tests/assets/toy_eur_irs.yaml`,
   `ASSET_CONFIG_GUIDE.md` lines ~220-270, `check_asset.py` :305-323 or the `swap_pack` body, the ARBS config, or the T3 skill scripts.
2. If we need their toy functions early, wait for `pnl-explain: T1 …` and then take the committed files as-is. For example,
   `git -C IR checkout v2-pnl-explain -- tests/toylib/rates.py tests/assets/toy_usd_irs.yaml` produces byte-identical content, which merges cleanly later.
   Never copy the current uncommitted versions: they may still change.
3. Merge (or rebase onto) `v2-pnl-explain` after their T1 (and ideally T2). Expected textual conflicts: only DECISIONS_LOG EOF appends and the `check_asset.py` dispatch lines, if both edited.
4. After the merge, run their T-* suite together with ours. §8 is the checklist of what our catalogue could have broken.

### 10.7 Items to relay to the pnl session (we cannot edit their tree)
1. The gamma bias and the corrected formula (§5.2), with its effect on T-GAMMA-2, T-GAMMA-3, A-GAMMA-FD and the `swap_gamma` probe.
   Their own DECISIONS_LOG entry (:677-739) records the symptom, attributes it to finite-bump convexity, and proposes moving
   T-GAMMA-2 to 30y. They correctly name par's convexity in z as the cause. But it is a missing chain-rule term that survives h → 0,
   not a bump artefact, and the chain-rule Γ gives exactly 0.5000 (§5.3). The fix is one extra `par(0)` evaluation, not a tenor change.
2. Make `explain_table` iterate `bt.pnl_explain_def.attributes`, instead of hardcoding `PNL_delta/PNL_gamma/PNL_carry` (PLAN:257), so that swaption (vega) and bond definitions reuse it.
3. Specify the exact constructor fields of `YearFraction` and `CashPaidToDate` (PLAN:238-239 give names only), so any other module can build an equal object.
4. Say in their docs that `IRTheta` is not gs `Theta` (gs's documented thetas are per one day; §6).

---

## 11. Commands run (evidence trail)

```
git -C PNL status -sb | log --oneline v2-skills..HEAD | diff --stat | diff --stat v2-skills..HEAD | diff | ls-files --others --exclude-standard
git -C IR status -sb | log --oneline --graph | merge-base v2-ir-risk v2-pnl-explain | diff --stat v2-skills v2-ir-risk | worktree list
grep -n "Theta|IRGamma…" GS154/target/measures.py GS217/target/measures.py; grep -rn -w Theta GS154 GS217 (excluding target/measures.py)
python (stir, -B): extract Measures.ipynb table; cdindex notebook theta cells
cp PNL/tests/toylib/{rates,swaption,__init__}.py -> scratchpad/pnlcopy/toylib; python -B probe.py; python -B probe_gamma.py
```
