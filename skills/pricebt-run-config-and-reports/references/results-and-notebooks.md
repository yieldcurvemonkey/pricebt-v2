# Results, reports and executed notebooks

Sources: `src/pricebt/api.py`, `src/pricebt/results/{result,attribution,reconcile,io,tearsheet,compare,report}.py`, `src/pricebt/config/loader.py` (`Built`), `tools/nb_build.py`, `src/pricebt/engine/progress.py`.
Every table below was printed by running the worked example (`acme_tieout_base.yaml` with the `acme_swap.yaml` overlay).

## 1. Python API

```python
from pathlib import Path
from pricebt import api

EX = Path("skills/pricebt-wire-external-library/example/config")
res = api.run(EX / "acme_tieout_base.yaml", stack=[EX / "acme_swap.yaml"], sets=["backtest.attribution.baseline=true", "backtest.audit={measures: [dv01, gamma, rate]}"])
print(res)                                         # BacktestResult('backtest', 19 points ... -> ..., pnl=..., 3 positions)
assert res.reconcile().ok
print(res.attribution(by="layer"))
```

* `api.load(path_or_dict, sets=(), interpolate_env=True) -> dict` (extends resolved, `--set` applied, `${ENV}` interpolated); `api.build(source, sets=(), stack=()) -> Built` (nothing runs); `api.run(source, sets=(), out=None, tearsheet=False, stack=()) -> BacktestResult`
  (writes parquet to `out` or `outputs.dir` when given, and `<dir>/tearsheet.html` with `tearsheet=True` or `outputs.tearsheet`). `source` and every overlay are a path or a dict; with a dict, relative paths resolve from the current directory.
* `Built` (`src/pricebt/config/loader.py`): `cfg` (the resolved dict), `config_hash`, `base_hash`, `grid`, `market`, `strategy`, `settings` (`EngineSettings`), `signals`, `instruments` (`{name: InstrumentSpec}` with `.bindings`, `.factory_path`, `.conventions`), `outputs`; `built.run()` returns a fresh `BacktestResult`; `built.engine()` a fresh `Engine`.
  `pricebt.config.loader.build(cfg, base_dir=..., stack=[...])` is the same as `api.build` on a loaded dict.
* Errors are `pricebt.errors.ConfigError` (`.code`, `.path`) at build and `PricebtError` subclasses at run (`MarketDataUnavailable`, `LookAheadError`, `MethodCallError`, `MeasureError`). With `backtest.on_error: record` (or `skip`) a `PricebtError` raised while marking or executing is a row in `res.errors` instead (`raise` is the default: fail fast); a library exception that is not a `PricebtError` still aborts the run under every mode
  (executed: `error: [RuntimeError] library blew up`, exit 2), and a `ConfigError` raised while a trade is built is recorded too, so a run can exit 0 with `n_fills 0` (`skills/pricebt-conformance-and-tieout/references/tieout-reference.md`, limit 1).

## 2. `BacktestResult`

| member | what it is (columns are the ones the example printed) |
|---|---|
| `equity` | frame indexed by timestamp: `equity, cash, tcost, positions_value, n_positions, step_pnl, interest_cum, financing_cum, flows_cum`, `measure_<name>` per recorded measure, `layer_<name>` per requested layer (`layer_carry, layer_roll, layer_delta, layer_convexity, layer_unexplained`), `signal_<name>` per recorded signal |
| `trades` | one row per fill: `ts, position, kind (open|close|resize), quantity, pv, cash, tcost, action, template, reason, instrument`, and `term_<name>` = the RESOLVED terms (`term_side, term_effective, term_maturity, term_notional, term_fixed_rate, term_direction`) |
| `positions` | one row per position id (index): `template, action, kind, tags, side, status, entry_ts, exit_ts, exit_reason, entry_quantity, quantity, entry_pv, exit_pv, hold_points, hold_seconds, hold_days, pnl_price, pnl_income, pnl_gross, tcost, pnl_net, trade_cash, flow_cash, financing`, `layer_<name>` |
| `layers`, `layers_by_position` | cumulative portfolio layer P&L (`carry, roll, delta, convexity, unexplained`, plus `tay_delta, tay_convexity, tay_unexplained` when `attribution.baseline` is on), and the same per position |
| `orders`, `events`, `errors` | `ts, type, action, template, quantity, final_ts`; `ts, kind, detail`; `ts, where, position, error` (`n_errors` is its length; the audit's swallowed measure failures have `where == "audit"`) |
| `measures`, `vectors` | the recorded scalar measures by name; `{name: frame}` of vector measures (`delta_ladder`: one column per bucket) |
| `manifest` | `instruments` (per instrument: `asset_class, conventions_digest, conventions, factory, roles, layers` as `<id>@<version>`), `name, n_points, n_positions, elapsed_seconds, fetches, memo_hits, fill_lag, settings, start, end, config_hash, grid` |
| `closed_trades()`, `open_positions()`, `trade_ledger()` | METHODS (call them): closed positions with holding period and net P&L; open ones; the gs_quant-vocabulary ledger |
| `summary_stats(labels="snake"|"gs")` | a Series: `start, end, basis, freq, periods_per_year, annualisation_source, n_periods, total_pnl, ...`, trade statistics, `total_tcost`, `n_fills`, `turnover`, `time_in_market`, `n_errors` |
| `attribution(by=...)` | `by` is one of `layer` (rows `carry, roll, delta, convexity, unexplained, transactions, cash_interest, total`, columns `pnl, share`), `baseline`, `component`, `day`, `position`, `template`, `action`, `kind`, `tag`. `by="baseline"` without the baseline on raises `ResultError: the run has no baseline decomposition: set `attribution.baseline: true` (EngineSettings.baseline) before running` |
| `reconcile()` | a `ReconcileReport`: `.ok`, `.checks` (each with `.name`, `.passed`, `.severity`). Checks include `finite`, `identity` (`equity = initial_capital + cash + tcost + positions_value`), `step_pnl`, `cash_rollforward`, `tcost_rollforward`, `positions_trades`, `layers`, `baseline_layers`, `errors` (a warning when errors were recorded) |
| `tearsheet(path)` | writes a self-contained `<path>.html` (verified; the CLI's `--tearsheet` writes `<out>/tearsheet.html`) |
| `to_parquet(dir)` / `BacktestResult.from_parquet(dir)` | `equity, trades, positions, layers, orders, events, errors` `.parquet`, `manifest.json`, `audit_inputs / audit_marks / audit_layers.parquet` when the audit was on, `vector_<name>.parquet`; the round trip returns an identical `equity` (verified). Needs pyarrow |
| `pricebt.results.compare({name: res, ...})`, `build_report(results, out_dir, title=..., intro=...)` | comparison table, P&L correlation, combined equity; `report.md` with `figures/` and `tables/*.csv` |

The audit tables live on `res.record.audit` (`inputs`, `marks`, `layers`), used by the tie-out (`pricebt-debug-tieout-differences`, `skills/pricebt-debug-tieout-differences/references/localise-and-bisect.md`). Ledger identity and units: pv, measures and layer `unit` are PER UNIT AS BUILT and the engine multiplies by the position `quantity`.

## 3. Files a run writes

`python -m pricebt run <cfg> --stack <overlay> --out <dir> [--tearsheet]` prints `summary_stats()` and writes the parquet set above (plus `tearsheet.html`). Without `--out`, `outputs.dir` of the config is used (relative to the CONFIG FILE); with neither, nothing is written.
`python -m pricebt tieout ... --out <dir>` writes `<ref>_vs_<name>.md/.html/.summary.parquet/.offenders.parquet` and `tieout.json` (`pricebt-conformance-and-tieout`).

## 4. Executed showcase notebooks (`tools/nb_build.py`)

`notebooks/src/showcase_swap_book.py` is the model: percent-format Python that `tools/nb_build.py` turns into an EXECUTED `notebooks/showcase_swap_book.ipynb`.

Source format (`nb_build.parse`, verified on the showcase source): a line `# %% [markdown]` starts a markdown cell whose lines are `# `-prefixed comment text; a line `# %%` starts a code cell; anything before the first marker is dropped.

```powershell
python tools/nb_build.py                                          # every notebooks/src/*.py
python tools/nb_build.py notebooks/src/showcase_swap_book.py      # one source
python tools/nb_build.py --no-exec                                # write the .ipynb without running (structure check)
python tools/nb_build.py --timeout 3600                           # per-cell seconds (default 1800)
```

Facts about the tool (read from `tools/nb_build.py`, then exercised in-process):

* the output path is HARD-WIRED to `<project root>/notebooks/<source stem>.ipynb` (no `--out`); building overwrites an existing notebook of that stem, even with `--no-exec`, and it writes the notebook EVEN WHEN A CELL FAILS (partial outputs, exit code 1, `FAILED <name>: <error tail>` printed): keep the last good notebook under version control;
* it executes with the `python3` kernel (needs `nbformat`, `nbclient` and `ipykernel`), working directory = the project root, `PYTHONPATH = <root>/src;<root>;<your existing PYTHONPATH>`. `tests/` is NOT on that path (the showcase does `sys.path.insert(0, "tests")` in its first cell), and neither is
  the directory of an adapter that lives elsewhere: export it in `PYTHONPATH` before calling the tool, or insert it in the first cell;
* exit code 0 only if every notebook executed; a cell that raises stops that notebook (`allow_errors=False`).

A minimal source (executed in-process with the same `parse` and `NotebookClient` calls, without touching `notebooks/`; about 8 s; the worked example's directory must be on `PYTHONPATH`):

```python
# %% [markdown]
# # Mini showcase
# One backtest of the worked example under the acme stack.

# %%
from pathlib import Path
from pricebt import api

EX = Path("skills/pricebt-wire-external-library/example/config")
res = api.run(EX / "acme_tieout_base.yaml", stack=[EX / "acme_swap.yaml"], sets=["backtest.progress.show=true", "backtest.progress.desc=MINI"])
print(res)
assert res.reconcile().ok
res.equity[["equity", "layer_delta"]].tail(3)
```

**The progress bar in a notebook.** A run shows exactly one tqdm bar (`backtest.progress.show`, default true). Inside a notebook KERNEL it is drawn into ONE display output that every refresh replaces (`update_display_data`; `_NotebookSink` in `src/pricebt/engine/progress.py`), because a
frontend that does not fold carriage returns across messages (VS Code) would otherwise print one line per step; a saved notebook keeps a single final bar. Executed: the cell's outputs were `[('display_data',), ('stream', 'stdout'), ('execute_result',)]` with one display output whose text was
`MINI: 100%|...| 19/19 [00:01<00:00, 15.22step/s]`. Rules: leave `progress.show` on (or set it false to hide it) and do not draw your own tqdm bars in the same cell (`quiet_tqdm()` disables every other bar created during a run); one run per cell keeps one bar per cell;
`tests/test_progress_notebook.py` enforces it. On Windows the kernel prints a harmless `RuntimeWarning: Proactor event loop does not implement add_reader ...` (zmq).

**Structure to copy for your library.** Section 1: the instrument spec, the Kit's `SWAP_BIND` printed with `yaml.safe_dump`, one overriding binding; section 2-6: run and read the results; section 7: an assertion cell (`reconcile().ok`, expected bounds) so a wrong number fails the build; section 8: the same base
under your stack and the reference stack with `run_tieout` (base and overlays as dicts, `tieout.tolerances` with reasons, `audit_measures=("dv01", "gamma", "rate", "delta_ladder")`), a table of the non-`exact` rows, and assertions (`tie.passed`, every L0 row `exact`, no `exceeds`/`input`/`structure`, every declared tolerance has a reason).
The showcase's `## 8` cells are the pattern.
