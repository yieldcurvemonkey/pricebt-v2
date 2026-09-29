"""Toy pricing library for pricebt v2 tests (IMPLEMENTATION_PLAN.md P1.5). Test-only fixture,
never shipped: nothing under src/pricebt imports this package.

Modules: `rates` (curves, swaps), `swaption` (Bachelier swaption), `irrisk` (the IR measure
contract for swaps, on top of `rates`; IR_RISK_DESIGN section 6) and `bond` (a tiny bond master
and pricer).

Import it as `toylib.rates` / `toylib.swaption` / ..., never `tests.toylib.*`. Every test runner puts
`tests/` on `sys.path` (PYTHONPATH="src;tests"), which makes this directory a top-level package
`toylib` -- so a toy asset config's `imports: | import toylib.rates as tr` and a test's
`import toylib.rates as tr` load the SAME module object, and its recorders (EVAL_COUNTS, CSA_SEEN,
HOLES) are visible from both (IMPLEMENTATION_PLAN.md section 0.6).
"""
