# The measure catalogue (`pricebt.risk`)

`pricebt.risk` holds the whole gs_quant 2.1.17 measure catalogue as **data**. That is 124 measure instances and presets plus the `PnlExplain` family, with gs's Python names, `measure_type` strings, asset classes, gs units and parameter classes, plus the 11 pricebt-only bond and financing measures of section 6 (`pricebt.risk.PRICEBT_MEASURES`). pricebt computes none of them: an asset config maps each measure you use to a function. `tests/skills/test_skill_risk_measures.py` checks that every exported measure is listed here.

How to read the tables:

- **Class:**
  - *plain* is a `RiskMeasure`, requested by name (`Theta`).
  - *ccy-param* is `RiskMeasureWithCurrencyParameter`, callable as `Price(currency="EUR")`.
  - *FD-param* is `RiskMeasureWithFiniteDifferenceParameter`, callable as `IRDelta(aggregation_level="Type", bump_size=..., ...)`. Bare, it asks for the bucketed form; with `aggregation_level` `Type`, `Asset` or `Class` it asks for the scalar form.
  - *preset* is a parameterised instance with its own name (gs `risk/measures.py`). It falls back to its base measure's mapping.
- **gs unit:** what gs declares (`Percent`, `BPS`, ...). pricebt instead reports the unit your config declares on the function (DEV-I7), e.g. `IRFwdRate` in bp.

## 1. The IR contract (`IRSwap`, `IRSwaption`, `Bond`)

An asset whose `instrument:` is one of these three classes must, for every row and form of its class, **map** it to a function with an allowed unit. That is the only way for all three (`contracts.STRICT_CLASSES`; DEV-I11 amended, `docs/v2/IR_STRICT_CONTRACT.md`, `docs/v2/BOND_DESIGN.md` decision 4.1): declaring a contract measure, a form or a preset of one under `unsupported_measures:` is a load error, and a literal constant is honest only for `contracts.ZERO_BY_CONVENTION`. Every other measure name is unrestricted. The full one-line semantics are in `src/pricebt/risk/contracts.py`; print them with `python skills/pricebt-risk-measures/scripts/measures.py contract IRSwaption` (or `Bond`). The table below is the swap and swaption view; the Bond's 40 rows, several with Bond-specific text, are in section 1.1.

| Measure | Kind | Forms | Allowed units | Classes | Semantics (short) |
|---|---|---|---|---|---|
| `Price` | value | scalar | ccy | all 3 | holder-signed PV of one unit trade; drops paid flows or is total return, consistently with `Cashflows` (Bond: section 1.1) |
| `IRDelta` | sens1 | scalar, bucketed | ccy_per_bp | all 3 | s: **total** dPV/dr per bp of the own rate r (`IRFwdRate`), strike vol fixed; b: curve ladder per pillar |
| `IRDiscountDeltaParallel` | sens1 | scalar | ccy_per_bp | all 3 | +1bp parallel shift of the discount curve only |
| `IRGammaParallel` | sens2 | scalar | ccy_per_bp2 | all 3 | chain-rule d²PV/dr² per bp² (never d(annuity pv01)/dr) |
| `IRGamma` | sens2 | bucketed | ccy_per_bp2 | all 3 | diagonal gamma ladder (DEV-I13) |
| `IRVega` | sens1 | scalar, bucketed | ccy_per_bp | all 3 | per +1bp **normal** vol at the strike; cube keys `'<tail>;<expiry>'`; swaps and bonds 0.0 |
| `IRVanna` | sens2 | scalar | ccy_per_bp2 | all 3 | d(IRDelta scalar)/dσ per bp·bp; request with `aggregation_level='Type'` (DEV-I9) |
| `IRVolga` | sens2 | scalar | ccy_per_bp2 | all 3 | d²PV/dσ² per bp²; request with `aggregation_level='Type'` |
| `IRBasis` | sens1 | scalar | ccy_per_bp | all 3 | +1bp projection-vs-discount basis; single-curve 0.0 |
| `IRXccyDelta` | sens1 | scalar | ccy_per_bp | all 3 | cross-currency basis delta; single-currency 0.0 |
| `IRFwdRate` | rate | scalar | bp, pct, decimal (intensive) | all 3 | own rate: swap par rate, swaption forward swap rate, bond YTM (DEV-I12); finite on every held date |
| `IRSpotRate` | rate | scalar | bp, pct, decimal | all 3 | par rate of the spot-starting equivalent; bond: its yield |
| `IRAnnualImpliedVol` | vol | scalar | bp, pct, decimal | all 3 | normal vol at the strike; swaps and bonds 0.0 |
| `IRAnnualATMImpliedVol` | vol | scalar | bp, pct, decimal | all 3 | ATM-forward normal vol; swaps and bonds 0.0 |
| `IRDailyImpliedVol` | vol | scalar | bp, pct, decimal | all 3 | annual / sqrt(252); swaps and bonds 0.0 |
| `Theta` | theta | scalar | ccy | all 3 | one **calendar day**, own rate and vol fixed, total return (DEV-I15) |
| `ExpiryInYears` | time | scalar | decimal, number (intensive) | all 3 | `max(final_or_expiry − t, 0).days / 365` (DEV-I17) |
| `Annuity` | annuity | scalar | ccy | all 3 | N·A = 1e4 × fixed-leg pv01, signed notional (payer / bought / long > 0) |
| `Cashflows` | table | frame | (frame) | all 3 | flows `Price` still includes, `payment_date > t`; empty for a total-return `Price` |
| `ParSpread` | rate | scalar | bp, pct, decimal (intensive) | all 3 (Bond text: 1.1) | floating-leg spread making Price 0; one curve and matching schedules: `K − IRFwdRate`; direction-independent (DEV-I19) |
| `FairPremium` | value | scalar | ccy | all 3 (Bond text: 1.1) | `Price / DF(premium settlement)` (premium payment date, else spot; no spot lag: Price) |
| `ForwardPrice` | value | scalar | ccy | all 3 (Bond text: 1.1) | `Price / DF(expiry)`, the date `ExpiryInYears` counts to; Price after it |
| `PremiumCents` | notional_level | scalar | bp, pct, decimal, number (intensive) | all 3 (Bond text: 1.1) | `Price / \|notional_amount\|` (bp: cents per 100 of notional) |
| `LocalAnnuityInCents` | notional_level | scalar | bp, pct, decimal, number (intensive) | all 3 (Bond text: 1.1) | `Annuity / \|notional_amount\|`, holder-signed |
| `CompoundedFixedRate` | rate | scalar | bp, pct, decimal (intensive) | all 3 (Bond text: 1.1) | `(1 + K/f)^f − 1` of the fixed rate / strike; annual leg: K |
| `CRIFIRCurve` | table | frame | (frame) | all 3 (Bond text: 1.1) | SIMM CRIF IR-curve delta rows; Σ `Amount` = Σ `IRDelta` ladder; `scale_columns: [Amount]` |
| `PnlExplain` | value | bucketed | ccy | all 3 (Bond text: 1.1) | a `returns: buckets` portfolio function reading `market_to`; rows by `mkt_type`; `PnlExplainClose` resolves here |
| `ProbabilityOfExercise` | prob | scalar | decimal, number | IRSwaption | probability of finishing in the money (annuity measure) |

**Kinds and units.**

- value, theta and annuity are `ccy`; sens1 is `ccy_per_bp`; sens2 is `ccy_per_bp2`. These are extensive: pricebt multiplies them by `quantity_`.
- rate, vol, time, prob, notional_level and days are intensive: `scale_with_quantity` must be false, which is the default for `bp`, `pct` and `decimal` (set it for `number`).
- days (`DaysToSettlement` only) allows `number` alone, so it always needs `scale_with_quantity: false`.
- table is a `functions:` entry with `returns: frame`, whose `scale_columns` include `payment_amount` (`Cashflows`) or `Amount` (`CRIFIRCurve`).

**Presets that count toward a contract row** (R2-10): `IRDeltaParallel` and `IRDeltaLocalCcy` → `IRDelta`; `IRVegaParallel` and `IRVegaLocalCcy` → `IRVega`; `IRBasisParallel` → `IRBasis`; `IRXccyDeltaParallel` → `IRXccyDelta`; `IRGammaParallelLocalCcy` → `IRGammaParallel`; `IRDiscountDeltaParallelLocalCcy` → `IRDiscountDeltaParallel`; `PnlExplainClose` → `PnlExplain`. Declaring a contract measure or one of these presets is a load error on every contract class.

**Zero by convention** (`contracts.ZERO_BY_CONVENTION`, `{class: {measure: reason}}`): the only contract rows a literal `0.0` (or an empty ladder) may answer.

| Class | Measures | Reason (verbatim) |
|---|---|---|
| `IRSwap` | `IRVega`, `IRVanna`, `IRVolga`, `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol` | no optionality: the contract defines vol exposure and vol levels as 0 (R2-8) |
| `IRSwap`, `IRSwaption` | `IRBasis` | single curve: the contract defines the basis delta as 0 for a single-curve library |
| `IRSwap`, `IRSwaption` | `IRXccyDelta` | single currency: the contract defines the cross-currency delta as 0 |
| `Bond` | `IRVega`, `IRVanna`, `IRVolga`, `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol` | a bullet bond has no optionality: vol exposure and vol levels are 0 (R2-8) |
| `Bond` | `IRBasis` | a bullet bond is discounted on one curve: no projection-vs-discount basis |
| `Bond` | `IRXccyDelta` | a bond pays in one currency: no cross-currency basis |

The IR-relevant measures deliberately outside every contract, with the reason, are `contracts.EXCLUDED` (listed in the skill's [SKILL.md](../SKILL.md), "Excluded, with reasons"). `LightningDV01` and `LightningOAS` left that list when they became Bond contract rows.

### 1.1 The Bond contract (40 rows)

`contracts.CONTRACTS["Bond"]`, in contract order: the IR base with Bond text for `Price`, `Theta` and `Cashflows`, the gs measures a cash bond can answer, the pricebt bond analytics (DEV-I20) and the financing contract (DEV-I21). Holder-signed, per unit trade (one signed face amount); pricebt applies quantity. **H**, the carry horizon, is the settlement date plus one calendar month, rolled to the following business day of the bond's calendar. The yield to maturity is `IRFwdRate`: there is no separate yield measure, by design (two names for one number could drift apart). Units: value/theta/annuity `ccy`, sens1 `ccy_per_bp`, sens2 `ccy_per_bp2`, rate and notional_level bp/pct/decimal (notional_level also number), time decimal/number, days number.

| # | Measure | Kind | Forms | Bond semantics (condensed from the contract text) | Dead |
|---|---|---|---|---|---|
| 1 | `Price` | value | scalar | the settlement-date market value: (clean + accrued at standard settlement) per 100 × face / 100, holder-signed (long > 0), **not** discounted from settlement to the pricing date (DEV-I20); drops a flow on the first trade date whose standard settlement is on or after the flow's payment date | 0 |
| 2 | `IRDelta` | sens1 | scalar, bucketed | total dPrice/dy per bp of the yield; ladder per pillar; long bond < 0 | 0 |
| 3 | `IRDiscountDeltaParallel` | sens1 | scalar | +1bp of the discount curve only | 0 |
| 4 | `IRGammaParallel` | sens2 | scalar | chain-rule d²Price/dy² per bp²; bullet bond > 0 | 0 |
| 5 | `IRGamma` | sens2 | bucketed | diagonal gamma ladder (DEV-I13) | empty |
| 6-8 | `IRVega`, `IRVanna`, `IRVolga` | sens1 / sens2 | scalar (+ bucketed vega) | 0.0 / `{}` by convention | 0 |
| 9 | `IRBasis` | sens1 | scalar | 0.0 by convention (one curve) | 0 |
| 10 | `IRXccyDelta` | sens1 | scalar | 0.0 by convention (one currency) | 0 |
| 11 | `IRFwdRate` | rate | scalar | the yield to maturity (DEV-I12), in the convention delta and gamma use; intensive | finite (last value) |
| 12 | `IRSpotRate` | rate | scalar | the yield | finite |
| 13-15 | `IRAnnualImpliedVol`, `IRAnnualATMImpliedVol`, `IRDailyImpliedVol` | vol | scalar | 0.0 by convention | 0 |
| 16 | `Theta` | theta | scalar | carry per calendar day with the yield held: `[Price(nb, same yield) + flows Price drops in (t, nb] − Price(t)] / (nb − t).days`, nb the next business day (DEV-I15); **financing is not in Theta** | 0 |
| 17 | `ExpiryInYears` | time | scalar | `max(maturity − t, 0).days / 365` (DEV-I17) | 0 |
| 18 | `Annuity` | annuity | scalar | PV of 1.0 per annum on the bond's schedule, per signed face; long > 0 | 0 |
| 19 | `Cashflows` | table | frame | the flows still in `Price`, holder-signed; `payment_date` = the trade date on which `Price` drops the flow (T+1: the business day before a business-day coupon date); a financed position's engine books these rows as cash (DEV-E22) | empty |
| 20 | `LightningDV01` | sens1 | scalar | yield DV01: Price change for +1bp of yield (= the `IRDelta` scalar) | 0 |
| 21 | `LightningOAS` | rate | scalar | OAS over the library's reference curve (bullet: its Z-spread); intensive | finite |
| 22 | `ParSpread` | rate | scalar | par asset-swap spread (or the library's par spread); intensive | finite |
| 23 | `FairPremium` | value | scalar | the amount the holder pays at standard settlement: `Price` itself (DEV-I20) | 0 |
| 24 | `ForwardPrice` | value | scalar | the financed forward value at H: `Price × (1 + RepoRate × τ(s, H)) − Σ_{s < c ≤ H} C × (1 + RepoRate × τ(c, H))`, τ in the repo day count, `RepoRate` flat to H (DEV-I21; the swap rows forward to expiry instead) | 0 |
| 25 | `PremiumCents` | notional_level | scalar | `Price / \|face\|` in the declared unit (pct: the dirty price per 100) (DEV-I19) | 0 |
| 26 | `LocalAnnuityInCents` | notional_level | scalar | `Annuity / \|face\|` (DEV-I19) | 0 |
| 27 | `CompoundedFixedRate` | rate | scalar | the coupon as `(1 + c/f)^f − 1` for f coupons a year; a trade term, finite on every date (DEV-I19) | finite |
| 28 | `CRIFIRCurve` | table | frame | SIMM CRIF rows from the `IRDelta` ladder; Σ `Amount` = Σ ladder; `scale_columns: [Amount]` | empty |
| 29 | `PnlExplain` | value | bucketed | Price(market_to) − Price(market) by risk factor, no time component; rows e.g. `IR` (curve) and `CREDIT` (the spread) | 0 |
| 30 | `CleanPrice` | notional_level | scalar | the quoted clean price per 100 face for standard settlement: `DirtyPrice − 100 × AccruedInterest / face` (signed face) (DEV-I20) | 0 |
| 31 | `DirtyPrice` | notional_level | scalar | `100 × Price / face` (signed face): the invoice price per 100, the same for a long and a short (DEV-I20) | 0 |
| 32 | `AccruedInterest` | value | scalar | coupon accrued from the last coupon date to standard settlement in the bond's accrual convention (UST: ACT/ACT ICMA), holder-signed (DEV-I20) | 0 (also 0 on a coupon settlement date) |
| 33 | `ModifiedDuration` | time | scalar | `−(1/P) dP/dy` in years per unit of decimal yield, y the `IRFwdRate` convention, P the dirty price (DEV-I20) | 0 |
| 34 | `Convexity` | time | scalar | `(1/P) d²P/dy²` in years² (DEV-I20) | 0 |
| 35 | `DaysToSettlement` | days | scalar | calendar days from the pricing date to standard settlement (UST T+1: 1, or 3 over a weekend or holiday) (DEV-I20) | finite |
| 36 | `RepoRate` | rate | scalar | the funding rate in force on the pricing date for this position: overnight GC or special, or a term rate locked at the trade date; simple interest in the config's repo day count (USD: ACT/360); finite on every held date (DEV-I21) | finite |
| 37 | `RepoHaircut` | rate | scalar | the fraction of the settlement value not financed, in the declared unit (decimal 0.02 = 2%) (DEV-I21) | finite |
| 38 | `FinancingToDate` | value | scalar | cumulative repo interest on the funding leg from the settlement of the trade date to the settlement of the pricing date (or maturity), holder-signed: long ≤ 0, short ≥ 0; principal `(1 − RepoHaircut) × Price(trade date)` pinned by resolve; simple interest at each calendar day's `RepoRate` (last business day's fixing over weekends and holidays); 0 on the trade date; **the engine books its change as cash (DEV-E22)** (DEV-I21) | the total to maturity |
| 39 | `Carry` | value | scalar | clean value now − clean forward value at H: `(Price − AccruedInterest) − (ForwardPrice − accrued at H)` = coupon income over (s, H] minus financing at `RepoRate` (DEV-I21) | 0 |
| 40 | `RollDown` | value | scalar | clean value at H on the library's reference curve rolled down (time to maturity unchanged, spread held) − clean value now; on a flat curve the pull to par at constant yield; `Carry + RollDown` = the financed P&L to H if the curve does not move (DEV-I21) | 0 |

Rows 6-8 and 13-15 are listed together to save space; each is its own contract row. "finite" in the Dead column means the contract requires a finite value (levels continue from the last live value, R2-7); the contract text does not fix a dead value for `DaysToSettlement`, `RepoRate`, `RepoHaircut`, `LightningOAS` and `ParSpread` beyond that.

## 2. Rates (31)

The contract rows above plus the rest of gs's rates family. The rows outside the contract (inflation, and any rates measure on a class without a contract) load and resolve by name with no unit checks.

| gs name | class | measure_type | gs unit |
|---|---|---|---|
| `Annuity` | ccy-param | AnnuityLocalCcy | - |
| `IRAnnualATMImpliedVol` | plain | Annual ATMF Implied Volatility | Percent |
| `IRAnnualImpliedVol` | plain | Annual Implied Volatility | Percent |
| `IRBasis` | FD-param | Basis | - |
| `IRBasisParallel` | preset of IRBasis (aggregation_level=Asset) | Basis | - |
| `IRDailyImpliedVol` | plain | Daily Implied Volatility | BPS |
| `IRDelta` | FD-param | Delta | - |
| `IRDeltaLocalCcy` | preset of IRDelta (currency=local) | Delta | - |
| `IRDeltaParallel` | preset of IRDelta (aggregation_level=Asset) | Delta | - |
| `IRDiscountDeltaParallel` | plain | ParallelDiscountDelta | - |
| `IRDiscountDeltaParallelLocalCcy` | plain; falls back to IRDiscountDeltaParallel | ParallelDiscountDeltaLocalCcy | - |
| `IRFwdRate` | plain | Forward Rate | Percent |
| `IRGamma` | plain | Gamma | - |
| `IRGammaParallel` | plain | ParallelGamma | - |
| `IRGammaParallelLocalCcy` | plain; falls back to IRGammaParallel | ParallelGammaLocalCcy | - |
| `IRSpotRate` | plain | Spot Rate | Percent |
| `IRVanna` | FD-param (DEV-I9) | Vanna | - |
| `IRVega` | FD-param | Vega | - |
| `IRVegaLocalCcy` | preset of IRVega (currency=local) | Vega | - |
| `IRVegaParallel` | preset of IRVega (aggregation_level=Asset) | Vega | - |
| `IRVolga` | FD-param (DEV-I9) | Volga | - |
| `IRXccyDelta` | FD-param | XccyDelta | - |
| `IRXccyDeltaParallel` | preset of IRXccyDelta (aggregation_level=Type) | XccyDelta | - |
| `InflDeltaParallelLocalCcyInBps` | plain | Inflation Delta in Bps | - |
| `InflMaturityCPI` | plain | FinalCPI | - |
| `Infl_CompPeriod` | plain | Inflation Compounding Period | - |
| `InflationDelta` | FD-param | InflationDelta | - |
| `InflationDeltaParallel` | preset of InflationDelta (aggregation_level=Type) | InflationDelta | - |
| `LocalAnnuityInCents` | plain | Local Currency Accrual in Cents | - |
| `ParSpread` | plain | Spread | - |
| `PremiumCents` | plain | Premium In Cents | - |

## 3. Cross-asset and general (22)

| gs name | class | measure_type | gs unit | Notes |
|---|---|---|---|---|
| `BaseCPI` | plain | BaseCPI | - | |
| `CRIFIRCurve` | plain | CRIF IRCurve | - | |
| `Cashflows` | plain | Cashflows | - | IR contract table (`returns: frame`) |
| `CompoundedFixedRate` | plain | Compounded Fixed Rate | - | |
| `CrossMultiplier` | plain | Cross Multiplier | - | |
| `Description` | plain | Description | - | |
| `DollarPrice` | plain | Dollar Price | - | pricebt serves it as `Price(currency="USD")` |
| `ExpiryInYears` | plain | ExpiryInYears | - | IR contract; swaps and bonds to the final date (DEV-I17) |
| `FairPremium` | ccy-param | FairPremium | - | |
| `FairVarStrike` | plain | FairVarStrike | - | |
| `FairVolStrike` | plain | FairVolStrike | - | |
| `ForwardPrice` | plain | Forward Price | BPS | |
| `LightningDV01` | plain | DV01 | - | Bond contract |
| `LightningOAS` | plain | OAS | - | Bond contract |
| `Market` | plain | Market | - | |
| `MarketData` | plain | Market Data | - | |
| `MarketDataAssets` | plain | Market Data Assets | - | |
| `Price` | ccy-param | PV | - | required in every config (the engine books cash with it) |
| `PricePips` | ccy-param | Price | Pips | |
| `ProbabilityOfExercise` | plain | Probability Of Exercise | - | IRSwaption contract |
| `ResolvedInstrumentValues` | plain | Resolved Instrument Values | - | pricebt serves it as the resolved instrument |
| `Theta` | plain | Theta | - | IR contract; per calendar day (DEV-I15) |

**Relative measures** (classes, not instances; `pricebt.risk`):

- `PnlExplain(to_market=CloseMarket(date=...))` is mapped to a `returns: buckets` portfolio function that reads `market_to`. It is optional, never in a contract (DEV-M2).
- `PnlExplainClose()` is the same with the target set to the pricing date's close.
- `PnlExplainLive()` and `PnlPredictLive()` raise `NotSupportedError`.

## 4. FX (41)

| gs name | class | measure_type | gs unit |
|---|---|---|---|
| `Cross` | plain | Cross | - |
| `FX25DeltaButterflyVolatility` | plain | FX BF 25 Vol | - |
| `FX25DeltaRiskReversalVolatility` | plain | FX RR 25 Vol | - |
| `FXAnnualATMImpliedVol` | plain | Annual ATM Implied Volatility | Percent |
| `FXAnnualImpliedVol` | plain | Annual Implied Volatility | Percent |
| `FXBlackScholes` | plain | BSPrice | - |
| `FXBlackScholesPct` | plain | BSPricePct | - |
| `FXCalcDelta` | plain | FX Calculated Delta | - |
| `FXCalcDeltaNoPremAdj` | plain | FX Calculated Delta No Premium Adjustment | - |
| `FXDelta` | FD-param | Delta | - |
| `FXDeltaHedge` | plain | FX Hedge Delta | - |
| `FXDeltaHedgeLocalCcy` | ccy-param | FX Hedge Delta Local Ccy | - |
| `FXDeltaLocalCcy` | ccy-param | FX Delta Local Ccy | - |
| `FXDiscountFactorOver` | plain | FX Discount Factor Over | - |
| `FXDiscountFactorUnder` | plain | FX Discount Factor Under | - |
| `FXFwd` | plain | Forward Rate | - |
| `FXGamma` | plain | Gamma | - |
| `FXGammaLocalCcy` | ccy-param | FX Gamma Local Ccy | - |
| `FXImpliedCorrelation` | plain | Correlation | - |
| `FXPoints` | plain | Points | - |
| `FXPremium` | plain | FX Premium | - |
| `FXPremiumPct` | plain | FX Premium Pct | - |
| `FXPremiumPctFlatFwd` | plain | FX Premium Pct Flat Fwd | - |
| `FXQuotedDelta` | plain | QuotedDelta | - |
| `FXQuotedDeltaNoPremAdj` | plain | FX Quoted Delta No Premium Adjustment | - |
| `FXQuotedVega` | plain | FX Quoted Vega | - |
| `FXQuotedVegaBps` | plain | FX Quoted Vega Bps | - |
| `FXSpot` | plain | Spot | - |
| `FXSpotVal` | plain | FXSpotVal | - |
| `FXStrikePts` | plain | StrikePts | - |
| `FXThetaLocalCcy` | ccy-param | FX Theta Local Ccy | - |
| `FXVega` | FD-param | Vega | - |
| `FXVegaLocalCcy` | ccy-param | FX Vega Local Ccy | - |
| `FairPremiumInPercent` | plain | FairPremiumPct | Percent |
| `NonUSDOisDomRate` | plain | NonUSDOisDomesticRate | - |
| `OisFXSprExSpkRate` | plain | OisFXSpreadRateExcludingSpikes | - |
| `OisFXSprRate` | plain | OisFXSpreadRate | - |
| `RFRFXRate` | plain | RFRFXRate | - |
| `RFRFXSprExSpkRate` | plain | RFRFXSpreadRateExcludingSpikes | - |
| `RFRFXSprRate` | plain | RFRFXSpreadRate | - |
| `USDOisDomRate` | plain | USDOisDomesticRate | - |

## 5. Equity (7), Credit (17), Commodities (6)

| gs name | class | measure_type | gs unit |
|---|---|---|---|
| `EqAnnualImpliedVol` | plain | Annual Implied Volatility | Percent |
| `EqDelta` | ccy-param | Delta | - |
| `EqForwardSpot` | plain | Forward Price | - |
| `EqGamma` | ccy-param | Gamma | - |
| `EqSpot` | plain | Spot | - |
| `EqTheta` | ccy-param | Theta | - |
| `EqVega` | ccy-param | Vega | - |
| `CDATMSpread` | plain | ATM Spread | - |
| `CDDelta` | plain | Delta | - |
| `CDFwdSpread` | plain | Forward Spread | - |
| `CDGamma` | plain | Gamma | - |
| `CDIForward` | plain | CDIForward | - |
| `CDIIndexDelta` | plain | CDIIndexDelta | - |
| `CDIIndexVega` | plain | CDIIndexVega | - |
| `CDIOptionPremium` | plain | CDIOptionPremium | - |
| `CDIOptionPremiumFlatFwd` | plain | CDIOptionPremiumFlatFwd | - |
| `CDIOptionPremiumFlatVol` | plain | CDIOptionPremiumFlatVol | - |
| `CDISpot` | plain | CDISpot | - |
| `CDISpreadDV01` | plain | CDISpreadDV01 | - |
| `CDIUpfrontPrice` | plain | CDIUpfrontPrice | - |
| `CDImpliedVolatility` | plain | Implied Volatility | - |
| `CDIndexVega` | plain | Vega | - |
| `CDTheta` | plain | Theta | - |
| `CDVega` | plain | Vega | - |
| `CommodDelta` | plain | Delta | - |
| `CommodImpliedVol` | plain | Volatility | - |
| `CommodTheta` | plain | Theta | - |
| `CommodVega` | plain | Vega | - |
| `FairPrice` | plain | Fair Price | - |
| `PremiumSummary` | plain | Premium | - |

Non-IR classes (`FXOption`, `FXForward`, `EqOption`, `InflationSwap`, `Cash`, `ConfigInstrument`) have **no contract**: only `Price` is required, and any measure above may be mapped by name.

**Custom names.** A `risk_measures:` key that is not in `pricebt.risk` loads and resolves by name, with no unit checks. Examples are the swap P&L recipe's `IRTheta` and `YearFraction` (`skills/pricebt-strategy-recipes/scripts/swap_pnl.py`), or your own `CarryRoll`. Request it with `pricebt.risk.RiskMeasure(name="CarryRoll")`.

## 6. pricebt-only measures (11; `PRICEBT_MEASURES`)

gs has no bond analytics, repo, financing, carry or roll-down measure (`docs/v2/BOND_DESIGN.md` section 1). pricebt adds these as catalogue data like the rest, each with a DEV id in its contract text (`docs/v2/DEVIATIONS.md`). `pricebt.risk.PRICEBT_MEASURES` lists the names; `tests/test_gs_api_parity.py` allows exactly these extras, and none shadows a gs name. All 11 are `Bond` contract rows (section 1.1); on a class without a contract they are ordinary names a config may map.

| pricebt name | class | measure_type | DEV | Notes |
|---|---|---|---|---|
| `AccruedInterest` | ccy-param | Accrued Interest | DEV-I20 | value (ccy) |
| `CleanPrice` | plain | Clean Price | DEV-I20 | per 100 face |
| `DirtyPrice` | plain | Dirty Price | DEV-I20 | per 100 face |
| `ModifiedDuration` | plain | Modified Duration | DEV-I20 | years |
| `Convexity` | plain | Convexity | DEV-I20 | years² |
| `DaysToSettlement` | plain | Days To Settlement | DEV-I20 | calendar days, `number` |
| `RepoRate` | plain | Repo Rate | DEV-I21 | simple, repo day count |
| `RepoHaircut` | plain | Repo Haircut | DEV-I21 | fraction not financed |
| `FinancingToDate` | ccy-param | Financing To Date | DEV-I21 | booked as cash by the engine (DEV-E22) |
| `Carry` | ccy-param | Carry | DEV-I21 | to H = settlement + 1 calendar month |
| `RollDown` | ccy-param | Roll Down | DEV-I21 | to H |
