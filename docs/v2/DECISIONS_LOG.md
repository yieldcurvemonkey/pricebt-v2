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

## 2026-09-27 — Phase 2: a verdict-parsing bug in the orchestrating workflow script hid two real
adversarial-verifier FAILs as PASS; caught at the phase gate, fixed before commit

**Situation:** The Phase 2 workflow script's fix-loop used `/^\s*PASS\b/i.test(verdict)` to decide whether
an adversarial verifier's free-text report counted as a pass. Two verifier reports (P2.1's round-2 verdict
on `Portfolio`, P2.2's round-3 verdict on `PricingService`) literally began with the line "PASS" followed
immediately by "FAIL" and then a "## Verdict: FAIL" header and a list of confirmed problems — apparently
the verifier agent started to write "PASS", caught itself, and corrected to "FAIL" in the same response.
The regex matched only the leading token and treated both as passing, so the workflow's internal fix loop
exited early and two real bugs went unfixed: (1) `Portfolio.__getitem__`/`pop` raised `KeyError` on no
match instead of returning `gs`'s `()`, `append()` didn't unpack an iterable, `__add__` raised the wrong
exception type, `to_frame` was missing `mappings=None` — five unmarked gs-parity violations, one of them
actively pinned "correct" by the implementer's own test; (2) `PricingService`'s DESIGN §8.1 rule-5 "no
aggregation_level parameter, no scalar form mapped" case wrongly summed a bucketed mapping into a scalar
instead of returning the bucketed form, and `kwargs` was never injected into `trade:`/`functions:`
evaluations on an asset with no `resolve:` block, per DESIGN §4.3.

**Rule applied:** IMPLEMENTATION_PLAN.md §9 row "An agent reports 'done'" ("Treat it as a claim. Re-run the
acceptance command and the phase gate yourself before committing") — this is exactly that case, one level
removed: the orchestrator's own automated re-check had a bug, and the *orchestrator's* independent phase-gate
run (full suite green, guards green) did not by itself catch the two parity/correctness gaps, because the
existing test suite didn't exercise them (a `KeyError`-pinning test made the bug pass, not fail; the rule-5
gap had no covering test at all). Reading the workflow's own JSON result payload before trusting its
"PASS" framing is what caught it.

**Decision:** read the raw verdict text for every task before committing Phase 2 (not just the workflow's
round-classification), found the two false positives, and ran a targeted follow-up workflow to fix both,
with a corrected parser (`VERDICT: PASS`/`VERDICT: FAIL` on its own line, checked for an explicit FAIL
token before accepting a leading PASS as real). Both follow-up fixes were independently re-verified and the
full phase-2 gate (484/484, guards 68/68) was re-run before the phase-2 commit. Adopted the corrected
parser and the explicit `VERDICT: PASS`/`VERDICT: FAIL` instruction for every subsequent phase's workflow
scripts (P3 onward).

**Evidence:** the two verifier reports' raw text (`tests/test_portfolio.py`'s
`test_len_iter_getitem_contains` originally asserted `pytest.raises(KeyError)` for `p["missing"]`, now
asserts `p["missing"] == ()`, confirmed against the real installed gs_quant 1.5.4 via `inspect.getsource`
and live execution); `src/pricebt/assets/pricing.py`'s rule-5 scalar/bucketed selection logic, fixed and
covered by a new test using a bucket-only-mapped plain `RiskMeasure`.

**Alternative considered:** trust the workflow's own round-classification and commit — rejected; that is
precisely the failure mode IMPLEMENTATION_PLAN §9 warns against, just one layer further from the surface
(a buggy checker, not a buggy implementation, hiding the real state).

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

---

## 2026-09-27 — P3.3: DEV-E5's own wording is internally in tension; resolved in favour of "hedges get no meta"

**Situation:** a verifier flagged that `HedgeAction.__post_init__` routed its inner priceable
through `_rename_priceables`, which unconditionally sets `position_meta` on every priceable it
touches — but DESIGN.md section 11's DEV-E5 row itself reads two ways: "Each action's
`__post_init__` sets `(action.name, ...)` ... on every renamed priceable" (which would include
HedgeAction) versus, later in the same row, "Positions with no meta (hedges) are never matched by
name" (which names hedges as the one category `position_meta` is None for).

**Rule applied:** DESIGN.md section 11 DEV-E5's stated *purpose* (fix the gs crash where a held
hedge's parsed name breaks `ExitTradeAction`, research/02 line 209) outweighs the row's first,
looser sentence when the two are read literally against each other — a reading that gives every
action's renamed priceable real position_meta reproduces the exact crash under a different
exception (`TypeError: '<=' not supported between NoneType and date`, once an `ExitTradeAction`
name list happens to contain the generic `'Priceable0'` a hedge's anonymous leg would get), because
R4's hedge rename embeds the create date into the wrapping portfolio's *name string*
(`generic_engine.py`, P3.5), never into the leg's own `position_meta[2]` the way R2/R3 fill it in
for ordinary trades.

**Decision:** `HedgeAction.__post_init__` calls `_rename_priceables(self.name, portfolio,
set_meta=False)` (new keyword, default `True` for every other caller), so a hedge leg's
`position_meta` stays the `Instrument.__init__` default of `None`.
`test_position_meta_set_by_hedge_action_on_its_inner_priceable` (asserted `==
("Action2", "Priceable0", None)`) is renamed `test_position_meta_not_set_by_hedge_action_...` and
now asserts `is None`.

**Evidence:** DESIGN.md section 11 DEV-E5 row (both sentences quoted above); research/02 line 209
("Hedge instruments end in `..._Action2_Priceable0` → `strptime('Priceable0')` raises `ValueError`
→ any `ExitTradeAction(priceable_names=...)` crashes if a hedge position is held") and line 190 (R4:
the hedge rename touches the wrapping portfolio's name, not a per-leg `position_meta`); the real gs
2.1.17 `gs_quant/backtests/actions.py:440-446` (HedgeAction's own rename loop has no `position_meta`
field to set at all, since gs has no such field).

**Alternative considered:** keep DEV-E5's first sentence literally ("every renamed priceable" incl.
hedges) — rejected: it reintroduces the exact bug DEV-E5 exists to remove, just with a different
`TypeError` instead of gs's `ValueError`, the moment any `ExitTradeAction(priceable_names=[...])`
list contains the generic anonymous-priceable name `'Priceable0'` shared by every action type while
a hedge is held.

---

## 2026-09-27 — P3.3: DEV-E10's fix is `None`-preserving, not the row's literal unconditional
`make_list`

**Situation:** DESIGN.md section 11 DEV-E10's row text says the fix is `make_list(priceable_names)`
applied unconditionally, to replace gs's bare-string substring-match bug and a typo'd alternate
kwarg. `src/pricebt/backtests/actions.py:345-346` instead guards it:
`if self.priceable_names is not None: self.priceable_names = make_list(self.priceable_names)` — so
a `None` value (the default, meaning "exit everything") stays `None` rather than becoming `[]`. This
is the same shape of tension DEV-E5 above hit: DESIGN's one-line fix text, read literally, breaks on
contact with the real gs downstream code it has to interoperate with.

**Rule applied:** DESIGN.md section 11's own stated purpose for DEV-E10 (fix gs's bare-string
substring-match bug and the `priceables_names` typo) outweighs the literal "unconditional" wording
where the two conflict, the same precedent set for DEV-E5 above.

**Decision:** keep the `is not None` guard. `make_list(None) == []` would collapse the `None`
sentinel; the real gs 2.1.17 `gs_quant/backtests/generic_engine_action_impls.py:440` branches
`if self.action.priceable_names is None:` (exit everything) versus line 453's truthiness branch on
a real list — collapsing `None` to `[]` hits neither branch and raises `UnboundLocalError` inside
gs's own impl code the moment `ExitAllPositionsAction()` (no args, `priceable_names=None` by
default) is used. Pinned by `test_exit_trade_action_none_stays_none` and
`test_exit_all_positions_action_default_args_keeps_the_none_sentinel`.

**Evidence:** DESIGN.md section 11 DEV-E10 row; real gs `gs_quant/backtests/
generic_engine_action_impls.py:440,453` (the `None`-vs-truthy branch pair `ExitTradeActionImpl`
reads); `src/pricebt/backtests/actions.py:332-346` (the `# pricebt DEV-E10` comment already
documents this reasoning at the guard itself).

**Alternative considered:** unconditional `make_list(priceable_names)` per the row's literal text —
rejected: crashes `ExitAllPositionsAction()`'s default-args "exit everything" path in P3.5's engine
impl, which is a real, exercised code path (research/02's action-handler table), not a hypothetical.

---

## 2026-09-27 — P3.5: two cross-ownership bug fixes to P1.3's `risk/results.py` and P2.2's
`assets/pricing.py`, retroactively declared as shared edits

**Situation:** while wiring `HedgeAction` under a `HistoricalPricingContext` (P3.5's own scope),
the implementer hit two real, pre-existing bugs in already-closed tasks' files, both of which block
the engine whenever a hedge is priced on more than one risk measure across history (i.e. essentially
always, since the hedge's own risk measure and the price measure are both present):

1. `assets/pricing.py`'s `_historical_instrument_value` built a `pandas.Series` directly from a dict
   of `FloatWithInfo` values. pandas silently downcasts a float subclass to plain `float64` the
   moment it lands in a `Series`, so every `.unit`/`.risk_key` was lost — `SeriesWithInfo` carries
   that metadata on the *series itself* (`_metadata`), not per element, and the constructor call
   here never passed it through.
2. `risk/results.py`'s `PortfolioRiskResult._by_date` did not handle a `MultipleRiskMeasureResult`
   (a dict keyed by `RiskMeasure`, produced whenever `HistoricalPricingContext.calc()` is called with
   more than one measure) — indexing it by a `date` key always raised `KeyError`, since the dict's
   keys are measures, not dates. It also called `.loc[item]` directly on a per-date Series, which
   has the same metadata-loss problem as (1).

Neither bug is a gs-behavior deviation (DESIGN §11 has no row for either — there is no gs parity
question here, `PortfolioRiskResult`/`SeriesWithInfo` are pricebt's own reimplementation), so no
`# pricebt DEV-XX` marker applies; both are pure pricebt-internal correctness bugs that happened to
be latent until P3.5's engine became the first real caller to exercise the historical-hedge path.
An adversarial verifier (round 3) correctly flagged that touching `assets/pricing.py` and
`risk/results.py` — files P2.2 and P1.3 respectively own per IMPLEMENTATION_PLAN.md §4 — without a
declared shared edit or a marker was a process violation regardless of the fix's correctness, per
the same "an unmarked change is a finding regardless of whether it seems like an improvement" logic
applied throughout this project.

**Rule applied:** IMPLEMENTATION_PLAN.md §9 row "A MUST seems impossible as written" (closest
analogue: a task cannot complete correctly without touching a file outside its declared ownership) —
find the closest design that keeps all five MUSTs, record it, do not relax a MUST. Reverting the
fixes would restore two real, reproducible correctness bugs purely for process compliance, which
DESIGN §2.1's MUSTs do not ask for; documenting and keeping them is the correct resolution.

**Decision:** both fixes are kept as written (each already carries an in-code comment explaining the
root cause, both are covered by mutation-verified regression tests —
`test_historical_multi_measure_calc_by_date_preserves_unit_and_indexes_by_measure` in
`tests/test_pricing_service.py`, added during P3.5's own fix loop). This entry is the retroactive
"shared edit" declaration IMPLEMENTATION_PLAN.md §5's P3.5 task description should have named
up front.

**Evidence:** `git -C <worktree> diff HEAD -- src/pricebt/assets/pricing.py` (the `_historical_
instrument_value` change, ~lines 517-543) and `-- src/pricebt/risk/results.py` (`_by_date`'s new
`MultipleRiskMeasureResult` branch and the `_series_item` helper, ~lines 343-388); the P3.5 round-1
fix report's "Finding 5" section, which added the regression test and confirmed the mutation
evidence for both files independently.

**Alternative considered:** revert both fixes and have P3.5 work around them from within its own
files only — rejected: the bugs are in the shared metadata-preservation contract
(`FloatWithInfo`/`SeriesWithInfo`.unit surviving a round trip through a `pandas.Series`), not in
anything P3.5-specific, so any workaround would either duplicate the fix inside generic_engine.py
(worse: the same latent bug would still exist for any other future caller of
`_historical_instrument_value`/`_by_date`) or silently produce wrong `.unit` values for hedges
priced under a `HistoricalPricingContext`.

---

## 2026-09-28 — P4.2: DESIGN.md §6.5 names the wrong property — real gs is `date_range`, not `dates`

**Situation:** DESIGN.md §6.5 spells out `HistoricalPricingContext`'s surface as
`dates -> tuple[date, ...]  # 'dates' as given; else date_range(start, end or today); ...` — i.e. it
names the property itself `dates`. P1.2 built it exactly as DESIGN wrote it
(`src/pricebt/markets/__init__.py`'s `HistoricalPricingContext.dates` property), and every later
phase (P2.2's `pricing.py`, P3's engine, tests) read `ctx.dates`. P4.2's gs API parity test, run
against the real installed gs_quant 1.5.4 (not just DESIGN's prose), found gs's actual property is
named `date_range` — confirmed directly: `inspect.getsource(gs_quant.markets.
HistoricalPricingContext.date_range.fget)` exists and returns `self.__date_range`; there is no
`.dates` property on the real class at all.

**Rule applied:** IMPLEMENTATION_PLAN.md §9 row "DESIGN.md vs this plan, or a research note vs
DESIGN.md" — "On a *fact* about gs or ARBS behaviour, the primary source code wins." This is exactly
that: DESIGN.md's naming of this one property is simply wrong, confirmed against gs 1.5.4 itself, not
a deliberate MUST-2 deviation (DESIGN §11 has no DEV row for renaming this property, and decision 0.3
already confirmed §11 as the complete, closed deviation list — inventing an unapproved exception
instead of fixing the name would itself be an unmarked deviation).

**Decision:** renamed the property to `date_range` everywhere: `markets/__init__.py`'s property
definition, every read site in `assets/pricing.py` (`_historical_instrument_value`,
`_historical_resolve`, `_resolve_portfolio_one_date`'s caller — 3 sites), and `tests/test_contexts.py`'s
signature/behaviour assertions. `tests/data/gs_api_exceptions.yaml`'s `HistoricalPricingContext`
`properties` entry documents the resolution inline (quoted verbatim in the entry: "`date_range` is
not listed here: it is implemented under gs's own name... so there is no mismatch to except"). Grepped
the full `src/`/`tests/` tree post-rename for any remaining `.dates` read on a
`HistoricalPricingContext`/`PricingContext` instance specifically (as opposed to the unrelated
`PortfolioRiskResult.dates`, `ScalingPortfolio.dates`, or `EventTriggerRequirements.dates` properties,
which are distinct attributes on different classes and were correctly left untouched) — none found.

**Evidence:** DESIGN.md §6.5's `dates -> tuple[date, ...]` line (the wrong spec text); real gs_quant
1.5.4 `gs_quant/markets/__init__.py` (`HistoricalPricingContext.date_range`, confirmed live via
`inspect.getsource`); `tests/data/gs_api_1_5_4.json`'s snapshot of the same; `tests/test_gs_api_parity.py`
now passes with `date_range` and would fail again with `dates` (this is the mismatch the parity test
exists to catch).

**Alternative considered:** add a `gs_api_exceptions.yaml` entry excepting pricebt's `dates` name as a
deliberate difference from gs's `date_range` — rejected: there is no DESIGN §11 DEV row authorizing
this as an intentional deviation, and the whole point of MUST-2 is that an unlisted API difference is
a defect to fix, not a exception to grant.

---

## 2026-09-28 — Phase 4 gate: 5 full-suite failures the parallel tasks' own scoped acceptance
commands could not see, all fixed before commit

**Situation:** every P4.1-P4.4 task passed its own scoped acceptance command and its adversarial
verifier. Running the FULL suite myself at the phase gate (per IMPLEMENTATION_PLAN.md §9 "An agent
reports 'done'") surfaced 5 failures no single task's own command would ever exercise, because they
only manifest from cross-file pytest-execution-order interaction:

1/2/3. `test_actions.py::test_action_count_matches_the_pre_session_baseline...`,
   `...test_action_count_was_reset_between...`, and `test_engine_smoke.py::test_ledger_names_and_dates`
   (wrong `Action2_...` instead of `Action1_...`) all traced to ONE root cause:
   `tests/test_040304_toy.py`'s `notebook_ns` fixture is `scope="module"` (changed from the pytest
   default `function` during P4.3's own fix loop, to fix real Tk-backend flakiness from re-running
   the notebook 3x per process). Running the toy notebook script calls
   `PricebtSession.use(assets=[...])` and constructs an unnamed `AddTradeAction`, mutating
   `PricebtSession.current`/`GsSession.current`/`pricebt.backtests.actions.action_count` — all
   process-global state `tests/conftest.py`'s `isolation` autouse fixture normally saves/restores
   PER TEST. A module-scoped fixture's setup runs OUTSIDE that per-test window (pytest sets up
   higher-scoped fixtures before the function-scoped ones needed by the same test), and this fixture
   had no teardown at all (a plain `return`, not `yield`) — so the mutation was never undone. Because
   pytest collects `test_040304_toy.py` first alphabetically (`0` sorts before every letter), every
   test file that runs afterward in the same process inherited the polluted global state.
4. `test_instrument.py::test_asset_config_without_session_raises_pricebt_error` ("DID NOT RAISE") —
   the same root cause: by the time this test ran, `PricebtSession.current` was still the toy
   notebook's leaked session (not `None`), so `swap.asset_config` found a live session instead of
   raising `PricebtError`.
5. `test_gs_api_parity.py::test_datetime_function_parity[prev_business_date]` — a real, structural
   (not order-dependent) flakiness: both gs 1.5.4 and pricebt write
   `prev_business_date(dates=date.today(), ...)`, and Python evaluates a default argument value
   exactly ONCE, at function-definition/module-import time. `tests/data/gs_api_1_5_4.json` froze
   whatever date `gs_api_snapshot.py`'s process imported on (2026-09-27, per its own header); the
   session's clock rolled over to 2026-09-28 between Phase 3 and Phase 4, so pricebt's live import
   now bakes in a different date than the frozen snapshot — guaranteed to diverge on any day after
   the snapshot date, regardless of test order.

**Rule applied:** IMPLEMENTATION_PLAN.md §9 "An agent reports 'done'" (re-run the acceptance command
and the phase gate yourself before committing) and "A test cannot pass without skipping, xfailing or
loosening it" (fix the root cause, never paper over it).

**Decision:**
- `test_040304_toy.py`'s `notebook_ns` fixture now saves `PricebtSession.current`/
  `GsSession.current`/`actions.action_count` before running the notebook and restores all three in a
  `try/finally` after `yield`ing the namespace — the same pattern `conftest.py`'s `isolation` fixture
  uses, just bracketing the whole module instead of one test. The module-scope optimisation (and the
  Tk-flakiness fix it was for) is kept; only the missing teardown was the bug.
- `tests/data/gs_api_exceptions.yaml` gained one `ignore_default: [dates]` entry for
  `gs_quant.datetime.prev_business_date`'s `signature` aspect (the same mechanism already used for
  `GsSession.use`'s `EnumBase.__repr__` difference) — the parameter's name/kind and every other
  parameter's default must still match exactly; only this one wall-clock-dependent default value is
  excepted, with the mechanism (not the gs behaviour) doing the "fixing". Checked: no other
  `pricebt.datetime` function has a bare `date.today()`-valued default (grepped the module), so this
  is the only symbol needing the exception.
- Re-ran the full suite after both fixes: 948 passed (943 + the 5 that were failing), no new
  failures, guards still green.

**Evidence:** the 5 failures' tracebacks (captured verbatim in this session's tool output);
`tests/test_040304_toy.py`'s fixture diff (module-scoped `try/finally` added); the
`gs_api_exceptions.yaml` entry; `inspect.getsource`/live signature checks confirming both gs 1.5.4
and pricebt's `prev_business_date` use a bare `date.today()` default.

**Alternative considered:** regenerate `tests/data/gs_api_1_5_4.json` today to make the dates match
again — rejected: this only defers the same failure to the next day boundary rather than fixing the
structural flakiness, and the snapshot is meant to be a stable, rarely-regenerated reference
artifact, not something re-run to chase a moving wall clock.

---

## 2026-09-28 — P6.2 fix loop: DEV-E18 currency-conversion scope (a judgment call)

**Situation:** finding 2 (P6.2) is that `ScaledTransactionModel(scaling_type=<RiskMeasure>)` never
gets rewritten to `result_ccy`, unlike the run's `risks` list (DEV-E15). The fix mirrors that
rewrite via a new `_RESULT_CCY` ContextVar read by `ScaledTransactionModel.get_unit_cost`. The
open question: what happens when `scaling_type` is a plain, non-currency-capable `RiskMeasure`
(e.g. `IRGamma`, which has no `currency` parameter at all) while `result_ccy` is set?

**Rule applied:** IMPLEMENTATION_PLAN §9 row "A gs behaviour not in DESIGN §11 looks like a bug" is
not quite this case (this is pricebt's OWN mechanism, DEV-E15, not a gs behaviour); closest is "A
MUST seems impossible as written" in spirit — MUST-4 promises currency consistency, but a plain
`RiskMeasure` literally cannot carry a `currency` parameter to convert through.

**Decision:** left unconverted, same as the pre-existing `scaling_type` as a bare attribute-name
string (e.g. `'notional_amount'`), which was already undocumented-currency/unconverted before this
fix and is not part of this finding. Did **not** add a `risks`-list-style
`raiser(f"Unparameterised risk: {r}")` for this case, to keep the fix scoped to the finding (making
the previously-silently-wrong case correct) rather than inventing new strict-validation behaviour
nobody asked for and no existing test exercises either way.

**Evidence:** `src/pricebt/risk/__init__.py`'s `ParameterisedRiskMeasure`/`RiskMeasureWithCurrencyParameter`/
`RiskMeasureWithFiniteDifferenceParameter` hierarchy — only these accept `currency=`; a bare
`RiskMeasure` (e.g. `IRGamma`, `DollarPrice`) does not. `grep`ped `tests/test_transaction_costs.py`
and `tests/test_multi_currency.py`: no existing test combines a non-parameterised `RiskMeasure`
scaling_type with `result_ccy`, so this choice breaks nothing either way.

**Alternative considered:** raise (mirroring the `risks` list's strict `Unparameterised risk: {r}`)
whenever `result_ccy` is set and `scaling_type` is a plain `RiskMeasure` — rejected as unrequested
scope creep for this finding; the finding's own evidence and repro only concern the
`ParameterisedRiskMeasure` case. Can be revisited if a future finding shows the unconverted plain-
`RiskMeasure` case actually causes a silent wrong number in practice.

---

## 2026-09-28 — P5.2 live ARBS run: executed with the user's explicit approval, all 7 checks passed

**Situation:** P5.2 was deferred throughout autonomous execution per the hard safety limit (never
import/execute ARBS without the user's own approval for the first run). After all 6 autonomous
phases were complete, committed, and pushed to
[github.com/yieldcurvemonkey/pricebt-v2](https://github.com/yieldcurvemonkey/pricebt-v2), the user
directly instructed "run the live ARBS tests" in this session.

**Rule applied:** this is exactly the condition the deferral was waiting for — DESIGN.md's own text
for Appendix A says "the implementer copies this file, then runs the live checks in
IMPLEMENTATION_PLAN P5.2, only after the user approves the first live run." The user, present and
directing this session, gave that approval explicitly.

**Decision:** ran `$env:PRICEBT_LIVE_ARBS="1"; pytest -m live_arbs -o addopts= -v` from the worktree
root. All 7 tests passed. Filled in `docs/v2/LIVE_ARBS_REPORT.md`'s Results section with the actual
observed values (previously template placeholders) — npv/dv01/par_rate/delta_ladder for the 10y ATM
payer check, the seasoned-mark npv, the short-040304-run ledger count and runtime, and confirmation
that the no-network/no-curve-store-write check (6) found zero unexpected filesystem changes (one
empty today-dated fixings-cache folder, the only change ARBS is documented to make even on a pure
cache hit). Re-ran the full non-live suite afterward to confirm nothing else was affected: unchanged
at 968 passed / 7 skipped.

**Evidence:** live pytest output (`7 passed, 968 deselected, 3 warnings in 27.13s`); the values now
recorded in `docs/v2/LIVE_ARBS_REPORT.md`'s Results table; the full-suite re-run's unchanged count.

**Alternative considered:** none — this was a direct, explicit user instruction for the exact action
the project's own documentation said only the user could authorize; no autonomous judgment call was
needed here.

---

## 2026-09-28 — Safety finding: the ARBS notebook had been executed (uncommitted); reverted

**Situation:** while staging the P5.2-results commit above, `git status` showed an unexpected
~315-line diff on `notebooks/040304_mean_reversion_usd_sofr_arbs.ipynb`, a file no task in this
session was asked to touch after P5.1 committed it (`1b9d236`) in its clean, unexecuted state (P5.1's
own verifier independently confirmed at the time: "every cell has `execution_count=None` and 0
outputs"). Inspecting the working-tree copy showed cell 1 had `execution_count=1` and a populated
`outputs` array: a `ModuleNotFoundError: No module named 'pricebt'` traceback, dying at
`from pricebt.backtests.actions import AddTradeAction` — the SECOND import statement in the first
cell, well before the `PricebtSession.use(...)` call three lines later, and far before any of the
config's own lazy `imports:`/`code:` blocks (which only execute on the FIRST evaluation of an
expression against the ARBS asset, i.e. only after a session using it actually prices something).
**ARBS was never imported or reached** — the notebook was run with a plain `jupyter`/`nbconvert`-style
kernel that had no `PYTHONPATH` pointing at `src/`, so it crashed on pricebt's own import chain
before getting anywhere near the safety boundary. This was never staged or committed by any phase
commit (verified: `git log --follow -- <path>` shows only the single P5.1 commit).

**Rule applied:** the hard safety limit ("never import or execute ARBS... P5.2 the live ARBS run is
deferred... do not run this notebook") and IMPLEMENTATION_PLAN.md's general instruction to
investigate unexpected state rather than silently overwrite it, then restore to the last known-good
committed state once satisfied it's safe to do so.

**Decision:** ran `git checkout -- notebooks/040304_mean_reversion_usd_sofr_arbs.ipynb`, restoring it
byte-for-byte to the P5.1 commit's content (confirmed: `execution_count=None`, 0 outputs, on every
cell, and `git diff` against HEAD empty afterward). Could not determine which of the many subagents
across P5/P6 ran it, or when, given the number of agents in this session and that the change was
never committed (so no commit timestamp to anchor it to) — recording this here as a process gap
rather than leaving it unexplained: task prompts told agents not to run *the config* / not to import
ARBS, but did not always say, in so many words, "do not execute this specific notebook file" to every
agent that might have opened it out of general curiosity while reviewing. No actual harm occurred
(the crash happened before any ARBS-adjacent code ran, so no network/production-DB/filesystem
boundary was ever approached), but the intent — "built but not executed" — was briefly violated in
the uncommitted working tree.

**Evidence:** the diff inspected before reverting (cell 1's `execution_count`/`outputs` fields, the
literal traceback text); `git log --follow` showing only one commit ever touched this file;
`git status --porcelain` empty after the revert.

**Alternative considered:** leave it and just not commit it — rejected: an uncommitted "notebook was
executed" artifact sitting in the worktree is itself the kind of state a future session (or a
careless `git add -A`) could accidentally commit or build on; reverting immediately to the clean
committed state removes the risk entirely rather than merely deferring it.

---

## 2026-09-28 — T1-A (PNL_EXPLAIN_PLAN.md): the toy's half-gamma-trap ratio depends on tenor, and
10y sits right at the edge of T-GAMMA-2's stated [0.45, 0.55] band

**Situation:** PNL_EXPLAIN_PLAN.md §2.1/§3.1's `gamma()` formula was implemented exactly as given
(central second difference of `npv` over the *measured* par move under a ±1bp `zero_rate` shift —
never differencing `dv01`). §5.1's T-GAMMA-2 documents the "half-gamma trap" as
`(dv01₊−dv01₋)/(p₊−p₋) ≈ Γ/2`, band `[0.45, 0.55]`, "at the money" with no tenor named (unlike
T-GAMMA-1, which is explicit: "2y/10y/30y"). Verifying `tr.gamma` against an independent hand-rolled
second difference (rel diff < 1e-9 at every tenor tried — the formula itself is correct) also
computed this same half-gamma ratio for a payer, ATM, on 2026-09-28's market, at the three T-GAMMA-1
tenors:

| Tenor | half-gamma / Γ ratio |
|---|---|
| 2y | 0.7524 |
| 10y | 0.5533 |
| 30y | 0.5205 |

10y — the tenor used in essentially every other example in this plan (T-GAMMA-3, T-THETA-1,
T-SHOCK, T-MONTHLY, …) — sits *outside* the stated band by 0.6% relative (0.5533 > 0.55). 2y misses
badly (0.75). Only 30y sits comfortably inside.

**Diagnosis (not a bug in `gamma()`):** §2.1's exact identity is
`∂²npv/∂z² = 2·∂pv01/∂z·∂par/∂z + (par−K)·∂²pv01/∂z²` (chain-ruled into par-space via
`∂par/∂z`); the "half the gamma" intuition (`half-gamma ≈ Γ/2`) is exact only in the limit that
`par(z)` is linear in `z`. The toy's annual-coupon par is close to `1 − e^{-zT}` in `z`-space, so
`par` is measurably convex in `z` at long tenors, and the finite (not infinitesimal) 1bp bump used
by both `Γ` and the "trap" estimator picks up a chunk of that convexity. The ratio is not a constant
0.5; it moves with duration (rougher intuition: it worsens — moves further from 0.5 — the *shorter*
the tenor, because a shorter swap's par-vs-zero relationship is closer to linear over a fixed 1bp
window relative to its own convexity scale... empirically here it is 2y that is furthest from 0.5,
not closest, so treat the closed-form intuition as directional only — the reliable statement is
just "it is tenor-dependent, verify per tenor," not a specific monotonic law).

**Rule applied:** PNL_EXPLAIN_PLAN.md §9, "A convention here conflicts with what the code shows: the
code wins on facts, and this plan wins on intent" — the *intent* (a config author who differences
`dv01` instead of computing `∂²npv` gets roughly half the true gamma, and that mistake is
catchable) is correct and confirmed; the *literal band on a specific tenor* is a calibration detail
the plan's author did not appear to verify at 10y specifically. §5.6's calibration procedure ("never
loosen a threshold silently... diagnose with exact_split first, then document") is the closer
process match, even though this is T-GAMMA-2's *documentation* test rather than an `*_TARGET`.

**Decision (informational only — T1-A owns no pytest files, so no test was written or adjusted
here; this is a heads-up for whoever writes T-GAMMA-2 in T1-B/T1-C):** do **not** pick 10y for
T-GAMMA-2 as literally written if the stated `[0.45, 0.55]` band is kept — it is a near-miss there.
Two non-threshold-loosening options, in order of preference: (a) run T-GAMMA-2 at 30y, where the
ratio (0.5205) sits comfortably inside the existing band with real headroom, since T-GAMMA-1 already
separately proves the exact formula holds at every tenor to 1e-9 — T-GAMMA-2 only needs *one* tenor
to demonstrate the trap; or (b) if 10y is kept for narrative consistency with the rest of the plan's
examples, document the tenor-dependence explicitly and widen the *documented rationale* (not silently
the number) to whatever band actually holds at 10y, citing this entry. Did not choose between (a)
and (b) myself since T-GAMMA-2 is outside T1-A's file ownership (`tests/skills/test_skill_swap_pnl.py`
is T1-B's).

**Evidence:** `tr.gamma` cross-checked against a from-scratch second difference at 2y/10y/30y,
payer and receiver, USD, 2024-01-03 market (rel diff < 1e-9 in every case — the implementation is
correct); the ratio table above, reproducible via the ad-hoc script used for T1-A's acceptance
sanity check (not committed, per T1-A's own scope — recomputed inline for this entry).

**Alternative considered:** silently note nothing and let T1-B discover it when the test is red —
rejected, that is exactly the "test the calibration once, then loosen if it misses" trap §5.6 warns
against; recording the actual numbers now lets T1-B pick a tenor that needs no threshold change at
all.

---

## 2026-09-29 — T1-B: T-GAMMA-2 uses 30y (per T1-A's entry above); T-GAMMA-3's "doubling Δ → 6–10×"
is a plan-vs-facts miss, not a bug

**Situation:** built `tests/skills/test_skill_swap_pnl.py`. T-GAMMA-2 (the half-gamma-trap
documentation test) follows the previous entry's recommendation (a): 30y, ATM, 2024-01-03 — ratio
0.5205, comfortably inside `[0.45, 0.55]` (re-verified here, matches T1-A's number exactly).

T-GAMMA-3 ("Taylor order on an instant shock … doubling Δ multiplies the with-gamma residual by
6–10×, third order") does not hold as literally written. Computed directly from `tr.gamma`/`tr.npv`/
`tr.par_rate` at the ATM 10y trade, 2024-01-03 market, Δ = 25/50/100bp (same-date synthetic shock,
`dataclasses.replace(market, zero_rate=market.zero_rate ± Δ)`):

| Δ (bp) | Δpar (bp) | delta-only residual | with-gamma residual | with-gamma / delta-only |
|---|---|---|---|---|
| 25 | 26.00 | −273.35 | −23.94 | 8.8% |
| 50 | 52.07 | −1085.61 | −85.47 | 7.9% |
| 100 | 104.40 | −4281.20 | −260.56 | 6.1% |

The "≤10% of delta-only" bound holds at every Δ (this part of the plan is correct and is what
T1-B's test asserts). But the DOUBLING factor on the with-gamma residual is **25→50: 3.57×,
50→100: 3.05×** — not 6–10×.

**Diagnosis (not a bug, same root cause as the previous entry's half-gamma finding):** §2.1 defines
`Γ` as the z-space second difference of `npv`, redenominated by the MEASURED par move —
`Γ = ∂²npv/∂z² / (∂par/∂z)²` at a finite (not infinitesimal) 1bp probe. By the chain rule,
`∂²npv/∂z² = Γ_par·(∂par/∂z)² + pv01·∂²par/∂z²`, so `Γ = Γ_par + pv01·(∂²par/∂z²)/(∂par/∂z)²`. The
toy's flat-curve par is (for a flat 1bp probe) close to `par(z) ≈ 1 − e^{−zT}`-shaped in `z`, i.e.
genuinely convex in `z`, so the second term is a real, non-negligible LEFTOVER — not a third-order
remainder but a second-order one (`Γ` is a biased estimator of the true `Γ_par`, with an O(1) bias
at the tenor/notional here, not merely an O(Δ) or O(Δ²) discretisation error). A residual dominated
by an uncorrected second-order leftover scales like `Δpar²` — a doubling of `Δ` should multiply it
by ≈4×. The observed 3.05–3.57× matches that (not exactly 4× because `Δpar` itself is not perfectly
linear in `Δ`, and there is a genuine third-order tail mixed in, pulling the ratio down slightly from
4). A "6–10×" ratio would only be produced by a *clean* third-order remainder, which requires `Γ`
to already equal the true `Γ_par` (no second-order bias) — not the case here, for the same reason
T-GAMMA-2 exists at all (the half-gamma-adjacent chain-rule term is large enough to be the whole
point of that test).

**Rule applied:** PNL_EXPLAIN_PLAN.md §9, "the code wins on facts, and this plan wins on intent" —
`tr.gamma` is implemented exactly per §2.1's own formula (T-GAMMA-1 proves this to 1e-9 at every
tenor), so there is nothing to fix; the plan's literal "6–10×" expectation assumed a cleaner
estimator than the one §2.1 itself specifies. Also §5.6's diagnostic-first spirit (not a `*_TARGET`
row, but the same discipline): diagnosed via the closed-form chain-rule argument above instead of
guessing at a new band.

**Decision:** T-GAMMA-3's test asserts what is actually true and still fully diagnostic of the
named mutation ("use half gamma"): (1) with-gamma residual ≤ 10% of delta-only residual at every Δ
(the plan's own number, confirmed); (2) doubling Δ multiplies the with-gamma residual by a factor in
`[2.5, 5.0]` (brackets the observed 3.05/3.57 with real headroom on both sides — a HALF-gamma
mutation roughly doubles the with-gamma residual at each Δ relative to the correct one, which this
window still catches, verified in the mutation pass below); it does **not** assert "6–10×" or
"third order" as such. Never silently loosened past what's needed: the `[2.5, 5.0]` window is
centred on the two observed ratios, not blown open to "whatever passes."

**Mutation check:** with `tr.gamma` monkeypatched to
`(dv01(up)-dv01(down))/(par(up)-par(down))` (the half-gamma trap itself, T-GAMMA-2's own bad
estimator, ratio ≈0.5533 to the true `Γ` at this tenor/date) substituted for the true `Γ`, the
with-gamma residual is ≈48-50% of the delta-only residual at every Δ tried (25/50/100bp) — about
5× over the "≤10%" bound, not a borderline miss. Confirmed red under this mutation (all three Δ
assertions fail), reverted. Recorded under T-GAMMA-3 in the tier report.

**Evidence:** table above, computed inline against `tests/toylib/rates.py`'s shipped `gamma`/`npv`/
`par_rate` (unchanged by this tier — T1-B never edits `tests/toylib/rates.py`, that is T1-A's file).

**Alternative considered:** silently keep "6–10×" and pick Δ values that happen to produce a ratio
in that range by chance — rejected as exactly the kind of post-hoc threshold-fitting §5.6 forbids
even though this isn't a `*_TARGET` row; the chain-rule diagnosis is what makes the new window a
real, falsifiable claim rather than a fitted one.

---

## 2026-09-29 — T1-B: T-ROLL's `R2_TARGET ≥ 0.9999` is not met by the toy's monthly-ATM-roll world;
calibrated to the observed, inherent off-market moneyness residual

**Situation:** T-ROLL (PNL_EXPLAIN_PLAN.md §5.2): a monthly periodic roll of a fresh ATM 10y payer
(`AddTradeAction(swap, "1m")` on a `PeriodicTrigger(frequency="1m")`), daily marks, USD toy, 2024
full year. `swap_pnl.explain_stats(swap_pnl.explain_table(bt))` gives:

- `r2 = 0.999824` (target `R2_TARGET ≥ 0.9999` — **missed**, by 0.008 percentage points)
- `residual_share = 8.86e-5` (target `RS_TARGET ≤ 1e-3` — met, with ~11× headroom)

**Diagnosis (§5.6 step 3, via `exact_split`):** the 10 largest `|residual|` dates all show
`moneyness_term` (from `exact_split`, §2.7's `(par(t-1)−K)·Δpv01`) accounting for 80–95% of the
residual on that date (e.g. 2024-11-04: residual −59.11, moneyness_term −47.75; 2024-05-02: residual
−54.996, moneyness_term −58.196). This is exactly §2.7's own documented, EXPECTED behaviour: "Off-
market trades carry a first-order residual… Tight residual bounds apply only to near-ATM books
(monthly roll)." A trade re-struck to ATM only once a month drifts up to the toy's own documented
~50bp/month swing before its next roll, and the moneyness term is first-order in that drift — not a
bug in `explain_table`, `exact_split`, or `swap_pnl_definition` (all three independently agree: the
`explain_table` residual and the `exact_split`-derived reconciliation match to 1e-8 relative
elsewhere in this suite — T-RECON).

Checked for a unit/timing/coupon cause first, per §5.6: no coupons on the toy (§2.4, `cash_paid_to_date
≡ 0`, confirmed by T-CASH); no unit mismatch (`swap_pnl_definition`'s own scaling is separately
covered by T-DEF and matches section 2's formulas exactly); no off-by-one in the date walk (T-LEDGER's
ledger tie-out passes on this exact run to 1e-6 relative). The miss is the moneyness effect alone.

**Rule applied:** PNL_EXPLAIN_PLAN.md §5.6 step 3: diagnosed with `exact_split` first (done above,
moneyness — an anticipated §2.7 cause, not a bug); not fixed (nothing to fix); documented here with
the observed value and the new bound, per the rule; never silently widened without this entry.

**Decision:** `tests/skills/test_skill_swap_pnl.py`'s T-ROLL uses a toy-specific
`R2_TARGET_TOY_ROLL = 0.9996`, applying §5.6 step 2's own 2× headroom rule to the miss itself:
`1 - 0.999824 = 1.76e-4`, doubled = `3.52e-4`, so `R2 >= 1 - 3.52e-4 ≈ 0.99965`, rounded down
slightly to `0.9996` — strictly tighter than the plan's literal `0.9999` cannot be met, but with
real, calculated headroom below the observed 0.999824 (not an arbitrarily round "three nines")
so a real regression still fails it. `RS_TARGET` is kept at the plan's own `1e-3` value (met with
headroom) — no change needed there, and T1-B additionally tightens its own assertion to
`residual_share <= 2e-4` (≈2× the observed 8.86e-5, same §5.6 step 2 rule, and still no looser than
the plan's `1e-3`).

**Evidence:** the r2/residual_share/moneyness_term numbers above, reproduced by
`test_t_roll_periodic_monthly_roll_r2_and_residual_share` and cross-diagnosed with `exact_split` in
the same file's development notes (not a separate committed script — the diagnosis is this entry).

**Alternative considered:** (a) shrink T-ROLL's window so the trade never drifts far enough
off-market to miss 0.9999 — rejected: it would mask the exact effect §2.7 says a monthly-roll book
should show, defeating the point of running a full-year roll test; (b) switch the roll frequency to
weekly to stay closer to ATM — rejected: the plan explicitly specifies "monthly new ATM 10y,
`trade_duration` 1m" for T-ROLL, and changing the archetype to dodge a calibration miss is exactly
the kind of silent loosening §5.6 forbids in spirit, even applied to the scenario instead of the
number.

---

## 2026-09-29 — T1-C: `swap_pv_identity` is exact only for an ANNUITY dv01; meridian's realistic
full-curve DV01 is a different, equally legitimate convention, not a bug

**Situation:** built `swap_pv_identity` in `check_asset.py`'s `swap_pack`, per §3.4's exact formula:
`|npv - dv01*(par - fixed_rate*1e4)| <= 1e-6*|N| + 1e-3*|dv01|`, evaluated at d1 (ATM) and d2
(off-market). On `tests/assets/toy_usd_irs.yaml` it holds to floating-point precision at both dates
(residual ~1.8e-11 at d2, vs tolerance ~1.81). Running it (as the task instructions require) on the
**pre-existing**, unrelated `skills/pricebt-connect-pricing-library/example/meridian_usd_irs.yaml`
worked example — whose own protected test asserts zero FAILs on the clean config — it FAILed hard at
d2: residual 12,963.1 vs tolerance 1.75 (≈7,400x over), while d1 (ATM) was exact.

**Diagnosis:** §2.1 states "Both libraries' dv01 is an annuity pv01" for the toy and ARBS
specifically. The identity `PV = pv01*(par-K)` is algebraically EXACT only when "pv01" is that
annuity — `PV(K) = FloatPV - K*Annuity` is linear in `K` with slope `-Annuity`, and
`par := FloatPV/Annuity` by definition, so `PV = Annuity*(par-K)` holds trivially, for ANY curve,
with zero approximation, PROVIDED the "dv01" plugged into the formula literally IS `Annuity`
(strike-independent). Read `skills/pricebt-connect-pricing-library/example/meridian_sdk/__init__.py`
(`_price_one`): Meridian's `DV01 = -1e-4 * sum(dPV/dz_i)` — a genuine analytic FULL-CURVE parallel
sensitivity (`dPV/dz = N*[Annuity*dpar/dz + (par-K)*dAnnuity/dz]`), which depends on the trade's own
strike `K` through the `(par-K)*dAnnuity/dz` term. This is not a bug: it is the realistic definition
most real risk systems report for "DV01"/"IRDelta" (a curve-bump PV sensitivity), and it is a
perfectly valid instance of this file's own `swap_dv01_sign` convention ("PV change per +1bp") —
just a DIFFERENT quantity than the annuity, and the two provably diverge once the trade is off
market (confirmed numerically: `dv01(ATM K)=912.117` vs `dv01(K+100bp)=959.60`, a ~5% strike-
dependence, vs the toy's dv01, which is bit-identical regardless of strike since it is
`notional*annuity*1e-4` with no `K` in it at all).

**Rule applied:** §9, "A convention here conflicts with what the code shows: the code wins on facts,
and this plan wins on intent." The plan's INTENT ("catch npv/dv01/par/strike unit or sign
disagreements no single-function check can see") is preserved; the letter of §3.4's formula, applied
UNCONDITIONALLY to any IRSwap config regardless of its dv01 convention, is not — the code (Meridian's
own, correct, independently-tested implementation) proves the unconditional exact-tolerance version
is wrong on a real, valid config. Consulted the harness's stronger-reviewer `advisor` tool before
committing to a fix, given the stakes (a brand-new check silently going on to FAIL a pre-existing,
protected, cross-skill test if handled wrong); its diagnosis matched this entry's independently-
derived one, and it proposed the discriminating probe below.

**Decision:** added a cheap, decisive precondition probe BEFORE trusting the exact tolerance: at d1,
resolve a SECOND payer at the SAME schedule but a strike 100bp off the ATM one
(`ctx.inst.clone(pay_or_receive="Pay", fixed_rate=fixed_rate_1 + 0.01)`) and compare its dv01 to the
ATM payer's dv01, `rel_tol=1e-6`. An annuity pv01 is strike-independent by construction, so this
probe is itself an EXACT, zero-calibration discriminator (not a new fudge threshold):
- Equal (annuity convention, e.g. the toy) → run §3.4's exact formula UNCHANGED at d1 and d2, FAIL
  if it breaks — never loosened.
- Differs (full-curve convention, e.g. Meridian) → WARN, reporting both dv01 values and the d2
  npv-vs-predicted numbers for a human to read, PLUS one hard, zero-tolerance FAIL check that never
  goes away regardless of convention: `sign(npv(d2)) == sign(dv01(d2)*(par(d2)-K))` — a sign
  disagreement (the classic npv/dv01 opposite-convention bug this check exists to catch) is never
  just "a different convention," so it is never downgraded to a WARN.

Verified all three protected cases: `toy_usd_irs.yaml` → annuity path → PASS (unchanged, exact);
`meridian_usd_irs.yaml` (clean) → full-curve path → sign agrees → WARN, zero new FAILs, so
`test_meridian_example_and_mistakes[meridian_usd_irs.yaml-None]` (asserts empty `fails`) is
unaffected; `mistakes/dv01_sign_not_flipped.yaml` and `mistakes/par_rate_in_percent.yaml` still land
in the full-curve WARN path (Meridian's DV01 formula is untouched by either mistake) but now ALSO
correctly FAIL the sign check (a genuine bonus catch — the existing tests for those fixtures only
assert their OWN named check is among the fails, which still holds).

**Evidence:** the numeric probe/residual figures above, reproduced by
`skills/pricebt-verify-asset-config/scripts/check_asset.py`'s own `swap_pv_identity` block and by an
ad-hoc script run against both configs during this diagnosis (not committed; the numbers are
recorded here and are reproducible by rerunning the CLI on either config with `--date` twice).

**Alternative considered:** (a) loosen §3.4's absolute tolerance until Meridian passes — rejected
outright: the observed gap is ~7,400x the specified tolerance, so "loosening" would gut the formula's
ability to catch the exact unit/sign bugs it exists for (confirmed separately: `bad_identity_par_pct`
below produces a FAIL of a similar or larger order under the SAME unmodified tolerance, which a
loosened bound calibrated to hide Meridian's gap would also hide); (b) flip
`test_meridian_example_and_mistakes[meridian_usd_irs.yaml-None]`'s expectation to tolerate this one
named FAIL — rejected: that test is outside this tier's ownership (a different, pre-existing skill's
worked example) and the task's explicit instruction is that it "still passes unchanged"; a checker
whose flagship "this config is fine" example FAILs its own new row is a worse outcome for
`pricebt-verify-asset-config`'s users than the probe-gated design; (c) skip `swap_pv_identity`
entirely on any config that isn't obviously toy/ARBS-shaped — rejected: indistinguishable from (and
strictly worse than) the probe, which gets the SAME outcome (no false FAIL) while still running a
real, informative check (the WARN detail, and the sign-disagreement FAIL) on every config, annuity or
not.

**Also noted (not this tier's problem, flagged for T2/the cookbook):** `swap_pnl.py`'s `exact_split`
and §2.1's gamma denominator both assume the SAME annuity-dv01 convention — their exact
reconciliation promises (T-RECON, A-DAILY) are therefore scoped to libraries that report dv01 as the
annuity (confirmed true for the toy; asserted true for ARBS by §2.1, to be verified when T2 builds
the real `gamma`/`theta` functions against rateslib). A config using a full-curve DV01 convention
(like Meridian, or conceivably a future non-toy asset in this repo) would need `swap_pnl_definition`
re-derived against §2.7's general `dPV = pv01*dpar + (par-K)*dpv01 + dpv01*dpar` split rather than
the simplified `PNL_delta + PNL_gamma` this plan builds — out of scope for T1.
