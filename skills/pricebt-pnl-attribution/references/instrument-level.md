# Instrument-level explain between two dates: `PnlExplain(CloseMarket(...))`

**Available.** `PnlExplain`, `PnlExplainClose` (in `pricebt.risk`) and `CloseMarket` (in `pricebt.markets`) are implemented. The code is `src/pricebt/risk/__init__.py` and `src/pricebt/markets/__init__.py`; the specification is IR_RISK_DESIGN §8, DEV-M1 and DEV-M2. This is gs's notebook 030007 ("PnL explain"), on your own library.

The backtest attribution (definitions.md) is a **Taylor expansion per step over a held book**. `PnlExplain` is a **full revaluation between two markets for chosen instruments**. Use it to price a what-if between two closes, and to confirm a suspicious step of the backtest attribution.

## Usage

```python
from datetime import date
from pricebt.markets import CloseMarket, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import PnlExplain, Price

F, T = date(2024, 1, 2), date(2024, 1, 9)
with PricingContext(pricing_date=F):
    rows = swaption.calc(PnlExplain(CloseMarket(date=T))).result()     # DataFrameWithInfo, one row per risk factor
    book = Portfolio((swaption, bond)).calc(PnlExplain(CloseMarket(date=T))).aggregate()   # rows summed by factor
with PricingContext(pricing_date=F, market=CloseMarket(date=T)):
    moved = float(swaption.calc(Price).result())                         # F's valuation on T's market
with PricingContext(pricing_date=F):
    base = float(swaption.calc(Price).result())
# rows["value"].sum() == moved - base
```

- **What the rows are.** The change in value from the context's market (the market of F) to T's close, **with the pricing date held at F**. There is no time component: as in gs, it must be computed separately, as `Price(T) − Price(F on T's market)`.
- **The result** is always the bucketed frame (`mkt_type`, `mkt_asset`, ..., `value`), one row per factor (for example `IR`, `IR VOL`, `CREDIT`), with a `CROSSES` row for the rest of the full revaluation (DEV-M2; gs turns small frames into a float).
- **Two targets are two measures.** The target lives in the measure's parameters, so `PnlExplain(CloseMarket(date=T1))` and `PnlExplain(CloseMarket(date=T2))` never share a cached value, and label as `PnlExplain(date:..., location:LDN)`.
- **Quantity and portfolios.** Quantity scales the rows, and a portfolio's `aggregate()` sums them by factor.
- **Market overrides.** `PricingContext(market=CloseMarket(date=T))` evaluates every market (and FX) at T while `pricebt_date` stays the pricing date (DEV-M1). `resolve` always uses the pricing date's own market.

## What raises

| Call | Result |
|---|---|
| `PricingContext(market=<anything but a CloseMarket>)` | `NotSupportedError` (DEV-M1: never silently ignored) |
| `PnlExplain(<not a CloseMarket>)` | `NotSupportedError` |
| `PnlExplainClose()` when the target resolves to the pricing date itself | `NotSupportedError`: it would explain a market to itself |
| `PnlExplainLive()`, `PnlPredictLive()` | `NotSupportedError` (a live GS market) |
| a `CloseMarket` dated in the future | raises |
| `CloseMarket(date=d)` before d's close is in for its location (default `LDN`) | rolls to the previous business day; `check=False` skips the roll |
| an asset whose config does not map `PnlExplain` | the usual "no mapping for risk measure" error. `PnlExplain` is optional, outside the IR contract |

## What your asset config must supply

```yaml
portfolio_functions:
  pnl_explain:
    expr: 'lib.explain_by_factor(market, market_to, trades, weights, pricebt_date)'
    unit: ccy
    returns: buckets
risk_measures:
  PnlExplain: pnl_explain
```

- **What the function receives.** The usual `market`, `trades`, `weights` (quantities) and `pricebt_date`, plus the injected `market_to` (the market of the target date, on the context's csa) and `pricebt_to_date`.
- **What it returns.** A list of dicts with keys drawn from `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value` (per-row coordinates, IR_RISK_DESIGN R2-14), or `{mkt_point: value}`.
- **The reference implementations** show the row pattern: `pnl_explain` in `tests/toylib/irrisk.py` (swap: IR, CROSSES), `tests/toylib/swaption.py` (IR, IR VOL, CROSSES) and `tests/toylib/bond.py` (IR, CREDIT, CROSSES).

### How to implement it with your library (full revaluation by factor)

1. **Value every trade on `market`** at the pricing date: the base.
2. **For each factor k** (curve, vol surface or cube, credit spread, FX): value on `market` with **only factor k** replaced by its value in `market_to`. Row k = that value − base, weighted.
3. **Value on `market_to`**: the total. `CROSSES` = total − Σ rows.
4. **Hold the valuation date at `pricebt_date`** in every revaluation, so that no time passes. A library that values on its market object's own date must be given the valuation date explicitly. The toy swaption takes `pricebt_date` for this reason; the toy swap and bond are carry-free at a fixed date, so they do not need it. A memo in such a function keys on the market object **and** `pricebt_date` (and the trade): pricebt hands the same cached market object to its own date's evaluations and to this one.

How to do step 2 depends on your library's shape:

- **Object library** (QuantLib- or rateslib-style): build the scenario market by swapping one handle (curve, vol surface) of `market` for the one in `market_to`.
- **Platform service**: send a scenario request that overrides one factor's market data with the target date's. If the service has its own P&L-explain endpoint, map its factor names onto `mkt_type` rows.
- **Bond analytics package**: reprice at the target curve with the base spread, then at the base curve with the target spread.

## Verify

- **Rows sum to the full revaluation.** `rows["value"].sum() == Price(F on T's market) − Price(F)`, to 1e-12 relative on the toys. The test pattern is `test_rows_sum_to_the_price_change_holding_the_pricing_date` in `tests/test_pnl_explain_measure.py`.
- **Each factor row is close to the greeks for that factor** between F and T: the IR row ≈ delta + gamma, and the IR VOL row ≈ vega + volga. The gap, and `CROSSES`, are the higher-order and cross terms (vanna sits in `CROSSES`).

## Confirming a backtest step

For the worst step (t−1, t) of `pnl_explain_table` (see [diagnosing-residuals.md](diagnosing-residuals.md)):

1. Under `PricingContext(pricing_date=t−1)`, calc `PnlExplain(CloseMarket(date=t))` on the instruments of `bt.results[t−1].portfolio`.
2. Compare its IR row with `PNL_delta + PNL_gamma`, its IR VOL row with `VegaPnL + PNL_volga`, and its `CROSSES` row with `PNL_vanna`.
3. The time part is `economic_pnl − Σ rows`; compare it with `PNL_theta`.

Whichever pair disagrees names the greek to fix.
