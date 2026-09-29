# Implementing the IR measures with your library

For each measure in the `IRSwap` / `IRSwaption` / `Bond` contract, this page gives:

- what pricebt expects: definition, unit, sign, and what a dead instrument returns;
- how to get it from your library, for several library shapes, including how to derive it by bump-and-reprice when your library does not have it;
- the traps that give a plausible but wrong number;
- how to check the number.

The contract text itself is `src/pricebt/risk/contracts.py`, and you can print it with `measures.py contract <class>`. The design is `docs/v2/IR_RISK_DESIGN.md`, §2 and the overriding §00. A runnable reference implementation of every recipe is in `tests/toylib/irrisk.py` (the shared kernel and the swap), `tests/toylib/swaption.py` and `tests/toylib/bond.py`, wired up by `tests/assets/toy_usd_irs_full.yaml`, `tests/assets/toy_usd_swaption.yaml` and `tests/assets/toy_usd_bond.yaml`. `tests/test_toylib_ir.py` proves each identity below against an independent computation.

In the snippets, `lib.*` stands for **your** library. Every `lib.*` name is a placeholder, not a real API.

## 0. Rules for every measure

| Rule | What it means for your function |
|---|---|
| **Unit trade** | A `functions:` entry is evaluated for ONE unit of the resolved trade, meaning its own notional or face. pricebt multiplies an extensive unit (`ccy`, `ccy_per_bp`, `ccy_per_bp2`) by `quantity_` and never multiplies an intensive one (`bp`, `pct`, `decimal`). |
| **Holder-signed** | Values are from the position holder's side. pricebt never reads `buy_sell`: your `resolve` must fold `buy_sell` × sign(notional or size) into one signed notional or face, and every function prices that. A sold option is then exactly minus the bought one. |
| **Own rate r** | `IRFwdRate` is the instrument's own quoted rate: a swap's par rate, a swaption's underlying forward swap rate, a bond's yield to maturity (DEV-I12). The `IRDelta` scalar and `IRGammaParallel` are derivatives with respect to this r, per bp of r. |
| **Normal vol σ** | `IRAnnualImpliedVol` is the normal (Bachelier) vol at the strike. `IRVega`, `IRVanna` and `IRVolga` are per bp of that σ. |
| **Units agree across a book** | Every asset in one book must declare the same unit for each level (`IRFwdRate`, `IRAnnualImpliedVol`). The shipped and toy configs use `bp`, and the P&L definitions take the unit as a parameter. |
| **Dead instruments** | A matured, expired-OTM or fully paid instrument has every sensitivity at `0.0`, finite levels, and an empty `Cashflows`. A level must continue from its last live value (R2-7). One NaN poisons every later cumulative `pnl_explain` value: pricebt skips a risk that is exactly 0 and never guards NaN. |
| **No-exposure measures** | A swap's or a bond's `IRVega`, `IRVanna`, `IRVolga` and vol levels are `0.0`, which is the true value under any rates model (R2-8). Map them to a `'0.0'` function; do not declare them. |

Where the code goes: in the config's `code:` block, or in your own helper module that `imports:` loads. It never goes in `src/pricebt` (MUST-1).

## 1. Library shapes and the one bump primitive you need

| Shape | Typical API | Risk it usually has | How it bumps |
|---|---|---|---|
| **A. Object library** (QuantLib-, rateslib-style) | curve and instrument objects, `npv()` | analytic or AD (dual-number) curve DV01 and key-rate ladders | wrap the curve with a spread, rebuild it with shifted quotes, or read AD gradients |
| **B. Platform or REST service** (bank-platform style) | `price(trades, market_handle, measures=[codes])` | vendor codes for PV, DV01, bucketed DV01, vega, in the vendor's units and signs | a scenario or shift request (parallel shift, vol shift, as-of override); if there is none, you cannot bump |
| **C. In-house function library** | `pv(curve, trade)` pure functions | little or none | build a shifted curve object and call again |
| **D. Bond analytics package** | price, yield, duration, convexity, accrued by identifier | yield-based risk | `price_from_yield(y ± h)` |
| **Toy** (`tests/toylib`, runnable) | `SimpleNamespace(curve=ToyCurve, sigma=...)` | none: everything is derived | `dataclasses.replace(curve, zero_rate=z + h)` (`toylib.irrisk.bumped`) |

Every derived first- and second-order measure needs one primitive: **reprice one unit trade on a shifted copy of the market and return (PV, own rate)**.

```python
# config code: block (or your helper module). lib.* = YOUR library (placeholders)
def _reprice(market, trade, curve_bp=0.0, vol_bp=0.0):
    m = lib.shifted(market, parallel_bp=curve_bp, normal_vol_bp=vol_bp)   # shape A/C: build it; B: a scenario request
    return lib.pv(m, trade), lib.own_rate(m, trade) * 1e4                 # own rate in bp

def _own_rate_greeks(market, trade, h=1.0, vol_bp=0.0):
    """(IRDelta scalar per bp of r, IRGammaParallel per bp^2 of r): the toylib.irrisk.own_rate_greeks recipe."""
    (n_dn, r_dn), (n_0, r_0), (n_up, r_up) = (_reprice(market, trade, s, vol_bp) for s in (-h, 0.0, h))
    delta = (n_up - n_dn) / (r_up - r_dn)
    gamma = (n_up + n_dn - 2 * n_0 - delta * (r_up + r_dn - 2 * r_0)) / ((r_up - r_dn) / 2) ** 2
    return delta, gamma
```

```yaml
functions:
  delta: {expr: '_own_rate_greeks(market, trade, pricebt_bump_size or 1.0)[0]', unit: ccy_per_bp}   # honours IRDelta(bump_size=...)
  gamma: {expr: '_own_rate_greeks(market, trade)[1]', unit: ccy_per_bp2}
```

Why the gamma formula has a second term: your shift `s` is not the own rate `r`. The chain rule gives `d²V/dr² = (V_ss − V_r·r_ss) / r_s²`, and the correction removes the curvature of `r(s)`. With a par-quote shift `r(s)` is nearly linear, and with a zero-rate shift it is not.

**Hold the strike's vol fixed ("sticky strike").** When you bump the curve, the forward moves. If your vol cube is quoted against moneyness or delta, the vol at the fixed strike then moves too. The contract fixes the vol at the instrument's own strike, so pass the base market's σ(K) into the bumped reprice as a constant. On a platform, ask for sticky-strike risk or a vol override.

**Caching across functions (the memo rule).** pricebt caches each function's value per (asset, date, market, resolved terms, parameters), but it does not share work between `delta` and `gamma`. If repricing is expensive, compute both in one cached helper, and make its key at least as specific as pricebt's: the **market object** (`id(market)`, or keep the memo on `market.__dict__`), **`pricebt_date`**, the trade or its resolved terms, and every `pricebt_*` parameter the function reads. Under `PricingContext(pricing_date=d, market=CloseMarket(date=t))` pricebt hands t's cached market object to an evaluation at `pricebt_date` d, and the same object to t's own evaluations. So a memo on the market alone returns t-valued numbers wherever a function values at `pricebt_date` (the toy swaption's `npv`, the bond template's same-yield theta), and a memo on `pricebt_date` alone returns d's market for t. A market-derived object that does not depend on the valuation date (a calibrated risk curve, a trade remarked to the market's fixings) may key on the market alone.

**A process-global evaluation date** (QuantLib-style `Settings.instance().evaluationDate`) is shared state across every date pricebt interleaves in one process: the checker prices d1, d2, d2 + 1 business day, the 20 days after d1 and the final date + 7; `CloseMarket` mixes two dates in one evaluation. Carry the valuation date on each market object, set the global date from it inside every primitive that prices, move it for `Theta` only inside `try/finally` (restore it), never set it at import, and put the date in every memo key.

### Shape B with no scenario request: PV and first-order codes only

Many services value only on their own market (no curve shift, no valuation-date override) and return a PV, a par rate and a DV01 or DV01 ladder. The fictional Meridian example (`skills/pricebt-connect-pricing-library/example/meridian_usd_irs.yaml`) is this case, tested in `tests/skills/test_skill_connect_example.py`. Price the trade at its strike K and at K ± 1bp in **one** batched call, then:

| Measure | Recipe | Note |
|---|---|---|
| `Annuity` (swap) | `A = −[PV(K+1bp) − PV(K−1bp)] / 2e-4` | exact (a swap's PV is linear in K); payer > 0 |
| `IRDelta` scalar | `DV01_par = DV01(K) + (r − K)·[DV01(K+1bp) − DV01(K−1bp)] / 2e-4`; `dr/ds = DV01_par / (A·1e-4)`; `IRDelta = DV01(K) / (dr/ds)` | exact to first order: DV01 is linear in K, and a swap struck at its par rate r moves only through r. DV01 in pricebt's sign (per +1bp, payer > 0) |
| `IRDelta` scalar, shortcut | the sign-converted DV01, or the sum of a single-trade ladder call | exact only where dr/ds = 1. With par-quote pillars, dr/ds ≈ 1 for a par swap on a pillar; with continuously compounded zero pillars it is about 1 + r (1.03-1.05 on the Meridian curve). State which in the function comment |
| `IRFwdRate` | the service's par rate; with none, `K + PV / A` (a swap's PV is `A·(F − K)`) | intensive |
| `IRSpotRate` | the par rate of a spot-starting probe swap to the same end date, in the same batch | |
| `IRDiscountDeltaParallel` | single-curve service: the zero-curve DV01 (as `toylib.irrisk.discount_delta_on`); multi-curve: declare | |
| vol level (swaption) | invert the service's premium through Bachelier at the fixed annuity (`bachelier_implied_vol` in the swaption template) | intensive |
| `IRVega`, `IRVanna`, `IRVolga` (swaption) | the service's normal vega if it quotes one; otherwise declare "no vol-shift request" | a swap's or bond's are 0.0 |
| `ExpiryInYears`, the R2-8 zeros | date arithmetic; `'0.0'` | every library |
| declare | `IRGammaParallel`, `IRGamma`, `Theta` (and `Cashflows` with no schedule call) | reason: "no curve-shift or valuation-date request"; a first-order DV01 has no curvature |

A declared scalar blocks the ladder-sum fallback of section 4 of `portfolio-and-results.md` (a contract class must map or declare the scalar itself), so map the scalar with one of the rows above.

## 2. Per-measure recipes

### `Price` (value, ccy)

- **Definition:** PV of one unit trade in the function currency, holder-signed. Use one of two conventions consistently with `Cashflows` and `Theta`:
  - **drop paid flows:** each flow leaves `Price` on its payment date, and `Cashflows` lists the flows still to drop;
  - **total return:** paid flows never leave `Price`, and `Cashflows` is empty.
- **Swaption after expiry:** under physical settlement, an exercised leg is the underlying swap and an unexercised one is 0 (R2-7). If your library cash-settles, the settlement amount leaves `Price` on its payment date, so list it in `Cashflows`.
- **Bond:** dirty PV of the face held, **not** clean and **not** per 100. From a shape-D quote: `Price = sign × (clean + accrued_per_100) / 100 × face`. Also discount the settlement-date dirty price to the pricing date if your library quotes for T+1/T+2, and decide how you treat ex-coupon periods. Whatever you choose, do the same in `Theta` and `Cashflows`.
- **Traps:**
  - a library PV that ignores `buy_sell` (fold it in `resolve`);
  - clean price;
  - price per 100 or per 1mm face;
  - a PV quoted in another currency without the `currency:` key on the function.
- **Verify:**
  - Buy = −Sell exactly;
  - an ATM swap is about 0;
  - a long option is > 0;
  - a bond near par has dirty PV of about face.

### `IRDelta` scalar (sens1, ccy per bp of r)

- **Definition:** the **total** derivative of `Price` with respect to the own rate r, along your library's parallel curve shift, with the strike's vol fixed: `[PV(+h) − PV(−h)] / [r(+h) − r(−h)]` per bp of r (R2-1).
- **Signs:** a pay-fixed swap is > 0, a payer swaption is > 0, and a long bond is < 0.
- **Dead instruments:** 0.0.
- **From your library:**
  - **It has an own-rate DV01** (a bond's yield DV01 dP/dy, or a swaption delta in forward terms that includes the annuity's move): check the unit and sign conventions (the conversions table at the end of this section), then map it.
  - **It only has a curve DV01** (∂PV/∂s for its own shift s): `Δ = DV01_s / (dr/ds)`, with `dr/ds = (r(+h) − r(−h)) / 2h` taken from the same shifts. With a par-quote shift, `dr/ds ≈ 1` for a par swap on a pillar (R2-2), so a platform that only returns a par-quote parallel DV01 is close; a zero-rate DV01 is 3-5% off. Say which in the function comment. **No shift request at all:** get dr/ds from DV01 at two strikes (section 1, "Shape B with no scenario request").
  - **AD** (dual numbers): `Δ = (∂PV/∂s) / (∂r/∂s)` from one dual evaluation of PV and r.
  - **Nothing:** use `_own_rate_greeks` (section 1).
- **Trap: fixed-annuity delta.** A swap's `pv01 = N·A·1bp` equals the own-rate delta **only at the money**. `PV = N·A·(F−K)` gives `dPV/dF = N·A + N·(F−K)·dA/dF`, so off-market the annuity pv01 misses `N·(F−K)·ΔA` of first-order P&L (R14). `test_swap_off_market_delta_is_not_the_annuity_pv01` in `tests/test_toylib_ir.py` shows the gap. The shipped toy and ARBS swap configs still map the annuity pv01; `docs/v2/MERGE_NOTES_pnl_explain.md` records that as known non-compliance.
- **Verify:**
  - your own ±1bp reprice in the library matches;
  - payer ≈ −receiver;
  - Buy = −Sell;
  - `quantity_` scales it.

### `IRDelta` bucketed (sens1, ccy per bp at each pillar)

- **Definition:** a key-rate ladder. Bump each curve pillar (quote or zero) by +1bp and record the PV change. It is a **portfolio function** (`returns: buckets`) over `trades` and `weights`; `weights` already carry the quantity, so do one batched library call.
  - Return `{mkt_point: value}` with `labels: {mkt_type: IR, mkt_asset: <curve id>, mkt_class: <OIS|...>}`.
  - For several curves, return a list of row dicts, e.g. `[{"mkt_asset": "USD-SOFR", "mkt_point": "5Y", "value": 12.3}, ...]` (R2-14).
- **Relation to the scalar:** Σ ladder ≈ the parallel **curve** delta = scalar × dr/ds, not exactly the scalar (R2-2). The toy ladders put the scalar at the nearest pillar so that they sum exactly; that is a toy simplification, not a recipe.
- **Traps:**
  - a receiver-positive bucketed DV01: negate every bucket;
  - keys that do not match your pillars across dates (keep the key set stable);
  - per-trade calls in a loop when your library batches.
- **Verify:**
  - Σ ladder vs your library's parallel DV01;
  - each bucket vs a single-pillar bump.

### `IRDiscountDeltaParallel` (sens1)

- **Definition:** a +1bp parallel shift of the **discount** curve only, with projection curves held (R2-3). This is not normalised by dr/ds.
- **Single-curve library:** `(PV(+h) − PV(−h)) / 2` per 1bp of your zero or quote shift (`toylib.irrisk.discount_delta_on`). On the toy 10y payer this is 844, against an `IRDelta` scalar of 812: the two are different measures.
- **Multi-curve:** shift only the discount or OIS curve object.
- **Declare it** if your platform can only shift everything together.

### `IRGammaParallel` (sens2, ccy per bp² of r)

- **Definition:** the chain-rule second derivative of `Price` in the own rate r, on the delta bumps (formula in section 1).
- **Signs:** a long option or bullet bond is > 0. A pay-fixed swap is < 0 and a receiver is > 0, because the annuity falls as rates rise: `d²PV/dF² = 2·N·dA/dF` at the money. On the toy 10y, the payer is −0.81 per bp². Dead instruments: 0.0.
- **The half-gamma trap:** never map `d(pv01)/dr` where pv01 is the fixed annuity. At the money `d²PV/dF² = 2·N·dA/dF`, but `d(N·A)/dF = N·dA/dF` is **half** of that. Known answer on a swap: at the money, `Γ = 2·Δpv01/Δpar` (`test_swap_atm_gamma_is_twice_the_annuity_pv01_slope` in `tests/test_toylib_ir.py`).
- **Conversions:**

  | Your library reports | To get Γ per bp² of r |
  |---|---|
  | Γ per (1%)² | ÷ 1e4 |
  | ∂²PV/∂r² with r decimal (per unit²) | × 1e-8 |
  | "change in DV01 per 1bp", where DV01 is the **total** own-rate delta | as is |
  | the same, where DV01 is an annuity pv01 | half-gamma trap: bump instead |
  | bond convexity C (yield decimal, same compounding as your `IRFwdRate` yield) | `C × dirty PV × 1e-8`. Some packages report C/100 or "dollar convexity": check against `PV(y+1bp) + PV(y−1bp) − 2·PV(y)` |
  | Black or SABR gamma ∂²V/∂F² at a fixed annuity | not total: bump instead |

- **Verify:** reprice at ±1bp and ±2bp. The Γ estimate must be stable across the two step sizes, and `½Γ·Δr²` must shrink the residual of a big move.

### `IRGamma` bucketed (sens2, ccy per bp² at each pillar)

- **Definition:** a diagonal gamma ladder (DEV-I13; gs returns a 12-column cross-gamma table). For each pillar i: `PV(+h_i) + PV(−h_i) − 2·PV` with h = 1bp. It is a portfolio function with the same keys and labels as the delta ladder.
- **Declare it** (`IRGamma: "..."`) if a per-pillar second bump is too expensive, or if your platform has no per-pillar shift.

### `IRVega` scalar (sens1, ccy per +1bp of normal vol)

- **Definition:** the PV change for +1bp of the normal implied vol at the strike. A long option is > 0.
- **Swaps and bonds:** `0.0`. Dead or expired instruments: 0.0.
- **From your library:**

  | Your library reports | Conversion |
  |---|---|
  | normal-model vega per 1bp normal | as is |
  | per 1 normal vol point (1% = 100bp) | ÷ 100 |
  | ∂V/∂σ_N with σ decimal | × 1e-4 |
  | lognormal or SABR vega per 1% lognormal | **a model change, not a scale.** Best: reprice with the normal vol +1bp, converted to your model's vol by your library's own normal↔lognormal map. At-the-money approximation only (σ_N ≈ σ_LN·F): `vega_N,bp ≈ vega_LN,1% × 0.01 / F`, with F decimal |

- **Keep the level consistent:** `IRAnnualImpliedVol` must be the **normal** vol that this vega is per bp of, or `VegaPnL = vega × Δσ` is meaningless.
- **Traps:**
  - vega per 1% taken as per bp (100× too big);
  - lognormal vega;
  - a straddle priced as one leg;
  - vega not 0 after expiry.
- **Verify:**
  - the at-the-money normal-model band `vega ≈ Annuity·√T·φ(0)·1e-4`, with `Annuity` and `T = ExpiryInYears` from the same config;
  - straddle = 2 × payer vega at ATM;
  - Sell = −Buy.

### `IRVega` bucketed (the vol cube)

- **Definition:** a portfolio function returning `{"<tail>;<expiry>": value}` with **the tail first**, as gs does: `"10Y;1Y"` is 1y into 10y. Use `labels: {mkt_type: IR VOL, mkt_asset: <ccy or cube id>, mkt_class: SWAPTION}`.
- **Key order:** most libraries key their cube as (expiry, tail), so swap the order.
- **Strike dimension:** if your cube has one, sum over it (the contract ladder is expiry × tail).
- **Scalar:** Σ cube ≈ the scalar vega.
- **Swaps and bonds:** `'{}'` (an empty ladder), as in `tests/assets/toy_usd_irs_full.yaml`.

### `IRVanna` and `IRVolga` (sens2)

- **Request them as** `IRVanna(aggregation_level='Type')` and `IRVolga(aggregation_level='Type')`: they are finite-difference measures (DEV-I9), and the bare name asks for a bucketed form that the contract does not have.
- **Vanna:** `d(IRDelta scalar)/dσ` per bp of r × bp of σ. With h = 1bp: `(_own_rate_greeks(m, t, vol_bp=+1)[0] − _own_rate_greeks(m, t, vol_bp=−1)[0]) / 2`.
- **Volga:** `PV(σ+1bp) + PV(σ−1bp) − 2·PV(σ)` per bp² of normal vol.
- **Library values:** a library's "vanna" is usually ∂²V/∂F∂σ at a fixed annuity (so not the total delta's slope), and its "volga" is often lognormal. Convert by bumping, not by scaling. Units: per unit² × 1e-8, per (1%)² ÷ 1e4.
- **Swaps and bonds:** `0.0`. Dead instruments: 0.0.

### `IRBasis` and `IRXccyDelta` (sens1)

- **IRBasis:** +1bp on the projection-vs-discount spread. In a multi-curve library, shift the projection curve's spread over discount, or the basis swap quotes.
- **IRXccyDelta:** +1bp on the cross-currency basis. It is relevant when the CSA or discounting is in another currency.
- **Single-curve or single-currency models:** `0.0` is the true value in your model (the contract says so).
- **Declare** only when the model has the basis but your library cannot bump it.

### `IRFwdRate` (rate, intensive)

- **Definition:** the own rate r:
  - a swap's par rate on its own schedule (forward-starting if the swap is);
  - a swaption's forward swap rate from expiry to termination;
  - a bond's yield to maturity (DEV-I12), in the one yield convention (compounding, day count) your delta and gamma are taken against.
- **Unit:** declare `bp`, `pct` or `decimal`; `bp` is recommended. gs itself says percent, and pricebt uses whatever unit you declare (DEV-I7).
- **Finiteness:** finite on **every held date including the exit date**:
  - a matured swap keeps its last par rate;
  - an expired swaption keeps the live forward of its underlying (physical-settlement convention);
  - a matured bond keeps a one-day yield, as `toylib.bond._yield` does.
- **Traps:**
  - percent vs bp;
  - NaN after maturity (the ARBS config has this known gap, `docs/v2/MERGE_NOTES_pnl_explain.md`);
  - returning the trade's fixed rate (the strike) instead of its live par rate;
  - a swap in bp and a swaption in decimal in one book.

### `IRSpotRate` (rate)

The par rate of the spot-starting instrument with the same final date (`toylib.irrisk.spot_par_bp`). For a bond, its yield.

### `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol` (vol, intensive)

- **Annual:** the normal vol at the strike. ATM: the ATM-forward normal vol for the same expiry and tail. Daily: `annual / sqrt(252)`.
- **Swaps and bonds:** `0.0`. After expiry: the last live value (R2-7).
- **A lognormal or SABR library:** return the **normal-equivalent** vol, either from your library's own converter or from Hagan's approximation. Say which in the function comment.
- **Trap:** a vol in percent or decimal next to a vega per bp. That is fine only if the level's declared unit says so and the P&L definition is built with that unit.

### `Theta` (theta, ccy per calendar day)

- **Definition:** one calendar day of carry, **holding the own rate r and the vol σ fixed**, total return (DEV-I15, R2-4):
  `Theta = Price(t+1d; r, σ fixed) + (flows Price drops in (t, t+1d]) − Price(t)`.
- **Swaps and swaptions: translate the curve.** Keep every forward and move the valuation date by one day, `DF'(x) = DF(x) / DF(t+1d)` (the `toylib.irrisk._TranslatedCurve` recipe, which keeps the CSA). A swaption uses the same F and σ with `T − 1/365`; a step onto expiry exercises on the frozen F.
- **Never roll the curve.** Rebuilding it on t+1 from the same quotes or zero rates by tenor keeps the zero rates, not the forwards. That is roll-down, and it belongs in the delta attribution as a ΔIRFwdRate.
- **Bonds:** reprice at t+1 at the **same yield**, and add the coupons paid in (t, t+1] (`toylib.bond.theta_1d`).
- **Shape A:** a curve wrapper that divides by `DF(t+1d)`, plus the evaluation date moved to t+1. A process-global evaluation date (QuantLib-style) is moved inside `try/finally` and restored, or better set from the translated market inside the PV call (section 1).
- **Shape B:** use a "forward valuation date, constant forwards" scenario if there is one. A vendor "theta" or "carry" code qualifies only if it is per calendar day, excludes roll-down and financing, and includes the day's coupon cash under the drop-paid-flows convention. Otherwise compute Theta by the translated reprice, or declare it.
- **Shape D:** a package's "carry" (coupon accrual minus repo) is **not** Theta: it has financing in it and holds no yield.
- **Traps:**
  - per-year theta: the in-flight `IRTheta` is `365 × Theta`, so never map `Theta` to a per-year function;
  - business-day theta: Friday→Monday is three calendar days, and the P&L attribute multiplies Theta by −365 × ΔExpiryInYears;
  - a rolled curve;
  - forgetting the coupon cash when `Price` drops paid flows;
  - including repo.
- **Verify:** the frozen-world identity. In your library, keep forwards and vols constant from t to t+1 (a translated market); then `Price(t+1) + cash − Price(t) == Theta` exactly (`test_frozen_world_theta_identity_swap_and_swaption` and `test_frozen_world_theta_identity_bond_including_a_coupon_step` in `tests/test_toylib_ir.py`). The sign of a long option is < 0.

### `ExpiryInYears` (time, intensive)

- **Definition:** `max(final_or_expiry − t, 0).days / 365`, in calendar days (ACT/365F). A swaption uses its expiry; a swap or bond uses its final date (DEV-I17).
- **Every library can supply it:** it is date arithmetic on the resolved terms, e.g. `{expr: 'max((resolved["expiration_date"] - pricebt_date).days, 0) / 365', unit: decimal}`. Declaring it is not honest.
- **Why the exact convention matters:** the theta attribute uses `−365 × ΔExpiryInYears` as "days elapsed". A library year fraction (ACT/360, business/252) mis-scales every theta P&L.

### `Annuity` (annuity, ccy)

- **Definition:** N·A, the PV of 1.0 per annum on the unit trade's fixed schedule (1e4 × the fixed-leg pv01), times the **signed notional**: pay-fixed swap > 0, receive-fixed < 0 (the sign of `IRDelta`, and `−dPV/dK`); a bought swaption > 0, payer or receiver (its underlying's annuity from expiry); a long bond > 0 (1.0 per annum on its schedule, per signed face).
- **Library sign trap:** a QuantLib-style `fixedLegBPS` is the fixed leg's own PV per bp, negative for a payer. For a swap, `Annuity = −1e4 × fixedLegBPS`. `check_asset.py`'s `swap_annuity_sign` FAILs the other sign.
- **Shape B:** `−[PV(K+1bp) − PV(K−1bp)] / 2e-4` needs only your PV (section 1).
- **Verify:**
  - put-call parity: `Price(payer K) − Price(receiver K) = Annuity × (F − K)`;
  - the ATM vega band above.

### `Cashflows` (table, `returns: frame`)

- **Definition:** the flows that `Price` still includes and will drop on their payment date (`payment_date > pricing date`), holder-signed, one row each.
- **Columns:** `payment_date`, `payment_amount`, `currency` and `payment_type` are required. `notional`, `rate`, `accrual_start_date`, `accrual_end_date`, `discount_factor` and the other gs columns are optional.
- **Total-return Price:** `Cashflows` is empty (R2-6); return `pd.DataFrame(columns=[...])` or `[]`.

  ```yaml
  cashflows: {expr: 'lib_cashflows(market, trade)', unit: ccy, returns: frame, scale_columns: [payment_amount, notional]}
  ```

- **Rules:** `scale_columns` must include `payment_amount`. Frames never convert currency. They are left out of `result_summary`/`risk_summary` (DEV-R11) and stay in `backtest.results`.
- **Verify:** across a payment date, the drop in `Price` ≈ that date's `payment_amount`. `BackTest.pnl_explain_table()` reports it as `cashflow_pnl`.

### Class extras

| Class | Measure | Definition | Notes |
|---|---|---|---|
| IRSwaption | `ProbabilityOfExercise` | the probability of finishing in the money under the annuity measure. Normal model: `Φ(d)` for a payer and `Φ(−d)` for a receiver, `d = (F−K)/(σ√T)` | intensive (`decimal`). After expiry, 1 or 0 as decided |
| Bond | `LightningDV01` | yield DV01, dP/dy per +1bp (< 0 long) | many packages report it positive (the loss convention): negate. From modified duration: `−D_mod × dirty PV × 1e-4` |
| Bond | `LightningOAS` | OAS over your reference curve; a bullet bond uses its Z-spread | intensive, rate unit |
| Bond | `ParSpread` | par asset-swap spread, or your library's par spread | intensive, rate unit; declare it if you have no swap curve |

### Unit and sign conversions (first-order sensitivities)

| Your library's number | Multiply by |
|---|---|
| PV change for **−1bp** (receiver-positive DV01) | −1 |
| **loss** for +1bp (positive for a long bond) | −1 |
| per 1% (100bp) | 0.01 |
| per unit rate (∂PV/∂r, r decimal) | 1e-4 |
| per 1mm notional, when the unit trade is N | N / 1e6 |

## 3. When to declare a measure unsupported, and why that is honest

Declare a measure (`unsupported_measures:`) when your library **genuinely cannot** produce it, and give a reason a reviewer can check. For example:

- "no vol cube; the platform prices swaptions off one ATM vol per expiry" (for `IRVega` bucketed);
- "the platform has no curve-shift request and no discount-only curve" (for `IRDiscountDeltaParallel`, `IRGammaParallel`);
- "no OAS model" (for `LightningOAS`).

Requesting the measure then raises `UnsupportedMeasureError` with that reason, so the caller sees the gap. A fake number would flow silently into hedges, risk triggers and P&L attribution.

**Not honest:**

- mapping `0.0` for exposure your model has;
- mapping a proxy with different semantics: an annuity pv01 as gamma's source, a per-year theta, a lognormal vega, a clean price;
- declaring something every library can compute: `ExpiryInYears`, `IRDailyImpliedVol` when the annual vol is mapped, the `0.0` vol measures of a swap or bond, and a swap's `Annuity` and `IRSpotRate` when the library prices a swap at a given fixed rate and has a par rate (`measures.py matrix --strict` flags each);
- leaving a `TODO` reason (`measures.py matrix` flags it).

**Consequences to accept:**

- a `PnlDefinition` or hedge that needs a declared measure raises for that asset;
- a declared swap or bond vega breaks mixed books with vol attribution (R2-8).

## 4. Verify numerically

**In your library, before pricebt:** for each measure you derived, reprice it independently with a different step (±2bp) or an independent method (analytic vs bump). Check the stability of Δ and Γ across step sizes, the frozen-world theta identity, Σ ladder vs parallel, and put-call parity.

**Through pricebt:** the block below runs as-is on the toy configs from the repository root. Point it at your own configs and instruments to check the contract identities end to end: buy/sell symmetry, signs, straddle additivity, the ATM vega band, vol-level consistency, parity, a one-day Taylor explain in the contract's units, ladder sums, bond sign, and a dead instrument. `tests/skills/test_skill_risk_measures.py` executes it.

```python
# runnable: the contract identities on the toy configs (PYTHONPATH=src;tests, repository root)
import datetime as dt
import math

from pricebt.instrument import Bond, IRSwap, IRSwaption
from pricebt.markets import PricingContext
from pricebt.risk import (Annuity, ExpiryInYears, IRAnnualImpliedVol, IRDailyImpliedVol, IRDelta, IRFwdRate,
                          IRGammaParallel, IRVanna, IRVega, IRVolga, LightningDV01, Price, Theta)
from pricebt.session import PricebtSession

PricebtSession.use(assets=["tests/assets/toy_usd_swaption.yaml", "tests/assets/toy_usd_irs_full.yaml", "tests/assets/toy_usd_bond.yaml"])
d1, d2 = dt.date(2024, 3, 4), dt.date(2024, 3, 5)
DELTA, VEGA = IRDelta(aggregation_level="Type"), IRVega(aggregation_level="Type")
VANNA, VOLGA = IRVanna(aggregation_level="Type"), IRVolga(aggregation_level="Type")


def val(inst, measure):
    """One number: inside a PricingContext an instrument's calc returns a future (gs behaviour)."""
    return float(inst.calc(measure).result())


def swaption(pay_or_receive="Pay", buy_sell="Buy", strike="ATM"):
    return IRSwaption(pay_or_receive, "10y", "USD", expiration_date="1y", strike=strike, notional_amount=1e6, buy_sell=buy_sell)


with PricingContext(pricing_date=d1):
    payer, sold, receiver, straddle = swaption(), swaption(buy_sell="Sell"), swaption("Receive"), swaption("Straddle")
    for inst in (payer, sold, receiver, straddle):
        inst.resolve()  # pin the ATM strike at d1, so d2 prices the same trade
    m = {k: val(payer, x) for k, x in [("pv", Price), ("delta", DELTA), ("gamma", IRGammaParallel), ("vega", VEGA), ("vanna", VANNA),
                                       ("volga", VOLGA), ("theta", Theta), ("r", IRFwdRate), ("vol", IRAnnualImpliedVol),
                                       ("T", ExpiryInYears), ("A", Annuity)]}
    # 1. buy_sell is folded into the sign: a sold option is exactly minus the bought one
    for x in (Price, DELTA, VEGA):
        assert val(sold, x) == -val(payer, x)
    # 2. a long payer: delta > 0, gamma > 0, vega > 0, theta < 0; a receiver's delta < 0
    assert m["delta"] > 0 and m["gamma"] > 0 and m["vega"] > 0 and m["theta"] < 0 and val(receiver, DELTA) < 0
    # 3. a straddle is a payer plus a receiver
    assert math.isclose(val(straddle, Price), val(payer, Price) + val(receiver, Price), rel_tol=1e-12)
    # 4. ATM normal vega per bp = Annuity * sqrt(T) * phi(0) * 1e-4
    assert math.isclose(m["vega"], m["A"] * math.sqrt(m["T"]) / math.sqrt(2 * math.pi) * 1e-4, rel_tol=1e-3)
    # 5. vol levels agree: daily = annual / sqrt(252)
    assert math.isclose(val(payer, IRDailyImpliedVol), m["vol"] / math.sqrt(252), rel_tol=1e-12)
    # 6. put-call parity off the money: payer - receiver = Annuity * (F - K), F from IRFwdRate (bp here)
    p50, r50 = swaption(strike="ATM+50"), swaption("Receive", strike="ATM+50")
    strike = p50.resolve(in_place=False).result().strike
    assert math.isclose(val(p50, Price) - val(r50, Price), val(p50, Annuity) * (val(p50, IRFwdRate) / 1e4 - strike), rel_tol=1e-9)

# 7. one day later, the Taylor expansion in the contract's units explains the P&L
with PricingContext(pricing_date=d2):
    pv2, r2, vol2 = val(payer, Price), val(payer, IRFwdRate), val(payer, IRAnnualImpliedVol)
dr, dv, days = r2 - m["r"], vol2 - m["vol"], (d2 - d1).days
explained = (m["delta"] * dr + 0.5 * m["gamma"] * dr ** 2 + m["vega"] * dv + m["vanna"] * dr * dv
             + 0.5 * m["volga"] * dv ** 2 + m["theta"] * days)
assert abs(pv2 - m["pv"] - explained) < 0.02 * abs(pv2 - m["pv"])

# 8. swap ladder vs scalar (exact here: the toy ladder buckets the scalar; yours: ~ scalar x dr/ds); long bond delta < 0
with PricingContext(pricing_date=d1):
    swap = IRSwap("Pay", "10y", "USD", fixed_rate=0.05, notional_amount=1e6)
    assert math.isclose(swap.calc(IRDelta).result().value.sum(), val(swap, DELTA), rel_tol=1e-9) and val(swap, DELTA) > 0
    bond = Bond(buy_sell="Buy", identifier="TOY 4.25 2034-11-15", size=1e6, settlement_currency="USD")
    assert val(bond, DELTA) < 0 and math.isclose(val(bond, DELTA), val(bond, LightningDV01), rel_tol=1e-3)

# 9. a dead instrument: after expiry an unexercised option has zero sensitivities and finite levels
with PricingContext(pricing_date=d1):
    otm = IRSwaption("Pay", "10y", "USD", expiration_date="1m", strike="ATM+200", notional_amount=1e6)
    otm.resolve()
with PricingContext(pricing_date=dt.date(2024, 6, 3)):
    assert val(otm, Price) == 0.0 and val(otm, DELTA) == 0.0 and val(otm, VEGA) == 0.0
    assert all(math.isfinite(val(otm, x)) for x in (IRFwdRate, IRAnnualImpliedVol, ExpiryInYears))
```

Over a backtest, the same explain runs every step as `BackTest.pnl_explain_table()`: attribution by measure, residual, and coupon cash. The `pricebt-pnl-attribution` skill covers it.
