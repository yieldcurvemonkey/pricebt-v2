# ADR 007: A dependency-free reference stack

Status: accepted, built (`src/pricebt/testing/refstack.py`). Requirements A4, X5, SC10, SC11.

## Context

Two libraries that agree may still share a mistake, and two that disagree need a third opinion. Something must be derivable by hand from the snapshot.

## Decision

`pricebt.testing.refstack` is an adapter that happens to need no library (numpy, pandas and the standard library only). It provides `swap` (an overnight-indexed swap: closed-form
schedule, PV, par rate, a bootstrapped delta ladder, layers), `bond` (fixed-coupon, treasury price/yield rule, coupon sweep, repo financing, yield-space layers), `wrap`, and a `STACK`.

* It implements the **shared conventions vocabulary** and says what it does not support (`ConfigError` naming the value and the supported set).
* Its layers use the definitions of ADR 005 and reproduce the golden case to six decimals; its ladder is a per-pillar bootstrap (Newton from the seed, bisection as the safety net).
* It ships **no default instrument spec** and no calendar name: a spec needs a calendar name, which is the snapshot provider's data, so the shared blocks `USD_SOFR_OIS_CONVENTIONS` and
  `UST_CONVENTIONS` omit `calendar` and the caller adds it (`{**USD_SOFR_OIS_CONVENTIONS, "calendar": "<a calendar of the snapshot>"}`). This keeps a banned token out of core.
* It is allow-listed in guard Z4 (it implements conventions by definition) and nowhere else.

## Measured agreement (rateslib against the reference stack, on the golden world)

`pv` to ~1e-9, layers to ~1e-10, the delta ladder to ~1.9e-5 of the largest bucket (rateslib solver tolerance), par-space gamma to ~1e-4 relative. On the real fixtures (ADR 006, the ledger)
the reference stack and rateslib agree to 1.3e-9 in pv and 3e-8 in dv01 on the swap carry run, and to 4e-15 in pv on the financed bond; the two libraries agree with each other to the same order.
The one systematic difference is not the reference stack's: QuantLib's `unexplained` (finite-difference step h = 0.1).

## Enforcement

`tests/test_refstack*.py` (six files, mutation-checked: solver 9 mutants, closed forms, layers), the layer conformance kit, and the harness self-test.
The kit found a real defect in the reference stack itself (a discount factor requested for past pay dates on a payment date, change log section 11).
