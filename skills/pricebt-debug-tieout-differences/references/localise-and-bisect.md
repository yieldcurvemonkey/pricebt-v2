# Localise and bisect: from a failing row to one position, one day, one binding

All commands: repository root as working directory, PowerShell, `python` = the interpreter with pricebt's dependencies. Set once:
`$env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example"` (the example's directory is your adapter's parent directory in real use), `$ex = "skills/pricebt-wire-external-library/example"`.

## 1. One position, a short window (executed)

The report's `where` column and `## Largest offenders` give a position id and a timestamp. Rebuild the run around them. `--set` accepts YAML flow syntax and replaces a whole list, so the trigger's
actions can be replaced by ONE trade; `backtest.grid.start` / `end` cut the window. Here a stack with a wrong `dv01` sign, one 5Y payer of 1mm, three days:

```powershell
python -m pricebt tieout $ex/config/acme_tieout_base.yaml --stack reference=configs/adapters/refstack_swap.yaml --stack bad=$ex/config/mistakes/acme_swap_wrong_sign.yaml `
  --set 'strategy.triggers.0.actions=[{type: add_trade, priceables: {instrument: usd_sofr_ois, name: one, terms: {side: pay, maturity: 5Y, notional: 1e6, fixed_rate: par}}, trade_duration: 1m}]' `
  --set backtest.grid.start=2024-06-10 --set backtest.grid.end=2024-06-12
```

Printed (excerpt): `reference `reference`, other `bad`; 3 points, 1 positions`; `resolved_terms ... exact`; `dv01 | swap | 3 | 9.018e+02 | 2.000e+00 | ... | EXCEEDS | position=P000001, ts=2024-06-10 17:00:00-04:00`;
`gamma` and `rate` `exact`; exit 1 with `TIE-OUT FAILED: reference_vs_bad: L2.dv01; reference_vs_bad: L3.tay_delta; reference_vs_bad: L3.tay_delta/unit; reference_vs_bad: L3.tay_unexplained; reference_vs_bad: L3.tay_unexplained/unit` (five failing rows; each `L3` amount has its `/unit` twin).
The same numbers of one position now fit on one screen.
Use the base's own action shape (`add_trade` with `priceables: {instrument, name, terms}`); a swap that matures inside the window needs an explicit maturity date, a trade that pays needs a window that crosses the payment date.
`trades:` and `params:` blocks can be edited the same way. In Python, load the base with `api.load(path, sets=[...])`, edit the dict, and pass it to `run_tieout` as `base` (relative paths then resolve from the current directory).

## 2. Two probes on the audit trail (executed)

Both stacks run with the audit on keep `result.record.audit["marks"]` (columns `ts, position, quantity, pv, cash, financing, measure_<name>[.<bucket>]`), `["layers"]` (`ts, position, layer, unit, amount`) and `["inputs"]` (`ts, role, digest, stamp`).

```python
def first_divergence(a, b, column, *, rel=1e-6, floor=1.0):
    """The earliest (ts, position) at which two audited marks differ by more than `rel` (relative to max(|a|, floor)). a and b are BacktestResults run with the audit on.
    `column` is `pv`, `cash`, `financing`, `measure_<name>` or `measure_delta_ladder.<bucket>`."""
    m = a.record.audit["marks"].merge(b.record.audit["marks"], on=["position", "ts"], suffixes=("_ref", "_oth"))
    d = (m[f"{column}_ref"] - m[f"{column}_oth"]).abs() / m[f"{column}_ref"].abs().clip(lower=floor)
    rows = m[d > rel].sort_values(["ts", "position"])
    return rows[["position", "ts", f"{column}_ref", f"{column}_oth"]].head(3) if len(rows) else None


def ratio(a, b, column, *, floor=1e-9):
    """other / reference of one audited column where the reference is not ~0: a CONSTANT ratio names the fault (-1 a sign, 0.01 or 100 percent vs decimal, 1e4 bp, the notional or 1e6 / notional
    a per-unit or per-million factor, 0.99 = 1 - h^2 a finite-difference step on the `unexplained` layer); a ratio that drifts with time names a definition or a date."""
    m = a.record.audit["marks"].merge(b.record.audit["marks"], on=["position", "ts"], suffixes=("_ref", "_oth"))
    m = m[m[f"{column}_ref"].abs() > floor]
    return (m[f"{column}_oth"] / m[f"{column}_ref"]).describe()[["count", "min", "50%", "max"]].round(6).to_dict()
```

Usage on the worked example (`res = run_tieout(base, stacks, audit_measures=("dv01", "gamma", "rate"))`, then `ref, bad = res.results["reference"], res.results["wrong_sign"]`):

```python
from pathlib import Path
from pricebt.tieout import run_tieout

EX = Path("skills/pricebt-wire-external-library/example/config")
res = run_tieout(EX / "acme_tieout_base.yaml", {"reference": ["configs/adapters/refstack_swap.yaml"], "wrong_sign": [str(EX / "mistakes/acme_swap_wrong_sign.yaml")]}, audit_measures=("dv01", "gamma", "rate"))
ref, bad = res.results["reference"], res.results["wrong_sign"]
print("pv:", first_divergence(ref, bad, "pv"))
print("dv01 ratio:", ratio(ref, bad, "measure_dv01"))
print(first_divergence(ref, bad, "measure_dv01").to_string())
```

Printed: `pv: None` (the values agree), `dv01 ratio: {'count': 52.0, 'min': -1.0, '50%': -1.0, 'max': -1.0}` (a sign), and the first divergence at the first timestamp of every position with equal magnitude and opposite sign
(`-8182.11 vs 8182.11`). The same with `no_percent`: `measure_rate` ratio 0.01. A first divergence on day 1 for every position points at a definition or a unit; a first divergence later points at a date event (a payment, a fixing, a roll).
For the ladder pass `measure_delta_ladder.10Y` etc.; for a layer use `audit["layers"]` (`unit` per unit of position).

## 3. Bisect by stack overlay: one hypothesis, one change

A stack overlay `bind` merges over the Kit's default block BY NAME (and replaces the base's own `bind` block), so an overlay that rebinds ONE schema name is a one-change experiment. Keep two overlays that differ in exactly that binding
and tie the two stacks out against each other (`--reference` picks which is the reference; neither has to be the reference stack). The shipped case: `configs/adapters/rateslib_swap_recorded_dv01.yaml` rebinds only `dv01` to the analytic
definition (`tools/swap_suite_support.py:recorded_dv01`, a `function` target, hence the allow-list entry). This example needs the `rateslib` package (without it: ``OptionalDependencyError: pricebt.contrib.rateslib needs the optional extra `rateslib` (pip install rateslib)``; skip it,
the method is the same with your own two overlays, last paragraph of this section). Executed with rateslib installed (the window is cut to three months, about a minute):

```powershell
$env:PYTHONPATH = "src;tests;."
python -m pricebt tieout configs/suite/s11_buy_hold_10y.yaml --stack default=configs/adapters/rateslib_swap.yaml --stack recorded=configs/adapters/rateslib_swap_recorded_dv01.yaml `
  --set "registry.allow=[support, tools.swap_suite_support]" --set backtest.grid.end=2022-03-31 --set backtest.progress.show=false
```

Result: `L0` exact, `L1.pv`, `cash`, `L2.gamma`, `L2.rate`, `L3.delta`, `L3.unexplained`, `L4.equity` all `exact`; only `L2.dv01` `EXCEEDS` (about 7% at the end of the window, growing with the swap's age) and the two baseline layers that read it
(`L3.tay_delta`, `L3.tay_unexplained`) follow; `TIE-OUT FAILED: default_vs_recorded: L2.dv01; ...`. One binding changed, one row moved: the definition of `dv01` for a started swap is the whole difference (`worked-examples.md`, example 2).
For a foreign adapter: copy your overlay, change one binding (a `sign`, a `scale`, a different method, a different `tenors` list), run both as stacks, and read which rows move.

## 4. Bisect a convention or a data range with `--set` (the conventions block is shared)

The conventions block is one block for every stack, so `--set` flips it for BOTH: a difference that appears only under one convention names the convention your library handles differently. Executed on the worked example, each line a full tie-out:

```powershell
foreach ($s in "payment_lag_days=1", "frequency=semiannual", "day_count=act365f", "spot_lag_days=1") {
  python -m pricebt tieout $ex/config/acme_tieout_base.yaml --stack reference=configs/adapters/refstack_swap.yaml --stack acme=$ex/config/acme_swap.yaml --set "instruments.usd_sofr_ois.conventions.$s" | Select-String "^TIE-OUT|^error"
}
```

All four print `TIE-OUT PASSED`. A token that the library cannot express is refused before anything runs, naming the supported set: `error: [CFG-CONVENTION-UNSUPPORTED] convention stub='long_front' is in the vocabulary but the reference stack does not implement it; it implements ['short_front']`.
A token your adapter cannot honour must raise the same code (`pricebt-instrument-kit`): never map it silently to a neighbour.

To bisect the DAY: run the whole window, read the first divergence (`first_divergence`), then re-run with `--set backtest.grid.start=<a few days before it> --set backtest.grid.end=<that day>` and one position. Bisect the DATA the same way when a
divergence sits at a timestamp you suspect (one snapshot): the L0 rows say whether the inputs themselves differed on that day.

## 5. What to record when you find the cause

Write the cause as a mechanism, not a symptom (`the wrap loaded one holiday too many`, not `pv differs`), pin it with `regression-test-template.md` (a unit test at the lowest level plus the negative-control overlay), and, if it is
a real noise floor, declare it with its reason (`pricebt-conformance-and-tieout`, `skills/pricebt-conformance-and-tieout/references/tolerances.md`).
