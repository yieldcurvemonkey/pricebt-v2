# Capability matrix: a worksheet to fill for your library

Fill in one row per contract (measure, form) **before** you write YAML, from what you know about your own pricing library. The finished worksheet is your design and your review document. After you write the config, `measures.py matrix <config>` checks the result row by row.

**There is no "declare" column** for `IRSwap`, `IRSwaption` or `Bond` (`docs/v2/IR_STRICT_CONTRACT.md` R3-0, `docs/v2/BOND_DESIGN.md` decision 4.1): every row must be mapped, and a row your library has no call for is derived (bump-and-reprice on the primitives you already have, or arithmetic on the trade's static and repo data). Declaring a contract measure, a form or a preset of one is a load error.

## How to fill it

1. Print the rows for your class: `python skills/pricebt-risk-measures/scripts/measures.py contract IRSwaption` (or `IRSwap`, `Bond`).
2. For each row, choose one **how**:

   | how | when | what goes in the config |
   |---|---|---|
   | **native** | your library returns exactly this measure, in pricebt's unit and sign | `{expr: 'lib_call(...)', unit: ...}` |
   | **convert** | your library returns it in another unit or sign (per 1%, receiver-positive, per 100 face, clean) | the call times a documented factor |
   | **derive** | your library can reprice on a shifted market (curve, vol, valuation date) | a bump-and-reprice helper in `code:` ([implementing-measures.md](implementing-measures.md) section 1) |
   | **AD** | your library gives exact gradients (dual numbers, adjoints) | `(∂PV/∂s) / (∂r/∂s)` and similar |
   | **zero** | the contract defines the value as 0 (`contracts.ZERO_BY_CONVENTION`: vol measures on a swap or bond, single-curve basis, single-currency xccy) | `{expr: '0.0', unit: ...}` (empty ladder: `'{}'`); any other literal fails `check_asset.py` `ir_fake_constant` |

3. Write the **verified by** column: the independent check you ran. Examples: "±2bp reprice agrees to 1e-6", "put-call parity to 1e-9", "frozen-world theta identity", "Σ ladder = parallel DV01 × dr/ds".

## The worksheet

"pricebt expects" is the contract, per unit trade, holder-signed. r is the own rate (`IRFwdRate`) and σ is the normal vol at the strike. The "typical answers" column shows how each library shape usually lands:

- A: object library (QuantLib- or rateslib-style);
- B: platform or REST service;
- C: in-house functions;
- D: bond analytics package.

| Measure (form) | pricebt expects | Typical answers | Your library call | Native unit and sign | How | Expression or recipe | Verified by |
|---|---|---|---|---|---|---|---|
| `Price` | ccy, holder-signed; drops paid flows or is total return (pick one). Bond: the settlement-date value, not discounted | A/C `npv`; B "PV" code; D dirty price per 100 at standard settlement → × face/100, sign from `buy_sell` | | | | | |
| `IRDelta` (scalar) | ccy per +1bp of r, **total** derivative; payer and long option > 0, long bond < 0 | A AD or bump; B curve DV01 / (dr/ds), often receiver-positive; D yield DV01, often loss-positive | | | | | |
| `IRDelta` (bucketed) | ccy per +1bp per pillar, `labels.mkt_type: IR`, a portfolio function over `trades, weights` | A key-rate AD; B bucketed DV01 code (check sign and key format); C per-pillar bumps | | | | | |
| `IRDiscountDeltaParallel` | ccy per +1bp discount-curve shift only, projection forwards held (≈0 for an at-the-money swap) | A shift the discount curve object; single-curve: hold the forwards and rediscount the projected flows (never the parallel DV01, which moves the forwards too); B a discount-only scenario; D (bond priced off one yield) the bond's flows rediscounted on a ±1bp-shifted reference curve with the spread held | | | | | |
| `IRGammaParallel` | ccy per bp² of r, chain rule; payer swap < 0, long option > 0 | derive by the ±1bp formula; D convexity × dirty PV × 1e-8; never d(annuity pv01)/dr | | | | | |
| `IRGamma` (bucketed) | ccy per bp² per pillar, diagonal | derive per pillar (costly); a bond package without key-rate risk: bump the reference curve's pillars with the spread held | | | | | |
| `IRVega` (scalar) | ccy per +1bp **normal** vol; long > 0; swap and bond 0.0 | normal model: native; lognormal or SABR: bump through a normal↔lognormal map; per 1 vol point ÷ 100 | | | | | |
| `IRVega` (bucketed) | `{"<tail>;<expiry>": v}` (tail first), `labels.mkt_type: IR VOL`; swap and bond `'{}'` | cube vega, swap the key order, sum strikes; a single-vol model: the whole vega at the trade's nearest `'<tail>;<expiry>'` point | | | | | |
| `IRVanna` | ccy per bp(r)·bp(σ); swap and bond 0.0 | derive: (Δ(σ+1bp) − Δ(σ−1bp)) / 2 | | | | | |
| `IRVolga` | ccy per bp² of σ; swap and bond 0.0 | derive: PV(σ+1) + PV(σ−1) − 2PV | | | | | |
| `IRBasis` | ccy per +1bp basis spread | multi-curve: bump the spread; single-curve: zero | | | | | |
| `IRXccyDelta` | ccy per +1bp xccy basis | single-currency: zero; else bump the basis quotes | | | | | |
| `IRFwdRate` | own rate, bp recommended, intensive, finite after death | swap par rate; swaption forward swap rate; bond YTM in **your delta's** yield convention | | | | | |
| `IRSpotRate` | spot-starting par rate to the same end; bond: yield | A/C par rate from the curve; bond: = `IRFwdRate` | | | | | |
| `IRAnnualImpliedVol` | normal vol at the strike; swap and bond 0.0 | normal model: native; lognormal: the normal equivalent | | | | | |
| `IRAnnualATMImpliedVol` | ATM-forward normal vol; swap and bond 0.0 | the vol surface at ATM | | | | | |
| `IRDailyImpliedVol` | annual / sqrt(252); swap and bond 0.0 | always derivable from the annual vol | | | | | |
| `Theta` | ccy per **calendar day**, r and σ fixed, curve translated, total return | derive by the translated-curve reprice; a vendor theta only if per day, no roll-down, no financing. Bond: same yield to the next business day, ÷ its calendar days | | | | | |
| `ExpiryInYears` | `max(end − t, 0).days / 365` | always derivable from the resolved dates | | | | | |
| `Annuity` | N·A = 1e4 × fixed-leg pv01, ccy; payer / bought / long > 0 | A/C annuity or pv01 × 1e4 (QuantLib-style `fixedLegBPS`: × −1e4); B pv01 code × 1e4, or `−[PV(K+1bp) − PV(K−1bp)] / 2e-4` | | | | | |
| `Cashflows` (frame) | flows still in `Price` (`payment_date > t`), holder-signed; empty for total return. Bond: `payment_date` = the trade date `Price` drops the flow | A/C cashflow schedule; B a cashflow report; a total-return `Price`: the empty frame; D the coupon schedule with each date moved back by the settlement lag | | | | | |
| `ParSpread` (swap, swaption) | floating-leg spread making Price 0, rate unit, intensive, direction-independent | one curve, matching schedules: `K − IRFwdRate` (swaption: strike − forward); else solve PV(spread) = 0 | | | | | |
| `FairPremium` (swap, swaption) | `Price / DF(premium settlement)`, ccy | settlement = the premium payment date or spot; a DF call or `exp(−z·t)` | | | | | |
| `ForwardPrice` (swap, swaption) | `Price / DF(expiry)`, ccy; Price on or after it | the DF to a swaption's expiry / a swap's final date | | | | | |
| `PremiumCents` (swap, swaption) | `Price / \|notional_amount\|`, notional_level unit (bp = cents per 100), intensive | always derivable from Price | | | | | |
| `LocalAnnuityInCents` (swap, swaption) | `Annuity / \|notional_amount\|`, decimal, intensive | always derivable from Annuity | | | | | |
| `CompoundedFixedRate` (swap, swaption) | `(1 + K/f)^f − 1`, rate unit, intensive | the resolved fixed rate or strike and the fixed-leg frequency | | | | | |
| `CRIFIRCurve` (frame; swap, swaption) | SIMM CRIF rows from the unit trade's delta ladder; Σ Amount = Σ `IRDelta` ladder | the bucketed delta with `weights=[1.0]`, pillars as lower-case SIMM tenors | | | | | |
| `PnlExplain` (bucketed; swap, swaption) | value change market → `market_to` by risk factor, no time | a buckets portfolio function: reprice on `market_to` seen from the pricing date | | | | | |
| `ProbabilityOfExercise` (swaption) | 0..1 under the annuity measure | normal model: Φ(±d) | | | | | |
| `LightningDV01` (bond) | dP/dy per +1bp, < 0 long (= the `IRDelta` scalar) | D yield DV01 (negate a loss-positive one) or −D_mod × P × 1e-4 | | | | | |
| `LightningOAS` (bond) | spread, rate unit | D OAS; a bullet bond: its Z-spread (solve the parallel spread over the reference curve that reprices the dirty price) | | | | | |
| `ParSpread` (bond) | par asset-swap spread, rate unit | D par ASW, or the library's par spread; state the swap curve and the spread's sign convention in the function comment | | | | | |
| `FairPremium` (bond) | `Price` | always: map it to the `Price` function | | | | | |
| `ForwardPrice` (bond) | financed forward value at H = settlement + 1 calendar month: `Price × (1 + RepoRate × τ(s,H)) − Σ C × (1 + RepoRate × τ(c,H))` | arithmetic on `Price`, `RepoRate` and the coupon schedule; τ in the repo day count | | | | | |
| `PremiumCents` (bond) | `Price / \|face\|` (pct: the dirty price per 100), intensive | always derivable from Price | | | | | |
| `LocalAnnuityInCents` (bond) | `Annuity / \|face\|`, intensive | always derivable from Annuity | | | | | |
| `CompoundedFixedRate` (bond) | `(1 + c/f)^f − 1` of the coupon, intensive | static data | | | | | |
| `CRIFIRCurve` (frame; bond) | SIMM rows from the `IRDelta` ladder | as the swap row | | | | | |
| `PnlExplain` (bucketed; bond) | value change market → `market_to` by factor (`IR`, e.g. `CREDIT`), no time | reprice on `market_to` from the pricing date, curve moved and spread moved separately | | | | | |
| `CleanPrice` (bond) | per 100 face at standard settlement; `DirtyPrice − 100 × AccruedInterest / face` | D the quoted clean price (check it is for standard settlement) | | | | | |
| `DirtyPrice` (bond) | `100 × Price / face` (signed face), intensive | always derivable from Price | | | | | |
| `AccruedInterest` (bond) | ccy, holder-signed, to standard settlement, bond's accrual convention (UST ACT/ACT ICMA); 0 on a coupon settlement date | D accrued per 100 × face / 100; mind ex-coupon conventions | | | | | |
| `ModifiedDuration` (bond) | `−(1/P) dP/dy`, years, y the `IRFwdRate` convention, P dirty | D native (check the yield convention matches); or `(P(y−h) − P(y+h)) / (2hP)` | | | | | |
| `Convexity` (bond) | `(1/P) d²P/dy²`, years² | D native (some report C/100); or `(P(y+h) + P(y−h) − 2P) / (h²P)` | | | | | |
| `DaysToSettlement` (bond) | calendar days to standard settlement; `unit: number`, `scale_with_quantity: false` | the bond's settlement lag on its calendar (UST T+1) | | | | | |
| `RepoRate` (bond) | simple rate in the repo day count (USD ACT/360); overnight GC or special, or a term rate locked at the trade date | a repo fixing series (GC index, a special spread per identifier), or a term quote pinned in `resolve` | | | | | |
| `RepoHaircut` (bond) | fraction not financed (decimal 0.02 = 2%) | a config default or a per-trade kwarg | | | | | |
| `FinancingToDate` (bond) | cumulative repo interest since the trade's settlement, principal `(1 − h) × Price(trade date)` pinned at resolve; long ≤ 0; 0 on the trade date | Σ over calendar days between the two settlement dates of principal × `RepoRate`(day) / basis; weekends and holidays take the last business day's fixing | | | | | |
| `Carry` (bond) | clean value now − clean forward value at H | `(Price − AccruedInterest) − (ForwardPrice − accrued at H)`; a vendor "carry" only if it matches this horizon and repo | | | | | |
| `RollDown` (bond) | clean value at H on the rolled-down reference curve − clean value now | reprice at H with the curve's shape held by time to maturity and the spread held; flat curve: pull to par at constant yield | | | | | |

**Book-wide decisions** (write them above the table):

- the unit of every level: bp, pct or decimal, the same for every asset in a book;
- the `Price` convention: total return or drop paid flows;
- the vol model, and how you convert to normal;
- the bond yield convention (compounding, day count), settlement lag and calendar, and accrual convention;
- the bond's repo: GC or special, overnight or term (locked at the trade date), repo day count, haircut, where the historical fixings come from, and the roll-down reference curve;
- the bump size and whether you honour `pricebt_bump_size`;
- the settlement convention after a swaption's expiry;
- how `resolve` folds `buy_sell` and pins the strike, expiry and maturity.

## Completed examples in this repository

- **Full contract, derived** (the toy library has almost no native risk, so almost everything is **derive** or **zero**):
  - `tests/assets/toy_usd_swaption.yaml`: Bachelier, flat vol, physical settlement;
  - `tests/assets/toy_usd_irs_full.yaml`: a total-return swap with an empty `Cashflows`;
  - `tests/assets/toy_usd_irs.yaml`: the same contract with the annuity pv01 as the `IRDelta` scalar (exact only at the money, R2-1), plus the swap P&L recipe's custom `IRTheta` / `YearFraction` / `CashPaidToDate`;
  - `tests/assets/toy_usd_bond.yaml`: the settlement-date value that drops paid coupons, a `Cashflows` frame, same-yield theta, and the financing rows (GC repo with a special spread per identifier, overnight or term, a haircut).

  The functions are in `tests/toylib/`.
- **A bank-style service, every row mapped:** the fictional bank-SDK example `skills/pricebt-connect-pricing-library/example/meridian_usd_irs.yaml`. It derives the own-rate `IRDelta`, `Annuity` and `IRSpotRate` from PV, DV01 and the par rate in ONE batch (`implementing-measures.md` section 1, Shape B), computes gamma, the gamma ladder and `Theta` on the SDK's scenario markets, converts its `CASHFLOWS` code to the frame, and maps the eight strict additions. `measures.py matrix --strict` passes on it.

## What `measures.py matrix` reports

One row per contract (measure, form):

| status | means |
|---|---|
| `MAPPED` | a `risk_measures:` slot provides it (function, unit; `via` a preset key such as `IRDeltaParallel`) |
| `MISSING` | not provided. A declared row is also `MISSING` (its reason shown), and the declaration itself is listed under the problems: `Bond/IRSwap/IRSwaption configs must map every contract measure; unsupported_measures cannot satisfy them` |

Then the load problems and warnings, and the next step. For a gap, `measures.py block <config>` prints the **mapping skeleton** for every contract class (`contracts.mapping_skeleton`, the same text the load error ends with: `functions:` / `portfolio_functions:` stubs with an allowed unit, `scale_with_quantity: false` for an intensive kind, and the `risk_measures:` lines; each `expr` is `contracts.SKELETON_EXPR`, which does not compile until you write it).

Run `measures.py matrix` on each to see a filled matrix:

```powershell
$env:PYTHONPATH = "src;tests"
python skills/pricebt-risk-measures/scripts/measures.py matrix tests/assets/toy_usd_bond.yaml
python skills/pricebt-risk-measures/scripts/measures.py matrix tests/assets/toy_usd_irs.yaml
```
