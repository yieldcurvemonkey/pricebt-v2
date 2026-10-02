# P&L definitions: what pricebt multiplies, and what your library must supply

Everything here is implemented in `src/pricebt/backtests/backtest_objects.py` (`PnlAttribute`, `PnlDefinition`, `BackTest.pnl_explain`, `BackTest.pnl_explain_table`, `ir_pnl_definition`, `swaption_pnl_definition`, `bond_pnl_definition`). The measure contract it relies on is `src/pricebt/risk/contracts.py`, specified in [`IR_RISK_DESIGN.md`](../../../docs/v2/IR_RISK_DESIGN.md) section 00 (which overrides the rest of that document) and section 2.2.

## 1. The objects

```python
PnlAttribute(attribute_name, attribute_metric, market_data_metric, scaling_factor, second_order=False,
             cross_market_data_metric=None, market_data_unit=None, cross_market_data_unit=None)
PnlDefinition(attributes)
```

- The first five fields are gs's, in gs's order, so positional gs code runs unchanged.
- The last three are pricebt additions:
  - `cross_market_data_metric` (DEV-E19) makes a cross term expressible, for example vanna = `R·Δr·Δσ`;
  - `market_data_unit` and `cross_market_data_unit` (DEV-E21) make every level read check its unit. A level whose `FloatWithInfo.unit` differs raises `ValueError("<attribute>: <measure> on <instrument> has unit {...}; the definition expects <u>")`.
- `second_order=True` together with a cross metric raises `ValueError`.
- `PnlDefinition.get_risks()` lists every metric of every attribute. `run_backtest(pnl_explain=definition)` adds them to the backtest's risks, so **every one is priced for every held instrument on every date**.

Each attribute contributes, per step (t−1 → t) and per instrument held at t−1, with `R` = its `attribute_metric` at t−1, `m` its level, and `k` its `scaling_factor`:

| form | step P&L |
|---|---|
| first order | `k · R(t−1) · (m(t) − m(t−1))` |
| `second_order=True` | `½ · k · R(t−1) · (m(t) − m(t−1))²` |
| cross metric `m₂` | `k · R(t−1) · Δm · Δm₂` (no ½) |

## 2. The IR definitions

`ir_pnl_definition(rate_unit="bp", vol_unit="bp", *, delta=True, gamma=True, vega=True, vanna=True, volga=True, theta=True)` builds the table below. `swaption_pnl_definition(rate_unit, vol_unit)` returns all six attributes. `bond_pnl_definition(rate_unit)` returns delta, gamma and theta. Only gs measure names are used.

| attribute_name | risk at t−1 | level | cross level | form | scaling_factor k | unit checked |
|---|---|---|---|---|---|---|
| `PNL_delta` | `IRDeltaParallel` (resolves to the `IRDelta` scalar) | `IRFwdRate` | | first | f_r | rate_unit |
| `PNL_gamma` | `IRGammaParallel` | `IRFwdRate` | | second | f_r² | rate_unit |
| `VegaPnL` (gs's name) | `IRVegaParallel` (resolves to the `IRVega` scalar) | `IRAnnualImpliedVol` | | first | f_v | vol_unit |
| `PNL_vanna` | `IRVanna(aggregation_level=Type)` | `IRFwdRate` | `IRAnnualImpliedVol` | cross | f_r·f_v | both |
| `PNL_volga` | `IRVolga(aggregation_level=Type)` | `IRAnnualImpliedVol` | | second | f_v² | vol_unit |
| `PNL_theta` | `Theta` | `ExpiryInYears` | | first | −365 | none |

### Scaling factors come from the declared units

- Every sensitivity in the contract is per **bp** (bp² for second order) of its level. A level declared in another unit needs a factor, so that `k·Δm` is in bp: **f = 1 for `bp`, 100 for `pct`, 1e4 for `decimal`**.
- Second-order and cross attributes take the product of the factors, applied once, as gs's formula does.
- `attribution.definition_for(...)` reads `rate_unit` from the `unit:` of each config's `IRFwdRate` function and `vol_unit` from its `IRAnnualImpliedVol` function. It refuses a book whose assets declare different units. A book quoted in percent therefore attributes exactly like the same book in bp (tested to 1e-6 in `tests/skills/test_skill_pnl_attribution.py`).
- **Theta.** `ExpiryInYears` is `max(expiry_or_final − t, 0).days / 365` (DEV-I17), so `−365 · ΔExpiryInYears` is the number of calendar days elapsed, and `PNL_theta = Theta(t−1) × days`. The count is capped at expiry, because `ExpiryInYears` floors at 0.
- **Theta has no unit check.** Its level is a time, and `Theta`'s unit is `ccy` whether it is per day or per year. A per-year theta passes every load and unit check and inflates `PNL_theta` 365-fold. See [diagnosing-residuals.md](diagnosing-residuals.md).

## 3. What your library must supply, per measure

These are the contract semantics (IR_RISK_DESIGN R2-1 to R2-8, DEV-I12/I15/I17), written as what to ask of *your* library. The "own rate" r is the instrument's quoted rate: a swap's par rate, the forward swap rate of a swaption's underlying, a bond's yield to maturity. h is the library's ±1bp bump. Every value is holder-signed, for one unit trade (pricebt applies quantity).

| Measure | What pricebt needs | If your library has it | If it does not: derive it | Verify numerically |
|---|---|---|---|---|
| `IRFwdRate` (level, intensive) | r in the unit you declare (`bp`, `pct`, `decimal`). Finite on **every held date, including the exit date**; it continues past death (R2-7) | object library: the swap's `par_rate`/`fair_rate`, the swaption's forward swap rate, the bond's `ytm`; platform service: the par/forward rate or yield field of its analytics request | reprice to par: solve for the fixed rate with PV = 0 (swap), or solve price → yield (bond) | an ATM trade's resolved strike/fixed rate equals `IRFwdRate` at inception (× 1e4 for bp) |
| `IRDelta` scalar (`IRDeltaParallel`) | the **total** own-rate derivative `[PV(+h) − PV(−h)] / [r(+h) − r(−h)]` along the library's parallel shift, own-strike vol held (sticky strike), ccy per bp. Payer swap > 0, payer swaption > 0, long bond < 0 | an analytic `dPV/dr` in the *own rate* (rare) | the ratio above, from three valuations and three rates on the library's parallel bump (zero- or par-curve shift) | compare with the ratio at an **off-market** strike (for example K = F − 100bp). A fixed-annuity pv01 only agrees at the money |
| `IRGammaParallel` | `∂²PV/∂r²` per bp², by the chain rule on the same bumps: `Γ = [n₊ + n₋ − 2n₀ − Δ·(r₊ + r₋ − 2r₀)] / ((r₊ − r₋)/2)²` | an analytic gamma: convert per decimal² × 1e-8, per 1%² × 1e-4 | the formula above (reference: `own_rate_greeks` in `tests/toylib/irrisk.py`) | at the money, Γ ≈ 2·Δpv01/Δr. **Never** `d(pv01)/dr` of an annuity pv01: that is half the gamma at the money |
| `IRVega` scalar (`IRVegaParallel`) | PV change per +1bp of the **normal** implied vol (the `IRAnnualImpliedVol` level). Swaps and bonds: 0.0 | normal-model libraries: the vega per bp of normal vol | lognormal (Black) libraries: take the Bachelier-implied normal vol of the library's price as the level, and bump it ±1bp through the Bachelier formula. **Never multiply a Black vega per 1% by a constant** | `[PV(σ+h) − PV(σ−h)]/2` against the mapped value |
| `IRVanna` (request `IRVanna(aggregation_level='Type')`) | `d(IRDelta scalar)/dσ` per bp·bp. Swaps and bonds: 0.0 | rare | recompute the own-rate delta at σ ± 1bp: `[Δ(σ+h) − Δ(σ−h)] / 2` | symmetric: equals `d(vega)/dr` on the same bumps |
| `IRVolga` (request `IRVolga(aggregation_level='Type')`) | `∂²PV/∂σ²` per bp². Swaps and bonds: 0.0 | rare | `PV(σ+h) + PV(σ−h) − 2·PV(σ)` | an ATM option under a normal model has volga = 0 (its price is linear in σ) |
| `IRAnnualImpliedVol` (level) | the annualised normal vol at the strike, in the unit you declare. Swaps and bonds: `0.0` (R2-8) | the vol-cube lookup at (expiry, tail, strike) | the Bachelier-implied vol of the library's price | an ATM value matches the cube's quote |
| `Theta` | **one calendar day, total return**: `Price(t+1d; r and σ held) + cash paid in (t, t+1d] − Price(t)`, ccy **per day** (DEV-I15) | object library: value on a **translated** curve `DF'(x) = DF(x)/DF(t+1d)` (forwards fixed), same vol, expiry one day closer; bond analytics: price at the same yield with settlement +1 day, plus a coupon paid that day | never a rolled curve (same zero rates by tenor one day later): that moves the forwards and so double counts with delta; never per year (`IRTheta` = 365 × `Theta`) | reprice by hand on t's curve translated one day (and the same vol), add the cash paid that day, subtract `Price(t)`: equals `Theta` |
| `ExpiryInYears` | `max(expiry − t, 0).days / 365`. Swaps and bonds: to the final date (DEV-I17) | | date arithmetic in the config | exactly 1/365 less per calendar day |
| `Cashflows` (frame; not in the definition, read by `pnl_explain_table` when among the risks) | holder-signed flows with `payment_date > t` that `Price` **will drop** when paid (R2-6); columns `payment_date, payment_amount, currency, payment_type`; `scale_columns: [payment_amount, ...]`. A total-return `Price` returns an **empty** frame | the library's cashflow table | from the schedule | on a coupon date, `cashflow_pnl` equals face × coupon / frequency |

Full recipes for every contract measure, with the capability worksheet, are in [`pricebt-risk-measures`](../../pricebt-risk-measures/SKILL.md) ([implementing-measures.md](../../pricebt-risk-measures/references/implementing-measures.md)) and `docs/v2/ASSET_CONFIG_GUIDE.md`. Reference implementations on a closed-form toy library: `tests/toylib/irrisk.py` (swap), `tests/toylib/swaption.py`, `tests/toylib/bond.py`, wired in `tests/assets/toy_usd_irs_full.yaml`, `tests/assets/toy_usd_swaption.yaml` and `tests/assets/toy_usd_bond.yaml`.

### Declaring a measure unsupported, and what it costs attribution

- **Honest use.** Your library genuinely cannot compute the measure: no vol model, so no vanna or volga; no cashflow table. Declare it under `unsupported_measures:` with the real reason.
- **Build the definition without that attribute**, for example `definition_for(session, vanna=False, volga=False)`. The missing term then lands in the residual; say so in the report.
- **What happens if you do not.** A definition that still reads the measure fails at the first calc with `UnsupportedMeasureError`, naming the measure and your reason. `definition_for` refuses it earlier and lists every gap.
- **Swaps and bonds in a book with vol attribution** must *map* the 0.0 convention (a `'0.0'` function with unit `ccy_per_bp`, `ccy_per_bp2` or the vol unit), not declare it. A risk of exactly 0 is skipped before its level is read, but every measure is still *priced* for every held instrument, and a declared-unsupported measure raises.

## 4. The step semantics of `pnl_explain` (gs, ported verbatim)

1. **Steps** are consecutive dates of `results ∪ trade_exit_risk_results`.
2. **Skipped steps.** If t−1 has no `results` (it was only an exit date), the step adds 0 and the cumulative value carries over.
3. **The held set** is `results[t−1].portfolio.all_instruments`. For each instrument:
   - `R = results[t−1][inst][attribute_metric]`. If `R == 0` exactly, the instrument is skipped and its level is never read.
   - Otherwise `m(t−1)` comes from `results[t−1]`, and `m(t)` from `results[t]` while the instrument is still held, else from `trade_exit_risk_results[t]` (its exit valuation).
4. **Entries** contribute from their first full step. A trade bought at its PV has no P&L on its entry date, apart from transaction costs, which are not attributed.
5. **Exits** are attributed on the step that ends at the exit, from the exit results. Off-grid exits attribute correctly (DEV-E20; gs raises `KeyError`).
6. **No NaN guard.** One NaN risk or level makes that step NaN and every later cumulative value of `pnl_explain()` NaN.
7. **The return value** of `pnl_explain()` is `{attribute_name: {date: cumulative P&L}}`.

`pnl_explain_table()` (a pricebt addition) walks the same steps and the same held set. It returns one row per step:

| column | meaning |
|---|---|
| `actual_pnl` | Σ over the held set of `price_measure(t) − price_measure(t−1)` (exits valued from the exit results) |
| `cashflow_pnl` | Σ `payment_amount` of the `Cashflows` held at t−1 with `t−1 < payment_date ≤ t`; 0.0 when `Cashflows` is not among the risks |
| `economic_pnl` | `actual_pnl + cashflow_pnl` |
| one column per attribute | its P&L over the step; the column's `cumsum()` is exactly `pnl_explain()[name]` |
| `explained_pnl` | Σ attributes |
| `residual_pnl` | `economic_pnl − explained_pnl` |

- **Error cases.** Attribute names must be unique and must not collide with the fixed columns (`ValueError`). A step mixing price units or cash currencies raises `ValueError`.
- **Reconciling with `result_summary`.** For a book bought at PV, with no transaction costs, no cash accrual and no coupons, `Σ actual_pnl = Total(end) − Total(start)`. Coupons are **not** booked by the engine (gs parity, decision 0.10), so on a coupon-paying `Price` the `economic_pnl` (with `cashflow_pnl`) is the P&L you would have had, and `Total` understates it.

## 5. Mixed books

- **Every held asset must answer every measure of the definition.** `definition_for` checks this against the configs; the engine checks it at the first calc.
- **Swaps and bonds in a swaption book** map vega, vanna and volga to 0.0, and the vol levels to 0.0 (R2-8).
- **One unit per level across the book.** Convert in the configs; `definition_for` refuses mixed units.
- **Each instrument is attributed on its own rate.** The book's attribution is the sum of the per-instrument attributions, and so is its residual.
- **Hedges across instrument types are approximate.** Own-rate deltas of instruments quoted on different rates are not strictly additive (R2-2), so a `HedgeAction(IRDeltaParallel, swap)` on a swaption is approximate. The attribution is still exact per leg; the hedge is what is imperfect. The delta-hedged toy book in `tests/test_pnl_ir.py` shows the swap legs carrying delta, gamma and theta, and the vol terms coming only from the swaption.

## 6. Swaps

- **The in-flight swap definition.** The swap P&L-explain branch adds `swap_pnl_definition` in [`swap_pnl.py`](../../pricebt-strategy-recipes/scripts/swap_pnl.py) (merged; see `docs/v2/MERGE_NOTES_pnl_explain.md`). Its carry attribute is `IRTheta × YearFraction`, with a per-**year** `IRTheta`. Use it for configs written to it.
- **The swap definition available today** is `ir_pnl_definition(vega=False, vanna=False, volga=False)`. It has the same three attributes as `bond_pnl_definition()`, and is what `definition_for` picks for a book with no swaption.
  - It needs `IRGammaParallel`, `Theta` and `ExpiryInYears` mapped. `tests/assets/toy_usd_irs_full.yaml` maps them.
  - `tests/assets/toy_usd_irs.yaml` and the shipped ARBS config map `IRGammaParallel` (the in-flight gamma, without the chain-rule term) but declare `Theta` and `ExpiryInYears`, so they are still refused.
- **Never map `Theta` to the in-flight per-year `IRTheta` function.** `IRTheta` = 365 × `Theta`.
- **The shipped swap configs map the `IRDelta` scalar to an annuity pv01.** That is exact only at the money; off-market it leaves the residual `N·(F−K)·ΔA` ([diagnosing-residuals.md](diagnosing-residuals.md) §1).

## 7. Custom attributes

Any risk measure your configs map (ccy per unit of a level) times any level measure is a valid `PnlAttribute`. For example, a bond `CREDIT` attribute: a custom spread-dv01 measure name × `LightningOAS`. To write one:

- set `market_data_unit` so that a wrong unit raises, instead of scaling silently;
- keep attribute names unique;
- map the measure on every held asset.

A per-pillar (key-rate) attribution does not fit one `PnlAttribute`, which is scalar risk × scalar level. For that, use one attribute per pillar with custom measure names, or use `PnlExplain` ([instrument-level.md](instrument-level.md)).
