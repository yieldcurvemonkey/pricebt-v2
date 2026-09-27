# ADR 003: One instrument spec, terms per action

Status: accepted, built (`src/pricebt/contracts/spec.py`, `strategy/actions.py`, `config/loader.py`). Requirements T1-T7, B4-B6.

## Context

A strategy trading pay and receive at several tenors needed one pricable per direction and tenor (`recv10y`, `pay10y`, `recv5y`, ...), each naming a library factory.

## Decision

* An **instrument spec** is defined once: asset class, `factory`, `conventions`, `bind`, pricer role. It is a `Kit` (factory + the adapter's default bindings + the class whose methods the
  bindings name) or a plain callable; a user `bind:` overrides the kit's block name by name.
* **Terms** are supplied by the action: `{instrument: usd_sofr_ois, terms: {side: receive, maturity: 10Y, notional: 1e7, fixed_rate: par}}`. `terms` may hold `@signal.<n>`,
  `@param.<n>`, `@trigger.scaling`; a ladder-hedge action sets `side` per leg.
* `factory(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)`: the library resolves dates and `par` at the fill pricer and returns what it resolved. pricebt
  normalises `side` to a direction sign through the schema's alias map, passes both, and records the resolved terms on the position and as trade-ledger columns
  (`instrument`, `term_<name>`).
* The only thing that imports an adapter is a dotted path in config (`factory`, `wrap`, `registry.import`), under the `registry.allow` allow-list. There are no short adapter names in core
  registries and no text sniffing of the config (guard GT-Z3c: a config that merely says `rl_swap` in a comment imports nothing).

## Alternatives rejected

* Terms as constructor kwargs of a per-trade pricable object: brings back one spec per direction and tenor.
* Resolving `par` in core: it needs a library's day counts and calendars (a convention in core).

## Enforcement

`tests/test_spec.py`, `tests/test_spec_review.py` (mutation-checked), `tests/test_strategy_actions.py` (terms, ladder-hedge side per leg), `tests/test_engine.py` (ledger term columns), the migrated swap suite (one spec `usd_sofr_ois`).
