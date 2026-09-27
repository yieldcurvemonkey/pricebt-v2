# Tie-out reference: overlay rule, levels, statuses, reading the report, limits

Sources: `src/pricebt/config/stacks.py` (overlays), `src/pricebt/tieout/{runner,compare,report,tolerances}.py`, `src/pricebt/config/cli.py`, ADR 006 (`docs/design/adr/006-stacks-and-tieout.md`).
Every quoted row name, message and status below was produced by running the worked example (`skills/pricebt-wire-external-library/example/`).

## 1. What a stack is (the overlay rule)

A stack overlay may set ONLY `instruments.<name>.factory`, `instruments.<name>.bind` and `market.pricers.<role>.wrap` (`validate_overlay`). Anything else is `[CFG-STACK]` naming the path:

```
[CFG-STACK] registry: a stack may not set 'registry': only instruments.<name>.factory / .bind and market.pricers.<role>.wrap (everything else is shared by every stack)
[CFG-STACK] instruments.usd_sofr_ois.conventions: a stack may not set instruments.usd_sofr_ois.conventions: only ['factory', 'bind'] (conventions and terms are shared so a difference cannot be a convention)
[CFG-STACK] instruments.other: the base defines no instrument 'other' (a stack cannot add one); it defines ['usd_sofr_ois']
```

Merge rules (two, not stated in ADR 006): `apply_stack` (`src/pricebt/config/stacks.py`) makes an overlay `bind` REPLACE the base's whole `bind` block of that instrument; `build_spec` (`src/pricebt/contracts/spec.py`) then merges the Kit's default block with whatever `bind` remains BY NAME (one entry replaces one binding).
`Built.base_hash` (`base_digest`) hashes the config WITHOUT those three keys, so equal hashes prove that only the library changed; `run_tieout` raises `TieoutError` `the stacks do not share one base config (X1): base hashes {...}` otherwise.
That guard is defensive: an overlay key outside the three is refused earlier as `[CFG-STACK]`, and `--set` / `sets=` is applied identically to every stack, so it could not be triggered through the CLI or `run_tieout` (not reproduced). If it ever appears, look for something non-deterministic in loading
(an environment variable read by `${VAR}`, a `$call` that returns a different object per load), not at the overlays.
Consequences: `registry.allow` (which lets your package be imported) belongs in the BASE config; the conventions block is one block for every stack (a library that cannot honour a token must raise `CFG-CONVENTION-UNSUPPORTED`, never adapt it).
The report's last sections print each stack's factory, wrap and EVERY binding with scale/offset/sign/literal kwargs (`NOT IDENTITY: sign -1.0`): a `bind` can hide a unit conversion that the base hash cannot see, so read it.

## 2. What is compared: five levels

| level | quantities (row names) | how the row is judged |
|---|---|---|
| L0 inputs | `snapshot_digests` (the set of `(ts, role, digest)` consumed; digest taken BEFORE the wrap), `resolved_terms` (every `term_*` column of the opening trade rows, dates listed first; a position opened in one run only), `conventions_digest` | exact or `input`; NO tolerance key. `resolved_terms` compares floats within 1e-12 (relative to `max(1, abs(x))`, so absolute below 1) and dates exactly (`pd.Timestamp` equality; None and NaN match only each other; anything else `==`), so a par rate from another library that differs in the 15th digit is still `exact` (`_same` in `src/pricebt/tieout/compare.py`). No digests recorded at all is `info` (identical inputs are not proven) |
| L1 marks | `marks_rows` (same `(position, ts)` rows), `quantity` (position size), `pv`, `cash`, `financing`, per asset class | tolerance keys `L1.pv[.<asset class>]` ... |
| L2 measures | one row per audited measure column: `dv01`, `gamma`, `rate`, and `delta_ladder.<bucket>` per bucket (the UNION of the two runs' columns), per asset class | key `L2.<measure>` (a ladder bucket uses `L2.delta_ladder`), per unit of position |
| L3 layers | `layer_definitions` (ids and versions), `layer_rows`, `baseline_layers`, `layers_in_one_run_only`, each layer `carry`, `roll`, `delta`, `convexity`, `unexplained`, the baseline `tay_delta`, `tay_convexity`, `tay_unexplained`, each twice (amount and `<layer>/unit`), `reconcile[<stack>]` per run | key `L3.<layer>` (the `/unit` row takes its own key, else the layer's) |
| L4 portfolio | `equity_points`, `equity`, `cash`, `positions_value`, `trade_ledger_structure`, `trade_pv`, `trade_cash`, `stats`, `stats_ratios`, `stats_traded` | `L4.<quantity>`; the ledger's sizes follow `L1.quantity` |

Only what the run computed is compared: list your layers in `backtest.attribution.layers` in the BASE (the tie-out itself turns `attribution.baseline` on), and the measures you want compared in `audit_measures`.
A difference is `|a - b| / max(|reference|, floor)` (`_rel`); `where` in the report is the position and timestamp of the worst RELATIVE difference (the worst absolute one can be elsewhere).

## 3. The seven statuses

`PASSING = ("exact", "noise", "expected", "info")` in `compare.py`; `input`, `structure` and `exceeds` FAIL the run.

| status | markdown verdict | meaning | pass? |
|---|---|---|---|
| `exact` | exact | max abs difference is 0.0 (bit-identical) | yes |
| `noise` | within tolerance | `max_rel <= tol.rel` | yes |
| `expected` | expected definition difference | beyond tolerance but the declared tolerance says `expected: true` (a known DEFINITION difference, with a `reason`) | yes |
| `info` | info | nothing to compare: NaN on both sides ("the measure is not bound for these instruments, or was never computed"), no digests recorded, layer ids/versions differ between stacks, extension layers of one adapter | yes |
| `exceeds` | EXCEEDS | beyond tolerance: a model or convention difference, or a bug | NO |
| `input` | explained by inputs | beyond tolerance, but EVERY violating value is at a position or timestamp an L0 difference already explains (resolved terms of that position differ; a snapshot digest differs at that timestamp in a market role the instrument reads; the conventions digests differ). L0 rows themselves are `input` when they differ | NO |
| `structure` | STRUCTURE | the two runs do not have the same rows, keys or NaN pattern: a column that one run lacks or broke, another number of trades, another equity index | NO |

## 4. Reading a report top down (what to look at, in this order)

1. The header line: `Verdict: PASS|FAIL (<n> quantities, <k> failing)`, then the CLI's last lines `harness self-test: passed` and `TIE-OUT PASSED` or `TIE-OUT FAILED: <pair>: L2.dv01; ...` (exit 0 / 1). A failing self-test aborts before any comparison (section 7).
2. `## L0 inputs`: three rows must be `exact` (the same snapshots, the same terms resolved, the same conventions). `resolved_terms` `where` lists `P000001 effective: 2024-05-29 vs 2024-05-30` with dates FIRST: an L0 difference explains what follows.
3. `## L1 marks`: `marks_rows` `structure` means the runs priced different position/timestamp sets (a run that skipped marks); then `pv`, `cash`, `financing`, `quantity`.
4. `## L2 measures`, `## L3 layers`, `## L4 portfolio` in that order: the FIRST failing level is the lead, the later ones usually follow from it.
5. `## Unexplained share`, `## Largest offenders` (position, ts, both values, relative difference: the place to start a one-position localisation), then `## Stacks: what each stack owns` and `## Tolerances used` (each key with its floor UNIT and its `reason`).

Traps when reading:

* The `note` column of an EXACT row still prints the row's description (`snapshot_digests ... exact ... the two runs did not consume identical snapshots`). Read the verdict, not the note.
* An `input` cascade can leave `L4.stats` and `L4.stats_traded` as `exceeds` (their rows are keyed by statistic name, not by position or timestamp, so they cannot be attributed). Measured on the wrong-calendar control: every other level `input`, these two `exceeds`. The cause is still L0.
* `L3.<layer>` and `L3.<layer>/unit` are the same numbers for a position of size 1; for a sized position the `/unit` row isolates the library from the held size.
* `reconcile[<stack>]` `exceeds` means that run's own ledger identity failed (the note names the failing checks): a bug in the engine inputs of ONE stack, not a cross-stack difference.

## 5. Output files (`--out DIR` or `run_tieout(out=DIR)`)

`<reference>_vs_<name>.md` and `.html` (the report), `.summary.parquet` (`TieoutReport.frame()`: level, quantity, asset_class, n, max_abs, max_rel, where, tol_rel, tol_floor, status, note), `.offenders.parquet`, and `tieout.json` (`header` and per pair `passed`, `failures`, `header`, `disclosure`).
The console prints the markdown of every pair (`to_markdown`) and then one line per stack (`stack <name>: factory ...; wrap ...; bindings not identity: ...`).

## 6. Python objects (`pricebt.tieout`)

`run_tieout(base, {name: [overlay, ...]}, *, sets=(), tolerances=None, audit_measures=("dv01", "gamma", "rate"), baseline=True, reference=None, selftest=True, out=None, top=10) -> TieoutResult`
(`base` and overlays are paths or dicts; the reference defaults to the FIRST stack). `TieoutResult`: `.reports` (`{"<ref>_vs_<name>": TieoutReport}`), `.results` (`BacktestResult` per stack), `.builts`, `.selftest`, `.header`, `.passed`.
`TieoutReport`: `.rows` (`Row`: level, quantity, asset_class, n, max_abs, max_rel, where, tol_rel, tol_floor, status, note), `.passed`, `.failures()`, `.level_rows("L2")`, `.frame()`, `.offenders_frame()`, `.max_abs(level, quantity)`, `.tolerances` (the used keys), `.disclosure`.
`run_stack(base, overlays, *, sets, audit_measures, baseline) -> (Built, BacktestResult)` runs one stack alone with the audit on (`result.record.audit` has `inputs`, `marks`, `layers`).
`compare(a, b, names=(ref, other), tolerances=Tolerances(...))` compares two results that both carry an audit.

**The ladder is compared only through this API**: `DEFAULT_AUDIT_MEASURES` is `("dv01", "gamma", "rate")`, the CLI has no flag, and `run_stack` overwrites `backtest.audit` from `audit_measures`
(a `--set backtest.audit=...` is discarded). Pass `audit_measures=("dv01", "gamma", "rate", "delta_ladder")`.

## 7. The harness self-test

Unless `selftest=False` (CLI `--no-selftest`: "a result without it is not to be trusted"), the REFERENCE stack is run a second time and compared with its first run; every row must be `exact` or `info`, else
`TieoutError: harness self-test failed: the same stack run twice differs at <level>.<quantity> (<status>, max abs <x>)`. It proves the harness and the market data are deterministic. Two consequences for a bank library:
a live data source that changes between the two runs fails it (freeze the data into snapshots first, see `pricebt-market-data-snapshots`), and if YOUR stack is the reference (`--reference NAME`) the self-test is a determinism test of your library
(threads, caches, wall-clock). Known-answer self-tests of the harness itself: `tests/test_tieout_selftest.py` (a flipped day count on one side is detected at L1 and located; a shifted calendar shows first at L0).

## 8. Known limits (facts, each executed)

1. **A failing stack aborts the whole tie-out.** `run_tieout` runs each stack in a loop with no error handling: the CLI prints `error: [MarketDataUnavailable] market data unavailable at 2024-06-20 17:00:00-04:00: 1 overnight fixings missing between 2024-05-28 and 2024-06-19; first 2024-06-19` and exits 2, and there is NO report
   (the negative control `acme_swap_no_holidays.yaml`). To get a report of a run that fails part-way, set `backtest.on_error: record` in the base (`--set backtest.on_error=record`): the run continues, and the report shows `L0.resolved_terms` `input` and `L1.marks_rows` `structure` ("6 rows only in reference").
   **What `record` covers (`Engine._guarded`):** only `PricebtError` subclasses (`MarketDataUnavailable`, `MethodCallError`, `MeasureError`, and a `ConfigError` raised while a trade is built, for example `[CFG-CONVENTION]`) raised while marking, executing, closing, computing layers or signals. It does NOT cover:
   a library exception that is not a `PricebtError` (executed: a bound function raising `RuntimeError("library blew up")` prints `error: [RuntimeError] library blew up`, exit 2, under `raise` AND under `record`; translate the exception in the adapter, `acme_adapter/_compat.py: translate`),
   a `ConfigError` raised while the config is loaded or built (`validate`, `[CFG-ALLOW]`, `[CFG-IMPORT]`, a binding that does not fit), and exceptions raised inside triggers or actions. A `MethodCallError` DOES become a row (executed: `n_errors 3`, exit 0).
   Caution: under `record` a run whose every trade fails to build (a bad conventions value) also exits 0, with `n_fills 0` and the failures only in `res.errors`: read `n_fills` and `n_errors`, not the exit code.
2. **A failing audit measure is a NaN cell, not an error.** The engine catches the exception (`Engine._audit_mark`), writes NaN and appends `{ts, where: "audit", position, error}` to the run's errors. The report only says `the measure column exists in reference only: it is missing or broken in the other run (57 values are NaN on one side only)`.
   The real message is in `result.errors[result.errors["where"] == "audit"]["error"]`, for example `MeasureError: measure 'delta_ladder': key '30y' is not a canonical tenor (upper-case <int><D|W|M|Y>, e.g. '3M', '10Y')`. When BOTH runs fail, the row is `info` ("NaN on both sides"): a measure that
   fails everywhere looks harmless. Only a measure the instrument does not bind is a silent NaN by design (`MeasureNotBound`).
3. **Small ladder buckets are judged absolutely.** Below the floor a difference is compared with `rel * floor` (for `L2.delta_ladder` that is 0.1 currency per bp per unit, see `tolerances.md`): a bucket of 2.8 that is off by 3.5% passes as `noise`, a 2x error on it fails.
   The power of the ladder tie-out is therefore concentrated in the large buckets; also compare the ladder directly against the reference in a unit test (tight tolerance, two directions, two notionals).
4. **Shipped floors assume unit-sized templates** (per unit of a 1mm swap or a bond of face 100). A run of much smaller units declares its own floors (with a `reason`), or a ladder off by a factor of two whose every bucket is below the floor stays `noise`.
   Executed: the wrong-sign stack of the worked example on ONE payer of notional `1e-3` (`--set 'strategy.triggers.0.actions=[{... terms: {side: pay, maturity: 5Y, notional: 1e-3, fixed_rate: par}}]'`) prints `Verdict: PASS` and `TIE-OUT PASSED`: the flipped `dv01` differs by 9e-7 and the absolute tolerance below the floor is `rel * floor` (1e-4).
   A tie-out is only as sharp as the SIZE of what it compares: keep the base's trades at the size the floors assume, and assert it in the test (`adapter-test-templates.md`).
5. **A tie-out that compared nothing PASSES.** With `--set 'strategy.triggers.0.actions=[]'` (no trades) the report says `19 points, 0 positions`, `snapshot_digests` `info`, and `TIE-OUT PASSED`; nothing warns. Assert in your test that `rep.header["n_positions"]` and the row counts `n` of the L1/L2/L3 rows are what you expect,
   and that at least one position has a payment inside the window (the worked example's test `test_the_tie_out_is_not_vacuous`).
6. **The CLI audits `dv01`, `gamma`, `rate` only** (section 6). **An extension measure of your Kit can be tie-out audited only if EVERY stack defines it** (the same identifier in each Kit's `extra`). `audit_measures` is validated when each stack is BUILT, so a one-sided name never reaches the comparison and there is no `structure` row:
   the stack that lacks it aborts the whole tie-out with no report, exit 2 (executed, `audit_measures=("dv01", "gamma", "rate", "acme_zero_dv01")` against the reference stack: `[CFG-REF] backtest.audit.measures: backtest.audit.measures: measures ['acme_zero_dv01'] not defined by any instrument (known: ['delta_ladder', 'dv01', 'dv01_zero', 'gamma', 'gamma_zero', 'notional', 'pv', 'rate'])`;
   with the reference stack's own extension `dv01_zero` the acme stack fails the same way, `... (known: ['acme_zero_dv01', ...]); did you mean 'dv01'?`). Compare a measure that only your library has in a unit test against a hand-derived value instead.
   Name an extension measure with a plain identifier such as `acme_zero_dv01` (the worked example's), not a dotted one: `compare.py` reads `<measure>.<bucket>` as a ladder bucket and takes the tolerance key from `q.split(".")[0]`, and `validate_declaration` wants an identifier,
   while `src/pricebt/contracts/spec.py` says extension names "must be namespaced (for example 'lib.name')" and the shipped `dv01_zero` has no dot. A dotted name loads; it was not run through a tie-out (read from the code, not executed).
7. **Every stack is compared with the reference, not with each other**: `reference_vs_a` and `reference_vs_b`; a difference between `a` and `b` is inferred, or run them as reference and other.
