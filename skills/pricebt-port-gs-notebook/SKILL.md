---
name: pricebt-port-gs-notebook
description: Port gs_quant code to pricebt - GenericEngine backtests (triggers, actions, result_summary, trade_ledger, including the swaption notebooks 040303/040305/040307 and the Backtesting tutorial), Portfolio and PortfolioRiskResult notebooks (030000-030011 - calc, aggregate, to_frame, historical contexts, PnlExplain with CloseMarket) and the rates pricing-and-risk examples - with the three edits (imports, session, datasets) plus the checks for asset configs and the measure contract, gs decimal vs pricebt bp units (delete the '* 1e4' lines), Straddle and buy_sell, server-only grammar and methods that raise, and the documented behavioural deviations. Use when a user has existing gs_quant code, or when an idea is easiest to express by adapting a gs example notebook.
---

# Port gs_quant code to pricebt

pricebt's public API mirrors `gs_quant` (1.5.4 signatures, 2.1.17 behaviour) for backtests, pricing
and risk, portfolios and results. Code that uses only the GenericEngine, `Portfolio`,
`PricingContext` and the gs risk measures ports with three edits. The rest of this skill covers what
does *not* port by itself: missing asset configs, units, server-side grammar, and the documented
deviations. The per-notebook table is in [`references/notebook-map.md`](references/notebook-map.md).

## When to use / not use

- **Use** for gs_quant notebooks or scripts built on `GenericEngine`, `Portfolio` /
  `PortfolioRiskResult`, `PricingContext` / `HistoricalPricingContext`, and the IR measures
  (swaps, swaptions, bonds).
- **Not for** `EquityVolEngine`, `PredefinedAssetEngine` or `StrategySystematic`. These are GS
  server-side only and raise `NotSupportedError` in pricebt; rewrite them against `GenericEngine`.
  The same goes for cross-currency swaps, caps and floors, CMS spread options and fixed or float
  legs: pricebt does not generate those classes.

## Inputs and outputs

- **Inputs:** the gs code, and an asset config for every instrument class and currency it uses
  (or the toy configs in `tests/assets/` for a dry run).
- **Outputs:** the ported code, running top to bottom with no `gs_quant` import, and a list of the
  places where its numbers differ from gs's printed output, each with its reason.

## Procedure

1. **Imports.** Replace `gs_quant` with `pricebt` in every import. Module paths and class names are
   identical, for example `from pricebt.backtests.triggers import ...`,
   `from pricebt.instrument import IRSwap, IRSwaption, Bond`,
   `from pricebt.risk import Price, IRDelta, IRVega, PnlExplain`,
   `from pricebt.markets import PricingContext, HistoricalPricingContext, CloseMarket, close_market_date`,
   `from pricebt.markets.portfolio import Portfolio`, and
   `from pricebt.backtests.backtest_objects import PnlDefinition, PnlAttribute, swaption_pnl_definition`.
   Two exceptions: `gs_quant.datetime.date.business_day_offset` becomes
   `from pricebt.datetime import business_day_offset`, and `DisplayOptions` comes from `pricebt.config`.
2. **Session.** Replace `GsSession.use(...)` with
   `PricebtSession.use(assets=[<one asset config per instrument type and currency>], fx=<fx config if several currencies>)`.
   `GsSession.use` still exists as a harmless no-op, but it prices nothing.
3. **Datasets.** Replace `Dataset(...).get_data(...)` with your own `pandas.Series` (indexed by
   `datetime.date`), or with `pricebt.data.measure_series(instrument, "<function>", start, end, frequency="1b")`
   for a series from the pricing library (e.g. a par-rate history). Build series on the full
   business-day schedule, so `GenericDataSource` never has to fill gaps.
4. **Instruments need asset configs.** Each instrument class and currency must match exactly one
   registered asset (`match:` in the config). `IRSwap`, `IRSwaption` and `Bond` configs also carry
   the **measure contract**: every contract measure is mapped (IRSwap, IRSwaption: always; a Bond may
   declare one unsupported with a reason). The contract is not every gs IR measure: the ones left out
   on purpose, each with its reason, are `pricebt.risk.contracts.EXCLUDED` (FX quoting, inflation,
   live-market and server-metadata measures, ...). No
   config yet? Go to [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md);
   its three templates cover these classes. 040303 trades an EUR swaption, so it needs an EUR
   swaption config.
5. **gs-only kwarg grammar.** The GS server parses these; pricebt leaves them to the config's
   `resolve` (DEV-I6). `'ATM+25'`, `'A-50'` and `'ATMF'` work if the config parses them (the
   templates do). `'=solvefor(...)'` (040305, `01_rates/18`, `19`, `23`), `'10000/pv'`, `'25d'`,
   cross-leg references such as `"=[foo].strike + 5bp"` (030010), a non-zero `premium`, and
   `notional_amount='100k'` all raise: replace them with numbers, or implement them in `resolve`.
6. **Risk measures map 1:1.** `pricebt.risk` has every gs measure and preset under the same name.
   A request for a measure a Bond config declares unsupported raises `UnsupportedMeasureError` with
   the config's reason, where gs would return an `UnsupportedValue` (a swap or swaption config maps
   every contract measure). `aggregation_level` Type, Asset or
   Class returns a float. A bare finite-difference measure (`IRDelta`, `IRVega`) returns the
   bucketed frame. `IRVanna` and `IRVolga` are finite-difference measures too, so request
   `IRVanna(aggregation_level='Type')` (DEV-I9). `bump_size`, `finite_difference_method`,
   `scale_factor` and `local_curve` reach the config function only if its expression names
   `pricebt_<parameter>`; otherwise they raise `NotSupportedError` (DEV-I10).
   `mkt_marking_options` always raises.
7. **Units: delete the `* 1e4` lines.** gs returns `IRFwdRate`, `IRSpotRate` and the annual implied
   vols as **decimals**, so its notebooks multiply by `1e4` or `10000`. pricebt returns the config's
   declared unit, and the shipped and toy configs use **bp** (DEV-I7). Delete `raw_data[IRFwdRate] *= 1e4`
   (040307) and the `* 10000` on implied vols (030006, the Measures tutorial). A hand-written gs
   `PnlAttribute` scaling factor is 1e4 too big (1e8 for gamma) against bp configs: use
   `ir_pnl_definition(rate_unit='bp', vol_unit='bp')` instead.
8. **Swaptions: `Straddle`, `buy_sell`, server defaults.** pricebt never reads `buy_sell` itself: the
   config folds it into the signed notional, so `Sell` is exactly `-Buy`. `PayReceive.Straddle`
   (the Backtesting tutorial) prices only if the config prices payer + receiver. Unset kwargs come
   from the config's `defaults:`, not from the GS server. gs defaults a swaption to a `Straddle`
   with a `'10y'` expiry, so a notebook that relies on those defaults needs the same values in the
   config. `AddTradeAction(option, 'expiration_date')` needs the config's
   `attributes: {expiration_date: ...}`.
9. **Contexts.** `PricingContext(pricing_date=...)`, `HistoricalPricingContext(start, end)` and the
   `PricingContext.current = ...` setter port. `PricingContext(market=...)` accepts only a
   `CloseMarket(date=...)`, which prices on that date's market while the pricing date stays put
   (DEV-M1). Any other market raises `NotSupportedError`; it used to be ignored silently.
10. **Portfolios and results.** `Portfolio` construction, nesting, `resolve`, `calc`, `price`,
    `dollar_price`, `paths`, `subset`, `from_frame`, `from_csv`, `to_csv` and `Grid` port, and so do
    `PortfolioRiskResult` indexing (by measure, name, instrument, path, date), `aggregate()`, `+`,
    `* k` and `to_frame(values, index, columns, aggfunc, display_options)`. Server-only members
    (`get`, `save`, `from_portfolio_id`, `from_book`, `market()`, ...) raise
    `NotSupportedError`. Build portfolios in memory.
11. **P&L explain.** gs 030007's `PnlExplain(CloseMarket(date=to_date))` ports if the config maps
    `PnlExplain` to a buckets portfolio function that names `market_to`
    ([`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md) pattern 22). No
    gs backtest notebook uses `BackTest.pnl_explain`. For a swaption or bond backtest decomposition,
    use `swaption_pnl_definition()` or `bond_pnl_definition()` with `run_backtest(..., pnl_explain=...)`,
    and `BackTest.pnl_explain_table()` for the per-step actual, explained and residual P&L.
12. **Currencies.** A mixed-currency book needs `run_backtest(..., result_ccy='USD')` plus an FX
    config. Without them, pricebt raises gs's own error, with a hint.
13. **Run and compare.** Result objects are the gs ones: `result_summary` (a property),
    `trade_ledger()` and `strategy_as_time_series()`. The pricebt additions are `summary_stats()`,
    `pnl_bps(risk)`, `missing_market_dates` and `pnl_explain_table()`.

## Behavioural differences to expect

The full list, with a test for each, is in [`docs/v2/DEVIATIONS.md`](../../docs/v2/DEVIATIONS.md). The
ones users notice:

- **`MeanReversionTrigger` works** (the installed gs 1.5.4 crashes on every fire; pricebt uses the
  2.1.17 fix).
- **`result_summary` is correct on flat dates.** gs double-counts an exited trade's PV there (DEV-R1).
- **Trigger state resets at the start of every run.** gs carries it across runs (DEV-T10).
- **Missing market dates are dropped with a warning.** gs prices every weekday on server data (DEV-E16).
- **Scaling is a signed `quantity_`,** not an edit of `notional_amount` / `pay_or_receive` (DEV-I1).
  The ledger names and cash are the same. pricebt also scales a `Bond`, which gs cannot.
- **`initial_value` is seeded on the first grid date** (DEV-E11).
- **The `IRDelta` scalar is the own-rate delta** (swap par rate, swaption forward, bond yield;
  DEV-I12), and `Theta` is per calendar day (DEV-I15). `ExpiryInYears` is defined for swaps and
  bonds too (DEV-I17).
- **A swap's vega is `0.0`**, where gs returns an empty frame (R2-8). An inapplicable measure a Bond
  config declares raises `UnsupportedMeasureError`, where gs returns an `UnsupportedValue` (DEV-I11;
  IRSwap and IRSwaption configs cannot declare).
- **`to_frame` labels each leaf from its own path** (DEV-R6). Where gs mislabels a level that mixes
  leaves and sub-portfolios (030009), pricebt differs from gs's printed output and is right.
- **Table measures** (`Cashflows`) are left out of `result_summary`, `risk_summary` and
  `to_frame(values='value')`, and stay in `backtest.results` (DEV-R11).

## Known defects in gs's own example notebooks

Fix these while porting (from `docs/v2/research/05-notebook-coverage.md` §8 and the notebook map):

- Some notebooks use `PricingContext` without importing it. Add `from pricebt.markets import PricingContext`.
- 040310's title belongs to the inflation notebook.
- 040307 indexes `result_summary` columns by position (`iloc[5, 1]`). Use the RiskMeasure key instead.
- 040311 passes `holiday_calendar` as a tuple. That works; a list is also accepted (DEV-T16).
- 030001 and the Portfolios tutorial assign `portfolio.pricables` (a typo): a silent no-op in both
  libraries.

## Checks

- The notebook runs top to bottom on a toy or real asset, with no `gs_quant` import
  (`python -c "import sys; ...; assert 'gs_quant' not in sys.modules"` after running).
- The ledger's trade names and dates match the gs logic (the `Action<N>_<name>_<date>` naming is
  preserved).
- No `* 1e4` / `* 10000` remains on a rate or vol level from a bp config, and every measure the code
  requests is mapped (not declared) in the configs.
- Each difference from gs's printed output is explained by a row in `docs/v2/DEVIATIONS.md` or in
  the notebook map.

## Pitfalls

- **A config default that differs from gs's server default** (`pay_or_receive`, `expiration_date`,
  `strike`) silently prices a different trade. Set the kwargs explicitly in the ported code.
- **Hedging a swaption's delta with swaps** sums own-rate deltas quoted on different rates
  (forward vs par): approximate, not exact (R2-2).
- **A gs `PnlDefinition` copied verbatim** has gs's decimal scaling factors: 1e4 wrong against bp
  configs.
- **Server markets** (`LiveMarket`, `OverlayMarket`, `RelativeMarket`) do not exist in pricebt, and
  any market object other than a `CloseMarket` raises in `PricingContext(market=...)`. Scenarios
  (`RollFwd`, `CurveScenario`, `MarketDataShockBasedScenario`) are later work.

## Worked examples in this repository

- gs 040304 (mean reversion) → `notebooks/src/040304_mean_reversion_toy.py` (toy) and
  `notebooks/src/040304_mean_reversion_usd_sofr_arbs.py` (a real bank library).
- gs 030000-030011 and the Portfolios tutorial, every code cell on toy assets:
  `tests/test_portfolio_notebooks.py` (030007's `CloseMarket` and `PnlExplain` included).
- A swaption `GenericEngine` backtest (`buy_sell`, `'A-50'`, expiry exits): `tests/test_engine_swaption.py`.
- Swaption and bond P&L decomposition with `swaption_pnl_definition` / `bond_pnl_definition`:
  `tests/test_pnl_ir.py`.
- The notebook-by-notebook porting tables: [`references/notebook-map.md`](references/notebook-map.md)
  and `docs/v2/research/05-notebook-coverage.md` §4.

## Related skills

- [`pricebt-architecture`](../pricebt-architecture/SKILL.md) (semantics), [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md) (missing configs), [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md) (measure recipes, error messages), [`pricebt-strategy-workflow`](../pricebt-strategy-workflow/SKILL.md) (turn the port into a reviewed study), [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md) (the measures and their units), [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md) (`pnl_explain` and `PnlExplain`).
