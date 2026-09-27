# pricebt v2 — autonomous decisions log

Format per entry: date, situation, rule applied (IMPLEMENTATION_PLAN §9), decision, evidence, alternative considered.
Written as decisions happen, not reconstructed at the end.

---

## 2026-09-27 — P5.2 (live ARBS run) deferred by rule

**Situation:** IMPLEMENTATION_PLAN §9 and the hand-off prompt require P5.2 (the live ARBS run) to be
deferred in autonomous mode: ARBS must never be imported or executed by this session.

**Rule applied:** IMPLEMENTATION_PLAN §9, row "P5.2 (live ARBS run), or any execution that imports ARBS".

**Decision:** P5.2 is marked deferred. `tests/test_live_arbs.py` will be written with marker `live_arbs`,
skipped unless `PRICEBT_LIVE_ARBS=1`, and `docs/v2/LIVE_ARBS_REPORT.md` will give the user the exact
commands and expected results. This session will not run them, will not raise `_LAST_SAFE`, will not
enable NOJUMPS, and will not add any network-capable path.

**Evidence:** HANDOFF_PROMPT.md "Hard safety limits"; IMPLEMENTATION_PLAN.md §9 table row 1; DESIGN.md
decision 0.2.

**Alternative considered:** none — this is a hard safety limit, not a judgment call.

---

## 2026-09-27 — Pre-flight environment checks (before P0)

**Situation:** Before starting P0, verified the environment the plan assumes.

**Rule applied:** n/a (verification, not a plan deviation).

**Findings:**
- `C:\Users\chris\anaconda3\python.exe`: `gs_quant.__version__ == "1.5.4"` — confirmed.
- `C:\Users\chris\anaconda3\envs\stir\python.exe`: pandas 2.3.1, numpy 2.2.6, and pytest 9.0.2, nbformat,
  nbclient, ipykernel all already importable — no installs needed for the `test` extra.
- `C:\Users\chris\clee\gsquant-temp-claude\gs-quant` is checked out at tag `release-2.1.17` — confirmed
  as the port source DESIGN.md names.
- `pricebt-v2/.gitignore` patterns are already anchored (`/results/`, `/results_new/`); `git check-ignore`
  on `tests/data/x.json` and `src/pricebt/risk/results.py` returns no match, so P1.6's JSON snapshots and
  the `risk/results.py` module will not be silently ignored the way `src/pricebt/results` was in commit
  `b5bbc28`.

**Decision:** proceed to P0 with no environment remediation needed.

---

## 2026-09-27 — P0.1: `target/__init__.py` added to the skeleton

**Situation:** DESIGN.md section 3.2's literal skeleton list under `src/pricebt/` names
`target/common.py`, `target/measures.py`, `target/backtests.py` as a package but never lists
`target/__init__.py`, without which `target/` is not an importable Python package.

**Rule applied:** IMPLEMENTATION_PLAN.md section 9 row "A MUST seems impossible as written": "Find
the closest design that keeps all five MUSTs, even if it costs more code. Record it in the log. Do
not relax a MUST." A `target/` package with submodules but no `__init__.py` cannot satisfy MUST-2
("same module paths" as gs, i.e. `pricebt.target.common` importable) as written, so this is treated
as that row, not as a plan/DESIGN.md disagreement (the plan's introduction, not section 0.7, is what
says "if this plan and DESIGN.md disagree, DESIGN.md wins" — section 0.7 is the unrelated
"Definition of done").

**Decision:** added `target/__init__.py` (docstring-only stub, like the other skeleton files) to the
skeleton, `tests/guards/dag.py`'s `SKELETON_PATHS` (the literal path list `test_skeleton.py` checks
against) and its `DAG_TIERS`.

**Evidence:** DESIGN.md section 3.2, the `src/pricebt/` tree under "Package layout"; a package
directory with submodules but no `__init__.py` is not importable as `pricebt.target.common` et al.

**Alternative considered:** leaving `target/` un-packaged and importing its files by path — rejected,
contradicts MUST-2's "same module paths" (`pricebt.target.*` mirrors gs's own `gs_quant.target.*`).

---

## 2026-09-27 — P0.1: import-DAG interpreted as ordered tiers, not a strict linear chain

**Situation:** DESIGN.md section 3.2's "Import DAG (MUST)" lists a sequential arrow chain (`errors →
base → common → datetime → risk → risk.results → risk.transform → markets → instrument →
markets.portfolio → assets.{...} → assets.pricing → session → data → backtests.*`), but the same
section's package-layout table has `risk/__init__.py` re-export from `risk.results`, and
`assets/__init__.py` expose `load_asset`/`AssetConfig`/`FxConfig` (which live in `assets.config` and
`assets.fx`). Python always runs a parent package's `__init__.py` before any submodule, so importing
bare `pricebt.risk` necessarily pulls in `pricebt.risk.results` too — a strict "M then nothing named
after M" reading is unsatisfiable by the design's own target state (trap 2 in the P0.1 task brief
flagged this risk before it was hit).

**Rule applied:** IMPLEMENTATION_PLAN.md section 0.7.3 (a guard "has tests that fail when the logic
is broken") plus the instruction that the import-order guard "must be non-vacuous now and stay
non-vacuous later" — the assertion has to encode the DAG's real meaning, not a reading that no valid
program could satisfy.

**Decision:** `tests/guards/dag.py` models the DAG as an ordered list of **tiers** (each tier a set
of module names). A package `__init__` is grouped into the same tier as the submodule(s) its own
row of the section-3.2 table says it re-exports (`risk`+`risk.results`; `assets`+`.yamlio`+`.config`+
`.namespace`+`.fx`+`.registry`). `test_import_order.py`'s generic check is "importing M alone must
load no module from a strictly *later* tier"; the DAG text's own explicit stronger clauses
("`markets` MUST NOT import portfolio/instrument/session/assets"; "`instrument` imports session and
markets only inside method bodies"; "`risk.results` MUST NOT import `transform`") are additionally
checked by name in `test_named_must_not_import_at_top_level_clauses`, since the generic tier rule
alone would not catch those (their targets are earlier or same-tier, which the tier rule allows).
Two modules the DAG text never mentions at all: `progress` (no pricebt-internal imports; placed
right after `common`, its section-3.2 table position) and `target.*` (placed *after* `backtests.*`,
since `target/backtests.py` is a "thin re-export shim ... used by notebooks" and must therefore
depend on `backtests.*` existing, even though the package-layout table lists `target/` textually
before `backtests/`). `pricebt` (root) and `errors` are one tier, because `pricebt/__init__.py`
imports `errors` eagerly by the design's own text.

**Evidence:** DESIGN.md section 3.2 "Package layout" table (the `risk/__init__.py` and
`assets/__init__.py` rows) versus its "Import DAG (MUST)" prose; verified by mutation
(`tools/mutcheck.py`, see the entry below) — a real eager top-level import planted in
`instrument/__init__.py` and in `base.py` was killed by `test_import_order.py`, then the file was
restored.

**Alternative considered:** a strict linear order with no tiers — rejected as unsatisfiable per
above; an untiered "no module imported after M was seen" check on `sys.modules` order rather than
DAG position — rejected as strictly weaker (it cannot name *which* later module is wrong) and does
not match "assert on the actual DAG order" in the task brief.

---

## 2026-09-27 — P0.1: mutation-tested the guards before calling them done

**Situation:** IMPLEMENTATION_PLAN.md section 0.7.3 requires new logic to have tests proven to fail
when the logic is broken, and the P0.1 task brief specifically named two token-scan twins as
"easiest to get wrong": the trailing-comment must-pass case (`x = 1  # uses ARBS`) and the
letter-look-around must-fail case (`os.environ["ARBS_SUPABASE_ENABLED"] = "0"`).

**Decision:** ran `tools/mutcheck.py` against `tests/guards/scan.py` with two mutations —
(1) making comments get scanned like strings (kills the trailing-comment twin,
`test_twin_token_scan_must_pass_series_and_a_trailing_comment`), and (2) replacing the vendor
regex's letter look-arounds with `\b` (kills the `ARBS_SUPABASE_ENABLED` twin *and* the
`usd_sofr_eris_rlbasic` twin, since `\b` does not treat `_` as a boundary) — and against
`test_import_order.py` with two more mutations on real skeleton files: planting
`import pricebt.markets` in `instrument/__init__.py` (kills the named-clause test) and planting
`import pricebt.session` in `base.py` (kills the generic tier test). All four mutations were
KILLED (rc != 0, the named test in the failure output), and `mutcheck.py` restored every mutated
file byte-for-byte (verified separately by re-reading each file's content after the run). A fifth,
non-mutcheck manual check added a stray `.py` file under `src/pricebt/` and confirmed
`test_skeleton.py` fails on it, then removed the file. A sixth added `import rateslib` to a stub and
confirmed `test_import_scan.py` fails on it, then reverted.

**Evidence:** `mutcheck.py` output (in the task's final report), plus a post-run `grep -r "planted
mutation"` over `src/` and `tests/guards/` returning no matches.

**Note:** all seven twins (DESIGN.md section 12.2 item 7's list, plus one extra gs_quant-docstring
pair) live in one `tests/guards/test_twins.py`, each calling the real `tests/guards/scan.py`
functions against a synthetic `tmp_path` tree — one file, per the task brief's "your call, document
it," so every guard's twin exercises the exact same scanning code the real guard tests
(`test_import_scan.py` etc.) run against `src/pricebt/`.

---

## 2026-09-27 — P0.1: `tools/mutcheck.py` confirmed self-contained after the strip

**Situation:** `tools/mutcheck.py` is salvaged (excluded from the `tools/` wipe), but
`tests/support/mutcheck/*.json` (its spec files) were deleted along with the rest of `tests/`. Its
own docstring only says it reads "a JSON spec file passed as argv[1]" with no import of
`tests.support.mutcheck`.

**Decision:** ran `python tools/mutcheck.py` with no arguments; it failed with `IndexError: list
index out of range` at `Path(argv[1])` — a missing-argv error, not an `ImportError` — confirming it
has no import-time dependency on the deleted `tests/support/mutcheck` tree. No changes needed.

**Evidence:** command output in the task's final report.

---

## 2026-09-27 — P0.1: no `results/`, `results_new/` or `data/fixtures` present in this worktree

**Situation:** the task brief said to delete `results/`, `results_new/` (untracked run output) from
disk and to leave `data/fixtures` (git-ignored third-party data) untouched.

**Decision:** none of the three exist in this worktree (`data/` holds only `.gitignore`; confirmed
by the pre-flight check above and re-confirmed by `find` before the strip). Nothing was deleted, and
`data/.gitignore` was left as-is. Reported as "nothing to delete", not claimed as a deletion.

**Evidence:** `find . -maxdepth 1 -iname "results*"` (no output) and `find data -maxdepth 2` (only
`data/.gitignore`) before any deletion.

---

## 2026-09-27 — P0.1: `assets/yamlio.py` drops `${ENV}` interpolation, `extends`, and `--set` overrides

**Situation:** v1's `config/yamlio.py` (research/08 section 4) also had `${VAR}` interpolation,
top-level `extends:` (config inheritance), `deep_merge`, `set_path`/`apply_overrides` (`--set`
overrides) and a `ConfigError(message, *, path, code)` signature. DESIGN.md section 4.1 requires only
"a duplicate key is an error" and "`${ENV}` interpolation is off"; the new `ConfigError` signature
(section 6.6) is `(message, asset=None, key=None)`, with no `path`/`code` fields.

**Decision:** kept only `load_text`/`load_file` (hardened YAML: YAML-1.2 scalars, duplicate-key
detection) and the `_Loader` machinery. Dropped `interpolate`/`_ENV` (the design requires it off, not
merely unused), and dropped `extends`/`deep_merge`/`set_path`/`apply_overrides`: v2 has no CLI in
scope (IMPLEMENTATION_PLAN.md section 2 "Create" pyproject list has no console script) and MUST-3
requires "one self-contained config file per asset", which `extends` would undermine. The duplicate-
key error folds the `file:line:col` position into the message text (the new `ConfigError` has no
`path`/`code` fields to carry it separately).

**Evidence:** DESIGN.md sections 4.1 and 6.6; IMPLEMENTATION_PLAN.md section 2 "Create" (pyproject.toml
has no `[project.scripts]` entry, unlike v1's).

**Alternative considered:** keeping `extends`/`--set` as unused-but-present code for a possible later
use — rejected: IMPLEMENTATION_PLAN.md section 0.2 ("DO NOT") lists "DO NOT reintroduce any v1
machinery: schemas, bindings, snapshots, `wrap`, Kits, conventions blocks, stack overlays, tie-out,
reference stacks, P&L layers, `contrib/`" and config inheritance/override machinery is exactly the
kind of v1-specific apparatus MUST-3 ("one self-contained config file per asset") rules out for v2.

---

## 2026-09-27 — P0.1: `progress.py`'s notebook-display lookup must not import IPython itself

**Situation:** v1's `engine/progress.py` docstring claimed "IPython is never imported here: if
nobody imported it, this is not a notebook," but its `_notebook_display()` body did
`from IPython.display import display` when a notebook kernel was detected — a real import
statement, which `tests/guards/test_import_scan.py` (DESIGN.md section 12.2 item 1, "every import at
any depth, including inside a function body") would fail once this file were scanned, since
`IPython` is not in the allowed root set (stdlib ∪ numpy/pandas/yaml/dateutil/tqdm/pricebt).

**Decision:** changed `_notebook_display()` to read `sys.modules.get("IPython.display")` and
`getattr(..., "display", None)` instead of importing it, making the docstring's claim literally
true rather than aspirational. Behaviour is unchanged: IPython's own top-level `__init__.py` already
imports `IPython.display` eagerly, so if `sys.modules["IPython"]` exists (the kernel-detection
branch above it already requires this), `sys.modules["IPython.display"]` exists too.

**Evidence:** `tests/guards/test_import_scan.py` passing on `src/pricebt/progress.py` (part of the
green `pytest tests/guards` run); DESIGN.md section 12.2 item 1.

---

## 2026-09-27 — P0.1: `pyproject.toml` and `NOTICE` fully replaced (v1 content dropped, not merged)

**Situation:** both files already existed with v1 content (task brief point 7): v1's
`pyproject.toml` had `name = "pricebt"`, `version = "0.1.0"`, deps
`numpy>=1.26/pandas>=2.1/pyyaml>=6/tqdm>=4.66`, extras `parquet/plot/rateslib/dev`, and a
`[project.scripts] pricebt = "pricebt.config.cli:main"` entry point; v1's `NOTICE` credited gs_quant
for "design, vocabulary and logic" and had a rateslib source-availability paragraph.

**Decision:** replaced both wholesale per IMPLEMENTATION_PLAN.md section 2 "Create": `pyproject.toml`
now has `version = "2.0.0"`, `requires-python = ">=3.11"`, deps
`numpy/pandas>=2.0/pyyaml/python-dateutil/tqdm`, one extra `test =
[pytest, nbformat, nbclient, ipykernel]`, no `parquet`/`plot`/`rateslib`/`dev` extras and **no
`[project.scripts]`** (v2 has no CLI in scope — `config/cli.py` was deleted with the rest of
`config/`). `NOTICE` drops the rateslib paragraph (v2 has no rateslib dependency at all, optional or
otherwise) and cites "Copyright 2019 Goldman Sachs" (the actual gs_quant NOTICE year) plus a note
that ported files carry the original Apache-2.0 header and a changes list, rather than v1's "design,
vocabulary and logic" phrasing.

**Evidence:** IMPLEMENTATION_PLAN.md section 2 "Create"; DESIGN.md section 0's decision 0.1 ("port
the logic and design of gs_quant, do not integrate it" — pyproject.toml correctly has no
`gs_quant` dependency, live or optional).

---

## 2026-09-27 — P0.1: deleted `configs/suite/calendars/usd_fed.json` and `docs/research/mdp-feasibility.md` despite research/08's "KEEP"

**Situation:** research/08-pricebt-v1-strip-inventory.md section 8 marks
`configs/suite/calendars/usd_fed.json` as "DELETE, except KEEP" (move to e.g. `calendars/usd_fed.json`)
and `docs/research/mdp-feasibility.md` as "KEEP as reference." IMPLEMENTATION_PLAN.md section 2 "Delete"
says "everything currently in `configs/`" and "everything in `docs/` except `docs/v2/`," with no
carve-out for either file, and README.md's own reading-order table says "Where a note *proposes* a
design ..., DESIGN.md supersedes it" (research notes are evidence, not instructions).

**Rule applied:** `docs/v2/README.md` line 13 ("Where a note *proposes* a design (config key names,
variable names, a draft config, 'recommendations'), DESIGN.md supersedes it") plus
IMPLEMENTATION_PLAN.md's own introduction ("What this plan covers: what to build ... If this plan
and DESIGN.md disagree, DESIGN.md wins: stop and report the conflict") — a research note's "KEEP"
verdict is evidence about v1, not an instruction, and section 2's literal P0.1 "Delete" list is the
plan's stated *what to do*, which names no carve-out for either file.

**Decision:** deleted both with the rest of `configs/` and `docs/` (non-`v2`). Both are recoverable
from tag `v1-final` if a later phase wants the calendar file or the ARBS safety research; P5.1 (ARBS
config + docs) can re-pull `usd_fed.json` then if the example config needs a calendar, and
research/06 already carries the safety-hazard facts research/08 section 8.1 says mdp-feasibility.md
also covers.

**Evidence:** IMPLEMENTATION_PLAN.md section 2 "Delete" list (P0.1); research/08-pricebt-v1-strip-
inventory.md section 8 (the "KEEP" cells, now superseded).
