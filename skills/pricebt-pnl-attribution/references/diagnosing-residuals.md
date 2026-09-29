# Diagnosing the residual

`residual_pnl = economic_pnl − explained_pnl`, per step of `backtest.pnl_explain_table()`. A correct contract on a smooth market leaves only third-order and time cross terms. The toy demos give r2 ≈ 1 and a residual variance share of about 1e-7 (swaption) and 1e-6 (bond). The grade reads the worst of that variance share, 1 − r2 and `|Σ residual| / Σ|economic|`: the variance share alone cannot see a steady bias (a sign-flipped `Theta` leaves it at 1.7% while the residual is 74% of the P&L). Anything larger has a cause, and the cause is almost always one of the entries below. Diagnose before you report the components: a component total is only as good as the residual next to it.

## Read the table first

```python
import sys; sys.path.insert(0, "skills/pricebt-pnl-attribution/scripts")
import attribution, pandas as pd
table, cumulative = attribution.attribution_frames(bt)
stats = attribution.explain_stats(table)
print(attribution.grade(stats), attribution.grade_reason(stats))
print(stats["signatures"])                                           # {attribute: implied scale}, below
corr = {a: c for a, c in stats["residual_corr"].items() if c is not None}
suspect = max(corr, key=lambda a: abs(corr[a]))                      # what the residual co-moves with
scale = 1 + stats["totals"]["residual_pnl"] / stats["totals"][suspect]   # its implied scale, below
worst = table.loc[[stats["worst_date"]]]                             # the worst step, every column
days = pd.Series(pd.to_datetime(table.index)).diff().dt.days.to_numpy()  # step length (weekends are 3)
```

The implied scale `1 + residual / X` is the factor X must be multiplied by to absorb the residual. `explain_stats` lists it under `signatures` when |corr| ≥ 0.9 and it reads as one of the first four rows; the grade then FAILs a material residual and names X.

| Signature | Reading |
|---|---|
| `corr(residual, X)` ≈ −1, `residual / X` ≈ **−2**, scale ≈ **−1** | X has the wrong **sign** (the flipped-vega test: residual 1,877 = −2 × −939) |
| `corr` ≈ +1, `residual / X` ≈ **+1**, scale ≈ **2** | X is **half** its true size (half gamma) |
| `corr` ≈ −1, `residual / X` ≈ **−1**, scale ≈ **0** | X is **far too large**: per-year theta (−364/365), a ×100 or ×1e4 unit error |
| `corr` ≈ +1, scale ≥ **20** (≈ 100) | X is **far too small**: its level in pct (or decimal) declared bp, a greek per 1% declared per bp |
| residual concentrated on **one date** | an event: a coupon, an exit, a data glitch, expiry |
| residual per step ∝ `days` (Mondays 3×) | a **time** term: the theta definition, or a time cross term |
| residual ∝ Δr and growing with \|F − K\| | a **fixed-annuity delta** off the money |
| NaN (`stats["finite"]` is False) | NaN poisoning: find the first NaN row |

In a toy or trending market, several attributes drift together, so more than one correlation is high. Prefer the attribute whose `|corr|` is closest to 1 **and** whose total ratio matches a signature. Then confirm with one step computed by hand (the last section).

## The taxonomy

### 1. Moneyness with a fixed-annuity delta
- **Symptom.** The residual co-moves with `PNL_delta` and scales with |F − K|: near zero for a trade struck at the money that stays there, large once the book drifts off-market or is entered off-market.
- **Cause.** The `IRDelta` scalar is `N·A` (an annuity pv01, `∂PV/∂F` with the annuity held), not the total own-rate derivative. For a swap, `PV = N·A·(F − K)`, so the missing first-order term is `N·(F − K)·ΔA`.
- **Probe.** At an off-market strike, compare `IRDelta` with `[PV(+h) − PV(−h)] / [r(+h) − r(−h)]`.
- **Fix.** Map the total derivative (definitions.md §3). The shipped toy and ARBS swap configs are at-the-money exact by design (`docs/v2/MERGE_NOTES_pnl_explain.md`), so an off-market swap book on them shows this residual.

### 2. Half gamma
- **Symptom.** `residual / PNL_gamma` ≈ +1. The residual has gamma's sign on every step and is proportional to Δr².
- **Cause.** Either `IRGammaParallel` = `d(pv01)/dr` of an annuity pv01 (half the second derivative at the money), or a finite-difference gamma missing the chain-rule term `−Δ·(r₊ + r₋ − 2r₀)`. The second is the in-flight branch's finding (research note R14): it biases gamma by −4% to −34%, depending on tenor.
- **Probe.** At the money, Γ ≈ 2·Δpv01/Δr. Recompute Γ from three bumps with the chain rule (`own_rate_greeks` in `tests/toylib/irrisk.py`).

### 3. Gamma or vega in the wrong unit
- **Symptom.** `PNL_gamma` or `VegaPnL` is 1e4 or 1e8 too large (ratio −1), or negligible (the residual carries the whole term).
- **Cause.** The declared unit (`ccy_per_bp2`, `ccy_per_bp`) is right but the number is per 1% or per unit of rate. For example, a Black vega per 1% of lognormal vol mapped as per bp of normal vol. The unit check cannot see this: it checks the *level's* unit, not the greek's magnitude.
- **Probe.** Bump the level by ±1bp in your library and compare. Run the verify-asset-config checker: `ir_taylor` FAILs a greek ×100 or ×1e4.

### 4. Per-year theta
- **Symptom.** `PNL_theta` about 365× plausible; `residual / PNL_theta` ≈ −364/365; three times as large on Monday steps.
- **Cause.** `Theta` mapped to a per-year carry (the in-flight `IRTheta` is per year: `IRTheta = 365 × Theta`).
- **What catches it.** Not the loader or the DEV-E21 unit check: `PNL_theta` has no unit check (its level is `ExpiryInYears`), and `ccy` is `Theta`'s unit either way. `check_asset.py`'s `ir_theta` FAILs it ("looks per year") and so does `ir_taylor`; this grade FAILs it too (implied scale ≈ 0.003).
- **Probe.** `Theta` ≈ one day's `Price` change on the translated curve (definitions.md §3). For a 10y swap, |Theta| is a few days of coupon accrual, not a year.

### 5. Rolled-curve theta (double count)
- **Symptom.** A persistent drift in the residual that co-moves with `PNL_theta`. Its sign follows the curve slope, and it vanishes on a flat curve. The toy curves are flat, so the toys cannot show it.
- **Cause.** `Theta` was computed by **rolling** the curve (the same zero rates at the same tenors one day later). That moves the forwards, and therefore the own rate r. The roll-down is then attributed twice: once inside theta, and again by `PNL_delta` through Δr.
- **Fix.** Translate the curve, keeping the forwards: `DF'(x) = DF(x)/DF(t+1d)` (IR_RISK_DESIGN R2-4).

### 6. Coupons: `Cashflows` vs total-return `Price`
- **Symptom.** The residual on the payment step is about −coupon, `worst_date` is the coupon date, and the grade is FAIL. The bond demo without `Cashflows` shows −21,250 on 2024-05-15.
- **Cause.** `Price` drops a paid coupon (it values only the flows still to come), but `Cashflows` is not mapped, or not among the run's `risks`.
- **Fix.** Map `Cashflows` (a frame of the flows `Price` will drop, holder-signed) and run with `risks=[Cashflows]`. `cashflow_pnl` then carries the coupon and `economic_pnl` is smooth.
- **Total-return `Price`** (it never drops a flow, like the toy swap): `Cashflows` is an empty frame and the coupon is already in `actual_pnl`.
- **`Theta` must follow the same total-return rule**: `Price(t+1d) + cash paid in (t, t+1d] − Price(t)`. A theta that omits the cash term while `Price` drops the coupon shows a `PNL_theta` spike of about −coupon on the step before the payment, and a residual of about +coupon.

### 7. Unit mismatch
- **Symptom.** `ValueError: PNL_delta: IRFwdRate on <instrument> has unit {'pct': 1}; the definition expects bp` (DEV-E21) when you read the table.
- **Cause.** A definition built for bp (for example `swaption_pnl_definition()` with its defaults) on a percent config.
- **Fix.** `attribution.definition_for(session)` reads the units from the configs, or pass `rate_unit='pct'` yourself. A book with different units on different assets is refused: convert in the configs.
- **What the check cannot see:** a level *declared* `bp` that returns pct or decimals. A rate in decimal puts `PNL_delta` off by 1e4 (ratio ≈ −1); a vol in pct leaves the vega P&L in the residual (implied scale ≈ 100). `check_asset.py` catches them: `swap_par_rate_atm` and `swaption_fwd_unit` FAIL a rate in the wrong unit, and `bond_yield_unit` and `swaption_vol_unit` WARN on a percent and FAIL on a decimal.

### 8. NaN poisoning
- **Symptom.** `stats["finite"]` is False. The spot check FAILs and names the columns. `pnl_explain()` is NaN from the first bad step to the end.
- **Cause.** A level or greek that is NaN on some held date. Usually a dead instrument: a matured swap's par rate, a swaption after expiry, a bond past maturity. Sometimes a calc that failed on a holiday.
- **Fix.** Sensitivities of a dead instrument are 0.0, and **levels continue past death** (R2-7): the last live value, or the underlying's forward. The shipped ARBS swap config's par rate is NaN after maturity; this is known and recorded in `docs/v2/MERGE_NOTES_pnl_explain.md`.

### 9. Non-parallel curve moves (ladder vs own rate)
- **What own-rate attribution captures.** Any curve move, through the instrument's own rate r. A bond's price is a function of its yield alone, so a twist is fully captured.
- **What it misses.** PV changes that do not go through r:
  - the annuity or discounting change of an off-market swap or swaption when the curve twists (`(F − K)·ΔA`, as in §1);
  - a swaption whose annuity moves differently from the parallel bump that defined its delta.
- **Symptom:** a residual that co-moves with the curve's slope changes, not with Δr.
- **Scalar/level mismatch.** If your `IRDelta` scalar is the parallel *curve* dv01 (the sum of the ladder, `∂PV/∂s` for a zero-rate shift s) rather than `∂PV/∂r`, the definition multiplies it by a move in r. The residual is then about `(1 − dr/ds)·PNL_delta`. Compute the scalar as ΔPV/Δr on the same bump.
- **The bucketed ladder is for hedging and reporting**; it does not enter `IRDeltaParallel × IRFwdRate`. For key-rate attribution, see definitions.md §7, or use `PnlExplain` rows ([instrument-level.md](instrument-level.md)).

### 10. Time cross terms, and the breakdown near expiry
- **Symptom.** A small residual proportional to the step's day count, co-moving with `PNL_theta`. It grows as expiry approaches, and is large in the last days of an at-the-money option, where gamma and theta explode and third-order terms dominate.
- **Cause.** The definition has no charm (`∂Δ/∂t·Δr·Δt`), veta (`∂vega/∂t·Δσ·Δt`) or `½·Θ_t·Δt²` term, and none of third order. "Exact to second order" was withdrawn (IR_RISK_DESIGN R2-1).
- **Example.** The bond demo leaves −0.58 per one-day step and −1.73 per three-day step, with `corr(residual, PNL_theta)` = −0.99: 0.03% of the economic P&L.
- **What the engine's tests allow.** They bound each step's residual by an explicit Taylor bound (R2-18) and exclude steps ending within 2 calendar days of expiry.
- **Action.** Report it. Use a daily grid rather than a weekly one. Never "fix" it by scaling theta.

### 11. Weekend and holiday steps
- **Theta is multiplied by calendar days.** `ExpiryInYears` counts calendar days, so a Friday → Monday step attributes 3 × `Theta(Friday)`. That is right for linear decay; the time cross terms of §10 grow with the step.
- **The step across expiry** attributes theta only up to expiry (`ExpiryInYears` floors at 0).
- **After expiry, an exercised swaption is the underlying swap** (R2-7), with a non-zero carry, but `PNL_theta = Theta × −365·ΔExpiryInYears` is 0 once `ExpiryInYears` is 0. The swap's carry goes to the residual. A 1mm 1m payer struck ATM−100 on the toy leaves +10.87 per one-day step (+32.5 over a weekend) after exercise; `PNL_delta` still explains the rate moves. Exit at expiry (`AddTradeAction(..., 'expiration_date')`), or book the exercised swap as its own trade, so its own `ExpiryInYears` carries the theta.
- **A monthly grid** makes every step long: the residual grows roughly with Δt² and with the size of the moves.

### 12. Sign errors
- **Symptom.** Ratio −2 and `corr` ≈ −1 on one attribute.
- **Causes.**
  - `buy_sell` not folded into the sign of a greek: check `npv(Sell) == −npv(Buy)` and every greek flips with it.
  - A receiver's delta quoted positive.
  - A vega taken from a library that reports "vega per short option".

### 13. Hedged books and additivity
- **The attribution** of a delta-hedged book is exact per leg, each on its own rate.
- **The hedge** is approximate across instrument types (R2-2), so the book's delta P&L does not net to zero; that is real hedge slippage, not a residual.
- **A residual** that appears only in the hedged run points at the hedge instruments' configs. Run each leg alone.

### 14. `actual_pnl` does not match `Total`
- `pnl_explain_table` sees only the held book's price changes and the paid cash. It does not see:
  - transaction costs;
  - cash accrual;
  - entry at a price other than PV;
  - the engine's non-booking of coupons (gs parity).
- **Reconcile** with `Σ actual_pnl = Total(end) − Total(start)` on a no-cost, no-accrual run (definitions.md §4).

## Confirm on one step by hand

1. **Take the worst step** (t−1, t) and one instrument held at t−1.
2. **Take the greeks and levels at t−1 and t** from `bt.results[t−1][inst]` and `bt.results[t][inst]` (or from `bt.trade_exit_risk_results[t]` if the instrument exits at t).
3. **Recompute each attribute's formula** (definitions.md §1) and compare with the row.
4. **Reprice the instrument in your own library** on t's market with t−1's valuation date, then on t's market with t's valuation date. The first difference is the market move; the second is the time move. Compare each with the attributes that should explain it.
5. **Cross-check with `PnlExplain`.** The same split is what `PnlExplain(CloseMarket(date=t))` under `PricingContext(pricing_date=t−1)` returns, by risk factor ([instrument-level.md](instrument-level.md)).
