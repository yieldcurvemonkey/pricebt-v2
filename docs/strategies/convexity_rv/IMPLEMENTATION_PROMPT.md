# Prompt: implement the forward curve vs straddle backtest

You are implementing a systematic backtest on **our internal backtesting framework**. You have access to that framework's code, data, conventions and tests. The person who wrote the spec and this prompt did **not**. Nothing below assumes anything about the framework's architecture, API, data sources, configuration system or reporting. Where the spec uses words like "ticket", "engine", "config key", "trial log", "blotter" or shows YAML, those describe *what* must be computed or recorded. Implement them the way the framework already does things.

## Inputs

All three are in `docs/strategies/convexity_rv/` on the `convexity-rv-backtest-spec` branch of the `pricebt-v2` repo, or were provided to you directly:

1. **`BACKTEST_SPEC.md`**: the source of truth for the math and the research protocol. Read the "Read this first" note at the top, then the whole document, before writing any code.
2. **`screener.py`**: a prototype that shows how the signal is built. Use it as a reference for the formulas only, and as a numerical cross-check. Do not copy its structure. It reads one spreadsheet, uses flat-yield annuities and spot swap rates as option underlyings, and has no position engine.
3. **`REFERENCES.md` and `papers/`**: background reading. You don't need it to implement, but it's useful when a concept is unclear. The JPM papers use "carry" loosely; the spec's §5.1 defines it precisely.

## How to work

**Use the framework's own patterns.**
- Before designing anything, find how the framework already handles each of these:
  - strategies, signals and their parameters;
  - instruments: spot and forward-starting swaps, swaptions or straddles;
  - market data: forward curves, swap rates, a vol surface or grid;
  - pricing and risk: DV01, gamma, vega, theta;
  - position ageing and marking;
  - rebalancing and delta hedging;
  - transaction costs;
  - P&L attribution;
  - walk-forward or parameter sweeps;
  - experiment logging;
  - tests;
  - reports.
- Find the closest existing strategy and use it as your template.
- Reuse existing components. Don't build parallel infrastructure.
- If a shared framework component needs to change, stop and ask first. Don't edit it quietly.

**Keep the math; the implementation is free.**
- Must be preserved:
  - the formulas in spec §5;
  - the trade logic in §6;
  - the marking and hedging economics in §7: positions age, are marked at their live start, hedge on a shared grid and re-match gamma;
  - the attribution identities in §8;
  - the controls in §11;
  - the evaluation protocol in §1 and §12–§14.
- Free to change: names, data structures, storage and code layout.
- Where the framework has better inputs than the prototype, use them. Examples: a real curve build, true forward-starting swaption underlyings, a vol smile, real cost models, proper annuities.
- Record every such substitution in a **deviations list**: what the spec says, what you did and why, and the expected effect on the numbers.

**When to stop and ask instead of guessing:**
- a spec concept has no equivalent in the framework;
- data the spec needs is missing or shorter than assumed (the vol history should start around 2015-10);
- two reasonable implementations would give materially different results;
- you think the spec is wrong. Say so, with evidence, rather than silently "fixing" it.

## Phases

Each phase ends with a short report (see Reporting). **Stop after Phase 0 and after Phase 4 and wait for approval.**

**Phase 0: survey and plan. No strategy code yet.**
- Write a mapping table: each spec concept → the framework component that will implement it, or "missing".
- Cover at least:
  - forward strips and roll derivatives;
  - annuity and duration;
  - the option grid and total-variance interpolation;
  - Bachelier straddle pricing and Greeks;
  - curve and option positions with ageing;
  - move-based delta hedging;
  - gamma re-match and rolls;
  - costs;
  - attribution buckets;
  - walk-forward;
  - trial logging;
  - the holdout lock.
- List open questions and a build plan.

**Phase 1: data (§3).**
- Load the forward strips, spot swap rates and the ATM normal vol grid.
- Apply the alignment rules: inner join, drop weekends, drop stale rows and log them, never forward-fill.
- Lock the holdout so that no code path can read it before §13.4. Enforce this in code, not by convention.

**Phase 2: signal (§5).**
- Compute every §5 quantity, including:
  - `carry_horizon` and the carry definition in §5.1;
  - horizon-matched σ_imp and `option_sizing` in §5.3;
  - the side logic, gates G1–G7 and option selection.
- **Parity check:** run `screener.py` on the same input data, then compare carry, ΔD, σ_BE, edge, basis, risk and Sharpe for every curve × option cell on several dates.
  - Anchors on 2026-10-17: σ_BE(10y10y/20y10y) is between 87 and 97; σ_BE(20y5y/25y5y) is below 25.
  - Differences that come from your better inputs, such as real annuities, are fine. Measure them and list them as deviations.
  - Unexplained differences are bugs.

**Phase 3: position lifecycle and engine tests (§6, §7, §16).**
- Implement entries, exits X1–X6, rolls, portfolio constraints, curve and option positions, hedging, re-matching and costs.
- Implement T1–T12 from §16 in the framework's test tooling.
- **Prove the tests work:** break a sign or a factor in the code (for example, flip the option sign for side −1, or drop the 2 in the straddle premium). Confirm that the relevant test (T10 or T11) fails, then restore the code.
- Check the attribution residual: at most 10% of gross (T9).

**Phase 4: Stage A (§12). Stop and report.**
- Build the unit-package panel and run the IC, quintile, side-split and persistence tests.
- Report H5 pass or fail per §12, for the score S and for the dislocation score D.
- Do not start Stage B until approved.

**Phase 5: Stage B (§13, §11, §14, §15).**
- Run walk-forward with **only** the six tuned parameters, and apply the plateau rule.
- Run controls C1a, C1b, C2, C3a, C3b, C4 and C5, every listed ablation, and the cost sweep.
- Report everything per side and for the combined book.
- Append every run to the trial log, and use the log's row count as N in the deflated Sharpe.
- **Do not run the holdout.**

**Phase 6: outputs (§17, §18).**
- Produce the report, the H1–H6 scorecard per side and combined, the deviations list, and the §18 limitations as they apply to your implementation.
- Run the holdout (§13.4) **only when explicitly told to**, exactly once, and report the result whatever it is.

## Non-negotiables

- **No look-ahead.** Signals use data up to the close of t, execution is at t + lag, and every rolling statistic is backward-looking (T6, T8).
- **The holdout is untouched** until instructed.
- **Costs are on** in every headline result.
- **Every run is logged** with its full config.
- **No tuning** beyond the six listed parameters. In-sample results are never reported as performance.
- **Do not change defaults silently.** Any change to a spec default is a deviation with a reason.
- **Report failures plainly.** A negative result is an acceptable outcome (§1). Don't hunt for settings until something passes.

## Reporting (at the end of every phase)

1. What was built, and where it lives in the framework.
2. Test and parity results, with numbers.
3. Deviations added in this phase.
4. Open questions and blockers.
5. The next step.

Keep each report short. Use tables for numbers.
