# pricebt v2 — start here

pricebt v2 is a gs_quant-compatible, event-driven backtester with **no pricing or market data of its own**. Each tradable asset is described by one YAML config whose Python expression strings call whatever library the user chooses (e.g. ARBS). pricebt ports `gs_quant.backtests` near 1:1 and only evaluates those strings.

## Reading order for the implementer

1. `DESIGN.md` §0 (the decisions), §2 (the five MUSTs), then the rest of `DESIGN.md` in full.
2. `IMPLEMENTATION_PLAN.md` §0 (ground rules, DO-NOT list, commands) and §1 (phases, gates, the ultracode workflow recipe).
3. For each task: the research sections it cites in `research/`.

## Research notes (evidence; cite by section, e.g. R02§4)

| Note | Contents |
|---|---|
| `research/01-gs-strategy-triggers-datasources.md` | Strategy, every trigger (exact semantics, MR golden table), data sources (the v2 GenericDataSource spec), backtest_utils, RelativeDate rules, 1.5.4 vs 2.1.17 drift, proposed deviations D1–D15 |
| `research/02-gs-actions-and-generic-engine.md` | Every action's fields, naming rules R1–R7, `get_final_date`, transaction costs, `run_backtest` signature, **engine pseudocode (phases 0–8)**, action handlers, every GS-server touch point, quirks Q1–Q18, the 2.1.17 diff |
| `research/03-gs-results-backtest-objects.md` | `BackTest` members, the exact `result_summary` / `trade_ledger` / `strategy_as_time_series` / `pnl_explain` algorithms and shapes, verified worked examples, risk-measure identity, the currency semantics |
| `research/04-gs-instrument-risk-session.md` | Import paths notebooks use, the IRSwap 31-field constructor, other instruments' field lists, Instrument/Portfolio behaviour, enums, PricingContext/HistoricalPricingContext, GsSession, dates (verified fixtures), risk measures |
| `research/05-notebook-coverage.md` | All 24 gs backtest notebooks: API use, which port with swaps only, the 040304 deep dive and v2 text, 040310/040303/040300 dives, notebook defects |
| `research/06-arbs-pricing-api.md` | ARBS `IRSwapsMDP` / `RLIRSwapCurve`: sources, request rules, the method table with units and signs, re-marking, the ladder recipe, FX (none offline), **safety hazards**, a draft config |
| `research/07-pricebt-v1-engine-strategy-results.md` | v1 engine/strategy/results inventory (mostly stripped in v2; useful for what NOT to repeat) |
| `research/08-pricebt-v1-strip-inventory.md` | v1 strip inventory: what to delete and the few files to salvage (yamlio, progress, errors, nb_build, mutcheck) |

## Key locations

| What | Where |
|---|---|
| Worktree / branch | `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2` / `v2-redesign` |
| v1 (recoverable) | tag `v1-final` on `main` (primary checkout `C:\Users\chris\clee\gsquant-temp-claude\pricebt`) |
| gs port source (2.1.17, read-only) | `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant\backtests\` |
| gs API reference (1.5.4, read-only) | `C:\Users\chris\anaconda3\Lib\site-packages\gs_quant\` (base python `C:\Users\chris\anaconda3\python.exe`) |
| Python for pricebt | `C:\Users\chris\anaconda3\envs\stir\python.exe` |
| ARBS (read-only; import only in plan task P5.2 after the user approves) | `C:\Users\chris\clee\ARBS` |

## Suggested hand-off prompt (for the implementing session)

> Implement pricebt v2 in the worktree `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2` (branch `v2-redesign`) exactly as specified in `docs/v2/DESIGN.md` and `docs/v2/IMPLEMENTATION_PLAN.md`. Work phase by phase (P0→P6), with one workflow per phase following the plan's §1 recipe: parallel implementation agents with disjoint file ownership, one adversarial verifier per task, a fix loop, then run the phase gate yourself and commit. Re-read DESIGN §2 (the five MUSTs) at the start of every phase. Stop and ask me at every item in IMPLEMENTATION_PLAN §9, and in particular before anything imports ARBS. ultracode
