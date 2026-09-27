---
name: pricebt-run-config-and-reports
description: Runs pricebt end to end. Covers a base config and a stack overlay, registry.allow for an external package, YAML 1.2 and --set, the CLI (validate, describe, run, tieout), the Python api, BacktestResult and executed notebooks (tools/nb_build.py). Use when you write or debug a config, run an adapter, or read results.
---

# pricebt-run-config-and-reports

## Purpose

Everything you need to RUN pricebt with an adapter: the anatomy of a config, the overlay that swaps the library, the errors the loader gives (with their codes), the four CLI verbs and the Python API, the result tables, and an executed showcase notebook.
`references/recipe-minimal-end-to-end.md` is a copy-and-run recipe (base + overlay + `validate`, `run`, `tieout`), executed on the worked example.

## Prerequisites

* The repository root as working directory and the Python interpreter that has pricebt's dependencies installed (pandas, numpy, pyyaml, pyarrow, tqdm; notebooks also need `nbformat`, `nbclient`, `ipykernel`; the tearsheet needs matplotlib). Check it:
  `python -c "import pandas, pyarrow, yaml, tqdm"` must print nothing. Commands below say `python`: it must be THAT interpreter (a machine can have several, with different library versions).
* `PYTHONPATH=src;tests;<the directory that CONTAINS your adapter package>` (`:` on Linux). pricebt never adds a path: a config can import your package only if it is importable. `python -m pricebt` also puts the working directory on `sys.path` (an adapter at the repository root imports without help); a
  script run as `python tools/<script>.py` gets only its own directory, so it needs the adapter's parent in `PYTHONPATH` (`.` for the repository root) ([CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import)).
* An adapter package (`pricebt-wire-external-library`, `pricebt-wrap-and-pricer`, `pricebt-instrument-kit`) and, for real data, a snapshot provider (`pricebt-market-data-snapshots`).

## Steps

1. **Start from the recipe.** Copy the base and the overlay of `references/recipe-minimal-end-to-end.md` into a directory of your own (not a `config/` folder in the pricebt root; `outputs.dir` is relative to the base file, so give `--out` to every `run`); the worked example's own files are `skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml` and `acme_swap.yaml`. The library-free reference stack is the base's default library; your library is an overlay.
2. **Know the anatomy** (`references/config-anatomy.md`): top-level keys `name doc meta extends registry backtest market instruments trades params signals strategy outputs tieout`; `backtest` (`name tz calendar session date_policy grid initial_capital fill_lag fill_price exit_policy on_error progress attribution measures vector_measures record_signals cash_accrual audit`; `name` is the run label, default the top-level `name`);
   `market.mdps.<n>: {type, kwargs, time, request}` (`time` only on a provider that takes a `time` or `mapping` parameter: `SyntheticMarket` does not, `config-anatomy.md` section 4) and `market.pricers.<role>: {mdp, request, wrap}`; `instruments.<n>: {asset_class, factory, conventions, bind, pricer, pricers, layers, params, doc}`; actions carry the TERMS of each trade (`side maturity notional fixed_rate`).
3. **Write the base so a mistake can show.** Two holidays inside the window (a holiday-free base cannot detect a wrong calendar; build the synthetic market's calendar from the same list with `{$call: "pricebt.timeutil:Calendar", kwargs: {holidays: *holidays}}` and a YAML anchor), both directions with different notionals, at least one short trade whose maturity or coupon falls INSIDE the window (`pay_short` in the recipe: without a payment `L1.cash` compares zeros and the payment date, lag and cash sweep are never tied out),
   the layers you want compared in `backtest.attribution.layers`, a complete conventions block (no implicit defaults), and `registry.allow: [<your package>]` (ONLY the base may set it).
4. **Write the overlay** with `instruments.<n>.factory` and `market.pricers.<role>.wrap` (and, optionally, `bind:` to override one binding by name). Nothing else is allowed (`[CFG-STACK]`): conventions, terms, strategy and providers are shared by every stack.
5. **YAML 1.2 and `--set`** (`references/yaml-and-set.md`): `1e7` is a float; only `true`/`false` are booleans (`no`, `off` are strings); a bare `2024-05-27` is a date; duplicate keys are errors; `${VAR:-default}` interpolates; `extends:` merges mappings and REPLACES lists.
   `--set a.b.0.c=<yaml>` sets a dotted path (numeric parts index lists; flow YAML replaces whole mappings and lists); a key that contains a dot cannot be addressed, so set the whole mapping (`--set 'tieout.tolerances={"L2.dv01": {rel: 1.0e-3, reason: "..."}}'`).
6. **`validate` then `describe` then `--dry-run` then a short `run`.** `validate` builds everything and runs nothing; it does not call a factory, so a conventions mistake shows only at the first trade. `describe` prints `config_hash`, `base_hash` (identical for every stack of one base), the grid, roles and instruments.
   `run --dry-run` also fetches the first pricer (your `wrap` runs once). A short window (`--set backtest.grid.end=<3 days later> --out <scratch dir>`) exercises the factory, the bindings and the conventions; read `n_fills` (not 0) and `n_errors` (0) in the summary: under `on_error: record` a failed trade build is a recorded error and the exit code stays 0.
7. **Run.** `python -m pricebt run <base> --stack <overlay> [--stack <overlay 2> ...] [--set k=v] [--out DIR] [--tearsheet] [--no-progress] [--dry-run]` (`validate` and `run` take one overlay per repeated `--stack`, applied in order; a comma list is `[CFG-FILE] config file not found`; `tieout` differs, step 8) prints `summary_stats()` and writes the parquet set (+ `tearsheet.html`); `--out` overrides `outputs.dir`, which is relative to the config FILE.
   Python: `from pricebt import api; res = api.run(base, stack=[overlay], sets=[...])` (`api.load`, `api.build`, `api.run`; base and overlays are paths or dicts).
8. **Tie out.** `python -m pricebt tieout <base> --stack NAME=overlay.yaml ... [--reference NAME] [--out DIR] [--set k=v] [--no-selftest] [--top N]`: exit 0 passed, 1 a comparison failed, 2 any error. Here `--stack` carries a NAME, repeats once per stack, and a comma joins several overlays of ONE stack: `--stack reference=configs/adapters/refstack_swap.yaml,<second overlay>` (the same NAME twice is `[CLI] --stack 'reference' is given twice`;
   [CE-STACK-SYNTAX](../pricebt-wire-external-library/references/common-errors.md#ce-stack-syntax)). The ladder needs the Python API (`pricebt-conformance-and-tieout`, [CE-LADDER-CLI](../pricebt-wire-external-library/references/common-errors.md#ce-ladder-cli)).
9. **Read the results** (`references/results-and-notebooks.md`): `res.equity`, `trades` (with the resolved `term_*`), `positions`, `layers`, `errors`, `manifest`; methods `closed_trades()`, `open_positions()`, `trade_ledger()`; `summary_stats()`, `attribution(by=...)`, `reconcile()` (must be `.ok`), `tearsheet(path)`, `to_parquet(dir)` / `from_parquet(dir)`.
   Values are per unit as built; the engine multiplies by the position quantity.
10. **Build a notebook** from a percent-format source in `notebooks/src/` (`# %% [markdown]` / `# %%`) with `python tools/nb_build.py notebooks/src/<name>.py`: it executes in the project root and writes `notebooks/<name>.ipynb` (path hard-wired; written even if a cell fails). One tqdm bar per run is kept as ONE display output in a kernel: do not add your own bars.
    Copy the structure of `notebooks/src/showcase_swap_book.py` (section 8 is the tie-out).
11. **Keep it honest.** Every stack of one base has the same `base_hash`; record `config_hash` with any result you quote; put widened tolerances in the base with a reason (`pricebt-conformance-and-tieout`; the loader does not require the reason, your test must).

## Checks

| command | expected |
|---|---|
| `python -m pricebt validate <base> --stack <overlay>` | `OK`, exit 0 |
| `python -m pricebt describe <base> [--stack <overlay>]` | `base_hash` identical with and without the overlay |
| `python -m pricebt run <base> --stack <overlay> --dry-run` | `OK: first pricer <YourPricer> at <ts> (reference_date <date>)` |
| `python -m pricebt run <base> --stack <overlay> --out <dir> --tearsheet` | the summary statistics; `equity trades positions layers orders events errors` parquet, `manifest.json`, `tearsheet.html` in `<dir>` |
| `python -m pricebt tieout <base> --stack reference=configs/adapters/refstack_swap.yaml --stack mylib=<overlay>` | `harness self-test: passed`, `TIE-OUT PASSED`, exit 0 |
| `res.reconcile().ok` in Python | `True` |
| `python tools/nb_build.py --no-exec notebooks/src/<name>.py` | `ok   <name>.py -> notebooks/<name>.ipynb  (<n> code cells, ...s, not executed)` (writes the file) |

## Common failures (real messages)

* `[CFG-ALLOW] ... module 'acme_adapter' is not under an allowed prefix ['pricebt']`: [CE-ALLOW](../pricebt-wire-external-library/references/common-errors.md#ce-allow); `[CFG-STACK] registry: a stack may not set 'registry'`: [CE-STACK-REGISTRY](../pricebt-wire-external-library/references/common-errors.md#ce-stack-registry).
* `[CFG-IMPORT] ... cannot import 'acme_adapter': No module named 'acme_adapter'`: [CE-IMPORT](../pricebt-wire-external-library/references/common-errors.md#ce-import).
* `[CFG-FILE] config file not found: a.yaml,b.yaml` (a comma list on `validate` or `run`) or `[CLI] --stack 'x' is given twice` (`tieout`): [CE-STACK-SYNTAX](../pricebt-wire-external-library/references/common-errors.md#ce-stack-syntax).
* `[CFG-UNKNOWN-KEY] backtest.gird: unknown key 'gird' (did you mean 'grid'?)`, `[CFG-REF] backtest.measures: measures ['dv0l'] not defined by any instrument (known: [...])`, `[CFG-TYPE] backtest.progress.show: ... must be true or false, got 'no'`: the loader names the path and the near miss; YAML 1.2 makes `no` a string.
* `validate` says `OK` but `run` fails with `[CFG-CONVENTION] missing convention keys [...]: there are no implicit defaults` or `[CFG-CONVENTION] convention 'day_count' must be one of [...], got 'banana'`: conventions are checked when the first trade is built. Use a short `run`.
* `[CFG-CONVENTION-UNSUPPORTED] convention stub='long_front' is in the vocabulary but the reference stack does not implement it; it implements ['short_front']`: the shared block is asked for something a stack cannot do; never let an adapter map it silently.
* `[CFG-TIEOUT] tieout.tolerances.L2: malformed tolerance key 'L2'` after `--set tieout.tolerances.L2.dv01.rel=...`: `--set` splits on every dot. Set the whole mapping.
* `[CFG-REQUIRED] market.mdps.rates.time: an asof time mapping needs max_staleness (or unbounded_ok: true)`: a provider `time:` block of mode `asof` without `max_staleness`. `[CFG-UNKNOWN-KEY] market.mdps.rates.time: pricebt.testing.synthetic:SyntheticMarket does not accept a time mapping`:
  that provider takes no `time` (only one with a `time` or `mapping` parameter does; `config-anatomy.md` section 4). `[TIME] ...` is the same check reached through `TimeMapping` itself (`unknown time mode 'x'`, or a provider that builds its own mapping).
* `ResultError: the run has no baseline decomposition: set `attribution.baseline: true` ...`: `attribution(by="baseline")` needs `backtest.attribution.baseline: true` (and `dv01`, `gamma`, `rate` bound: `CFG-BASELINE`). The tie-out turns it on itself.
* `error: [MarketDataUnavailable] market data unavailable at <ts>: ...` at run time (exit 2): a missing snapshot, stale data (`max_staleness`) or a missing fixing. Not a config error: read the reason.
* The Python facade (`PricebtSession`, `pricebt.instrument.IRSwap`) cannot use an adapter that lives outside `pricebt` (or whose default bindings contain `function` targets): use `run` / `tieout` with a config, or ship method-only default bindings ([CE-FACADE](../pricebt-wire-external-library/references/common-errors.md#ce-facade)).
* In a notebook or REPL: `importlib.reload(<adapter>.swap)` raises `ConfigError [CFG-REDUCER] reducer '<name>' is already registered` (a reducer name is registered once), and `import <pkg>.swap as m` returns the Kit, not the module, when the package's `swap` attribute is the Kit: use `sys.modules["<pkg>.swap"]`.
* `nb_build` printed `FAILED <name>: ...` and exit 1 but the notebook file changed: it writes the notebook even when a cell fails and always to `notebooks/<stem>.ipynb`. Restore the last good one from version control and fix the cell.
* Two progress bars, or one line per step in a notebook: another tqdm bar was created inside the run; keep exactly one bar per run and leave `backtest.progress.show` true (or false).

## Related skills

`pricebt-wire-external-library` (the master playbook and worked example), `pricebt-conformance-and-tieout` (the CLI tie-out, the Python API, tolerances), `pricebt-debug-tieout-differences` (failing rows), `pricebt-market-data-snapshots` (providers and time mappings),
`pricebt-bindings-cookbook` (the `bind:` of an overlay), `pricebt-instrument-kit` (conventions and factories), `pricebt-guards-and-packaging` (allow-list guards and partition markers), `pricebt-architecture-and-rules` (the one rule).
