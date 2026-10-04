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
| `Theta` on a **Bond** | `Price` is a settlement-date value, so the contract spreads the step to the next business day nb: `[Price(nb, same yield) + flows Price drops in (t, nb] − Price(t)] / (nb − t).days`, ccy per calendar day. **Financing is not in Theta** (it is `FinancingToDate`) | bond analytics: price at the same yield for nb's settlement date, plus a coupon dropped in (t, nb] | price → yield at t, yield → price at nb's settlement | `Theta × (nb − t).days` equals the hand repricing; on a business-day grid `PNL_theta` is then exact at constant yield |
| `ExpiryInYears` | `max(expiry − t, 0).days / 365`. Swaps and bonds: to the final date (DEV-I17) | | date arithmetic in the config | exactly 1/365 less per calendar day |
| `Cashflows` (frame; not in the definition, read by `pnl_explain_table` when among the risks) | holder-signed flows with `payment_date > t` that `Price` **will drop** when paid (R2-6); columns `payment_date, payment_amount, currency, payment_type`; `scale_columns: [payment_amount, ...]`. A total-return `Price` returns an **empty** frame. **Bond:** `payment_date` is the trade date on which `Price` drops the flow (T+1: the business day before a business-day coupon date) | the library's cashflow table | from the schedule | on a coupon date, `cashflow_pnl` equals face × coupon / frequency |

Full recipes for every contract measure, with the capability worksheet, are in [`pricebt-risk-measures`](../../pricebt-risk-measures/SKILL.md) ([implementing-measures.md](../../pricebt-risk-measures/references/implementing-measures.md)) and `docs/v2/ASSET_CONFIG_GUIDE.md`. Reference implementations on a closed-form toy library: `tests/toylib/irrisk.py` (swap), `tests/toylib/swaption.py`, `tests/toylib/bond.py`, wired in `tests/assets/toy_usd_irs_full.yaml`, `tests/assets/toy_usd_swaption.yaml` and `tests/assets/toy_usd_bond.yaml`.

### No contract class declares a measure unsupported

- **`IRSwap`, `IRSwaption` and `Bond` map every contract measure** or fail to load (`docs/v2/IR_STRICT_CONTRACT.md`, `docs/v2/BOND_DESIGN.md` decision 4.1). A declaration of a contract measure under `unsupported_measures:` is itself a load error. So a definition built from contract measures never refuses one of these books for a gap.
- **The vol rows of a bond are 0.0 by convention** (`contracts.ZERO_BY_CONVENTION["Bond"]`: a bullet bond has no optionality), mapped as `'0.0'` functions with unit `ccy_per_bp`, `ccy_per_bp2` or the vol unit. A risk of exactly 0 is skipped before its level is read, so a bond in a swaption book adds nothing to the vol attributes.
- **`unsupported_measures:` remains for classes without a contract** (`ConfigInstrument`). A definition that reads a measure such an asset declares fails at the first calc with `UnsupportedMeasureError`; `definition_for` refuses it earlier and lists every gap. Build the definition without that attribute and report the term the residual then carries.

### Bonds: carry, financing and coupons

A financed bond's step P&L has four parts, and the table keeps them apart:

| part | where it lands | what it is |
|---|---|---|
| move in the yield | `PNL_delta`, `PNL_gamma` | `IRDelta` and `IRGammaParallel` against `IRFwdRate` (the yield to maturity, DEV-I12) |
| time at constant yield | `PNL_theta` | `Theta × days`: accrual of the coupon plus pull to par, at the yield of t−1. No financing |
| coupons | `cashflow_pnl` | the flows `Price` dropped in the step (the engine's holding-cash record for a financed position) |
| repo interest | `financing_pnl` | the change of `FinancingToDate` the engine booked as cash: a long pays (≤ 0), a short receives (≥ 0) |

- `bond_pnl_definition(rate_unit)` is `ir_pnl_definition(rate_unit, vega=False, vanna=False, volga=False)`: delta, gamma and theta against the bond's own yield. It has no spread attribute: a move in the bond's spread to the curve is already a move in its yield, so it is in `PNL_delta`.
- `PNL_theta` and `financing_pnl` are disjoint by construction: the contract's `Theta` holds the yield and leaves financing out ("Financing is not in Theta"), and `financing_pnl` is cash the engine booked, not a greek. Their sum over a quiet step is the financed carry: coupon accrual and pull to par minus repo interest.
- `Carry` and `RollDown` (the financing contract, DEV-I21) are a forward-looking estimate to the horizon H = settlement + 1 calendar month; they are not attributes. On an unmoved flat curve, `Carry + RollDown` at entry is close to `Σ (PNL_theta + financing_pnl)` over the next month (RollDown is then the pull to par at constant yield, which `PNL_theta` carries). On a sloped curve the roll-down is a fall in the bond's yield, so it lands in `PNL_delta`. Either way it is a sanity check, not an identity: `Carry` and `RollDown` hold the repo rate flat to H and use clean values, the backtest uses each day's repo fixing.

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
| `cashflow_pnl` | for a financed position (its asset maps `FinancingToDate`), the coupons the engine booked as holding cash on t (DEV-E22); for any other, Σ `payment_amount` of the `Cashflows` held at t−1 with `t−1 < payment_date ≤ t`, and 0.0 when `Cashflows` is not among the risks |
| `financing_pnl` | the change of a financed position's `FinancingToDate` the engine booked on t; 0.0 for every other position |
| `economic_pnl` | `actual_pnl + cashflow_pnl + financing_pnl` |
| one column per attribute | its P&L over the step; the column's `cumsum()` is exactly `pnl_explain()[name]` |
| `explained_pnl` | Σ attributes + `financing_pnl` (financing is known cash, not a market move) |
| `residual_pnl` | `economic_pnl − explained_pnl` (so financing never reaches it) |

- **Error cases.** Attribute names must be unique and must not collide with the fixed columns (`actual_pnl`, `cashflow_pnl`, `financing_pnl`, `economic_pnl`, `explained_pnl`, `residual_pnl`; `ValueError`). A step mixing price units or cash currencies raises `ValueError`.
- **Where the engine's numbers live.** `bt.holding_cash[d][position] = (ccy, cashflow, financing)`: per date and per financed position, what the engine booked as cash on d since the position's previous mark (in `result_ccy` when the run sets one). The table reads it; read it yourself to tie a step to the cash ledger. `tests/test_holding_cash.py` is the reference.
- **Reconciling with `result_summary`.** For a book bought at PV, with no transaction costs and no cash accrual:
  - a book of financed positions (every `Bond`): `Σ economic_pnl = Total(end) − Total(start)`, because the engine books coupons and the change of `FinancingToDate` as cash, exactly on any grid. `Total` changes by ΔPV + coupons + ΔFinancingToDate, i.e. ΔPV + coupons − repo interest for a long;
  - a book of swaps or swaptions with no coupons in the period: `Σ actual_pnl = Total(end) − Total(start)`. Their coupons are **not** booked by the engine (gs parity, decision 0.10), so on a coupon-paying `Price` the `economic_pnl` (with `cashflow_pnl`) is the P&L you would have had, and `Total` understates it.

## 5. Mixed books

- **Every held asset must answer every measure of the definition.** `definition_for` checks this against the configs; the engine checks it at the first calc.
- **Swaps and bonds in a swaption book** map vega, vanna and volga to 0.0, and the vol levels to 0.0 (R2-8).
- **One unit per level across the book.** Convert in the configs; `definition_for` refuses mixed units.
- **Each instrument is attributed on its own rate.** The book's attribution is the sum of the per-instrument attributions, and so is its residual.
- **Hedges across instrument types are approximate.** Own-rate deltas of instruments quoted on different rates are not strictly additive (R2-2), so a `HedgeAction(IRDeltaParallel, swap)` on a swaption is approximate. The attribution is still exact per leg; the hedge is what is imperfect. The delta-hedged toy book in `tests/test_pnl_ir.py` shows the swap legs carrying delta, gamma and theta, and the vol terms coming only from the swaption.

## 6. Swaps

- **The swap recipe's definition.** `swap_pnl_definition` in [`swap_pnl.py`](../../pricebt-strategy-recipes/scripts/swap_pnl.py) (merged from the swap P&L-explain branch; see `docs/v2/MERGE_NOTES_pnl_explain.md`). Its carry attribute is `IRTheta × YearFraction`, with a per-**year** `IRTheta`. Use it for configs that map those custom names (`tests/assets/toy_usd_irs.yaml` does; `tests/assets/toy_eur_irs.yaml` does not, so this definition refuses it).
- **The contract's swap definition** is `ir_pnl_definition(vega=False, vanna=False, volga=False)`. It has the same three attributes as `bond_pnl_definition()`, and is what `definition_for` picks for a book with no swaption.
  - It needs `IRGammaParallel`, `Theta` and `ExpiryInYears`. Every IRSwap config that loads maps them (the strict contract, `docs/v2/IR_STRICT_CONTRACT.md`), so it never refuses a swap for a gap: `tests/assets/toy_usd_irs.yaml`, `tests/assets/toy_usd_irs_full.yaml` and the ARBS config all qualify.
- **Never map `Theta` to the per-year `IRTheta` function.** `IRTheta` = 365 × `Theta`.
- **The shipped swap configs map the `IRDelta` scalar to an annuity pv01.** That is exact only at the money; off-market it leaves the residual `N·(F−K)·ΔA` ([diagnosing-residuals.md](diagnosing-residuals.md) §1).

## 7. Custom attributes

Any risk measure your configs map (ccy per unit of a level) times any level measure is a valid `PnlAttribute`. For example, a bond `CREDIT` attribute: a custom spread-dv01 measure name × `LightningOAS`. To write one:

- set `market_data_unit` so that a wrong unit raises, instead of scaling silently;
- keep attribute names unique;
- map the measure on every held asset.

A per-pillar (key-rate) attribution does not fit one `PnlAttribute`, which is scalar risk × scalar level. For that, use one attribute per pillar with custom measure names, or use `PnlExplain` ([instrument-level.md](instrument-level.md)).
