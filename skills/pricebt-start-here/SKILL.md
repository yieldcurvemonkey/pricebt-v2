---
name: pricebt-start-here
description: Entry point for any AI agent working in the pricebt repository - routes you to the right skill for connecting a bank pricing/data library (Citi Datapoint-style, JPM Athena-style, in-house), running your first backtest, porting gs_quant notebooks, or taking a strategy idea through intake, implementation, adversarial review, spot checks and a tearsheet. Use first, whenever you are new to this repository or unsure which skill applies.
---

# Start here

pricebt is a gs_quant-compatible backtester that owns no pricing and no data: your library plugs in through one YAML asset config per asset. Two journeys cover almost everything.

## Journey A: "I have a pricing library; get me to a backtest" (target: under an hour)

| Step | Do | Skill |
|---|---|---|
| A0 | Read the mental model (5 minutes) | [`pricebt-architecture`](../pricebt-architecture/SKILL.md) |
| A1 | Answer the discovery questions about your library: market handle by date, trade construction, measure units and signs, holiday behaviour | [`pricebt-connect-pricing-library`](../pricebt-connect-pricing-library/SKILL.md) |
| A2 | Copy the template or worked example config and replace its library calls with yours | same skill; patterns in [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md) |
| A3 | Prove the config: `python skills/pricebt-verify-asset-config/scripts/check_asset.py <config>` | [`pricebt-verify-asset-config`](../pricebt-verify-asset-config/SKILL.md) |
| A4 | Remote platform? Secrets, point-in-time data, record/replay, safety envelope | [`pricebt-enterprise-integration`](../pricebt-enterprise-integration/SKILL.md) |
| A5 | First backtest: the 10-line quick start in `README.md`, pointed at your config | [`pricebt-strategy-recipes`](../pricebt-strategy-recipes/SKILL.md) |

## Journey B: "Here is an idea; tell me if it works"

| Step | Do | Skill |
|---|---|---|
| B0 | Run the whole pipeline with gates | [`pricebt-strategy-workflow`](../pricebt-strategy-workflow/SKILL.md) |
| B1 | Idea → follow-up questions → frozen `strategy_spec.yaml` | [`pricebt-strategy-intake`](../pricebt-strategy-intake/SKILL.md) |
| B2 | Spec → pricebt strategy → backtest | [`pricebt-strategy-recipes`](../pricebt-strategy-recipes/SKILL.md) |
| B3 | Try to break it: checklist, robustness experiments, significance | [`pricebt-adversarial-review`](../pricebt-adversarial-review/SKILL.md) |
| B4 | Verify the numbers | [`pricebt-spot-checks`](../pricebt-spot-checks/SKILL.md) |
| B5 | Deliver the tearsheet and a summary | [`pricebt-tearsheet-report`](../pricebt-tearsheet-report/SKILL.md) |
| - | The standards behind every step | [`pricebt-research-methodology`](../pricebt-research-methodology/SKILL.md) |

## Other entry points

- **Existing gs_quant backtest code:** [`pricebt-port-gs-notebook`](../pricebt-port-gs-notebook/SKILL.md).
- **An error message naming a config key:** [`pricebt-asset-config-cookbook`](../pricebt-asset-config-cookbook/SKILL.md), `references/error-catalogue.md`.

## Ground rules (from `AGENTS.md`)

1. **Never put library code, or library names, in `src/pricebt`.** The guards fail the build. Your library lives in asset configs, ideally in a private repository, because this one is public.
2. **Run from the repository root** with `PYTHONPATH=src;tests` (Windows) or `src:tests` (POSIX).
3. **Test suite:** `python -m pytest tests -o addopts= -p no:cacheprovider`.
4. **No real market data is needed to learn.** The toy assets in `tests/assets/` and the fictional bank library in `skills/pricebt-connect-pricing-library/example/` behave like real ones.
