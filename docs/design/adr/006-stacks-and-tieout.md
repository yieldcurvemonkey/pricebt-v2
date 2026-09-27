# ADR 006: Stacks are overlays; the tie-out compares five levels

Status: accepted, built (`src/pricebt/config/stacks.py`, `src/pricebt/tieout/`, `pricebt tieout`). Requirements X1-X6, SC8-SC10.

## Context

The purpose of the refactor is to run ONE backtest across rateslib, QuantLib and a dependency-free reference stack and see where, and why, they differ.

## Decision

* A **stack** is an overlay that may set only `instruments.<n>.factory`, `instruments.<n>.bind` and `market.pricers.<r>.wrap`. `apply_stack(base, overlay)` rejects anything else, naming the
  path (`conventions`, terms, `asset_class`, `layers`, the strategy, the providers, a pricer's `mdp` or `request`, `registry`, `extends`, ...). `base_digest` ignores exactly those keys, so the
  same base has the same digest under every stack: equal digests are the proof that only the library changed (X1). The conventions block is therefore identical for every stack.
* `run_tieout(base, {name: [overlays]}, ...)` (CLI: `pricebt tieout base.yaml --stack ref=a.yaml --stack ql=b.yaml`) runs the base under each stack with the audit trail on, verifies the shared
  base, runs the **self-test** (the reference stack twice must differ by exactly zero at every level, X5a) and compares every stack with the reference.
* Levels: **L0** inputs (snapshot digests, resolved terms with dates first, conventions digest), **L1** marks (`pv`, `cash`, `financing` per position and point), **L2** measures (`dv01`,
  `gamma`, `rate`, ladder buckets), **L3** layers (adapter layers by name and the engine's baseline layers), **L4** portfolio (equity, cash, positions value, ledger counts and timestamps,
  summary statistics).
* Every quantity gets a **status**: `exact`, `noise` (within tolerance), `expected` (a declared definition difference, X6), `info`, `exceeds`, `input` (an L0 difference already explains it),
  `structure` (different keys or NaN pattern). The report lists the worst offenders with position and timestamp. A difference is `|a - b| / max(|reference|, floor)`.
* Tolerances are declared per level, quantity and asset class (`tieout.tolerances` in the base config or the `tolerances` argument); a declaration always beats a shipped default;
  the shipped defaults are the placeholders of spec Appendix B until a run measures the noise floor. **A tolerance is never widened to turn a run green without a written reason**:
  every value and every change is in `tasks/tolerance_ledger.yaml` and the change log. A declaration carries a `reason` (validated, printed in the report); an explicit argument replaces every
  declared key it covers.
* A quantity that one run lacks is never omitted: measures are compared over the union of the runs' columns, so a broken or missing ladder bucket is a `structure` failure (doubt cycle 3, T1).
* The report discloses what each stack bound (factory, wrap, every binding with its scale, offset, sign and literal arguments) because a `bind` can hide a unit conversion that `base_digest` ignores.

## Measured (real EOD fixtures, 2024-01-02 .. 2024-06-28, 124 points; `results/tieout/`, `tasks/tolerance_ledger.yaml`)

* Financed CT10 bond (`configs/suite/s05_ust_ct10_financed.yaml`): reference, rateslib and QuantLib agree to better than 1e-7 relative at every level (pv 4e-15, dv01 6e-12, gamma 2e-8,
  layers <= 1.4e-7, unexplained 8e-6, equity 2e-12) with the SHIPPED defaults, nothing declared.
* Swap carry (`s1_swap_carry_eod.yaml`): pv 1e-9 (rateslib) / 6e-9 (QuantLib), dv01 3e-8 / 6e-7, gamma 1e-6 / 2e-6, layers <= 1.5e-6, equity 1e-10; the shipped defaults pass for rateslib. QuantLib's
  `unexplained` is 0.99 times the reference's: its adapter takes the two directional derivatives by central differences of step h = 0.1 of the realised move, so its `delta` absorbs h^2 = 1% of the
  third-order remainder (verified with a known-answer control). That one difference is declared in the run's `tieout.tolerances` (`rel 1.5e-2`) with this reason.
* A tie-out of a RISK-SIZED strategy (the ladder hedge) also needs a declared position-size tolerance, because each stack sizes the hedge from its own measured ladder (ledger `swap_book`).

## Alternatives rejected

* Compare only final P&L: hides which level differs and why. The levels give the localisation the spec asks for (an input difference explains everything downstream).
* Let an overlay change conventions "to match": it would make two libraries agree by construction and prove nothing.

## Enforcement

`tests/test_stacks.py`, `tests/test_tieout.py`, `tests/test_tieout_selftest.py` (a flipped day count and a shifted calendar on ONE side are detected at L1 and located; the reference stack
matches hand-derived and golden answers to machine precision), `tests/test_quantlib_tieout.py`.
