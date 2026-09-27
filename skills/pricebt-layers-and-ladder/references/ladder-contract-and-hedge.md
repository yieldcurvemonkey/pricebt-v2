# The delta ladder: contract, reducer, the tie-out floor, and the hedge that consumes it

## 1. The contract (`swap.yaml`, `measures.delta_ladder`; `src/pricebt/contracts/evaluate.py`, `check_ladder`)

* Returns `dict[str, float]`. Keys are UPPER-CASE tenor strings `<int><D|W|M|Y>` (`TENOR = \d+[DWMY]` in `src/pricebt/contracts/binding.py`): `"3M"`, `"2Y"`, `"10Y"`. Values are currency per +1bp of THAT bucket's
  rate, for one unit as built with its notional, holder-signed (a payer has positive entries).
* The values sum to the parallel `dv01` for a STARTED swap (exactly, to 1e-12) and for an unstarted swap struck at par (to 1e-4 to 1e-3): `dv01` of an unstarted swap is the analytic annuity, which for a swap struck
  off the market differs from the ladder sum by percents (`dv01-one-definition.md`: same `tenors`, one definition per state).
* The binding declares `keys:` (the exact set the result must carry) and passes the same `tenors:` list as an argument; the result must have exactly those keys, no more, no fewer. A bucket the
  position does not touch is `0.0`, not absent.
* A vector measure is recognised by the SCHEMA's return type (`returns: "dict[str, float]"`), never by a name. In `backtest.measures` a ladder is recorded as its SUM (`scalar_of`); its buckets
  are recorded with `backtest.vector_measures: [delta_ladder]` (`BacktestResult.vectors["delta_ladder"]`, one column per bucket). The audit (`backtest.audit`) records one column per bucket.

The four messages of the contract, each provoked on purpose on the worked example (real text):

```
MeasureError: measure 'delta_ladder': key '3m' is not a canonical tenor (upper-case <int><D|W|M|Y>, e.g. '3M', '10Y')
MeasureError: measure 'delta_ladder' must return exactly the bound tenors ['3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '15Y', '20Y', '30Y']: missing ['30Y']; extra []
MeasureError: measure 'delta_ladder' must return a dict[str, float] keyed by tenor, got Series (bind a reducer such as series_to_tenor_dict)
ConfigError: [LAYER] layers need a previous pricer (ctx.prev_pricer)
```

(a reducer that keeps the library's lower-case labels; one that drops a bucket; no reducer on a library that returns a pandas Series; a layer asked at the first point of a run, where there is no
previous pricer). In a run, a failing AUDIT measure is always a NaN cell plus one error row (`where: audit`); a failing `vector_measures` entry aborts the run under `backtest.on_error: raise` (the default) and under `record` is one error row and a NaN row of the vector, never a zero ladder.

## 2. What a bucket is: the risk variable

The shipped adapters bucket in PAR-RATE space: a risk curve is bootstrapped so that a set of pillar par swaps (default `3M 6M 1Y 2Y 3Y 5Y 7Y 10Y 15Y 20Y 30Y`) reprice at the dense curve's par
rates; each pillar's par rate is bumped by +-0.5bp (`LADDER_BUMP` in `src/pricebt/testing/refstack.py`), the curve re-bootstrapped and the position revalued; the bucket is the central difference per bp. `gamma`
is the second difference of the same variable for a parallel +-1bp move of all pillars (`PARALLEL_BUMP`). The zero-rate variant (`dv01_zero`, `gamma_zero`, `acme_zero_dv01`) is a DIFFERENT
variable (2 to 3 percent apart on a 5Y to 10Y swap) and lives in `Kit.extra` as an extension measure, never under the schema names.

| the library gives | do this |
|---|---|
| key-rate / bucketed par-rate risk | map it: reducer for labels, `scale`/`sign` for units and view, code for the notional basis |
| risk per input quote (a bump list) | one bucket per pillar quote; make sure the pillar tenors are the ones you declare in `keys` |
| only a parallel dv01 | bump each pillar yourself: build the risk curve from the snapshot (`pricebt-wrap-and-pricer`), or report a one-bucket ladder `{"<tenor>": dv01}` ONLY when you can say why |
| risk per zero-rate node | it is a different variable: do NOT bind it to `delta_ladder`; put it in `Kit.extra` |

## 3. The reducer: the library's shape -> the contract's

Reducers turn the method's raw result into what `keys` promises. Shipped (`REDUCERS` in `src/pricebt/contracts/binding.py`): `float`, `real`, `identity`, `dict_of_floats`, `series_to_tenor_dict` (upper-cases labels, checks
the grammar, refuses duplicates). Yours are registered with `register_reducer(name, fn)`; the same name for a DIFFERENT function is `[CFG-REDUCER] reducer 'x' is already registered`, which is what an
`importlib.reload` of the adapter module triggers. The worked example's `ladder_to_tenor_dict` (`skills/pricebt-wire-external-library/example/acme_adapter/swap.py`) drops the library's extra `on` bucket ONLY when it is 0 and refuses
otherwise (`pricebt's ladder has no such bucket, so it cannot be dropped silently`): dropping a bucket that carries risk would break `sum(ladder) == dv01`.

Post-processing is `sign * (scale * reduce(x) + offset)`, applied to every value of a dict. What a binding CANNOT do: `scale` is a constant (it cannot read the trade's notional, so a per-million basis
is code), and `sign`/`scale` cannot touch a `Valuation` (so `value` converts in code).

## 4. The tie-out floor and what it hides (executed)

`L2.delta_ladder` is compared per bucket, per unit, as `|a - b| / max(|reference|, floor) <= rel` with `rel = 1e-3` and `floor = 100` (`DEFAULT_TOLERANCES` in `src/pricebt/tieout/tolerances.py`;
`_rel` in `src/pricebt/tieout/compare.py`). Solver noise does not shrink with the bucket, so small buckets are compared absolutely: a bucket below 100 currency per unit is `noise` while its difference stays
under `rel x floor = 0.1` currency. The CLI `pricebt tieout` does not compare the ladder at all (it audits `dv01`, `gamma`, `rate`: `DEFAULT_AUDIT_MEASURES` in `src/pricebt/tieout/runner.py`); only
`run_tieout(..., audit_measures=("dv01", "gamma", "rate", "delta_ladder"))` does.

To see exactly what the floor hides, a ladder reducer wrong by the same factor in a small bucket (`2Y`, about 2.3 per unit on the example base) and a large one (`5Y`, about 2,250) was tied out against
the reference stack (save the first block as `ladder_mut.py` and the second next to it, both in the repository root, and run the second from there):

```python
"""a ladder reducer that is WRONG by a factor in two buckets (a small one, 2Y, and a large one, 5Y); the factor comes from the environment"""
import importlib
import os

from pricebt.contracts.binding import register_reducer

SW = importlib.import_module("acme_adapter.swap")  # NOT `import acme_adapter.swap as SW`: the package attribute `swap` is the Kit, so that yields the Kit and SW.ladder_to_tenor_dict fails


def scaled(series):
    d = SW.ladder_to_tenor_dict(series)
    f = float(os.environ.get("LADDER_FACTOR", "1"))
    d["2Y"] *= f
    d["5Y"] *= f
    return d


register_reducer("ladder_mut_scaled", scaled)
```

```python
"""What the L2.delta_ladder floor (rel 1e-3, floor 100, per bucket, per unit) hides: the same wrong factor in a small bucket (2Y) and a large one (5Y)."""
import importlib
import os

import ladder_mut  # noqa: F401  registers the reducer
import acme_adapter as A
from pricebt.tieout import run_tieout

EX = "skills/pricebt-wire-external-library/example/config/"
SW = importlib.import_module("acme_adapter.swap")  # the MODULE (see the previous block): `acme_adapter.swap` as an attribute is the Kit
bind = {"delta_ladder": {**SW.SWAP_BIND["delta_ladder"], "reduce": "ladder_mut_scaled"}}
mut_overlay = {"instruments": {"usd_sofr_ois": {"factory": "acme_adapter:swap", "bind": bind}}, "market": {"pricers": {"primary": {"wrap": "acme_adapter:wrap"}}}}
for f in ("1.0", "1.02", "1.05", "1.3", "2.0"):
    os.environ["LADDER_FACTOR"] = f
    res = run_tieout(EX + "acme_tieout_base.yaml", {"reference": ["configs/adapters/refstack_swap.yaml"], "acme": [mut_overlay]}, audit_measures=("dv01", "gamma", "rate", "delta_ladder"))
    rep = res.reports["reference_vs_acme"]
    rows = {r.quantity: r for r in rep.rows if r.quantity in ("delta_ladder.2Y", "delta_ladder.5Y")}
    print(f"factor {f:<4} " + "   ".join(f"{q}: {r.status:<7} max_abs {r.max_abs:9.3g} max_rel {r.max_rel:8.3g}" for q, r in rows.items()) + f"   report passed: {rep.passed}")
```

```
factor 1.0  delta_ladder.2Y: noise   max_abs  2.18e-11 max_rel 2.18e-13   delta_ladder.5Y: noise   max_abs     2e-11 max_rel    2e-13   report passed: True
factor 1.02 delta_ladder.2Y: noise   max_abs    0.0469 max_rel 0.000469   delta_ladder.5Y: exceeds max_abs        45 max_rel     0.02   report passed: False
factor 1.05 delta_ladder.2Y: exceeds max_abs     0.117 max_rel  0.00117   delta_ladder.5Y: exceeds max_abs       113 max_rel     0.05   report passed: False
factor 1.3  delta_ladder.2Y: exceeds max_abs     0.704 max_rel  0.00704   delta_ladder.5Y: exceeds max_abs       675 max_rel      0.3   report passed: False
factor 2.0  delta_ladder.2Y: exceeds max_abs      2.35 max_rel   0.0235   delta_ladder.5Y: exceeds max_abs  2.25e+03 max_rel        1   report passed: False
```

Read it: a +2 percent error in the 2Y bucket is `noise` (0.047 currency, under 0.1), a +5 percent error in the same bucket is `exceeds`, and the 5Y bucket is caught at +2 percent. So the floor
hides errors below 0.1 currency per unit in EVERY bucket, which is a large RELATIVE error only for the small buckets; it does not make a bucket immune. Consequences: (1) the ladder tie-out has
real power only in the buckets that carry risk in your base config: choose trades that load the buckets you care about; (2) compare small buckets to the reference DIRECTLY as well, with an
absolute tolerance you can defend; (3) if your library agrees far better than the shipped floor, declare a tighter `L2.delta_ladder` floor with a `reason` (`tasks/tolerance_ledger.yaml`); the floors assume
templates sized like the shipped ones (a 1mm swap): a run of much smaller units MUST declare its own or a wrong ladder whose every bucket is below the floor is `noise`.

## 5. The ladder-hedge action (`src/pricebt/strategy/actions.py`, `HedgeAction`; `src/pricebt/strategy/sizing.py`, `ladder_hedge_quantities`)

`{type: hedge, risk: delta_ladder, priceables: [...], target: {...}}` measures the book's ladder (`view.measure_vector`), measures ONE UNIT ladder per hedge leg (`view.measure_of_vector`, at the
current market), and solves one quantity per leg by weighted least squares so that `book + sum_j q_j U_j = target`. Options: `target` (a bucket -> level mapping; `0`/absent is flat; any other scalar is a
`StrategyError`), `bucket_weights` (per bucket, enter the loss squared), `ridge`, `risk_percentage`, `min_trade_risk` (a leg whose own trade risk is at most this is dropped), `mode: resize` (one live
position per leg), `on_zero_hedge_risk: raise | skip`. Your ladder is consumed exactly as returned: a wrong bucket is a wrong hedge, and nothing downstream can tell.

The sizing function on plain dicts (executed; `after` is the ladder left over, measured against the target):

```python
"""ladder_hedge_quantities on plain dicts: what the ladder-hedge action does with the ladders your adapter returns."""
from pricebt.errors import SizingError
from pricebt.strategy.sizing import ladder_hedge_quantities

# per UNIT of each hedge leg (a receive-fixed 1mm swap): currency per +1bp of the bucket's rate
UNITS = [{"2Y": -190.0}, {"5Y": -470.0}, {"7Y": -650.0}, {"10Y": -830.0}]  # a par swap of a pillar tenor has (nearly) all its risk in that pillar
book = {"2Y": 3800.0, "5Y": 9400.0, "7Y": 6500.0, "10Y": 160.0}  # the book's net ladder: a payer book

r = ladder_hedge_quantities(book, UNITS)
print("flat target       quantities", [round(q, 2) for q in r.quantities], "after", {k: round(v, 2) for k, v in r.after.items()}, "skip", r.skip)
r = ladder_hedge_quantities(book, UNITS, target={"10Y": 1000.0})
print("target 10Y=1000   quantities", [round(q, 2) for q in r.quantities], "after", {k: round(v, 2) for k, v in r.after.items()})
r = ladder_hedge_quantities(book, UNITS[:2])
print("two legs only     quantities", [round(q, 2) for q in r.quantities], "after", {k: round(v, 2) for k, v in r.after.items()}, " <- the 7Y and 10Y risk the hedge set cannot reach stays")
r = ladder_hedge_quantities(book, UNITS, min_trade_risk=1000.0)
print("min_trade_risk 1000 quantities", [round(q, 2) for q in r.quantities], "after", {k: round(v, 2) for k, v in r.after.items()}, " <- legs whose own trade risk is below it are dropped")
for label, kw in (("typo in the target", dict(target={"10y": 1.0})), ("dead hedge set", dict())):
    try:
        ladder_hedge_quantities(book, [{"2Y": 0.0}] if label == "dead hedge set" else UNITS, **kw)
    except SizingError as e:
        print(f"{label}: SizingError: {e}")
```

```
flat target       quantities [20.0, 20.0, 10.0, 0.19] after {'2Y': 0.0, '5Y': 0.0, '7Y': 0.0, '10Y': 0.0} skip None
target 10Y=1000   quantities [20.0, 20.0, 10.0, -1.01] after {'2Y': 0.0, '5Y': 0.0, '7Y': 0.0, '10Y': 0.0}
two legs only     quantities [20.0, 20.0] after {'2Y': 0.0, '5Y': 0.0, '7Y': 6500.0, '10Y': 160.0}  <- the 7Y and 10Y risk the hedge set cannot reach stays
min_trade_risk 1000 quantities [20.0, 20.0, 10.0, 0.0] after {'2Y': 0.0, '5Y': 0.0, '7Y': 0.0, '10Y': 160.0}  <- legs whose own trade risk is below it are dropped
typo in the target: SizingError: target buckets ['10y'] are touched by no hedge leg and no position (a typo? buckets in play: ['10Y', '2Y', '5Y', '7Y'])
dead hedge set: SizingError: the hedge instruments have (numerically) zero risk in every bucket of the hedged ladder
```

The action through a config, on the worked example's adapter and on the reference stack, from the same file (executed; the trades and the recorded ladders are identical to the last digit printed). Save the YAML
as `ladder_hedge.yaml` in the REPOSITORY ROOT (the paths of the script are relative to the working directory, or pass absolute paths). The lines marked `LIB` are the ones to change for your adapter: the `registry.allow`
entry of the YAML and the overlay `OV` of the script.

```yaml
name: ladder_hedge_demo
doc: >
  A 6Y payer of 20mm is held from the first day. Once a month a ladder hedge measures the book's delta ladder and trades receive-fixed swaps of the hedge set until the
  ladder is flat, or as flat as the hedge set allows. The library is a stack overlay: the same config runs on the reference stack and on acmelib.
registry: {allow: [acme_adapter]}   # LIB: your adapter package
backtest:
  tz: America/New_York
  date_policy: close
  grid: {start: 2024-05-24, end: 2024-06-28, freq: 1b}
  progress: {show: false}
  attribution: {layers: [carry, roll, delta, convexity], cadence: eod}
  measures: [dv01]
  vector_measures: [delta_ladder]
market:
  mdps:
    rates: {type: "pricebt.testing.synthetic:SyntheticMarket", kwargs: {start: 2024-01-02, end: 2024-12-31, seed: 7}}
  pricers:
    primary: {mdp: rates, wrap: "pricebt.testing.refstack:wrap"}
instruments:
  usd_sofr_ois:
    asset_class: swap
    factory: "pricebt.testing.refstack:swap"
    conventions: {calendar: usd_fed, spot_lag_days: 2, day_count: act360, frequency: annual, business_day_convention: modified_following, payment_lag_days: 2,
                  compounding: daily_compounded, stub: short_front, end_of_month: false, fixing_lag_days: 1, time_accrual: lump}
trades:
  book: {instrument: usd_sofr_ois, terms: {side: pay, maturity: 6Y, notional: 2e7, fixed_rate: par}}
  h5y: {instrument: usd_sofr_ois, terms: {side: receive, maturity: 5Y, notional: 1e6, fixed_rate: par}}
  h7y: {instrument: usd_sofr_ois, terms: {side: receive, maturity: 7Y, notional: 1e6, fixed_rate: par}}
strategy:
  initial_portfolio: [{template: book, quantity: 1}]
  triggers:
    - type: periodic
      frequency: 1m
      actions:
        - {type: hedge, name: lh, risk: delta_ladder, priceables: [h5y, h7y], min_trade_risk: 1.0}
```

```python
import pandas as pd

from pricebt import api

pd.set_option("display.width", 200)
CFG = "ladder_hedge.yaml"   # the YAML block above, saved in the repository root (run from there)
OV = "skills/pricebt-wire-external-library/example/config/acme_swap.yaml"   # LIB: your stack overlay

for label, stack in (("reference stack", []), ("acmelib", [OV])):
    res = api.run(CFG, stack=stack)
    v = res.vectors["delta_ladder"]
    print(f"--- {label}: {len(res.trades)} trades, errors {res.n_errors}, reconcile ok {res.reconcile().ok}")
    print(res.trades[["ts", "kind", "template", "quantity"]].to_string(index=False))
    cols = [c for c in v.columns if abs(v[c]).max() > 1e-6]
    print("book delta ladder (currency per +1bp), buckets ever non-zero:", cols)
    print(v[cols].iloc[[0, 1, len(v) // 2, -1]].round(2).to_string())
```

Output for the `acmelib` stack (the reference stack, `stack=[]`, printed the same rows; run with `PYTHONPATH="src;tests;skills/pricebt-wire-external-library/example"`):

```
--- acmelib: 5 trades, errors 0, reconcile ok True
                       ts kind template  quantity
2024-05-24 17:00:00-04:00 open     book  1.000000
2024-05-24 17:00:00-04:00 open      h5y  9.799823
2024-05-24 17:00:00-04:00 open      h7y 10.200319
2024-06-24 17:00:00-04:00 open      h5y  0.774060
2024-06-24 17:00:00-04:00 open      h7y -0.405889
book delta ladder (currency per +1bp), buckets ever non-zero: ['3M', '6M', '1Y', '2Y', '3Y', '5Y', '7Y', '10Y']
                             3M    6M    1Y    2Y      3Y      5Y      7Y  10Y
ts                                                                            
2024-05-24 17:00:00-04:00  0.00  0.00 -0.00 -0.01   -0.02   -0.00    0.00 -0.0
2024-05-27 17:00:00-04:00 -0.00 -0.00 -0.00 -0.01   -3.58   12.57   -9.26 -0.0
2024-06-12 17:00:00-04:00 -0.00 -0.00 -0.01 -0.01  -60.42  203.03 -143.18  0.0
2024-06-28 17:00:00-04:00 -0.41 -0.02 -0.01 -0.05 -127.01   80.13  -58.05  0.0
```

The book is a 6Y payer, so its risk sits in the 5Y and 7Y buckets and the hedge set `{5Y, 7Y}` flattens it at the first firing (5 trades, 0 errors, `reconcile().ok`); by the end of the window the
aged position has drifted into the 3Y bucket, which no hedge leg reaches: `docs/DESIGN.md` section 15 lists it as a limitation (a hedge set that does not span the book's buckets leaves a residual).
For a real run choose a hedge set that spans the buckets an aged book drifts into.

## 6. Checks (each was executed)

| check | command | expected |
|---|---|---|
| the ladder contract | the conformance kit (`pricebt-conformance-and-tieout`) and `evaluate_measure(spec, "delta_ladder", env)` in the check of `dv01-one-definition.md` | no `MeasureError`; `sum(ladder)` equals `dv01` for a started swap to 1e-12 |
| the ladder through a tie-out | `run_tieout(..., audit_measures=("dv01","gamma","rate","delta_ladder"))` | rows `L2.delta_ladder.<bucket>`: `exact` or `noise`, none `structure` |
| the hedge consumes it | the YAML above with your stack overlay | the same trades as the reference stack |
