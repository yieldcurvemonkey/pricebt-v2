# Known-answer probes

Hand-run probes with an answer you know before you compute it. They cover what `skills/pricebt-verify-asset-config/scripts/check_asset.py` cannot see, and they work with any pricing library because they go through pricebt only. Run each once per asset, write down the expected answer first, then the observed one.

Setup, in a Python session from the repository root with `PYTHONPATH=src;tests` (POSIX `src:tests`):

```python
from datetime import date
from pricebt.instrument import IRSwap
from pricebt.risk import IRDelta, IRFwdRate, Price
from pricebt.session import PricebtSession

s = PricebtSession.use(assets=["tests/assets/toy_usd_irs.yaml"])   # your config here
svc = s.pricing
d1, d2 = date(2024, 1, 3), date(2024, 2, 5)
payer = IRSwap("Pay", "10y", "USD", 1e6)
dv01 = lambda inst, d: float(svc.value(inst, d, IRDelta(aggregation_level="Type"), None))
pv = lambda inst, d: float(svc.value(inst, d, Price, None))
```

`svc.resolve(inst, d, None)` gives the trade as it would be struck on `d`; pass the resolved instrument to later dates to value the *same* trade.

| # | probe | how | expected | a miss usually means |
|---|---|---|---|---|
| 1 | **Par swap PV is about 0** | `r = svc.resolve(payer, d1, None); pv(r, d1)` | abs value below about 1e-4 x notional | ATM strike and valuation use different curves, dates or conventions |
| 2 | **Payer/receiver symmetry** | resolve `IRSwap("Receive", ...)` at the *payer's* fixed rate (pass `fixed_rate=r.resolved_terms[...]` in your library's rate unit); compare PV and dv01 | PV and dv01 exactly opposite | `pay_or_receive` ignored, or an asymmetric fee/spread term |
| 3 | **dv01 matches a bump** (only if the library can bump its curve) | reprice with the curve shifted +1bp and -1bp in the library itself | dv01 about (PV(+1bp) - PV(-1bp)) / 2, sign included | dv01 per 1% or per -1bp; bucket shift vs parallel shift |
| 4 | **Roll-down sign** | on an upward-sloping curve, value a resolved 10y payer at `d1` and on a later date with the *same* market (if the library can hold the curve fixed) | a receiver gains as it rolls down an upward curve; a payer loses | effective/maturity dates re-derived on each date (see `resolve_pins_terms`), or time running backwards |
| 5 | **Holiday behaviour** | `svc.has_market(svc.asset_for(payer), date(2024, 7, 4), None)` for a real holiday of the currency | `False` (market returns `None`), never an exception | the library raises on holidays; wrap the market call in a `code:` helper that returns `None` |
| 6 | **Seasoned trade remark** | resolve on `d1`, value on a date after the first coupon or fixing; compare with the library's own report for that trade on that date | same PV to the tolerance in [independent-validation.md](independent-validation.md) | past fixings missing, coupon paid twice or never, maturity drifting |
| 7 | **FX round trip** | with an FX config: `svc.fx("EUR", "USD", d1) * svc.fx("USD", "EUR", d1)` | exactly 1 | the reverse pair returns the same quote instead of its reciprocal |
| 8 | **Quantity is linear** | `pv(r.clone(quantity_=2), d2)` against `2 * pv(r, d2)` | exactly double; intensive measures (`IRFwdRate`) unchanged | wrong `unit:` or `scale_with_quantity:` |
| 9 | **Par rate is independent of direction and size** | `svc.value(r, d1, IRFwdRate, None)` for payer, receiver and 10x notional | identical | the par rate function reads notional or direction |

Record the results next to the config (for example in its `description:` or in your research log): probe number, expected, observed, date run.
