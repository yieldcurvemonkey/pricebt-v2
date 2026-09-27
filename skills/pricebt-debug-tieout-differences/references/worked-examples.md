# Three worked examples from this repository

Each one is a real difference that was found, explained by a MECHANISM and pinned. Follow the same moves on your own report: read the level and the ratio, form ONE hypothesis, test it with ONE change, and check the hypothesis on a known answer first.

## 1. The QuantLib `unexplained` that is 0.99 x the reference's (the h-squared mechanism)

Sources: `tasks/tolerance_ledger.yaml` (`swap_book`, `suite_s1_swap_carry`), `tools/probe_unexplained_ratio.py`, `src/pricebt/contrib/quantlib/swap.py` (`_H`), `configs/suite/s1_swap_carry_eod.yaml` (`tieout.tolerances`).

* **Symptom.** Reference vs the QuantLib stack on the same base: L0 exact, L1 `pv` about 6e-9, L2 within the shipped tolerances, every other layer within 1.5e-6, L4 within 1e-8 (worst `trade_pv`, 6.2e-9; `tasks/tolerance_ledger.yaml`, `suite_s1_swap_carry`, the reference-vs-QuantLib column), BUT `L3.unexplained` fails the shipped `rel 1e-3` (measured 1.0e-2). One quantity, all positions.
* **Read the ratio, not the difference.** The per-unit `unexplained` of the two runs, aligned by `(position, ts)`, has ratio 0.9896 .. 0.9905: near-constant, so a mechanism, not noise. 0.99 = 1 - 0.1^2.
* **Hypothesis from the adapter.** The QuantLib adapter takes its two directional derivatives along the realised move `dz` by central differences of step `h = _H = 0.1` of that move: `first = (up - dn) / (2 * _H)`, `second = (up + dn - 2 * v_base) / (2 * _H * _H)`.
  A central difference has error `h^2 x (third-order term)`, so its `delta` absorbs `h^2 = 1%` of the third-order remainder and `unexplained` (interval P&L minus the layers) is `(1 - h^2)` times the true remainder that the reference computes with its exact directional derivative.
* **Known answer first.** The same probe on the reference against itself must give ratio exactly 1. Executed (about 20 s, `PYTHONPATH=src;tests`, with the `QuantLib` package installed: without it the probe stops at import with ``OptionalDependencyError: pricebt.contrib.quantlib needs the optional extra `QuantLib` (pip install QuantLib)``, and you read the output below instead):

  ```powershell
  python tools/probe_unexplained_ratio.py
  # _H = 0.1  1 - _H**2 = 0.99
  # reference vs reference (known answer: ratio 1): n=55 ratio min 1.000000 max 1.000000 mean 1.000000
  # quantlib / reference: n=55 ratio min 0.989611 max 0.990504 mean 0.990014
  ```
* **Disposition.** Not a bug: a documented finite-difference choice. Declared, with the mechanism as the reason, in the base's `tieout.tolerances` (`L3.unexplained: {rel: 1.5e-2, floor: 1.0, reason: ...}`, 1.5 x h^2, tight enough to still catch a wrong layer definition) and recorded in the ledger with the measured ratio.
  Everything else in that run passes with the SHIPPED tolerances. Alternatives: take exact directional derivatives (Richardson), or shrink `h`.
* **The moves to copy.** A constant ratio names a mechanism; find the constant in the adapter; prove it with a probe that has a known answer; declare the smallest tolerance that covers it, with the mechanism, the measured value and what the tolerance still catches.

## 2. The `dv01` of a started swap: bisection by one rebinding

Sources: `tasks/suite_reproduction.md` sections 2-4, `configs/adapters/rateslib_swap_recorded_dv01.yaml`, `tools/swap_suite_support.py:recorded_dv01`,
`tests/test_suite_swaps.py::test_with_the_recorded_dv01_definition_s2_reproduces_the_recorded_run_exactly_and_without_it_the_rebalances_differ`.

* **Symptom.** The migrated suite reproduced the recorded results bit for bit EXCEPT: the cost of every exit (a scaled cost of `0.1 x |dv01|`), the `measure_dv01` column of buy-and-hold runs, and, in one strategy (a steepener that rebalances on the book's net dv01 band), a different number of trades.
  Marks, cash, financing and every layer were bit-identical. One quantity (`dv01`), one situation (a swap that has started).
* **Hypothesis.** The definition of `dv01` after the start: the recorded runs used the analytic PV01 of the fixed leg (which keeps the accrued coupon, no longer sensitive to rates); the code used the sum of the delta ladder (the market dv01). At inception both agree.
  Independent evidence on one date: the analytic figure equals the recorded column to the last digit; the ladder sum equals an independent +-1bp parallel par-rate bump to 3e-7 (numbers in section 3 of the document).
* **Bisection: one hypothesis, one change.** An overlay that rebinds ONLY `dv01` to the recorded definition (`bind: {dv01: {target: {function: "tools.swap_suite_support:recorded_dv01"}, kwargs: {instrument: "@instrument", ctx: "@ctx"}}}`) made every run bit-identical to the recorded one (section 4 of the document).
* **Re-executed here as a tie-out of two stacks** (needs the `rateslib` package, else ``OptionalDependencyError: pricebt.contrib.rateslib needs the optional extra `rateslib` (pip install rateslib)``; about a minute for three months): `python -m pricebt tieout configs/suite/s11_buy_hold_10y.yaml --stack default=configs/adapters/rateslib_swap.yaml --stack recorded=configs/adapters/rateslib_swap_recorded_dv01.yaml --set "registry.allow=[support, tools.swap_suite_support]" --set backtest.grid.end=2022-03-31 --set backtest.progress.show=false`
  with `PYTHONPATH=src;tests;.`. Rows: L0, `L1.pv`, `L1.cash`, `L2.gamma`, `L2.rate`, `L3.delta`, `L3.unexplained`, `L4.equity` exact; `L2.dv01` EXCEEDS (a few percent, growing with age); `L3.tay_delta` and `L3.tay_unexplained` follow (the baseline reads dv01);
  `TIE-OUT FAILED: default_vs_recorded: L2.dv01; ...`. The picture of a DEFINITION difference: pricing exact, one measure off from the moment the instrument is no longer fresh.
* **Disposition.** The adapter's definition is the reference's (annuity before the start, ladder sum after; `docs/design/11-quantlib-conventions.md` section 5), which is the market dv01; the recorded files were the stale artefact (written ten minutes before the rule was last edited).
  A wrong choice here would have cost every risk-sized strategy: the trigger of one suite strategy read this number and produced different trades. Pinned by the test named above.
* **The moves to copy.** Decide which definition is right with an independent finite difference, then keep both definitions as two overlays that differ in ONE binding; the tie-out shows exactly which rows depend on the choice. Check WHEN the reference artefact was produced against when the rule changed before blaming the code.

## 3. The five negative controls of the worked example

`skills/pricebt-wire-external-library/example/config/mistakes/` (each overlay breaks exactly one thing; code for two of them in `example/acme_mistakes.py`; driven by `CONTROLS` in `tests/test_skills_example_acme.py`).
Every row was executed with `run_tieout(<acme base>, {..}, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))` against the reference stack; the correct wiring (`acme_swap.yaml`) is `exact`/`noise` on the same rows.

| overlay | the one mistake | level.quantity | status | located, size | what else moves | how to recognise it from the report alone |
|---|---|---|---|---|---|---|
| `acme_swap_no_percent.yaml` | `rate` binding without `scale: 100` | `L2.rate` | `exceeds` | `position=P000001, ts=2024-06-04`, max rel 0.99 (0.0403 against 4.03) | `L3.tay_delta`, `L3.tay_convexity`, `L3.tay_unexplained` (the baseline turns the rate move into bp); L0, L1, L4, `L2.dv01`, `L2.gamma` stay exact/noise | L1 exact, one L2 measure at ratio 0.01, only baseline layers follow |
| `acme_swap_wrong_sign.yaml` | `dv01` binding without `sign: -1` | `L2.dv01` | `exceeds` | `position=P000003, ts=2024-06-11`, max rel 2.0 | `L3.tay_delta`, `L3.tay_unexplained`; the rest exact/noise | ratio -1 at every row; the kit passes this one |
| `acme_swap_lower_key.yaml` | ladder reducer lower-cases one key (`30Y` -> `30y`) | `L2.delta_ladder.<every bucket>` | `structure` | note: "the measure column exists in reference only: it is missing or broken in the other run (57 values are NaN on one side only)" | `L4.stats` `n_errors` (62 recorded errors against 5); pv and dv01 exact/noise. Needs `delta_ladder` in `audit_measures` (Python API) | every bucket `structure`; the real message is in `result.errors`: `MeasureError: measure 'delta_ladder': key '30y' is not a canonical tenor (upper-case <int><D|W|M|Y>, e.g. '3M', '10Y')` |
| `acme_swap_wrong_calendar.yaml` | the wrap loads one holiday too many (2024-05-28) | `L0.resolved_terms` | `input` | `P000001 effective: 2024-05-29 vs 2024-05-30; P000001 maturity: 2034-05-29 vs 2034-05-30; ...` (dates first) | `L1.pv`, `L1.cash`, every `L2` row that exceeded, `L3.*`, `L4.equity`... are `input`; `L4.stats` and `L4.stats_traded` stay `exceeds` (cannot be attributed); snapshot digests and conventions exact | L0 `input` with dates first: the calendar or the spot lag |
| `acme_swap_no_holidays.yaml` | the wrap loads NO holidays (weekend-only) | the run STOPS | `MarketDataUnavailable` | `1 overnight fixings missing between 2024-05-28 and 2024-06-19; first 2024-06-19` (exit 2, no report) | with `--set backtest.on_error=record`: `L0.resolved_terms` `input` (`effective: 2024-05-29 vs 2024-05-28`) and `L1.marks_rows` `structure` (6 rows only in the reference), then most rows fail | a missing-fixing exception on a market holiday |

Reading the family as a triage tree: (1) did the run stop? (2) is L0 exact? if not, the input is the lead (a date first: calendar or lag); (3) at L2, is the ratio constant? (-1 sign, 0.01 or 100 units, `structure` on a vector measure: keys and errors);
(4) do only the `tay_*` layers follow? then the cause is a measure the baseline reads.
