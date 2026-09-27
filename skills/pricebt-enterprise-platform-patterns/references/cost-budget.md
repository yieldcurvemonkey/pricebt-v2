# Latency, batching and caching: budget the cost of a run BEFORE the first tie-out

A local library answers in milliseconds; a platform behind a network answers in tens or hundreds, and a full run is `positions x grid points x calls per mark`. The engine is SEQUENTIAL and has no
batching hook: it marks one position at a time (`Engine._mark`) and evaluates measures and layers one position at a time. So the budget is a number you compute, not a thing you hope for.

## 1. What is cached, and where (read from the code)

| cache | key | lifetime | who owns it |
|---|---|---|---|
| snapshot fetch | `(role, ts, request)` in `MarketData._memo` (LRU, `maxsize` 32) | per run; `MarketData.reset()` clears | pricebt |
| `wrap` result | `(role, snapshot digest, stamp)` in `MarketData._wrapped` (LRU, `maxsize` 32) | per run | pricebt calls your `wrap` ONCE per snapshot and stamp |
| `ctx.cache` | anything your callables put in it | ONE position at ONE mark: shared by `value`, the measures and the audit of that mark; the layer flush gets a FRESH one | your code (`ctx.cache[("<lib>_swap_ladder", id(self), tenors)]`) |
| per-snapshot work | your key, stored on your pricer (`p.memo(key, fn)` in the shipped pricers) | as long as the pricer | your `wrap`: the risk-curve build, the environment upload, calendar registration |

Nothing is shared ACROSS positions: two identical trades are two full sets of calls. A trade's per-snapshot work (a curve bootstrap for the ladder) must therefore live on the PRICER, keyed by the
snapshot, not on the position. (A position holds its entry, previous and last pricers, so a remote environment stays alive for as long as a position priced on it is open.)

## 2. What one run costs (count it; executed on the worked example)

Where calls come from, per position: a `value` at every mark (+ a cash-flow list when the previous mark exists); every measure of `backtest.measures` at every mark; every `backtest.audit` measure at every
mark (NOT throttled by the layer cadence); a layer flush at every cadence point (`each`, `eod`, `every_n:<k>`, and at every close or resize): one decomposition, 6 valuations (QuantLib adapter) to 13
(worked example) plus the cash-flow lists; the baseline (`attribution.baseline: true`): 3 measures per flush; book signals and ladder-hedge firings: the measures of the whole book at that point, and the
unit ladder of every hedge leg; a trade's `factory`: at least one library call when `fixed_rate: par`.

The script counts the calls that cross the adapter-to-library boundary (patch the attribute the adapter calls; for a remote client wrap the client in a counting proxy, `RecordedClient.n_live` does exactly this):

```python
"""Library calls per position-point, by configuration: count the calls that cross the adapter -> library boundary (what would be a network call for a remote platform)."""
import collections
import functools

import acmelib
from pricebt import api

EX = "skills/pricebt-wire-external-library/example/config/"
BASE, OVERLAY = EX + "acme_tieout_base.yaml", EX + "acme_swap.yaml"
API = ("pv", "cashflows", "par_rate", "pv01", "bucket_risk", "gamma")
REAL = {n: getattr(acmelib, n) for n in API}
calls = collections.Counter()


def counted(name):
    @functools.wraps(REAL[name])
    def f(*a, **k):
        calls[name] += 1
        return REAL[name](*a, **k)

    return f


for n in API:
    setattr(acmelib, n, counted(n))  # the adapter calls `acme.<name>(...)`, an attribute lookup at call time


def measure(label, sets):
    calls.clear()
    res = api.run(BASE, sets=["backtest.progress.show=false", *sets], stack=[OVERLAY])
    pp = int(res.equity["n_positions"].sum())  # position-points: one mark per open position per grid point
    total = sum(calls.values())
    print(f"{label:<44} position-points {pp:>3}  library calls {total:>5}  per position-point {total / pp:5.1f}   {dict(sorted(calls.items()))}")


measure("marks only (no layers, no measures)", ["backtest.attribution={}", "backtest.measures=[]"])
measure("+ measures [dv01] every point", ["backtest.attribution={}"])
measure("+ layers, cadence eod (the base)", [])
measure("+ layers, cadence every_n:5", ["backtest.attribution.cadence=every_n:5"])
measure("+ audit dv01,gamma,rate,delta_ladder", ["backtest.audit={measures: [dv01, gamma, rate, delta_ladder]}"])
measure("audit, layers every_n:5", ["backtest.attribution.cadence=every_n:5", "backtest.audit={measures: [dv01, gamma, rate, delta_ladder]}"])
```

Output (as printed on the example base config, three swaps over a month, 57 position-points; re-run on yours):

```
marks only (no layers, no measures)          position-points  57  library calls   113  per position-point   2.0   {'cashflows': 54, 'par_rate': 2, 'pv': 57}
+ measures [dv01] every point                position-points  57  library calls   166  per position-point   2.9   {'bucket_risk': 44, 'cashflows': 54, 'par_rate': 2, 'pv': 57, 'pv01': 9}
+ layers, cadence eod (the base)             position-points  57  library calls   931  per position-point  16.3   {'bucket_risk': 44, 'cashflows': 156, 'par_rate': 2, 'pv': 720, 'pv01': 9}
+ layers, cadence every_n:5                  position-points  57  library calls   346  per position-point   6.1   {'bucket_risk': 44, 'cashflows': 78, 'par_rate': 2, 'pv': 213, 'pv01': 9}
+ audit dv01,gamma,rate,delta_ladder         position-points  57  library calls  1054  per position-point  18.5   {'bucket_risk': 57, 'cashflows': 156, 'gamma': 57, 'par_rate': 55, 'pv': 720, 'pv01': 9}
audit, layers every_n:5                      position-points  57  library calls   469  per position-point   8.2   {'bucket_risk': 57, 'cashflows': 78, 'gamma': 57, 'par_rate': 55, 'pv': 213, 'pv01': 9}
```

Read it: (1) a mark alone is about 2 calls (`pv` and the cash-flow list); (2) `dv01` adds about one call per mark (the ladder sum of a started swap is ONE risk build per position and mark, cached in
`ctx.cache`); (3) layers at `eod` multiply the cost by about six; the same run at `every_n:5` costs about a third; (4) the audit adds a constant per MARK (`gamma`, `bucket_risk` 57 each in both audit rows) whatever
the layer cadence: the last two rows differ only in the layers. So the levers are the layer cadence, the audit measures (tie-out runs only) and the number of positions and points.

## 3. The procedure

1. Run the counter on a SMALL config (a month, three trades, like the worked example's base) with the features you will use. Get calls per position-point by kind.
2. Time each kind of call on the platform: p50 and p95, cold and warm, from a script (`skills/pricebt-discover-the-library/references/probe-skeleton.md`, the model `tools/ql_evidence/p1_timing.py`). Note rate limits and the price of an upload.
3. Multiply: `seconds = sum(count_k x latency_k)`. The engine does not parallelise, so wall time is the sum. For the real run scale the counts by positions x points.
4. If it does not fit: (a) `attribution.cadence: every_n:<k>` or layers only for the tie-out window; (b) `backtest.measures: []` and audit only in the tie-out; (c) a coarser grid; (d) fewer trades in the
   tie-out base (the base of a tie-out is a probe, not a strategy); (e) memoise inside the adapter: the shocked valuations of the layers (the reference stack and the worked example evaluate `v_eps` eight
   times where four distinct shocks suffice; the QuantLib adapter needs 6 valuations in all), the risk curve per snapshot, the environment per digest; (f) the recorded mode (`offline-and-live-tests.md`):
   record once, replay for every later run.
5. Record the budget in the conventions document (section 0, cost row) and re-measure after every change of the adapter.

## 4. Batching: what exists and what does not

* Exists: one upload or one curve build per SNAPSHOT (memoised in `wrap`, keyed by `SnapshotPricer.digest`: `remote_environment.py`), and per-position caches in `ctx.cache`.
* Does not exist: a hook that hands the adapter the whole book at a point. A portfolio-pricing call of the platform cannot be used by pricebt as it is; a pricer does not know the positions, so it cannot
  prefetch them. This is a limitation of the engine, not something to work around inside core: note it in section 12 of your conventions document ("Contract gaps found", `pricebt-discover-the-library`), for the maintainers.
* Do not retry or batch inside `value` in a way that changes WHAT is computed: batching must return exactly the numbers the single calls would.

## 5. A service that prices only from a market you send: count uploads AND requests (executed on the example service)

Section 2 counts calls that cross the adapter-to-library boundary. For a service that holds markets, add the UPLOADS: an upload is the expensive call (120 ms simulated on the example service, against 25 ms plus 3 ms per trade
for a price request), and the layers multiply them. The rule (each term read from the code, then confirmed by the counters below):

* **Layers off: `uploads = snapshots`.** `wrap` uploads nothing; the market goes up on the first price that needs it, once per snapshot (memoised on the wrapped pricer, which `MarketData` builds once per snapshot and stamp).
* **Layers on: `uploads = snapshots + 5 x intervals + cash worlds`** for a service whose scenarios cannot express the rolled, resampled and shocked worlds (`5 x intervals` alone holds only while every step is within the payment lag, as at cadence `eod`: see the next bullet). `Engine._flush_layers` (`src/pricebt/engine/engine.py`) asks each layer of each held position once
  per flush, with the previous flush's pricer and the current one; the decomposition (`RefSwap.decomposition` in `src/pricebt/testing/refstack.py` is the model) values `V0`, `V1`, the rolled curve, its resampling at the t1 nodes and
  the shocked curves `V(+-h)` and `V(+-h/2)` (Richardson). Every world that is not the snapshot's own market is one upload, content-addressed so every position shares it: the rolled world (which IS the resampled base when the
  t1 curve's node offsets equal t0's, a different market otherwise: 6 per interval: the golden case, `p6_golden_control.py`, uploads 7 = 6 + 1 cash world over one 14-day step with different node grids), and `2 x steps` shocked worlds (4 for Richardson at h and h/2; 2 for a single h, so 3 per interval, by the same count and not run).
* **`+ 1` per interval (the cash world)** when `t1` is more than the payment lag after `t0` (2 business days on the example service) and your cash query needs the t1 fixings (`pricebt-layers-and-ladder`, `layer-definitions-and-assembly.md` section 3): the old market is rebuilt with them.
* **Requests**: the layer flush is about 11 requests per position per flush on the example service (forward value, rolled, base and eight shocked valuations; four distinct shocks suffice, so 7 once memoised), plus about 2 per
  position-point for the marks. `every_n:<k>` divides the layer part by about `k`.

```python
"""Uploads and requests of a SERVICE adapter, by configuration: what does the platform's own counter say? Run from the repository root."""
import zeta  # LIB: your service's client package
import zeta_adapter._compat as C  # LIB: your adapter's client holder: `set_client` installs a fresh client per run, `UPLOADS` counts snapshot markets and derived markets apart
from pricebt import api

EX = "skills/pricebt-wire-external-library/example-service/zeta_adapter/config/"  # LIB
BASE, OVERLAY = EX + "zeta_tieout_base.yaml", EX + "zeta_swap.yaml"  # LIB


def measure(label, sets):
    cli = zeta.Client()  # LIB: a fresh client, so its counters (`stats["markets_uploaded"]`, `stats["requests"]`) are this run's
    C.set_client(cli)
    C.UPLOADS.update(snapshot=0, derived=0)
    built = api.build(BASE, sets=["backtest.progress.show=false", *sets], stack=[OVERLAY])
    res = built.run()
    pp = int(res.equity["n_positions"].sum())  # position-points: one mark per open position per grid point
    n = built.market.n_wraps  # snapshots wrapped == distinct snapshots fetched
    up = cli.stats["markets_uploaded"]
    print(f"{label:<30} snapshots {n:>2}  uploads {up:>3} = {C.UPLOADS['snapshot']} snapshot + {C.UPLOADS['derived']} derived  requests {cli.stats['requests']:>4}  position-points {pp}  requests per position-point {cli.stats['requests'] / pp:5.1f}")


measure("layers off", ["backtest.attribution={}"])
measure("layers on, cadence eod", [])
measure("layers on, cadence every_n:5", ["backtest.attribution.cadence=every_n:5"])
measure("layers on, cadence every_n:3", ["backtest.attribution.cadence=every_n:3"])
```

Output (`PYTHONPATH` = `src;tests;skills/pricebt-wire-external-library/example-service;skills/pricebt-wire-external-library/example-service/zeta_lib`; 4 positions, 19 daily snapshots):

```
layers off                     snapshots 19  uploads  19 = 19 snapshot + 0 derived  requests  178  position-points 76  requests per position-point   2.3
layers on, cadence eod         snapshots 19  uploads 109 = 19 snapshot + 90 derived  requests  970  position-points 76  requests per position-point  12.8
layers on, cadence every_n:5   snapshots 19  uploads  43 = 19 snapshot + 24 derived  requests  370  position-points 76  requests per position-point   4.9
layers on, cadence every_n:3   snapshots 19  uploads  59 = 19 snapshot + 40 derived  requests  510  position-points 76  requests per position-point   6.7
```

Read it: 19 = 19; 109 = 19 + 5 x 18 (18 intervals, every step within the 2-day lag: no cash world); 43 = 19 + 5 x 4 + 4, that is four intervals of five worlds and four cash worlds (the four steps are 4, 5, 5 and 4 business days because of the two holidays, all above the lag; per date the derived uploads were
1, 6, 6, 6, 5, the cash world being uploaded under the OLD snapshot's date); 59 = 19 + 5 x 7 + 5 (seven intervals, five of them 3 business days long: 3 is above the lag, the first (05-24 to 05-29, over a holiday) and the last (one day) are not). A counter that disagrees is a finding: a wrap that uploads eagerly or per position (`uploads` above `snapshots` with layers off), a shock that is
not content-addressed (`uploads` growing with the number of positions), or a layer flush that re-uploads the snapshot. The two counters are asserted in a test, not read by eye: `pricebt-wrap-and-pricer`, `tests-template.md`,
section "Service variant".
