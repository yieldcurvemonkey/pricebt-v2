---
name: pricebt-market-data-snapshots
description: "Use when pricebt must get market data from a new source (a bank data service, an in-house library that also serves curves, recorded files), to write a provider, pick a time mapping, build snapshots, or make every stack of a tie-out consume identical inputs. Covers the provider protocol, every snapshot type, the digest, the look-ahead guard and the record-once pattern."
---

# pricebt-market-data-snapshots

## Purpose

Market data reaches pricebt as plain, immutable **snapshots** emitted by a **provider**; no pricing library is involved until the adapter's `wrap` turns a snapshot into that library's
objects. This skill writes the provider side: what to emit, at which time, how to prove two runs consumed the same data, and what to do when the library you are wiring in is ALSO the
data source.

## Start here: which of three cases is your library?

| your library | who serves the market data | what you write | worked example |
|---|---|---|---|
| A. a platform that ALSO serves curves, fixings and calendars | your adapter: a provider (`<lib>_adapter/marketdata.py`) that exports the platform's data ONCE into a recorded store; every stack reads that store | steps 1 to 7 and "When the external library is also the market-data source" | `references/library-as-data-source.md` |
| B. an in-process library WITHOUT data (it prices from the objects you give it) | nobody in your adapter: the BASE config's provider (`pricebt.testing.synthetic:SyntheticMarket`, or the recorded store `support.curves:CurveStore`) feeds EVERY stack, so `L0.snapshot_digests` is `exact` by construction | no provider: `wrap` builds the library's objects from each snapshot (`pricebt-wrap-and-pricer`) | `skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml` |
| C. a pricing SERVICE that serves NO data (it prices only from a market you send it) | the same as B: no adapter provider, the base's provider feeds all stacks | no provider: `wrap` uploads each snapshot as a market, LAZILY (on the first price that needs it) and ONCE per snapshot, memoised on the wrapped pricer, so a snapshot never priced costs nothing; the layers add derived markets (`pricebt-enterprise-platform-patterns` step 3) | `skills/pricebt-wire-external-library/example-service/zeta_adapter/wrap.py` |

In B and C skip steps 3, 4 and 6 below (there is no provider to write; the base config names the shipped one and each stack overlay swaps only `wrap`); steps 1 and 2 still tell you what a snapshot holds.

## Prerequisites

* The layout rule: everything under `src/pricebt` except `contrib/` knows no library (skill `pricebt-architecture-and-rules`). A provider therefore returns data only.
* `<LIB>` = the external library. You have its documentation and code; you do NOT know pricebt. Questions marked "Q" below are yours to answer from `<LIB>`'s own material.
* Runtime: the repository root as working directory; `PYTHONPATH=src;tests;<parent directory of your adapter package>` (PowerShell: `$env:PYTHONPATH = "src;tests;..."`; `:` separators on Linux;
  pricebt adds no path itself: `python -m pricebt` puts the working directory on it, a script such as `python tools/<script>.py` does not, [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)); a Python 3.11+ that has pricebt's dependencies (numpy, pandas, pyyaml, tqdm, pytest, and pyarrow for the recorded-store reader). `python` below means
  that interpreter (in this checkout: `C:/Users/chris/anaconda3/envs/stir/python.exe`).
* References: needed only when you write a provider (case A): `references/provider-and-time.md`, and `references/library-as-data-source.md` for case A. Optional in every case: `references/snapshot-types.md` (field-level detail: skip it if you build snapshots as the example does).
* Read once: `src/pricebt/pricer.py` (`Pricer`, `MarketDataProvider`, `TimeMapping`, `SnapshotIndex`, `FunctionMDP`), `src/pricebt/snapshot.py`, `src/pricebt/market.py` (`MarketData`),
  `docs/design/adr/004-snapshots-and-wrap.md`. Field-level detail: `references/snapshot-types.md`. Protocol, time and models: `references/provider-and-time.md`.

## Steps

1. **Map `<LIB>`'s data onto the snapshot types.** Q: what does `<LIB>` serve for each row?

   | `<LIB>` serves | snapshot type (`src/pricebt/snapshot.py`) | non-negotiables |
   |---|---|---|
   | a discount / zero / forward curve for an as-of | `CurveSnapshot(name, reference_date, node_dates, values, value_kind="discount_factor", interpolation="log_linear_df")` | DFs at node dates; first node = reference date, first DF = 1.0; ONE interpolation tag exists |
   | published overnight fixings | `FixingsSeries(name, dates, values, unit, proxied)` | `unit` explicit (`percent` / `decimal`); only dates strictly before the reference date; `proxied` lists any value the provider invented |
   | a holiday list + weekend | `CalendarData(name, holidays, weekmask)` | the SAME holidays the library uses; `name` is what `conventions.calendar` will say |
   | bond reference data and quotes | `Security`, `Quote`, `QuoteSet(reference_date, quotes, securities, aliases, price_convention)` | coupon and yield PERCENT, price per 100 face; aliases (`CT10`) resolved by the provider as of the reference date |
   | everything of one instant | `MarketSnapshot(ts, reference_date, curves, fixings, quotes, calendars, provenance)` | `ts` tz-aware; all curves anchored at `reference_date` |

2. **Build snapshots as plain data** (executed):

```python
import datetime as dt, pandas as pd
from pricebt.snapshot import CalendarData, CurveSnapshot, FixingsSeries, MarketSnapshot, SnapshotPricer
ref = dt.date(2024, 6, 12)
curve = CurveSnapshot("sofr", ref, (ref, dt.date(2025, 6, 12)), (1.0, 0.96), provenance={"source": "<LIB>", "curve_id": "..."})
snap = MarketSnapshot(pd.Timestamp("2024-06-12 17:00", tz="America/New_York"), ref, curves={"sofr": curve},
                      fixings={"sofr_fix": FixingsSeries("sofr_fix", (dt.date(2024, 6, 11),), (5.31,), "percent")},
                      calendars={"usd_fed": CalendarData("usd_fed", (dt.date(2024, 7, 4),))})
pricer = SnapshotPricer(snap)          # .ts .reference_date .digest .describe() ; LOOKUPS = ()
```

   Convert every `<LIB>` date type (ISO strings, its own `Date`) to `dt.date` HERE, once. Put where-it-came-from (curve id, library version, build time) in `provenance`, which is NOT in the
   digest. `references/snapshot-types.md` has every field, every validation message and the exact list of what is in the digest.

3. **Write the provider** (`<lib>_adapter/marketdata.py` for a live client, or a reader of a recorded store): any object with a `get_pricer`, and optionally `available_timestamps`:

```python
from typing import Sequence
import pandas as pd
from pricebt.snapshot import SnapshotPricer

class Provider:
    def get_pricer(self, ts: pd.Timestamp, request=None) -> SnapshotPricer: ...   # raises MarketDataUnavailable / StaleSnapshot on a miss, never returns an empty stand-in
    def available_timestamps(self, start, end) -> Sequence[pd.Timestamp]: ...     # optional; needed for backtest.grid: {from_mdp: <role>}
```

   Rules: return `SnapshotPricer(snapshot)` (it carries the `digest` the tie-out reads; a pricer without one proves nothing); stamp the snapshot with the time it is FOR (never "now");
   do not mutate `request`; raise `ConfigError` on a request key you do not take. Models: `src/pricebt/testing/synthetic.py: SyntheticMarket`, `tests/support/curves.py: CurveStore`.

4. **Choose the time mapping** (`TimeMapping`, `SnapshotIndex.select`; a stored-data provider uses it, a live one has none). Q: how old may a snapshot be and still be "the market"?
   * `asof` + `max_staleness`: latest stamp <= ts within the bound (`unbounded_ok: true` for no bound). EOD data: `12h`; minute data: `5min`.
   * `exact`: stamp == ts. `date`: latest stamp on the same local date already visible at ts.
   * `eod_visible_at: "18:00"` DELAYS an end-of-day row until that local time (never earlier): the anti-look-ahead device for data stamped at midnight of its date.

   Config: `market: {mdps: {sofr: {type: "pkg.mod:Provider", kwargs: {...}, time: {mode: asof, max_staleness: 12h}}}}`. The loader passes the block as `time=` (a dict) if your `__init__`
   has a parameter `time`, as `mapping=` (a `TimeMapping`) if it has `mapping` or `**kwargs`, else refuses (Common failures).

5. **Know the look-ahead guard** (`MarketData.pricer`): `ts` may not exceed the engine clock (`LookAheadError`), and the snapshot you return may not be stamped after the request
   (`LookAheadError: provider returned a snapshot stamped ... for a request at ...`). A source that can only answer "now" cannot serve a backtest live: record it first (next section).

6. **Register the provider in the BASE config** (an overlay cannot set `market.mdps`). Leave `wrap` out until `pricebt-wrap-and-pricer` is done: the role then serves your plain
   `SnapshotPricer`, which is all the "five lines" check below needs (a `wrap` naming a module that is not written or not on `PYTHONPATH` is `[CFG-IMPORT] market.pricers.primary.wrap: ...`):

   ```yaml
   registry: {allow: [<lib>_adapter]}              # BASE config only: dotted paths outside `pricebt` are imported only under an allowed prefix
   market:
     mdps:    {sofr: {type: "<lib>_adapter.marketdata:Provider", kwargs: {...}}}
     pricers: {primary: {mdp: sofr}}               # later: add  wrap: "<lib>_adapter:wrap"  (the wrap is what a stack overlay swaps)
   ```

7. **Give the tests an offline source.** A tiny tree (`tests/support/tiny.py`: `write_curve_store`, `write_calendars`, `write_fixings`, milliseconds), the library-free
   `SyntheticMarket` / `flat_swap_world`, or the real `data/fixtures` (`pytest.mark.fixtures`, skips when absent). A live-source test is opt-in behind its own marker.

## When the external library is also the market-data source

`<LIB>` serves curves AND pricing functions (a bank's rates platform). Full recipe with executed code: `references/library-as-data-source.md`. The decisions:

* **Where the provider lives:** in the ADAPTER package (`<lib>_adapter/marketdata.py`), never in `src/pricebt` core and never in `tests/support` (`tests/test_support_guard.py` keeps that
  directory free of every banned library import). It MAY import `<LIB>`'s data client. It SHOULD NOT call `<LIB>`'s pricing functions (`pv`, `par_rate`, risk, an instrument pricer): this
  skill's rule (design advice; nothing in pricebt enforces it for an adapter package): data must stay neutral, or the library's own conventions leak into the inputs of the stacks you compare it with.
* **Curve object -> `CurveSnapshot`:** nodes = the curve's anchor plus its own pillar dates; values = its own discount factors at those dates through an accessor; anchor = reference date
  with DF 1.0; tag `log_linear_df`; provenance = curve id, library version. Q: is the curve log-linear in DF between nodes? Run the export-fidelity check (reference file section 4): nodes
  must round-trip exactly; the between-node difference is the floor of every downstream difference and needs a declared `reason` if it is not zero.
* **Fixings, calendars, quotes** come from the library's data services, in explicit units (`FixingsSeries.unit`), with holidays exported as `CalendarData` (check the library's calendar
  horizon: past it every day is a business day) and bond reference data and quotes as a `QuoteSet`. The provider applies the fixings visibility rule, not the adapter.
* **THE tie-out point: every stack must consume IDENTICAL snapshots.** The base config owns the provider, so all stacks share ONE (`stacks.validate_overlay`:
  `[CFG-STACK] market.mdps: a stack may not set market.mdps`). A live `<LIB>` provider would be fetched again by every stack and by the harness self-test, and the other stacks may
  have no `<LIB>` at all. So: **export the library's curves ONCE into a recorded store and let every stack read that same store**, through `support.curves:CurveStore` (layout in the
  reference file, section 5; `tests/support/tiny.py` writes it; `data/fixtures` is the real example). `<LIB>` is then needed only by the exporter and by its own adapter. The base config
  for that (complete file with its executed tie-out: reference file, section 6; the shipped analogue is `configs/suite/_swap_base.yaml` + `_swap_base_eod.yaml`, over `data/fixtures`):

  ```yaml
  registry: {allow: [support, <lib>_adapter]}   # `support` OWNS CurveStore (module support.curves): without it [CFG-ALLOW] market.mdps.sofr.type
  backtest:
    calendar: {holidays: [2024-01-01, 2024-01-15, 2024-02-19, 2024-03-29, 2024-05-27, 2024-06-19, 2024-07-04, 2024-09-02, 2024-11-28, 2024-12-25]}   # a COPY of <store>/calendars/nyc.json (the `usd_fed` the snapshots carry)
    grid: {from_mdp: primary, start: 2024-05-24, end: 2024-06-21}
  market:
    mdps:
      sofr:
        type: "support.curves:CurveStore"
        kwargs: {asset: "<ASSET>", root: "<store>/curves", profile: {kind: eod, freshness: trading_date}}   # root = the `curves` DIRECTORY, not the store root
        time: {mode: asof, max_staleness: 12h}
    pricers: {primary: {mdp: sofr, wrap: "pricebt.testing.refstack:wrap"}}   # the base names the reference stack; each stack overlay swaps `wrap` and `factory`
  ```
* **How L0 proves it:** `MarketData` records `(ts, role, digest)` before any wrap; `tieout.compare` L0 `snapshot_digests` is `exact` only if both runs fetched the same set. Executed: a
  provider that drifts between fetches stops `run_tieout` at its self-test; a provider whose pricers carry no digest yields an `info` row (identical inputs NOT proven) and still
  `passed: True`. Read the L0 rows, not only the exit code.

## Checks

```powershell
$env:PYTHONPATH = "src;tests"
python -m pytest tests/test_snapshot.py tests/test_market_wrap.py tests/test_synthetic_market.py tests/test_support_selector.py -q -o addopts= -p no:cacheprovider
# expected: all passed (snapshot rules and digest, wrap-once, the synthetic provider, the time selector)
python -m pytest tests/test_support_curves.py tests/test_support_common.py -q -o addopts= -p no:cacheprovider
# expected: all passed (the recorded-store reader on tiny trees and on the fixtures; skips with a reason if data/fixtures is absent)
```

Your own provider, in five lines (expected output: `True`, `True`, a 64-hex digest, then `LookAheadError`):

```python
import pandas as pd
from pricebt.market import Binding, MarketData
from pricebt.pricer import MarketDataProvider
from pricebt.timeutil import Clock
p = MyProvider(...); ts = pd.Timestamp("2024-06-12 17:00", tz="America/New_York")
clock = Clock(); md = MarketData({"primary": Binding(p)}, clock); md.audit = []; clock.advance(ts + pd.Timedelta(days=1))
print(isinstance(p, MarketDataProvider), md.pricer(ts).digest == p.get_pricer(ts).digest, md.audit[0]["digest"])   # identical content -> identical digest
md.pricer(ts + pd.Timedelta(days=5))                                                                                # the clock is a day ahead: LookAheadError
```

The tie-out proof, worked example (the adapter's parent directory MUST be on `PYTHONPATH`; skill `pricebt-conformance-and-tieout` has the full command set):

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example"      # for your adapter: "src;tests;<parent directory of <lib>_adapter>"
$ex = "skills/pricebt-wire-external-library/example"
python -m pricebt tieout $ex/config/acme_tieout_base.yaml --stack reference=configs/adapters/refstack_swap.yaml --stack acme=$ex/config/acme_swap.yaml
# expected: "**Verdict: PASS**"; section L0: snapshot_digests exact (n 19), resolved_terms exact (n 3), conventions_digest exact (n 1)
```

For YOUR adapter over a recorded store: the same command with your store-based base (reference file, section 6) and `--stack <lib>=<overlay.yaml>`; the L0 rows read the same.

## Common failures

| message (real) | cause | fix |
|---|---|---|
| `LookAheadError: pricer requested for T but the clock is at C` | a trigger, signal or measure asked for a time after the engine clock | ask for `ts <= clock`; never pass a future date to a provider |
| `LookAheadError: provider returned a snapshot stamped S for a request at R` | the provider returned "latest" data stamped after the request | serve the row visible at R (asof/eod_visible_at), or record and replay |
| `StaleSnapshot: market data unavailable at T: newest visible snapshot S older than 0 days 12:00:00` | `asof` bound too tight, or a market holiday | widen `max_staleness` (`3D` over long weekends) or skip the day in the grid |
| `MarketDataUnavailable: ... before_first_snapshot` / `provider X returned None` | request before the first stamp; the provider returned `None` | raise the typed error yourself with a reason; never return `None` or an empty snapshot |
| `NotImplementedError: X cannot list its timestamps` | `grid: {from_mdp: ...}` on a provider without `available_timestamps` | implement it, or give the grid a `freq` |
| `[SNAPSHOT] curve 'c': the first node D must be the reference date R` / `the anchor value must be 1.0, got 0.99` | curve not anchored, or DFs relative to something else | shift/renormalise at export; record the rule in `provenance` |
| `[SNAPSHOT] interpolation 'cubic' is not a snapshot tag; known: ['log_linear_df']` | only one tag exists | export dense nodes and declare the between-node tolerance; framework limit (see `framework_issues` of the return value) |
| `[SNAPSHOT] fixings 'f' run to D: only fixings strictly before the reference date D exist at the snapshot` | today's fixing put in today's snapshot | the provider's publication rule: dated d is public later |
| `[CFG-UNKNOWN-KEY] market.mdps.m.time: pkg:Cls does not accept a time mapping` | `__init__` has no `time` / `mapping` / `**kwargs` | add a `time=None` or `mapping=None` parameter (or drop `time:` for a live provider) |
| `[CFG-REQUIRED] ...time: an asof time mapping needs max_staleness (or unbounded_ok: true)` | `asof` without a bound, for a provider that receives `mapping=` (a `mapping` parameter or `**kwargs`); a provider with a `time` parameter gets the raw dict and applies its own defaults (`CurveStore`: 12h for EOD data), so it does not raise this | add `max_staleness: 12h` |
| `[CFG-STACK] market.mdps: a stack may not set market.mdps: only market.pricers.<role>.wrap` | provider put in a stack overlay | it belongs in the BASE; overlays swap wrap / factory / bind only |
| `[CFG-ALLOW] market.mdps.m.type: [CFG-ALLOW] module 'pkg' is not under an allowed prefix ['pricebt', 'pricebt']` (also `module 'support.curves'` for `CurveStore`) | provider `type:` outside `pricebt` and not allowed | [CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow) (also list `support` for `CurveStore`) |
| `[CFG-IMPORT] market.pricers.primary.wrap: [CFG-IMPORT] cannot import 'acme_adapter': No module named 'acme_adapter'` | the adapter's parent directory is not on `PYTHONPATH`, or the wrap is not written yet (omit `wrap` until it exists) | [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import) |
| `[CFG-VALUE] market.mdps.sofr.kwargs: no store partitions for asset 'A' under <dir>` | `root:` is the store root, not its `curves` directory | `root: "<store>/curves"` |
| `TieoutError: harness self-test failed: the same stack run twice differs at L0.snapshot_digests (input, ...)` | the provider is not repeatable (live drift, a time-dependent field inside the digest) | record once and read the store; move volatile facts to `provenance` |
| L0 `snapshot_digests info ... no digests recorded` with `passed: True` | pricers without a `digest` | return `SnapshotPricer`; the report passes but proves nothing |
| `[PROFILE] no built-in profile for asset 'X'; pass profile={kind: eod\|minute, freshness: ...}` | `CurveStore` on an asset name it does not know | `kwargs: {asset: X, profile: {kind: eod, freshness: trading_date}}` |
| `KeyError: 'Field "spline_knots" does not exist in schema'` (same for `source_variant`) | a store file lacks a column: all seven columns are REQUIRED (reference file, section 5) | write `spline_knots` all-null and `source_variant` free text |
| `market data unavailable at T: swap started D but the pricer carries no fixings`, though the store read fine | `<store>/fixings/USD-SOFR-1D.parquet` is missing: `CurveStore` (`fixings="auto"`) then serves snapshots with NO fixings and no error | the exporter must write the fixings file beside `curves/` |
| `FixturesMissing: directory not found: <root>/calendars` | the store lacks `calendars/nyc.json` | the exporter must write calendars and fixings beside `curves/` |
| `DataQualityError: f.parquet: N values outside (0, 0.2) for unit 'decimal'` | percent fixings read as decimal | `fixings_unit: percent` or export decimals |
| a holiday-sensitive check never fails | `SyntheticMarket` default calendar has no holidays | pass `calendar: {$call: "pricebt.timeutil:Calendar", kwargs: {holidays: [...]}}` and repeat it in `backtest.calendar` |
| one stack's failure aborts `run_tieout` with no report | `src/pricebt/tieout/runner.py: run_tieout` loops over the stacks calling `run_stack` with no per-stack error handling | `backtest.on_error: record` in the base gives a report (L0 `input`, L1 `structure`) |

## Related skills

`pricebt-wire-external-library` (the master playbook), `pricebt-architecture-and-rules` (the one rule), `pricebt-discover-the-library` (answering the Q's above),
`pricebt-map-library-to-schemas`, `pricebt-wrap-and-pricer` (snapshot -> library objects), `pricebt-instrument-kit` (what consumes the pricer), `pricebt-conformance-and-tieout`
(L0-L4, tolerances), `pricebt-debug-tieout-differences` (an `input` row), `pricebt-run-config-and-reports`, `pricebt-guards-and-packaging` (banned imports, markers, the live-test
marker), `pricebt-enterprise-platform-patterns`.
