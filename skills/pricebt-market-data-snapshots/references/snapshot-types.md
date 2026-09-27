# Snapshot types, fields, validation messages and the digest

Source of truth: `src/pricebt/snapshot.py` (read it, it is short). Everything below was read from it and then executed (the digest claims are asserted, the messages are
copied from a run). A snapshot is PLAIN DATA: frozen dataclasses, pickle-safe, no library object, no convention (spot lag, day counts, calendar NAMES that mean something to a
library, yield rules are `conventions` of the instrument, never a snapshot field).

## Vocabulary constants (data, not code)

| constant | value | meaning |
|---|---|---|
| `snapshot.VALUE_KINDS` | `("discount_factor",)` | the only curve value kind |
| `snapshot.INTERPOLATIONS` | `("log_linear_df",)` | ln(DF) linear in calendar time between nodes (a constant instantaneous forward per segment), extended beyond the last node with the last segment's forward; undefined before the reference date |
| `snapshot.FIXING_UNITS` | `("percent", "decimal")` | unit of a fixings series: always stated, never implied |
| `snapshot.PRICE_CONVENTIONS` | `("market", "unspecified")` | `market`: accrued interest is 0 on a coupon date |

There is exactly ONE interpolation tag. A library curve that is not log-linear in the discount factor between nodes cannot be described by a snapshot: export dense nodes and
accept (and declare, with a `reason`) the between-node difference. See `references/library-as-data-source.md`, step 3.

## The types

| type | fields (in order) | rules enforced at construction (`ConfigError`, code `SNAPSHOT`) |
|---|---|---|
| `CurveSnapshot` | `name`, `reference_date`, `node_dates`, `values`, `value_kind="discount_factor"`, `interpolation="log_linear_df"`, `provenance={}` | name non-empty; kind and tag in the vocabulary; at least 2 nodes and one value per node; node dates strictly ascending; **first node == reference date**; every DF finite and > 0; **first DF == 1.0** (1e-12). A rising DF is allowed (real data has them). |
| `FixingsSeries` | `name`, `dates`, `values`, `unit="percent"`, `proxied=()`, `provenance={}` | name non-empty; unit in `FIXING_UNITS`; same number of dates and values; dates strictly ascending (sorted, unique); values finite; every `proxied` date is in `dates`. `proxied` = dates whose value a provider policy INVENTED (not published). |
| `CalendarData` | `name`, `holidays=()`, `weekmask="Mon Tue Wed Thu Fri"`, `provenance={}` | name non-empty; holidays sorted and de-duplicated; `weekmask` re-spelled canonically (`timeutil.weekmask_names`), so `"Mon,Tue,Wed,Thu,Fri"` and `"Mon Tue Wed Thu Fri"` digest alike |
| `Quote` | `clean=None`, `ytm=None`, `bid=None`, `offer=None` | at least one of `clean` / `ytm`; all given values finite. Prices are per 100 face, `ytm` is PERCENT. |
| `Security` | `id`, `coupon`, `issue_date`, `maturity_date`, `label=""` | coupon finite and PERCENT; maturity after issue |
| `QuoteSet` | `reference_date`, `quotes`, `securities`, `aliases={}`, `price_convention="unspecified"`, `provenance={}` | convention in the vocabulary; every quoted id has a `Security`; every alias points at a known security. Aliases (`CT10`, `O10`, ...) are resolved BY THE PROVIDER as of `reference_date`. |
| `MarketSnapshot` | `ts`, `reference_date`, `curves={}`, `fixings={}`, `quotes=None`, `calendars={}`, `provenance={}` | **`ts` tz-aware**; each mapping key equals its value's own `name`; **every curve anchored at the snapshot's reference date**; **fixings strictly before the reference date** (a fixing dated on or after it does not exist yet); quotes as of the same reference date |
| `SnapshotPricer` | `SnapshotPricer(snapshot, *, ts=None)` | attributes `snapshot`, `ts` (the request time if given, else the snapshot stamp), `reference_date`, `digest`; `describe()`; `LOOKUPS = ()` (data only: an adapter's `wrap` adds the lookups) |

Mapping fields (`curves`, `fixings`, `calendars`, `quotes.*`) are COPIED into plain dicts at construction, which are mutable: do not mutate them, the digest was already taken.
`FrozenInstanceError: cannot assign to field 'values'` is what an attempt on a field gives.

## Real messages (copied from a run)

```
[SNAPSHOT] interpolation 'cubic' is not a snapshot tag; known: ['log_linear_df'] (tags are named by their maths, not by a library)
[SNAPSHOT] value_kind 'zero_rate' is not supported; known: ['discount_factor']
[SNAPSHOT] curve 'c': the first node 2024-01-04 must be the reference date 2024-01-03
[SNAPSHOT] curve 'c': the anchor value must be 1.0, got 0.99
[SNAPSHOT] curve 'c': node dates must be strictly ascending
[SNAPSHOT] curve 'c': discount factors must be positive
[SNAPSHOT] curve value must be finite, got nan
[SNAPSHOT] fixings unit 'bp' must be one of ['percent', 'decimal']
[SNAPSHOT] fixings 'f': dates must be strictly ascending (sorted, unique)
[SNAPSHOT] a snapshot stamp must be tz-aware
[SNAPSHOT] curve 'c' is anchored at 2024-01-03, not the snapshot reference date 2024-01-04
[SNAPSHOT] curve key 'x' must equal its own name 'c'
[SNAPSHOT] fixings 'f' run to 2024-01-03: only fixings strictly before the reference date 2024-01-03 exist at the snapshot
```

## The digest (`MarketSnapshot.digest()`, `SnapshotPricer.digest`)

sha256 over a canonical byte encoding: big-endian IEEE-754 doubles (a one-ulp change is visible), day ordinals, UTC nanoseconds, length-prefixed strings, names sorted. Independent
of `hash()` randomisation, mapping order and provenance. Cost: about a quarter of a millisecond for 20 nodes and 640 fixings (measured; computed once when the `SnapshotPricer` is built).

| IN the digest (a change gives another digest) | NOT in the digest (a change gives the same digest) |
|---|---|
| the snapshot stamp `ts` (as UTC ns) and `reference_date` | every `provenance` (snapshot, curve, fixings, calendar, quote set) |
| per curve: name, reference date, value kind, interpolation tag, every node date and value | the REQUEST time given to `SnapshotPricer(snapshot, ts=...)` |
| per fixings series: name, unit, every date and value, the `proxied` dates | `Security.label` |
| per calendar: name, weekmask, every holiday | mapping insertion order; the spelling of a weekmask |
| quotes: reference date, price convention, each quote's `clean/ytm/bid/offer` (absent is not zero), each security's coupon/issue/maturity, each alias; "no quotes" is its own token | |

Consequences for a provider: put everything that changes a price into the snapshot proper, and everything that only explains where it came from (source system, curve id in the
library, build time, library version) into `provenance`. Put nothing volatile (wall-clock time, a request id) into a field that is in the digest, or two fetches of identical data
digest differently and the tie-out's L0 row fails.

## Where the digest is read

`MarketData.pricer` appends `{ts, role, digest, stamp}` to `MarketData.audit` (when a run has `backtest.audit` on) BEFORE the role's `wrap` is applied: the digest is the snapshot's, whichever
library consumes it. `tieout.compare._level0` compares the SETS `(str(ts), role, digest)` of the two runs. If every digest is missing it writes one `info` row instead:

```
L0 snapshot_digests info | no digests recorded: the provider's pricers carry none, so identical inputs are not proven
```

and the tie-out still reports `passed: True` (executed). So a provider MUST return a `SnapshotPricer` (or a pricer with a `digest` attribute), or L0 proves nothing.
