# Independent validation against the desk's own numbers

`check_asset.py` proves an asset config is consistent with itself and with pricebt's conventions. It cannot prove the numbers are right: a config that loads the wrong curve, the wrong CSA or the wrong convention passes every automated check. This page is the manual step that closes that gap. Do it once per asset, and again after a library upgrade or a config change that touches `market:`, `resolve:` or `trade:`.

## Principle

Compare pricebt's numbers for two or three concrete trades with numbers produced **by the same library through a route that does not go through your config**: the library's GUI, its standard risk report, the desk's risk system, or a trade booked in the library's own trade store. Agreement means the config calls the library the way the desk does. A mismatch is a finding until explained.

## Pick the trades

Choose two or three trades that differ in the ways the config could get wrong:

1. **A fresh at-the-money trade** struck on a recent date (tests the market, resolve and ATM logic).
2. **A seasoned, off-market trade** struck months earlier and valued today (tests pinning, past fixings, accrued coupon).
3. **A different shape**: a short tenor, a forward start, or the opposite direction (tests conventions and sign).

For each trade write down the terms in the desk system's own vocabulary (effective date, maturity, fixed rate, direction, notional, conventions) so both sides price the same thing.

## Produce pricebt's numbers

From the repository root, with `PYTHONPATH=src;tests` (POSIX `src:tests`):

```python
from datetime import date
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, IRFwdRate, Price
from pricebt.session import PricebtSession

s = PricebtSession.use(assets=["tests/assets/toy_usd_irs.yaml"])   # your config here
svc = s.pricing
trade_date, value_date = date(2024, 1, 3), date(2024, 2, 5)
t = svc.resolve(IRSwap("Pay", "10y", "USD", 1e7), trade_date, None)
print(t.resolved_terms)                      # compare these terms with the booked trade first
for m in (Price, IRDelta(aggregation_level="Type"), IRFwdRate):
    print(m, float(svc.value(t, value_date, m, None)))
print(svc.value(t, value_date, IRDelta, None).result())   # the ladder, if mapped
```

To match a booked trade exactly, pass its absolute terms instead of tenors (for example `termination_date=date(2034, 1, 3)` and an explicit `fixed_rate` in the unit your `resolve:` expects), so both sides value identical terms.

## Produce the desk's numbers

Use the library's own front end or report for the same trade, on the same valuation date, with the same:

- **close / snapshot** (end of day vs intraday; the market `pricebt_date` maps to);
- **CSA / discounting curve** (pricebt passes `pricebt_csa`, `None` by default);
- **currency and units** (PV in currency; dv01 per +1bp on the full notional; rates in the unit the function declares).

Screenshots or report extracts are fine. Record the source, the date, and the time of the snapshot.

## Compare

| quantity | tolerance (starting point) | notes |
|---|---|---|
| resolved terms (dates, fixed rate) | exact | a one-day shift in effective or maturity date is a convention bug (spot lag, roll convention) |
| PV | 1e-5 x notional, or the desk's own bid/mid tolerance | compare mid with mid |
| dv01 (parallel) | 1% | sign: payer positive per +1bp in pricebt |
| par rate | 0.1bp | in the unit the function declares |
| ladder buckets | 2% per bucket, 1% on the sum | pillar labels must match the desk's |

Tighten the tolerances if both sides use the identical library call; loosen them only with a written reason.

## Record the result

Write a short table per trade: terms, each quantity from pricebt and from the desk, the difference, pass/fail, and the source of the desk number. Keep it next to the config or in the research log. The asset is validated when every trade is within tolerance or every difference has an explanation that does not affect the strategy.

## When they disagree

1. Compare resolved terms first. Most mismatches are different trades, not different prices.
2. Check the snapshot: date, close time and CSA.
3. Check units and signs: per +1bp vs per -1bp, per 1% vs per 1bp, decimal vs bp.
4. Run the relevant probe in [known-answer-probes.md](known-answer-probes.md) to localise the error.
5. Fix the config, then rerun `skills/pricebt-verify-asset-config/scripts/check_asset.py` and this comparison.
