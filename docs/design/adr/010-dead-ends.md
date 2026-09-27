# ADR 010: Dead ends (what was tried and dropped)

* **A "compat" alias table for method names.** Any list of library names in core is a library's vocabulary in core. Dropped for bindings (ADR 001).
* **`layers + unexplained == P&L` as a kit identity.** `unexplained` is defined as the residual, so the identity cannot fail; it was removed as vacuous and replaced by a bound on the unexplained share
  (ADR 005).
* **A Python factory for derived signals with named inputs** (shipped by the old stores as a workaround for a supposed core issue). Plain YAML `derived` signals with named inputs drive triggers; pinned by
  `tests/test_config_loader.py::test_a_derived_signal_with_a_named_input_can_drive_a_trigger`.
* **`np.where` over a masked domain.** `np.where(mask, f(x), y)` evaluates `f` on every element; a discount factor requested before a curve's reference date raised on a payment date. The reference stack
  now indexes the future payments only (found by the layer kit; change log section 11).
* **A shipped default instrument spec for the reference stack.** It needed a calendar name (a banned token in core); the name is the snapshot provider's data, so the caller supplies it.
* **A convention inside the layer kit's flat worlds.** The ACT/365 time axis of the flat-curve world factories moved to `testing/synthetic.py` (already labelled and allow-listed as a generator definition) instead
  of adding the kit to the Z4 allow-list.
* **Dead registries `LADDERS` and `PRICABLES`.** Only the old rateslib tests used them; a ladder is a bound measure, not a registered function.
* **Keeping the old stores and an old-versus-new equivalence run in the suite.** The equivalence was measured once, before deletion, and recorded (change log section 7).
* **Swaptions.** Out of scope (Q15): the schema, factory, configs, tests and the straddle suite were removed; `IRSwaption` raises `NotSupportedError`.
