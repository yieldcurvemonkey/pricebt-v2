# Notes for AI agents working in this repository

* **Connecting an external pricing library to pricebt** (an in-house bank platform, QuantLib, rateslib, anything): read `skills/README.md`, then follow
  `skills/pricebt-wire-external-library/SKILL.md`. It starts with a fast path, is an ordered playbook, and has two tested worked examples (in-process, and service-style); the common errors are on one page, `skills/pricebt-wire-external-library/references/common-errors.md`.
  Work in a copy or a worktree (`skills/pricebt-guards-and-packaging/SKILL.md`, step 1) and copy with absolute `robocopy /XD` paths only (a bare `results` also drops `src/pricebt/results`).
* **Architecture:** `docs/DESIGN.md`; decisions `docs/design/adr/`; requirements `docs/design/11-refactor-spec.md`; what changed and why `docs/design/11-refactor-changelog.md`.
* **The one rule:** everything under `src/pricebt` except `contrib/` has zero dependence on any pricing library. Do not edit core to make a library work.
* **Run things** from the repository root with `PYTHONPATH=src;tests` (Windows; `:` on Linux): `python -m pytest tests -q -o addopts= -p no:cacheprovider` (about 12 minutes; run single files while
  working), `python -m pricebt validate|run|describe|tieout ...`.
* A removed or weakened test needs a row in the change log; a widened tie-out tolerance needs a written reason (`tasks/tolerance_ledger.yaml`).
