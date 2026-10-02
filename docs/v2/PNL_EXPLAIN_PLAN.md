# Swap P&L explain (delta / gamma / carry): implementation plan

Status: plan, ready for hand-off. Branch `v2-pnl-explain` (worktree `pricebt-pnl`), based on `v2-skills`.
Author: Opus session, 2026-09-28. Implementer: a Sonnet session, working autonomously.

## 0. What and why

pricebt already ships gs's `BackTest.pnl_explain()`, a straight port of the gs code at
`src/pricebt/backtests/backtest_objects.py:404`. It is driven by a `PnlDefinition` made of `PnlAttribute`s.
Each attribute contributes this per step, summed over the instruments held at the previous date:

```
first order:   scaling · risk(t-1) · (mkt(t) - mkt(t-1))
second order:  0.5 · scaling · risk(t-1) · (mkt(t) - mkt(t-1))²
```

The only built-in definition is `fx_pnl_definition()`, for FX options. For a swap there is nothing:
- no gamma function in any asset config;
- no carry term;
- no ready-made definition;
- no residual or reconciliation view.

This plan adds all four, **without touching the engine**. Deliverables:

1. **Optional asset-config functions** for P&L explain: `gamma`, `theta`, `year_fraction`, `cash_paid_to_date`.
   Add them to the toy USD config and to the ARBS USD SOFR config, and document them in the cookbook.
2. **`swap_pnl_definition()`**, a gs-native `PnlDefinition` with three attributes (`PNL_delta`, `PNL_gamma`, `PNL_carry`),
   plus **`explain_table()`**. `explain_table()` reconciles the attribution against the actual P&L of the held book
   (including coupon cash) and reports the residual.
3. **Checker rows** in `check_asset.py` that catch the classic mistakes: the half-gamma trap, theta per day
   versus per year, a quantity-scaled time measure, and an npv/dv01/par/strike inconsistency.
4. **Workflow integration** (last tier): a spec flag, a tearsheet "P&L attribution" section, and a spot check.
5. **Comprehensive tests on toy data (always run) and on real ARBS data (opt-in live).** This is the priority.
   Most of the effort should go here (section 5).

## 1. Ground rules (read before anything else)

- **The engine is frozen.** Do not edit `src/pricebt/**`. `pnl_explain()` stays byte-for-byte the gs port.
  If you believe a `src` change is unavoidable, stop that tier. Write the case in `docs/v2/DECISIONS_LOG.md`
  and use the fallback in §3.6. Do not edit `src`.
- **Five MUSTs still hold** (DESIGN §2.1). In particular: nothing library-specific under `src/pricebt`,
  and `tests/guards/` stays green. Never weaken a guard.
- **The repository is public.** Put no secrets and no new absolute user paths in `skills/` or `tests/skills/`.
  The existing ARBS config already contains the ARBS path; leave that line as it is.
- **Tests must be able to fail.** For every new check, mutate the logic it covers (listed per test in §5) and
  confirm that the named test fails. Record the mutation and the failing test name in the tier report.
- **Derive expected numbers from inputs, never from magic constants.** Thresholds you calibrate (§5.6) are the one
  exception, and they follow the calibration rule there.
- **Work in this worktree only**: `C:\Users\chris\clee\gsquant-temp-claude\pricebt-pnl`, branch `v2-pnl-explain`.
  Every git command is `git -C <that path> ...`. Do not push. Commit once per tier (§7).
- Commands, from the worktree root:
  ```powershell
  $env:PYTHONPATH = "src;tests"
  C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests -o addopts= -p no:cacheprovider
  ```
  Use the stir interpreter directly. `conda run` rejects multi-line `-c`.

### 1.1 ARBS safety rails (the user authorised live ARBS **reads** for this work; the rails are unchanged)

The user asked for this to be tested on real ARBS data. That authorises **store-only reads through the existing config**.
It does not authorise loosening any rail:

- Live tests go in a `live_arbs`-marked file. They run only with `PRICEBT_LIVE_ARBS=1`.
- **Never import ARBS, rateslib, MDP, etc. at module level** in any test file. Follow the pattern and the docstring
  in `tests/test_live_arbs.py`: ARBS is reached only lazily, inside a test function body, through the config.
- Use dates **before today and on or before 2026-08-20** (`_LAST_SAFE`).
  - Never raise `_LAST_SAFE`.
  - Never enable the `...-NOJUMPS` source.
  - Never call ARBS methods that fetch or write, such as `live` data or fixings refreshes.
- New ARBS logic goes **only in `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`**, in its `code:` block and
  `functions:`. The ARBS repository itself is read-only.
- Known and accepted side effect: every call creates an empty today-dated folder in the ARBS fixings cache.
- Run the live file deliberately, a few times per change. Never run it in a loop or under a retry harness.
- If anything unexpected happens (a network call, a store write, a stack trace from inside ARBS), stop the live tier.
  Write it up in `docs/v2/LIVE_ARBS_REPORT.md` and continue with the toy tiers.

## 2. The decomposition: conventions that decide correctness

Per held trade, per step t−1 → t (consecutive available dates, which can be several calendar days apart):

```
economic P&L  =  ΔPV  +  Δcash_paid_to_date          (cash that left the PV, received by the holder)
              =  PNL_delta + PNL_gamma + PNL_carry + residual

PNL_delta = dv01(t-1)  · Δpar                    [ccy/bp · bp]
PNL_gamma = ½ · Γ(t-1) · Δpar²                   [ccy/bp² · bp²]
PNL_carry = θ(t-1)     · Δyear_fraction          [ccy/yr · yr]
```

Here `par` is the **trade's own par rate** (its `IRFwdRate` mapping, in bp). The following conventions are
requirements. Each one exists because the obvious alternative gives a wrong answer.

### 2.1 Gamma is the second derivative of **npv**, never the derivative of dv01

Both libraries' `dv01` is an **annuity pv01**:
- toy: `N·A·1e-4`;
- ARBS: rateslib's analytic fixed-leg pv01 (research/06 line 6).

With `PV = pv01·(par − K)`, the true `∂²PV/∂par² = 2·∂pv01/∂par + (par−K)·∂²pv01/∂par²`. At the money, this is
**exactly twice** the change in pv01. A config author who bumps the curve and differences `dv01` gets **half the
gamma**. Define it as:

```
Γ = [npv(up) + npv(down) − 2·npv(0)] / ((par(up) − par(down)) / 2)²       (up/down = ±1bp parallel curve shift)
```

The denominator uses the **measured** par move of this trade. That makes Γ "per bp² of this trade's par rate",
which matches `PNL_gamma`'s market variable, whatever the library's shift primitive is (zero-rate or par shift).
Signs:
- a payer's Γ < 0 (it is short the fixed bond's convexity);
- receiver = −payer.

### 2.2 Theta is at **constant forwards**, so roll-down lives in delta, not twice

θ is `∂PV/∂t` in **currency per year**, measured with the curve **translated** (forward rates held fixed; see note 1).
It is not the curve **rolled** (shape held fixed in tenor space; see note 2).

Under a translation, the trade's par rate is (nearly) unchanged, so `Δpar` over a "nothing moved" step is ≈ 0,
and delta and carry do not overlap. Roll-down that actually happens shows up as a real `Δpar` and is attributed
to `PNL_delta`.

If θ were measured on a rolled (static-shape) curve, roll-down would be counted in both θ and `Δpar`. That double
count is a test (§5.2 T-SLOPE-2).

Notes:
1. Translated: `DF'(x) = DF(x) / DF(t+δ)`.
2. Rolled: this is ARBS's `roll_curve` / `carry_bps_running`, which are static-curve measures.

Implementation: a one-calendar-day forward difference, `θ = (npv(translated by 1 day) − npv) · 365`.

### 2.3 `year_fraction` is an **intensive** time coordinate

`year_fraction = (pricebt_date − date(2000, 1, 1)).days / 365.0`, `unit: decimal`.

It must be intensive, so that pricebt does not multiply it by `quantity_`. `number` is extensive (DESIGN §4.2) and
would scale time by the trade size, which is wrong for any `AddScaledTradeAction` trade. `Δyear_fraction`
automatically spans weekends, holidays and dropped dates.

### 2.4 `cash_paid_to_date` makes coupon days explainable (ARBS only)

`cash_paid_to_date` is the cumulative net cash the trade has paid to the holder from its effective date up to and
including the market's reference date. Unit `ccy`; holder-signed; payer-positive when the float leg receives more
than the fixed leg pays.

gs parity (`DEV`-documented): **coupons paid between marks are not booked as cash**. The PV simply drops on a
payment date. Without this function, the residual on every coupon date equals minus the coupon.

The toy never pays coupons. Its `_annuity` keeps past coupons and `npv` never drops them, so on the toy
`cash_paid_to_date ≡ 0`. That is correct, and it is itself a test.

### 2.5 Explain functions never return NaN for a held trade

`pnl_explain()` does `cum_total += metric_pnl` with no guard. One NaN poisons every later cumulative value.
ARBS's `par_rate` currently returns `float("nan")` once a swap is dead. A trade that matures between two marks
has `dv01(t−1) ≠ 0` and `par(t) = NaN`, so the NaN spreads.

Requirements:
- Dead trade → `dv01`, `gamma`, `theta` = `0.0`.
- The explain rate on a dead trade = `fixed_rate · 1e4`. Then `PV = pv01·(par−K) = 0`, and the maturity step's
  delta term equals `−PV(t−1)`, which is exactly the drop to zero.
- Preferred: change the ARBS `par_rate` dead-trade value from NaN to `fixed_rate·1e4`. Before doing so, grep every
  consumer: `tests/`, `notebooks/`, `skills/`, and `docs/v2/LIVE_ARBS_REPORT.md`.
- If anything depends on the NaN, add a separate `explain_rate` function instead. Then make the definition's market
  measure configurable (`swap_pnl_definition(rate_measure=...)`).
- Record which option you chose in `DECISIONS_LOG.md`.

### 2.6 Single currency only

With `result_ccy` set, `GenericEngine.run_backtest` rewrites every risk as `r(currency=...)`, and raises
`Unparameterised risk` for plain measures. `IRFwdRate`, `IRGammaParallel` and the new measures are plain, so the
swap definition cannot be combined with `result_ccy`. Document this, and test that the error is clean (§5.1 T-CCY).

### 2.7 The exact identity: the validator and the residual diagnostic

For any vanilla swap, on every date, `PV = pv01 · (par − K)` holds (K = strike in bp). The fixed leg is linear in K,
and par solves PV = 0 on the same schedule and fixings. Differencing the identity between two marks gives an
**exact** split:

```
ΔPV = pv01(t-1)·Δpar  +  (par(t-1) − K)·Δpv01  +  Δpv01·Δpar
      └ = PNL_delta ┘   └ annuity / moneyness ┘   └ ≈ PNL_gamma ┘
```

What this means for the residual you should expect:

- **Off-market trades carry a first-order residual.** Part of `(par−K)·Δpv01` is rate-driven:
  `(par−K)·∂pv01/∂par·Δpar`, which is first order in Δpar and grows with moneyness. The rest is time-driven, and
  θ explains it.
  - Order of magnitude: a 10y trade 100bp off-market leaves about 5% of `PNL_delta` in the residual.
  - This is the true cost of an annuity dv01. Do not hide it.
  - Tight residual bounds apply only to **near-ATM books** (monthly roll).
  - Single held trades get **diagnostic** tests: the residual must reconcile to the exact split (§5.1 T-RECON).
- The identity itself is a strong config check (§4, `swap_pv_identity`). It catches npv/dv01/par/strike unit or
  sign disagreements that no single-function check can see.

## 3. Deliverables and file ownership

| Tier | Files (create or modify) |
|---|---|
| T1 | `tests/toylib/rates.py` (toy functions, sloped curve); `tests/assets/toy_usd_irs.yaml` (new functions and mappings); `skills/pricebt-strategy-recipes/scripts/swap_pnl.py` (new); `skills/pricebt-verify-asset-config/scripts/check_asset.py` (new rows, allowlist); `tests/skills/test_skill_swap_pnl.py` (new); `tests/skills/test_skill_check_asset.py` (new rows); `tests/skills/fixtures/check_asset/*` (new broken fixtures) |
| T2 | `configs/assets/usd_sofr_ois_interest_rate_swap.yaml`; `tests/test_live_arbs_pnl.py` (new, `live_arbs`); `docs/v2/LIVE_ARBS_REPORT.md` (new section) |
| T3 | `skills/pricebt-strategy-intake/templates/strategy_spec.yaml`, `scripts/spec.py`; `skills/pricebt-strategy-recipes/scripts/recipes.py`; `skills/pricebt-strategy-workflow/scripts/run_study.py`; `skills/pricebt-tearsheet-report/scripts/tearsheet.py`; `skills/pricebt-spot-checks/scripts/spot_check.py`; docs listed in §6 |

### 3.1 Toy library (`tests/toylib/rates.py`, test-only)

- `ToyCurve` gains an optional `slope: float = 0.0`, so that
  `DF(d) = exp(−(zero_rate + slope·t)·t)` with `t = days/365`.
  With the default, existing behaviour is unchanged. Keep every existing test green.
- `TranslatedCurve(base, days)`: same interface (`ref_date`, `ccy`, `discount_factor`, `csa`), with
  `ref_date = base.ref_date + days` and `discount_factor(x) = base.discount_factor(x) / base.discount_factor(ref_date)`.
- `gamma(market, trade)` per §2.1: shift `zero_rate` by ±1bp using `dataclasses.replace`, and use the measured par
  move as the denominator.
- `theta(market, trade)` per §2.2: `(npv(TranslatedCurve(market, 1), trade) − npv(market, trade)) · 365`.
- `year_fraction(market)` per §2.3. `cash_paid_to_date(market, trade)` returns `0.0` (§2.4).
- `market_sloped(d, ccy, csa=None, slope=...)`: the same `z(d)` as `market`, plus a fixed slope. It is used by the
  sloped-world tests through a test-only config variant, written to `tmp_path` or fixtures.

### 3.2 Toy config (`tests/assets/toy_usd_irs.yaml`)

```yaml
  gamma:             {expr: 'tr.gamma(market, trade)', unit: ccy_per_bp2}
  theta:             {expr: 'tr.theta(market, trade)', unit: ccy}                  # ccy per YEAR
  year_fraction:     {expr: 'tr.year_fraction(market)', unit: decimal}             # intensive time coordinate
  cash_paid_to_date: {expr: 'tr.cash_paid_to_date(market, trade)', unit: ccy}
risk_measures:
  IRGammaParallel: gamma
  IRTheta: theta
  YearFraction: year_fraction
  CashPaidToDate: cash_paid_to_date
```

Leave `toy_eur_irs.yaml` without the new functions. It is the "explain not supported" negative case (§5.1 T-MISSING).

### 3.3 `skills/pricebt-strategy-recipes/scripts/swap_pnl.py` (new)

- Three custom measures, defined in the skill, **not** in `src`:
  - `IRTheta = RiskMeasure(name="IRTheta", asset_class=AssetClass.Rates, measure_type="Theta")`;
  - `YearFraction`;
  - `CashPaidToDate`.

  Check the `RiskMeasure` constructor in `src/pricebt/risk/__init__.py:112`. Use module-level singletons, because
  results are keyed by the measure object.
- `swap_pnl_definition(rate_unit="bp", gamma=True, carry=True, rate_measure=IRFwdRate) -> PnlDefinition`:
  - `PNL_delta`: `IRDeltaParallel` × `rate_measure`. It **must** be a scalar-form delta. Bare `IRDelta` returns the
    bucketed frame.
  - `PNL_gamma`: `IRGammaParallel` × `rate_measure`, `second_order=True`.
  - `PNL_carry`: `IRTheta` × `YearFraction`.
  - `scaling_factor` converts rate units to bp: `bp` → 1, `pct` → 100, `decimal` → 1e4. Gamma uses the square of the
    same factor.
  - `rate_unit` can be read from the config instead: `rate_unit_for(asset_config)`.
- `explain_table(bt, cash=True) -> pandas.DataFrame`, indexed by date. It rebuilds, **independently of
  `pnl_explain()`'s loop**, the held-book actual P&L:
  - For each step, sum over the instruments in `bt.results[t−1]`:
    - `PV(t) − PV(t−1)`;
    - `cash_paid_to_date(t) − cash_paid_to_date(t−1)` if `CashPaidToDate` was computed.
  - Use `bt.trade_exit_risk_results[t]` for instruments exited at t, mirroring `pnl_explain()`'s lookup.
  - Columns: `actual_dpv`, `cash`, `economic`, `PNL_delta`, `PNL_gamma`, `PNL_carry`, `explained`, `residual`.
    The attribution columns are the **per-step** differences of `bt.pnl_explain()`'s cumulative dicts.
- `explain_stats(table) -> dict`:
  - per-component totals;
  - `r2` (explained vs economic, daily);
  - `residual_share = var(residual) / var(economic)`;
  - `abs_residual_total / abs_economic_total`;
  - the worst-residual date.
- `exact_split(bt)` (the diagnostic in §2.7): per step, the three exact terms, computed from per-trade `npv`, `dv01`,
  `par` and the resolved strike. Tests use it to reconcile the residual.
- Wiring note for users: pass `pnl_explain=swap_pnl_definition()` to `GenericEngine().run_backtest(...)`. To get the
  cash column, add `CashPaidToDate` to `risks=`.

### 3.4 Checker rows (`check_asset.py`, swap pack)

- Allowlist `IRTheta`, `YearFraction` and `CashPaidToDate` in the "not a pricebt.risk measure name" WARN
  (`check_asset.py:312`). Import the names from `swap_pnl.py`; do not duplicate the strings.
- `swap_pv_identity` (all swap configs that expose `fixed_rate` in the resolved terms):
  - Condition: `|npv − dv01·(par − fixed_rate·1e4)| ≤ 1e-6·|N| + 1e-3·|dv01|`.
  - Evaluate it at d1 (the ATM trade) **and** at d2 (the same trade, now off-market).
  - FAIL if it does not hold.
- `swap_gamma` (if `IRGammaParallel` is mapped):
  - Checks:
    - payer Γ < 0;
    - receiver ≈ −payer (1%);
    - `0.2 ≤ |Γ| / (|dv01|·T·1e-4) ≤ 2`.
  - **Half-gamma probe:**
    - Find two business days, up to 10 apart and within 30 of d1, where the ATM trade's |Δpar| ≥ 3bp.
      They need not be consecutive: over 10 days, aging moves an annuity dv01 by only about z·k/365 (≈0.08%).
      A 15bp move changes it by several percent, so the estimate stays clean. The toy's daily move is capped
      at about 2.5bp, so a consecutive-day probe would always SKIP there.
    - Compute `Γ_est = 2·Δdv01/Δpar` on the same resolved trade.
    - Ratio Γ/Γ_est in [0.7, 1.4] → PASS. In [0.4, 0.6] → FAIL, with "looks like gamma from dv01 differences
      (half-gamma trap)". Otherwise → WARN. No qualifying pair → SKIP.
- `swap_theta` (if `IRTheta` mapped): receiver ≈ −payer. `|θ| ≤ 1000·|dv01|` (a carry above 1000bp per year means
  a unit error), otherwise FAIL.
- `year_fraction` (if `YearFraction` mapped):
  - `Δ` between d1 and d2 equals `(d2−d1).days/365` within 1e-12;
  - the value is identical for a notional ×3 instrument (intensive).
- `cash_paid_to_date` (if mapped): the fresh ATM trade at d1 → 0. Receiver = −payer at d2.

### 3.5 ARBS config (T2)

Add these to `code:` and `functions:`. Every function returns 0.0 when dead (§2.5).

- **`gamma`**, per §2.1:
  - Shift the dense curve by ±1bp. Verify rateslib 2.7.1's `Curve.shift` signature on the installed version first.
  - Wrap each shifted handle in an `RLIRSwapCurve`, the way `_risk_model` does.
  - Price the **remarked** trade: `npv` on each curve, and `fair_rate` on each curve for the denominator.
  - Memoise the shifted handles per market, in `m.__dict__`.
- **`theta`**, per §2.2: the curve translated by 1 calendar day.
  - Verify whether `Curve.translate` exists on 2.7.1 and what it does.
  - Fallback: build an `rl.Curve` with nodes `DF'(x) = DF(x)/DF(t+1d)`, using the same convention and calendar.
  - **Pitfall:** the float period accruing over [t, t+1d] needs a SOFR fixing for t. The translated curve starts at
    t+1d, so it cannot forecast t. Append the curve-implied overnight forward for t to a *copy* of the fixings.
    Never write to ARBS's fixings cache.
- **`year_fraction`**: identical to the toy version.
- **`cash_paid_to_date`**:
  - Use `remark(m, t).cashflows(curves=m.handle())` (research/06 around line 251). Sum the holder-signed cashflows
    whose payment date is on or before the reference date, across both legs.
  - Verify the column names on the installed version.
  - Cross-check one fixed-leg coupon by hand: `N·K·τ` (ACT/360).
- **The dead-trade explain rate**, per §2.5.
- Keep `dv01`, `npv` and `par_rate` on a live trade unchanged.

### 3.6 Fallback if the measure design fights you

If `YearFraction` with `unit: decimal` trips something you cannot fix outside `src`, compute carry inside
`explain_table` instead: `IRTheta(t−1) · Δdays/365`, taken from a requested `IRTheta` risk column. The time measure
is then not needed. The trade-off: `bt.pnl_explain()` returns only delta and gamma, and `explain_table` adds carry.
Record this in `DECISIONS_LOG.md` if you use it.

## 4. Tier gates

- **T1 gate (toy):**
  - every T1 test in §5.1 to §5.4 passes;
  - every listed mutation fails its named test;
  - the full suite is green;
  - `python tools/sync_agent_skills.py --check` passes;
  - `check_asset.py tests/assets/toy_usd_irs.yaml --fx tests/assets/toy_fx.yaml` has no FAIL, and the new rows appear.
- **T2 gate (ARBS):**
  - `PRICEBT_LIVE_ARBS=1` with `tests/test_live_arbs_pnl.py` is green;
  - `check_asset.py configs/assets/usd_sofr_ois_interest_rate_swap.yaml --date 2024-01-03 --date 2024-02-05` has no
    FAIL;
  - the full non-live suite is still green, with ARBS never imported (the guards prove it);
  - `LIVE_ARBS_REPORT.md` has the numbers.
- **T3 gate:**
  - `run_study.py` on the toy example spec with `pnl_explain.enabled: true` writes `pnl_explain.csv` and the tearsheet
    section;
  - the new spot check appears;
  - the full suite is green;
  - the skills library tests are green.

## 5. Tests: the core of this work

Put every toy test in `tests/skills/test_skill_swap_pnl.py`, unless noted. Test worlds are made by monkeypatching
toy parameters inside a test; the `isolation` fixture resets recorders only, so restore anything you patch. Use
`monkeypatch`.

- **Frozen world:** `tr._CCY_PARAMS["USD"] = (0.03, 0.0, 252)`, so the zero rate is constant.
- **Shock world:** replace `tr._zero_rate` with a step function (for example +100bp from a given date).
- **Sloped world:** `market_sloped`.

### 5.1 Toy: definitions and plumbing

| ID | Test | Known answer / assertion | Mutation that must fail it |
|---|---|---|---|
| T-GAMMA-1 | Γ against an independent second difference for ATM payer and receiver, 2y/10y/30y | the test computes its own `(npv₊+npv₋−2npv₀)/((p₊−p₋)/2)²` with h=1bp; agree within 1e-9 relative; receiver = −payer; payer < 0 | flip the sign; drop the square in the denominator |
| T-GAMMA-2 | **half-gamma trap** | an alternative Γ from `(dv01₊ − dv01₋)/(p₊ − p₋)` is ≈ Γ/2 (ratio in [0.45, 0.55]) at the money: documents *why* | n/a (documents the trap) |
| T-GAMMA-3 | Taylor order on an instant shock (same date, z → z+Δ for Δ = 25/50/100bp, ATM 10y) | residual with gamma ≤ 10% of residual delta-only; doubling Δ multiplies the with-gamma residual by 6–10 (third order) | use half gamma → the 10% bound fails |
| T-THETA-1 | frozen world, **off-market** payer (K = 2% vs z = 3%; an ATM trade has θ = 0) | `θ == npv·(exp(z/365)−1)·365` within 1e-9 relative (DFs scale by `exp(z·δ)`) | per-day θ (drop ·365) |
| T-THETA-2 | frozen world: par invariance | `par(t) == par(t0)` to 1e-9 bp over 1 year of marks | n/a |
| T-YF | `year_fraction` | Δ over Fri→Mon = 3/365; identical for notional ×3 | declare `unit: number` → notional-×3 differs |
| T-CASH | toy `cash_paid_to_date ≡ 0` for all dates | exact 0 | n/a |
| T-DEF | `swap_pnl_definition` structure and units | scalar-form delta measure; gamma `second_order`; scaling 1/100/1e4 for bp/pct/decimal (gamma squared) | bare `IRDelta` → the backtest raises or returns frames: assert a clear error |
| T-MISSING | definition on `toy_eur_irs` (no gamma/theta) | clean `ConfigError` naming the missing measure; `gamma=False, carry=False` works (delta only) | n/a |
| T-CCY | definition plus `result_ccy="EUR"` | raises cleanly (`Unparameterised risk`); the message is surfaced | n/a |
| T-RECON | single held off-market trade, daily, 1 year | `residual == exact_split` pieces minus the attribution pieces, to 1e-8 relative: the residual is fully accounted for | break `explain_table`'s exit-result lookup |

### 5.2 Toy: backtest-level attribution

Use real `GenericEngine` runs. The expected values come from the test worlds' closed forms, never from a prior run.

| ID | Scenario | Assertions | Mutation |
|---|---|---|---|
| T-FROZEN | frozen world, off-market payer held 1y, daily | `|PNL_delta| ≤ |dv01|·1e-9` and `|PNL_gamma|` ≈ 0 (Δpar = 0 algebraically, but only to floating-point in practice); carry ≈ economic, per step `|residual| ≤ 2·|npv|·(z·k/365)²` (k = calendar days in the step) | per-day θ; carry attribute on the wrong measure |
| T-SHOCK | shock world: +100bp jump on one date, ATM 10y payer | on the jump step, `|residual| ≤ 1%` of `|PNL_delta|`, and delta+gamma beats delta-only by more than 10× | half gamma |
| T-SLOPE-1 | sloped world, static shape (z and slope constant, so real roll-down), ATM payer 1y daily | residual per step ≤ the target ceiling; carry ≠ 0; delta picks up roll-down (Δpar ≠ 0, sign per slope) | n/a |
| T-SLOPE-2 | **double-count non-vacuity**: the same, with θ computed on the static-shape roll instead of the translation | residual drifts systematically: `Σresidual ≈ −Σ pv01·Δpar_roll` within 5%; assert the correct θ does *not* show this | this *is* the mutation test for §2.2 |
| T-MONTHLY | normal toy world, monthly marks (~50bp moves), ATM 10y | gamma term > 1% of delta on the biggest steps; with-gamma residual < delta-only residual on each of the 3 biggest steps | half gamma |
| T-ROLL | momentum/periodic roll strategy (monthly new ATM 10y, `trade_duration` 1m), daily, 2024 | `r2 ≥ R2_TARGET` and `residual_share ≤ RS_TARGET` (§5.6); exits and entries handled | break the exit-results branch in `explain_table` |
| T-LEDGER | the T-ROLL run | **ledger tie-out:** `Σ actual_dpv == (Total[-1] − Total[0]) − (costs[-1] − costs[0])`, no cash accrual, to 1e-6 relative; independent of the attribution | an off-by-one step in `explain_table` |
| T-SCALE | `AddScaledTradeAction`, quantity 2.5 | every attribution and actual column = 2.5 × the unit-trade run, to 1e-9 | `year_fraction` extensive |
| T-SYM | payer vs receiver, the same run | every column negates exactly | n/a |
| T-HOLES | toy `HOLES` inside the window (missing markets, `missing_market='drop'`) | carry step spans the gap (`Δyf = k/365`); no NaN anywhere; ledger tie-out holds | n/a |
| T-NAN | any NaN in any column | a guard assertion used by every backtest test: `np.isfinite` on the whole table | n/a |
| T-CONSIST | `explain_table` attribution vs raw `bt.pnl_explain()` | the cumulative sums of the per-step columns equal the dicts exactly | n/a |

### 5.3 Toy: checker rows (`tests/skills/test_skill_check_asset.py`)

- `toy_usd_irs.yaml`:
  - no FAIL;
  - rows `swap_pv_identity`, `swap_gamma`, `swap_theta`, `year_fraction` and `cash_paid_to_date` present and PASS;
  - the half-gamma probe is not SKIP. It uses pairs up to 10 business days apart (§3.4); if it still SKIPs, pass
    `--date`s that qualify. Do not lower the 3bp threshold.
- New broken fixtures, each expected to FAIL its named row:
  - `bad_half_gamma.yaml` (gamma from dv01 differences) → `swap_gamma`;
  - `bad_theta_per_day.yaml` → `swap_theta` if it is caught. If per-day θ slips through the magnitude band
    (it is smaller, not bigger), assert it is caught by **T-THETA-1** instead, and record that the checker cannot see it;
  - `bad_year_fraction_extensive.yaml` → `year_fraction`;
  - `bad_identity_par_pct.yaml` (par in pct declared bp) → `swap_pv_identity` **and** `swap_par_rate_atm`.
- The meridian example still passes, and its three mistakes still FAIL their rows.

### 5.4 Toy: performance

The new toy tests must add under 60 s to the core suite. Gamma and theta cost 3 extra npv calls per trade-date.
Keep the date ranges to one year daily at most.

### 5.5 Live ARBS (`tests/test_live_arbs_pnl.py`, `live_arbs`, lazy imports only)

Every test builds its backtest through the ARBS config only. Use dates in 2024, well inside the rails. Record every
number (not just pass/fail) in `docs/v2/LIVE_ARBS_REPORT.md`, in a new section "P&L explain".

| ID | Scenario | Assertions |
|---|---|---|
| A-IDENT | 10y payer, resolved 2024-01-03, marked daily through 2024 | `PV == pv01·(par − K)` every date, to `1e-6·N + 1e-3·|pv01|`. If it fails, that is a config finding: fix the config and record it |
| A-GAMMA-FD | same trade, 5 sampled dates | Γ(config) vs the test's own second difference via `Curve.shift` ±1bp, within 1%; and vs `rl.Portfolio([...]).gamma(solver=sv)` (the par-space cross-gamma summed), from `_risk_model`, within 5%; payer < 0, receiver = −payer |
| A-THETA | same trade, 5 dates | θ receiver = −payer; par under the 1-day translation changes by < 0.05bp (no roll-down leaks into carry); cross-check against ARBS `carry_bps_running(·,"1m")`: `θ·(days to 1m)/365` has the same sign as `−carry_1m·pv01` and agrees within 30% (a static-curve vs constant-forward convention gap is expected; record the ratio) |
| A-CASH | 10y payer 2024-01-03 → 2025-03-31 (spans its first annual coupon plus a 2b lag) | on the payment step, the residual **without** cash ≈ `−Δcash` within 2% of the coupon; **with** cash, the payment-step residual ≤ the 99th percentile of normal-day residuals; fixed-leg coupon = `N·K·τ` by hand (ACT/360) |
| A-DAILY | single 10y ATM payer, daily 2024 (**diagnostic**: the trade drifts about 80bp off-market, so §2.7's first-order moneyness residual is several % of delta) | hard: the residual reconciles to `exact_split` (T-RECON analogue, 1e-6 relative). **Record, do not assert:** R² on non-coupon days, and residual/economic by moneyness bucket. The hard R² target applies only to A-ROLL (near-ATM by construction) |
| A-GAMMA-USE | 30y and 10y ATM payers, daily 2024 | on the 20 largest-|Δpar| days, RMS residual with gamma < RMS without gamma, for both tenors; record both RMS values |
| A-ROLL | periodic roll (monthly new ATM 10y, `trade_duration` 1m), daily 2024 | `r2 ≥ R2_TARGET_ARBS`; `residual_share ≤ RS_TARGET_ARBS`; ledger tie-out (T-LEDGER analogue) |
| A-MATURE | 1y payer from 2024-01-03, marked to 2025-03-31 | no NaN anywhere across maturity and settlement; the maturity step's delta ≈ `−PV(t−1)` (§2.5); cumulative series finite to the end |
| A-FWD | 1y-forward 5y payer | θ, Γ and cash behave before the effective date (cash 0 until the first payment); no exceptions |
| A-SYM | receiver vs payer, the A-DAILY run | columns negate within 1e-9 relative |
| A-CHECK | `check_asset.py` on the ARBS config | no FAIL; every new row present |
| A-TIME | wall time of each live test | recorded in the report; whole file < 15 min. If gamma or theta dominate, memoise per market; never lower coverage silently |

### 5.6 Calibrating thresholds (targets marked `*_TARGET`)

The exact tests (1e-8 to 1e-12) are fixed. The ceilings below are **targets**:

- `R2_TARGET` ≥ 0.9999 and `RS_TARGET` ≤ 1e-3 (toy, near-ATM roll, daily);
- `R2_TARGET_ARBS` ≥ 0.999 and `RS_TARGET_ARBS` ≤ 1e-2 (ARBS, near-ATM roll, daily).

Procedure:

1. Run once. Look at the observed value.
2. Set the assertion at 2× headroom over what you observed, but never looser than the target.
3. If the observed value misses the target, **do not loosen the target**:
   - Diagnose it with `exact_split`. Is it moneyness (expected, §2.7)? Coupons? A unit error? Timing?
   - Fix the cause if it is a bug.
   - If it is inherent, document the observed value, the diagnosis and the new bound in `DECISIONS_LOG.md` and in
     the tier report.
4. Never silently widen a bound.

## 6. Docs to update (T3, except where noted)

- `docs/v2/ASSET_CONFIG_GUIDE.md` (T1): a section "P&L explain functions" covering the four functions, their units,
  the §2.1 to §2.5 conventions, and the NaN rule.
- `skills/pricebt-asset-config-cookbook/references/patterns.md` (T1): a new pattern with toy and ARBS-shaped
  snippets, plus the traps (half gamma, per-day θ, extensive time, static-roll θ, NaN).
- `skills/pricebt-verify-asset-config/SKILL.md` (T1): table rows for each new check.
- `skills/pricebt-strategy-recipes/SKILL.md` and `references/construct-cheatsheet.md` (T1): how to use
  `swap_pnl_definition` and `explain_table`.
- `skills/pricebt-tearsheet-report/references/metrics-definitions.md` (T3): the attribution metrics.
- `skills/pricebt-adversarial-review/references/checklist.md` (T3): one item. "Explain residual large? Check
  moneyness (§2.7), coupon cash, and gamma units before trusting the attribution."
- If any skill frontmatter changes, run `python tools/sync_agent_skills.py`.

## 7. Tier 3: workflow integration

- The spec template gains:
  ```yaml
  pnl_explain: {enabled: false, gamma: true, carry: true, cash: auto}
  ```
  `spec.py` adds defaults and validation. `cash: auto` means "add `CashPaidToDate` to the risks if the config maps it".
- `recipes.run` passes `pnl_explain=swap_pnl_definition(...)`, plus the extra risks, when enabled and the primary is
  an `IRSwap`. Otherwise it records a note.
- `run_study.py` writes `pnl_explain.csv` (the `explain_table`) and `pnl_explain.json` (the `explain_stats`), and
  passes both to the tearsheet.
- Tearsheet: a "P&L attribution" section with a component-totals table, a cumulative stacked chart, and a
  residual-share line with a WARN badge above `RS_TARGET`.
- `spot_check.py` gains `check_pnl_attribution`: PASS if the residual share is ≤ the target, WARN if ≤ 10×, FAIL
  otherwise. It is INFO when explain is not enabled.
- Tests: extend `test_skill_run_study.py` and `test_skill_tearsheet.py`. The example spec keeps `enabled: false`;
  add a second example spec with it on.

## 8. Commits

One commit per tier, with a body that lists the tests and mutations. Messages:
- `pnl-explain: T1 toy functions, swap_pnl_definition, explain_table, checker rows`;
- `pnl-explain: T2 ARBS gamma/theta/cash functions, live tests, report`;
- `pnl-explain: T3 spec flag, tearsheet section, spot check`.

End each message with the attribution lines the session's system reminder gives you. Before and after each commit:
`git -C <worktree> status -sb | head -1` and `git -C <worktree> log --oneline -1`. Do not push.

## 9. Autonomous decision rules

- A convention here conflicts with what the code shows: **the code wins on facts**, and this plan wins on intent.
  Record the conflict in `DECISIONS_LOG.md` and pick the option that keeps `src` untouched.
- A rateslib API differs from what §3.5 assumes: use the fallback given, and record the version and the call you used.
- A live ARBS test cannot run (import failure, store missing): do not work around the rails. Mark T2 blocked in the
  report, with the error, and finish T1 and T3.
- Running out of time: T1 and T2 matter more than T3. Never ship T3 without T1.
