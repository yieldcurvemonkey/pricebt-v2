# gs notebook map: swaption backtests, portfolios, pricing and risk (rates)

Paths are under the gs_quant repository's `documentation/` (2.1.17); the `made_with_gs_quant`
notebooks are under its `content/`. "Ports" means the code runs on pricebt after the three edits
(imports, session, datasets) and the config work named in the row. Evidence:
`docs/v2/research/15-gs-backtest-notebooks-swaption-bond.md` (swaption backtests),
`docs/v2/research/10-gs-portfolio-and-results.md` (portfolios) and
`docs/v2/research/11-gs-instruments-swaption-bond-contexts.md` (instruments and contexts).

## Units first: gs returns decimals

gs's `IRFwdRate`, `IRSpotRate`, `IRAnnualImpliedVol` and `IRAnnualATMImpliedVol` come back as
**decimals** (0.0385, 0.0080), although their docstrings say "(in percent)". gs notebooks
therefore multiply by `1e4` or `10000` to show bp. pricebt returns whatever unit the asset config
declares (DEV-I7), and the shipped and toy configs declare **bp**. Under a bp config, **delete**
every such line:

| Notebook | Line to delete (bp config) |
|---|---|
| 040307 cell 7 | `raw_data[IRFwdRate] *= 1e4` |
| `02_pricing_and_risk/00_instruments_and_measures/tutorials/Measures.ipynb` cell 13 | the `* 10000` in `swaption.calc(risk.IRAnnualImpliedVol) * 10000` |
| 030006 cell 4 | the `* 10000` in `results.to_frame('value', 'portfolio_name_0', 'instrument_name') * 10000` |
| `01_rates/15_spread_option_grid_pricing` | `vols = result_v.to_frame() * 10000` (the class is not ported anyway) |

A gs-written `PnlAttribute` scaling factor is off by 1e4 (1e8 for a second-order term) against bp
configs. Use `ir_pnl_definition(rate_unit=..., vol_unit=...)`, which derives the factors from the
declared units and checks them (DEV-E21). gs documents `IRDailyImpliedVol` in basis points, and
040307 does not rescale it: keep it as it is under a bp config.

## Swaption backtests (`04_backtesting`)

| Notebook | What it uses | Port notes |
|---|---|---|
| `examples/03_GenericEngine/040303_strategy_delta_hedge_Rates` | monthly short **EUR** 1m30y swaption held to expiry, swap delta hedge, `IRDelta(Type, 'USD')`, `result_ccy` | needs an EUR swaption config and an EUR swap config (`match: {notional_currency: EUR}`), an FX config, and `run_backtest(..., result_ccy='USD')`. `buy_sell='Sell'` is folded by the config. The hedge sets a swaption's forward-rate delta against a swap's par-rate delta: approximate, not exact (R2-2) |
| `examples/03_GenericEngine/040305_strategy_exit_trade_action` | USD 6m10y short, `strike='=solvefor(25e3,bp)'`, a risk-trigger roll on `IRDelta(Type, 'USD')`, `ExitTradeAction` | the strike is GS server grammar (DEV-I6): use a numeric or `'ATM+x'` strike, or implement the solve in the config's `resolve`. The rest ports |
| `examples/03_GenericEngine/040307_strategy_seagull_bullish` | 3-leg USD 1y10y seagull (`'A±N'` strikes, `'Receiver'`), weekly ramp; after the run, `HistoricalPricingContext` + `calc([IRFwdRate, IRDailyImpliedVol])` and `DollarPrice` | delete `*= 1e4`; replace `iloc[5, 1]` by the `IRDelta(...)` column key; map or reject `floating_rate_option='USD-LIBOR-BBA'` in the config; `csa_term='USD-1'` reaches the config as `pricebt_csa`. The post-run seagull is unresolved, so each date re-strikes a fresh seagull (as in gs) |
| `tutorials/Backtesting` | daily short USD 1m10y `PayReceive.Straddle`, `buy_sell='Sell'`, held to expiry | the config must price `Straddle` (payer + receiver legs, as the swaption template does) or reject it at resolve |
| `made_with_gs_quant/3-Systematic Selling`, `4-Delta Hedging` | the same straddle; `DollarPrice`; `IRDeltaParallel`; an `'ATMF'` swap hedge with `IRDelta(Type, 'local')` | the swap config's `resolve` must accept `'ATMF'` (the templates do); `IRDeltaParallel` resolves to the `IRDelta` scalar |

No gs notebook backtests a `Bond`, and none uses the backtest `pnl_explain`. For the decomposition
of a swaption or bond backtest, use `swaption_pnl_definition` / `bond_pnl_definition` and
`BackTest.pnl_explain_table()` (worked in `tests/test_pnl_ir.py`).

## Portfolios (`03_portfolios`)

A worked, executed port of every code cell is `tests/test_portfolio_notebooks.py` (transcribed onto
the toy assets; its docstring lists each skipped cell with the reason).

| Notebook | Status on pricebt | Notes |
|---|---|---|
| 030000 create_portfolio | ports | |
| 030001 modify_instruments | ports | `portfolio.pricables = ...` is a typo in gs's own notebook: a silent no-op in both libraries |
| 030002 extracting_instruments_and_results | ports | `'ATMF'`/`'ATMF+50'` strikes must parse in your config; historical `result[Price].aggregate()` is a date-indexed series, `hist_results[IRDelta][0]` a date-indexed ladder |
| 030003 resolve_portfolio | ports | `strike='atm+50'` is parsed by the config (DEV-I6); `as_dict()` also shows `quantity_` (DEV-I2) |
| 030004 price_portfolio, 030005 calculate_portfolio_risk | ports | `DollarPrice` is `Price(currency='USD')` |
| 030006 portfolio_grid_calc | ports | nested portfolios pivot on `portfolio_name_0`; delete `* 10000` under bp configs; the seaborn cell is plotting only |
| 030007 pnl_explain | ports | `PnlExplain(CloseMarket(date=to_date))` needs a `PnlExplain:` mapping to a buckets portfolio function that names `market_to` (cookbook pattern 22). `PricingContext(pricing_date=from, market=CloseMarket(date=to))` prices on `to`'s market. `from gs_quant.datetime.date import business_day_offset` becomes `from pricebt.datetime import business_day_offset`. `PnlExplain` has no currency parameter, so a book in two currencies cannot aggregate its rows |
| 030008 portfolio_from_frame | ports | `Portfolio.from_frame(data, mappings)`, `to_frame()` |
| 030009 portfolio_risk_result_to_frame | ports, with two deliberate differences | pricebt labels each leaf from its own path (DEV-R6), so where gs mislabels a level that mixes leaves and sub-portfolios, pricebt is right and differs from gs's printed output. A swap's `IRVegaParallel` is `0.0` (R2-8), not gs's empty frame, so swap rows stay. `DisplayOptions` comes from `pricebt.config` |
| 030010 portfolio_inter_leg_dependencies | does not port | `strike="=[foo].strike + 5bp"` is resolved on the GS server; a config resolves one instrument at a time, so it fails loudly. Set explicit strikes |
| 030011 portfolio_from_csv | ports with parsing mappers | the GS server parses `'27-Jan-15'` dates and `'50,000,000'` notionals; add mappers that parse them (DEV-I6) |
| `tutorials/Portfolios` | ports | `PricingContext.current = ...` (the setter) works |

Server-only `Portfolio` members raise `NotSupportedError` ("... is GS server-side (portfolio
persistence) ..."): `get`, `from_portfolio_id`, `from_portfolio_name`, `from_quote`,
`from_asset_id`, `from_asset_name`, `from_book`, `from_eti`, `save`, `save_as_quote`,
`save_to_shadowbook` and `market()`. `Portfolio.id` and `quote_id` are always `None`. Build
portfolios in memory, or with `from_frame` / `from_csv`.

## Pricing and risk, rates (`02_pricing_and_risk/00_instruments_and_measures/examples/01_rates`)

| Notebook | Status | Notes |
|---|---|---|
| 01-08 (swap definition, construction, price, risk, contexts, historical) | ports | 02's `fixed_rate='10000/pv'` is server grammar: replace it or implement it in `resolve`. `HistoricalPricingContext` gives date-indexed results |
| 09_swaption_trade_construction | ports, partly | `strike='10000/pv'`, `strike='25d'` and a non-zero `premium` are server grammar or unsupported: replace them. Printing `swaption.strike * 100` shows percent from the resolved decimal (fine) |
| 10_straddle_price | ports | `pay_or_receive='Straddle'` = payer + receiver (the swaption template's legs) |
| 11_midcurve_swaption_price | ports if your config pins a forward-starting underlying | |
| 12_swap_future_cashflows | ports | `Cashflows` is a table (`DataFrameWithInfo`), empty for a total-return swap config (cookbook pattern 23) |
| 13, 14, 17 (cross-currency swaps) | does not port | `IRXccySwap*` classes are not generated in pricebt |
| 15_spread_option_grid_pricing | does not port | `IRCMSSpreadOption` is not generated |
| 16_change_discount_curve | ports | `PricingContext(csa_term=...)` reaches the config as `pricebt_csa` (cookbook pattern 10) |
| 18_solve_present_value, 19_solve_delta | the solve does not port | `'=solvefor([name].risk.X, ...)'` cross-trade solves are server-side: compute the strike or notional yourself (e.g. notional = the payer's delta / the swap's delta per unit) and pass numbers |
| 20_fix_float_legs_price | does not port | `IRFixedLeg`, `IRFloatLeg` are not generated |
| 21_asset_swap_definition | does not port | `IRAssetSwapFxdFlt` is not generated (`Bond` is, and needs a Bond config) |
| 22_cap_floor | does not port | `IRCap`, `IRFloor`, `IRCapFloor` are later work |
| 23_solve_vanna_&_volga | ports except the solve | `port.calc(IRVanna(aggregation_level='Type'))` and `IRVolga` port (DEV-I9: both are finite-difference measures); the `notional_amount='=solvefor(...)'` must become a number |
