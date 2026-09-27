# ADR 005: Layers, `unexplained`, the baseline decomposition and the audit trail

Status: accepted, built (`engine/engine.py`, `contracts/schemas/*.yaml`, `results/reconcile.py`, `testing/layer_conformance.py`). Requirements L1-L6, D5, X2.

## Decision

* **Layers are schema names bound like any method** (`carry`, `roll`, `delta`, `convexity`, each with an id and a version; the manifest lists `<id>@<version>` per instrument, an
  extension layer is `<name>@ext`). The engine owns `unexplained`, the residual of the interval P&L.
* Definitions (folded, following the rateslib adapter and the golden case; both adapters and the reference stack implement them):
  `carry = X_fwd - V0` (the t0 world's flows valued at t1), `roll = X_roll_act - X_fwd` (curve unchanged in tenor space anchored at t1, including realised fixings),
  `delta = (V_base + cash - X_roll_act) + g.dz` (directional derivative along the realised zero-rate move) and `convexity = 1/2 dz'H dz`.
  Golden: seasoned 3y payer 50mm carry -561.82, roll 12,159.98 (raw; 1,532.07 with the realised fixings folded in), delta 70,190.64 (raw; 70,302.17 folded), convexity -50.23, residual 0.02, total 71,222.20.
* **Baseline decomposition** (`attribution.baseline: true`), engine-computed and library-independent: `tay_delta = dv01(start) x d rate_bp`, `tay_convexity = 1/2 gamma(start) x d rate_bp^2`,
  `tay_unexplained` = interval P&L minus both. `reconcile()` checks the adapter layers and the baseline layers separately.
* **Audit trail** (`EngineSettings.audit`, off by default and observation-only): the snapshot digest per distinct fetch (recorded before the wrap), per position and point the pv, cash,
  financing and requested per-unit measures, and per position and flush the layer amounts including `unexplained`; persisted as `audit_<name>.parquet`.
* **Layer conformance kit** (`testing/layer_conformance.py`), run for every adapter: a parallel shock at ONE date, small and large, must equal `delta + convexity` by full revaluation
  (and carry and roll must be zero); a payer and its mirror have equal and opposite value, dv01, gamma and every layer; across a payment date the value drop equals the cash booked
  and the market-move layers are zero; the unexplained share is reported and bounded. A small `unexplained` alone proves the layers complete, not right.

## Consequences

`unexplained` is a residual by construction, so no identity `layers + unexplained == P&L` is tested (it cannot fail); the kit bounds the share instead.

## Enforcement

`tests/test_engine_baseline.py`, `tests/test_engine_audit.py`, `tests/test_results_reconcile.py`, `tests/test_refstack_golden.py`, `tests/test_layer_conformance*.py` (the kit is itself checked on sabotaged specs: one broken
layer per check, plus 18 mutations of the kit).
