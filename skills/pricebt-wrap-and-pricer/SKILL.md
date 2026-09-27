---
name: pricebt-wrap-and-pricer
description: "Use when a pricebt market snapshot must become the objects of an external library (writing wrap(SnapshotPricer) -> pricer, loading snapshot holidays and fixings into it, guarding its process-global state such as the valuation date, calendar registry and fixings store, translating its exceptions), or when the error says the market pricer is a SnapshotPricer, not a <Lib>Pricer."
---

# pricebt-wrap-and-pricer

## Purpose

`wrap(SnapshotPricer) -> <Lib>Pricer` is the ONE place a plain-data snapshot becomes `<LIB>` objects. The pricer it returns is what your instruments read on every mark; it is
immutable, built once per snapshot, and it is the only home of `<LIB>`'s process-global state and exception types (both live in `_compat.py`).

## Prerequisites

* A provider that returns `SnapshotPricer`s (skill `pricebt-market-data-snapshots`). `<LIB>` = the external library; "Q" items are yours to answer from its documentation.
* Models to open first: `src/pricebt/contrib/quantlib/wrap.py` and `_compat.py` (lazy, frozen, private memo), `skills/pricebt-wire-external-library/example/acme_adapter/wrap.py` and
  `_compat.py` (eager build, weak module memo), `src/pricebt/contrib/rateslib/wrap.py` and `_compat.py`, `src/pricebt/pricer.py` (`Pricer`, `PricerBase`), `src/pricebt/market.py`
  (`MarketData._wrap`), `docs/design/adr/004-snapshots-and-wrap.md`. A SERVICE that prices only from a market you upload: `skills/pricebt-wire-external-library/example-service/zeta_adapter/wrap.py` (the upload is lazy, once per
  snapshot, memoised on the wrapped pricer; derived markets for the layers are content-addressed there).
* References: needed are `references/library-state-and-errors.md` (S1 to S10; for a service whose client holds no process-global state S1 to S5 are one line each and S6, S7, S10 carry the work) and `references/tests-template.md` (its "Service variant" for a service). Optional:
  `references/pricer-skeleton.md` (a full in-process skeleton: skip it when you copy `example/acme_adapter/wrap.py`, or for a service `example-service/zeta_adapter/wrap.py`).
* Runtime: the repository root as working directory; `PYTHONPATH=src;tests;<parent directory of your adapter package>` (PowerShell: `$env:PYTHONPATH = "src;tests;..."`; `:` separators on Linux;
  pricebt adds no path itself: `python -m pricebt` puts the working directory on it, a script does not, [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)); a Python 3.11+ that has pricebt's dependencies. `python` below means that interpreter (in this checkout: `C:/Users/chris/anaconda3/envs/stir/python.exe`).

## Steps

1. **Answer the state questions S1-S10** of `references/library-state-and-errors.md` for `<LIB>`: is there a global valuation date, a calendar registry, a fixings store, another result-changing
   switch? Which exceptions? Which failures are silent? Thread-safe? Date horizon? Units and spellings? Write the answers as the docstring of `_compat.py`.
2. **Create `<lib>_adapter/_compat.py`**, the ONLY module that imports `<LIB>`, touches its globals and catches its exceptions (full executed skeleton: `references/pricer-skeleton.md`):
   `try: import <LIB> as lib` / `except ImportError as e: raise OptionalDependencyError(...) from e`; `to_date(d) -> dt.date`; `guard(date)` (context manager: set, `yield`, restore in `finally`,
   also "unset"); `translate(ts)` (context manager: library exceptions -> pricebt errors); `load_calendar(CalendarData) -> str`.
3. **Create `<lib>_adapter/wrap.py`**: a class derived from `pricebt.pricer.PricerBase` and the function `wrap`. The contract of the wrapped pricer:

   | attribute / method | required by | note |
   |---|---|---|
   | `ts` (tz-aware `pd.Timestamp`) | the `Pricer` protocol; `pricebt run --dry-run` prints it | copy `source.ts`: the pricer's own stamp, which is the SNAPSHOT stamp for the shipped readers (`SnapshotPricer(snapshot)`), and the request time only when a provider passes `ts=` (executed: a request at 20:00 served by the 17:00 row gives `ts` 17:00, wrapped or not) |
   | `reference_date` (`dt.date`) | your instruments (marks, cash windows, `started`/`expired`); `pricebt run --dry-run` | copy `source.reference_date` |
   | `digest` | audit and tie-out read the digest of the UNWRAPPED pricer; carrying it lets your `describe()` name the input | copy `source.digest` |
   | `snapshot` | your instruments read holidays, fixings, quotes from it | keep `source.snapshot`, NEVER `source` (weak-memo leak, below) |
   | `describe() -> Mapping` | `Pricer` protocol | type, ts, reference date, digest, chosen curve/fixings names |
   | `lookup(name, **kw)` | `Pricer` protocol; optional schema capabilities (`security`, `quote` for bonds) | `PricerBase.lookup` serves the names in `LOOKUPS` (default `()`) |
   | your accessors (`market()`, `calendar(name)`, ...) | your instruments | bindings may reach any attribute with `@pricer.<attr>`; core never learns their names |

   `wrap(pricer, *, curve=None, fixings=None)` must accept ONE positional argument (the loader calls `wrap(p)`), be idempotent (`wrap(wrapped) is wrapped`) and raise
   `ConfigError [CFG-WRAP]` for anything that is not a `SnapshotPricer` (`wrap needs a SnapshotPricer (a pricer holding a MarketSnapshot), got object`).
4. **Convert the data, refusing what you cannot honour.**
   * Curve: check `interpolation` and `value_kind` in `__init__` (wrap time, not first price): `[CFG-INTERPOLATION] curve 'c' has interpolation 'x'; this adapter honours only 'log_linear_df'`. Build
     the library's curve in ITS storage convention (acmelib: zero rates on ACT/365, `z = -ln(DF)/t`). Q: does the library's native interpolation equal log-linear in DF between nodes? Prove it
     with the round trip of `references/tests-template.md` (DF back to 1e-14 at and between nodes) or record the difference.
   * Fixings: convert by `FixingsSeries.unit` (`percent` -> `/100` for a library that wants decimals; a snapshot may say either); the provider already applied the publication policy, so
     apply it NOWHERE else (rateslib `wrap.py` installs the series exactly as carried, "the policy is applied exactly once"). Check the contract the library assumes and raise instead of
     letting it misprice (rateslib `_compat.check_fixings`: contiguous business days, nothing on/after as-of).
   * Calendars: load the SNAPSHOT's `CalendarData` (holidays + weekmask) into the library; never use the library's built-in calendar of the same name. Register under a CONTENT-ADDRESSED
     name (`PBT.<name>.<hash of holidays + weekend>`), never `replace`, because a layer holds two pricers at once (`ctx.prev_pricer`, `ctx.pricer`) whose snapshots may carry different
     holidays under one name. An unknown calendar: `[CFG-CALENDAR] the snapshot has no calendar 'nyse'; it has ['cal']`.
   * Several curves or fixings series in one snapshot: refuse until a name is given (`[CFG-WRAP] the snapshot has several curves [...]: name one in wrap(..., curve=...)`). The config cannot pass
     kwargs to `wrap` (it names a dotted path only): to choose a curve, write a module-level `def wrap_b(p): return wrap(p, curve="b")`. A `functools.partial` is REFUSED
     (`[CFG-ALLOW] ...: 'wrap_partial' is owned by 'functools', which is not under an allowed prefix`).
5. **Respect the memoisation contract.** Two layers exist and both are yours to understand:

   | layer | where | key | what it guarantees |
   |---|---|---|---|
   | `MarketData._wrap` (`src/pricebt/market.py`) | engine | `(role, snapshot digest, pricer stamp)`, bounded LRU (`maxsize=32`) that also keeps the unwrapped pricer alive | your `wrap` runs ONCE per role, digest and stamp; the digest is recorded BEFORE the wrap. Executed: the worked example (3 positions, layers on) over 19 grid points made 19 fetches, 92 memo hits, 19 wraps; two requests for one snapshot: 2 fetches, 1 wrap (the same wrapped object) |
   | a weak module-level dict in `wrap.py`: `_WRAPPED` (`acme_adapter/wrap.py`, `src/pricebt/testing/refstack.py`), `_MEMO` (`src/pricebt/contrib/rateslib/wrap.py`); QuantLib has none | your module | the `SnapshotPricer` OBJECT + `(curve, fixings)` | makes direct callers (layers, tests, the conformance kit) idempotent |

   Consequence: `wrap` must be a PURE function of the `SnapshotPricer` (no clock, no randomness, no hidden input). Expensive derived objects (risk curves, schedules, the library curve) are built
   lazily and cached on the WRAPPED pricer in a private dict (`memo(key, build)`), keyed by VALUES (dates, rate, notional, conventions), never on the snapshot and never on an instrument,
   and never by `id(instrument)` (`refstack.py` keys a few entries that way: an id can be reused after garbage collection). A per-mark cache in `ctx.cache` keyed by `id(self)` is safe.
6. **Make it immutable, picklable and deep-copyable.** `__setattr__` refusing writes after `__init__` (`AttributeError: LibPricer is immutable`), `__getstate__` dropping the private memo (library objects
   are caches), `__setstate__` restoring (and, for a global registry, re-registering calendars: a fresh process starts empty). The engine itself deep-copies only per-position `state`
   (`src/pricebt/engine/engine.py: _snapshot`) and never pickles a pricer, so this is a quality gate pinned by the shipped adapters' tests, not an engine requirement: it keeps multi-process sweeps and
   notebooks working. Executed: pickle and deepcopy keep the digest and rebuild library objects lazily.
7. **Guard every call that reads global state.** Every public path of your instruments that reaches `<LIB>` runs inside `with C.translate(p.ts), C.guard(p.reference_date):` (acme `swap.py`, quantlib
   `swap.py`), so the global is set for one call and restored, also on error. Anything that observes the global (quantlib bootstrap helpers) must not outlive its `guard`. The guard is NOT
   thread-safe by itself: pricebt is single-threaded; if your host is not, add one module `RLock` (in the skeleton, `references/library-state-and-errors.md` section 4).
8. **Translate exceptions** at the same boundary: missing datum -> `MarketDataUnavailable`, bad configuration/terms -> `ConfigError(code=...)`, anything else -> `MethodCallError`; catch the
   library's base class last; keep `__cause__`. The instruments' first line checks that the market pricer IS your pricer and names the fix when it is not (first row of Common failures;
   see `pricebt-instrument-kit`).
9. **Test it and mutate it.** Copy `references/tests-template.md` into `tests/test_<lib>_wrap.py` (marker `adapter_<lib>`), run it, then apply each mutant of its table and confirm each fails.
   A SERVICE (it prices only from a market you upload) needs the tests of the template's "Service variant" instead of the process-global ones: uploaded once per snapshot and never for a snapshot nobody prices (`client.stats`), a clone
   re-uploads, an error dict becomes a pricebt error, and the derived-world accounting of `pricebt-enterprise-platform-patterns` step 3, from a fresh client per test.
10. **Wire it.** Stack overlay (`instruments.<n>.factory`, `market.pricers.<role>.wrap` only): `market: {pricers: {primary: {wrap: "<lib>_adapter:wrap"}}}`, and `registry: {allow: [<lib>_adapter]}` in the BASE
    config (an overlay may not set `registry`). Worked example: `skills/pricebt-wire-external-library/example/config/acme_swap.yaml` and `acme_tieout_base.yaml`.

## Performance notes (order of magnitude, measured here, machine dependent)

* Wrap itself is cheap: reference stack 0.06 ms, quantlib 0.3 ms, rateslib 0.5 ms, acme 0.6 ms per 20-node snapshot; the digest of a 640-fixing snapshot costs 0.25 ms and is computed once by the
  provider. The first use costs more (calendar registration, schedule, risk curves: 2 to 3 ms for quantlib/rateslib): that is why derived objects are memoised on the wrapped pricer.
* One wrap per distinct snapshot is the floor (19 grid points -> 19 wraps): do not build anything at import time or per call that could be built once per snapshot; do not rebuild the library curve inside
  every measure (`ctx.cache` shares work between `value`, measures and layers within one mark). A `guard` costs about 2 microseconds (acme): call it per public method, not per helper.
* Layers (`carry`, `roll`, `delta`, `convexity`) hold two pricers and revalue several times: they are off by default (`attribution.layers`) and cost tens of milliseconds per position per cadence point.

## Checks

```powershell
$env:PYTHONPATH = "src;tests"
python -m pytest tests/test_market_wrap.py tests/test_quantlib_wrap.py tests/test_quantlib_globals.py tests/test_snapshot_wrap_rateslib.py -q -o addopts= -p no:cacheprovider
# expected: all passed (needs the QuantLib and rateslib extras; each file skips or fails with an import error naming the missing extra otherwise)
python -m pytest tests/test_skills_example_acme.py -q -o addopts= -p no:cacheprovider -k "wrap_is_memoised or discount_factors_become or only_the_log_linear or snapshots_holidays or two_wrapped or valuation_date or acmelibs_own_exceptions or plain_data"
# expected: all passed (the worked example's wrap tests; no library needed)
python -m pytest <your>/test_<lib>_wrap.py -q -o addopts= -p no:cacheprovider     # expected: all passed; then the mutation table: every mutant fails
```

The wrap in the real config, without running it (expected: the first line names YOUR pricer class; with the overlay's `wrap` missing it names the base's, `RefPricer` in the example base). The adapter's
parent directory MUST be on `PYTHONPATH` ([CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)):

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example"     # your adapter: "src;tests;<parent directory of <lib>_adapter>"
$ex = "skills/pricebt-wire-external-library/example"
python -m pricebt run $ex/config/acme_tieout_base.yaml --stack $ex/config/acme_swap.yaml --dry-run
# OK: first pricer AcmePricer at 2024-05-24 17:00:00-04:00 (reference_date 2024-05-24)
```

The one-line proof that wrap runs once per snapshot in a real run (`Built.market` is the run's `MarketData`; a second `run()` of the same `Built` starts its counters clean). Expected on the worked
example (3 positions, layers on): `19 92 19`, that is wraps == the number of distinct snapshots fetched, whatever the number of positions and marks:

```python
from pricebt import api
ex = "skills/pricebt-wire-external-library/example"
b = api.build(f"{ex}/config/acme_tieout_base.yaml", stack=[f"{ex}/config/acme_swap.yaml"])
b.run(); print(b.market.n_fetches, b.market.n_hits, b.market.n_wraps)   # 19 92 19
```

## Common failures

| message (real) | cause | fix |
|---|---|---|
| ``[CFG-WRAP] the market pricer is a SnapshotPricer, not an AcmePricer: set `wrap` on the pricer role to acme_adapter:wrap`` (in a base that wraps for another library: `... is a RefPricer, not an AcmePricer ...`) | the stack overlay sets `factory` but not `market.pricers.<role>.wrap` (or the role is not the instrument's `pricer`) | add `market: {pricers: {primary: {wrap: "<lib>_adapter:wrap"}}}` to the overlay |
| `[CFG-WRAP] wrap needs a SnapshotPricer (a pricer holding a MarketSnapshot), got X` | the provider returns its own pricer type | return `SnapshotPricer` (skill `pricebt-market-data-snapshots`) |
| `[CFG-WRAP] the snapshot has several curves ['a', 'b']: name one in wrap(..., curve=...)` | two curves in one snapshot | a `def wrap_b(p)` wrapper; a config cannot pass kwargs |
| `[CFG-ALLOW] ...: 'wrap_partial' is owned by 'functools'...` | a `functools.partial` named as `wrap:` | use a `def` in your module |
| `[CFG-TYPE] market.pricers.primary.wrap: wrap 'X' must be a callable wrap(pricer) -> pricer` | the path names a non-callable | point at the function |
| `[CFG-INTERPOLATION] curve 'c' has interpolation 'cubic_spline'; this adapter honours only 'log_linear_df'` | a tag the adapter cannot honour (correct refusal) | export nodes the snapshot tag describes; never substitute |
| `MarketDataUnavailable: ... 1 overnight fixings missing between D1 and D2; first D3` and the run STOPS with no report | the wrap loaded no (or too few) holidays: the library demanded a fixing on a market holiday. It looks like a crash | load the snapshot's holidays; a holiday TOO MANY instead gives a comparable report (`L0.resolved_terms input`, `effective: 2024-05-29 vs 2024-05-30`) |
| a holiday-sensitive control never fails | the synthetic market's default calendar has no holidays | build the market with a `pricebt.timeutil:Calendar` (`$call`) and repeat it in `backtest.calendar` |
| numbers depend on which snapshot was wrapped last | calendars registered by NAME in a global registry: the last wrap wins | content-addressed names, never `replace` (test: two pricers alive, both orders equal) |
| a foreign caller finds its valuation date changed | `guard` did not restore | `finally:` restore, also "unset"; test with a foreign date set before |
| `AttributeError: LibPricer is immutable` in your own code | you set an attribute after `__init__` | build everything in `__init__` or in the private memo |
| the weak memo never frees (memory grows with the number of snapshots) | the wrapped pricer stores `source` (its own key) | store `source.snapshot` only |
| `import <lib>_adapter.wrap as m` gives a function, not the module | the package re-exports `wrap` over the submodule (the same for the Kit named `swap`) | `sys.modules["<lib>_adapter.wrap"]` or `importlib.import_module` |
| `binding 'value': method 'value' called with args=[] kwargs=['ctx']: ...` and the tail is a `TypeError` of your own code | `call_binding` labels ANY `TypeError` from the callable as an argument mismatch | read the END of the message ([CE-TYPEERROR](../pricebt-wire-external-library/references/common-errors.md#ce-typeerror)) |
| `OptionalDependencyError: <lib>_adapter needs the optional extra <LIB> (pip install <LIB>)` | `<LIB>` not importable | install it or skip the tests with `pytest.importorskip("<LIB>")` |

## Related skills

`pricebt-market-data-snapshots` (what `wrap` receives), `pricebt-instrument-kit` (what reads your pricer; the kit checks `_lib(p)`), `pricebt-bindings-cookbook` (signs, units, `@pricer.<attr>`),
`pricebt-discover-the-library` (S1-S10 probes), `pricebt-layers-and-ladder` (why two pricers live at once), `pricebt-conformance-and-tieout` (`L1`/`L2` differences that start in the wrap),
`pricebt-debug-tieout-differences`, `pricebt-guards-and-packaging` (the library's import root in `banned.yaml`, the optional extra, markers), `pricebt-wire-external-library` (the master playbook).
