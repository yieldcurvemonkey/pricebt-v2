# The provider protocol, the time mapping, the look-ahead guard, offline fixtures and the models to copy

All outputs below were produced by running the snippets (project root, `PYTHONPATH=src;tests`; in this checkout on Windows with `C:/Users/chris/anaconda3/envs/stir/python.exe`).

## 1. The protocol (`src/pricebt/pricer.py`)

```python
class MarketDataProvider(Protocol):
    def get_pricer(self, ts: pd.Timestamp, request: Optional[Mapping[str, Any]] = None) -> Pricer: ...
```

That is the whole Protocol (`isinstance(x, MarketDataProvider)` checks `get_pricer` only). One more method is OPTIONAL and NOT part of it: `def available_timestamps(self, start: pd.Timestamp,
end: pd.Timestamp) -> Sequence[pd.Timestamp]`, which `MarketData.available_timestamps` reads with `getattr` and `backtest.grid: {from_mdp: <role>}` needs.

* `ts` is a tz-aware `pd.Timestamp`. A provider must not mutate `request`; it raises `MarketDataUnavailable` (or `StaleSnapshot`, its subclass) on a miss and never returns an empty
  substitute. `MarketData.pricer` turns a `None` into `market data unavailable at <ts>: provider <Class> returned None`.
* What it returns: a `SnapshotPricer(snapshot)` (attributes `ts`, `reference_date`, `digest`). Anything else has no digest and the tie-out cannot prove identical inputs
  (`references/snapshot-types.md`, last section).
* `available_timestamps` is optional: without it `MarketData.available_timestamps` raises `NotImplementedError: <Class> cannot list its timestamps` and a config that builds its
  grid `from_mdp` fails. `loader.py` (`_Builder.grid`) filters the provider's stamps by the time context's business-day calendar.
* `request` is the role's request template (`market.pricers.<role>.request`; or `market.mdps.<n>.request` when an instrument names the mdp directly as its role) with the per-call
  request merged over it (`MarketData.pricer(ts, role, request)`); the memo key is `(role, ts, frozen request)`, so two requests for one snapshot fetch twice and wrap once. A provider
  that takes no request keys should refuse them, as `support.common.check_request` does: `[REQUEST] unsupported request keys ['x']; this provider accepts []`.

The smallest provider (executed; `isinstance(x, MarketDataProvider)` is True for it and for `FunctionMDP`):

```python
import datetime as dt, pandas as pd
from pricebt.snapshot import CurveSnapshot, MarketSnapshot, SnapshotPricer

class OneDay:
    def get_pricer(self, ts, request=None):
        ref = pd.Timestamp(ts).date()
        c = CurveSnapshot("c", ref, (ref, ref + dt.timedelta(days=365)), (1.0, 0.96))
        return SnapshotPricer(MarketSnapshot(pd.Timestamp(ts), ref, {"c": c}))
```

`pricebt.pricer.FunctionMDP(fn, available=None)` wraps a plain `fn(ts, request) -> Pricer` (this is what `tests/test_market_wrap.py` uses).

## 2. MarketData: memo, look-ahead guard, audit (`src/pricebt/market.py`)

```
MarketData.pricer(ts, role="primary", request=None):
  ts > clock.now                                   -> LookAheadError
  memo hit on (role, ts.value, freeze(request))    -> the same object (n_hits += 1)
  else provider.get_pricer(ts, request)            (n_fetches += 1)
  audit row {ts, role, digest, stamp}              (when auditing) BEFORE the wrap
  snapshot stamped after ts                        -> LookAheadError
  wrap applied ONCE per (role, digest, stamp)      (see the skill pricebt-wrap-and-pricer)
```

Executed messages:

```
LookAheadError: pricer requested for 2024-01-11 17:00:00-05:00 but the clock is at 2024-01-10 17:00:00-05:00
LookAheadError: provider returned a snapshot stamped 2024-01-09 17:00:00-05:00 for a request at 2024-01-05 17:00:00-05:00
MarketDataUnavailable: market data unavailable at 2024-01-05 17:00:00-05:00: provider FunctionMDP returned None
ConfigError: [ROLE] unknown pricer role 'nope'; known: ['primary']
```

The second one is the channel a live provider trips when it returns "the latest curve" for a past request: the stamp of what you return must never exceed the request time. A provider
that serves history from a library that only knows "now" cannot be used live: record first (skill section "When the external library is also the market-data source").

## 3. The time mapping (`TimeMapping`, `SnapshotIndex`)

`TimeMapping(mode="asof", max_staleness=5min, eod_visible_at=None, unbounded_ok=False, tz="America/New_York")`. `SnapshotIndex(stamps).select(ts, mapping)` is the ONE selection routine the
frame-backed providers share (`tests/support/common.py: Selector` wraps it and adds a reason for every miss). Executed on three end-of-day snapshots stamped 17:00 New York on Mon
2024-06-10, Tue 06-11 and Thu 06-13:

| mode and request | result |
|---|---|
| `asof`, 12h, Tue 18:00 | Tue's snapshot |
| `asof`, 12h, Wed 09:00 and Wed 17:00 | `StaleSnapshot: ... newest visible snapshot 2024-06-11 17:00:00-04:00 older than 0 days 12:00:00` |
| `asof`, 2 days, Wed 17:00 | Tue's snapshot |
| `asof`, `max_staleness=None, unbounded_ok=True`, Wed 17:00 | Tue's snapshot (any age) |
| `asof`, request before the first stamp | `None` (the `Selector` turns it into `MarketDataUnavailable: ...: before_first_snapshot`) |
| `exact`, Tue 17:00 / Tue 17:01 | Tue's snapshot / `None` |
| `date`, Tue 20:00 / Wed 12:00 | Tue's snapshot (latest stamp on the same local date) / `None` (no Wednesday row) |
| `date` + `eod_visible_at: 18:00`, rows stamped 00:00, request Tue 09:00 / Tue 18:00 | `None` / Tue's row |

* `max_staleness` is measured from the STAMP, not from the visible instant. On a market holiday an `asof` mapping with 12h serves nothing (`stale: newest visible snapshot 2024-05-24 17:00:00-04:00 is 3 days 00:00:00 old (max_staleness 0 days 12:00:00)`); with `max_staleness: 3D` it serves the previous close.
* `eod_visible_at` can only DELAY a row (a stamp becomes visible at `max(stamp, that time of day on the stamp's local date)`), never expose it early. It is the anti-look-ahead
  device for end-of-day data stamped at midnight of its date.
* Errors at construction: `[TIME] unknown time mode 'nearest'` and `[TIME] asof mapping needs max_staleness (or unbounded_ok: true)`. The default `max_staleness` (5 minutes) suits
  minute data; an EOD provider needs an explicit value (`support.common.DEFAULT_STALENESS`: 12h for `eod`, 5min for `minute`).

### How a config's `time:` block reaches the provider (`_Builder.mdp`, executed)

```yaml
market:
  mdps:
    sofr: {type: "support.curves:CurveStore", kwargs: {asset: ...}, time: {mode: asof, max_staleness: 12h}}
```

| the provider's `__init__` has | what it receives |
|---|---|
| a parameter named `time` | `time=<the dict as written>` (the provider parses it; `support.common.time_mapping` does) |
| a parameter named `mapping`, or `**kwargs` | `mapping=TimeMapping(...)` built by the loader |
| neither | `ConfigError [CFG-UNKNOWN-KEY] market.mdps.m.time: mdsnap_prov:TakesNothing does not accept a time mapping` |
| a `mapping` parameter or `**kwargs`, and `asof` without `max_staleness` | `ConfigError [CFG-REQUIRED] market.mdps.m.time: an asof time mapping needs max_staleness (or unbounded_ok: true)` (a provider with a `time` parameter never gets this from the loader: it parses the dict itself, `CurveStore` defaults to 12h) |

A provider with no `time:` block in its config gets nothing (a live provider has no time mapping: it is asked for `ts` and answers for `ts`).

## 4. Immutability

Snapshots are frozen and pickle-safe; their mapping fields are plain dicts you must not mutate. The wrapped pricer is a different object (immutable by convention and by
`__setattr__` in the shipped adapters, see `pricebt-wrap-and-pricer`). A provider may cache the `SnapshotPricer` it built per stamp (`support.common.CachedProvider` keeps a bounded
LRU keyed by the stamp); it must never hand out a pricer it will later change. Providers pickle without their caches (`CachedProvider.__getstate__`).

## 5. Models to copy (all library-free)

| file | what it is | copy it for |
|---|---|---|
| `src/pricebt/testing/synthetic.py` | `SyntheticMarket`: deterministic Nelson-Siegel curves, seeded fixings with a publication rule, a bond panel quoted in yield; `flat_swap_world(calendar, ...)` and `flat_bond_world(security, ...)` return `world(ref, shock_bp) -> MarketSnapshot` used by the layer conformance kit | a provider with `get_pricer` + `available_timestamps`; the smallest complete snapshots (curve + fixings + calendar + quotes). Default calendar is weekends only: pass a `pricebt.timeutil.Calendar` or `CalendarData` for holidays |
| `tests/support/common.py` | `CachedProvider`, `Selector` (asof/exact/date + reasons), `FixingsHistory` (publication policy: a fixing dated d is public `after_bdays` business days later at `at` local time; the newest needed one is proxied and listed in `proxied`, or `strict` raises), `calendar_data`, `load_fixings` (unit-checked: `DataQualityError: pct.parquet: 167 values outside (0, 0.2) for unit 'decimal'`), `default_fixtures_root` (`$PRICEBT_DATA`, then `$PRICEBT_FIXTURES`, then `data/fixtures`; `FixturesMissing` when absent) | a provider over a recorded store |
| `tests/support/curves.py` | `CurveStore(asset, root=..., time=..., profile=..., fixings=..., fixings_unit=..., curve_id="sofr")`: the recorded-store reader (row layout in `references/library-as-data-source.md`); index-time freshness rule drops holiday rows that carry the previous day's nodes and duplicate stamps (`CurveStore.dropped`) | THE reader for a recorded curve store |
| `tests/support/tiny.py` | writers of tiny fixture trees: `write_calendars`, `write_fixings`, `curve_row`, `write_curve_store`, `nodes`, `NYC_HOLIDAYS` | exporters (same layout) and rule tests with known answers |
| `tests/support/known.py` | known answers of the fixtures and `needs_fixtures` (skip with a reason when `data/fixtures` is absent) | offline tests that must not fail on a machine without the data |
| `tests/support/ust_common.py`, `ust_eod.py`, `ust_minute.py` | bond quote panels (`QuoteSet` with on-the-run aliases resolved as of the reference date) | a bond source |
| `tests/quantlib_support/snapshots.py` | builders (`snapshot`, `quote_set`, `synthetic_nodes`, `fixings_series`, `template`) that need no fixtures | adapter tests over in-memory snapshots |

## 6. Offline fixtures

`data/fixtures/` (generated by a maintainer tool, described by `MANIFEST.json`, checksummed in `files.sha256`, third-party licence note in `README.txt`) holds
`curves/asset=<A>/date=<YYYY-MM-DD>/*.parquet`, `fixings/USD-SOFR-1D.parquet` (columns `date`, `rate` DECIMAL), `calendars/{nyc,fed,us_govt_bond}.json`, `ust/*.parquet`.
`data/.gitignore` is `*` / `!.gitignore`: nothing under `data/` is ever committed, so a store exported from a proprietary library stays out of version control by default.
Tests that read it carry `pytest.mark.fixtures` (registered in `pytest.ini`) and skip with a reason when it is absent. A rule test should prefer a tiny tree
(`tiny.write_curve_store(tmp_path, ...)`, a few milliseconds) to the real fixtures.
