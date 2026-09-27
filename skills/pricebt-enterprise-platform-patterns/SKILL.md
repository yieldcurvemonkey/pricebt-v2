---
name: pricebt-enterprise-platform-patterns
description: Use when the library you wire into pricebt is an in-house bank platform, in-process or a remote service or grid, possibly also the market-data source, and you must handle sessions and secrets, remote market environments, latency and cost, no-look-ahead, offline tests, error mapping, confidentiality and versioning. Poses the questions and gives the pricebt-side answer to each.
---

# Enterprise platforms: sessions, remote environments, cost, offline tests, errors, confidentiality

## Purpose

A bank platform brings what a pip-installed library does not: a login, a network, a licence, confidential data and a cost per call. pricebt knows none of that, and nothing is known here about your platform's API.
This skill lists the questions to ask the platform's owners and gives, for each answer, what to do on the pricebt side (which hook, which file, what does NOT exist). Every claim below was read from the code or executed
on the worked example (`skills/pricebt-wire-external-library/example/`).

## Prerequisites

* `pricebt-discover-the-library` done (questionnaire groups B6 and C in particular) and `pricebt-wrap-and-pricer` read (`wrap`, `SnapshotPricer`, memoisation, `_compat.guard`).
* Repository root as working directory, `PYTHONPATH` = `src;tests;skills/pricebt-wire-external-library/example` for the executed pieces (Windows separator `;`, Linux `:`). Commands below are written in bash form with the
  variable quoted, `PYTHONPATH="src;tests" python x.py`; in PowerShell set it first: `$env:PYTHONPATH = "src;tests"; python x.py`.
* References: for a remote service needed are `references/cost-budget.md`, `references/errors-time-and-determinism.md`, `references/offline-and-live-tests.md` and the helper `references/remote_environment.py`; optional:
  `references/config-secrets-and-setup.md` (only when a login exists), `references/versioning-and-concurrency.md` (until you publish results). For an in-process library only `references/errors-time-and-determinism.md` matters.
* The helpers of this skill: `references/recorded_client.py` and `references/remote_environment.py` (standard library plus `pricebt`; each has a self-check).
* `pip install -e .[parquet]` (pyarrow) if you write results with `to_parquet`, as the driver of `references/versioning-and-concurrency.md` does.

## Steps

Each step: the question for the platform team, then the pricebt answer.

1. **Choose the profile, and whether the platform is also the data source.** Ask: does it run in my process (a Python API) or behind a service or grid? Does it serve the curves, fixings and calendars as well as
   the prices? Decide with the two lists at the end of this skill. If it serves the data, a PROVIDER (`get_pricer(ts, request) -> SnapshotPricer`, `pricebt-market-data-snapshots`) turns its curves into
   `CurveSnapshot` discount factors, and the same recorded snapshots must feed the reference stack: the tie-out then compares pricing, not data.

2. **Session, authentication, clients: outside pricebt** (`references/config-secrets-and-setup.md`). Ask: how do we log in, what expires, is anything interactive? Create `<lib>_adapter/session.py`
   (`configure(profile)` reads the credential from the environment or a secret store inside the function, is idempotent, and installs one client; `client()` returns it or raises `ConfigError` code `CFG-AUTH`) and call it from
   the BASE config: `registry: {allow: [<lib>_adapter], setup: [{call: "<lib>_adapter.session:configure", kwargs: {profile: uat}}]}`. The complete injection surface is `${VAR}` / `${VAR:-default}`
   (`yamlio.interpolate`; missing: `[CFG-ENV] environment variable 'X' is not set and has no default`), `$call` (with kwargs) and `$ref` (the object, no kwargs), provider `type:` + `kwargs`, `registry.setup` and `registry.import`;
   `wrap` and `factory` are dotted paths with NO kwargs. Never a secret in a config, an overlay, a `bind:` literal, a fixture or a log: `${VAR}` is substituted before `config_hash`, so it rotates the run's identity, and the tie-out report prints
   every binding's literals.

3. **A snapshot becomes a remote market environment: once per digest** (`references/remote_environment.py`). Ask: can the platform hold a market (curves, fixings, calendars) under a key, how do I release it? Upload from the
   memoised `wrap` (eagerly in it, or lazily on the first price that needs it, as `example-service` does: a snapshot never priced then costs nothing), keyed by `SnapshotPricer.digest` (`MarketData` calls `wrap` once per snapshot and stamp); share one environment per digest between pricers; release with `weakref.finalize`. There is NO release hook:
   `MarketData.reset()` clears its memo and eviction pops an LRU entry (`maxsize` 32), and neither tells anyone. An environment lives as long as some pricer or open position holds it. Executed: one upload per distinct digest,
   one release per upload, exactly once, also after `reset()`. A re-established session (a NEW client) must not be served the old client's environments: the pattern's `configure(new_client)` drops its registry, so the next `wrap` uploads again.
   **Upload accounting: what the platform's counter must say.** Layers OFF: `uploads = snapshots` (one per digest, lazily on the first price that needs it, so a snapshot never priced is never uploaded). Layers ON, for a
   service whose scenarios cannot express the rolled, resampled and shocked worlds (`pricebt-layers-and-ladder`, `layer-definitions-and-assembly.md` section 4, the scenario-engine row): derived worlds are
   content-addressed on the current pricer, so every position shares them and `uploads = snapshots + 5 x intervals + cash worlds`: per interval five worlds for Richardson at two steps (h and h/2: the rolled world, which is also
   the resampled base while the two snapshots' node offsets agree, plus four shocked worlds; 3 per interval with a single h by the same count, not run; 6 when the node offsets differ: one 14-day interval of the golden case,
   `p6_golden_control.py`, uploaded 7 = 6 + 1 cash world), and ONE cash world per interval whose `t1` is more than the payment lag after `t0` (2 business days for zeta: the cash query then needs the t1 fixings, so the old market
   is rebuilt with them; `t1 - t0` within the lag adds none). So `5 x intervals` alone holds only while every step is within the lag. An `interval` is one flush of the layers, from the previous flush (or the entry of the book) to this one:
   18 for 19 daily grid points at cadence `each` or `eod` (no step exceeds the lag: no cash world), 4 at `every_n:5` (steps of 4 or 5 business days, all above the lag: 4 cash worlds), 7 at `every_n:3` (five of the seven steps are 3 or more business days: 5 cash worlds).
   The count is of DISTINCT (previous, current) pairs: worlds are shared by every position.
   Requests: about 11 per position per FLUSH (forward value, rolled, base, 8 shocked valuations; 7 when the four distinct shocks are memoised) on top of about 2 per position-point for the marks.
   Executed on the example service (4 positions, 19 snapshots): 19 uploads with layers off, 109 = 19 + 5 x 18 at cadence `eod`, 43 = 19 + 5 x 4 + 4 cash worlds at `every_n:5`, 59 = 19 + 5 x 7 + 5 at `every_n:3`; 2.3 requests per position-point
   with layers off, 12.8 at `eod`, 4.9 at `every_n:5`, 6.7 at `every_n:3` (`references/cost-budget.md` section 5, and the counter tests of `pricebt-wrap-and-pricer`, `tests-template.md`, "Service variant").

4. **Latency, batching, caching: compute the budget before the first tie-out** (`references/cost-budget.md`). Ask: latency (p50, p95) per valuation, per bump, per upload; rate limits; batching. pricebt has no batch hook
   (the engine marks one position at a time) and shares nothing across positions: `ctx.cache` is per position and mark, per-snapshot work belongs on the pricer. Count library calls per position-point (and, for a service, uploads: step 3) with the
   script of that file: a mark is about 2 calls, layers at `eod` multiply it by about six, the audit adds a constant per MARK regardless of the layer cadence. Pull the levers: `attribution.cadence: every_n:<k>`,
   audit only for the tie-out, a small base config, memoised shocks (four distinct valuations suffice for the layers), the recorded mode.

5. **Determinism, no look-ahead, time zones** (`references/errors-time-and-determinism.md`, section 5). Ask: what is the platform's implicit "now" and "latest"? are curves revised after their date? which time zone
   are its stamps in? Send an explicit as-of on EVERY request; stamp each snapshot with the time its data was knowable; give it tz-aware stamps; choose the `TimeMapping` (`asof` + `max_staleness`, `exact`, `date` + `eod_visible_at`);
   let `MarketData`'s guard raise `LookAheadError`. Run the determinism check (a stack tied out against itself: every row `exact`).

6. **Offline tests by record and replay; live tests opt-in** (`references/offline-and-live-tests.md`). Copy `recorded_client.py` to `tests/support/recorded_client.py` (import it as `support.recorded_client`); record the
   platform's answers once with the live client (`PRICEBT_LIVE_<LIB>=record`; it only ADDS requests not on disk, to refresh delete the directory first), replay in CI (a request never recorded raises
   `MarketDataUnavailable`, never an empty answer), key by a digest of the request without secrets (extend its key list with your platform's credential names), scrub the literal secret values. Register ONE
   marker, `adapter_<lib>`, in `pytest.ini` `markers`, in `PARTITIONS` of `tests/guards/partition.py` and in the pinned tuple of `tests/guards/test_partition.py`; live tests carry the SAME marker and are opt-in by
   `skipif(<env var unset>, reason="live platform: set PRICEBT_LIVE_<LIB>=1 ...")` (one partition, three pinned edits: `pricebt-guards-and-packaging`).

7. **Error mapping** (`references/errors-time-and-determinism.md`, sections 1 to 4). Ask: which exceptions can the client raise, which are transient? Wrap EVERY library call in a `translate` context manager (the pattern of
   every shipped `_compat.py`): timeouts and unavailable data -> `MarketDataUnavailable(ts, request, reason)`; authentication, entitlement, wrong input -> `ConfigError(code=...)`; anything else -> `MethodCallError`; sanitise the message. A raw
   exception is NOT caught by `on_error: record`: it aborts the run (executed), except inside an audited measure and the baseline, which record ANY exception as a NaN cell plus an error row under every mode: a tie-out can
   complete with holes, so read `n_errors` and `result.errors`. Retry inside the adapter only for idempotent reads, bounded and counted. pricebt has no timeout anywhere: set client-side timeouts, or a hung
   call hangs the run. Run with `on_error: raise` until the run has no mark, execution or layer error (`n_errors` counts audit and baseline rows too, and a swap that matured in the window gives some on every stack): under `record` a failed mark keeps its previous mark for that row (executed: -157.81 in `positions_value`, the next mark restores it).

8. **Confidentiality and licence** (`references/offline-and-live-tests.md`, section 4). Ask: may tests run in CI? may recorded answers or results be committed or shared? Never commit proprietary data, credentials, host names or
   trade ids; sanitise before a fixture exists; keep uncommittable data under a git-ignored directory (`data/.gitignore` is `*` and `!.gitignore`) and let its tests SKIP WITH A REASON when it is absent; commit the generator
   (`tools/export_fixtures.py` is the model) and a provenance file (`RecordedClient.write_provenance`: library, version, environment PROFILE, recording date, what was sanitised, licence status, plus the `files_sha256` it computes). Results
   (`outputs.dir`) are confidential too. The library's name stays out of core.

9. **Versioning** (`references/versioning-and-concurrency.md`). Ask: how do I read the library, platform and data-set versions? pricebt's run manifest has NO field for them, snapshot `provenance` is excluded from the digest and
   `describe()` is called by nothing, so the honest answer is: record them yourself. Run through a driver that writes `environment.json` next to `manifest.json` (library, version, environment profile, Python, `config_hash`, `base_hash`,
   conventions digests, `n_errors`), and put the same facts in the snapshots' `provenance`. Adding an extra's version pin is an "Ask first" item of `docs/design/11-refactor-spec.md`.

10. **Concurrency.** Ask: is the client thread-safe? The engine is sequential and starts no thread, but two stacks of a tie-out share one process. Guard every process-global setting around each call (`_compat.guard`), register global
    names content-addressed, never start a background thread from `configure`, a provider or a `wrap`; a `threading.RLock` around guard-and-call is the caller's decision, not the engine's.

11. **Pass the checklist below, then run the first tie-out** (`pricebt-conformance-and-tieout`).

### Profile A: the library runs in your process (a Python API)

1. Guard its process-global state and translate its exceptions in `_compat.py` (steps 7 and 10); it has no session to release, so steps 3 and 6 reduce to a fixture snapshot and a `importorskip`.
2. Build library objects once per snapshot in `wrap`; positions stay plain data (deep copy, pickle) and rebuild library objects per call.
3. Cost is CPU: budget with the counter, then time it (step 4); a memoised risk curve per snapshot is usually the whole optimisation.
4. If a licence is needed, decide what the default run does without it: `importorskip` and the `adapter_<lib>` marker keep the core run green.

### Profile B: a remote service or grid

1. Session and secrets first (step 2); nothing interactive; timeouts on every call (step 7).
2. One environment per snapshot digest, uploaded in `wrap`, released by finalizer (step 3); budget round trips (step 4): the layer flush is 6 to 13 valuations per position per cadence point, and a service adds `5 x intervals` derived markets plus one cash world per interval longer than the payment lag (step 3).
3. Record-and-replay from day one (step 6): the tie-out must run offline, from recorded answers, on a laptop without the network.
4. Map every transport error (step 7) and sanitise messages; no retry on non-idempotent calls.
5. Explicit as-of on every request, knowledge time versus valuation time, and a determinism check that includes the service (step 5).
6. Record library, platform and data versions per run (step 9); results are confidential (step 8).

### Checklist before the first tie-out (all yes, or stop)

* [ ] Session installed by a `registry.setup` step; no secret in any config, overlay, `bind:` literal, fixture, log or message (search for it).
* [ ] Every library call sits inside `translate`; a raw exception was injected in a test and the run recorded it (or aborted with a pricebt error); messages are sanitised.
* [ ] Client-side timeouts set; retries only on idempotent reads, bounded, counted.
* [ ] Environments are uploaded once per digest and released (self-check of `remote_environment.py` pattern on your client).
* [ ] The platform's own counters agree with step 3: `uploads == snapshots` with layers off, `== snapshots + 5 x intervals + one cash world per interval longer than the payment lag` with them on (a wrap test asserts both; a plain `5 x intervals` is exact only when no step exceeds the lag, as at cadence `eod`).
* [ ] The cost budget is written down (calls per position-point by kind, latency, total) and fits; audit and layers use the cadence you budgeted.
* [ ] Every request carries an explicit as-of; snapshots are tz-aware; the determinism check passed (every row `exact`).
* [ ] Offline recordings exist and are sanitised, the default test run passes without the platform, live tests skip WITH a reason.
* [ ] `environment.json` is written by the driver; the conventions document names the versions it is valid for.
* [ ] The base config has `backtest.on_error: raise` (or you accept and check `n_errors`), a small window and few trades.

## Checks

| what | command | expected |
|---|---|---|
| recorded client | `PYTHONPATH="src" python skills/pricebt-enterprise-platform-patterns/references/recorded_client.py` (PowerShell: `$env:PYTHONPATH = "src"` first) | `recorded_client self-check ok` |
| remote environment lifecycle | `PYTHONPATH="src" python skills/pricebt-enterprise-platform-patterns/references/remote_environment.py` | `remote_environment self-check ok: N uploads, N releases` (equal) |
| errors are mapped | the block of `references/errors-time-and-determinism.md` section 1 (`PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example"`) | control recorded, raw `TimeoutError` ABORTED, mapped one recorded |
| determinism | the block of section 5 of the same file | `statuses of every row: ['exact'] | passed: True`, exit 0 |
| cost budget | the block of `references/cost-budget.md` on your base config | a table of library calls per position-point; total x latency fits |
| offline test file | the block of `references/offline-and-live-tests.md` section 3 as `tests/test_<lib>_offline.py`, with `tests/support/recorded_client.py` in place, `python -m pytest tests/test_<lib>_offline.py -q -o addopts= -rs` | offline tests pass; the live one `SKIPPED` with its reason; with `PRICEBT_LIVE_<LIB>=1` all pass |
| no secret in the recordings | `grep -rl "<the token value>" tests/fixtures/<lib>_recorded data` (PowerShell: `Select-String -Path ... -Pattern ... -List`) | no file |
| `registry.setup` ran | `python -m pricebt validate <base> --stack <overlay>` with the token unset, then set | `error: [CFG-AUTH] no credential for profile 'uat': set <VAR> (login happens BEFORE a run; a run never prompts)` and exit 2 from YOUR `configure`; with the token: `OK` |

## Common failures

* `TimeoutError: gateway timeout` (or any raw exception) and the run stops although `backtest.on_error: record`: the engine records only `PricebtError`. Translate in the adapter (`references/errors-time-and-determinism.md`).
* `[CFG-ENV] environment variable 'X' is not set and has no default`: a `${X}` in a config with the variable unset; give a default (`${X:-uat}`) for non-secrets, and never put a secret there.
* `[CFG-ALLOW] module '<lib>_adapter' is not under an allowed prefix ['pricebt']`: `registry.allow` belongs in the BASE config, never in an overlay ([CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow), [CE-STACK-REGISTRY](../pricebt-wire-external-library/references/common-errors.md#ce-stack-registry)).
* `[CFG-ALLOW] market.pricers.primary.wrap: [CFG-ALLOW] module "{'$call'" is not under an allowed prefix [...]`: a `wrap` cannot be a `$call` mapping; it is a dotted path. Reach the client through `registry.setup` and module state.
* `[CFG-CALL] <pkg>:<fn>(**kwargs) failed: <Type>: <message>`: a `$call` or setup step raised; the foreign message is echoed, so it must not carry a secret.
* `MarketDataUnavailable: ... no recorded answer for npv({...}) in <dir> (digest ...); record it with the live platform`: replay met a request that was never recorded; record it with `PRICEBT_LIVE_<LIB>=record` (it adds requests that are not on disk; to REFRESH answers that changed, delete the recording directory first: a request already on disk is replayed, never re-recorded), do not loosen the client.
* `LookAheadError: pricer requested for ... but the clock is at ...` / `provider returned a snapshot stamped ... for a request at ...`: the provider ignored the request time or stamped the snapshot with the wrong clock.
  `[SNAPSHOT] a snapshot stamp must be tz-aware`; `[SNAPSHOT] fixings 'f' run to ...: only fixings strictly before the reference date ... exist at the snapshot`: fix the provider, not the guard.
* A tie-out "passes" but `n_errors` differs or `L1.marks_rows` is `structure`: marks failed under `record` and were carried stale. Re-run with `on_error: raise`.
* A raw exception raised inside an AUDITED measure or a baseline measure does not abort even under `on_error: raise`: it is a NaN cell and an error row (`where: audit` / `baseline`), and the tie-out completes with a hole
  (`NaN on both sides: nothing was compared ...`). Read `run_tieout(...).results[<stack>].errors`; translate in the adapter anyway.
* Remote environments pile up on the server: a `RemotePricer` kept alive (a global, a list, a closure) defeats the finalizer; the environments of open positions live until the positions close.
* The whole tie-out stops at the first failing stack (no report): `run_tieout` runs stacks in sequence and the exception escapes; set `backtest.on_error: record` in the BASE to get a report.

## Related skills

`pricebt-discover-the-library` (the questions behind steps 1 and 7), `pricebt-market-data-snapshots` (providers, time mapping, recorded stores), `pricebt-wrap-and-pricer` (memoised wrap, globals), `pricebt-run-config-and-reports`
(`on_error`, outputs), `pricebt-conformance-and-tieout` (the first tie-out, declared tolerances), `pricebt-guards-and-packaging` (markers, banned tokens, extras, pins), `pricebt-layers-and-ladder` (what a layer flush costs),
`pricebt-wire-external-library` (the playbook).
