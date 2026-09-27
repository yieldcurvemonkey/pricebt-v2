# Reading order of the repository (what to read, why, and what to skip)

Read in this order. Stop reading a file as soon as you can answer the question in its "you can now" column; the code wins over prose wherever they disagree (`docs/DESIGN.md` says so).

| # | read | you can now |
|---|---|---|
| 1 | `AGENTS.md`, then `skills/README.md` | know where the playbook is and how to run things (`PYTHONPATH=src;tests`, single test files only: the whole suite takes about twelve minutes) |
| 2 | `docs/DESIGN.md` sections 1-4 and 10-11 | state the one rule and the four ideas; know the five tie-out levels |
| 3 | `docs/design/adr/README.md`, then ADR 001 (bindings), 002 (schemas), 003 (spec and terms), 004 (snapshots and wrap), 006 (stacks and tie-out), 005 (layers), 007 (reference stack), 008 (guards). ADR 009 (facade) and 010 (dead ends) when the facade or a "why not X" question comes up | know each decision, its rejected alternatives and where it is enforced |
| 4 | `src/pricebt/contracts/schemas/swap.yaml`, `bond.yaml`, `generic.yaml` | know every name, unit, sign and return type (or run the generator in `pricebt-map-library-to-schemas`) |
| 5 | `docs/design/11-refactor-spec.md` sections 2 (Z1-Z6), 6.2 (B1-B8, S1-S7), 6.3 (T1-T7), 6.4 (D1-D8), 6.5 (L1-L7; L7 is the out-of-scope swaption gap), 6.7 (A1-A6), 6.8 (X1-X6), 9 (tests) | quote a requirement id; know what MUST and what MAY. Skip 3, 7, 13, 14 and the appendices unless you need a number |
| 6 | code: `contracts/binding.py`, `contracts/spec.py` (`Kit`, `build_spec`, `TradeTemplate`), `contracts/evaluate.py`, `snapshot.py`, `market.py` (`MarketData._wrap`), `pricable.py` (`Valuation`, `MarkContext`) | predict what a config does |
| 7 | the models: `src/pricebt/testing/refstack.py` (library-free adapter), `src/pricebt/contrib/quantlib/` and `src/pricebt/contrib/rateslib/` (real adapters: `_compat.py`, `wrap.py`, `conventions.py`, `swap.py`, `bond.py`), `src/pricebt/testing/layer_conformance.py` | see every file an adapter has and what goes in it |
| 8 | `skills/pricebt-wire-external-library/example/README.md` and `example/acme_adapter/` | see a foreign library wired end to end, with its negative controls |
| 9 | `docs/design/11-quantlib-conventions.md` (headings first) | see how a library's conventions are PROBED on known answers and recorded, each supported value verified, each unsupported one with its reason |
| 10 | `tests/guards/` (`banned.yaml`, `scan.py`, `blocker.py`, `partition.py`, `zero_toys.py`) | know exactly what will fail if you put something in the wrong place |
| 11 | when needed: `docs/design/11-refactor-changelog.md` (why something changed; measured noise floors), `tasks/tolerance_ledger.yaml` (every widened tolerance and its reason), `docs/guides/backtesting.md` (the gs_quant-style facade), `docs/research/` (evidence notes cited by code) | |

Skip: `results/`, `results_new/`, `notebooks/`, `configs/suite/` until you run a real-data suite; `docs/guides/gs-quant-deviations.md` unless you use the facade.
