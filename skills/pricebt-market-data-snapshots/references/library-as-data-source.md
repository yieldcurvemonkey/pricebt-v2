# When the external library is ALSO the market-data source: record once, read everywhere

The situation: `<LIB>` serves curves (and fixings, holidays, quotes) AND pricing functions. pricebt needs the data as snapshots and the pricing through the adapter. This page is the
executed recipe. `acmelib` (the fictional library of `skills/pricebt-wire-external-library/example/`) stands in for `<LIB>`; every line that touches it is tagged `# <LIB>`.
acmelib ships NO data client: section 3 gives the 20-line stand-in client (`acme_source.py`) that the later sections run against; a real client replaces it.
NOTHING is known here about a real bank library: the questions in section 1 are for YOU to answer from ITS documentation and code.

## 0. Why "record once" (the argument, with the code that enforces it)

* A tie-out runs ONE base config under several stacks. `pricebt.config.stacks.validate_overlay` refuses an overlay that sets `market.mdps`:
  `[CFG-STACK] market.mdps: a stack may not set market.mdps: only market.pricers.<role>.wrap`. So the provider is in the BASE and every stack reads the same one; only `wrap`,
  `factory` and `bind` differ. That is the structural half of "identical inputs".
* If that one provider is a LIVE client of `<LIB>`, every stack (and the harness self-test, which runs the reference stack a second time) makes its own fetches: revised history,
  intraday drift, a model or version change, or a rebuilt curve between two runs give different snapshots, and the rateslib / QuantLib / reference stacks in a CI job may have no
  `<LIB>` licence, network or credentials at all. Executed (section 7): a provider that moves the level by 0.05bp per fetch stops `run_tieout` at its self-test:
  `TieoutError: harness self-test failed: the same stack run twice differs at L0.snapshot_digests (input, max abs 38); L0.resolved_terms (input, max abs 2); L1.pv (input, max abs 200); ...`
  (the numbers depend on the config); with `selftest=False` the report shows `L0.snapshot_digests input | 38 differ: the two runs did not consume identical snapshots` and everything after it as `input`.
* So: export the library's curves ONCE into a recorded store; the base config names a store READER that imports no pricing library (and no `<LIB>` at all); every stack reads the same
  bytes; `<LIB>` is needed only by the exporter, by its own adapter and by the optional live provider.

```
<lib>_adapter/marketdata.py   LIVE provider: imports <LIB>'s DATA client; returns SnapshotPricer(MarketSnapshot); never calls a pricing function
<lib>_adapter/record.py       EXPORTER (run once, by a human or a job): live provider -> store
<store>/curves/asset=<A>/date=<D>/part-0.parquet, <store>/fixings/..., <store>/calendars/...     the recorded store (outside version control: data/.gitignore)
support.curves:CurveStore     the READER named in the base config (library-free; lives in tests/support: `PYTHONPATH=src;tests` and `registry.allow: [support, <lib>_adapter]`)
```

## 1. Questions to answer from `<LIB>`'s documentation before writing anything

1. Which call returns a curve for (curve id, as-of date/time)? What object comes back (nodes? a fitted model? a callable)? Is the as-of a date or a timestamp? Can it serve HISTORY
   (a past date) reproducibly, or only "now"? Is history restated when the library is upgraded?
2. Can the curve object give discount factors at arbitrary dates through an ACCESSOR (not by pricing an instrument)? At which dates are its own nodes/pillars? On which basis are its
   pillars stored (zero rates ACT/365? forward rates? par rates?). If the object only exposes par-swap or zero quotes, which library call converts them to discount factors, and does
   that call use a calendar or a day count (then it is a convention and belongs to the adapter, not to the data)?
3. Interpolation: is the curve log-linear in the discount factor between its nodes (`log_linear_df`, the only tag a snapshot can carry)? If not, what is it (cubic on zeros, monotone
   convex, ...) and how far from log-linear on the same nodes (measure it, section 4)?
4. Fixings: which call returns published overnight fixings (dates, values, UNIT: percent or decimal, which reference rate), and as of what visibility rule (a fixing dated d is public
   when)? Are late corrections restated?
5. Calendars: which call lists a calendar's holidays and weekend, over what horizon (a library calendar that ends in a given year makes later dates plain business days)?
   Which calendar name does the library use for the swap, and is it the same as the one you will name in `conventions.calendar`?
6. Bonds: which call returns reference data (coupon, issue and maturity dates), quotes (clean / yield, which unit, which side), and on-the-run rankings as of a date?
7. Identity and provenance: curve id, source system, version of the library, build time. (These go in `provenance`, never in a field that is in the digest.)
8. Licence and confidentiality: may the exported curves leave the bank's environment or sit on a CI machine? (`data/.gitignore` already excludes everything under `data/`.)

## 2. The live provider: what it may and may not do

| allowed in `<lib>_adapter/marketdata.py` (DATA) | forbidden there (PRICING; this skill's rule, design advice that pricebt does not enforce for an adapter package) |
|---|---|
| import the data client / session / curve-service class of `<LIB>` | calling anything that values an instrument: `pv`, `npv`, `par_rate`, `risk`, `bucket_risk`, a swap or bond pricer |
| fetch a curve OBJECT and read its nodes and its own discount factors at those nodes through an accessor | building a par-rate or a zero-rate "market quote" by pricing (it would embed the library's calendars and day counts into what must be neutral data) |
| fetch fixings, holiday lists, bond reference data and quotes | applying a convention to the data (spot lag, business-day adjustment, day count) |
| record `provenance` (curve id, library version, source) | anything that reads `conventions`: a provider does not know them |

The provider returns `SnapshotPricer(MarketSnapshot(...))`, stamps every snapshot with the time it is FOR (never `now`), applies its own fixings visibility rule (the provider owns it: the
snapshot carries only fixings visible at its stamp, and lists an invented value in `proxied`), and implements `available_timestamps`. It must not import `pricebt.contrib` or another
adapter. Model: `src/pricebt/testing/synthetic.py: SyntheticMarket` (publication rule, `proxied`) and `tests/support/curves.py: CurveStore`.

## 3. From a library curve OBJECT to a `CurveSnapshot` (executed)

The data client below is a STAND-IN (acmelib has none): save it as `acme_source.py` in any directory on `PYTHONPATH`. A real client replaces it; what the rest of this page needs from it is
`.curve(date)` -> an object with `.anchor`, `.pillars` (ISO strings) and `.df(iso)`, and `.fixings(date)` -> `{ISO date: DECIMAL rate}` published strictly before `date`.

```python
# acme_source.py: a stand-in for a data client. Deterministic: a level that drifts with the date, 12 pillars, fixings on business days only.
import datetime as dt

import acmelib  # <LIB>

PILLAR_DAYS = (30, 91, 182, 365, 730, 1095, 1826, 2557, 3652, 5479, 7305, 10957)
HOLIDAYS = ("2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25")  # <LIB>: its swap calendar


def is_bd(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


class Client:
    def curve(self, d: dt.date) -> "acmelib.ZeroCurve":  # <LIB>: a zero-rate curve object anchored at d
        level = 0.043 + 0.00005 * (d - dt.date(2024, 5, 1)).days
        pillars = [(d + dt.timedelta(days=n)).isoformat() for n in PILLAR_DAYS]
        return acmelib.ZeroCurve(d.isoformat(), pillars, [level + 0.0003 * i for i in range(len(PILLAR_DAYS))])

    def fixings(self, d: dt.date) -> dict:  # <LIB>: {ISO date: DECIMAL rate}
        days = [dt.date(2024, 1, 2) + dt.timedelta(days=i) for i in range((d - dt.date(2024, 1, 2)).days)]
        return {x.isoformat(): 0.0531 for x in days if is_bd(x)}
```

The conversion (the provider's core):

```python
import datetime as dt
from pricebt.snapshot import CurveSnapshot


def curve_snapshot(lib_curve, name: str = "sofr") -> CurveSnapshot:
    nodes = (lib_curve.anchor,) + tuple(lib_curve.pillars)                        # <LIB>: anchor date + the curve's own pillar dates
    return CurveSnapshot(name, dt.date.fromisoformat(lib_curve.anchor),           # reference_date == the curve's anchor == nodes[0]
                         tuple(dt.date.fromisoformat(n) for n in nodes),          # <LIB>: ISO strings -> dt.date
                         tuple(1.0 if n == lib_curve.anchor else lib_curve.df(n) for n in nodes),   # <LIB>: its own DF accessor, an ACCESSOR not a pricer
                         provenance={"source": "<LIB>", "curve": "USD-SOFR", "library": "<LIB> <version>"})
```

Rules: the first node IS the reference date with DF 1.0 (`CurveSnapshot` refuses anything else); if `<LIB>` stores zero rates, convert to DFs with ITS basis (acmelib: ACT/365) and
record that basis in your own notes, not in the snapshot; keep every date as `dt.date` (a library's ISO strings or `Timestamp`s are converted HERE, once); node choice: use the
library's own pillars, plus extra nodes if its interpolation is not log-linear in DF (then the error between nodes shrinks with node density: section 4).

## 4. Export fidelity: what the snapshot says versus what the library says (run this at export time)

```python
import datetime as dt
import pricebt.testing.refstack as R
from acme_source import Client                                                # <LIB>: your data client

lib = Client().curve(dt.date(2024, 6, 12))                                    # <LIB> curve object for the day
c = curve_snapshot(lib)                                                       # the CurveSnapshot you are about to record (or the one a live provider returns)
rc = R.RefCurve(c.reference_date, c.node_dates, c.values)                     # pricebt.testing.refstack.RefCurve: the snapshot's `log_linear_df`
print("nodes exact:", max(abs(lib.df(n.isoformat()) - v) for n, v in zip(c.node_dates[1:], c.values[1:])))                  # must be exactly 0.0
mids = [c.reference_date + dt.timedelta(days=n) for n in (10, 45, 200, 500, 1500, 4000, 9000)]
print("between nodes max |lib.df - snapshot log-linear df|:", max(abs(lib.df(m.isoformat()) - rc.df(m)) for m in mids))     # library interpolation vs the tag
beyond = c.reference_date + dt.timedelta(days=15000)                           # past the last node
print("beyond last node   |lib.df - snapshot|:", abs(lib.df(beyond.isoformat()) - rc.df(beyond)))                            # library extrapolation vs the tag
```

Executed on acmelib all three print `0.0` because acmelib reuses the reference arithmetic. A REAL library shows `between` and `beyond` > 0 when its interpolation or extrapolation differ:
that number is the floor of every downstream difference (L1/L2) and must be recorded, with a `reason`, as a `tieout.tolerances` declaration (skill `pricebt-conformance-and-tieout`;
`tasks/tolerance_ledger.yaml` is the ledger). Quantify it BEFORE blaming an adapter.

## 5. The recorded store: the layout `support.curves.CurveStore` reads

`<root>/curves/asset=<ASSET>/date=<YYYY-MM-DD>/*.parquet`, one row per snapshot (a row is one immutable curve stamped `timestamp_utc`). ALL seven columns are REQUIRED (the reader selects
them by name):

| column | type | meaning |
|---|---|---|
| `timestamp_utc` | timestamp[us, UTC] | the stamp (the time the curve is FOR); rows of one stamp: the LAST wins, the rest are `dropped` (`duplicate_stamp`) |
| `trading_date` | date32 | the partition date |
| `node_dates` | list<date32> | first element is the reference date |
| `discount_factors` | list<double> | first element 1.0 |
| `interpolation` | string | the STORE tag; `log_linear` is the only one mapped (to `log_linear_df`); a null is read as `log_linear` |
| `source_variant` | string | free text: where the row came from (put the library and version here); the column must exist |
| `spline_knots` | list<date32> | must be null in every row (write an all-null column: it must exist): `ConfigError: spline-interpolated store rows are not supported; only node-table (DF) curves` |

A file without `spline_knots` or `source_variant` fails with `KeyError: 'Field "spline_knots" does not exist in schema'` (executed for both), not with a message about the layout.

Beside it: `<root>/fixings/USD-SOFR-1D.parquet` (columns `date`, `rate`, DECIMAL unless `fixings_unit="percent"`; validated: ascending, unique, inside (0, 0.2) for decimal),
`<root>/calendars/nyc.json` (`{"name", "holidays": [...], "weekmask": "Mon Tue Wed Thu Fri"}`). Names the reader emits into every snapshot (provider-defined data that configs and
conventions refer to): curve `sofr` (`curve_id=`), fixings `USD-SOFR-1D` (`support.common.FIXINGS_NAME`), calendar `usd_fed` (`support.common.SWAP_CALENDAR`, read from `nyc.json`).
A MISSING fixings file is not an error at read time: with the default `fixings="auto"` the snapshots simply carry no fixings, and the first swap that has started stops the run
(`MarketDataUnavailable: market data unavailable at 2024-05-30 17:00:00-04:00: swap started 2024-05-29 but the pricer carries no fixings`, executed).
`CurveStore` is USD-SOFR shaped; for another currency, index or calendar write a sibling reader on `support.common.CachedProvider` + `Selector` (the model is `tests/support/curves.py`) and keep it
library-free. Executed exporter (`tiny.py` writers = the layout's reference implementation, and it writes all seven columns; use `pandas.DataFrame.to_parquet` in production):

```python
import datetime as dt
import pandas as pd
from support import tiny                                                      # tests/support/tiny.py


def export_store(client, root, days, asset="ACME-SOFR"):
    rows = []
    for d in days:                                                            # one row per business day, stamped 17:00 New York
        cs = curve_snapshot(client.curve(d))                                  # section 3
        stamp_utc = pd.Timestamp(dt.datetime.combine(d, dt.time(17, 0))).tz_localize("America/New_York").tz_convert("UTC")
        rows.append(tiny.curve_row(str(stamp_utc.tz_localize(None)), d.isoformat(), [(n.isoformat(), v) for n, v in zip(cs.node_dates, cs.values)],
                                   interpolation="log_linear", variant="<LIB> <version>"))
    tiny.write_curve_store(root, asset, rows)                                 # <root>/curves/asset=<asset>/date=<d>/part-0.parquet
    tiny.write_calendars(root)                                                # PRODUCTION: write <LIB>'s own holidays as calendars/nyc.json
    fx = client.fixings(days[-1])                                             # <LIB>: {iso date: DECIMAL rate}, history long enough for the oldest swap you will trade
    tiny.write_fixings(root, [dt.date.fromisoformat(k) for k in fx], list(fx.values()))
```

Run it on the stand-in client (41 business days, 2024-05-01 to 2024-06-28; `<store>` = any empty directory outside version control), then read it back and prove it:

```python
from pathlib import Path
from acme_source import Client, is_bd                                         # is_bd: the stand-in's business-day test
from support.curves import AssetProfile, CurveStore

root = Path("<store>")
export_store(Client(), root, [d for d in (dt.date(2024, 5, 1) + dt.timedelta(days=i) for i in range(60)) if is_bd(d)])
store = CurveStore("ACME-SOFR", root=root / "curves", profile=AssetProfile("eod"), time={"mode": "asof", "max_staleness": "12h"})
sp = store.get_pricer(pd.Timestamp("2024-06-12 17:00", tz="America/New_York"))
s = sp.snapshot
print("served:", sp.ts, sp.reference_date, "| curve:", *[(k, v.interpolation, len(v.node_dates)) for k, v in s.curves.items()], "| calendars:", sorted(s.calendars),
      "| fixings:", {k: len(v.dates) for k, v in s.fixings.items()}, "| dropped rows:", len(store.dropped))
# served: 2024-06-12 17:00:00-04:00 2024-06-12 | curve: ('sofr', 'log_linear_df', 13) | calendars: ['usd_fed'] | fixings: {'USD-SOFR-1D': 112} | dropped rows: 0
```

Do NOT export holiday rows that carry the previous day's nodes: the reader would drop them (`stale_reference`), which is right, but exporting them hides that the library has no curve
that day.

`profile` is required for an asset name the reader does not know: `[PROFILE] no built-in profile for asset 'ACME-SOFR'; pass profile={kind: eod|minute, freshness: ...}` (freshness
`trading_date`: a row is served only if its first node equals its trading date; `local_bd`: exchange-dated minute data; `none`: always).

## 6. The base config that reads the store (executed) and its tie-out

Three things differ from a base over `SyntheticMarket` (`acme_tieout_base.yaml`): the reader lives in `support`, so `support` must be on `registry.allow` next to your adapter package; `root` is the
`curves` DIRECTORY of the store; and `backtest.calendar` must hold the same holidays as `<store>/calendars/nyc.json`. Everything else is the shipped pattern
(`configs/suite/_swap_base.yaml` and `_swap_base_eod.yaml` over `data/fixtures`). Save it as `store_base.yaml`, with `<store>` = the directory of section 5:

```yaml
registry: {allow: [support, acme_adapter]}          # <lib>_adapter for your library; `support` owns CurveStore
backtest:
  tz: America/New_York
  calendar: {holidays: [2024-01-01, 2024-01-15, 2024-02-19, 2024-03-29, 2024-05-27, 2024-06-19, 2024-07-04, 2024-09-02, 2024-11-28, 2024-12-25]}   # a COPY of calendars/nyc.json
  date_policy: close
  grid: {from_mdp: primary, start: 2024-05-24, end: 2024-06-21}     # the store's own stamps, filtered by the calendar: 19 points
  progress: {show: false}
  attribution: {layers: [carry, roll, delta, convexity], cadence: eod}
  measures: [dv01]
market:
  mdps:
    sofr:
      type: "support.curves:CurveStore"
      kwargs: {asset: "ACME-SOFR", root: "<store>/curves", profile: {kind: eod, freshness: trading_date}}
      time: {mode: asof, max_staleness: 12h}
  pricers:
    primary: {mdp: sofr, wrap: "pricebt.testing.refstack:wrap"}       # the base names the reference stack; each stack overlay swaps `wrap` and `factory`
instruments:
  usd_sofr_ois:
    asset_class: swap
    factory: "pricebt.testing.refstack:swap"
    conventions: {calendar: usd_fed, spot_lag_days: 2, day_count: act360, frequency: annual, business_day_convention: modified_following, payment_lag_days: 2, compounding: daily_compounded, stub: short_front, end_of_month: false, fixing_lag_days: 1, time_accrual: lump}
strategy:
  triggers:
    - type: periodic
      frequency: 1m
      actions:
        - {type: add_trade, priceables: {instrument: usd_sofr_ois, name: recv10y, terms: {side: receive, maturity: 10Y, notional: 1e7, fixed_rate: par}}, trade_duration: 1m}
        - {type: add_trade, priceables: {instrument: usd_sofr_ois, name: pay5y, terms: {side: pay, maturity: 5Y, notional: 5e6, fixed_rate: par}}, trade_duration: 1m}
```

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example"     # your adapter: the parent directory of <lib>_adapter (the store needs no <LIB>)
$ex = "skills/pricebt-wire-external-library/example"
python -m pricebt validate store_base.yaml --stack $ex/config/acme_swap.yaml                     # OK
python -m pricebt tieout store_base.yaml --stack reference=configs/adapters/refstack_swap.yaml --stack acme=$ex/config/acme_swap.yaml
# **Verdict: PASS** ... 19 points, 2 positions; L0 snapshot_digests exact n 19, resolved_terms exact n 2, conventions_digest exact n 1
```

The three mistakes that break it, each executed (`validate` output): `support` missing from the allow-list gives
`error: [CFG-ALLOW] market.mdps.sofr.type: [CFG-ALLOW] module 'support.curves' is not under an allowed prefix ['pricebt', 'pricebt', 'acme_adapter']`; `root` = the store root gives
`error: [CFG-VALUE] market.mdps.sofr.kwargs: no store partitions for asset 'ACME-SOFR' under <store>`; the example directory off `PYTHONPATH` gives
`error: [CFG-IMPORT] market.pricers.primary.wrap: [CFG-IMPORT] cannot import 'acme_adapter': No module named 'acme_adapter'`.

## 7. Proving the inputs were identical (three levels)

1. **The same object in the base.** `base_digest` ignores exactly `factory`, `bind` and `wrap`, so equal `base_hash` values prove the stacks share the provider block
   (`run_tieout` raises `TieoutError: the stacks do not share one base config (X1)` otherwise).
2. **One provider, four wraps, one digest** (executed on the store base of section 6; every stack's wrapped pricer carries the snapshot's digest; QuantLib and rateslib need their extras):

```python
import pandas as pd
from pricebt import api
stacks = {"reference": "configs/adapters/refstack_swap.yaml", "acme": "skills/pricebt-wire-external-library/example/config/acme_swap.yaml",
          "quantlib": "configs/adapters/quantlib_swap.yaml", "rateslib": "configs/adapters/rateslib_swap.yaml"}
t = pd.Timestamp("2024-06-12 17:00", tz="America/New_York")
d = {n: api.build("store_base.yaml", stack=[o]).market.pricer(t).digest[:12] for n, o in stacks.items()}
print("digest per stack:", d, "| all equal:", len(set(d.values())) == 1)
# digest per stack: {'reference': 'bd68c249eeee', 'acme': 'bd68c249eeee', 'quantlib': 'bd68c249eeee', 'rateslib': 'bd68c249eeee'} | all equal: True
```

3. **L0 of the tie-out** compares the SET `(ts, role, digest)` of the two runs, recorded BEFORE the wrap. Executed, reference against acme over the recorded store
   (19 grid points, two positions, layers on; the Python API can also audit the ladder, the CLI cannot):

```python
from pricebt.tieout import run_tieout
ex = "skills/pricebt-wire-external-library/example"
r = run_tieout("store_base.yaml", {"reference": ["configs/adapters/refstack_swap.yaml"], "acme": [f"{ex}/config/acme_swap.yaml"]}, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))
print("passed:", r.passed, "| self-test:", r.header["selftest"])
for row in r.reports["reference_vs_acme"].rows:
    if row.level == "L0": print(" ", row.level, row.quantity, row.status, "n=", row.n)
# passed: True | self-test: passed
#   L0 snapshot_digests exact n= 19
#   L0 resolved_terms exact n= 2
#   L0 conventions_digest exact n= 1
```

   Read the row as: 19 (timestamp, role) fetches, identical digests in both runs. What L0 does NOT prove: that the wrap did not alter the data (that is L1/L2 territory) or that the
   store matches the live library (that is section 4, at export time).

Negative controls, each executed: (a) a provider whose every fetch moves the level by 0.05bp (a `CurveStore` subclass): `run_tieout` stops at its self-test (section 0), and with
`selftest=False` the L0 row is `input`; (b) a provider that returns a pricer WITHOUT a digest (`p.digest = None`) gives
`L0 snapshot_digests info | no digests recorded: the provider's pricers carry none, so identical inputs are not proven` and `passed: True`: an `info` row is not a failure, so read L0 in
the report, do not rely on the exit code alone; (c) an overlay that sets `market.mdps` is refused (section 0).

## 8. Calendars and fixings: the two ways this goes wrong quietly

* The snapshot's `CalendarData` must equal the holiday set the library itself uses for the swap. Export it FROM `<LIB>` (its own list, its own horizon: check the last year it
  covers) and give the `backtest.calendar` of the base the same holidays (the shipped suite carries a copy and pins equality with a test:
  `tests/test_suite_swaps.py: test_the_suite_calendar_is_a_copy_of_the_calendar_the_curve_store_serves`). A wrap that forgets the holidays does not give a wrong number, it stops the
  run (`MarketDataUnavailable ... 1 overnight fixings missing between ...`); a wrap with one holiday too many gives a comparable report whose first difference is
  `L0.resolved_terms input` (`effective: 2024-05-29 vs 2024-05-30`). See `pricebt-wrap-and-pricer`.
* `SyntheticMarket`'s default calendar has NO holidays: a holiday-sensitive check on it is vacuous. Build the market with `calendar: {$call: "pricebt.timeutil:Calendar", kwargs: {holidays: [...]}}` and repeat the
  list in `backtest.calendar` (the example base uses a YAML anchor: `skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml`).
* Fixings: give the reader the history the oldest seasoned swap needs (the loader of a started swap wants a fixing for every business day since its effective date), in the right unit;
  a percent file read as decimal fails loudly (`DataQualityError: pct.parquet: 167 values outside (0, 0.2) for unit 'decimal' (first 2023-11-01: 4.30...)`), a unit mislabelled inside the
  range does not.

## 9. Live versus offline tests

* Offline (in the default run): a tiny store built in `tmp_path` (`support.tiny`) or a small committed-in-the-adapter-package snapshot; no `<LIB>`, no network. The reader, the wrap and the
  tie-out run on it.
* Live (opt-in): a test that calls the real data client, carrying the library's `adapter_<lib>` marker (registered as `pricebt-guards-and-packaging` says) and skipped unless an environment variable is set (`skipif(<env var unset>, reason=...)`).
  It checks the export step: live snapshot digest equals the recorded snapshot digest for a sampled day.
* The exporter should follow the safety rules of the maintainer's own fixture exporter (`tools/export_fixtures.py`: read-only on the source, a checksum manifest, a `--verify`
  mode): record the library version, the export time and a sha256 per file next to the store (`data/fixtures/MANIFEST.json` and `files.sha256` are the shape).
