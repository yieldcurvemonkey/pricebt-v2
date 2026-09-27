# Architecture decision records (refactor)

Each ADR records one decision **as built**, its context, the alternatives that were rejected and where it is enforced. The contract is
`docs/design/11-refactor-spec.md` (requirement ids in brackets); `docs/design/11-refactor-changelog.md` is the log of what changed and why. The ADRs grew from a pre-implementation plan (AD1..AD10), since removed: where the two differed, the ADR is what was built.

| ADR | Decision | Requirements |
|---|---|---|
| [001](001-bindings.md) | A binding is data executed by one function; the closed reference grammar; no name-based resolution | B1-B9, Z3 |
| [002](002-neutral-schemas.md) | Schemas are neutral data: terms, methods, measures, layers, a conventions vocabulary | S1-S7, T2, C5 |
| [003](003-instrument-spec-and-terms.md) | One instrument spec, trade terms per action, resolved terms recorded | T1-T7 |
| [004](004-snapshots-and-wrap.md) | Market data is plain-data snapshots; an adapter's `wrap` is the only place a library object is built | D1-D8 |
| [005](005-attribution.md) | Layer definitions, `unexplained`, the baseline decomposition and the audit trail | L1-L6, D5 |
| [006](006-stacks-and-tieout.md) | A stack is an overlay of factory/bind/wrap; the tie-out compares five levels with named statuses | X1-X6 |
| [007](007-reference-stack.md) | A dependency-free reference stack is the known answer of the tie-out and the oracle of the golden numbers | A4, X5 |
| [008](008-zero-dependence-guards.md) | Zero dependence is enforced at import, name, shape and semantic level by guards that each have a twin | Z1-Z6, G-1..G-5 |
| [009](009-facade.md) | The gs_quant facade is terms plus a session-registered spec; conventions are never in core | F-1..F-6, C1-C7 |
| [010](010-dead-ends.md) | What was tried and dropped | - |
