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
| bucketed ladder (`returns: buckets`) | `{"2Y": x, "5Y": y, ...}`, same unit and sign as `dv01`. Sums to `dv01` |
| resolved notional (internal to your config) | signed, payer > 0 (the toy and ARBS configs use this) |
| dates in `resolved` | `datetime.date`, absolute, pinned at the trade date |
| "no market today" | `market.expr` returns `None` (never raises) |

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

## Signs: a 30-second self-test

1. Build a **payer** ATM 10y swap. Its `npv` should be about 0, with `|npv| < 1e-6 * notional`.
2. Its `dv01` must be **> 0** and about 0.09% of notional per bp for a 10y (roughly 900 per 1mm).
3. A receiver has exactly `-dv01`.
4. The ladder sums to `dv01` (to 1e-6 relative if the vendor's buckets are exact; a few % if they are par-rate buckets of a zero-rate scalar, so say which).
5. The same resolved payer priced 3 months later: `npv(d2) - npv(d1) ≈ dv01 * (par(d2) - par(d1))` in bp. The sign must match.
