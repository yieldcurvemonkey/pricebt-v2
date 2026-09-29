# Convention conversion table

pricebt's side is fixed; see [`docs/v2/ASSET_CONFIG_GUIDE.md`](../../../docs/v2/ASSET_CONFIG_GUIDE.md) "Units". Your library's side is
whatever the [discovery questionnaire](discovery-questionnaire.md) found. Write the conversion
**on the line that does it**, with a comment `# vendor: X -> pricebt: Y`.

## What pricebt expects

| Thing | pricebt / gs convention |
|---|---|
| `fixed_rate` kwarg and resolved strike | **decimal** (0.0385 = 3.85%) |
| `'ATM+25'` / `'ATM-10'` | offset in **bp** from the par rate at the trade date |
| `npv` (`unit: ccy`) | PV in the asset's `currency:`, holder's view, for ONE unit (quantity 1) |
| `dv01` (`unit: ccy_per_bp`) | PV change for a **+1bp** parallel move. Payer swap **> 0** |
| `par_rate` (`unit: bp`) | basis points (385.0) |
| bucketed ladder (`returns: buckets`) | `{"2Y": x, "5Y": y, ...}`, same unit and sign as `dv01`. Sums to the parallel curve delta |
| resolved notional (internal to your config) | signed, payer > 0 (the toy and ARBS configs use this) |
| dates in `resolved` | `datetime.date`, absolute, pinned at the trade date |
| "no market today" | `market.expr` returns `None` (never raises) |
| `IRDelta` scalar (contract) | `ccy_per_bp` per bp of the instrument's **own** rate (par rate, forward swap rate, yield); payer swap > 0, payer swaption > 0, **long bond < 0** |
| `IRGammaParallel` | `ccy_per_bp2`, the full second derivative per bp² of the own rate |
| `IRVega` / `IRVanna` / `IRVolga` | per bp (bp²) of **normal** vol; a long option's vega > 0 |
| vol levels (`IRAnnualImpliedVol`, ...) | normal vol, intensive; the shipped configs use **bp** (80.0 = 0.80%) |
| `Theta` | `ccy` **per calendar day** (never per year) |
| `strike` kwarg and resolved strike (swaption) | **decimal**; `'A-50'` / `'ATM+25'` offsets in bp |
| `buy_sell` (swaption, bond) | folded by `resolve` into the signed notional or face (bought > 0) |
| bond PV | holder-signed **dirty** PV in ccy for the resolved face (dirty price / 100 x face) |

## Vendor convention to pricebt convention

| Vendor says | pricebt wants | Conversion | Sanity number (10y USD swap, 1mm) |
|---|---|---|---|
| rate in decimal (0.0385) | bp | `x * 1e4` | ~385 |
| rate in percent (3.85) | bp | `x * 100` | ~385 |
| rate in bp (385) | bp | none | ~385 |
| strike input in percent | from the resolved decimal | `k * 100` in `build` | |
| strike input in bp | from the resolved decimal | `k * 1e4` in `build` | |
| DV01 per 1bp **down** (receiver > 0) | per +1bp, payer > 0 | `-x` | payer ~ +900 |
| PV01 / "risk" per 1bp **up** (payer > 0) | same | none | payer ~ +900 |
| DV01 as a positive magnitude (unsigned) | signed | `x * (1 if payer else -1)` | |
| delta per 1% (100bp) | per bp | `x / 100` | ~90,000 → ~900 |
| delta per unit rate (dPV/dr) | per bp | `x * 1e-4` | ~9,000,000 → ~900 |
| risk per unit notional | total | `x * abs(notional)` | ~0.0009 → ~900 |
| bucket key `"USD.SOFR:2Y"` | `"2Y"` | `k.split(":", 1)[1]` | |
| bucket key `("USD", "2Y")` | `"2Y"` | `k[1]` | |
| bucket key `"24M"` / `"2y"` | `"2Y"` | a normalising map; **raise** on unknown | |
| ladder as a DataFrame | dict | `{str(i): float(v) for i, v in s.items()}` | |
| notional > 0 plus `direction` | signed, payer > 0 | `abs(n)`, `"PAY" if n > 0 else "RECEIVE"` in `build` | |
| buy/sell or long/short | signed | map explicitly, and state **which leg** | |
| ISO date string | `date` | `date.fromisoformat(s)` out; `d.isoformat()` or `str(d)` in | |
| `datetime` / `Timestamp` | `date` | `.date()` | |
| Excel serial | `date` | `date(1899, 12, 30) + timedelta(days=n)` | |
| raises on a holiday | `None` | `try/except <ThatError>: return None` (narrow `except`) | |
| returns the previous day's curve | `None` or error | check the curve's own ref date `== d` | |
| tenor `end` relative to the pricing date | absolute date | `resolve` calls the library's maturity helper at the trade date | |
| strike `None` = at par | decimal strike | `resolve` prices the par rate once | |
| PV in a reporting currency | asset currency | set `currency:` to what the function returns, or convert | |

## Vols, higher-order greeks, theta and bonds

Sanity numbers are for a 1mm USD 1y10y ATM swaption (forward F about 4%, normal vol about 80bp,
annuity N·A about 8.2mm) and a 1mm 10y 4.25% bond.

| Vendor says | pricebt wants | Conversion | Sanity number |
|---|---|---|---|
| normal vol in decimal (0.0080) | `bp` | `x * 1e4` | ~80 |
| normal vol in percent (0.80) | `bp` | `x * 100` | ~80 |
| lognormal (Black) vol (0.20) | **normal** vol in bp | not a rescale. Exact: `bachelier_implied_vol(PV / annuity, F, K, T, is_payer) * 1e4` (swaption template). At the money only: `σ_LN * F * 1e4` | 0.20 x 0.04 → ~80 |
| vega per 1% lognormal | `ccy_per_bp` of normal vol | at the money: `x * 0.01 / F`; away from it, to leading order: `x * 0.01 * ln(F/K) / (F - K)`. Better: bump the normal vol (`vol_bump_vega`) | ~1300 → ~330 |
| vega per 1% normal (100bp) | per bp | `x / 100` | ~33,000 → ~330 |
| vega per unit vol (dPV/dσ, σ decimal) | per bp | `x * 1e-4` | ~3.3mm → ~330 |
| gamma per unit rate² (d²PV/dF², F decimal) | `ccy_per_bp2` | `x * 1e-8` | ~4 per bp² |
| gamma per 1% move² | per bp² | `x / 1e4` | |
| "gamma" = change in an annuity pv01 per bp | the full second derivative | **not a conversion**: at the money it is half the gamma. Use `own_rate_gamma` | |
| theta per year | per **calendar** day | `x / 365` (never `/ 252`) | ATM 1y10y: about -36 per day |
| theta per business day, or with the curve **rolled** (constant-curve roll-down) | one calendar day, forwards fixed | not a rescale: recompute with `lib_translate` | |
| bond clean price per 100 | holder-signed dirty PV in ccy | `(clean + accrued) / 100 * face` | ~1mm |
| bond DV01 per 100 face, positive magnitude | `ccy_per_bp`, long < 0 | `-x * face / 100` | about -800 |
| modified duration D | `ccy_per_bp` | `-D * dirty_pv * 1e-4` | D ~ 8 → about -800 |
| convexity C | `ccy_per_bp2` | `C * dirty_pv * 1e-8` | C ~ 75 → about 0.75 |
| yield in percent, semiannual street | the own rate in `bp` | `x * 100`, and `lib_pv_at_yield` must invert the same convention | ~425 |
| gs `IRFwdRate` / `IRAnnualImpliedVol` read in a notebook | the config's declared unit | gs returns decimals (despite "in percent" in its docstrings); the shipped pricebt configs return bp, so delete a notebook's `* 1e4` | |

## Signs: a 30-second self-test

1. Build a **payer** ATM 10y swap. Its `npv` should be about 0, with `|npv| < 1e-6 * notional`.
2. Its `dv01` must be **> 0** and about 0.09% of notional per bp for a 10y (roughly 900 per 1mm).
3. A receiver has exactly `-dv01`.
4. The ladder sums to `dv01` (to 1e-6 relative if the vendor's buckets are exact; a few % if they are par-rate buckets of a zero-rate scalar, so say which).
5. The same resolved payer priced 3 months later: `npv(d2) - npv(d1) ≈ dv01 * (par(d2) - par(d1))` in bp. The sign must match.

**A swaption** (bought ATM payer, 1y10y):

6. `npv > 0`, the delta is > 0 and about half the forward swap's delta (N·A x 1e-4), and vega is
   > 0 (about 330 per 1mm). Gamma > 0 and Theta < 0.
7. `buy_sell="Sell"` gives exactly `-npv` and `-vega`. A `Straddle` is exactly payer + receiver.
8. Parity: `npv(payer, K) - npv(receiver, K)` equals the forward swap's PV at strike K.
9. After expiry: vega, vanna and volga are exactly 0.0, and the levels are finite.

**A bond** (long 1mm 10y):

10. `npv` is about dirty price x face / 100, the delta is **< 0** (about -800 per bp) and gamma > 0.
11. `Theta` is about `dirty PV x yield / 365` per day. For a continuously compounded yield between
    coupons it is exactly `dirty PV x (exp(y / 365) - 1)`, because it is total return at a constant
    yield. On the day before a coupon, the coupon is in Theta's cash term.
12. `buy_sell="Sell"` negates every extensive measure; the yield, spreads and `ExpiryInYears` are
    unchanged.
