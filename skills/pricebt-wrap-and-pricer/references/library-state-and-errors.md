# What to find out about `<LIB>` before writing `_compat.py`, and how errors must leave it

Answer each question from `<LIB>`'s documentation and code, then write the answers as the module docstring of `_compat.py` (the model is the docstring of
`src/pricebt/contrib/quantlib/_compat.py` and section "The two libraries' process-global state (what the guard must own)" of `docs/design/11-quantlib-conventions.md`). A question you
cannot answer from the documents is answered by a probe on a known answer (a par swap is worth 0; a payer's dv01 is positive; two calls in the other order give the same number).

## 1. Questions

| # | question | why it matters | if yes |
|---|---|---|---|
| S1 | Does any call read a process-global "today" / valuation / evaluation date? Does a date change notify live objects (observers) and can that raise? | every value is wrong or an error if it is unset or stale; a foreign caller's date must survive | `guard(date)` sets it and restores the previous value in `finally` (also "unset"); tolerate a notification error only if the date IS set, and assert it (quantlib `_set_eval`) |
| S2 | Is there a global calendar registry, or do calendar objects mutate shared singletons (`addHoliday` on a named calendar)? Is a name reused? | two snapshots with different holidays under one snapshot name are alive at once (`ctx.prev_pricer` and `ctx.pricer` in every layer) | never mutate a shared calendar; build a private object (quantlib `BespokeCalendar`), or register under a content-addressed name and never replace (acme `load_calendar`) |
| S3 | Is there a global fixings / index-history store keyed by name? | a later run's fixings must never be seen by an earlier date; another user's index must not be wiped | install right before a call and remove after, or clear only YOUR family name on entry and exit; never a `clear all` (quantlib `clear_fixings`, rateslib `install_fixings`/`clear_fixings`) |
| S4 | Any other global switch that changes results (include-flows-today, day-count defaults, a numerical-tolerance singleton, RNG seed, a licence handle, a config singleton)? | silent wrong numbers | set inside `guard`, restore after; quantlib turns `includeReferenceDateEvents` on for one call: with it off a swap whose last flow is paid today is expired and worth 0 |
| S5 | Import side effects (banner, licence warning, plotting backend, prints to stdout from a solver)? | noise in the run, or a licence text that fails a strict-warnings run | filter/redirect in `_compat` before the import (rateslib `_compat`: warning filter, `MPLBACKEND`, `solve_quiet`) |
| S6 | Which exceptions does it raise (a base class? a hierarchy?), and which of them mean "data missing" versus "bad input" versus "not supported"? | pricebt reacts differently: a missing datum is `MarketDataUnavailable`, a bad configuration is `ConfigError` | `translate` (section 3) |
| S7 | Which failures are SILENT (NaN, a default calendar, a substituted interpolation, a fixing treated as zero)? | the tie-out finds them last | check the contract in the adapter and raise: rateslib's `check_fixings` (contiguous business-day fixings, nothing dated on/after as-of) and `finite()` (strip AD, assert finite); acme's wrap loads NO default calendar: a name it lacks is an error |
| S8 | Is it thread-safe? Does it hold a licence token per process? | see section 4 | say so in the docstring; lock or refuse |
| S9 | What is the horizon of its calendars and dates (QuantLib dates span 1901-2199)? | past it every day is a business day and a schedule silently changes | quantlib's `calendar_from_holidays` drops holidays outside its range (they can never matter); a snapshot calendar shorter than the longest maturity of the run is a DATA gap: the provider extends it (`docs/design/11-quantlib-conventions.md`, section "Calendar horizon (data, not convention)") |
| S10 | What are its units and spellings of everything that crosses (rates decimal or percent, dates as strings, tenors lower-case, sign of value, notional scale)? | each is one conversion at the boundary | fixings: the wrap converts by `FixingsSeries.unit`; rates, signs, per-million risk: skill `pricebt-bindings-cookbook` |

## 2. Where the two shipped adapters answered them (read, not run here)

| | rateslib | QuantLib |
|---|---|---|
| valuation date | none (the curve's initial node) | `Settings.instance().evaluationDate`, `Settings.includeReferenceDateEvents` |
| fixings | process-global `rl.fixings` store, entry `<name>_1B` | `IndexManager`, keyed by index name |
| calendars | shared objects from `get_calendar`; a custom `Cal` is private | named calendars are singletons; `BespokeCalendar` is private (the adapter builds only those) |
| licence / import noise | banner once on import; warning filtered in `_compat` | none |
| the rule for the rest of the package | everything imports `rl` from `_compat` so the filter is set first | no object that observes the evaluation date may outlive the `guard` it was created in |

## 3. Error translation (what the engine and the tie-out must see)

| the library says | raise (from `translate`, with `from e` so the cause is kept) | code |
|---|---|---|
| a missing datum (no fixing, no quote, no curve) | `MarketDataUnavailable(ts, request, reason)` | (class) |
| a calendar it does not have, a bad convention value, an unsupported tag | `ConfigError(msg, code="CFG-CALENDAR")`, `"CFG-CONVENTION-UNSUPPORTED"`, `"CFG-INTERPOLATION"`, `"CFG-WRAP"` | shown as `[CODE] message` |
| malformed terms (a tenor it cannot parse, an unknown extra) | `ConfigError(msg, code="CFG-TERMS")` / `"CFG-EXTRAS"` | |
| any other library exception | `MethodCallError("<LIB>: <ClassName>: <message>")` | |
| a value the library returns that is not finite | `ValueError`/`MeasureError` from your own check | |

The classes are in `src/pricebt/errors.py` (`PricebtError` is the base; `ConfigError(message, path=, code=)`, `MarketDataUnavailable`, `StaleSnapshot`, `MethodCallError`, `MeasureError`,
`OptionalDependencyError`). Rules: catch the library's BASE exception last so no library type escapes (asserted by a test: `not isinstance(err, <LIB>Error)`); keep `__cause__`;
never catch `Exception`; never turn an error into a default value (a missing fixing is never zero). Real messages a wrap and its instruments raise (executed):

```
[CFG-WRAP] the market pricer is a SnapshotPricer, not an AcmePricer: set `wrap` on the pricer role to acme_adapter:wrap
[CFG-WRAP] wrap needs a SnapshotPricer (a pricer holding a MarketSnapshot), got object
[CFG-WRAP] the snapshot has several curves ['a', 'b']: name one in wrap(..., curve=...)
[CFG-CALENDAR] the snapshot has no calendar 'nyse'; it has ['cal']
[CFG-INTERPOLATION] curve 'curve' has interpolation 'cubic_spline'; this adapter honours only 'log_linear_df'
[CFG-TYPE] market.pricers.primary.wrap: wrap 'pricebt.testing.refstack:USD_SOFR_OIS_CONVENTIONS' must be a callable wrap(pricer) -> pricer
market data unavailable at 2024-06-12 17:00:00-04:00: this snapshot carries no discount curve
```

The first one is the failure of every new adapter whose stack overlay forgot the `wrap` (the instrument factory checks its pricer with a helper such as `_acme(p)` / `_ql(p)` and names the fix).

## 4. Threads

Nothing under `src` or `tests/support` starts a thread or takes a lock (searched: no `threading`, `Lock`, `concurrent.futures` or `multiprocessing`), so the engine is single-threaded,
and none of the shipped adapters locks. A `guard` that sets and restores a process-global protects against nothing across threads: executed with two threads and acmelib's valuation date, without a lock
one thread read the other's date (`{'a': '2024-02-02', 'b': None}`), with `threading.RLock` around set-run-restore each read its own (`{'a': '2024-01-02', 'b': '2024-02-02'}`). If your host runs
several backtests in one process on threads, take one module-level `RLock` in `guard` (two lines, in the skeleton) and check that `<LIB>` objects themselves may be shared across
threads; if the library is not thread-safe at all, refuse threads in `_compat` rather than run wrong numbers. This is a recommendation of these skills, not a pricebt rule.

## 5. Silent failures reported by the worked example (each executed there)

* A wrap that forgets the holidays does not price on invented ones, it STOPS: with a weekend-only calendar the reference stack demands a fixing on a market holiday and the run ends in
  `MarketDataUnavailable` ("1 overnight fixings missing between ... ; first ..."), with no report: it looks like a crash. Only a calendar with a holiday TOO MANY gives a comparable report
  (`L0.resolved_terms input`, `effective: 2024-05-29 vs 2024-05-30`).
* Holiday-sensitive controls are vacuous on a market whose calendar has no holidays (`SyntheticMarket` default): build it with holidays (`skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml`).
* A `TypeError` raised INSIDE a bound callable (a mismatch inside your pricer code, not in the binding) is reported by `binding.call_binding` as
  `binding 'value': method 'value' called with args=[] kwargs=['ctx']: <the real cause at the end>`, which reads like an argument mismatch. Read the tail of the message.
* Memo keys: a cache on the WRAPPED pricer that is keyed by `id(instrument)` (as `refstack.py` does for a few entries) can return a stale entry if an instrument is garbage collected and another
  takes its address. Key derived-object memos by VALUES (dates, rate, notional, conventions: `QLBond._inst` does), or keep the instrument objects alive for the pricer's life. A per-mark cache
  in `ctx.cache` keyed by `id(self)` is safe (it lives for one position and one mark).
