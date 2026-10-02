---
name: pricebt-architecture
description: The mental model of pricebt v2 — a gs_quant.backtests port whose every number comes from YAML asset configs — plus the rules you must never break and the engine semantics that change results. Read before touching pricebt code, writing an asset config, or interpreting a backtest.
---

# pricebt architecture and rules

pricebt is an event-driven backtester whose public API is a near 1:1 port of the open-source `gs_quant.backtests` (Strategy, Triggers, Actions, `GenericEngine`, `BackTest`). **It contains no pricing and no market data.** Every market object, resolved trade, PV, risk number and FX rate comes from a **YAML asset config**: one file per tradable asset, whose Python expression strings call *your* pricing and data library. Nothing else in the repository knows that library exists.

## When to use / not use

- **Use** before your first change to anything in this repository, or when a backtest number surprises you.
- **Do not use** as a how-to. For the procedures, go to [`pricebt-start-here`](../pricebt-start-here/SKILL.md).

## The picture

```
your notebook / script  (gs_quant-style code: Strategy, triggers, actions, GenericEngine)
        │  PricebtSession.use(assets=["configs/assets/<asset>.yaml"], fx=...)
        ▼
src/pricebt/backtests/*      ← port of gs_quant 2.1.17 backtests (engine, triggers, actions, results)
        ▼  talks only to gs-shaped local objects
src/pricebt/markets, instrument, risk   (PricingContext, CloseMarket, Portfolio, IRSwap/IRSwaption/Bond, PortfolioRiskResult)
        ▼  every value request goes to ONE place
src/pricebt/assets/pricing.py: PricingService  (caches, FX, measure → function mapping, units, parameters, frames)
        ▼  eval() of the strings in the asset config  (loaded by assets/config.py, checked against risk/contracts.py)
YOUR LIBRARY (Citi-DP-style, Athena-style, in-house, QuantLib...)  ← reached ONLY via the config's imports/code
```

## Rules you must never break

| Rule | What it means | Enforced by |
|---|---|---|
| **MUST-1: no library in core** | Nothing under `src/pricebt/` may import, name or assume the shape of any pricing or market-data library. Your library lives only in `configs/assets/*.yaml` (and your own modules those configs import). | `tests/guards/` (import scan, token scan, import blocker) |
| **MUST-2: gs parity** | Module paths, class names, constructor signatures, result shapes and engine semantics match gs_quant. Every intentional difference has a `DEV-*` id. | `tests/test_gs_api_parity.py`, [`docs/v2/DEVIATIONS.md`](../../docs/v2/DEVIATIONS.md) |
| **MUST-3: one config per asset** | pricebt never interprets instrument kwargs; the config's `resolve` does. | the asset config contract, [`docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md) |
| **MUST-4: currencies and bp** | Risk is in currency per 1bp (`ccy_per_bp`; second order `ccy_per_bp2`); rates and normal vols are levels in the unit the config declares (`bp` recommended), the same across a book. Conversions happen only through an FX config. | `tests/test_multi_currency.py`, `tests/test_contracts.py` |
| **MUST-5: no asset special cases** | A new asset class (swaptions, futures, FX) is a new config, never engine code. | `tests/guards/test_asset_agnostic_scan.py` |

**If a library does not fit, change the config, never `src/pricebt`.** A pull request that edits core to make one library work is wrong by construction.

## Engine semantics that change your numbers

Read these before interpreting any result. They are gs behaviour, kept on purpose unless marked DEV.

1. **Signal and execution happen on the same close.** A trigger that fires on date `d` trades at `d`'s close prices. There is no next-day fill. If your signal uses `d`'s close, you are assuming you can trade at the price you observed; say so in the report.
2. **Holding window.** A trade is held for `create_date <= s < final_date`. Entry books cash `-PV(create)`; exit books `+PV(final)`. On its exit date the trade is out of the book and its value is in cash.
3. **Coupons paid between marks are not booked as cash.** gs has the same property. A coupon that pays while the trade is held drops out of `Price` without reaching cash. For a bond it is the main carry term, not a footnote. There are two remedies. A config whose `npv` is total return keeps paid coupons in `Price`. Otherwise the `Cashflows` measure lists the flows, and `BackTest.pnl_explain_table()` reports them as `cashflow_pnl`. Flag it in every report ([`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md)).
4. **Scaling is a quantity multiplier (DEV-I1).** `quantity_` multiplies extensive measures (`ccy`, `ccy_per_bp`, `ccy_per_bp2`) and a frame's `scale_columns`. Intensive measures (`bp`, `pct`, `decimal`) are never scaled, so a rate stays a rate. A short position is `quantity_ < 0`. A sold option is instead the config's `resolve` folding `buy_sell` into a signed notional.
5. **`resolve` pins the trade at its trade date.** `'10y'` becomes an absolute maturity and `'ATM'` becomes a number. A seasoned trade keeps those terms forever; a config that does not pin silently rolls the trade every day.
6. **Missing market data (DEV-E16).** With the default `missing_market='drop'`, grid dates with no market are removed and listed on `backtest.missing_market_dates`. Exits that land on such dates are rolled to the next available date (`backtest.missing_market_moves`).
7. **Mean-reversion "exits" are offsetting trades held forever** (gs `MeanReversionTrigger` + `AddTradeAction` with no duration). The ledger shows them all as `open`.
8. **Hedges on one date are sized against the pre-hedge book risk.** Several `HedgeAction`s firing on the same date do not see each other.
9. **Defaults are frictionless.** Transaction cost is `ConstantTransactionModel(0)`, there is no cash accrual, and `initial_value` is 0. `result_summary['Total']` is therefore cumulative P&L, not NAV.
10. **Currencies.** `Price` is in the asset's local currency. A mixed-currency book needs `run_backtest(result_ccy=...)` plus an FX config, or it raises gs's error.

## The measure layer (IR pricing and risk)

1. **Catalogue as data.** `pricebt.risk` holds every gs 2.1.17 measure and preset, with gs names and classes. It computes nothing: a config maps each measure you use to a function.
2. **Contracts at load (DEV-I11 amended, DEV-I19).** Every config of a class with a contract (`src/pricebt/risk/contracts.py`) answers every measure and form of it. The strict classes (`contracts.STRICT_CLASSES`: `IRSwap`, `IRSwaption`; `docs/v2/IR_STRICT_CONTRACT.md`) must **map** each one, the R3-1 rows (`ParSpread`, `FairPremium`, `ForwardPrice`, `PremiumCents`, `LocalAnnuityInCents`, `CompoundedFixedRate`, `CRIFIRCurve`, `PnlExplain`) included: a declaration is itself a load error, and `load_asset` lists every gap and ends with a paste-ready mapping skeleton (`contracts.mapping_skeleton`, stubs that do not compile until written). A `Bond` may map or declare: its error prints an `unsupported_measures:` block, and a declared measure raises `UnsupportedMeasureError` when requested. A literal `0.0` is honest only for `contracts.ZERO_BY_CONVENTION`.
3. **Contract semantics.** Sensitivities are with respect to the instrument's **own rate** (`IRFwdRate`: par rate, forward swap rate, yield; DEV-I12) and the normal vol. `Theta` is per calendar day (DEV-I15).
4. **Parameters pass through (DEV-I10).** `bump_size`, `finite_difference_method`, `local_curve` and `scale_factor` reach a function as `pricebt_<name>`. They are refused (raise) if the function does not name them, and they are part of every cache key.
5. **Frames.** A `functions:` entry with `returns: frame` produces a table measure (`Cashflows`). Tables stay out of `result_summary`/`risk_summary` (DEV-R11).
6. **Portfolio and results parity.** Nested `Portfolio` paths, `PortfolioRiskResult` indexing, `aggregate`, `to_frame` and historical shapes follow gs, plus the `pricebt.risk.core` helpers. `PricingContext(market=CloseMarket(date=t))` swaps in another date's market (DEV-M1), and `PnlExplain` is a relative measure (DEV-M2).
7. **P&L decomposition.** `PnlDefinition`/`PnlAttribute`, with a cross metric for vanna (DEV-E19) and a unit check (DEV-E21). `ir_pnl_definition`, `swaption_pnl_definition` and `bond_pnl_definition` build IR definitions. `BackTest.pnl_explain()` gives cumulative attribution; `pnl_explain_table()` gives per-step actual, cashflow, attributed and residual.

Details: [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md) and [`pricebt-pnl-attribution`](../pricebt-pnl-attribution/SKILL.md).

## Where things live

| Path | What |
|---|---|
| `src/pricebt/backtests/` | engine, triggers, actions, `BackTest` results (the gs port) |
| `src/pricebt/assets/` | asset-config loader, namespace, `PricingService`, FX |
| `src/pricebt/instrument/` | gs instrument classes (`IRSwap`, `IRSwaption`, `Bond`, …) with real gs signatures, generated from `_gs_fields.py` |
| `src/pricebt/risk/` | the measure catalogue (`Price`, `IRDelta`, `IRDeltaParallel`, …), `PnlExplain`, result objects (`results.py`), helpers (`core.py`) |
| `src/pricebt/risk/contracts.py` | the per-instrument measure contracts checked at load |
| `src/pricebt/markets/` | `PricingContext`, `HistoricalPricingContext`, `CloseMarket`; `portfolio.py`: `Portfolio`, `Grid` |
| `src/pricebt/backtests/backtest_objects.py` | `BackTest` views, `PnlDefinition`, `ir_pnl_definition`, `pnl_explain_table` |
| `configs/assets/` | real asset configs (one per asset) |
| `tests/assets/`, `tests/toylib/` | deterministic toy assets for tests and learning |
| [`docs/v2/DESIGN.md`](../../docs/v2/DESIGN.md) | the full contract; §4 is the asset config, §8 risk/results, §11 deviations |
| [`docs/v2/IR_RISK_DESIGN.md`](../../docs/v2/IR_RISK_DESIGN.md) | IR measures, contracts, portfolio parity, P&L decomposition (§00 overrides the rest) |
| `skills/` | this library |

## Glossary

- **Asset config**: the YAML file that prices one asset ([`docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md)).
- **Market**: whatever the config's `market.expr` returns for a date (a curve handle, a snapshot object, …). `None` means no data.
- **Resolve / resolved terms**: the plain-data dict that pins an instrument at its trade date.
- **Trade object**: the library's own representation, built from the resolved terms.
- **Unit value**: a function evaluated for quantity 1; pricebt applies `quantity_` and FX.
- **Extensive / intensive**: scales with position size / does not.
- **Measure contract**: the measures an `IRSwap` or `IRSwaption` config must map (strict classes), or a `Bond` config must map or declare under `unsupported_measures:`.
- **Own rate**: the instrument's `IRFwdRate`, which the `IRDelta` scalar and `IRGammaParallel` differentiate against.
- **`PricingService`**: the single producer of values (`session.pricing`); use it for spot checks.

## Related skills

- [`pricebt-start-here`](../pricebt-start-here/SKILL.md): which skill to use.
- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): write your first asset config.
- [`pricebt-risk-measures`](../pricebt-risk-measures/SKILL.md): what each IR measure must mean, and how your library provides it.
- [`pricebt-strategy-workflow`](../pricebt-strategy-workflow/SKILL.md): from idea to tearsheet.
