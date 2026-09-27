# ADR 008: Zero dependence is enforced at four levels, each guard has a twin

Status: accepted, built (`tests/guards/`). Requirements Z1-Z6, G-1..G-5, SC1-SC3.

## Decision

Core (everything under `src/pricebt` except `contrib/`) must not depend on any pricing library or vendor at:

| level | guard | how |
|---|---|---|
| import (Z1) | GT-Z1a, GT-Z1, GT-Z1b | every core module imports in a fresh interpreter under a `sys.meta_path` **blocker** that raises for banned roots (it sees imports hidden in functions and `try`/`except`); an AST import scan per file; the full `core` suite runs under the blocker |
| name (Z2) | GT-Z2a/b/c | banned tokens in identifiers and non-docstring literals, in schema keys and non-`doc` values, in shipped core configs |
| shape and semantics (Z3) | GT-Z3a/b/c | a toy with method names crossing the schema's, decoys that raise if reached by name and arguments named `zzz`, through an engine backtest (its unbound twin is rejected); a binding receives exactly the declared kwargs; a config that merely names an adapter imports none |
| convention (Z4) | GT-Z4 | no day-count, divisor, compounding or settlement convention in core outside an allow-list of labelled files with a reason each (`backtests/*` gs-compat, `accrual.py`, `testing/{toys,synthetic,refstack}.py`) |
| ARBS (Z5) | GT-Z5a/b | no ARBS package imported, no module or path named `arbs`, no ARBS token in `src/` code or prose |

* **Non-vacuity twins** (`test_twins.py`): each scan runs on a temporary tree with one planted violation and must find it. A guard that scans nothing, or a scan that cannot fail, is a defect.
* The banned tokens live in ONE data file (`banned.yaml`); adding is easy, removing needs a spec change; an allow-list entry needs a stated reason.
* **Partition** (G-5, `partition.py`): every test carries `core`, `adapter_rateslib`, `adapter_quantlib`, `fixtures` or `live_arbs`; an unmarked test, or a `core` test that also needs something, is an error in
  the collection hook. `core` means "passes with the libraries unavailable" and is proven by running it under the blocker.
* One guard case per file, so the red cases were the refactor's worklist.
* Mutation discipline: `tools/mutcheck.py <spec.json>` applies one textual mutation, expects the named tests to fail and restores the file; survivors are killed by a sharper test or recorded as equivalent
  (change log section 6).

## Rejected

* Checking `sys.modules` after the fact: an import inside a function or a `try` is invisible to it.
* Scanning only for library names: the failure that matters is a library's SHAPE (a `DataFrame` ladder, a keyword named like a library's) and its semantics (a same-name call), hence Z3.
