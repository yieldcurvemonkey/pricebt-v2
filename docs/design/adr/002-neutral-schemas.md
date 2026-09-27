# ADR 002: Schemas are neutral data

Status: accepted, built (`src/pricebt/contracts/schema.py`, `schemas/{generic,swap,bond}.yaml`). Requirements S1-S7, T2, C5.

## Context

The previous schemas listed rateslib method names, keyword arguments and return types (`analytic_delta`, `curves=`, a `DataFrame` from `delta()`), so "the schema"
and "rateslib" were the same thing.

## Decision

A schema is a YAML data file that says **what pricebt expects**, never **how a library provides it**. Per asset class:

* `terms`: the per-trade parameters, with the direction sign of a `side` value (`pay`/`receive`, `buy`/`sell`).
* `methods` (the value contract), `measures` (`dv01`, `gamma`, `rate`, `delta_ladder`, ...), `layers` (P&L decomposition rows with an `id` and a `version`, such as `swap.carry@1`).
* `conventions`: a **vocabulary** (keys, types, allowed tokens) validated by `AssetSchema.check_conventions`. pricebt applies no convention; an adapter checks its supported subset and says
  what it does not implement.
* optional `pricer_capabilities` (lookups a pricer may offer).

Every entry carries kind, neutral argument names, return type, unit, sign, `required`, synonyms and a `doc`. Prose lives only under `doc`. Unknown keys are errors;
`extends:` merges a parent entry by entry. The resolved schema must give every method, measure and layer a return type and a unit.

Two semantic decisions:

* `dv01`, `gamma` and `delta_ladder` are **dollar deltas** (S7: currency per +1 basis point), not library-specific "risk" units.
* `delta_ladder` is a plain `dict[str, float]` keyed by tenor strings (`<int><D|W|M|Y>`). No pandas type crosses the boundary; a library that returns another shape converts it with a reducer in its binding.

## Alternatives rejected

* One schema per library: defeats the purpose (a tie-out needs one contract two libraries satisfy).
* Putting conventions in code with per-library defaults: hides them (C5). They are data, hashed into the manifest (`conventions_digest`), and identical across stacks in a tie-out.

## Enforcement

`tests/test_schema_v1.py`, `tests/test_schema_review.py`, `tests/test_conventions_vocab.py` (22 tests), guard GT-Z2b (no banned token in schema keys or non-`doc` values).
