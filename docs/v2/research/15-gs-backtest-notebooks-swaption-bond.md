# 15 — gs backtesting notebooks with swaptions, bonds and IR greeks; exact `pnl_explain` semantics; proposed PnlDefinitions

## Key facts

1. **Six gs notebooks run a backtest with swaptions.** Four are under `documentation/04_backtesting`: 040303, 040305, 040307 and `tutorials/Backtesting`. R05 deemed these four swaption-dependent (R05§4). The other two are under `content/made_with_gs_quant`: `3-Systematic Selling` and `4-Delta Hedging`, which R05 did not scan. **No notebook backtests a `Bond`**, and no notebook puts `IRVega`, `IRGamma`, `Theta`, `IRAnnualImpliedVol` or `ExpiryInYears` into `run_backtest(risks=...)`. The only IR measures beyond Price/IRDelta in a backtest notebook are `IRFwdRate`, `IRDailyImpliedVol` and `DollarPrice`. 040307 computes them *after* the run, with `HistoricalPricingContext`, and 3-Systematic Selling reads `DollarPrice`.
2. **Nothing in gs uses backtest `pnl_explain`, `PnlDefinition` or `PnlAttribute`.** No notebook and no gs test does, and there is no test of `calc_risk_at_trade_exits` / `trade_exit_risk_results` either. The only other references are the xasset request decoder (`api/gs/backtests_xasset/request.py:95-119, 178`) and a docstring that names a `GenericEngineBasicBacktestRunner` absent from the repo (`bo.py:996-1001`). The notebooks named "pnl explain" (030007, `0001_portfolios_and_var`, `10-Explaining Performance Drivers`) use the **server-side relative measure `PnlExplain(to_market)`**. That is a different object: full revaluation, bucketed by `mkt_type`, with a `CROSSES` row and **no time component**. It is out of pricebt scope (§1.4).
3. **The `pnl_explain` body is byte-identical in 1.5.4 and 2.1.17** (installed `bo.py:323-370` = repo `bo.py:344-391`). `fx_pnl_definition()` exists only in the repo (`bo.py:996-1026`). I pinned the semantics by **running gs's own `BackTest.pnl_explain`** on duck-typed results (§3.3, S1–S6). Per instrument it computes `scaling_factor × risk(t−1) × Δmarket` (first order) or `0.5 × scaling_factor × risk(t−1) × Δmarket²` (second order), and returns a cumulative dict. **`scaling_factor` is applied once, not squared**: a gamma in ccy/bp² against a decimal level needs `1e8`, not `1e4`.
4. **gs edge behaviour, verified by execution:**
   - A trade that exits on a **non-grid** date while other trades continue makes gs raise **`KeyError`** (S3).
   - An exit-only date as `prev` **drops that interval's P&L for every continuing trade** (S6).
   - A **bucketed** (DataFrame) attribute metric raises `ValueError: truth value of a DataFrame is ambiguous` (S4).
   - A trade held on `prev` that is neither held nor exit-priced on `cur` raises `TypeError` (S5).

   pricebt's DEV-R1 (pricing continuing positions into `results[d]` on non-grid cash dates) turns S3 and S6 into correct attributions. That is a behavioural change and needs its own DEV row (§6).
5. **Units are the biggest trap.** The docstrings say `IRFwdRate` is "par rate (in percent)" and `IRAnnualImpliedVol` is "annual implied volatility (in percent)" (`target/measures.py:473, 455`). The gs notebooks, however, treat both as **decimals**: they bump `IRFwdRate` by `0.0001` for 1bp, set a vol of `0.009`, and multiply by `1e4` to get bp. `IRVega` is per **1bp** of `IRAnnualImpliedVol` and `IRGammaParallel` is per bp². Scaling factors are therefore `1e4` / `1e8` under gs units and `1` / `1` under DEV-I7 bp configs. **A gs-written PnlDefinition is off by 10⁴ in pricebt unless the configs return decimals.** 040307 cell 7 (`raw_data[IRFwdRate] *= 1e4`) has the same problem.
6. **Two further contract rules are needed:**
   - `result_summary` **sums every market-data metric across held instruments** (`aggregate(True, True)`), so an IRFwdRate column is a meaningless sum. Unequal units raise an uncaught `ValueError` (`core.py:590-592`; `bo.py:214` catches only `TypeError`). **Every asset that can sit in one book must declare the same unit for every shared measure.**
   - `ExpiryInYears` must be **intensive**. The closed unit set has no time unit, and `number` is extensive by default.
7. **`result_ccy` + `pnl_explain` is broken in gs (by reading).** Plain measures (`IRFwdRate`, `IRAnnualImpliedVol`, `IRGammaParallel`, `Theta`, `ExpiryInYears`) hit `raiser('Unparameterised risk')` (repo `ge.py:253-261`). Parameterised ones are rewritten in `risks` but not in the PnlDefinition, so the later lookups miss.
8. **Proposed swaption definition (gs names only):**
   - `delta`: `IRDeltaParallel` vs `IRFwdRate`;
   - `gamma`: `IRGammaParallel` vs `IRFwdRate`, second order;
   - `vega`: `IRVegaParallel` vs `IRAnnualImpliedVol`;
   - `volga`: `IRVolga(aggregation_level=Type)` vs `IRAnnualImpliedVol`, second order;
   - `theta`: `Theta` vs `ExpiryInYears`, scaling −365.

   **Proposed bond definition** (gs names in their docstring meaning):
   - `rates`: `IRDeltaParallel` vs `IRFwdRate`, with IRFwdRate = the matched-maturity par swap rate;
   - `spread`: `IRDeltaParallel` vs `ParSpread` (ASW/Z-spread);
   - `convexity`: `IRGammaParallel` vs `IRFwdRate`, second order;
   - `carry`: `Theta` vs `ExpiryInYears`.

   A yield-based variant would need IRFwdRate to mean YTM, which is a MUST-2 DEV. Vanna, charm and roll-down separated from carry cannot be written as a single `PnlAttribute` (§5.4).

Abbreviations: `bo.py` = `backtests/backtest_objects.py`, `ge.py` = `backtests/generic_engine.py`, `impls.py` = `backtests/generic_engine_action_impls.py`. **"repo"** = the 2.1.17 checkout `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant`. **"installed"** = 1.5.4 `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant`. Line numbers refer to the repo unless marked "installed". Notebook references are `<notebook> cell N` (0-based code/markdown cell index).

---

## 0. Sources and method

- **Notebooks.** All 330 `.ipynb` under repo `documentation/` and `content/` were parsed as JSON by a script (scratchpad `scan_nb.py`), which dumped every cell and counted these tokens in code cells: `IRSwaption, Bond, IRVega, IRGamma, Theta, IRAnnualImpliedVol, IRFwdRate, IRDailyImpliedVol, pnl_explain, PnlDefinition, PnlAttribute, IRVanna, IRVolga, DollarPrice, result_ccy, GenericEngine, backtest, ...`. I then ran `grep -l "gs_quant.backtests"` over the dumps and found 27 notebooks: the 24 in 04_backtesting, `3-Systematic Selling`, `4-Delta Hedging` and `eq_sx5e_spx_vol_spread_trade` (EquityVolEngine, EqOption only). I read every swaption-using backtest notebook in full.
- **Code.** I read repo `bo.py:69-391, 715-725, 996-1026`, `ge.py:130-340, 400-730`, `impls.py:60-139, 380-415`, `backtest_utils.py:90-116`, `risk/results.py:594-973`, `risk/core.py:516-617`, `risk/result_handlers.py:150-205`, `risk/measures.py:25-85`, `target/measures.py` (all IR measures, `Theta`, `ExpiryInYears`, `ParSpread`, FX local-ccy measures) and `target/instrument.py:81-90` (Bond) and `2685-2730` (IRSwaption). I diffed the pnl paths against installed `bo.py:61-80, 185-207, 323-370` and `ge.py:786-814, 1125-1131`.
- **Other docs.** The `gs-quant/docs` Sphinx sources contain nothing on backtest P&L. `risk.rst` only autodocs `Theta`, `IRGammaParallel`, `IRVega`, `IRAnnualImpliedVol`, `IRFwdRate`, etc. (`risk.rst:18-47`). No `.md` in the repo mentions `pnl_explain`. I did not fetch the developer.gs.com portfolios page named in the request: the local notebooks 030000-030011 are its source, and 030007 is covered in §1.4.
- **Verification.** Scratchpad script `pnl_check.py` drives installed `BackTest.pnl_explain` with fakes that mimic `PortfolioRiskResult`: `.portfolio.all_instruments`, `inst in .portfolio`, `[inst] → {measure: value}`, and `KeyError` for a missing instrument (results.py:951-952). **S1 is a hand-computed known answer and passes**, which checks the checker. S2–S6 are the edge cases.

---

## 1. Inventory

### 1.1 Backtest notebooks that use swaptions (6)

| Notebook (path under repo `gs_quant/`) | Engine | Swaption use | Also uses | Covered by R05? |
|---|---|---|---|---|
| `documentation/04_backtesting/examples/03_GenericEngine/040303_strategy_delta_hedge_Rates.ipynb` | GE | monthly short EUR 1m30y, held to expiry | IRSwap hedge, `IRDelta(Type, USD)` | yes (§7.2) |
| `.../040305_strategy_exit_trade_action.ipynb` | GE | USD 6m10y short, `strike='=solvefor(25e3,bp)'`, risk-trigger roll | `IRDelta(Type, 'USD')`, `ExitTradeAction` | yes (row) |
| `.../040307_strategy_seagull_bullish.ipynb` | GE | 3-leg USD 1y10y seagull, weekly ramp | `IRDelta(local, Type)`, post-run `IRFwdRate`, `IRDailyImpliedVol`, `DollarPrice` | yes (row) |
| `documentation/04_backtesting/tutorials/Backtesting.ipynb` | GE | daily short USD 1m10y straddle to expiry | – | yes (row) |
| `content/made_with_gs_quant/3-Systematic Selling.ipynb` | GE (cell 19) + manual | the same straddle as Backtesting | `DollarPrice`, `result_summary['Cash']` | **no** |
| `content/made_with_gs_quant/4-Delta Hedging.ipynb` | GE (cell 21) + manual | the same straddle + daily swap delta hedge | `IRDeltaParallel` (manual part), `IRDelta(Type, 'local')` hedge, IRSwap `'ATMF'` | **no** |

### 1.2 IR measures beyond Price/IRDelta in backtest context

| Measure | Where | How |
|---|---|---|
| `IRFwdRate` | 040307 cell 7 | `with HistoricalPricingContext(dates=backtest_summary.index, show_progress=True): ir_rates = seagull[0].calc([IRFwdRate, IRDailyImpliedVol])`, then `raw_data = ir_rates.result().to_frame()` and `raw_data[IRFwdRate] *= 1e4` (decimal → bp). Cell 8 plots `backtest_summary.iloc[5, 1] * (raw_data[IRFwdRate] - raw_data[IRFwdRate][0]) / 1e6` as a **"Delta Proxy (k per bp)"**. This is a hand-rolled first-order delta P&L: IRDelta at one date × cumulative Δfwd in bp. It is the only P&L attribution in any gs backtest notebook. |
| `IRDailyImpliedVol` | 040307 cell 7 | fetched only; never used after `describe()` |
| `DollarPrice` | 040307 cell 7 (`seagull.calc([DollarPrice])` → `entry_premium` column); 3-Systematic Selling cells 22-23 (`backtest.result_summary[DollarPrice]`, **never requested**, so a KeyError: defect §7) | |
| `IRDeltaParallel` | 4-Delta Hedging cells 9, 11 (manual, non-backtest path) | `inst.calc(IRDeltaParallel)` under `HistoricalPricingContext` |
| `IRVega`, `IRGamma`, `IRVanna`, `IRVolga`, `Theta`, `ExpiryInYears`, `IRAnnualImpliedVol`, `Bond` | **no backtest notebook** | – |

### 1.3 Uses of `pnl_explain` / `PnlDefinition` / `PnlAttribute` / `fx_pnl_definition`

| Location | Nature |
|---|---|
| repo `bo.py:69-88` (installed `61-80`) | class definitions |
| repo `bo.py:103, 115, 182-184` | `BackTest.pnl_explain_def` field, `_trade_exit_risk_results`, property |
| repo `bo.py:344-391` (installed `323-370`) | `BackTest.pnl_explain()` |
| repo `bo.py:996-1026` (**repo only**) | `fx_pnl_definition()`: `PNL_delta` (`FXDeltaLocalCcy` vs `FXSpot`, 1.0), `PNL_gamma` (`FXGammaLocalCcy` vs `FXSpot`, 1.0, second order), `VegaPnL` (`FXVegaLocalCcy` vs `FXAnnualImpliedVol`, **100.0**). The docstring says it "matches BasicBacktestRequest PnL measures … equivalent to the hardcoded formulas in GenericEngineBasicBacktestRunner". That runner is not in the repo (grep: 0 hits outside this docstring). |
| repo `ge.py:152-153, 175, 247-252, 271, 684-686` (installed `790-795, 814, 1129-1131`) | `run_backtest(..., calc_risk_at_trade_exits=False, pnl_explain=None)` plumbing |
| repo `api/gs/backtests_xasset/request.py:95-119, 178-180` | decode `pnl_explain_def` on `GenericBacktestRequest` (server-side generic backtest) |
| notebooks, repo `test/` | **none**. grep over `test/` for `pnl_explain|PnlAttribute|PnlDefinition|fx_pnl_definition|calc_risk_at_trade_exits|trade_exit_risk_results` returns 0 hits. |

`target/backtests.py:80-98` `FlowVolBacktestMeasure` (`PNL_delta, PNL_gamma, PNL_vega, PNL_theta, PNL_carry, PNL_vol, PNL_higher_order_spot, PNL_higher_order_vol, PNL_unexplained, …`) is the EquityVolEngine's server-side decomposition. 040202 uses `PNL`, `PNL_carry` and `PNL_vol` (cell 7). It is not a GenericEngine feature.

### 1.4 Server-side `PnlExplain` (a different feature, out of scope)

`PnlExplain(to_market)` is a `__RelativeRiskMeasure` (`risk/measures.py:25-52`) whose `pricing_context` is `RelativeMarket(from_market=current.market, to_market=...)`. Its result goes through `risk_by_class_handler` → an **unsorted DataFrame by `mkt_type`/`mkt_asset`** (`result_handlers.py:166-201`), and the SPIKE/JUMP rows are folded into `CROSSES`.
- 030007 cell 4: `explain = PnlExplain(CloseMarket(date=to_date))`, computed at `from_date`, with the comment **"Compute the time component (PnlExplain does not do this)"**. It prints `Dollar price difference` against `Pnl explain + time value total`.
- `content/events/.../0001_portfolios_and_var.ipynb` cell 6-7: `Theta + Pnl explain total`, so gs's own decomposition is "PnlExplain (market) + Theta (time)".
- `content/made_with_gs_quant/10-Explaining Performance Drivers.ipynb` cell 9: "You'll notice a `CROSSES` PnL, which represents the cross-effects among the other types."

pricebt cannot compute this generically. It needs full revaluation under a mixed market, which only a config could do. Leave it out, or later expose it as a config portfolio function.

### 1.5 Non-backtest notebooks that pin units (evidence used in §4)

| Evidence | Location |
|---|---|
| `MeasureScenario(IRFwdRate, 0.024)`; `MeasureScenario(IRFwdRate, init[IRFwdRate] + 0.0001)` for ±1bp | `documentation/02_pricing_and_risk/01_scenarios_and_contexts/examples/09_measure_scenario/010902_rates_override_example.ipynb` cells 4, 8 |
| `MeasureScenario(IRAnnualImpliedVol, 0.009)`, `(…, 0.002)` | same, cells 12, 14 |
| `swaption.calc(risk.IRAnnualImpliedVol) * 10000` | `documentation/02_pricing_and_risk/00_instruments_and_measures/tutorials/Measures.ipynb` cell 13 |
| `portfolios.calc(IRAnnualImpliedVol).to_frame().values * 10000`; `results.to_frame(...) * 10000` | `content/reports_and_screens/02_rates/0001_vol_moneyness_screen.ipynb` cell 2; `0000_vol_fixed_strike_grid.ipynb` cell 2 |
| `raw_data[IRFwdRate] *= 1e4` | 040307 cell 7 |
| `calc(IRSpotRate) * 100` (to percent) | `content/made_with_gs_quant/1-Navigating Rates.ipynb` cell 26 |
| "the OTM swaption prices at 0 post expiry whereas the ITM swaption prices at the value of the swap" | `documentation/02_pricing_and_risk/01_scenarios_and_contexts/examples/01_rollfwd_shock/03_rollfwd-showing-lifecycling-effects-on-swaps-and-swaptions.ipynb` cell 5 |
| `RollFwd(date=..., realise_fwd=True/False)` (carry vs roll-down vocabulary) | same, cells 2, 4; `content/reports_and_screens/02_rates/0002_swaption_carry_grid.ipynb` cell 2 (`with RollFwd(horizon): price_fwd = portfolios.price()`) |
| "Swaption … non-linear … we can calculate Vanna and Volga for it, while IRSwap is linear and have it as zero" | `.../01_rates/23_solve_vanna_&_volga.ipynb` cell 6 |
| "CDTheta - change in option Dollar Price over one day" | `.../04_credit/03_cdindex_option_risks.ipynb` markdown |

### 1.6 Bonds

No notebook constructs a `Bond`. The only bond mentions are two:
- `IRAssetSwapFxdFlt` "on a US10Y Bond" (`.../01_rates/21_asset_swap_definition.ipynb` cells 2, 4, with `identifier='GB5Y', traded_clean_price=99`);
- "IRDelta is additional available for … Bond Futures upon request" (`tutorials/Instruments.ipynb`).

gs `Bond` (`target/instrument.py:81-90`) has only the fields `buy_sell, identifier, identifier_type, size, settlement_date, settlement_currency, name` (plus `asset_class=Cross_Asset`, `type_=Bond`). gs has **no bond-specific risk measure**: there is no yield, no yield-DV01 and no convexity. The closest are `LightningDV01` and `LightningOAS` (`target/measures.py:511, 514`), whose semantics are undocumented. So everything in §5.2 is a proposal.

---

## 2. Per-notebook deep dives

In the "pricebt needs" column, "config fn" means a `functions:` entry in the swaption/swap asset YAML (DESIGN §4.2), and "attr" means an `attributes:` entry (DESIGN §4.2, §9.4).

### 2.1 040303_strategy_delta_hedge_Rates: sell a EUR 1m30y swaption monthly, delta-hedge monthly

| Aspect | gs text |
|---|---|
| Dates | `start_date = date(2021, 6, 1)`; `end_date = datetime.today().date()` (cell 3) |
| Instrument | `IRSwaption(expiration_date='1m', termination_date='30y', notional_currency=Currency.EUR, buy_sell='Sell')` (cell 4). **No `notional_amount`, `pay_or_receive` or `strike`**, so gs server defaults apply. |
| Hedge | `IRSwap(termination_date='30y', notional_currency=Currency.EUR, notional_amount=100e6, name='30yhedge')`. **No `pay_or_receive` or `fixed_rate`.** |
| Trigger | `PeriodicTrigger(PeriodicTriggerRequirements(start_date, end_date, frequency='1m'), [action_add, action_hedge])` |
| Actions | `AddTradeAction(option, 'expiration_date')`; `HedgeAction(IRDelta(aggregation_level='Type', currency=Currency.USD), swap_hedge)`. The hedge has no duration, so it is held forever and hedges accumulate. |
| Run | `GE.run_backtest(strategy, start=start_date, end=end_date, frequency='1b', show_progress=True)`. Comment: "The results will be in local ccy, EUR in this case. To change them to USD, specify result_ccy". |
| Risks | Price, plus `IRDelta(aggregation_level:Type, currency:USD)`, which enters through `strategy.risks` |
| Views | `trade_ledger()` (cell 6); `result_summary` (cell 7); `df = backtest.result_summary; df['Performance'] = df[Price] + df['Cumulative Cash']; df.plot(figsize=(10, 10)).legend(bbox_to_anchor=(1, 1))` (cell 8) |
| **pricebt needs** | EUR swaption asset (curve + vol cube market) and EUR swap asset. Config `defaults` for the missing kwargs: swaption `notional_amount`, `pay_or_receive`, `strike`; swap `pay_or_receive`, `fixed_rate`. Attr `expiration_date`. Scalar `IRDelta` form on both assets (dv01). FX config EUR→USD, because the hedge risk carries `currency=USD`. Exit at expiry: `npv` on the expiration date must return the exercise value (§4, "post-expiry"). |

### 2.2 040305_strategy_exit_trade_action: USD 6m10y short, rolled when IRDelta breaches +1k

| Aspect | gs text |
|---|---|
| Instrument | `IRSwaption(expiration_date='6m', termination_date='10y', notional_currency=Currency.USD, buy_sell=BuySell.Sell, strike='=solvefor(25e3,bp)', name='swaption10y')` (cell 3) |
| Trigger | `StrategyRiskTrigger(RiskTriggerRequirements(risk=IRDelta(aggregation_level=AggregationLevel.Type, currency='USD'), trigger_level=1e3, direction=TriggerDirection.ABOVE), [ExitTradeAction(), AddTradeAction(swaption)])`. Comment: "Order is important here, we first want to exit all positions in portfolio, then add the new trade". |
| Strategy | `Strategy(swaption, trigger_risk)`: the initial portfolio is the swaption |
| Run | `'1b'`, `show_progress=True` |
| Views | `result_summary` (cell 6); `trade_ledger()` (cell 7; the comment "excluding starting portfolio" is wrong, R05§8); `pd.DataFrame({'Generic backtester': backtest.result_summary[Price]}).plot(figsize=(10, 6), title='Mark to market')` (cell 8); the same with `['Cumulative Cash'] + [Price]`, title `'Performance'` (cell 9) |
| **pricebt needs** | USD swaption asset. `'=solvefor(25e3,bp)'` is a **server-side solver**, and its target semantics are not in gs source. The config `resolve` must raise (DESIGN §13.3) unless the user writes a documented solver. The notebook then runs with a numeric or `'ATM…'` strike. A scalar `IRDelta` with `currency='USD'`. The path-dependent trigger ensures risks daily. Every re-add re-resolves `'=solvefor…'` at the new date, so the resolve must pin the strike per trade date. |

### 2.3 040307_strategy_seagull_bullish: weekly ramp into a USD 1y10y seagull

| Aspect | gs text |
|---|---|
| Params | `start = date(2020, 1, 1)`; `end = business_day_offset(date.today(), -1, roll='preceding')`; `freq = '1w'`; `ramp_up_mult = 5`; `ramp_up_period = 4`; `holding_period = '4w'`; `strategy_notional = 100e6`; `notional_inception = round(100e6 / 5, 0)` = 20e6 (cell 2) |
| Instruments | For `i in 0..2`: `IRSwaption(pay_or_receive=['Receiver','Receive','Pay'][i], termination_date='10y', notional_currency='USD', expiration_date='1y', notional_amount=[-1, 1, -1][i] * notional_inception, strike=['A-50','A-25','A+50'][i], floating_rate_option='USD-LIBOR-BBA', name='swaption'+str(i))`. So: short receiver A-50, long receiver A-25, short payer A+50. **The sign is carried by a negative notional; `buy_sell` is unset.** `seagull = Portfolio(port, name='Seagull')`; `seagull.to_frame().transpose()` (cell 4). `csa_string = 'USD-1'`. |
| Trigger/action | `AddTradeAction(seagull, holding_period)` on `PeriodicTrigger(PeriodicTriggerRequirements(start_date=start, end_date=end, frequency=freq), [trade_action])` |
| Run | `GE.run_backtest(strategy, start=start, end=end, frequency=freq, risks=[Price, myParallelDelta], show_progress=True, csa_term=csa_string)` with `myParallelDelta = IRDelta(currency='local', aggregation_level=AggregationLevel.Type)` (cell 5) |
| Views | `backtest_summary = backtest.result_summary` (cell 6); `backtest_summary.index` (cell 7); `backtest_summary['Cumulative Cash'] / 1e6` and **`backtest_summary.iloc[5, 1]`** (cell 8) |
| Post-run pricing | `with HistoricalPricingContext(dates=backtest_dates_reduced, show_progress=True): ir_rates = seagull[0].calc([IRFwdRate, IRDailyImpliedVol]); entry = seagull.calc([DollarPrice])`; `premium = entry.result().to_frame()`; `raw_data = ir_rates.result().to_frame()`; **`raw_data[IRFwdRate] *= 1e4`**; `raw_data['entry_premium'] = premium.iloc[:, 0] + premium.iloc[:, 1] + premium.iloc[:, 2]`; `raw_data.describe()` (cell 7). `seagull` is **unresolved** here, so each historical date re-resolves `'A-50'` etc. at that date, which prices a fresh seagull (cf. 4-Delta Hedging cell 10: "I create and price a new swap each day which will reflect that's day's ATM rate"). |
| **pricebt needs** | USD swaption asset with the strike grammar `'A±N'` (N in bp relative to ATM, pinned at resolve). It must accept both `'Receiver'` and `'Receive'` (both are valid `PayReceive` members, `target/common.py:4311-4320`). The resolve folds the sign of `notional_amount` (DESIGN §13.3). `floating_rate_option='USD-LIBOR-BBA'` must be mapped (e.g. to SOFR) or rejected by the config, because LIBOR is gone. `csa_term='USD-1'` reaches configs as `pricebt_csa`. `pricebt.datetime.business_day_offset(date, -1, roll='preceding')`. `Portfolio.to_frame()`. `HistoricalPricingContext` + `calc([...]).result().to_frame()` with RiskMeasure-keyed columns. Config fns for `IRFwdRate` and `IRDailyImpliedVol`. **Under DEV-I7 bp units, `*= 1e4` must be deleted in the converted notebook**, or IRFwdRate returned as a decimal. With DEV-E14's ordered risks, `iloc[:, 1]` is deterministically `IRDelta(aggregation_level:Type, currency:local)`. In gs it depends on set order. |

### 2.4 tutorials/Backtesting.ipynb: sell a USD 1m10y straddle daily, hold to expiry

| Aspect | gs text |
|---|---|
| Dates | `start_date, end_date = date(2020, 1, 1), date(2020, 12, 1)` (cell 4) |
| Instrument | `IRSwaption(PayReceive.Straddle, '10y', Currency.USD, expiration_date='1m', notional_amount=1e8, buy_sell='Sell')` (cell 6). The positional arguments map to `pay_or_receive, termination_date, notional_currency` (`target/instrument.py:2686-2688`). |
| Trigger/action | `PeriodicTrigger(PeriodicTriggerRequirements(start_date, end_date, frequency='1b'), AddTradeAction(straddle, 'expiration_date'))`; `Strategy(None, trigger)` |
| Engine API | `strategy.get_available_engines()` (cell 10); `ge = GenericEngine(); ge.supports_strategy(strategy)` (cell 12); `backtest = ge.run_backtest(strategy, start=start_date, end=end_date, frequency='1b', show_progress=True); backtest` (bare repr, cell 14) |
| Views | markdown cell 15 lists `backtest.results_summary` (typo), `backtest.trade_ledger()`, `backtest.portfolio_dict`, `backtest.calc_calls`, `backtest.calculations`. Code: `trade_ledger()` (16), `result_summary` (17), `result_summary[Price]` "Mark to market" plot (18), `['Cumulative Cash'] + [Price]` "Performance" (19) |
| **pricebt needs** | The swaption config must price **`Straddle`** (payer + receiver) or reject it. `build_on` and the resolve pin expiry/strike (ATMF). `npv` on the expiry date = intrinsic (§4). `calc_calls`/`calculations` counters. About 21 overlapping live straddles, so vol-cube market sharing per date matters for speed. |

### 2.5 content/made_with_gs_quant/3-Systematic Selling.ipynb (gs-quant 0.8.102 era)

- **Manual part (cells 4-16).** `with HistoricalPricingContext(start=start_date, end=end_date, show_progress=True): f = IRSwaption(PayReceive.Straddle, '10y', Currency.USD, expiration_date='1m', notional_amount=1e8, buy_sell='Sell').resolve(in_place=False)`. Then `portfolio = Portfolio([v[1] for v in sorted(f.result().items())])`, `frame = portfolio.to_frame()` (it reads `frame.premium_payment_date` and `row.expiration_date`), and per-instrument `HistoricalPricingContext(start=row.trade_date, end=min(row.expiration_date, today), is_async=True): inst.price()` under `PricingContext(is_batch=True)`. It builds "Premium Received at Inception / Paid at Expiry / Mark to Market" by hand. `start_date = date(2020, 12, 1)`.
- **GE part (cell 19).** `IRSwaption(PayReceive.Straddle, '10y', Currency.USD, expiration_date='1m', notional_amount=1e8, buy_sell='Sell', name='1m10y')`; `AddTradeAction(irswaption, 'expiration_date')` on a `'1b'` Periodic trigger; `GE.run_backtest(strategy, start=start_date, end=end_date, frequency='1b', show_progress=True)`.
- **Views (cells 20-23).** `result_summary`; `backtest.result_summary['Cash'].cumsum()`; `backtest.result_summary[DollarPrice]`. **Both are KeyErrors against 1.5.4/2.1.17.** There is no `'Cash'` column (only `'Cumulative Cash'`, `bo.py:94`), and `DollarPrice` is never requested (the default `price_measure=Price`, `ge.py:89`). Defect (§7). The 1:1 port runs as in Backtesting.ipynb.
- **pricebt needs.** As §2.4, plus `resolve(in_place=False)` under `HistoricalPricingContext` returning a per-date future dict, `premium_payment_date` as an attr, and `PricingContext(is_batch=True)` / `is_async=True` accepted as no-ops.

### 2.6 content/made_with_gs_quant/4-Delta Hedging.ipynb (gs-quant 0.8.108 era)

- **Manual part (cells 4-15).** The same straddles, plus `inst.calc(IRDeltaParallel)`. There is a per-straddle hedge swap `IRSwap(PayReceive.Pay, row.termination_date, Currency.USD, effective_date=row.effective_date, fixed_rate='ATMF', notional_amount=1e8)`, resolved daily and priced one day later. The hedge notional is `-(swaption_delta / swap_sold_delta).shift(1)`. Markdown cell 19 says this is "economically equivalent to layering the hedges".
- **GE part (cell 21).** Imports `AggregationLevel`, `IRDelta`, `Price`.
  - `irswaption = IRSwaption('Straddle', '10y', 'USD', expiration_date='1m', notional_amount=1e8, buy_sell='Sell', name='1m10y')`;
  - `swap_hedge = IRSwap(PayReceive.Pay, '10y', 'USD', fixed_rate='ATMF', notional_amount=1e8, name='10y_swap')`;
  - `action_trade = AddTradeAction(irswaption, 'expiration_date')`;
  - `action_hedge = HedgeAction(IRDelta(aggregation_level=AggregationLevel.Type, currency='local'), swap_hedge, '1b')`;
  - both on `PeriodicTrigger(trig_req '1b', [action_trade, action_hedge])`, then `GE.run_backtest(..., frequency='1b', show_progress=True)`.
- Markdown cell 22: "passed '1b' for hedge holding period. This means that the delta from ALL swaption positions on a given date will be hedged with a single swap which will be replaced each day. Note if instead of '1b', we put '10y' … the swap hedge will be 'layered'."
- **View (cell 23).** `backtest.result_summary['Cash'].cumsum() + backtest.result_summary[Price]`. The `'Cash'` column is a KeyError (defect §7).
- **pricebt needs.** As §2.4. The swap asset must resolve `fixed_rate='ATMF'`, which for a spot-starting swap is the same as ATM. `IRDelta(Type, 'local')` must be scalar on both assets. **This notebook is the natural first pnl_explain use case** (swaption book + swap hedges), see §5.3.

### 2.7 Summary of what a config-defined swaption asset must provide for all six

| Item | Needed by |
|---|---|
| `IRSwaption` kwargs: `pay_or_receive` ∈ {Pay, Payer, Receive, Receiver, Rec, **Straddle**}, `termination_date`, `notional_currency`, `effective_date`, `notional_amount` (**signed**), `expiration_date`, `floating_rate_option`, `strike` (`'ATM'`, `'ATMF'`, `'A±N'`, decimal, `'=solvefor(…)'`→raise), `buy_sell`, `premium`, `fee`, `settlement` | all; the strike grammar for 040305/040307 |
| resolve: pin expiry/termination dates and strike at the trade date; sign = `buy_sell` × sign(`notional_amount`) | all (DESIGN §13.3) |
| defaults for omitted notional / pay_or_receive / strike | 040303 |
| attr `expiration_date` (for `trade_duration='expiration_date'`), `termination_date`, `premium_payment_date` | 040303, Backtesting, 3-SS, 4-DH |
| `npv` valid **on and after expiry**: 0 if OTM, the underlying swap value if ITM (physical) or the cash settlement amount | every `'expiration_date'` exit |
| scalar `IRDelta` (Type/Asset) in the asset currency, convertible to USD via FX | 040303, 040305, 040307, 4-DH |
| `IRFwdRate`, `IRDailyImpliedVol` (intensive) | 040307 post-run |
| `DollarPrice` (= Price in USD, DESIGN §8.1 rule 2) | 040307, 3-SS |
| market: curve + vol cube as one object, own market key; EUR and USD | 040303 (EUR), others (USD) |
| `csa_term` → `pricebt_csa` | 040307 |
| LIBOR `floating_rate_option` handling | 040307 |

---

## 3. `pnl_explain` — exact semantics

### 3.1 Engine plumbing (repo `ge.py`, installed line numbers in brackets)

```python
# ge.py:247-252 [installed 790-795]
if pnl_explain is not None:
    calc_risk_at_trade_exits = True
    pnl_risks = pnl_explain.get_risks()          # [attr.attribute_metric, attr.market_data_metric] per attribute
else:
    pnl_risks = []
risks = list(set(make_list(risks) + strategy.risks + pnl_risks + [self.price_measure]))
# ge.py:253-261: result_ccy rewrite
if result_ccy is not None:
    risks = [(r(currency=result_ccy) if isinstance(r, ParameterisedRiskMeasure)
              else raiser(f'Unparameterised risk: {r}')) for r in risks]
# ge.py:271 [814]
backtest = BackTest(strategy, strategy_pricing_dates, risks, price_risk, holiday_calendar, pnl_explain)
```

- **Every pnl measure is computed for every held position on every date**, and it becomes a `result_summary` column: `get_risk_summary_df` iterates `results.risk_measures` (bo.py:211). The docstring of `run_backtest` says: "`:param calc_risk_at_trade_exits:` separate results for requested risk measures on tradable exit dates; not to be included in main results but useful for PnL decomposition" and "`:param pnl_explain:` a Pnl Definition object which defines the risk attribution and mkt data for a pnl explain" (ge.py:173-175).
- **Exit risks** are built in `_handle_cash` (ge.py:664-686 [installed 1125-1131 for the calc block]). For every CashPayment with `effective_date <= strategy_end_date` whose trade is **not** in `results[effective_date]`, the trade is priced for `price_risk`. If `calc_risk_at_trade_exits and cp.direction == 1` (an exit payment; `CashPayment.direction` defaults to 1, bo.py:716-721), it is also priced for **all `risks`** into `backtest.trade_exit_risk_results[cash_date] = Portfolio(expiring_trades).calc(risks)` (ge.py:684-686).
- Positions are held on `create_date <= s < final_date` (impls.py:128-130, 388). So **on its exit date a trade is never in `results[exit]`**, and its market metric at exit comes from `trade_exit_risk_results`.

### 3.2 The method (repo bo.py:344-391, identical to installed 323-370)

```python
def pnl_explain(self):
    if self.pnl_explain_def is None:
        return None
    risk_results = self.results
    exit_risk_results = self.trade_exit_risk_results
    dates = sorted(set(risk_results.keys()).union(exit_risk_results.keys()))
    pnl_explain_results = {}
    for attribute in self.pnl_explain_def.attributes:
        result = {}
        cum_total = 0.0
        for idx in range(1, len(dates)):
            metric_pnl = 0.0
            cur_date = dates[idx]
            prev_date = dates[idx - 1]
            if prev_date not in risk_results:
                result[cur_date] = cum_total
                continue
            for prev_date_inst in risk_results[prev_date].portfolio.all_instruments:
                prev_date_risk = risk_results[prev_date][prev_date_inst][attribute.attribute_metric]
                if prev_date_risk == 0:
                    continue
                prev_date_mkt_data = risk_results[prev_date][prev_date_inst][attribute.market_data_metric]
                if cur_date in risk_results and prev_date_inst in risk_results[cur_date].portfolio:
                    cur_date_mkt_data = risk_results[cur_date][prev_date_inst][attribute.market_data_metric]
                else:
                    cur_date_mkt_data = exit_risk_results[cur_date][prev_date_inst][attribute.market_data_metric]
                if attribute.second_order:
                    metric_pnl += (0.5 * attribute.scaling_factor * prev_date_risk
                                   * (cur_date_mkt_data - prev_date_mkt_data)
                                   * (cur_date_mkt_data - prev_date_mkt_data))
                else:
                    metric_pnl += attribute.scaling_factor * prev_date_risk * (cur_date_mkt_data - prev_date_mkt_data)
            cum_total += metric_pnl
            result[cur_date] = cum_total
        pnl_explain_results[attribute.attribute_name] = result
    return pnl_explain_results
```

In words:
- The date axis is the union of result dates and exit dates. It includes off-grid exit dates, which are also cash dates and so are in the `result_summary` index.
- **Per instrument**, P&L uses the **previous date's own risk** times the change in its **own** market metric: first order `k·R(t−1)·Δm`, second order `½·k·R(t−1)·Δm²`. `R` is the position-scaled risk (gs scales the notional; pricebt multiplies extensive units by `quantity_`), and `m` is intensive (a level), so sign and size come from `R` alone.
- The skip `if prev_date_risk == 0` (bo.py:369) means **a zero attribute metric never reads the market metric**. That is useful for swaps in a vol attribution.
- Trades entered on `cur` contribute nothing for that interval. They enter at PV, with `−PV` booked as cash.
- The output is `{attribute_name: {date: cumulative P&L}}` with **no entry for `dates[0]`**. It is a plain dict (use `pd.DataFrame(bt.pnl_explain())`), not in any currency object, and not cached.
- The **unexplained** residual is not computed. It is `[Total(t)−Total(t₀)] − [TC(t)−TC(t₀)] − cash accrual − Σ_attr(t)`. Entry and exit cash are PV-neutral at the trade date, and the exit-date move is attributed through the exit risks.
- `PnlAttribute` is a plain dataclass (`attribute_name: str, attribute_metric: RiskMeasure, market_data_metric: RiskMeasure, scaling_factor: float, second_order: bool = False`, bo.py:69-79). Nothing validates the measures or units.
- `pnl_explain` does not have the `len(x[1])` filter that `get_risk_summary_df` applies to empty result entries (bo.py:208). pricebt's DEV-E8 (`.get(d)`, no defaultdict `[]` entries) makes that moot.

### 3.3 Edge semantics, **verified by execution** against installed `BackTest.pnl_explain` (scratchpad `pnl_check.py`)

The measures are symbolic: `D` = delta per bp, `G` = gamma per bp², `F` = a decimal level. Dates `d1 < d2 < d3`, and `e` is an off-grid date between `d1` and `d3`.

| # | Setup | gs result | Meaning |
|---|---|---|---|
| S1 (known answer) | A held d1..d3; D=100, G=10; F: 0.0200 → 0.0201 → 0.0203. Attributes: delta (k=1e4), gamma (k=1e8, 2nd), gamma_1e4 (k=1e4, 2nd) | delta {d2: 100, d3: 300}; gamma {d2: 5, d3: 25}; gamma_1e4 {d3: 0.0025}; no d1 key | Formula confirmed. **k is not squared in the 2nd-order branch**, so a bp² gamma on a decimal level needs k = 1e8. Output is cumulative with no first-date entry. |
| S2 | A (D=100) exits on grid d2 (exit results F=0.0201); B (D=−50) continues, F 0.0300 → 0.0301 | delta {d2: 50} | The exit-date move uses `trade_exit_risk_results` (100·1 − 50·1). |
| S3 | A exits on off-grid `e`; B held d1..d3; results only on d1, d3 | **`KeyError: 'B'`** | For `cur=e`, B is neither in `results[e]` nor in `exit_results[e]`. **gs pnl_explain crashes whenever an exit falls off the grid while other positions continue**, e.g. a `'1m'`/`'1w'` run grid with `'expiration_date'` exits, or any tenor exit on a coarse grid. |
| S4 | attribute metric is a bucketed DataFrame | **`ValueError: The truth value of a DataFrame is ambiguous`** | `prev_date_risk == 0` in an `if`. **Only scalar attribute metrics work**: use `IRDeltaParallel`, `IRVegaParallel`, `IRDelta(aggregation_level=Type)` and so on, never bare `IRDelta`/`IRVega`/`IRVolga`. |
| S5 | A held on d1, not held on d2, no exit results | **`TypeError: list indices must be integers or slices, not str`** | `exit_risk_results` is a `defaultdict(list)`. pricebt must guarantee exit results for every exit date that follows a held date, including exits rescheduled by missing-market handling (DEV-E16). |
| S6 | exit results on `e` include A and B; results on d1, d3 only | delta {e: 250, d3: 250} | Because `e ∉ results`, the interval e→d3 is **skipped for B**: its −150 is lost. |

**By reading, not executed:**
- **`result_ccy` + `pnl_explain`.** Plain measures (`RiskMeasure`, not `ParameterisedRiskMeasure`, `common.py:61, 86`) such as `IRFwdRate`, `IRAnnualImpliedVol`, `IRDailyImpliedVol`, `IRGamma`, `IRGammaParallel`, `Theta`, `ExpiryInYears` and `DollarPrice` make `run_backtest` raise `RuntimeError('Unparameterised risk: …')` (ge.py:62-63, 253-261). Even an all-parameterised definition fails later: `risks` holds `IRDeltaParallel(currency=X)` while the attribute still holds `IRDeltaParallel`, so `results[prev][inst][attribute.attribute_metric]` misses.
- **Hedge instruments.** Each hedge day receives a `copy.deepcopy(scaled_portfolio_position)` (ge.py:528-531). Continuity across dates relies on value equality in `Portfolio.__contains__` (`item in p.__priceables`, portfolio.py:128-134). pricebt's instrument `__eq__` (DESIGN §5.1 item 3) gives the same result for deep copies.
- **NaN/ErrorValue.** A NaN risk passes `== 0` and poisons `cum_total` for **all later dates**. An `ErrorValue` fails the arithmetic. In `result_summary`, an errored cell makes `aggregate_results` raise `ValueError('Cannot aggregate results in error')` (core.py:584-585), which `get_risk_summary_df` does not catch (bo.py:212-215 catches `TypeError` only).
- **`result_summary` columns for market metrics** are `results[m].aggregate(True, True)`, i.e. `FloatWithInfo(sum(...))` over held instruments (core.py:610-611). A summed IRFwdRate is not a level. Unequal units across instruments raise `ValueError('Cannot aggregate results with different units …')` (core.py:590-592). pricebt's `FloatWithInfo.__add__` raises on unit mismatch too (DESIGN §8.2). Per-instrument levels are in `strategy_as_time_series()['Risk Measures']`.

### 3.4 Consequences for pricebt

- DEV-R1 already prices continuing positions into `results[d]` on off-grid cash dates, **for all `risks`**. With that, S3 and S6 give correct attributions instead of `KeyError` / lost P&L. **This is a behavioural difference from gs pnl_explain and should be listed** (proposed DEV-R6, §6), with a test built from S3/S6.
- The ported `pnl_explain` needs no change beyond the DEV-E8 `.get`. Its correctness depends on:
  - (a) the scalar form of attribute metrics (rule 5 of DESIGN §8.1 picks bucketed for a bare `IRDelta`/`IRVega`, so S4 applies);
  - (b) same-unit market metrics across assets;
  - (c) intensive market metrics and extensive attribute metrics;
  - (d) finite values on exit dates (for example an expiring swaption's vol and forward on its expiry date).

---

## 4. Units and scale of the relevant measures

| gs measure | class (`target/measures.py` line) | docstring (quoted) | Value gs actually returns (evidence) | Unit in pricebt configs (recommended) |
|---|---|---|---|---|
| `Price` | CurrencyParam (547) | "Present Value" | ccy | `ccy` |
| `DollarPrice` | plain (310) | "Price of the instrument in US Dollars" | USD | via Price(currency=USD) |
| `IRDelta` / `IRDeltaParallel` | FD-param (463); preset `aggregation_level=Asset` (`risk/measures.py:81`) | "Change in Dollar Price (USD present value) due to individual 1bp moves in the interest rate instruments used to build the underlying discount curve" | ccy per +1bp. `aggregation_level=Type` comes back scalar in the gs tests (`test/backtest/test_generic_engine.py:171`: `round(summary[hedge_risk].sum())`) | `ccy_per_bp` |
| `IRGamma` | plain (475) | "IRGamma" | – | – |
| `IRGammaParallel` | plain (478) | "Change in aggregated IRDelta for a aggregated 1bp shift in the interest rate instruments used to build the underlying discount curve" | ccy per bp² | `ccy_per_bp2` |
| `IRVega` / `IRVegaParallel` | FD-param (490); preset Asset (`risk/measures.py:84`) | "Change in Dollar Price (USD present value) due to individual 1bp moves in the implied volatility (IRAnnualImpliedVol) of instruments used to build the volatility surface" | ccy per 1bp of normal vol | `ccy_per_bp` |
| `IRVolga` | FD-param (493) | "Interest Rate Volga (USD)" | unit unstated; ccy per bp² of vol by construction | `ccy_per_bp2` |
| `IRVanna` | FD-param (487) | "Interest Rate Vanna (USD)" | unit unstated; ccy per (bp rate × bp vol) | `ccy_per_bp2` |
| `IRFwdRate` | plain, unit Percent (472) | "Interest rate par rate (in percent)" | **decimal**: `MeasureScenario(IRFwdRate, 0.024)`, `+ 0.0001` = 1bp (010902 cells 4, 8); `*= 1e4` → bp (040307 cell 7) | `bp` (DEV-I7) |
| `IRAnnualImpliedVol` | plain, unit Percent (454) | "Interest rate annual implied volatility (in percent)" | **decimal normal vol**: `MeasureScenario(IRAnnualImpliedVol, 0.009)` (010902 cell 12); `* 10000` → bp (Measures cell 13; screens) | `bp` |
| `IRAnnualATMImpliedVol` | plain, Percent (451) | "Interest rate annual implied at-the-money volatility (in percent)" | decimal (010902 frames alongside IRAnnualImpliedVol) | `bp` |
| `IRDailyImpliedVol` | plain, unit BPS (460) | "Interest rate daily implied volatility (in basis points)" | bp per day. **The annual→daily factor (√252? √250? √365?) is not stated anywhere in gs** | `bp`, with the factor declared in the config; do not use it for vega P&L |
| `IRSpotRate` | plain, Percent (484) | "Interest rate at-the-money spot rate (in percent)" | decimal (`* 100` → percent, 1-Navigating Rates cell 26) | `bp` |
| `Theta` | plain (568) | "Theta" | unstated. Siblings: `EqTheta` "Change in Dollar Price over one day" (328); `CDTheta` "change in option Dollar Price over one day" (03_cdindex_option_risks). 0001_portfolios_and_var cell 7 adds `Theta` to a 1-day PnlExplain | `ccy` per **calendar day** (declare it) |
| `ExpiryInYears` | plain (334) | "Time to Expiry expressed in fractional Years." | year fraction; the day count is unstated | `number` **with `scale_with_quantity: false`**, or a new intensive `years` unit (§6) |
| `ParSpread` | plain, Rates (538) | "Par Spread" | – | `bp` |
| FX reference: `FXVegaLocalCcy` / `FXAnnualImpliedVol` | (430)/(346) | "…due to a 1 vol move in implied volatility" / "FX Annual Implied Volatility" | `fx_pnl_definition` uses **scaling 100**: vega per vol point × Δ(decimal vol) × 100. This is consistent with gs vols being decimals. | – |

**Post-expiry swaption value.** "the OTM swaption prices at 0 post expiry whereas the ITM swaption prices at the value of the swap" (rollfwd lifecycling cell 5). Under gs, the exit cash of `AddTradeAction(swaption, 'expiration_date')` is therefore the exercise value on the expiry date.

**Carry vs roll-down vocabulary in gs.** `RollFwd(date, realise_fwd=True)` rolls with forwards realised (pure carry). `realise_fwd=False` keeps the spot curve (carry + roll-down) (lifecycling cells 2, 4). gs has no *measure* for either; they are scenarios.

---

## 5. Proposed PnlDefinitions

Scaling is given in two columns: **k(gs)** for gs units (decimal levels) and **k(pb)** for pricebt configs following DEV-I7 (levels in bp). Every attribute metric must be scalar (S4).

### 5.1 Swaption (per-instrument; works for payer, receiver, straddle and multi-leg)

```python
from pricebt.backtests.backtest_objects import PnlAttribute, PnlDefinition
from pricebt.common import AggregationLevel
from pricebt.risk import (IRDeltaParallel, IRGammaParallel, IRVegaParallel, IRVolga, Theta,
                          IRFwdRate, IRAnnualImpliedVol, ExpiryInYears)

K = 1.0          # pricebt bp configs; use 1e4 for gs decimal levels
swaption_pnl = PnlDefinition([
    PnlAttribute('delta', IRDeltaParallel, IRFwdRate, K),
    PnlAttribute('gamma', IRGammaParallel, IRFwdRate, K * K, second_order=True),
    PnlAttribute('vega',  IRVegaParallel,  IRAnnualImpliedVol, K),
    PnlAttribute('volga', IRVolga(aggregation_level=AggregationLevel.Type), IRAnnualImpliedVol, K * K, second_order=True),
    PnlAttribute('theta', Theta, ExpiryInYears, -365.0),
])
backtest = GE.run_backtest(strategy, ..., pnl_explain=swaption_pnl)
pd.DataFrame(backtest.pnl_explain())
```

| Attribute | attribute_metric (unit) | market_data_metric (unit) | 2nd order | k(gs) | k(pb) | Notes |
|---|---|---|---|---|---|---|
| delta | `IRDeltaParallel` (ccy/bp) | `IRFwdRate` (dec / bp) | no | 1e4 | 1 | IRDelta is a **curve** delta ("1bp moves in the … instruments used to build the … curve"), while ΔIRFwdRate is the move of the swaption's own forward swap rate. dF/d(parallel) ≈ 1, so curve-shape moves fall into the residual. A model delta dV/dF would be exact but has no gs name. Keep the curve delta, which is what `HedgeAction(IRDelta…)` neutralises. |
| gamma | `IRGammaParallel` (ccy/bp²) | `IRFwdRate` | **yes** | **1e8** | 1 | `½·Γ·ΔF²`. k is applied once (S1), so it is 1e8, not 1e4. |
| vega | `IRVegaParallel` (ccy per bp vol) | `IRAnnualImpliedVol` (dec / bp normal) | no | 1e4 | 1 | Use the instrument's own-strike vol, not `IRAnnualATMImpliedVol`, so that smile and roll-down on the surface are captured. Do **not** use `IRDailyImpliedVol` (unknown annualisation). If a user insists, k = the config's √N. |
| volga | `IRVolga(aggregation_level=Type)` (ccy/bp²) | `IRAnnualImpliedVol` | **yes** | 1e8 | 1 | A bare `IRVolga` resolves to the bucketed form under DESIGN §8.1 rule 5: a `ConfigError` if only a scalar form is mapped, or S4 if a bucketed form is mapped. |
| theta | `Theta` (ccy per calendar day) | `ExpiryInYears` (yrs, ACT/365F) | no | −365 | −365 | ΔExpiryInYears = −Δcalendar_days/365 < 0, so k = −365 turns it into +days × Θ. A Fri→Mon step counts 3 days, which is correct for calendar-day theta and wrong for business-day theta. If Θ is annualised, k = −1. On the exit date ExpiryInYears = 0. |

**Theta consistency rule.** `delta` and `vega` are measured against the instrument's **own** level (the forward swap rate of a fixed-date underlying, and the vol at the instrument's remaining expiry). Θ must therefore be the 1-day PV change **with that own forward and own implied vol held fixed**: forwards realised, the gs `RollFwd(realise_fwd=True)` sense. Otherwise roll-down along the curve or the vol surface is counted twice, once in Θ and once in ΔF or Δσ.

**What falls into the residual:** vanna (ΔF·Δσ), charm (dΔ/dt), veta (dν/dt), third order, curve-shape vs forward mismatch, discounting/annuity drift, and at expiry the exercise discontinuity (the payoff kink is not a smooth Γ).

### 5.2 Bond (no gs precedent: every mapping is a proposal)

**Option B (recommended, uses gs names in their docstring meaning).** The rate level is the curve's matched-maturity par swap rate, and the spread is the bond's par spread to that curve.

```python
bond_pnl = PnlDefinition([
    PnlAttribute('rates',     IRDeltaParallel, IRFwdRate, K),                     # IRFwdRate := par swap rate at bond maturity
    PnlAttribute('spread',    IRDeltaParallel, ParSpread, K),                     # ParSpread := ASW / Z-spread of the bond (bp)
    PnlAttribute('convexity', IRGammaParallel, IRFwdRate, K * K, second_order=True),
    PnlAttribute('carry',     Theta, ExpiryInYears, -365.0),                      # ExpiryInYears := years to maturity
])
```

| Attribute | attribute_metric | market_data_metric | k(gs) / k(pb) | Notes |
|---|---|---|---|---|
| rates | `IRDeltaParallel` = curve DV01 of the bond (ccy per +1bp; **negative for a long bond**) | `IRFwdRate` = matched-maturity par swap rate ("Interest rate par rate", docstring-faithful) | 1e4 / 1 | curve DV01 ≈ yield DV01 for a bullet bond |
| spread | `IRDeltaParallel` (reused as spread DV01 ≈ curve DV01) | `ParSpread` ("Par Spread") | 1e4 / 1 | y ≈ par swap + spread, so rates + spread ≈ yield P&L. The DV01 approximation error goes to the residual. |
| convexity | `IRGammaParallel` (ccy/bp²) | `IRFwdRate` | 1e8 / 1 | spread convexity and cross (rate × spread) terms go to the residual |
| carry | `Theta` = 1-day PV change at a **constant own yield/level** (accrual + pull-to-par) | `ExpiryInYears` = years to maturity | −365 | extending `ExpiryInYears` to non-options needs a DEV id (§6) |

**Option A (yield-based, needs a MUST-2 DEV).** Use `PnlAttribute('dv01', IRDeltaParallel, IRFwdRate := YTM, K)` + `('convexity', IRGammaParallel, IRFwdRate := YTM, K², second_order=True)` + `('carry', Theta, ExpiryInYears, −365)`. This reuses `IRFwdRate` to mean yield-to-maturity, so the DEV text would be "IRFwdRate of a bond asset is its YTM". The alternative is a pricebt-only measure name such as `BondYield`, which gs does not have.

**Bond-specific pitfalls:**
- **Coupons are not booked.** gs books only entry and exit PVs, so "coupons between marks are not booked" (DESIGN §11 "kept on purpose"; no cashflow handling in `ge.py`/`impls.py`). On a coupon date a held bond's PV drops by the coupon and the residual absorbs it. Worse, `Θ × Δt` linearises across the jump. Use a **total-return `npv`** in the config (DESIGN §14), or accept a residual spike.
- **Clean vs dirty.** Θ at constant yield on a dirty PV includes accrual. On a clean price it does not. Pick one and use the same for `Price`.
- **Financing/repo** goes to `Cumulative Cash` via `Strategy(cash_accrual=...)`, not into pnl_explain. It lands in the residual formula as "cash accrual".
- **Roll-down separate from carry** needs a second time-attribute with a different market metric (for example a "rolled level"). No gs name exists, so this is a pricebt extension.

### 5.3 Mixed book (swaption + IRSwap hedge: 040303, 4-Delta Hedging)

- **Every asset held in the book must map every measure in the PnlDefinition.** The engine calcs all `risks` on all positions, and a missing mapping is a `ConfigError` (DESIGN §8.1 rule 3). The swap asset therefore needs `IRVegaParallel → 0`, `IRVolga → 0` (gs: "IRSwap is linear and have it as zero", `23_solve_vanna_&_volga` cell 6), `IRGammaParallel` (small, real), `Theta` (swap carry, real) and `ExpiryInYears` (years to termination).
- For `IRAnnualImpliedVol` the swap can return a constant, for example `0`, **in the same unit as the swaption's** (bp). The `== 0` skip means the value is never read by pnl_explain, but `result_summary` still sums it, and unequal units raise.
- `IRFwdRate` of the swap = its par rate, in bp, the same as the swaption forward. Delta P&L is then per instrument against its own rate. The combined delta attribution is ≈ 0 when hedged, and the curve-shape mismatch between the 10y spot hedge and the 1m10y forward shows up in the residual.

### 5.4 What `PnlAttribute` cannot express

| Term | Why not | Workaround |
|---|---|---|
| **Vanna** `V_{Fσ}·ΔF·Δσ` | One attribute has one market metric, and the second-order branch squares that single Δ | Polarisation: ΔFΔσ = ½[(ΔF+Δσ)² − ΔF² − Δσ²]. Use three second-order attributes with `attribute_metric = IRVanna(aggregation_level=Type)`: (market `S`, k=1), (`IRFwdRate`, k=−1), (`IRAnnualImpliedVol`, k=−1), with k(gs) × 1e8. `S = F_bp + σ_bp` is a **synthetic level with no gs name**. It works only if pricebt lets users build `RiskMeasure(name='…')` and map that name under `risk_measures:`. DESIGN §8.1 looks up by `measure.name`, so **check before promising**. The user must also sum the three output columns. |
| **Charm / veta** (dΔ/dt, dν/dt) | the product of time and market moves | residual |
| Cross-gamma (rate × spread for bonds; multi-curve) | as for vanna | polarisation with a synthetic sum level, same caveat |
| **Carry vs roll-down split** | needs two time sensitivities against different time coordinates | pricebt extension measure |
| **Curve-shape (bucketed) delta P&L** | S4: an attribute metric must be scalar, and a market metric is one level per instrument | per-bucket measures would need one PnlAttribute per bucket with bucket-specific scalar measures (no gs names) |
| Discrete events (coupons, fixings, exercise at expiry) | the linearisation `R(t−1)·Δm` | residual; total-return npv for coupons |
| Business-day theta | ΔExpiryInYears is calendar-based | declare Θ per calendar day |

---

## 6. Design items for pricebt (proposals)

| # | Item | Proposal |
|---|---|---|
| 1 | Missing measure objects in `pricebt.risk` | DESIGN §8.1 lists neither `Theta`, `ExpiryInYears`, `IRVolga`, `IRVanna`, `IRAnnualATMImpliedVol`, `ParSpread` nor `IRGammaParallelLocalCcy`. Add them as generated data with gs classes: `Theta` (568), `ExpiryInYears` (334), `IRAnnualATMImpliedVol` (451), `ParSpread` (538) and `IRGammaParallelLocalCcy` (481) are plain; `IRVolga` (493) and `IRVanna` (487) are FD-param. |
| 2 | **Unit consistency across assets** (config contract) | At registration, or at the first mixed aggregation, fail with a clear `ConfigError` if two assets map the same measure name with different `unit:`. Otherwise `result_summary` raises gs's generic `ValueError` (core.py:590-592). |
| 3 | Intensive time unit | Add `years` (intensive, not FX-convertible) to the closed unit set, or require `scale_with_quantity: false` on `ExpiryInYears`. With the default `number`, theta is scaled by quantity twice. |
| 4 | **DEV-R6 (new): pnl_explain on off-grid exit dates** | gs raises `KeyError` (S3) or drops intervals (S6). With DEV-R1, pricebt attributes them correctly. Record it as a deviation, with S3/S6 as tests. |
| 5 | **DEV (new): `result_ccy` + `pnl_explain`** | gs fails (§3.3). Proposal: apply the same `r(currency=result_ccy)` rewrite to each PnlAttribute's **attribute_metric** when it is currency-parameterised, and leave intensive market metrics un-rewritten. Only currency-bearing measures get the `raiser` (not levels). This interacts with DEV-E15 and DEV-E18. |
| 6 | Semantic extension of `ExpiryInYears` to non-options | Mark as a DEV: "for swaps/bonds, years to final date". Needed so that the swap/bond Theta can be attributed in mixed books. The alternative (Θ = 0 for swaps) loses swap carry. |
| 7 | PnlDefinition helper (optional, pricebt extension) | Hand-written `k` values break when configs change units. A helper `pnl_definition_from_units(...)` could derive k from the declared units (bp vs decimal, bp²), but it is not gs API. **Default: document the k tables (§5) in `ASSET_CONFIG_GUIDE.md` instead.** |
| 8 | Swaption asset config checklist | §2.7. Also: market metrics must be **finite on the exit date**, including an expiring swaption's `IRFwdRate`/`IRAnnualImpliedVol` on its expiry date (use the last available expiry or a zero-time limit), because vega(t−1) ≠ 0 forces a read (S-type NaN poisoning). Non-applicable attribute metrics should be **0**, not NaN. |
| 9 | 040307 conversion | Delete `raw_data[IRFwdRate] *= 1e4` (bp config), or keep IRFwdRate decimal for this asset (but see item 2). `iloc[5, 1]` becomes deterministic under DEV-E14. |

---

## 7. Notebook defects found here (additions to R05§8)

| Notebook | Defect |
|---|---|
| 3-Systematic Selling cells 21, 23 | `backtest.result_summary['Cash']`: no such column (only `'Cumulative Cash'`, bo.py:94), so a KeyError |
| 3-Systematic Selling cells 22-23 | `backtest.result_summary[DollarPrice]`: never requested (default `price_measure=Price`, ge.py:89), so a KeyError |
| 4-Delta Hedging cell 23 | `result_summary['Cash']`, KeyError, as above |
| 4-Delta Hedging cell 13 | `row.iteritems()` was removed in pandas 2.x (manual part) |
| 3-SS cell 12 | `fillna(method='ffill')` is deprecated in pandas 2.x (manual part) |
| Backtesting cell 15 (markdown) | `backtest.results_summary` typo (the attribute is `result_summary`) |
| 040307 cell 4 | `floating_rate_option='USD-LIBOR-BBA'`: LIBOR has ceased |
| 040307 cell 8 | `iloc[5, 1]` is positional on set-ordered columns (R05 already notes this). It reads the delta at the **6th** date as a constant proxy for the whole history. |
| 040305 cell 3 | `'=solvefor(25e3,bp)'` needs the server-side solver |
| `fx_pnl_definition` docstring (bo.py:1000) | refers to `GenericEngineBasicBacktestRunner`, which is not in the repo |

---

## 8. Open questions

1. Do the shipped configs return levels in bp (DEV-I7) for **all** assets, including swaptions? The answer fixes every k in §5 and the 040307 `*1e4` line. Mixed units are impossible (§6 item 2).
2. Should a swap's (or bond's) `ExpiryInYears` mean years to final date (a DEV), or should mixed books drop swap theta?
3. Is the bond decomposition Option B (par swap + ParSpread, gs names) or Option A (YTM, a DEV)?
4. May users construct custom `RiskMeasure(name=...)` objects that configs can map, for vanna polarisation and a roll-down level? DESIGN §8.1 does not say.
5. Should the gs S3/S6 behaviour be replicated for strict parity, or corrected via DEV-R1/DEV-R6? The recommendation is to correct it.
6. Should the swaption config support `'=solvefor(…)'` (040305) with a documented target semantics, or should the converted notebook use a numeric strike?
7. How is `Straddle` priced (payer + receiver legs inside one trade)? And is the post-expiry value physical (swap PV) or cash?
