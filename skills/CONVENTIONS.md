# Conventions for the pricebt skills library

This file is for skill authors and maintainers. **Users of the skills start at [`README.md`](README.md).**

## Layout

```
skills/
  README.md                         index: which skill for which job (every skill must be listed)
  CONVENTIONS.md                    this file
  pricebt-<name>/
    SKILL.md                        required. YAML frontmatter + the procedure
    references/*.md                 optional. Detail the SKILL.md links to
    scripts/*.py                    optional. Runnable helpers (functions + a CLI)
    templates/*                     optional. Files the agent copies and fills in
    example/                        optional. A worked, tested example
```

## SKILL.md format

- Frontmatter comes first:

  ```yaml
  ---
  name: pricebt-<name>          # MUST equal the directory name
  description: <one or two sentences: WHAT it does and WHEN to use it (trigger phrases)>
  ---
  ```

- Then the body, in this order:
  1. a one-paragraph purpose;
  2. **When to use / not use**;
  3. **Inputs** and **Outputs**;
  4. **Procedure**, as numbered steps with exact commands;
  5. **Checks**, i.e. how you know you are done;
  6. **Pitfalls**;
  7. **Related skills**.
- Keep a SKILL.md under about 300 lines. Move detail into `references/`.
- Write for an agent that knows its *own* pricing library and does NOT know pricebt. State facts, give commands, and link to repository files.

## Paths, commands, environment

- Every path is **relative to the repository root**. Never write a machine-specific absolute path (the repository is public).
- Commands assume the repository root as the working directory and `PYTHONPATH=src;tests` (Windows) or `src:tests` (POSIX):

  ```powershell
  $env:PYTHONPATH = "src;tests"; python -m pytest tests -o addopts= -p no:cacheprovider
  ```

- The runtime dependencies of `src/pricebt` are the stdlib, numpy, pandas, PyYAML, python-dateutil and tqdm (MUST-1; the guards enforce it).
- **Skill scripts** may also use matplotlib, scipy and jinja2, because they are *not* part of `src/pricebt`.

## Scripts

- A script lives in `skills/<skill>/scripts/<name>.py`. It exposes plain functions (so an agent can call them in-process on a live `BackTest` object) **and** an `if __name__ == "__main__":` CLI built with argparse.
- Import a script in-process with a path insert. This is the documented way; do not install the scripts:

  ```python
  import sys; sys.path.insert(0, "skills/pricebt-spot-checks/scripts")
  import spot_check
  ```

- Scripts never import a vendor library. They reach pricing only through pricebt (`PricebtSession`, the instruments, `BackTest`).
- Plotting uses `matplotlib.use("Agg")`. Reports are self-contained (images embedded as base64) and deterministic apart from an explicit "generated at" line.

## Tests

- Every script, recipe and example is executed by a test in `tests/skills/test_skill_<name>.py` (files are prefixed `test_skill_` so module names never collide).
- Tests use the toy assets in `tests/assets/` (or the fictional example library) and never a real vendor library.
- `tests/skills/test_skills_library.py` checks the library itself:
  - frontmatter present, and `name` equals the directory;
  - every relative link resolves;
  - every repository path cited in backticks exists;
  - the README lists every skill;
  - the `.claude/skills/` mirror is in sync.

## Public-repository hygiene

- No secrets, no credentials, no internal hostnames.
- **No real vendor APIs are claimed.** Bank platforms (for example Citi Datapoint (DP) or JPM Athena) may be *named* as examples of a platform shape. Every example library in this repository is fictional, and says so in its first line.
- Books are cited as `(Author, Title, ch. N, p. M)`, with paraphrase only and no copied passages.

## Mirror for Claude Code

`.claude/skills/<name>/SKILL.md` is a generated stub with the same frontmatter and a pointer to `skills/<name>/SKILL.md`. Regenerate the stubs with:

```powershell
python tools/sync_agent_skills.py
```

A test fails if the mirror drifts from the skills.
