---
name: pricebt-conformance-and-tieout
description: Proves a pricebt adapter right with two checks that catch different faults, the layer conformance kit (independent identities, no second library needed) and the level-by-level tie-out against the reference stack (CLI or run_tieout). Use when an adapter builds and prices and you must show it conforms, run or read a tie-out report, or declare a tolerance.
---

# pricebt-conformance-and-tieout

## Purpose

An adapter is accepted on two proofs. The **layer conformance kit** values your adapter's own bound spec under shocked and rolled synthetic worlds and checks identities that do not come from the layers
(full revaluation = delta + convexity, mirror symmetry, cash-sweep continuity, a bounded residual). The **tie-out** runs ONE base config under your stack and the dependency-free reference stack and compares five levels (L0 inputs .. L4 portfolio).
Neither is enough alone: a `dv01` without its sign passes the kit and fails the tie-out; your library's own layer definition can only be judged by the kit (`references/conformance-kit.md`, the fault table).

## Prerequisites

* An adapter package with a Kit (`swap`: factory + default bindings), a `wrap`, a conventions block and the four layers, importable from a directory you control (`pricebt-instrument-kit`, `pricebt-wrap-and-pricer`, `pricebt-layers-and-ladder`).
* The repository root as working directory; `PYTHONPATH=src;tests;<parent directory of your adapter package>` (`:` separators on Linux); the Python interpreter that has pricebt's dependencies installed
  (`python -c "import pandas, pyarrow, yaml, tqdm"` must print nothing; a machine can carry several interpreters with different library versions). Commands below say `python`: it must be that interpreter.
* A BASE config that lists your package under `registry.allow` and an OVERLAY that names your factory and wrap (`pricebt-run-config-and-reports`; the model is `skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml` and `acme_swap.yaml`).
* Deterministic market data: both stacks must read the same snapshots and the reference stack run twice must be identical (`pricebt-market-data-snapshots`).

## Steps

### A. The layer conformance kit (`src/pricebt/testing/layer_conformance.py`)

1. Build the spec from YOUR Kit and the shared conventions block, with the calendar NAME the synthetic world uses. Pass `allow=` naming your package whenever a default binding has a `function` target
   (`tests/test_layer_conformance_adapters.py` omits it and would fail for such a Kit with `[CFG-ALLOW] bind.carry.target.function: ...`):
   `spec = build_spec("ois", {"factory": A.swap, "conventions": {**A.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}}, schemas=SchemaRegistry.default(), allow=("pricebt", "acme_adapter"))`.
2. Fill `Setup(spec, wrap, terms, mirror_terms, world, t0, t1)`; worlds are `flat_swap_world("cal", holidays=HOL)` and `flat_bond_world(SEC, "cal", holidays=HOL)` (library-free, re-exported by the kit module). Fields: `references/conformance-kit.md`.
3. Run and assert: `report = run_kit(setup); report.assert_ok()`. `run_kit` never raises on a failing identity; `report.failures()` lists the rows, `report.frame()` is a DataFrame, `report.unexplained_share` reports the residual for a static and a moved market.
4. Cover four shapes: a fresh swap; a SEASONED swap across a payment date (`birth`, `t0`, `t1`, `payment_window`: without `payment_window` the cash-sweep check is silently skipped); the same over a COARSE step (`t1 = t0 + 9 days`, `payment_window` set: a flow paid inside the interval can depend on a fixing published inside it, a path a one-day step never runs; the runnable script is `coarse_step_kit.py` in `skills/pricebt-layers-and-ladder/references/kit-blind-spots.md`, section "The coarse step and the cash window", and `test_the_layers_conform_over_a_coarse_step` in `references/adapter-test-templates.md`); a bond if the library prices bonds.
5. Prove the kit has teeth on YOUR adapter: bind one layer without its sign and assert `run_kit` fails `fd_vs_delta_convexity`. `build_spec` has no `bind=` keyword; put `"bind": {"delta": dict(A.SWAP_BIND["delta"], sign=1.0)}` in the RAW MAPPING
   of step 1 (`spec_with` in the template; `A.SWAP_BIND` is the Kit's `default_bind`).
6. Put 2-5 in a test file with a partition marker (`references/adapter-test-templates.md`, executed).

### B. The tie-out (`src/pricebt/tieout/`, ADR 006)

7. Write the overlay: it may set only `instruments.<n>.factory`, `instruments.<n>.bind`, `market.pricers.<r>.wrap`. `registry.allow` goes in the BASE (an overlay carrying it is `[CFG-STACK] registry: a stack may not set 'registry'`).
   The whole overlay of the worked example:
   `instruments: {usd_sofr_ois: {factory: "acme_adapter:swap"}}` and `market: {pricers: {primary: {wrap: "acme_adapter:wrap"}}}` (`example/config/acme_swap.yaml`).
8. Run the CLI (PowerShell; `$ex` is the example directory, replace it by yours):
```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example"
$ex = "skills/pricebt-wire-external-library/example"
python -m pricebt tieout $ex/config/acme_tieout_base.yaml --stack reference=configs/adapters/refstack_swap.yaml --stack acme=$ex/config/acme_swap.yaml --reference reference --out <dir>
```
   Exit codes: **0** `TIE-OUT PASSED`; **1** `TIE-OUT FAILED: <pair>: <level>.<quantity>; ...` (a comparison failed); **2** any error (`error: [CFG-STACK] ...`, `error: [TieoutError] a tie-out needs at least two stacks`, a missing file, an import failure, a stack that raised).
   `--stack NAME=overlay.yaml[,overlay2.yaml]` repeats per stack (a comma joins SEVERAL overlays of ONE stack, for example `--stack reference=configs/adapters/refstack_swap.yaml,<second overlay>`; the same NAME twice is `error: [CLI] --stack 'reference' is given twice`); `validate` and `run` spell it differently, a repeated `--stack a.yaml --stack b.yaml` with no NAME, and the comma form fails there
   ([CE-STACK-SYNTAX](../pricebt-wire-external-library/references/common-errors.md#ce-stack-syntax)). `--reference` defaults to the first; `--set k=v` changes the base for every stack; `--no-selftest` skips the harness self-test (never trust such a result); `--top N` sets the offenders listed.
9. To compare the LADDER use the Python API; it is the ONLY way (the CLI audits `dv01`, `gamma`, `rate`):
```python
from pricebt.tieout import run_tieout
ex = "skills/pricebt-wire-external-library/example/config"
res = run_tieout(f"{ex}/acme_tieout_base.yaml", {"reference": ["configs/adapters/refstack_swap.yaml"], "acme": [f"{ex}/acme_swap.yaml"]},
                 audit_measures=("dv01", "gamma", "rate", "delta_ladder"))
rep = res.reports["reference_vs_acme"]
print(res.header["selftest"], rep.passed, [(r.level, r.quantity, r.status) for r in rep.failures()])
```
   Printed: `passed True []` (executed: reference against acme, ladder audited, nothing declared).
10. Read the report top down, in this order (`references/tieout-reference.md`): the verdict and self-test lines; L0 must be `exact` (snapshot digests, resolved terms, conventions digest); the FIRST failing level is the lead; then `Largest offenders`, `Stacks: what each stack owns`, `Tolerances used`.
    Seven statuses: `exact`, `noise`, `expected`, `info` pass; `exceeds`, `input`, `structure` fail. `input` means an L0 difference already explains the row (fix L0 first).
11. When something fails, do not touch a tolerance: go to `pricebt-debug-tieout-differences`.
12. Tolerances (`references/tolerances.md`): the shipped defaults are the goal; a widened key is declared as `tieout.tolerances.<key>: {rel, floor, reason}` in the base, with a measured mechanism, and gets an entry in `tasks/tolerance_ledger.yaml`.
    The reason and the ledger entry are repository POLICY (`AGENTS.md`), checked in review: the loader accepts a bare number or a mapping without `reason` (it only requires `rel`, and a non-empty `reason` if one is given), and no code reads the ledger.
    Enforce it yourself with the template's `test_every_declared_tolerance_carries_a_reason`. Never widen a tolerance to turn a run green.
13. Pin the wiring: write one negative-control overlay per mistake and a test that each fails at ONE located level and quantity, after showing it does not fire on the correct wiring (`pricebt-debug-tieout-differences`).

## Checks

| command | expected |
|---|---|
| `python -m pytest tests/test_<yourlib>_proofs.py -q -o addopts= -p no:cacheprovider` (the template) | all pass; the kit test with the sabotaged sign passes because `run_kit` FAILS on the sabotaged spec |
| the CLI of step 8 | prints `harness self-test: passed` and `TIE-OUT PASSED`, exit 0 (a few seconds for the worked example) |
| the same with a third `--stack wrong_sign=$ex/config/mistakes/acme_swap_wrong_sign.yaml` | exit 1, `Verdict: FAIL (<n> quantities, 5 failing)` for `reference_vs_wrong_sign`, last line `TIE-OUT FAILED: reference_vs_wrong_sign: L2.dv01; reference_vs_wrong_sign: L3.tay_delta; reference_vs_wrong_sign: L3.tay_delta/unit; reference_vs_wrong_sign: L3.tay_unexplained; reference_vs_wrong_sign: L3.tay_unexplained/unit`; `reference_vs_acme` still `Verdict: PASS`. Each failing row prints as `<pair>: <level>.<quantity>`, the `/unit` twin included |
| `python -m pytest tests/test_layer_conformance.py -q -o addopts= -p no:cacheprovider` | the kit itself is checked on sabotaged reference specs (one fault per test) |
| `python -m pytest tests/test_tieout_selftest.py -q -o addopts= -p no:cacheprovider` | the harness catches a flipped day count and a shifted calendar |
| the Python API snippet with `audit_measures=(..., "delta_ladder")` | `rep.rows` contains one `delta_ladder.<bucket>` row per tenor of your ladder |

## Common failures (real messages)

* `[CFG-ALLOW] bind.carry.target.function: ... 'acme_adapter.swap' is not under an allowed prefix ['pricebt']`: a spec built without `allow=`: add `allow=("pricebt", "<your package>")`. The same message from `error: [CFG-ALLOW] market.pricers.primary.wrap: ...` is a BASE config without `registry.allow`: [CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow).
* `error: [CFG-IMPORT] ... cannot import 'acme_adapter': No module named 'acme_adapter'`: [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import).
* `error: [CFG-STACK] instruments.usd_sofr_ois.conventions: a stack may not set ...`: a stack cannot change conventions, terms or the strategy: a difference must not be a convention. Fix the base for every stack or the library (`registry` in an overlay: [CE-STACK-REGISTRY](../pricebt-wire-external-library/references/common-errors.md#ce-stack-registry)).
* `error: [TieoutError] the stacks do not share one base config (X1): base hashes {...}`: a defensive guard that should be unreachable through the CLI or `run_tieout` (an overlay key outside factory/bind/wrap is refused earlier as `[CFG-STACK]`, and `--set` is applied to every stack alike).
  If it appears, something non-deterministic differs between the stack loads (a `${VAR}`, a `$call` result): look at the loading, not at the overlays.
* `ModuleNotFoundError: No module named 'acme_adapter'` and `Interrupted: 1 error during collection` when pytest collects your test file: the test file must insert your package's directory itself (the template's `PKG_DIR` block; [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)). One such file stops every test of the run.
* `[CFG-REF] backtest.audit.measures: backtest.audit.measures: measures ['<ext>'] not defined by any instrument (known: [...])` and exit 2, no report: an extension measure passed in `audit_measures` that some stack does not define. Only a measure that EVERY stack's Kit defines can be tie-out audited (`references/tieout-reference.md`, limit 6).
* `harness self-test failed: the same stack run twice differs at ...`: the reference (or the market data) is not deterministic: freeze the snapshots, or, if your library is the reference, find the thread, cache or clock dependence.
* `error: [MarketDataUnavailable] market data unavailable at ...` and NO report: one stack raising aborts every stack. `--set backtest.on_error=record` (or the same key in the base) turns it into a report with `L0.resolved_terms` `input` and `L1.marks_rows` `structure`.
  `record` helps ONLY for a `PricebtError` raised while marking or executing (`MarketDataUnavailable`, `MethodCallError`, `MeasureError`). A raw library exception (`error: [RuntimeError] library blew up`, exit 2 under `record` too) must be translated in the adapter,
  and an error raised while the config is loaded or built is never recorded (`references/tieout-reference.md`, limit 1).
* `L2.delta_ladder.<bucket>` `structure`, note `the measure column exists in reference only: it is missing or broken in the other run`: the audit turned an exception into NaN. The real text is only in
  `res.results["<stack>"].errors` (`errors[errors["where"] == "audit"]["error"]`), e.g. `MeasureError: measure 'delta_ladder': key '30y' is not a canonical tenor (upper-case <int><D|W|M|Y>, e.g. '3M', '10Y')`.
* No `L2.delta_ladder.*` rows at all: the ladder is not in `audit_measures` (the CLI cannot audit it; use the Python API): [CE-LADDER-CLI](../pricebt-wire-external-library/references/common-errors.md#ce-ladder-cli).
* `binding 'value': method 'value' called with args=[] kwargs=['ctx']: ...`: a `TypeError` raised INSIDE your bound callable, worded like a signature mismatch: [CE-TYPEERROR](../pricebt-wire-external-library/references/common-errors.md#ce-typeerror).
* A `noise` row on a small ladder bucket that is really wrong: below the floor the tolerance is absolute (`rel * floor`), see `references/tolerances.md` section 4.
* `TIE-OUT PASSED` and yet `0 positions` in the header, or a flipped sign that passes because the base's positions are tiny (executed: a payer of notional `1e-3` with the wrong `dv01` sign passes): the shipped floors assume unit-sized templates and nothing warns.
  Assert `rep.header["n_positions"]`, `n_points` and the row counts in your test (`references/adapter-test-templates.md`) and keep the base's trades at the size the floors assume.
* `layer conformance failed:` followed by `<check>/<quantity>: <value> vs expected <expected> (tolerance <tol>) <note>`: read `check` first: `fd_vs_delta_convexity` = a layer's sign, unit or reference date; `mirror` = an offset or a side-dependent bug;
  `cash_sweep` = carry does not equal the cash booked; `unexplained_share` = the layers leave the move unexplained.
* Kit passes, tie-out fails on `L2.dv01` / `L2.rate` / `L2.gamma` with `max rel` 2.0 (a ratio of -1: the sign) or 0.99 (a ratio of 0.01: percent against decimal): a `sign` or `scale` on a measure binding (the kit cannot see it): `pricebt-bindings-cookbook`.
  `max rel` is the report column; the ratio is other / reference of the audited column (`pricebt-debug-tieout-differences`).
* Only the `L3.tay_*` rows fail while L1 is exact and the library layers pass: the engine's baseline reads `dv01`, `gamma` and `rate`, so the cause is a sign or unit of one of those three at L2 (`pricebt-debug-tieout-differences`).

## Related skills

`pricebt-debug-tieout-differences` (a failing report to a cause), `pricebt-run-config-and-reports` (base config, overlays, CLI, results), `pricebt-layers-and-ladder` (the four layer definitions the kit checks), `pricebt-bindings-cookbook` (sign, scale, offset),
`pricebt-guards-and-packaging` (partition markers and guards for the tests), `pricebt-wire-external-library` (the master playbook and the worked example).
