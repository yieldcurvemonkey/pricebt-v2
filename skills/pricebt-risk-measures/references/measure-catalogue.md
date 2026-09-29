# The measure catalogue (`pricebt.risk`)

`pricebt.risk` holds the whole gs_quant 2.1.17 measure catalogue as **data**. That is 124 measure instances and presets plus the `PnlExplain` family, with gs's Python names, `measure_type` strings, asset classes, gs units and parameter classes. pricebt computes none of them: an asset config maps each measure you use to a function. `tests/skills/test_skill_risk_measures.py` checks that every exported measure is listed here.

How to read the tables:

- **Class:**
  - *plain* is a `RiskMeasure`, requested by name (`Theta`).
  - *ccy-param* is `RiskMeasureWithCurrencyParameter`, callable as `Price(currency="EUR")`.
  - *FD-param* is `RiskMeasureWithFiniteDifferenceParameter`, callable as `IRDelta(aggregation_level="Type", bump_size=..., ...)`. Bare, it asks for the bucketed form; with `aggregation_level` `Type`, `Asset` or `Class` it asks for the scalar form.
  - *preset* is a parameterised instance with its own name (gs `risk/measures.py`). It falls back to its base measure's mapping.
- **gs unit:** what gs declares (`Percent`, `BPS`, ...). pricebt instead reports the unit your config declares on the function (DEV-I7), e.g. `IRFwdRate` in bp.

## 1. The IR contract (`IRSwap`, `IRSwaption`, `Bond`)

An asset whose `instrument:` is one of these three classes must, for every row and form below, **map** it to a function with an allowed unit, or **declare** it under `unsupported_measures:` with a reason (DEV-I11). Every other measure name is unrestricted. The full one-line semantics are in `src/pricebt/risk/contracts.py`; print them with `python skills/pricebt-risk-measures/scripts/measures.py contract IRSwaption`.

| Measure | Kind | Forms | Allowed units | Classes | Semantics (short) |
|---|---|---|---|---|---|
| `Price` | value | scalar | ccy | all 3 | holder-signed PV of one unit trade; drops paid flows or is total return, consistently with `Cashflows` |
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
| `ProbabilityOfExercise` | prob | scalar | decimal, number | IRSwaption | probability of finishing in the money (annuity measure) |
| `LightningDV01` | sens1 | scalar | ccy_per_bp | Bond | yield DV01 (= the bond's IRDelta scalar) |
| `LightningOAS` | rate | scalar | bp, pct, decimal | Bond | OAS; a bullet bond uses its Z-spread |
| `ParSpread` | rate | scalar | bp, pct, decimal | Bond | par asset-swap spread |

**Kinds and units.**

- value, theta and annuity are `ccy`; sens1 is `ccy_per_bp`; sens2 is `ccy_per_bp2`. These are extensive: pricebt multiplies them by `quantity_`.
- rate, vol, time and prob are intensive: `scale_with_quantity` must be false, which is the default for `bp`, `pct` and `decimal`.
- table is a `functions:` entry with `returns: frame`, whose `scale_columns` include `payment_amount`.

**Presets that count toward a contract row** (R2-10): `IRDeltaParallel` and `IRDeltaLocalCcy` → `IRDelta`; `IRVegaParallel` and `IRVegaLocalCcy` → `IRVega`; `IRBasisParallel` → `IRBasis`; `IRXccyDeltaParallel` → `IRXccyDelta`; `IRGammaParallelLocalCcy` → `IRGammaParallel`; `IRDiscountDeltaParallelLocalCcy` → `IRDiscountDeltaParallel`. Declare the base measure, never the preset.

## 2. Rates (31)

The contract rows above plus the rest of gs's rates family. The rows outside the contract (inflation, cents measures, `ParSpread` on non-bonds) load and resolve by name with no unit checks.

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

**Custom names.** A `risk_measures:` key that is not in `pricebt.risk` loads and resolves by name, with no unit checks. Examples are the in-flight branch's `IRTheta` and `YearFraction`, or your own `CarryRoll`. Request it with `pricebt.risk.RiskMeasure(name="CarryRoll")`.
