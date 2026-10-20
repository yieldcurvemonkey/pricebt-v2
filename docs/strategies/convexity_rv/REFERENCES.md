# References: forward curve vs straddle convexity RV

This is a reading list for `BACKTEST_SPEC.md`. The papers are licensed sell-side research, so they are **not stored in this repo**. Get them from the publisher's research portal (J.P. Morgan Markets, Citi Velocity).

Each paper is marked as **Core** (named in the original research brief) or **Supporting**.

## Framework

| Kind | Publisher | Date | Title | Relevance |
|---|---|---|---|---|
| Core | Not stated in the file | — | *A holistic approach to convexity across fixed income* | The framework the screener adopts: expected return = carry + roll + ½Γσ², ranked by Sharpe ratio and plotted on an efficient frontier across convexity sources |

## Forward curves as a source of convexity

| Kind | Publisher | Date | Title | Relevance |
|---|---|---|---|---|
| Core | J.P. Morgan | 2017-02-03 | *An option by any other name: Sourcing cheap convexity in the long end of the curve* | The core idea: a PVBP-weighted long-end curve trade has an option-like payoff from convexity differences between tenors. Sets out an RV framework against swaptions and finds the curve is often the cheaper source of long gamma, especially in risk-off episodes |
| Core | J.P. Morgan | 2018-02-09 | *Valuing convexity in the long end of the yield curve: A global perspective* | Extends the valuation of long-end curve convexity across markets |
| Supporting | J.P. Morgan | 2019-08-23 | *Interest Rate Derivatives: For cheap gamma, look to the long end* | Buys long-end convexity through curve trades (15Yx5Y vs 35Yx5Y flatteners); flow drivers such as insurer and mortgage-servicer receiving |

## Curve vs vol relative value and trade examples

| Kind | Publisher | Date | Title | Relevance |
|---|---|---|---|---|
| Core | Citi | 2019-05-09 | *US Rates Vol Lab: Trading long-dated convexity* | Delta-hedged forward flatteners as cheap convexity. Source of the cost anchor in spec §9 (Fig 9: package and hedge costs) |
| Supporting | Citi | 2019-12-05 | *Trade Idea Alert: Taking profits on delta-hedged 15y5y/20y10y flatteners* | Realised result of the May-2019 trade: $50k DV01, delta-hedged every 20bp |
| Core | Citi | 2020-01-17 | *Rates Vol Lab: In search of cheap vol* | Straddle switches and vol supply (Formosa issuance); entry note for the GBP flattener below |
| Supporting | Citi | 2020-03-26 | *Trade Idea Alert: Taking profits on GBP 15y10y/25y10y flatteners* | Delta-hedged every 25bp, motivated by curve steepness and positive convexity |
| Core | Citi | 2023-06-12 | *Rates Vol Lab: Forward steepener and vol divergence* | The opposite side to the flattener trades: long-dated forwards look flat relative to vol, so it recommends forward steepeners as a short-vol proxy (spec side −1 logic) |
| Core | Citi | 2026-10-05 | *Rates Vol Lab: Fading vega dislocations* | The full package: short 3y10y vol against a 10y10y/15y15y forward flattener, because intermediate vol looks high relative to long-dated forward curves |
