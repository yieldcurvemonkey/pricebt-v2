# Mapping the methodology to rates backtests in pricebt

The reference books are mostly about equities and FX. This page translates their ideas into swap-book terms. Items marked *(inference)* are this library's own translation, not claims made by the books.

| Concept | Equity framing in the books | Rates / pricebt framing |
|---|---|---|
| Position size | dollars, shares | **dv01** (currency per bp): `IRDelta(aggregation_level='Type')`. Size with `AddScaledTradeAction(scaling_type=ScalingActionType.risk_measure, scaling_risk=IRDelta(aggregation_level='Type'), scaling_level=±target)` |
| Volatility of an instrument | price-return volatility | rate volatility in bp × dv01 *(inference)* |
| Industry / market neutral | subtract the group mean | **dv01-neutral** (level-neutral) or PCA-neutral (level, slope, curvature) depending on the hypothesis; hedge with `HedgeAction(IRDelta(aggregation_level='Type'), hedge)` *(inference; FA ch. 28 p. 190)* |
| Breadth | number of stocks | the number of **independent** factors or currencies, not tenors. Ten tenors on one curve are about three factors (GK ch. 6 p. 158; FA ch. 18 p. 117) |
| Consensus / naive forecast | market expectations | **carry and roll-down**. Alpha is the forecast beyond the carry you would earn anyway, so compare every curve trade against its always-on carry version *(inference; GK ch. 10 p. 263)* |
| Costs | half-spread + commission | half the bid/offer in bp × dv01, per side. Wider in long and off-the-run tenors and in stress. `ScaledTransactionModel(IRDelta(aggregation_level='Type'), level_bp)` charges level × \|dv01\| per trade |
| Corporate actions | splits, dividends | reset, roll and fixing discontinuities; convention changes (e.g. benchmark transitions) |
| Survivorship | delisted stocks | a point-in-time instrument set: tenors, indices and conventions as they existed on each date |
| Regimes | risk-on / risk-off | hiking / cutting / on hold; QE / QT; zero-rate floors. Report by regime as well as by year |
| Data timing | publication lags | curve snapshot time (EOD stamp), fixing publication lag, library version. All legs of a spread must use the same snapshot (AQM ch. 2) |
| Mean-reversion targets | price spreads, pairs | curve spreads (2s10s), flies (2s5s10s), swap spreads, cross-market spreads. The weights are **dv01** weights, and the traded notionals must carry the same dv01 weights as the signal (Chan ch. 7 p. 133; a Chan ch. 3 example gets this wrong) |
| Term premium / carry | not covered | forward minus the expected short rate. Test whether a constant-premium assumption holds, e.g. by rolling forward-rate regressions (AQM ch. 3 pp. 83–84, 108–113) |

## pricebt-specific cautions that interact with the methodology

- **Coupons paid between marks are not booked as cash** (gs parity). Carry P&L on long holds is therefore incomplete. Say so whenever carry is part of the hypothesis.
- **Signal and execution happen on the same close.** The cleanest look-ahead check is to re-run with the signal shifted by one business day (see [`pricebt-spot-checks`](../../pricebt-spot-checks/SKILL.md)).
- **Mean-reversion exits in gs are offsetting trades held forever.** Gross notional grows with every signal, so report net dv01 and gross notional.
- **Unfunded P&L.** `result_summary['Total']` is cumulative currency P&L. Choose a capital base explicitly before quoting percentages.
