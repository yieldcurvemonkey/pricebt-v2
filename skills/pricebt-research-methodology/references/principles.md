# Research principles, with sources

These are paraphrases of the four reference books, reorganised by the stage of the workflow they govern. Citation keys and page conventions:

- **GK**: Grinold & Kahn, *Active Portfolio Management* (1999). **Printed** page numbers.
- **Chan**: Chan, *Quantitative Trading* (2009). **Printed** page numbers.
- **FA**: Tulchinsky et al., *Finding Alphas* (2015). **PDF** page numbers; printed = PDF − 18.
- **AQM**: Dunis, Laws & Naïm, *Applied Quantitative Methods for Trading and Investment* (2003). **Printed** page numbers.

## A. Before any backtest (intake)

- **A strategy is a forecast plus a way to trade it.** Keep the forecast (signal), the risk model and the implementation separate, so one cannot flatter another. (GK ch. 1 p. 4; ch. 10 p. 261)
- **State the mechanism.** Every signal needs a written reason why it should work and who loses to it. Ask five questions: is it different, who doesn't know it, does it make sense, can it scale, will it last? (FA ch. 1 pp. 22–23; ch. 12 pp. 87–88)
- **Say in advance where the effect should appear.** Name the instruments and horizon where it should be strong, a control where it should be weaker, and one where it should be absent. The absent control catches coding errors. (FA ch. 25 pp. 170–171)
- **Choose the benchmark deliberately.** A cash benchmark recovers the total-return view, which suits an absolute-return swap book. Judge active or residual P&L against it. (GK ch. 1 p. 5; ch. 4 p. 88)
- **Match the rebalance period to how fast information arrives.** A view refreshed yearly and rebalanced monthly is one bet repeated. (GK ch. 6 p. 158; ch. 12 p. 319)
- **Measure the signal's horizon.** IC decays with lag, and the half-life is a stable property of a strategy. For mean reversion, set the holding period to about the Ornstein–Uhlenbeck half-life. (GK ch. 13 pp. 347–361; Chan ch. 7 pp. 140–142)
- **Fix the exit before the entry.** Exits come in four types: a fixed holding period, a target (e.g. the mean), the latest opposite signal, or a stop. Stops suit momentum. In a mean-reversion model a stop exits at the worst point. (Chan ch. 7 pp. 140–143; FA ch. 3 pp. 34–35)
- **Write the success criteria down first:** Sharpe floor, drawdown limit, the largest acceptable drop from in-sample to out-of-sample, and a correlation cap to existing books. (FA ch. 30 pp. 212–213; GK ch. 12 pp. 337–338)

## B. Designing the test

- **Out-of-sample must come later in time.** Using earlier data, or the other half of the instruments, leaks information. Do not reuse the OOS window to choose between models. (FA ch. 10 p. 76; AQM ch. 1 pp. 25–26)
- **Suggested split:** about 2/3 train, 1/6 tuning, 1/6 validation, or at minimum a test window at least a third the size of the training window. (AQM ch. 1 pp. 25–26; Chan ch. 3 pp. 53–54)
- **The training window must cover the regimes the test will meet.** A trending training window biases the model towards that trend. (AQM ch. 1 p. 37)
- **Few parameters.** Count qualitative choices too. About 5 at most, and about 252 observations per parameter on daily data. (Chan ch. 3 p. 53; FA ch. 10 pp. 76–77)
- **Prefer walk-forward re-estimation to fixed fitted parameters.** Anything estimated (hedge ratios, z-score means) must use only past data at each date. (Chan ch. 3 pp. 51–55; GK ch. 18 p. 521)
- **Use synchronous data.** Mixing snapshot times across the legs of a spread creates fake mean reversion and fake lead-lag. (AQM ch. 2 pp. 54, 57, 63; ch. 1 p. 26)
- **Take information timing seriously.** Decide on data available at the decision time. pricebt trades at the same close its triggers observe, so a signal built from that close assumes you can trade at the price you saw. (FA ch. 28 p. 188; Chan ch. 3 p. 51)

## C. Judging results

- **The figure of merit is IR (Sharpe of active P&L), with its significance.** t ≈ IR × √years. Proving IR 0.5 at t = 2 takes 16 years. (GK ch. 5 p. 112; ch. 17 p. 480)
- **Implausible numbers mean bugs:** IC above 0.20, IR above 2 on public data, or a hit rate well above 55%. (GK ch. 6 p. 154; ch. 12 pp. 272, 338)
- **Account for multiple testing.** 20 null tests give at least one t > 2 about 64% of the time. About 7 variants are enough to expect one 2-year backtest with Sharpe above 1 from pure noise. (GK ch. 12 p. 337; FA ch. 10 p. 75)
- **Judge models on trading P&L, not forecast error.** Forecast accuracy statistics disagree with one another and with P&L. Directional accuracy is the forecast statistic closest to P&L. (AQM ch. 1 pp. 31–32; ch. 4 pp. 145–149)
- **Always compare against naive benchmarks:** a random-walk forecast, a simple moving-average rule, and the always-on or buy-and-hold version. (AQM ch. 1 p. 32)
- **Weight recent data.** Older periods flatter strategies (wider real spreads, less competition), and regimes change. (Chan ch. 2 pp. 24–25; FA ch. 17 p. 113)
- **Report by sub-period and by regime.** Show per-year tables and up- vs down-market splits, and flag P&L that comes from one episode. (GK ch. 12 pp. 328–329; FA ch. 6 p. 52; AQM ch. 3 pp. 113, 126)
- **Glitchy marks look like alpha.** A bad price that is corrected the next day looks like a profitable reversal. Scan for one-period P&L spikes that reverse. (GK ch. 12 p. 321; Chan ch. 7 p. 117)

## D. Costs, turnover, sizing

- **Costs are first-order.** A top-quartile manager can lose half the return to costs. A high-turnover strategy with a thin edge flips sign under realistic costs. (GK ch. 16 p. 445; Chan ch. 2 p. 23; ch. 3 pp. 61–65)
- **Cost = one-way cost × Σ|Δposition|, and a flip counts twice.** (Chan ch. 3 p. 65; AQM ch. 1 p. 36)
- **Prefer slower rules for the same gross edge.** Expected turnover can be computed in advance. (AQM ch. 11 pp. 338–340)
- **Half-Kelly (f = m/s²) at most**, capped by the tolerable one-period loss divided by the worst historical one-period loss. (Chan ch. 6 pp. 96–106)
- **The optimal active risk is IR / (2λ).** Bond managers historically run little active risk (median about 0.6–1.3%). (GK ch. 5 pp. 122–132)
- **Use a no-trade band.** Trade back to the edge of the band, not to the target. (GK ch. 14 pp. 390–391)

## E. Validation and replication

- **Replicate before trusting**, and have a colleague or a second implementation reproduce the results. (Chan ch. 3 p. 31; ch. 6 p. 107)
- **Truncation test for look-ahead:** drop the last N days, re-run, and require identical positions on the overlap. (Chan ch. 3 pp. 51–52, 59)
- **Shift test:** lag the signal one extra period. Gradual decay is normal; a collapse means it was leaking. (GK ch. 13; FA ch. 14 p. 99)
- **Sensitivity sweep:** perturb each parameter; require a plateau, not a spike. (Chan ch. 3 p. 60; FA ch. 32 p. 239)
- **Random-data and known-answer tests:** a method must find nothing in noise and recover a planted effect. (GK ch. 10 p. 278; FA ch. 10 p. 77)
- **Leave-one-out:** drop an instrument or input in turn; the result should persist. (FA ch. 17 p. 113; ch. 25 p. 170)
