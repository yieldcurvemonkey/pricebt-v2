# pricebt skills library

Skills for AI agents (and people) who work with pricebt. The library assumes you know **your** pricing and data library (a bank platform such as a Citi Datapoint-style or JPM Athena-style service, an in-house library, or QuantLib), and that you do not know pricebt. Every skill is a directory containing a `SKILL.md` in Claude Code skill format (YAML frontmatter `name` and `description`, then the procedure), with optional `references/`, `scripts/`, `templates/` and a worked `example/`.

**New here? Open [`pricebt-start-here`](pricebt-start-here/SKILL.md).**

## Journey A: connect a pricing/data library → first backtest

| Skill | Use it to |
|---|---|
| [`pricebt-start-here`](pricebt-start-here/SKILL.md) | find the right skill |
| [`pricebt-architecture`](pricebt-architecture/SKILL.md) | learn the model, the rules, and the engine semantics that change numbers |
| [`pricebt-connect-pricing-library`](pricebt-connect-pricing-library/SKILL.md) | go from "I have a library" to a working asset config and a smoke backtest (fast path, discovery questionnaire, a fictional bank-SDK worked example) |
| [`pricebt-verify-asset-config`](pricebt-verify-asset-config/SKILL.md) | prove a config automatically (`check_asset.py`): pinning, units, signs, ladder, scaling, smoke backtest |
| [`pricebt-asset-config-cookbook`](pricebt-asset-config-cookbook/SKILL.md) | find patterns by library shape, unit and sign conversions, and the error catalogue |
| [`pricebt-enterprise-integration`](pricebt-enterprise-integration/SKILL.md) | handle remote platforms: secrets, point-in-time data, record/replay (`record_replay.py`), and safety envelopes |
| [`pricebt-port-gs-notebook`](pricebt-port-gs-notebook/SKILL.md) | port existing gs_quant backtest code |

## Journey B: idea → reviewed tearsheet

| Skill | Use it to |
|---|---|
| [`pricebt-strategy-workflow`](pricebt-strategy-workflow/SKILL.md) | run the gated pipeline end to end (`run_study.py`) |
| [`pricebt-strategy-intake`](pricebt-strategy-intake/SKILL.md) | ask the follow-up questions and turn the idea into a frozen `strategy_spec.yaml` (`spec.py`) |
| [`pricebt-strategy-recipes`](pricebt-strategy-recipes/SKILL.md) | turn a spec into a backtest (`recipes.py`: carry roll, mean reversion, momentum, curve trade, delta-hedged, risk band, event, stop-loss overlay) |
| [`pricebt-adversarial-review`](pricebt-adversarial-review/SKILL.md) | break the backtest: checklist, robustness experiments (`robustness.py`), reviewer prompts |
| [`pricebt-spot-checks`](pricebt-spot-checks/SKILL.md) | verify the numbers (`spot_check.py`) and run the manual checks |
| [`pricebt-tearsheet-report`](pricebt-tearsheet-report/SKILL.md) | deliver the tearsheet (`tearsheet.py`: HTML, markdown, metrics, charts) |
| [`pricebt-research-methodology`](pricebt-research-methodology/SKILL.md) | apply the research standard from four quant books, with a significance calculator (`research_stats.py`) |

## Install and use

- **Claude Code:** the skills are mirrored as stubs in `.claude/skills/`, so Claude discovers them when you open the repository. Each stub points back here. Regenerate the stubs with `python tools/sync_agent_skills.py`.
- **Any other agent:** start from `AGENTS.md`, which points here. Every skill is plain Markdown.
- **Paths and environment:** paths are relative to the repository root. Commands assume that working directory and `PYTHONPATH=src;tests` (Windows) or `src:tests` (POSIX).
- **Scripts:** import them in-process with `sys.path.insert(0, "skills/<skill>/scripts")`, or run them as CLIs.

## Tests

`tests/skills/` executes every script, recipe and worked example, and `tests/skills/test_skills_library.py` checks the library itself:
- frontmatter and names;
- that links resolve;
- that cited paths exist;
- that this README lists every skill;
- that the `.claude/skills/` mirror is in sync.

Authoring rules are in [`CONVENTIONS.md`](CONVENTIONS.md).

## Honest limits

- Nothing here knows your bank library's real API. The skills give you the questions to ask of your library and the pricebt side of every answer. The example library (`meridian_sdk`) is **fictional**.
- The methodology references paraphrase four books (cited inside). They are guidance, not a substitute for your desk's own model-risk and validation standards.
