# Minimal end-to-end recipe: base config + overlay + validate, run, tieout

Copy, replace the four `<...>` items, run the commands. Executed on the worked example's adapter (`acme_adapter`, from `skills/pricebt-wire-external-library/example/`); the files below are the verbatim files that were run.
The model is the example's own base and overlay (`skills/pricebt-wire-external-library/example/config/acme_tieout_base.yaml`, `acme_swap.yaml`).

| replace | with |
|---|---|
| `acme_adapter` in `registry.allow` | the import name of YOUR adapter package (top-level; covers its submodules) |
| `acme_adapter:swap` | `<your package>:<the Kit for the swap>` |
| `acme_adapter:wrap` | `<your package>:wrap` |
| the conventions block | the block your adapter accepts (`<your package>.USD_SOFR_OIS_CONVENTIONS` shows a complete one; the same block goes to the reference stack, so a token your library cannot express is a `CFG-CONVENTION-UNSUPPORTED`) |

**Where the files go.** Create the two YAML files in a directory of your own, called `<work>` below: a `config/` folder of your adapter project, or a scratch directory. Do NOT create a `config/` folder in the pricebt checkout (it has `configs/`, which is pricebt's).
`outputs.dir` of the base is relative to the BASE FILE, so a `run` without `--out` writes parquet next to your files (the recipe passes `--out` everywhere). Run every command from the pricebt repository root (the reference overlay `configs/adapters/refstack_swap.yaml` is addressed relative to it);
give `<work>` as an absolute path or relative to the root.

## 1. The base: the reference stack is the default library, your library is an overlay

`<work>/mylib_base.yaml` (nothing here names your library except `registry.allow`):

```yaml
name: mylib_recipe
registry: {allow: [acme_adapter]}                      # <- your package; only the BASE may set it
backtest:
  tz: America/New_York
  calendar: {holidays: &holidays [2024-05-27, 2024-06-19]}   # the grid skips the days the market does not serve (a holiday-free base cannot detect a wrong calendar)
  date_policy: close
  grid: {start: 2024-05-24, end: 2024-06-21, freq: 1b}
  progress: {show: false}
  attribution: {layers: [carry, roll, delta, convexity], cadence: eod}   # the layers the tie-out compares at L3
  measures: [dv01]
market:
  mdps:
    rates:
      type: "pricebt.testing.synthetic:SyntheticMarket"    # emits plain snapshots, imports no pricing library
      kwargs:
        start: 2024-01-02
        end: 2024-12-31
        seed: 7
        calendar: {$call: "pricebt.timeutil:Calendar", kwargs: {holidays: *holidays}}   # the snapshot calendar `usd_fed` carries the same holidays
  pricers:
    primary: {mdp: rates, wrap: "pricebt.testing.refstack:wrap"}
instruments:
  usd_sofr_ois:
    asset_class: swap
    factory: "pricebt.testing.refstack:swap"
    conventions: {calendar: usd_fed, spot_lag_days: 2, day_count: act360, frequency: annual, business_day_convention: modified_following, payment_lag_days: 2,
                  compounding: daily_compounded, stub: short_front, end_of_month: false, fixing_lag_days: 1, time_accrual: lump}
strategy:
  triggers:
    - type: periodic
      frequency: 1m
      actions:
        - {type: add_trade, priceables: {instrument: usd_sofr_ois, name: recv10y, terms: {side: receive, maturity: 10Y, notional: 1e7, fixed_rate: par}}, trade_duration: 1m}
        - {type: add_trade, priceables: {instrument: usd_sofr_ois, name: pay5y, terms: {side: pay, maturity: 5Y, notional: 5e6, fixed_rate: par}}, trade_duration: 1m}
        - {type: add_trade, priceables: {instrument: usd_sofr_ois, name: pay_short, terms: {side: pay, maturity: 2024-06-12, notional: 2.5e7, fixed_rate: 4.1}}, trade_duration: 1m}
outputs: {dir: out/mylib_recipe}
```

Design choices (each one is a way this goes wrong): the market is the library-free synthetic one (`pricebt.testing.synthetic`), so no data source is needed to start; the base carries TWO holidays and both directions with different notionals, so a wrong calendar, a wrong sign and a constant
standing in for `notional / 1e6` cannot cancel; the layers are listed in `attribution.layers` (else L3 has nothing of yours to compare); `outputs.dir` is relative to this file.
The THIRD trade, `pay_short`, matures on 2024-06-12 INSIDE the window: without a payment date in the window `L1.cash` compares zeros and the payment date, the payment lag and the cash sweep are never tied out (executed: the same base without `pay_short` has two positions and no cash flow in the window, `max |cash|` over the marks is 0.0). Keep at least one trade whose coupon or maturity falls in the grid. Swap the synthetic market for a snapshot provider of your own data
when you have one (`pricebt-market-data-snapshots`); the base does not change otherwise.

## 2. The overlay: factory and wrap only

`<work>/mylib_swap.yaml`:

```yaml
instruments:
  usd_sofr_ois: {factory: "acme_adapter:swap"}          # <- "<your package>:<your Kit>"; the Kit carries the default bindings
market:
  pricers:
    primary: {wrap: "acme_adapter:wrap"}                # <- "<your package>:wrap"
```

## 3. The commands

From the repository root, with the adapter's parent directory on `PYTHONPATH` (`src` and `tests` are pricebt's own). `$w` is your `<work>` directory:

```powershell
$env:PYTHONPATH = "src;tests;skills/pricebt-wire-external-library/example"    # <- the directory that CONTAINS your adapter package
$w = "<work>"
python -m pricebt validate $w/mylib_base.yaml --stack $w/mylib_swap.yaml
python -m pricebt run      $w/mylib_base.yaml --stack $w/mylib_swap.yaml --out $w/out/mylib --tearsheet
python -m pricebt tieout   $w/mylib_base.yaml --stack reference=configs/adapters/refstack_swap.yaml --stack mylib=$w/mylib_swap.yaml --out $w/out/mylib_tieout
```

Expected (executed):

| command | result |
|---|---|
| `validate` | prints `OK`, exit 0 (the graph builds; no factory is called, so conventions are NOT yet checked) |
| `run --dry-run` (optional) | `OK: first pricer AcmePricer at 2024-05-24 17:00:00-04:00 (reference_date 2024-05-24)`: your `wrap` ran once on the first snapshot |
| `run` | prints the summary statistics (`start`, `end`, `basis`, `freq`, `periods_per_year`, ..., `total_pnl`), exit 0; writes `equity.parquet`, `trades.parquet`, `positions.parquet`, `layers.parquet`, `orders.parquet`, `events.parquet`, `errors.parquet`, `manifest.json`, `tearsheet.html` into `--out` |
| `tieout` | the markdown report of `reference_vs_mylib`, then `stack mylib: factory ...; wrap ...; bindings not identity: ...`, `harness self-test: passed`, `TIE-OUT PASSED`, exit 0; writes `reference_vs_mylib.md/.html/.summary.parquet/.offenders.parquet` and `tieout.json` |

Without `--stack`, `validate` and `run` use the base's own library (the reference stack), which is a good first check of the base itself (`OK`).
To see the wiring problems that `validate` cannot: run a short window so that the factory builds a trade, and keep it out of the base's `outputs.dir`:
`python -m pricebt run $w/mylib_base.yaml --stack $w/mylib_swap.yaml --set backtest.grid.end=2024-05-31 --no-progress --out $w/out/short`
(`[CFG-CONVENTION] missing convention keys [...] for asset class 'swap': there are no implicit defaults`).

## 4. From here

* The ladder and a per-bucket comparison need the Python API (`run_tieout(..., audit_measures=("dv01", "gamma", "rate", "delta_ladder"))`): `pricebt-conformance-and-tieout`.
* A failing row: `pricebt-debug-tieout-differences`. A widened tolerance: a `tieout.tolerances` block in THIS base, with a reason.
* `python -m pricebt describe $w/mylib_base.yaml --stack $w/mylib_swap.yaml` prints `config_hash`, `base_hash`, the grid, the roles and instruments and the engine settings: `base_hash` must be the same with and without `--stack` for every stack of one base.
* Wrap the same commands in a test (`pricebt-conformance-and-tieout`, `skills/pricebt-conformance-and-tieout/references/adapter-test-templates.md`) so the recipe cannot rot. That template asserts `n_positions` and `n_points` for a base of this shape (three positions over a month of daily points);
  adapt those two numbers to your own base, and keep the cash-flow assertion that this base makes possible.
