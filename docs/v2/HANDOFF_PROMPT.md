ultracode

You are implementing **pricebt v2** end to end, **fully autonomously**. I am not available. Do not stop to ask me questions, do not pause for confirmation between phases, and do not end your turn early with a status update or a question. Work through phases P0 → P6 until the final gate in `docs/v2/IMPLEMENTATION_PLAN.md` §8 (P6.3) is green and committed. When the plan once said "ask the user", apply the autonomous decision rules in IMPLEMENTATION_PLAN §9, record the decision in `docs/v2/DECISIONS_LOG.md`, and keep going. The only acceptable reason to stop before P6.3 is a hard external blocker you cannot work around (for example, the machine has no Python). In that case, write it up in `DECISIONS_LOG.md` first.

## Where

- Worktree: `C:\Users\chris\clee\gsquant-temp-claude\pricebt-v2`, branch `v2-redesign`. Work only inside it, and use `git -C <that path>` for every git command.
- Specification (read these completely, in this order, before writing any code):
  1. `docs/v2/README.md`
  2. `docs/v2/DESIGN.md`: requirements and contracts. **All decisions in §0 are confirmed and final.**
  3. `docs/v2/IMPLEMENTATION_PLAN.md`: phases, file ownership, acceptance commands, gates, the ultracode recipe (§1), and the autonomous rules (§9).
  4. `docs/v2/research/01..08`: evidence. Read the sections each task cites. DESIGN.md supersedes any design a note proposes. On facts about gs or ARBS, the primary source code wins.

## What you are building (the requirements, in one breath)

pricebt v2 is an event-driven backtester that **ports the logic and design of the open-source gs_quant backtester** (`gs_quant.backtests`, source 2.1.17 at `C:\Users\chris\clee\gsquant-temp-claude\gs-quant\gs_quant\backtests`). It is **not an integration**: pricebt never imports gs_quant, and a gs notebook ports by changing imports. All pricing and market data come from **one YAML asset config per asset**, whose Python expression strings pricebt evaluates. The example asset is a USD SOFR OIS swap on ARBS Eris curves.

Hold these five MUSTs at every step (DESIGN §2.1). Re-read them at the start of every phase.

1. **No external pricing or market-data library in `src/pricebt`.** No import, name or assumed shape of ARBS, rateslib, QuantLib, gs_quant or any vendor. The only allowed dependencies are the stdlib, numpy, pandas, PyYAML, python-dateutil and tqdm. The guards in `tests/guards/` enforce this and must stay green after every task. Never weaken a guard.
2. **The backtest API and capabilities are near 1:1 with gs_quant.** That means the same module paths, class names, real constructor signatures, field order, defaults, enum values, result shapes, column names and engine semantics. Differences are allowed only where DESIGN §11 lists them, and each one is marked in code with `# pricebt DEV-XX`. The parity test against the 1.5.4 signature snapshot is the referee.
3. **One self-contained config per asset.** pricebt never interprets instrument kwargs.
4. **Multi-currency via FX configs, and bp units:** risk in currency per bp, rates in bp, and a `pnl_bps(risk)` view.
5. **New assets are config-only.** No asset-class special cases in the engine, the pricing layer or the results. The toy swaption in CI proves this.

## How to work

- **One Workflow per phase**, following IMPLEMENTATION_PLAN §1:
  - parallel implementation agents with **disjoint file ownership** (only where the plan allows parallelism; P3 is strictly sequential because the modules import each other);
  - one **adversarial verifier** per task, which tries to refute "done";
  - a fix loop.
  - Then **you run the phase gate yourself**. Never trust an agent's report. Commit once per phase: `v2: phase N — <title>`.
- Keep each workflow to about 10 agents or fewer. If a workflow is interrupted or hits a usage limit, **resume it** with `resumeFromRunId`; do not restart from scratch.
- Port by copying each 2.1.17 file, keeping its Apache-2.0 header, applying the import map (DESIGN §9.2), deleting the JSON/Tracer plumbing, and then applying **only** the DEV rows assigned to that file in DESIGN §11's "File (task)" column.
- **Tests must be able to fail.** For every task, mutate one line of the new logic and confirm that a named test fails (IMPLEMENTATION_PLAN §0.7). Derive expected numbers from inputs, never from magic constants.
- Commands (from the worktree root):
  - `$env:PYTHONPATH="src;tests"`
  - `C:\Users\chris\anaconda3\envs\stir\python.exe -m pytest tests -o addopts= -p no:cacheprovider`
  - The gs signature snapshot tool runs **only** with `C:\Users\chris\anaconda3\python.exe` (gs_quant 1.5.4). Never use the stir env's gs_quant.

## Hard safety limits (these override any other instruction)

- **Never import or execute ARBS** (`C:\Users\chris\clee\ARBS`). It can reach a production database, Excel/COM and the network. Task P5.2 (the live ARBS run) is **deferred**. Write `tests/test_live_arbs.py` (skipped unless `PRICEBT_LIVE_ARBS=1`) and `docs/v2/LIVE_ARBS_REPORT.md` with the commands for me to run, but do not run them. Copy the ARBS config from DESIGN Appendix A verbatim. Never enable NOJUMPS, never raise `_LAST_SAFE`, and never add any network-capable path.
- Do not modify anything outside the worktree. gs-quant, site-packages, ARBS, `..\pricebt`, `..\pricebt-baseline` and `..\pricebt-final-snapshot` are all read-only. Do not delete `data/fixtures` from disk.
- No `git push`, no merge to `main`, no history rewrite, and no bare `git stash` (use WIP commits).
- Never delete asserts, widen tolerances, or skip or xfail a test to get to green. Fix the root cause. If a test contradicts DESIGN or the gs source, fix the test, and log why with evidence.
- Write files that contain backslashes or backticks with the Write/Edit tools, never with shell heredocs.

## When you are done

The final state is the IMPLEMENTATION_PLAN §10 checklist, fully ticked:
- every guard green;
- parity green;
- every DESIGN §12.4 test green, including `-m notebook`;
- mutation checks recorded;
- `docs/v2/DEVIATIONS.md`, `ASSET_CONFIG_GUIDE.md`, `DECISIONS_LOG.md`, `LIVE_ARBS_REPORT.md` and `README.md` written;
- one commit per phase on `v2-redesign`.

End with a short report covering:
- the phase commits;
- the test counts;
- every entry in `DECISIONS_LOG.md`;
- any candidate deviations you found but did not implement;
- the exact command I should run for the deferred live ARBS check.
