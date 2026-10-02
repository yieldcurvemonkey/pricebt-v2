# Worked example: the fictional Meridian SDK

`meridian_sdk/` is a **fictional** bank-style pricing SDK. It is not any real vendor's API. It is
built to have the *shape* of an enterprise platform (the kind of thing Citi Datapoint (DP) or JPM
Athena users will recognise: a session, market handles by date, trade specs, batch pricing with
measure codes, what-if scenario markets). Its conventions deliberately differ from pricebt's, so that
[`meridian_usd_irs.yaml`](meridian_usd_irs.yaml) has to convert every one of them. That config is the
worked answer. It maps the **whole** strict `IRSwap` contract
([`docs/v2/IR_STRICT_CONTRACT.md`](../../../docs/v2/IR_STRICT_CONTRACT.md)): an `IRSwap` config
cannot declare a measure unsupported, so every measure comes from Meridian's measure codes, its
scenario markets, or an identity on them (see "The whole contract" below). Tests:
`tests/skills/test_skill_connect_example.py`.

Run it (from the repository root):

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-connect-pricing-library/example"
python -m pytest tests/skills/test_skill_connect_example.py -o addopts= -p no:cacheprovider -q
```

## The SDK API

| Call | Returns | Notes |
|---|---|---|
| `meridian_sdk.connect(env="SIM", gaps=None, latency=0.0)` | `Client` | Only `"SIM"` exists. `gaps` = ISO dates with no data (default `DEFAULT_GAPS = ("2024-03-14",)`). |
| `Client.calls` | `int` | Counts every remote-style call (`market`, `scenario`, `discount_factors`, `spot_date`, `maturity`, `price`). |
| `Client.market(as_of, curve)` | `MarketHandle` | `as_of` is an ISO **string**; `curve="USD.SOFR"`. Raises `MarketClosed` on weekends/`HOLIDAYS`, `NoData` before 2020-01-02, after 2026-06-30, or on a gap. |
| `Client.scenario(market, shift_bp=0.0, pillar_shifts_bp=None, valuation_date=None, hold="ZEROS")` | `MarketHandle` | A what-if copy, one remote call. `shift_bp` moves every pillar zero rate (bp, + = up); `pillar_shifts_bp` moves single pillars, keyed like `BUCKET_DV01` (`"USD.SOFR:10Y"`). `valuation_date` (ISO, any calendar day) re-values the snapshot from that date: `hold="ZEROS"` (the **default**) keeps the zero rates, so the curve rolls; `hold="FORWARDS"` keeps every forward (`DF(x) / DF(valuation_date)`). `HOLD = ("ZEROS", "FORWARDS")`. |
| `Client.discount_factors(market, dates)` | `list[float]` | DF from the market's valuation date to each ISO date, one remote call. |
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
| `FIXED_PCT` | fixed rate in percent | gs `fixed_rate` is **decimal** (0.0385) | `/ 100` (only needed if you read it); a spec's `fixed_rate_pct ± 0.01` moves the strike 1bp |
| `DV01` | PV change for a 1bp **decrease** of every pillar zero (receiver > 0) | `ccy_per_bp`, PV change per **+1bp** (payer > 0) | `_flip` (negate); it is the zero-curve DV01 `dPV/ds`, not the own-rate `IRDelta` |
| `BUCKET_DV01` | `{"USD.SOFR:2Y": ...}`, same receiver-positive sign, sums to `DV01` | `{"2Y": ...}`, payer-positive; CRIF `Label1` `"2y"` | strip the `USD.SOFR:` prefix, `_flip`; lower case for CRIF |
| `CASHFLOWS` | `[{"date": ISO, "amount": > 0, "direction": "PAY"/"RECEIVE" (the holder's), "leg": "FIXED"/"FLOAT"}]`, the flows `PV` still includes | `Cashflows` frame: `payment_date` (date), `payment_amount` holder-signed, `currency`, `payment_type` | `RECEIVE` → +amount, `PAY` → −amount; `date.fromisoformat`; `leg.title()` |
| tenor `end` | resolved against the **pricing** market's spot (floats) | resolved terms are pinned at the trade date | `resolve` calls `Client.maturity(...)` |
| `fixed_rate_pct=None` | re-strikes at par on every market | a strike is fixed at the trade date | `resolve` prices `PAR_PCT` once, stores a decimal |
| `scenario(valuation_date=...)` | keeps the **zero rates** by default (a rolled curve) | `Theta` holds the **forwards** (DEV-I15) | pass `hold="FORWARDS"` for `Theta` |
| dates | ISO strings | `datetime.date` | `d.isoformat()` / `str(date)` in, `date.fromisoformat` out |
| closed day | raises `MarketClosed` / `NoData` | `market.expr` returns `None` | `try/except` in `load_market` |

## The model (so you can sanity-check numbers)

Zero rates at pillars 1Y, 2Y, 5Y, 10Y and 30Y are linearly interpolated in time (flat outside),
continuously compounded, ACT/365F. Pillar level and slope oscillate with the business-day index, so
the 10y par rate moves by about 200bp over a few months, which is enough for mean-reversion
strategies to trade. Swaps have annual fixed coupons and float coupons on the same schedule, each
float coupon valued `N*(DF(prev) - DF(c))` (so before the first coupon the float leg is
`N*(DF(start) - DF(end))`; a period already running uses today's curve, there is no fixings
history). `PV` drops each flow on its payment date. `DV01` and `BUCKET_DV01` are analytic
first-order sensitivities to the pillar zeros, so the buckets sum to `DV01` exactly. A finite
difference agrees to about 0.1% (convexity).

## The whole contract

**First-order codes, one call.** `_risk(market, trade, pricebt_date)` prices the trade, the same
trade with its fixed rate ±1bp, and a spot-starting probe to the same end, with
`PV, DV01, PAR_PCT, BUCKET_DV01, CASHFLOWS`, in ONE `price` call, memoised on the market by
`(pricebt_date, trade)`. Under a `CloseMarket` override (the market is another date's), it values
the trade from the pricing date on that market's curve (a `scenario(valuation_date=...)`, zero rates
held), so no time passes, as `PnlExplain` does.

| Measure | From | Why it is exact |
|---|---|---|
| `Annuity` | `-[PV(K+1bp) - PV(K-1bp)] / 2e-4` | a swap's PV is linear in K; payer > 0, receiver < 0 |
| `IRDelta` scalar | `dr/ds = [DV01(K) + (r - K) dDV01/dK] / (Annuity x 1e-4)`, then `DV01(K) / (dr/ds)` | DV01 is linear in K, and a swap struck at its par rate r moves only through r. dr/ds is about 1.03-1.05 here (continuous zeros against an annual par rate), so the raw DV01 would overstate the own-rate delta by 3-5% |
| `IRDiscountDeltaParallel` | the zero-curve DV01 | one curve: discounting is the curve |
| `IRFwdRate`, `IRSpotRate` | `PAR_PCT` of the trade and of the spot-starting probe | the contract's definitions |
| `Cashflows` | `CASHFLOWS`, holder-signed | discounted with `discount_factors` they sum to `PV` exactly (tested) |
| `CRIFIRCurve` | the trade's own `BUCKET_DV01`, flipped, one row per non-zero pillar (`Risk_IRCurve`, `USD`, Bucket `"1"`, `Label1` the pillar in lower case, `Label2` `"SOFR"`) | the same numbers as the `IRDelta` ladder, so `sum(Amount)` equals its sum |
| `IRDelta` bucketed | `BUCKET_DV01` of the whole book in ONE call | zero-pillar ladder, payer-positive |
| `ExpiryInYears`, vol/basis/xccy zeros | date arithmetic, `'0.0'` | R2-8; the zeros are exactly `contracts.ZERO_BY_CONVENTION` for `IRSwap` |

**Scenario markets** (built once per market object and reused by every trade on it):

| Measure | From |
|---|---|
| `IRGammaParallel` | `scenario(shift_bp=±1)`: `PV` and `PAR_PCT` on both, the chain-rule second derivative in the own rate (never d(DV01)/dr) |
| `IRGamma` bucketed | `scenario(pillar_shifts_bp={pillar: ±1})` per pillar: the diagonal `PV(+) + PV(-) - 2PV` |
| `Theta` | `scenario(valuation_date=t+1d, hold="FORWARDS")`: `PV(t+1d) + flows paid in (t, t+1d] - PV(t)`, per calendar day |
| `PnlExplain` | `market_to` seen from the pricing date (`scenario(valuation_date=...)`, zero rates held): row `IR` = the whole move, `CROSSES` = 0 |

**The R3-1 measures** (identities on the above):

| Measure | Formula | Unit |
|---|---|---|
| `ParSpread` | `K x 1e4 - par` (one curve, float on the fixed schedule): the same for payer and receiver | bp |
| `FairPremium` | `PV / DF(spot)` (`discount_factors`; Meridian settles at spot) | USD |
| `ForwardPrice` | `PV / DF(termination)`; `PV` on or after it | USD |
| `PremiumCents` | `PV / abs(notional) x 1e4` | bp |
| `LocalAnnuityInCents` | `Annuity / abs(notional)` | decimal |
| `CompoundedFixedRate` | `(1 + K/f)^f - 1` with `f = 1` (annual fixed leg): `K` | bp |

`measures.py matrix --strict` passes (every row MAPPED). The checker has no FAIL; its WARNs, also in
the config's `description:`: `swap_bucket_sum` / `ir_ladder_sum[IRDelta]` (the zero-pillar ladder
sums to the curve DV01, dr/ds times the own-rate `IRDelta`, R2-2), `ir_theta` (the curve also
changes **shape** every day, which the own-rate delta does not explain, so the one-day implied carry
is noisy), `ir_ladder_sum[IRGamma]` (a zero-pillar diagonal against the own-rate gamma), and
`swap_pv_identity` (a full-curve DV01 depends on the strike).

## Deliberate mistakes

[`mistakes/`](mistakes/README.md) holds three one-error variants of the config, which the checker
skill must catch.
