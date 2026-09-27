# Errors, retries, time and determinism

## 1. pricebt understands only its own errors (executed)

`Engine._guarded` (the wrapper of marks, executions, layer flushes, signals, scheduled exits and recorded measures) catches `PricebtError` and nothing else. The binding layer rewraps only a `TypeError`
(`call_binding`), and the evaluation layer only a `MethodCallError` (into `MeasureError`, for measures and layers). So a raw `TimeoutError`, `ConnectionError`, `RuntimeError` or the library's own exception class
that reaches the engine is NOT recorded: it aborts the run, even under `backtest.on_error: record`. On the worked example, with one `pv` call made to raise in the middle of the base config:

```python
"""A RAW (non-pricebt) exception raised inside a bound method: does `backtest.on_error: record` catch it?  Known answer first: a pricebt error IS recorded."""
import contextlib
import io

import acmelib
import acme_adapter._compat as compat
from pricebt import api
from pricebt.errors import MarketDataUnavailable

EX = "skills/pricebt-wire-external-library/example/config/"
BASE, OVERLAY = EX + "acme_tieout_base.yaml", EX + "acme_swap.yaml"
REAL_PV, REAL_TRANSLATE = acmelib.pv, compat.translate


def flaky(exc):
    calls = {"n": 0}

    def pv(*a, **k):
        calls["n"] += 1
        if calls["n"] == 30:  # one call in the middle of the run
            raise exc
        return REAL_PV(*a, **k)

    return pv


def run(label, exc, translate=None):
    acmelib.pv = flaky(exc)
    if translate is not None:
        compat.translate = translate
    try:
        res = api.run(BASE, sets=["backtest.on_error=record"], stack=[OVERLAY])
        print(f"{label}: run COMPLETED, {res.n_errors} recorded error(s):", list(res.errors["error"])[:2])
    except BaseException as e:  # noqa: BLE001
        print(f"{label}: run ABORTED with {type(e).__module__}.{type(e).__name__}: {e}")
    finally:
        acmelib.pv, compat.translate = REAL_PV, REAL_TRANSLATE


@contextlib.contextmanager
def translate_network_errors(ts=None):
    """the adapter-side fix: every foreign exception becomes a pricebt error"""
    try:
        with REAL_TRANSLATE(ts):
            yield
    except (TimeoutError, ConnectionError) as e:
        raise MarketDataUnavailable(ts, {}, f"remote service: {type(e).__name__}") from e


run("control, a pricebt error (MarketDataUnavailable)", MarketDataUnavailable(None, {}, "known answer: recorded"))
run("raw TimeoutError, adapter maps only acmelib's own errors", TimeoutError("gateway timeout"))
run("raw TimeoutError, adapter maps it to MarketDataUnavailable", TimeoutError("gateway timeout"), translate=translate_network_errors)
```

```
control, a pricebt error (MarketDataUnavailable): run COMPLETED, 1 recorded error(s): ['MarketDataUnavailable: market data unavailable at None: known answer: recorded']
raw TimeoutError, adapter maps only acmelib's own errors: run ABORTED with builtins.TimeoutError: gateway timeout
raw TimeoutError, adapter maps it to MarketDataUnavailable: run COMPLETED, 1 recorded error(s): ['MarketDataUnavailable: market data unavailable at 2024-05-28 17:00:00-04:00: remote service: TimeoutError']
```

The control (a `MarketDataUnavailable`, a pricebt error) is recorded and the run completes; the raw `TimeoutError` aborts; the same `TimeoutError` mapped by the adapter is recorded. This is THE reason
every adapter has an error-translation context manager around every library call (`_compat.translate` in each shipped adapter): it is the only place where a foreign exception becomes a pricebt one.

**Two paths catch everything, so a tie-out can complete with holes.** The audit (`Engine._audit_mark`) and the baseline decomposition (`Engine._baseline_open`, `_baseline_rows`, `_baseline_failed`) catch `Exception`,
not only `PricebtError`: a raw exception inside an AUDITED measure or a baseline measure is recorded (a NaN cell and one error row, `where: audit` or `where: baseline`) under EVERY `on_error` mode, including `raise`,
and the run COMPLETES (executed: a raw `TimeoutError` raised in the 5th `bucket_risk` call with `audit_measures=("delta_ladder",)` gave `audit TimeoutError: gateway timeout (raw)` under `record` and under `raise`).
So a tie-out that reports no abort is not proof that nothing failed: check `n_errors` and `result.errors` (`run_tieout(...).results[<stack>].errors`), and still translate errors in the adapter so the text is sanitised.
`n_errors` is not always 0 even on a correct wiring: the worked example's base has a swap that matures inside the window, its `rate` is NaN after maturity and the engine records `MeasureError: measure 'rate' is not
finite (nan)` once for the baseline of that position and once per audited mark after the maturity (`where: audit`), identically on both stacks. Compare `n_errors` between the stacks and read the rows.

## 2. The mapping (the adapter's decision, one line each; write it in section 8 of the conventions document)

| what the platform does | raise | why | what the engine does |
|---|---|---|---|
| timeout, connection reset, 5xx, rate limit, "try later" (after your bounded retries) | `MarketDataUnavailable(ts, request, reason)` | transient: the data or the answer is not available NOW | recorded under `record` (a stale mark, below); abort under `raise` |
| a fixing, curve, calendar or trade that the platform does not have | `MarketDataUnavailable` | the same class as a missing fixing in the shipped adapters | same |
| authentication failed or expired, permission or entitlement denied, licence unavailable | `ConfigError(..., code="CFG-AUTH")` (or `CFG-ENTITLEMENT`) | permanent: retrying changes nothing; the fix is outside the run | same: pricebt has no error the engine treats as fatal under `record`, so run with `on_error: raise` (the default) until `n_errors` is 0 |
| the platform rejects an input (unknown convention, unsupported product, a bad date) | `ConfigError(..., code="CFG-TERMS")` or `CFG-CONVENTION-UNSUPPORTED` | it is a wrong request, not a flaky service | same |
| the platform cannot compute this measure for this trade | `MethodCallError` from a bound method (becomes `MeasureError`), or `MeasureError` | one measure failed, the run may continue | recorded (`where: measure`, `layers`, `audit`) |
| anything else the library raises | `MethodCallError(f"<lib>: {type}: {sanitised message}")` | an unknown error must never escape as a raw exception | same |

Rules for the wrapper: catch the library's exception classes and the transport's (`OSError` covers `TimeoutError` and `ConnectionError`); keep the original as `__cause__` (`raise ... from e`);
SANITISE the message (URLs, user names, request bodies, tokens: errors are written to `errors.parquet`, the tearsheet and the tie-out report); never catch `PricebtError` itself (it would hide
a real pricebt error), and never swallow an exception into a default value (`Never replaced by a silent empty result` is the contract of `MarketDataUnavailable`).

## 3. Retries

pricebt has none. Retry INSIDE the adapter, and only when it cannot change a result: idempotent READS (a curve fetch, a price of an existing environment), a small fixed number of attempts, a bounded
back-off, counted (`RecordedClient.n_live` counts the calls that were made). Never retry an upload that is not idempotent by digest, never retry a permission error, and turn the last failure into
`MarketDataUnavailable`. A retry changes the cost of a run (section "Latency" of `cost-budget.md`), never its numbers.

## 4. What a failed mark does under `on_error: record` (executed)

`Engine._record` books `sum(quantity x last_pv)`: a position whose mark failed keeps its PREVIOUS mark for that row. On the worked example with one mark made to fail (marks only, `record`); the script
(`PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example"`, repository root) patches the library's `pv` so that its 30th call raises a pricebt error:

```python
"""What `backtest.on_error: record` does with a MARK that fails: the position keeps its previous mark for that point."""
import acmelib
import pandas as pd

from pricebt import api
from pricebt.errors import MarketDataUnavailable

pd.set_option("display.width", 200)
EX = "skills/pricebt-wire-external-library/example/config/"
BASE, OVERLAY = EX + "acme_tieout_base.yaml", EX + "acme_swap.yaml"
SETS = ["backtest.on_error=record", "backtest.attribution={}", "backtest.measures=[]"]  # marks only, so the failing call is a mark
REAL_PV = acmelib.pv

clean = api.run(BASE, sets=SETS, stack=[OVERLAY])
calls = {"n": 0}


def flaky(*a, **k):
    calls["n"] += 1
    if calls["n"] == 30:
        raise MarketDataUnavailable(None, {}, "gateway timeout (simulated)")
    return REAL_PV(*a, **k)


acmelib.pv = flaky
try:
    bad = api.run(BASE, sets=SETS, stack=[OVERLAY])
finally:
    acmelib.pv = REAL_PV
print("recorded errors:\n", bad.errors.to_string(index=False))
d = (bad.equity["positions_value"] - clean.equity["positions_value"])
print("positions_value, run with the failed mark minus clean run, rows that differ:\n", d[d.abs() > 1e-6].round(2).to_string())
print("final equity: clean %.2f, with the failed mark %.2f" % (clean.equity["equity"].iloc[-1], bad.equity["equity"].iloc[-1]))
```

```
recorded errors:
                        ts where position                                                                               error
2024-06-07 17:00:00-04:00  mark  P000003 MarketDataUnavailable: market data unavailable at None: gateway timeout (simulated)
positions_value, run with the failed mark minus clean run, rows that differ:
 ts
2024-06-07 17:00:00-04:00   -157.81
final equity: clean 51552.27, with the failed mark 51552.27
```

One recorded error (`where: mark`, the message with the exception class), a row where `positions_value` is stale by -157.81, and a final equity identical to the clean run because the next successful mark
restores it. So `record` is for exploration: a run with `n_errors > 0` is not a run to tie out (the tie-out reports `n_errors` in `L4.stats`, and `L1.marks_rows` as `structure` when the rows differ).
A failing stack aborts `run_tieout` for every stack unless the BASE sets `backtest.on_error: record` (`pricebt-conformance-and-tieout`).

## 5. Time, no look-ahead and determinism

Contracts you must keep (real messages, executed):

```
LookAheadError: pricer requested for 2024-03-05 17:00:00-05:00 but the clock is at 2024-03-04 17:00:00-05:00
LookAheadError: provider returned a snapshot stamped 2024-03-05 17:00:00-05:00 for a request at 2024-03-04 17:00:00-05:00
ConfigError: [SNAPSHOT] a snapshot stamp must be tz-aware
ConfigError: [SNAPSHOT] fixings 'f' run to 2024-03-04: only fixings strictly before the reference date 2024-03-04 exist at the snapshot
```

* The guard is in `MarketData.pricer`: a request later than the clock, or a snapshot stamped later than the request, is an error. It sees only `SnapshotPricer.ts`, so the provider must STAMP the snapshot
  with the time the data was actually knowable, not the time you asked.
* **A remote platform has an implicit "now".** A default "latest" curve, a default valuation date of today, a server clock: every request must carry the explicit as-of date or time, and the provider must
  REFUSE an answer whose own timestamp is later than the request. Probe it: request the same as-of twice, days apart, and compare digests (the recorded mode makes it a test).
* **Knowledge time versus valuation time.** A curve for date D may have been revised after D. A backtest must see the version knowable at D: use the platform's as-known-at parameter if it has one, else record
  the snapshot at the time and treat later revisions as new snapshots. `TimeMapping` (`pricebt.pricer`): `asof` (latest stamp <= ts within `max_staleness`), `exact`, `date` (with `eod_visible_at`: an
  end-of-day stamp becomes visible only from that local time). Say which one your data needs.
* **Time zones.** Stamps are tz-aware `pandas.Timestamp`; the default zone is `America/New_York`; the digest encodes UTC nanoseconds. Convert the platform's stamps to tz-aware at the provider boundary,
  never compare naive and aware times, and let DST days have 23 or 25 hours (the shipped intraday grids are DST-safe).
* **Determinism.** No wall-clock in logic; positions are numbered sequentially; two runs are bit-identical (DESIGN section 6). A platform that returns numbers that wobble (a Monte-Carlo seed, a parallel
  reduction order, a cache warming) breaks that. The check, executed on the worked example: tie a stack against ITSELF (two stacks with the same overlay); every row must be `exact`:

```python
"""determinism.py: does YOUR stack reproduce itself? Two stacks with the SAME overlay, compared level by level: every row must be `exact`. Run from the repository root."""
import sys
from collections import Counter

from pricebt.tieout import run_tieout

EX = "skills/pricebt-wire-external-library/example/config/"
BASE, OVERLAY = EX + "acme_tieout_base.yaml", EX + "acme_swap.yaml"          # LIB: your base and your stack overlay
res = run_tieout(BASE, {"first": [OVERLAY], "second": [OVERLAY]}, reference="first", selftest=False,
                 audit_measures=("dv01", "gamma", "rate", "delta_ladder"))
rep = res.reports["first_vs_second"]
counts = Counter(r.status for r in rep.rows)
print("statuses of every row:", sorted(counts), "| passed:", rep.passed)
sys.exit(0 if set(counts) == {"exact"} else 1)
```

```
statuses of every row: ['exact'] | passed: True
```

A stack whose `pv` answers wobble by 1e-6 (the mutant of this check) prints `statuses of every row: ['exact', 'exceeds', 'noise'] | passed: False` and exits 1.
