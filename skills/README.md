# pricebt skills: wire an external pricing library into pricebt

A library of skills for an AI agent (or a person) who must connect a pricing library, for example a bank's in-house rates platform, to pricebt. The agent is assumed to know its
own library (its documentation, code and examples) and NOT to know pricebt. Each skill is a directory with a `SKILL.md` (Claude Code skill format: YAML frontmatter `name` and
`description`, then the steps) and optional `references/`. Start with **`pricebt-wire-external-library`**: it is the ordered playbook and points at every other skill, and its first section is a FAST PATH (the files to copy, the commands, the decisions).

**Two tested worked examples** sit next to the playbook; copy from the one that has your library's shape:

* [`example/`](pricebt-wire-external-library/example/README.md): an in-process library (`acmelib`, adapter `acme_adapter`), which may also serve market data.
* [`example-service/`](pricebt-wire-external-library/example-service/README.md): a service-style library, where the platform prices only from a market you upload (`zeta`, adapter `zeta_adapter`).

The failures every adapter meets are on one page, [`common-errors.md`](pricebt-wire-external-library/references/common-errors.md); the skills link to it instead of repeating them.

## Skills

| Skill | Use it when you must |
|---|---|
| [`pricebt-wire-external-library`](pricebt-wire-external-library/SKILL.md) | do the whole job: fast path, ordered phases, checks, definition of done; contains two tested worked examples (`example/`, `example-service/`) |
| [`pricebt-architecture-and-rules`](pricebt-architecture-and-rules/SKILL.md) | understand the model and the zero-dependence rule before touching anything |
| [`pricebt-discover-the-library`](pricebt-discover-the-library/SKILL.md) | inventory an unfamiliar library and probe its conventions on known answers |
| [`pricebt-enterprise-platform-patterns`](pricebt-enterprise-platform-patterns/SKILL.md) | handle a remote or service-based platform: sessions, secrets, batching, offline tests, errors, confidentiality |
| [`pricebt-map-library-to-schemas`](pricebt-map-library-to-schemas/SKILL.md) | map every pricebt schema name to a library callable, with units and signs |
| [`pricebt-bindings-cookbook`](pricebt-bindings-cookbook/SKILL.md) | write and debug bindings (`target`, `kwargs`, `reduce`, `scale`, `sign`, `keys`) |
| [`pricebt-market-data-snapshots`](pricebt-market-data-snapshots/SKILL.md) | feed market data: providers, snapshot types, time mapping, recorded stores, a library that also serves the data |
| [`pricebt-wrap-and-pricer`](pricebt-wrap-and-pricer/SKILL.md) | turn a snapshot into the library's objects, once, with its global state guarded |
| [`pricebt-instrument-kit`](pricebt-instrument-kit/SKILL.md) | write the factory, the conventions mapping, the Kit and the Stack |
| [`pricebt-layers-and-ladder`](pricebt-layers-and-ladder/SKILL.md) | implement dollar delta, the delta ladder and the carry / roll / delta / convexity layers |
| [`pricebt-conformance-and-tieout`](pricebt-conformance-and-tieout/SKILL.md) | prove the adapter: the layer conformance kit, then the tie-out and its tolerances |
| [`pricebt-debug-tieout-differences`](pricebt-debug-tieout-differences/SKILL.md) | find why two stacks differ, from the level and quantity down to the cause |
| [`pricebt-guards-and-packaging`](pricebt-guards-and-packaging/SKILL.md) | register the library with the guards, markers, extras and documents |
| [`pricebt-run-config-and-reports`](pricebt-run-config-and-reports/SKILL.md) | write the config, run the CLI, read the results, build the notebook |

## Install

* **Claude Code:** copy the directories you need into `.claude/skills/` of the pricebt repository (create it first), for example `cp -r skills/pricebt-* .claude/skills/` (PowerShell:
  `New-Item -ItemType Directory -Force .claude\skills; Copy-Item -Recurse skills\pricebt-* .claude\skills\`). Claude then loads a skill when its `description` matches the task. Work from the
  repository root: the skills cite repository paths, so a global install (`%USERPROFILE%\.claude\skills\`) still needs the pricebt checkout as the working directory.
* **Any other agent:** open `skills/pricebt-wire-external-library/SKILL.md` and follow it; every skill is plain Markdown. `AGENTS.md` at the repository root points here.
* Paths inside the skills are relative to the repository root, and commands assume that working directory with `PYTHONPATH=src;tests`.

## Honest limits

* Nothing here knows any bank library's API. The skills give you the questions to ask of YOUR library and the pricebt side of every answer; the example libraries (`acmelib`, and `zeta` of the service example) are fictional.
* The in-process example's numerical core reuses pricebt's reference stack, so its tie-out is exact by construction; the service example ties out with the shipped tolerances only after the reference is put on the library's fixed pillar set (a difference of definition, aligned by a second overlay, never declared as a tolerance). A real independent library shows real noise floors; declare each accepted
  difference with a reason (`pricebt-conformance-and-tieout`).
* The Python facade (`PricebtSession`) cannot load an adapter that lives outside the `pricebt` package and binds dotted-path functions; the CLI and `pricebt.api` can
  (`pricebt-instrument-kit`).
* `python -m pricebt tieout` compares `dv01`, `gamma` and `rate`; the delta ladder is compared only through `pricebt.tieout.run_tieout(..., audit_measures=...)`.

The skills are checked by `tests/test_skills_library.py` (structure, links, cited paths; paths under the git-ignored `data/fixtures` are skipped in a clone that lacks them) and the worked examples by
`tests/test_skills_example_acme.py` and `tests/test_skills_example_zeta_{facts,wrap,swap,proofs,no_infra}.py`.
