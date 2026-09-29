# Known-answer probes

These are hand-run probes whose answer you know before you compute it. They cover what `skills/pricebt-verify-asset-config/scripts/check_asset.py` cannot see: the checker never bumps your library, and it probes one trade.

- The **pricebt side** of every probe goes through pricebt only.
- The **bump side** uses your own library directly, in its own terms.

Run each probe once per asset. Write down the expected answer first, then the observed one.

Setup, in a Python session from the repository root with `PYTHONPATH=src;tests` (POSIX `src:tests`):

```python
from datetime import date
from pricebt.instrument import Bond, IRSwap, IRSwaption
from pricebt.risk import (Annuity, Cashflows, ExpiryInYears, IRAnnualImpliedVol, IRDelta, IRFwdRate,
                          IRGammaParallel, IRVega, Price, Theta)
from pricebt.session import PricebtSession

s = PricebtSession.use(assets=["tests/assets/toy_usd_swaption.yaml"])   # your config here
svc = s.pricing
d1, d2 = date(2024, 1, 3), date(2024, 2, 5)
v = lambda inst, d, m: float(svc.value(inst, d, m, None))
dv01 = lambda inst, d: v(inst, d, IRDelta(aggregation_level="Type"))
```

`svc.resolve(inst, d, None)` gives the trade as it would be struck on `d`. Pass the resolved instrument to later dates to value the *same* trade.

## Swap probes

Start from `payer = IRSwap("Pay", "10y", "USD", 1e6)`.

| # | probe | how | expected | a miss usually means |
|---|---|---|---|---|
| S1 | **Par swap PV is about 0** | `r = svc.resolve(payer, d1, None); v(r, d1, Price)` | abs value below about 1e-4 × notional | ATM strike and valuation use different curves, dates or conventions |
| S2 | **Payer/receiver symmetry** | resolve `IRSwap("Receive", ...)` at the payer's fixed rate; compare PV and dv01 | exactly opposite | `pay_or_receive` ignored, or an asymmetric fee/spread term |
| S3 | **Roll-down sign** | value a resolved 10y payer at `d1` and later with the *same* market (if the library can hold the curve fixed) | a receiver gains rolling down an upward curve; a payer loses | maturity re-derived on each date, or time running backwards |
| S4 | **Holiday behaviour** | `svc.has_market(svc.asset_for(payer), date(2024, 7, 4), None)` | `False` (market returns `None`), never an exception | the library raises on holidays; wrap the market call and return `None` |
| S5 | **Seasoned trade remark** | resolve on `d1`, value after the first coupon or fixing; compare with the library's own report | same PV to the tolerance in [independent-validation.md](independent-validation.md) | past fixings missing, coupon paid twice or never, maturity drifting |
| S6 | **FX round trip** | `svc.fx("EUR", "USD", d1) * svc.fx("USD", "EUR", d1)` | exactly 1 | the reverse pair returns the same quote |
| S7 | **Quantity is linear** | `v(r.clone(quantity_=2), d2, Price)` against `2 * v(r, d2, Price)` | exactly double; `IRFwdRate` unchanged | wrong `unit:` or `scale_with_quantity:` |
| S8 | **Par rate ignores direction and size** | `v(r, d1, IRFwdRate)` for payer, receiver and 10× notional | identical | the par-rate function reads notional or direction |

## Swaption probes

Start from `sw = IRSwaption("Pay", "10y", "USD", notional_amount=1e6, expiration_date="1y", strike="ATM", buy_sell="Buy")`.

| # | probe | how | expected | a miss usually means |
|---|---|---|---|---|
| O1 | **Put-call parity** | payer and receiver at the same resolved strike K: `v(p, d2, Price) - v(q, d2, Price)` against `v(p, d2, Annuity) * (F - K)`, with F = `IRFwdRate` in decimal | equal to rounding | strike not decimal; `Annuity` not holder-signed N·A; `IRFwdRate` not the underlying's forward |
| O2 | **ATM straddle symmetry** | at the money on its trade date: payer Price = receiver Price, payer delta = −receiver delta ± the annuity term | equal premiums; deltas opposite and nearly equal in size | strike not the forward; a skew applied to one side only |
| O3 | **Vega matches a vol bump in your library** | reprice with the normal vol at the strike ±1bp (see the bump table below) | `IRVega` = (PV(+1bp) − PV(−1bp)) / 2, sign included | vega per 1% (×100), lognormal vega (needs F·σ_LN conversion), per −1bp |
| O4 | **Delta matches a curve bump** | shift your curve primitive ±h, reprice PV and the forward: `(PV₊ − PV₋) / (F₊ − F₋)` with F in bp | = `IRDelta` scalar to 1% | a fixed-annuity delta (at-the-money exact only), vol not held at the strike (sticky strike) |
| O5 | **Gamma matches the chain rule** | same bumps plus the base: `Γ = [n₊+n₋−2n₀ − Δ·(F₊+F₋−2F₀)] / ((F₊−F₋)/2)²` | = `IRGammaParallel` to 2% | the half-gamma trap (`ir_gamma_ratio` ≈ 0.5), a per-bump-primitive gamma |
| O6 | **Theta on a frozen world** | reprice at t+1 calendar day on the curve translated `DF(x)/DF(t+1d)`, same vol, T − 1/365 | = `Theta` (per day) | per year, per business day, a rolled curve |
| O7 | **Expiry behaviour** | a `"1m"` expiry, valued on the expiry date and a week later, ITM and OTM | no exception, no NaN; physically settled: ITM → the swap's PV, OTM → 0; `ExpiryInYears` 0 after | the library refuses expired options; the config must return 0 or the swap |
| O8 | **Buy/sell fold** | `buy_sell="Sell"` against `"Buy"` | every PV and greek negated; levels equal | `resolve` drops `buy_sell` |

## Bond probes

Start from `bd = Bond(identifier="<your identifier>", size=1e6, buy_sell="Buy")`.

| # | probe | how | expected | a miss usually means |
|---|---|---|---|---|
| B1 | **Price and yield round trip** | price the bond in your library at pricebt's `IRFwdRate` (converted to the library's yield convention) | the library's price = pricebt `Price` (dirty, per face amount) | clean vs dirty, per 100 vs per face, compounding convention of the yield |
| B2 | **dv01 matches a yield bump** | price at yield ±1bp in your library: `(P(y+1bp) − P(y−1bp)) / 2` | = `IRDelta` scalar = `LightningDV01`, negative for a long bond | risk per −1bp, per 100 face, a curve delta with dy/dz ≠ 1 |
| B3 | **Convexity** | `P(y+1bp) + P(y−1bp) − 2P(y)` | = `IRGammaParallel` (per bp²), positive | convexity per 100 or per (1%)², or halved |
| B4 | **Accrued on a coupon date** | the library's accrued the day before and on a coupon date | resets to ~0 on the coupon date; the dirty `Price` drops by the coupon | ex-coupon or record-date conventions differ; `Price` never drops (then `Cashflows` must be empty) |
| B5 | **Carry over one day with the yield fixed** | the library's price at t+1 at the *same* yield, plus any coupon paid in (t, t+1] | − P(t) = `Theta` | theta per year, a rolled curve instead of a fixed yield |
| B6 | **Cashflows** | the library's schedule for the trade | same dates and amounts as `Cashflows`, holder-signed, principal included | a missing principal row, amounts per 100 |

## Bump probes by library shape

Every bump probe above needs one primitive: "reprice this trade under a shifted market". Library shapes provide it differently. Use the same primitive your config uses, so the probe tests the config and not a second convention.

| library shape | the bump primitive | the forward/yield under the bump |
|---|---|---|
| bank platform / risk service (a Citi Datapoint-style or JPM Athena-style request) | a scenario or shift argument on the price request (parallel curve shift in bp), or a bumped market-data id | ask for the par rate / forward / yield in the same bumped request |
| QuantLib-style object library | a spread quote on a relinkable curve handle (zero-spreaded term structure), set to ±h and back; vol through a quote handle | the swap's fair rate / the bond's yield from the same bumped handles |
| rateslib-style object library | a shifted copy of the curve (a `shift` method) or bumped solver instruments | the instrument's `rate()` on the shifted curve |
| REST analytics service | a request field for a parallel shift, or two market snapshots | a second call for the rate on the same shifted market |
| bond analytics package | price-from-yield: P(y ± 1bp) directly | the yield itself (yield DV01 needs no curve) |

The per-measure recipes (total own-rate delta, chain-rule gamma, translated-curve theta, vol bumps for vega/vanna/volga, key-rate ladders, `'<tail>;<expiry>'` cube keys) are in [`../../pricebt-risk-measures/references/implementing-measures.md`](../../pricebt-risk-measures/references/implementing-measures.md).

## Record

Record the results next to the config, in its `description:` or in your research log. For each probe give its number, the expected answer, the observed answer and the date run.
