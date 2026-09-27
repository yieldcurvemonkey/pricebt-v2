# ADR 004: Market data is plain-data snapshots; `wrap` builds the library object

Status: accepted, built (`src/pricebt/snapshot.py`, `market.py`, `contrib/*/wrap.py`, `testing/synthetic.py`, `tests/support/`). Requirements D1-D8, SC7.

## Context

The stores that read the maintainer's data also built rateslib pricers, so market data and a pricing library were one object, and a second library could not consume the same data.

## Decision

* A **snapshot** is immutable, pickle-safe plain data: discount factors at node dates, published fixings, bond quotes with reference data and on-the-run aliases, calendars as
  holiday sets. Nothing else. Spot lags, day counts, calendar names and yield rules are conventions and live in the instrument spec.
* A provider (`get_pricer(ts, request)`) returns a `SnapshotPricer` (library-free, in core). The pricer role's `wrap: "pkg:fn"` turns it into the adapter's pricer **once per digest**
  (memoised in `MarketData`); this is the only place a library object is built from data. The digest is recorded before the wrap (audit level L0).
* `digest()` is a sha256 over a canonical byte encoding (big-endian IEEE-754 doubles, day ordinals, UTC nanoseconds, length-prefixed strings, names sorted): independent of `hash()`
  randomisation, mapping order and provenance, so two runs can prove they consumed identical inputs.
* Interpolation and value kinds are tags. An adapter maps a tag to its native interpolation or errors (never a silent substitute). Evidence: rateslib `log_linear` and QuantLib
  `DiscountCurve` agree bitwise at whole-day dates on the same node table.
* The package ships no provider, store or vendor reader (D6). The fixture-backed providers are test support (`tests/support/`, no pricing-library import), plus the library-free
  `testing/synthetic.py`.

## Consequences

The old stores were deleted after an equivalence run (recorded in the change log, section 7: 41,740 curve rows, 8,328 day classifications and 33,656 minutes with identical
served/dropped sets; the deliberate differences are listed there).

## Enforcement

`tests/test_snapshot.py` (all mutants killed), `tests/test_market_wrap.py`, `tests/test_support_*.py`, `tests/test_snapshot_wrap_rateslib.py`, `tests/test_quantlib_wrap.py`; guard GT-Z1 keeps the libraries out of core.
