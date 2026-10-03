# Capability matrix: a worksheet to fill for your library

Fill in one row per contract (measure, form) **before** you write YAML, from what you know about your own pricing library. The finished worksheet is your design, your review document, and the source of the `unsupported_measures:` reasons. After you write the config, `measures.py matrix <config>` checks the result row by row.

## How to fill it

1. Print the rows for your class: `python skills/pricebt-risk-measures/scripts/measures.py contract IRSwaption` (or `IRSwap`, `Bond`).
2. For each row, choose one **how**:

   | how | when | what goes in the config |
   |---|---|---|
   | **native** | your library returns exactly this measure, in pricebt's unit and sign | `{expr: 'lib_call(...)', unit: ...}` |
   | **convert** | your library returns it in another unit or sign (per 1%, receiver-positive, per 100 face, clean) | the call times a documented factor |
   | **derive** | your library can reprice on a shifted market (curve, vol, valuation date) | a bump-and-reprice helper in `code:` ([implementing-measures.md](implementing-measures.md) section 1) |
   | **AD** | your library gives exact gradients (dual numbers, adjoints) | `(∂PV/∂s) / (∂r/∂s)` and similar |
   | **zero** | the instrument has no exposure to that factor under **any** model (swap or bond vol measures, single-curve basis, single-currency xccy) | `{expr: '0.0', unit: ...}` (empty ladder: `'{}'`) |
   | **declare** | your library genuinely cannot produce it | `unsupported_measures: {Measure: "specific, checkable reason"}` |

3. Write the **verified by** column: the independent check you ran. Examples: "±2bp reprice agrees to 1e-6", "put-call parity to 1e-9", "frozen-world theta identity", "Σ ladder = parallel DV01 × dr/ds".

## The worksheet

"pricebt expects" is the contract, per unit trade, holder-signed. r is the own rate (`IRFwdRate`) and σ is the normal vol at the strike. The "typical answers" column shows how each library shape usually lands:

- A: object library (QuantLib- or rateslib-style);
- B: platform or REST service;
- C: in-house functions;
- D: bond analytics package.

| Measure (form) | pricebt expects | Typical answers | Your library call | Native unit and sign | How | Expression or recipe | Verified by |
|---|---|---|---|---|---|---|---|
| `Price` | ccy, holder-signed; drops paid flows or is total return (pick one) | A/C `npv`; B "PV" code; D dirty price per 100 → × face/100, sign from `buy_sell` | | | | | |
| `IRDelta` (scalar) | ccy per +1bp of r, **total** derivative; payer and long option > 0, long bond < 0 | A AD or bump; B curve DV01 / (dr/ds), often receiver-positive; D yield DV01, often loss-positive | | | | | |
| `IRDelta` (bucketed) | ccy per +1bp per pillar, `labels.mkt_type: IR`, a portfolio function over `trades, weights` | A key-rate AD; B bucketed DV01 code (check sign and key format); C per-pillar bumps | | | | | |
| `IRDiscountDeltaParallel` | ccy per +1bp discount-curve shift only | A shift the discount curve object; single-curve: the parallel DV01; B declare if there is no discount-only shift | | | | | |
| `IRGammaParallel` | ccy per bp² of r, chain rule; payer swap < 0, long option > 0 | derive by the ±1bp formula; D convexity × dirty PV × 1e-8; never d(annuity pv01)/dr | | | | | |
| `IRGamma` (bucketed) | ccy per bp² per pillar, diagonal | derive per pillar (costly), or declare | | | | | |
| `IRVega` (scalar) | ccy per +1bp **normal** vol; long > 0; swap and bond 0.0 | normal model: native; lognormal or SABR: bump through a normal↔lognormal map; per 1 vol point ÷ 100 | | | | | |
| `IRVega` (bucketed) | `{"<tail>;<expiry>": v}` (tail first), `labels.mkt_type: IR VOL`; swap and bond `'{}'` | cube vega, swap the key order, sum strikes; or declare (a single-vol model has no cube) | | | | | |
| `IRVanna` | ccy per bp(r)·bp(σ); swap and bond 0.0 | derive: (Δ(σ+1bp) − Δ(σ−1bp)) / 2 | | | | | |
| `IRVolga` | ccy per bp² of σ; swap and bond 0.0 | derive: PV(σ+1) + PV(σ−1) − 2PV | | | | | |
| `IRBasis` | ccy per +1bp basis spread | multi-curve: bump the spread; single-curve: zero | | | | | |
| `IRXccyDelta` | ccy per +1bp xccy basis | single-currency: zero; else bump the basis quotes | | | | | |
| `IRFwdRate` | own rate, bp recommended, intensive, finite after death | swap par rate; swaption forward swap rate; bond YTM in **your delta's** yield convention | | | | | |
| `IRSpotRate` | spot-starting par rate to the same end; bond: yield | A/C par rate from the curve; bond: = `IRFwdRate` | | | | | |
| `IRAnnualImpliedVol` | normal vol at the strike; swap and bond 0.0 | normal model: native; lognormal: the normal equivalent | | | | | |
| `IRAnnualATMImpliedVol` | ATM-forward normal vol; swap and bond 0.0 | the vol surface at ATM | | | | | |
| `IRDailyImpliedVol` | annual / sqrt(252); swap and bond 0.0 | always derivable from the annual vol | | | | | |
| `Theta` | ccy per **calendar day**, r and σ fixed, curve translated, total return | derive by the translated-curve reprice; a vendor theta only if per day, no roll-down, no financing | | | | | |
| `ExpiryInYears` | `max(end − t, 0).days / 365` | always derivable from the resolved dates | | | | | |
| `Annuity` | N·A = 1e4 × fixed-leg pv01, ccy; payer / bought / long > 0 | A/C annuity or pv01 × 1e4 (QuantLib-style `fixedLegBPS`: × −1e4); B pv01 code × 1e4, or `−[PV(K+1bp) − PV(K−1bp)] / 2e-4` | | | | | |
| `Cashflows` (frame) | flows still in `Price` (`payment_date > t`), holder-signed; empty for total return | A/C cashflow schedule; B a cashflow report; else declare | | | | | |
| `ProbabilityOfExercise` (swaption) | 0..1 under the annuity measure | normal model: Φ(±d) | | | | | |
| `LightningDV01` (bond) | dP/dy per +1bp, < 0 long | D yield DV01 (negate a loss-positive one) or −D_mod × P × 1e-4 | | | | | |
| `LightningOAS` (bond) | spread, rate unit | D OAS or Z-spread; declare without an OAS model | | | | | |
| `ParSpread` (bond) | par asset-swap spread, rate unit | D ASW; declare without a swap curve | | | | | |

**Book-wide decisions** (write them above the table):

- the unit of every level: bp, pct or decimal, the same for every asset in a book;
- the `Price` convention: total return or drop paid flows;
- the vol model, and how you convert to normal;
- the bond yield convention (compounding, day count);
- the bump size and whether you honour `pricebt_bump_size`;
- the settlement convention after a swaption's expiry;
- how `resolve` folds `buy_sell` and pins the strike, expiry and maturity.

## Completed examples in this repository

- **Full contract, derived** (the toy library has almost no native risk, so almost everything is **derive** or **zero**):
  - `tests/assets/toy_usd_swaption.yaml`: Bachelier, flat vol, physical settlement;
  - `tests/assets/toy_usd_irs_full.yaml`: a total-return swap with an empty `Cashflows`;
  - `tests/assets/toy_usd_bond.yaml`: dirty PV that drops paid coupons, a `Cashflows` frame, same-yield theta.

  The functions are in `tests/toylib/`.
- **A PV-only service, honest declarations:** the fictional bank-SDK example `skills/pricebt-connect-pricing-library/example/meridian_usd_irs.yaml`. It derives the own-rate `IRDelta`, `Annuity` and `IRSpotRate` from PV, DV01 and the par rate (`implementing-measures.md` section 1, Shape B), maps the zeros and `ExpiryInYears`, and declares only `IRGammaParallel`, `IRGamma`, `Theta` and `Cashflows`, each with the reason (no scenario request, no schedule call). `measures.py matrix --strict` passes on it.
- **Declaration-only (a test fixture, not a pattern):** `tests/assets/toy_usd_irs.yaml` maps `Price`, `IRDelta`, `IRFwdRate` and `IRGammaParallel` and declares the rest, including the **zero** vol rows, `ExpiryInYears`, `Annuity` and `IRSpotRate`. The loader allows that, but those four kinds are declarations every library can avoid: `measures.py matrix` prints a hint on each and `--strict` exits 1. Mapping `'0.0'` for the zeros keeps mixed books with vol attribution working (R2-8).

Run `measures.py matrix` on each to see a filled matrix:

```powershell
$env:PYTHONPATH = "src;tests"
python skills/pricebt-risk-measures/scripts/measures.py matrix tests/assets/toy_usd_bond.yaml
python skills/pricebt-risk-measures/scripts/measures.py matrix tests/assets/toy_usd_irs.yaml
```
