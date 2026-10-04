# Intake question bank

Questions are in priority order. **Must-ask** questions change what gets built. **Should-ask** questions change parameters. **Nice-to-ask** questions change the report. Each gives the spec field it fills, why it matters, and the default an autonomous agent uses. Sources are the methodology references (keys as in [`../../pricebt-research-methodology/references/principles.md`](../../pricebt-research-methodology/references/principles.md)).

## Must-ask (one message, grouped, with defaults shown)

1. **What is the edge, and when should it fail?** → `hypothesis`
   - *Why:* a mechanism-backed idea survives out of sample; stating the failure regime pre-registers the review. (FA ch. 1, ch. 12; GK ch. 12)
   - *Default:* write the most plausible mechanism, and mark it `assumed` in `assumptions`.
2. **What exactly is traded?** Currency, tenor(s), instrument, direction convention. → `instruments`, `assets`
   - *Why:* this decides which asset configs must exist. For a swaption or a bond, also ask the instrument-specific questions below (they are must-ask for those classes).
   - *Default:* one instrument at the tenor named in the idea, ATM, spot-starting, size 10mm (`notional_amount`; a Bond's `size`).
3. **What is the signal, and how is it measured?** Which rate, level or change, and the lookback window. → `signal.*`
   - *Why:* each window is a free parameter. Declare a coarse grid rather than tuning. (Chan ch. 3 p. 53; FA ch. 10)
   - *Default:* `IRFwdRate` of the primary instrument (its own quoted rate: a swap's par rate, a swaption's forward, a bond's yield), resolved through the asset config's `risk_measures:`; lookback 30 business days for z-scores, 60 for momentum. Vol ideas: `IRAnnualImpliedVol`. Bond spread ideas: `ParSpread` or `LightningOAS`. A config function name (`par_rate`) also works but only on configs that define it.
4. **Entry, exit and holding period.** → `signal.params`, `rebalance`, `entry_exit` (in `archetype`)
   - *Why:* there are only four exit types (fixed period, target, opposite signal, stop). Stops suit momentum, not mean reversion. (Chan ch. 7 pp. 140–143)
   - *Default:* mean reversion enters at |z| > 2, with gs's offsetting-trade exit at the mean; momentum and carry rebalance monthly with `trade_duration: next schedule`.
5. **How big?** Notional, dv01 per trade or leg, or a cash (NAV) budget. → `sizing.*`
   - *Why:* size in risk units, especially across tenors. (GK ch. 5; Chan ch. 6)
   - *Default:* single-instrument ideas use notional 10mm; curve trades use a dv01 target of 10,000 per bp per leg.
6. **Limits.** Maximum dv01, a stop, maximum positions, a drawdown tolerance. → `risk_limits.*`
   - *Why:* limits change behaviour and must be fixed before seeing results. (FA ch. 3 pp. 34–35)
   - *Default:* none enforced; a max drawdown of 3× the expected annual P&L standard deviation is report-only.
7. **Period and out-of-sample split.** → `dates.*`
   - *Why:* the OOS period must come later in time, and the test window should be at least a third the length of the training window. (FA ch. 10 p. 76; Chan ch. 3 pp. 53–54)
   - *Default:* the full available history of the asset; `in_sample_end` at 70% of the period.
8. **Costs.** → `costs.*`
   - *Why:* costs decide tradability. Run both with and without them. (Chan ch. 3 pp. 60–65)
   - *Default:* `dv01_bp` 0.25 (a quarter of a bp of dv01 per side) for liquid swaps; 0.5 for long or off-the-run tenors. Bonds: `dv01_bp` 0.25 for on-the-run, 0.5 off-the-run (or `notional_bp` if the config maps a `notional_amount` attribute). Swaptions: `notional_bp` or `constant` (a dv01 cost is ~0 for a straddle); a cost in bp of vega needs a hand-built `ScaledTransactionModel(IRVegaParallel, level)`.

## Instrument-specific questions (swaptions and bonds)

Ask these with question 2 whenever the idea trades a swaption or a bond. `spec.py validate` rejects a spec that leaves the starred kwargs unstated, because the asset config's default would decide them invisibly.

The repo terms (GC or special, overnight or term, haircut) are **not** gs `Bond` fields, so a spec cannot state them as `kwargs` (`spec.py validate` rejects a name that is not a field of the class). The bond asset config decides them, often as its own defaults (the toy `tests/assets/toy_usd_bond.yaml` defaults `repo_term: overnight` and `repo_haircut: 0.02`). Choose the config that finances the way the idea needs, and write the repo terms under `assumptions` (`spec.py` cannot check them, so the review does).

| Question | Spec field (gs kwarg) | Why it matters | Default |
|---|---|---|---|
| Buy or sell the option? * | `buy_sell: Buy / Sell` | the **position**. It is what `flipped` and dv01 sizing act on | Buy, unless the idea says sell or short vol |
| Payer, receiver or straddle? * | `pay_or_receive: Pay / Receive / Straddle` | the **option type**, never the position: "short payers" is Pay + Sell. A Straddle has no delta sign, so it cannot be dv01-sized | from the idea: rates up → payer, rates down → receiver, vol → Straddle |
| Expiry? | `expiration_date` (tenor `'1m'`, `'3m'`, or a date) | sets the roll (`trade_duration: expiration_date` exits on it) and the theta/gamma profile | the idea's horizon, else 3m |
| Tail (underlying swap tenor)? | `termination_date` (tenor measured from expiry, e.g. `'10y'`) | which forward rate the option is on | 10y |
| Strike? | `strike`: `'ATM'`, `'ATM+25'` / `'A-50'` (bp from the forward at entry), or an absolute decimal (`0.04`) | resolved and pinned on the entry date; relative strikes re-strike only on new trades | `'ATM'` |
| Settlement? | `settlement` (e.g. `'Physical'`, `'Cash.PYU'`) | what Price is on and after expiry: physical = the underlying swap's PV if exercised, cash = intrinsic. The engine closes the option at that value on its expiry date | the library's market default; record it under `assumptions` |
| Premium and fee? | `premium`, `fee` | must be 0 (unset) in a backtest: the entry cash −Price already is the premium; a premium paid after entry would leave Price without reaching cash | 0 (enforced) |
| Which bond? * | `identifier` (+ `identifier_type` if not the library's default, e.g. ISIN or CUSIP) | the static data (coupon, maturity, schedule) come from your library's bond master; fixed for the whole backtest | the on-the-run issue at the idea's tenor on the start date |
| Long or short the bond? * | `buy_sell: Buy / Sell` | a long bond has **negative** `IRDelta` (it loses when yields rise). dv01 sizing signs its level negative | Buy |
| How much? | `size` (face), not `notional_amount` | `sizing.notional` overrides `size` for a Bond | 10mm face |
| Settlement date? | `settlement_date` | usually left to the library (standard settlement: T+1 for US Treasuries). `Price` is the settlement-date value and repo accrues between settlement dates; only set it for a forward-settling trade | unset |
| Roll into new issues? | an event or `dated_priceables` recipe (see the recipes catalogue) | the spec's `identifier` is fixed; rolling on-the-run needs identifiers known on each date (survivorship) | no roll: one issue |
| Coupons? | nothing to set | every Bond config maps `FinancingToDate`, so the engine books the coupons the bond drops as cash on the drop date (holding cash, DEV-E22). Coupons are paid out as cash, not reinvested in the bond; they sit in the cash balance (accruing only if a `cash_accrual` model runs, which a financed book should not use) | booked as cash, not reinvested |
| Repo: general collateral or special? (must-ask) | the bond asset config (`assets:`), recorded under `assumptions` | the config's `RepoRate` decides it. An on-the-run issue often trades special (a lower repo rate), which is real carry; GC on a special issue understates it, special on an off-the-run issue overstates it | the config's choice, stated |
| Repo: overnight or term? | the bond asset config, under `assumptions` | overnight re-fixes every day (repo risk inside the P&L); term locks the rate at the trade date for the holding period. A carry trade sized on a term rate but financed overnight is a different trade | the config's choice, stated |
| Haircut? | the bond asset config, under `assumptions` | `RepoHaircut`: the share of the settlement value not financed, so it is not charged repo. Who funds it, and at what rate, is not in the backtest unless the config puts it in `FinancingToDate` | the config's haircut (e.g. 2%), stated |
| Funding currency? | `result_ccy`, `fx` | repo is in the bond's currency. A bond funded in another currency is an FX-hedged position the backtest does not model; a non-local report converts each booking at its date's FX | the bond's currency |
| Cash accrual on top? | `financing.cash_accrual_rate: 0.0` | the bond config's `FinancingToDate` already charges the repo, and the engine books it. A cash-accrual rate charges the funding loan a second time; the engine warns ("the funding is counted twice") | 0 for any book holding bonds |
| Hedge with what, on which measure? | `instruments.hedge`, `risk_limits.hedge_measure` | e.g. delta-hedge a straddle with a swap on `IRDeltaParallel`; vega-hedge with another swaption on `IRVegaParallel`. Deltas across types are approximate (DEV-I12) | `IRDelta(aggregation_level='Type')` |

## Should-ask (only if the answer changes the build)

9. **Rebalance cadence and pricing grid.** → `rebalance.frequency`, `dates.frequency`. *Default:* grid `1b`; rebalance at the idea's natural cadence (monthly for carry, daily for hedging).
10. **Hedging.** Keep dv01-neutral? With what? → `archetype: delta_hedged`, `instruments.hedge`. *Default:* no hedge unless the idea is relative value.
11. **Financing and cash.** Accrue cash? At what rate? Starting value? → `financing.cash_accrual_rate`, `initial_value`. *Default:* 0 and 0. `Total` is then cumulative P&L; a swap needs no cash. A bond is financed by its own config (repo, booked by the engine), so keep the rate at 0 for any book holding bonds.
12. **Currency of the report.** Is it a mixed-currency book? → `result_ccy`, `fx`. *Default:* the asset's local currency. A mixed book needs an FX config.
13. **Event calendar.** Which dates (central-bank meetings, auctions, month-ends)? → `event_dates`. *Default:* none; if the idea depends on events, this becomes must-ask.
14. **Benchmark.** → `benchmark`. *Default:* `none` (cash), since swap P&L is already excess of funding. A bond is too: its config's repo financing is booked. An option bought for cash is not: set `financing.cash_accrual_rate` or say so. For carry ideas, compare against the always-on version.
15. **Parameter budget.** How many variants are you willing to test? → `trial_budget` (write it under `assumptions`). *Default:* 5. Every run is logged, and the significance test is deflated by the count.

## Nice-to-ask (report only)

16. **Success criteria.** → `success_criteria.*`. *Default:* Sharpe ≥ 0.5, OOS Sharpe ≥ 0.3, at least 10 trades.
17. **What would make you stop trading it?** A kill rule, reported as a threshold. *Default:* the worst in-sample drawdown × 1.5.
18. **Correlation to existing books.** Report it if the user can supply their P&L series. *Default:* skip.
19. **Delivery.** Format and audience. → `deliverables`. *Default:* HTML + markdown tearsheet in `reports/<name>/`.

## Message template (interactive mode)

```
To build this backtest I need 8 quick answers - reply "defaults" to accept all of them.
1. Edge / failure regime: <my reading> (default)
2. Instrument: <ccy tenor instrument, direction> (default)
   (swaption: <Buy/Sell> <payer/receiver/straddle> <expiry>x<tail>, strike <ATM>; bond: <Buy/Sell> <identifier>, <size> face)
3. Signal: <measure, lookback> (default)
4. Entry / exit / holding: <rule> (default)
5. Size: <notional or dv01 target> (default)
6. Limits: <none / max dv01 / stop> (default)
7. Period and out-of-sample split: <start..end, IS to date> (default)
8. Costs: <0.25bp of dv01 per side> (default)
```
