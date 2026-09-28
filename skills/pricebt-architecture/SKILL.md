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
src/pricebt/markets, instrument, risk   (PricingContext, Portfolio, IRSwap..., PortfolioRiskResult)
        ▼  every value request goes to ONE place
src/pricebt/assets/pricing.py: PricingService  (caches, FX, measure → function mapping, units)
        ▼  eval() of the strings in the asset config
YOUR LIBRARY (Citi-DP-style, Athena-style, in-house, QuantLib...)  ← reached ONLY via the config's imports/code
```

## Rules you must never break

| Rule | What it means | Enforced by |
|---|---|---|
| **MUST-1: no library in core** | Nothing under `src/pricebt/` may import, name or assume the shape of any pricing or market-data library. Your library lives only in `configs/assets/*.yaml` (and your own modules those configs import). | `tests/guards/` (import scan, token scan, import blocker) |
| **MUST-2: gs parity** | Module paths, class names, constructor signatures, result shapes and engine semantics match gs_quant. Every intentional difference has a `DEV-*` id. | `tests/test_gs_api_parity.py`, [`docs/v2/DEVIATIONS.md`](../../docs/v2/DEVIATIONS.md) |
| **MUST-3: one config per asset** | pricebt never interprets instrument kwargs; the config's `resolve` does. | the asset config contract, [`docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md) |
| **MUST-4: currencies and bp** | Risk is in currency per 1bp; rates are in bp. Conversions happen only through an FX config. | `tests/test_multi_currency.py` |
| **MUST-5: no asset special cases** | A new asset class (swaptions, futures, FX) is a new config, never engine code. | `tests/guards/test_asset_agnostic_scan.py` |

**If a library does not fit, change the config, never `src/pricebt`.** A pull request that edits core to make one library work is wrong by construction.

## Engine semantics that change your numbers

Read these before interpreting any result. They are gs behaviour, kept on purpose unless marked DEV.

1. **Signal and execution happen on the same close.** A trigger that fires on date `d` trades at `d`'s close prices. There is no next-day fill. If your signal uses `d`'s close, you are assuming you can trade at the price you observed; say so in the report.
2. **Holding window.** A trade is held for `create_date <= s < final_date`. Entry books cash `-PV(create)`; exit books `+PV(final)`. On its exit date the trade is out of the book and its value is in cash.
3. **Coupons paid between marks are not booked as cash.** gs has the same property. For multi-year swap holds, a coupon that pays while the trade is held drops out of the PV without reaching cash. P&L therefore misses realised coupons, which is material for carry strategies. Flag it in every report ([`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md)).
4. **Scaling is a quantity multiplier (DEV-I1).** `quantity_` multiplies extensive measures (`ccy`, `ccy_per_bp`). Intensive measures (`bp`, `pct`, `decimal`) are never scaled, so a rate stays a rate. A short position is `quantity_ < 0`.
5. **`resolve` pins the trade at its trade date.** `'10y'` becomes an absolute maturity and `'ATM'` becomes a number. A seasoned trade keeps those terms forever; a config that does not pin silently rolls the trade every day.
6. **Missing market data (DEV-E16).** With the default `missing_market='drop'`, grid dates with no market are removed and listed on `backtest.missing_market_dates`. Exits that land on such dates are rolled to the next available date (`backtest.missing_market_moves`).
7. **Mean-reversion "exits" are offsetting trades held forever** (gs `MeanReversionTrigger` + `AddTradeAction` with no duration). The ledger shows them all as `open`.
8. **Hedges on one date are sized against the pre-hedge book risk.** Several `HedgeAction`s firing on the same date do not see each other.
9. **Defaults are frictionless.** Transaction cost is `ConstantTransactionModel(0)`, there is no cash accrual, and `initial_value` is 0. `result_summary['Total']` is therefore cumulative P&L, not NAV.
10. **Currencies.** `Price` is in the asset's local currency. A mixed-currency book needs `run_backtest(result_ccy=...)` plus an FX config, or it raises gs's error.

## Where things live

| Path | What |
|---|---|
| `src/pricebt/backtests/` | engine, triggers, actions, `BackTest` results (the gs port) |
| `src/pricebt/assets/` | asset-config loader, namespace, `PricingService`, FX |
| `src/pricebt/instrument/` | gs instrument classes (`IRSwap`, `IRSwaption`, …) with real gs signatures, generated from `_gs_fields.py` |
| `src/pricebt/risk/` | risk measures (`Price`, `IRDelta`, `IRDeltaParallel`, …) and result objects |
| `configs/assets/` | real asset configs (one per asset) |
| `tests/assets/`, `tests/toylib/` | deterministic toy assets for tests and learning |
| [`docs/v2/DESIGN.md`](../../docs/v2/DESIGN.md) | the full contract; §4 is the asset config, §8 risk/results, §11 deviations |
| `skills/` | this library |

## Glossary

- **Asset config**: the YAML file that prices one asset ([`docs/v2/ASSET_CONFIG_GUIDE.md`](../../docs/v2/ASSET_CONFIG_GUIDE.md)).
- **Market**: whatever the config's `market.expr` returns for a date (a curve handle, a snapshot object, …). `None` means no data.
- **Resolve / resolved terms**: the plain-data dict that pins an instrument at its trade date.
- **Trade object**: the library's own representation, built from the resolved terms.
- **Unit value**: a function evaluated for quantity 1; pricebt applies `quantity_` and FX.
- **Extensive / intensive**: scales with position size / does not.
- **`PricingService`**: the single producer of values (`session.pricing`); use it for spot checks.

## Related skills

- [`pricebt-start-here`](../pricebt-start-here/SKILL.md): which skill to use.
- [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md): write your first asset config.
- [`pricebt-strategy-workflow`](../pricebt-strategy-workflow/SKILL.md): from idea to tearsheet.
