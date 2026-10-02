# Portfolios, results, parameters and markets

This page covers what a gs_quant user expects around the measures, as pricebt implements it: `Portfolio`, `calc` under pricing contexts, `PortfolioRiskResult` and its shapes, the `pricebt.risk.core` helpers, finite-difference parameters, frames, declared-unsupported measures, `CloseMarket` and `PnlExplain`. Each claim here is asserted by a test:

- `tests/test_portfolio_notebooks.py` runs gs's `03_portfolios` notebooks 030000-030011 on toy assets;
- `tests/test_pricing_params.py`;
- `tests/test_unsupported_measures.py`;
- `tests/test_pnl_explain_measure.py`.

Behavioural differences from gs carry DEV ids: `docs/v2/DEVIATIONS.md`, and DESIGN §11.

## 1. Portfolio (`pricebt.markets.portfolio.Portfolio`)

| You want | Code | Notes |
|---|---|---|
| a book | `Portfolio((swap, swaption), name="book")` | a dict `{name: inst}` names each member; a single priceable wraps as one child |
| nesting | `Portfolio([Portfolio([...], name="EUR"), Portfolio([...], name="USD"), swaption])` | `to_frame` labels each level `portfolio_name_0`, `portfolio_name_1`, ... |
| the members | `p.priceables`, `p.instruments` (direct leaves), `p.all_instruments` (every leaf, depth-first), `p.portfolios`, `p.all_portfolios` | `all_portfolios` recurses (DEV-P1) |
| find a member | `p[0]`, `p[inst]`, `p["name"]` (the first match anywhere), `p[path]` | no match gives `()`, not a `KeyError` (gs) |
| paths | `p.all_paths`, `p.paths("name")` (this level's matches first, then the sub-portfolios') | `PortfolioPath` objects; `path(p)` fetches the member |
| a sub-book | `p.subset(p.paths("5y") + p.paths("10y"), name="swaps")` | flat; a single path to a sub-portfolio returns that sub-portfolio |
| change it | `append`, `extend`, `pop(item)`, `p + q`, `clone(clone_instruments=True)`, `scale(k, in_place=True)` | `pop` rebuilds from the direct leaves (gs) |
| round-trip a table | `Portfolio.from_frame(df, mappings)`, `from_csv(path, mappings)`, `p.to_frame()`, `p.to_csv(path)` | rows map to classes by `(asset_class, type)`; `quantity_` and `pricebt_asset` round-trip |
| a grid | `Grid(priceable, x_param, x_values, y_param, y_values)` | one sub-portfolio per y value |
| server-side names | `Portfolio.get`, `from_portfolio_id`, `save`, `save_as_quote`, `market()`, ... | raise `NotSupportedError` ("GS server-side") |

**Resolve before comparing dates.** An unresolved `'ATM'` or `'10y'` instrument re-resolves on every pricing date: it is a new ATM trade each day. So call `p.resolve()` inside `PricingContext(pricing_date=trade_date)` before a historical calc, an explain, or any two-date comparison.

## 2. Pricing: `calc`, `price`, `dollar_price`

- `with PricingContext(pricing_date=d): res = p.calc((Price, IRDelta))` returns a `PortfolioRiskResult`, usable after the block.
- An **instrument's** `calc` inside a block returns a `PricingFuture`, so call `.result()`. Outside a block it prices at `PricingContext.current` (a settable default, e.g. `PricingContext.current = PricingContext(pricing_date=d)`) and returns the value.
- `p.price(currency=None)` is `calc(Price)`, and `p.dollar_price()` is `calc(DollarPrice)` (`Price(currency="USD")`, which needs an FX config for a non-USD asset).
- `p.calc(measures, fn=f)` applies `f` to each instrument's own result (gs).
- `HistoricalPricingContext(start, end)` (or `dates=[...]`) prices every business date in range:
  - a scalar becomes a `SeriesWithInfo` indexed by date;
  - a ladder becomes one `DataFrameWithInfo` indexed by `date`, where a date without buckets has no rows;
  - a table (`Cashflows`, `CRIFIRCurve`) becomes one frame with a leading `date` column.

## 3. `PortfolioRiskResult` (PRR)

| Operation | Result |
|---|---|
| `res[Price]` | a PRR for one measure |
| `res[Price]["10y"]`, `res["10y"][Price]`, `res[Price][1]`, `res[Price][inst]` | the leaf value, the same number every way |
| `res["10y"]` (several measures) | a `MultipleRiskMeasureResult`: a dict keyed by measure in request order, with `.instrument`, `to_frame()`, `* k` and `+` |
| `res[[Price, IRDelta]]`, `res[path]`, `res[inst_list]`, `res[slice]` | a sub-result |
| `res[date]` or `res[[d1, d2]]` on a historical result | the slice for those dates. A priced date with no buckets gives an empty frame; an unpriced date raises `KeyError` (DEV-R16) |
| `for v in res[Price]` | the leaf values in `all_paths` order |
| `res[Price].aggregate()` | a scalar sum (`FloatWithInfo`, keeping `.unit`); historical: a `SeriesWithInfo` summed per date |
| `res[IRDelta].aggregate()` | the ladders summed bucket by bucket (a 6-column frame) |
| `res.aggregate()` (several measures) | a `MultipleRiskMeasureResult` of per-measure aggregates |
| `res[Cashflows].aggregate()` | the tables concatenated with an `instrument_name` column, never summed (DEV-R11) |
| `res[Price].to_frame()` | the default pivot: `portfolio_name_0` × `instrument_name`, rows labelled from their own path (DEV-R6) |
| `to_frame(values=None, index=None, columns=None)` | the raw records `portfolio_name_k, instrument_name, risk_measure, value` (plus `dates` when historical) |
| `to_frame("value", "portfolio_name_0", "instrument_name")`, `aggfunc="mean"`, `display_options=DisplayOptions(show_na=True)` | gs pivots; same-name rows are summed by default |
| `res * 2`, `res + res2` | every leaf scaled (DEV-R8); dates composed, so `sum` over single-date results stitches a historical one. `res + 1` raises |

**Errors from `aggregate`.** A leaf in error, a mix of scalars and ladders (unless `allow_heterogeneous_types=True`), or mixed units raise gs's `ValueError`s. So do different dates or keys, unless `allow_mismatch_risk_keys=True`.

**Value types.**

| Kind | Type |
|---|---|
| scalar | `FloatWithInfo` (`.unit` such as `{"USD": 1}` or `{"bp": 1}`, `.risk_key`) |
| ladder | `DataFrameWithInfo` with `mkt_type, mkt_asset, mkt_class, mkt_point, mkt_quoting_style, value` |
| table | a `DataFrameWithInfo` with its own columns |
| error | `ErrorValue` |

**Additivity (R2-2).** Own-rate deltas of different instrument types are per bp of *different* rates. Summing `IRDeltaParallel` across a swap and a swaption, whether in `aggregate`, a `HedgeAction` or a risk trigger, is therefore approximate. It is exact when the hedge is the same type. With a par-quote curve shift the error is small.

**Helpers in `pricebt.risk` (`pricebt.risk.core`).**

- `aggregate_risk([ladder_a, ladder_b], threshold=None)` sums frames by bucket.
- `aggregate_results(results, allow_mismatch_risk_keys=False)`.
- `subtract_risk(left, right)`, the intent of gs's broken version (DEV-R10).
- `sort_risk(df)` reorders the columns (rows keep first-appearance order, DEV-R5).
- `combine_risk_key(k1, k2)`.

## 4. Presets, fallbacks and the forms a request asks for

| Request | Form asked | Served by |
|---|---|---|
| `IRDelta(aggregation_level='Type')`, `IRDeltaParallel` (`Asset`) | scalar | the `IRDelta` scalar slot. With only a ladder mapped, pricebt sums the buckets, but only for classes **without** a contract (`ConfigInstrument`, FX, ...): an `IRSwap` or `IRSwaption` config must map the scalar (it cannot load otherwise), and a Bond's declared scalar raises `UnsupportedMeasureError` instead of summing. Map the scalar (`implementing-measures.md` section 1, Shape B) |
| bare `IRDelta`, `IRDelta(aggregation_level='Point')` | bucketed | the `IRDelta` bucketed slot |
| `IRVanna(aggregation_level='Type')`, `IRVolga(...)`, `IRBasis(...)`, `IRXccyDelta(...)` | scalar | their scalar slot. The bare name asks for a ladder they do not have, and the error says to add `aggregation_level='Type'` (R2-13) |
| `IRVegaParallel`, `IRBasisParallel`, `IRXccyDeltaParallel`, `IRDeltaLocalCcy`, `IRVegaLocalCcy` | the preset's form | the base measure's mapping (`base_name`) |
| `IRGammaParallelLocalCcy`, `IRDiscountDeltaParallelLocalCcy` | scalar | the base measure (DEV-I16: pricebt prices in the instrument's own currency) |
| a plain measure (`Theta`, `IRFwdRate`, `Cashflows`) | whatever is mapped | its only slot |
| `PnlExplain(CloseMarket(...))`, `PnlExplainClose()` | bucketed | the `PnlExplain` mapping (a `returns: buckets` portfolio function) |

A config may map a preset key (for example `IRDeltaParallel: dv01`), and the contract counts it toward the base (R2-10). On a Bond, **declare the base measure, never a preset:** a declared preset name loads with a warning and declares nothing the contract counts. On an `IRSwap` or `IRSwaption` any declaration of a contract measure or its preset is a load error.

## 5. Finite-difference parameters (DEV-I10)

gs sends `bump_size`, `finite_difference_method`, `local_curve` and `scale_factor` to its server. pricebt injects them into every `functions:` and `portfolio_functions:` evaluation as `pricebt_bump_size`, `pricebt_finite_difference_method`, `pricebt_local_curve` and `pricebt_scale_factor`, each `None` when unset.

- **A function supports a parameter only if its expression names the variable.** Requesting a parameter the mapped function does not name raises `NotSupportedError("... does not reference pricebt_bump_size")`, so nothing is silently ignored.
- `mkt_marking_options` always raises.
- `aggregation_level` and `currency` are handled by pricebt, not passed.
- `finite_difference_method` accepts a string, case-insensitive, coerced to `FiniteDifferenceMethod.Up/Centered/Down/CenteredSecondOrder` (a `str` enum). An invalid value raises `ValueError`.
- Parameters are part of every cache and group key, so two bump sizes never share a value.

```yaml
functions:
  delta:
    # IRDelta(aggregation_level='Type', bump_size=5, finite_difference_method='Up') reaches _delta(h=5, method='Up')
    expr: '_delta(market, trade, h=pricebt_bump_size or 1.0, method=pricebt_finite_difference_method or "Centered")'
    unit: ccy_per_bp
```

Honour the value your library receives. A bump size in bp must still return **per 1bp** (divide by the bump); `scale_factor` multiplies the result. Mismatched units are your function's bug, not pricebt's.

## 6. Declared-unsupported measures (Bond only)

Only a `Bond` (or a class without a contract) can declare a measure: an `IRSwap` or `IRSwaption` config that declares a contract measure does not load (`docs/v2/IR_STRICT_CONTRACT.md`), so this never happens on a swap or swaption. A request for a declared measure or form raises `pricebt.errors.UnsupportedMeasureError`. It is both a `ConfigError` and a `NotSupportedError`, and carries `.measure`, `.form` (`scalar`, `bucketed`, `frame` or `*`) and `.reason`. The message starts with the generic "asset A has no mapping for risk measure X". A mapping always wins over a stale declaration of the same form: the config loads with a `UserWarning` (R2-9).

## 7. `CloseMarket` and `PnlExplain` (`pricebt.markets`, `pricebt.risk`)

- `PricingContext(pricing_date=d, market=CloseMarket(date=t))`: every function sees `market` = the market of `t`, while `pricebt_date` stays `d`, and `resolve` still uses `d`'s own market (DEV-M1). Any other `market=` raises `NotSupportedError`.
- `PnlExplain(CloseMarket(date=t))` priced at `d` is the change in value from `d`'s market to `t`'s, by risk factor, **with no time component**. gs 030007 adds time separately as `price(d, market t) − price(t)`. The target lives in the measure's parameters, so two targets never share a cache entry (DEV-M2).
- Map it in the config as a `returns: buckets` **portfolio function** that names `market_to` or `pricebt_to_date` (a function naming neither is refused). It returns row dicts by `mkt_type`, e.g. `IR`, `IR VOL`, `CROSSES`.
- It **is** in the `IRSwap` and `IRSwaption` contracts (form bucketed, `PnlExplainClose` resolving to it), so those configs must map it; a Bond config without it is fine.
- `PnlExplainClose()` explains to the pricing date's own close, so it raises unless it is priced under `PricingContext(market=CloseMarket(date=<another date>))`. `PnlExplainLive` and `PnlPredictLive` always raise (they are live GS server features).

## 8. Runnable tour

This block runs as-is from the repository root with `PYTHONPATH=src;tests`. `tests/skills/test_skill_risk_measures.py` executes it.

```python
# runnable: a nested book of toy swaps and a toy swaption (PYTHONPATH=src;tests, repository root)
import datetime as dt

from pricebt.assets import yamlio
from pricebt.errors import NotSupportedError, UnsupportedMeasureError
from pricebt.instrument import Bond, IRSwap, IRSwaption
from pricebt.markets import CloseMarket, HistoricalPricingContext, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import IRDelta, IRDeltaParallel, PnlExplain, Price, Theta, aggregate_risk
from pricebt.risk.results import DataFrameWithInfo, FloatWithInfo, MultipleRiskMeasureResult, SeriesWithInfo
from pricebt.session import PricebtSession

# a toy bond whose config declares Theta (only a Bond may declare: an IRSwap/IRSwaption config maps every measure)
bond_no_theta = yamlio.load_file("tests/assets/toy_usd_bond.yaml")
bond_no_theta["asset"] = "toy_usd_bond_no_theta"
del bond_no_theta["risk_measures"]["Theta"]
bond_no_theta["unsupported_measures"] = {"Theta": "this bond library has no carry call"}
PricebtSession.use(assets=["tests/assets/toy_usd_swaption.yaml", "tests/assets/toy_usd_irs_full.yaml", bond_no_theta])
d, d_later = dt.date(2024, 3, 4), dt.date(2024, 3, 8)
swaps = Portfolio([IRSwap("Pay", "5y", "USD", notional_amount=1e6, name="5y", pricebt_asset="toy_usd_irs_full"),
                   IRSwap("Pay", "10y", "USD", notional_amount=1e6, name="10y", pricebt_asset="toy_usd_irs_full")], name="swaps")
opts = Portfolio([IRSwaption("Receive", "10y", "USD", expiration_date="1y", notional_amount=1e6, name="1y10y")], name="opts")
book = Portfolio([swaps, opts], name="book")

with PricingContext(pricing_date=d):
    book.resolve()                                      # pin ATM strikes and dates at d (else each date re-resolves)
    res = book.calc((Price, IRDeltaParallel, IRDelta))  # a PortfolioRiskResult; an instrument's calc is a future here

# indexing: measure then name, or name then measure (a name matches anywhere in the tree, first match)
pv = res[Price]["10y"]
assert isinstance(pv, FloatWithInfo) and pv.unit == {"USD": 1} and float(pv) == float(res["10y"][Price])
assert isinstance(res["1y10y"], MultipleRiskMeasureResult) and list(res["1y10y"]) == [Price, IRDeltaParallel, IRDelta]
assert list(res[IRDelta]["10y"].columns) == ["mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style", "value"]

# aggregate: scalars sum, ladders sum bucket by bucket; iterating yields the leaves in all_paths order
assert abs(float(res[IRDeltaParallel].aggregate()) - sum(float(v) for v in res[IRDeltaParallel])) < 1e-6
assert isinstance(res[IRDelta].aggregate(), DataFrameWithInfo)
assert [i.name for i in book.all_instruments] == ["5y", "10y", "1y10y"] and len(book.all_paths) == 3

# to_frame: the default pivot, the raw records
assert list(res[Price].to_frame().index) == ["swaps", "opts"]
raw = res[Price].to_frame(values=None, index=None, columns=None)
assert list(raw.columns) == ["portfolio_name_0", "instrument_name", "risk_measure", "value"]

# paths and subset; aggregate_risk over ladders from separate calls
assert len(book.subset(book.paths("5y") + book.paths("10y"))) == 2
with PricingContext(pricing_date=d):
    one, two = book["5y"].calc(IRDelta), book["10y"].calc(IRDelta)
assert set(aggregate_risk([one, two]).mkt_point) == {"2Y", "5Y", "10Y", "30Y"}

# historical: a scalar is a date-indexed Series, a ladder one frame indexed by date
with HistoricalPricingContext(d, d_later):
    hist = book.calc((Price, IRDelta))
assert isinstance(hist[Price]["10y"], SeriesWithInfo) and len(hist[Price]["10y"]) == 5
assert isinstance(hist[Price].aggregate(), SeriesWithInfo) and hist[IRDelta]["10y"].index.name == "date"
assert float(hist[d_later][Price]["10y"]) == float(hist[Price]["10y"][d_later])  # slice by date, then index

# finite-difference parameters reach a function only if it names pricebt_<parameter>
with PricingContext(pricing_date=d):
    try:
        book["10y"].calc(IRDelta(aggregation_level="Type", bump_size=5)).result()
        raise AssertionError("expected NotSupportedError")
    except NotSupportedError as exc:
        assert "pricebt_bump_size" in str(exc)

# a declared-unsupported measure raises with the config's own reason (the bond declares Theta)
with PricingContext(pricing_date=d):
    try:
        Bond(identifier="TOY 4.25 2034-11-15", size=1e6, buy_sell="Buy", settlement_currency="USD", pricebt_asset="toy_usd_bond_no_theta").calc(Theta).result()
        raise AssertionError("expected UnsupportedMeasureError")
    except UnsupportedMeasureError as exc:
        assert exc.measure == "Theta" and exc.reason  # the reason written in the config

# CloseMarket: another date's market, same pricing date; PnlExplain: rows by risk factor, no time component
with PricingContext(pricing_date=d, market=CloseMarket(date=d_later)):
    moved = book.price()
with PricingContext(pricing_date=d):
    explain = book.calc(PnlExplain(CloseMarket(date=d_later)))
rows = explain.aggregate()
assert set(rows.mkt_type) == {"IR", "IR VOL", "CROSSES"}
assert abs(rows.value.sum() - (float(moved.aggregate()) - float(res[Price].aggregate()))) < 1e-6
```

## 9. What does not port

- **Server features:** portfolio persistence, live markets, quotes and books all raise `NotSupportedError`.
- **Scenarios:** `RollFwd`, `CurveScenario`, `MarketDataShockBasedScenario` and the rest are later work (`docs/v2/IR_RISK_DESIGN.md` §12). Compute a scenario in your own library as a function if you need one.
- **Cross-leg references** such as `strike="=[foo].strike + 5bp"` and `'=solvefor(...)'`: they stay literal strings, and your `resolve` must raise on them (gs 030010, pinned by the notebook test).
- **gs's 12-column cross-gamma:** pricebt's `IRGamma` ladder is diagonal (DEV-I13).
