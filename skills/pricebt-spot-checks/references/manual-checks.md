# Manual spot checks

The automated pass ([`spot_check.py`](../scripts/spot_check.py)) proves that pricebt's bookkeeping agrees with your asset config. These five checks cover what it cannot see: pricing bugs inside the config, strategy logic that does not do what the idea says, and look-ahead. Do all five for any result that goes into a report. Write one line per check: what you did, what you expected, what you saw.

Throughout, `backtest` is the finished `BackTest`, `run(**overrides)` is your function that rebuilds the strategy with some parameters changed and returns a new `BackTest`, and the working directory is the repository root.

## 1. Reprice two trades with the library directly

**Why:** every automated check prices through the asset config. If the config's `npv` expression is wrong (wrong curve, wrong day count, a payer/receiver flip), the engine and the checks agree with each other and are both wrong.

1. Pick two trades from `backtest.trade_ledger()`: the first closed trade and one near the end. For each, note `Open`, `Close`, `Open Value`, `Close Value`.
2. Get the resolved terms pricebt used. The instrument is in `backtest.portfolio_dict[open_date]`, found by name:

   ```python
   inst = next(i for i in backtest.portfolio_dict[open_date] if i.name == trade_name)
   print(inst.resolved_terms, inst.quantity_)
   ```

3. Build the same trade **in your pricing library, by hand**, from those terms (fixed rate, effective and termination dates, notional times `quantity_`, pay/receive). Do not call pricebt or the asset config's helper functions.
4. Price it on the open date and on the close date with your library's own market for those dates.
5. Expect `Open Value ≈ -PV(open)` and `Close Value ≈ +PV(close)`, to the tolerance of your library (usually a few currency units on a 10mm swap). An at-the-money entry has PV ≈ 0, so the close is the informative comparison.

**If it disagrees:** the asset config is wrong, or the resolved terms are not what you think (for example `'10y'` resolved from a different effective date). Fix the config, never `src/pricebt`.

## 2. First and last trade dates against the signal

**Why:** off-by-one windows and warm-up bugs show up at the edges.

1. Compute the signal the trigger reads (for `MeanReversionTrigger`, the rolling z-score of the data source; for momentum, the change over the lookback) on the same dates.
2. The **first** trade must not be earlier than the first date on which the signal is defined. A 30-observation window cannot fire on day 5. The mean-reversion window excludes today and uses the sample standard deviation (`ddof=1`); a zero standard deviation gives an infinite z-score, which fires the trigger (gs parity).
3. For the first and the last trade, write down the signal value on the trade date and confirm it crosses the threshold on that date and not the day before.
4. Confirm that no trade is dated after the backtest end, and that trades opened near the end have the expected `Status` (a trade whose exit falls after the end is `open`).

## 3. Perturb one parameter and check the direction

**Why:** a strategy whose P&L does not respond sensibly to its own parameters has a wiring bug.

Run at least two of these:

| Change | Expected |
|---|---|
| notional × 2 | every P&L, cost and risk number × 2 exactly (costs scale if they are notional- or dv01-based; constant costs do not) |
| entry threshold up (e.g. z 2.0 → 2.5) | fewer trades, never more |
| lookback longer | a smoother signal, usually fewer trades; the first trade later |
| pay ↔ receive | P&L before costs flips sign exactly; costs unchanged |
| rebalance frequency halved | about half the trades and half the cost |

Anything that moves the wrong way, or does not move at all, is a bug in the strategy code until proven otherwise.

## 4. Run with costs doubled

**Why:** it shows how much of the edge survives realistic frictions, and it checks that the cost model is wired in.

1. Rerun with the cost level doubled (for `ScaledTransactionModel(IRDeltaParallel, 0.25)`, use `0.5`).
2. For a linear cost model, expect `Total PnL(2x) = Total PnL(1x) + Total Transaction Costs(1x)`, i.e. net P&L falls by exactly the original cost total, and the trades are identical. A path-dependent strategy (one whose triggers read the book's P&L or risk) may legitimately change its trades. Say so if it does.
3. Report the break-even cost level (the level at which Total PnL reaches zero) if the strategy is marginal.

## 5. Shift the signal by one day (look-ahead test)

**Why:** pricebt executes on the same close the signal is computed on. If the signal uses information that is only available after the close (a same-day fixing published later, a revised series, an end-of-day rate that you could not trade on), the backtest has look-ahead.

1. Lag the signal series by one observation before feeding it to the data source:

   ```python
   lagged = signal.shift(1).dropna()
   ```

2. Rerun the strategy on the lagged signal.
3. Interpret:
   - The strategy claims **timing skill** (mean reversion over days, event trades): P&L should degrade but remain the same sign. A collapse from strong to nothing means the edge depends on trading at the price that produced the signal. Treat it as look-ahead until you can show the fill is achievable.
   - P&L **improves** when lagged: suspicious. Check that the signal is not already lagged somewhere, or reversed.
   - Slow strategies (monthly rolls, carry): little change is expected.
4. Put both numbers in the report next to each other.

## Recording the results

Add a table like this under the spot-check section of the tearsheet (`build_tearsheet(..., notes=...)`, or edit the Markdown):

| Manual check | Expected | Observed | OK? |
|---|---|---|---|
| reprice 2 trades by hand | close values within 5 USD | 3.1 and 0.8 USD off | yes |
| first/last trade vs signal | first trade after day 30 | day 31, z = 2.07 | yes |
| notional × 2 | P&L × 2 | × 2.000 | yes |
| costs × 2 | net − 10,569 | net − 10,569 | yes |
| signal lagged 1 day | degrades, same sign | Sharpe 0.9 → 0.6 | yes |
