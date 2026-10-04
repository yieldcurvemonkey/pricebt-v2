# Independent validation against the desk's own numbers

`check_asset.py` proves an asset config is consistent with itself and with pricebt's conventions. It cannot prove the numbers are right. A config that loads the wrong curve, the wrong CSA, the wrong vol surface or the wrong bond static data still passes every automated check, and so do greeks that agree with each other and are all wrong.

This page is the manual step that closes that gap. Do it once per asset. Do it again after a library upgrade, or after a config change that touches `market:`, `resolve:`, `trade:` or a measure function.

## Principle

Compare pricebt's numbers for two or three concrete trades with numbers produced **by the same library through a route that does not go through your config**. That route can be the library's GUI, its standard risk report, the desk's risk system, or a trade booked in the library's own trade store.

- Agreement means the config calls the library the way the desk does.
- A mismatch is a finding until it is explained.

## Pick the trades

Choose two or three trades that differ in the ways the config could get wrong:

1. **A fresh at-the-money trade**, struck on a recent date. It tests the market, resolve and ATM logic.
2. **A seasoned, off-market trade**, struck months earlier and valued today. It tests pinning, past fixings and accrued coupon. It also tests the total own-rate delta, because a fixed-annuity delta is exact only at the money.
3. **A different shape**. For a swap: a short tenor, a forward start, or the opposite direction. For a swaption: an out-of-the-money receiver, or an option within a month of expiry. For a bond: a different identifier, or a date across a coupon payment.

For each trade, write down the terms in the desk system's own vocabulary so both sides price the same thing:

- swap: effective date, maturity, fixed rate, direction, notional;
- swaption: expiry, underlying tenor, strike, payer/receiver, bought/sold, settlement;
- bond: identifier, face amount, trade date and settlement date, and the repo terms (general collateral or special, overnight or term, haircut).

## Produce pricebt's numbers

From the repository root, with `PYTHONPATH=src;tests` (POSIX `src:tests`):

```python
from datetime import date
from pricebt.instrument import IRSwaption
from pricebt.risk import (Annuity, ExpiryInYears, IRAnnualImpliedVol, IRDelta, IRFwdRate, IRGammaParallel,
                          IRVega, Price, Theta)
from pricebt.session import PricebtSession

s = PricebtSession.use(assets=["tests/assets/toy_usd_swaption.yaml"])   # your config here
svc = s.pricing
trade_date, value_date = date(2024, 1, 3), date(2024, 2, 5)
t = svc.resolve(IRSwaption("Receive", "10y", "USD", notional_amount=1e7, expiration_date="1y", strike="A-50", buy_sell="Buy"), trade_date, None)
print(t.resolved_terms)                      # compare these terms with the booked trade first
for m in (Price, IRDelta(aggregation_level="Type"), IRGammaParallel, IRVega(aggregation_level="Type"),
          IRFwdRate, IRAnnualImpliedVol, Theta, ExpiryInYears, Annuity):
    print(m, float(svc.value(t, value_date, m, None)))
print(svc.value(t, value_date, IRDelta, None).result())   # the ladder, if mapped
print(svc.value(t, value_date, IRVega, None).result())    # the vol cube, '<tail>;<expiry>' keys
```

To match a booked trade exactly, pass its absolute terms instead of tenors, for example `termination_date=date(2034, 1, 3)` and a decimal `strike`, so both sides value identical terms. For a bond, pass the booked identifier and face amount (`Bond(identifier=..., size=..., buy_sell="Buy")`), plus whatever repo terms your config takes as kwargs, and value `CleanPrice`, `DirtyPrice`, `AccruedInterest`, `IRFwdRate` (the yield), `ModifiedDuration`, `Convexity`, `DaysToSettlement`, `RepoRate`, `RepoHaircut`, `FinancingToDate`, `ForwardPrice`, `Carry` and `RollDown` the same way.

## Produce the desk's numbers

Use the library's own front end or report for the same trade, on the same valuation date. Match these on both sides:

- **close / snapshot**: end of day vs intraday, the market `pricebt_date` maps to, and for options the vol snapshot at the same close;
- **CSA / discounting curve**: pricebt passes `pricebt_csa`, `None` by default;
- **units and signs**: PV in currency; sensitivities per +1bp on the full notional, holder-signed; rates and normal vols in the unit each function declares; theta per calendar day.

Screenshots or report extracts are fine. Record the source, the date, and the time of the snapshot.

## Compare

| quantity | tolerance (starting point) | notes |
|---|---|---|
| resolved terms (dates, fixed rate, strike) | exact | a one-day shift in a date is a convention bug (spot lag, roll, expiry offset); the strike is a decimal |
| PV / premium | 1e-5 × notional, or the desk's own bid/mid tolerance | compare mid with mid; for a bond, dirty per face amount |
| dv01 / `IRDelta` scalar | 1% | pricebt: the **total** derivative per +1bp of the own rate; payer and long option > 0, long bond < 0. A desk "curve dv01" differs by dr/ds |
| `IRGammaParallel` | 2-5% | per bp² of the own rate (chain rule); a desk gamma "per 1bp shift of the curve" differs |
| `IRVega` | 1-2% | per +1bp of **normal** vol; convert a lognormal or per-1% desk vega before comparing |
| `IRVanna`, `IRVolga` | 5-10% | finite-difference noise is large; compare signs and orders of magnitude first |
| `Theta` | 2-5% | per calendar day, own rate and vol fixed, total return; a desk theta that rolls the curve differs by roll-down; a bond's `Theta` holds the yield fixed and excludes financing (that is `FinancingToDate`), so compare it with an unfinanced desk carry |
| implied vol (`IRAnnualImpliedVol`) | 0.1bp | normal, at the strike; the daily vol is annual/√252 |
| par rate / forward (`IRFwdRate`) | 0.1bp | in the unit the function declares |
| bond clean / dirty price | 1e-6 per unit of face (0.0001 per 100) | `Price` is the settlement-date dirty value (not discounted to the trade date); `CleanPrice` is the quote; `CleanPrice + 100 × AccruedInterest / face = DirtyPrice` |
| accrued interest | 1e-8 × face | ACT/ACT ICMA for US Treasuries, to the standard settlement date, in currency and holder-signed |
| bond yield | 0.1bp | state the compounding (continuous, semi-annual street) on both sides |
| duration, convexity | 1% / 2% | `ModifiedDuration` and `Convexity` in the `IRFwdRate` yield convention; convert: dv01 ≈ −modified duration × dirty price × 1e-4 × face; `IRGammaParallel` ≈ convexity × dirty price × 1e-8 × face |
| settlement date | exact | `DaysToSettlement` in calendar days; the desk's settlement calendar |
| repo rate and haircut | 0.5bp / exact | the desk's repo source for the same collateral (special or general collateral) and term; the haircut the desk's counterparty applies |
| financing to date | 1e-6 relative | recompute: Σ over calendar days of `(1 − h) × Price(trade date) × RepoRate(day) / basis` (360 for USD repo) between the two settlement dates, holder-signed (a long pays); weekends use the last business day's fixing |
| forward price, carry, roll-down | 1e-6 relative / desk tolerance | `ForwardPrice` by the contract formula to H = settlement + 1 calendar month; compare `Carry` and `RollDown` with the desk's carry-and-roll screen only after matching its horizon, repo and roll-down curve |
| ladder buckets and vol cube | 2% per bucket, 1% on the sum | pillar labels must match the desk's; cube keys `'<tail>;<expiry>'` |
| `Cashflows` | exact dates, 1e-6 relative amounts | holder-signed, only flows after the valuation date; a bond's `payment_date` is the trade date its `Price` drops the flow (T+1: the business day before a business-day coupon date), not the coupon date |

Tighten the tolerances if both sides use the identical library call. Loosen them only with a written reason.

## Record the result

Write a short table per trade:

- the terms;
- each quantity from pricebt and from the desk;
- the difference, and pass or fail;
- the source of the desk number.

Keep the table next to the config or in the research log. The asset is validated when every trade is within tolerance, or every difference has an explanation that does not affect the strategy.

## When they disagree

1. **Compare resolved terms first.** Most mismatches are different trades, not different prices.
2. **Check the snapshot**: date, close time, CSA and the vol snapshot.
3. **Check units, signs and definitions**:
   - per +1bp vs per −1bp; per 1% vs per 1bp; decimal vs bp;
   - normal vs lognormal vol; per day vs per year theta;
   - total vs fixed-annuity delta;
   - clean vs dirty price; a settlement-date value vs one discounted to the trade date;
   - general collateral vs special repo; overnight vs term; repo day count (ACT/360 for USD) vs the bond's.
4. **Localise the error** with the relevant probe in [known-answer-probes.md](known-answer-probes.md).
5. **Fix the config**, then rerun `skills/pricebt-verify-asset-config/scripts/check_asset.py` and this comparison.
