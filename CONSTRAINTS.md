# Constraints (pricebt refactor)

Source of truth: `docs/design/11-refactor-spec.md` (sections 2, 9, 11.5, 12, 15). This file is the written bar for the refactor. It is NEVER
weakened to make a change pass. Tightening is silent; loosening needs a change-log entry (`docs/design/11-refactor-changelog.md`) and, for a
MUST, a spec edit FIRST (spec 15).

Last reviewed: 2026-09-26 (refactor start). Baseline: 742 passed, 1 skipped, 743 collected (snapshot `..\pricebt-baseline`, ids in `tasks/baseline_test_ids.txt`).

Run everything from the project root with `set PYTHONPATH=src` and `C:\Users\chris\anaconda3\envs\stir\python.exe` (never from inside `src/pricebt`).

## Floor (always enforced)

1. No skipped, xfail-ed, deleted or weakened tests without a change-log entry naming the requirement id. A test removed from the baseline id list
   (`tasks/baseline_test_ids.txt`) must appear in the change log's "Tests removed or changed" table (checked by `tools/floor_check.py`).
2. No new suppression comments in `src/` or `tests/`: `# noqa`, `# type: ignore`, `# pragma: no cover` (existing ones are counted and must not grow).
3. No unimplemented stubs standing in for required behaviour: `raise NotImplementedError`, bare `pass` bodies, empty `except`, `TODO` in place of code.
4. No tolerance widened, no threshold raised or lowered, to turn a run green without a written explanation (spec X3); every tolerance lives in
   `tasks/tolerance_ledger.yaml` with its value, reason and date.
5. No banned token added to core and none removed from `tests/guards/banned.yaml` without a spec change (spec G-4).
6. ARBS (`C:\Users\chris\clee\ARBS`) and gs-quant are read-only. No live ARBS code runs unless `PRICEBT_LIVE_ARBS=1`.
7. No `git commit` / `git init` unless asked. No secrets in source.
8. A checking script is first run on an input whose answer is known (user standing rule); a guard test has a non-vacuity twin (spec 9.2).

## Enforced with numbers

| Dimension | Rule | Checked by | Runs at |
|---|---|---|---|
| Zero dependence, import level (Z1) | `import pricebt.<every core module>` succeeds with the import blocker installed; blocker raises on banned import roots | `tests/guards/` GT-Z1a | default suite |
| Core-only run (Z1, Z6, SC2) | `pytest -m core` passes with rateslib, QuantLib, ARBS, gs_quant blocked | `python -m pytest -m core` under the blocker (`tests/guards/blocker.py`) | after every module |
| Name level (Z2) | no banned token in core identifiers, non-docstring literals, schema keys/values, shipped core configs | GT-Z2a/b/c | default suite |
| Shape level (Z3) | unbound required name rejected; mismatched-names toy runs; loader never imports an adapter by config text | GT-Z3a/b/c | default suite |
| Semantic level (Z4) | no convention token in core outside the schema vocabulary and the labelled compat module | GT-Z4 | default suite |
| Provenance (Z5) | no ARBS import/token in `src/` | GT-Z5a/b | default suite |
| Non-vacuity | every GT-* has a twin planting ONE violation in a temp copy and asserting the guard names exactly it | `tests/guards/test_twins.py` | default suite |
| Mutation check | every new behavioural test: break the covered code, confirm failure; recorded in the change log | manual + `docs/design/11-refactor-changelog.md` | per module |
| Regression | baseline test ids present or listed as removed/changed with reason | `tools/floor_check.py` | per module |
| Partition | every test carries at least one of the markers core / adapter_rateslib / adapter_quantlib / fixtures / live_arbs (`core` is exclusive, `fixtures` may co-occur with an adapter marker); an unmarked test is a collection error (G-5). The hook is switched on once every file is marked. | conftest collection hook | default suite |
| Tie-out | tolerances per level/asset in `tasks/tolerance_ledger.yaml`; exceedances explained in the report | tie-out harness + report | tie-out run |
| Harness self-test (X5) | identical stacks -> exactly 0; mutated convention detected at L1; refstack == hand-derived answer to machine precision | `tests/test_tieout_selftest.py` | default suite (both libs present) |
| Golden numbers (A2) | seasoned 3y payer 50mm: carry -561.82, roll 12,159.98, fixings -10,627.91, resample 111.52, delta 70,190.64, convexity -50.23, residual 0.02, total 71,222.20 | rateslib adapter tests | default suite |
| Notebook (SC15) | `notebooks/showcase_swap_book.ipynb` executes from a clean kernel on fixtures, assertion cells pass, saved outputs | `tools/nb_build.py` + `nbclient` re-execution | end |
| External opinion | at least one check is independent of the project's own tests: the hand-derived refstack answers (X5c), the import blocker at interpreter level, and QuantLib vs rateslib themselves | tie-out harness | per module |

## Success criteria (spec 11.5): all must hold at the end

SC1..SC16 as written in the spec. The final report gives pass/fail and evidence per criterion.

## Measured, not yet enforced (ratchets)

| Metric | Today | Direction |
|---|---|---|
| Baseline tests | 742 passed / 1 skipped | migrated or removed-with-reason; total passing must not fall without a change-log row |
| Full suite wall time | 236 s (spec 3.1) | must not grow more than 2x |
| Suite-run reproduction | recorded `results/` of the 34 swap (suite S10, the swaption straddle, is out of scope: Q15) and 27 bond runs | reproduced within the stated tolerance or the difference explained (spec 9.5) |

## Exceptions

None. Add a row (id, rule, path, reason, owner, expiry) before quietly working around anything.
