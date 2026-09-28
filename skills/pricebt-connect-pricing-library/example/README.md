# Worked example: the fictional Meridian SDK

`meridian_sdk/` is a **fictional** bank-style pricing SDK. It is not any real vendor's API. It is
built to have the *shape* of an enterprise platform (the kind of thing Citi Datapoint (DP) or JPM
Athena users will recognise: a session, market handles by date, trade specs, batch pricing with
measure codes). Its conventions deliberately differ from pricebt's, so that
[`meridian_usd_irs.yaml`](meridian_usd_irs.yaml) has to convert every one of them. That config is the
worked answer. Tests: `tests/skills/test_skill_connect_example.py`.

Run it (from the repository root):

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-connect-pricing-library/example"
python -m pytest tests/skills/test_skill_connect_example.py -o addopts= -p no:cacheprovider -q
```

## The SDK API

| Call | Returns | Notes |
|---|---|---|
| `meridian_sdk.connect(env="SIM", gaps=None, latency=0.0)` | `Client` | Only `"SIM"` exists. `gaps` = ISO dates with no data (default `DEFAULT_GAPS = ("2024-03-14",)`). |
| `Client.calls` | `int` | Counts every remote-style call (`market`, `spot_date`, `maturity`, `price`). |
| `Client.market(as_of, curve)` | `MarketHandle` | `as_of` is an ISO **string**; `curve="USD.SOFR"`. Raises `MarketClosed` on weekends/`HOLIDAYS`, `NoData` before 2020-01-02, after 2026-06-30, or on a gap. |
| `Client.spot_date(market)` | ISO str | as_of + 2 business days. |
| `Client.maturity(market, start, tenor)` | ISO str | start + tenor, following business day. |
| `Client.swap(currency, start, end, fixed_rate_pct=None, notional=1e6, direction="PAY")` | `TradeSpec` (frozen dataclass) | `end` is an ISO date **or a tenor**. `fixed_rate_pct=None` means "at par on whatever market prices it". `notional > 0` always; `direction` is `"PAY"`/`"RECEIVE"` of the fixed leg. |
| `Client.price(specs, market, measures)` | `list[dict]` | ONE remote call for the whole list. One dict per spec, keyed by measure code. |
| `MarketHandle.as_of`, `.curve`, `.client` | | A plain class, so a config can memoise on `market.__dict__`. |

## Measure codes and conventions

| Code | Meridian convention | pricebt convention | Conversion in the config |
|---|---|---|---|
| `PV` | currency, holder's view | `ccy` | none |
| `PAR_PCT` | par rate in **percent** (3.85) | `bp` (385.0) | `* 100` |
| `FIXED_PCT` | fixed rate in percent | gs `fixed_rate` is **decimal** (0.0385) | `/ 100` (only needed if you read it) |
| `DV01` | PV change for a 1bp **decrease** (receiver > 0) | `ccy_per_bp`, PV change per **+1bp** (payer > 0) | `-DV01` |
| `BUCKET_DV01` | `{"USD.SOFR:2Y": ...}`, same receiver-positive sign, sums to `DV01` | `{"2Y": ...}`, payer-positive | strip the `USD.SOFR:` prefix, negate |
| tenor `end` | resolved against the **pricing** market's spot (floats) | resolved terms are pinned at the trade date | `resolve` calls `Client.maturity(...)` |
| `fixed_rate_pct=None` | re-strikes at par on every market | a strike is fixed at the trade date | `resolve` prices `PAR_PCT` once, stores a decimal |
| dates | ISO strings | `datetime.date` | `d.isoformat()` / `str(date)` in, `date.fromisoformat` out |
| closed day | raises `MarketClosed` / `NoData` | `market.expr` returns `None` | `try/except` in `load_market` |

## The model (so you can sanity-check numbers)

Zero rates at pillars 1Y, 2Y, 5Y, 10Y and 30Y are linearly interpolated in time (flat outside),
continuously compounded, ACT/365F. Pillar level and slope oscillate with the business-day index, so
the 10y par rate moves by about 200bp over a few months, which is enough for mean-reversion
strategies to trade. Swaps have annual fixed coupons and a float leg valued at N*(DF(start) - DF(end)).
`DV01` and `BUCKET_DV01` are analytic first-order sensitivities to the pillar zeros, so the buckets
sum to `DV01` exactly. A finite difference agrees to about 0.1% (convexity).

## Deliberate mistakes

[`mistakes/`](mistakes/README.md) holds three one-error variants of the config, which the checker
skill must catch.
