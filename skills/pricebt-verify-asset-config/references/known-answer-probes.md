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

Start from `bd = Bond(identifier="<your identifier>", size=1e6, buy_sell="Buy")`. A bond's `Price` is its settlement-date market value: (clean + accrued at standard settlement) × face / 100, holder-signed, **not** discounted to the pricing date. The bond measures are imported the same way: `from pricebt.risk import AccruedInterest, CleanPrice, DirtyPrice, ModifiedDuration, Convexity, DaysToSettlement, RepoRate, RepoHaircut, FinancingToDate, ForwardPrice, Carry, RollDown`.

| # | probe | how | expected | a miss usually means |
|---|---|---|---|---|
| B1 | **Price and yield round trip** | price the bond in your library at pricebt's `IRFwdRate` (converted to the library's yield convention) for standard settlement | the library's invoice amount = pricebt `Price` (dirty, per face amount, at settlement) | clean vs dirty, per 100 vs per face, compounding convention of the yield, a value discounted to the trade date |
| B2 | **dv01 matches a yield bump** | price at yield ±1bp in your library: `(P(y+1bp) − P(y−1bp)) / 2` | = `IRDelta` scalar = `LightningDV01`, negative for a long bond | risk per −1bp, per 100 face, a curve delta with dy/dz ≠ 1 |
| B3 | **Convexity** | `P(y+1bp) + P(y−1bp) − 2P(y)` | = `IRGammaParallel` (per bp²), positive; `1e8 × IRGammaParallel / Price` = `Convexity` | convexity per 100 or per (1%)², or halved |
| B4 | **Accrued and the coupon drop** | the library's accrued and invoice amount on the trade dates around a coupon | `AccruedInterest` resets to ~0 once settlement reaches the coupon date; `Price` drops the coupon on the trade date whose settlement is on or after the coupon date (T+1: the business day before a business-day coupon date), which is the `Cashflows.payment_date` | ex-coupon or record-date conventions differ; the drop placed on the coupon date itself (settlement ignored) |
| B5 | **Theta with the yield fixed** | from t to the next business day nb: the library's invoice at nb at the *same* yield, plus any coupon `Price` drops in (t, nb], minus P(t), divided by the calendar days (nb − t) | = `Theta` (per calendar day; a Friday spreads the step over 3 days) | theta per year, per business day, a rolled curve instead of a fixed yield, financing included (it belongs in `FinancingToDate`) |
| B6 | **Cashflows** | the library's schedule for the trade | same amounts as `Cashflows`, holder-signed, principal included; `payment_date` the drop date of B4 | a missing principal row, amounts per 100, the coupon date used as `payment_date` |
| B7 | **Clean, dirty, accrued** | the library's clean price and accrued for standard settlement | `CleanPrice` = the quoted clean price; `CleanPrice + 100 × AccruedInterest / face = DirtyPrice = 100 × Price / face`; `FairPremium = Price`; `PremiumCents` (pct) `= DirtyPrice` | accrued per 100 instead of in currency, an unsigned face, a clean price returned as `Price` |
| B8 | **Settlement lag** | `DaysToSettlement` on a Monday, a Friday and the day before a holiday | US Treasuries T+1: 1, 3, and the calendar days to the next good day | business days counted, the wrong calendar, scaling with size |
| B9 | **One day of repo** | `FinancingToDate` on the trade date and the next business day | 0, then `−(1 − h) × Price(t₀) × RepoRate × days / basis` for a long (days between the two settlement dates; basis 360 for USD repo); the opposite sign for a short | compounding, trade-date day counts, the principal re-read daily instead of pinned at resolve, the haircut applied the wrong way |
| B10 | **Forward parity** | recompute `Price × (1 + RepoRate × τ(s, H)) − Σ C × (1 + RepoRate × τ(c, H))`, H = settlement + 1 calendar month (following business day), τ in the repo day count | = `ForwardPrice` | a forward to maturity, coupons in (s, H] not netted, the bond's day count used for τ |
| B11 | **Carry and roll-down on a flat curve** | a flat curve at the yield, repo set to the yield in the same compounding, a horizon with no coupon | `Carry + RollDown ≈ 0`; `Carry = (Price − AccruedInterest) − (ForwardPrice − accrued at H)` | carry on the dirty price, roll-down on a re-fitted curve, a horizon other than H |
| B12 | **Repo source** | `RepoRate` on a date when the bond trades special, against the desk's repo screen | the special rate if the config finances at special, else general collateral; a term rate stays at its trade-date level | GC used for a special issue (financing overstated), an overnight fixing where the trade is term |

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
