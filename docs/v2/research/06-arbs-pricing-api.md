# 06: What a USD SOFR OIS asset config calls in ARBS

> **Key facts**
> 1. Use **`IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC")`** for backtests. It reads the local curve store first (asset `USD-SOFR-1D-RLBASIC`, 1,423 days, 2020-07-01..2026-08-20). **`ERIS_EOD_LIVE-RL_BASIC-NOJUMPS` never reads the curve store.** Its local diskcache holds only **26 dates**. Every other date goes to `files.erisfutures.com` and then **overwrites** the shared curve-store partition `USD-SOFR-1D/date=<d>`, deleting any GSQUANT rows stored for that day.
> 2. The call is `mdp.get_pricer(request=dict(curve_name="USD-SOFR-1D", timestamp=<datetime.date in New York>))`. `get_data` **pops** `curve_name` and `timestamp`, so every call needs a **fresh dict**; reusing one raises `KeyError: 'curve_name'`. The call returns `RLIRSwapCurve` (ARBS's rateslib-backed curve wrapper). **Safer:** `mdp.bulk_get_data(dict(curve_name="USD-SOFR-1D", timestamps=[d], ignore_cache_miss=True)).get(d)` is store-only. It returns `None` on a miss and can never reach the network or write the store (§1.4b).
> 3. Units and signs, verified on rateslib 2.7.1: `+notional` = **payer** (pays fixed). `npv` and `pv01` are in USD, signed from the holder's side, and `pv01` is per +1bp (a 10Y payer on 1mm shows about +820). `fixed_rate` and `fair_rate` are **decimals**, but `rl.IRS` itself holds percent. `dv01`, `gamma` and `dollar_carry` raise `NotImplementedError`.
> 4. A bucketed ladder `{tenor: USD per +1bp}` comes from a par-swap risk curve calibrated with `rl.Solver`, followed by `rl.Portfolio(trades rebuilt on the risk handle).delta(solver=...)`. ARBS ships the calibration as `MDP.IRSwaps.BARCHART_STIRF.risk.build_delta_risk_ladder`. A 25-line replica using only verified methods is in §4.
> 5. ARBS has **no offline historical FX** (`FXForwardMDP` scrapes Barchart through NordVPN proxies, and its cache holds 3 dates). Set `ARBS_SUPABASE_ENABLED=0` **before the first ARBS import**. Never let any request reach the Citi Excel sources.

Scope: read-only study of `C:\Users\chris\clee\ARBS` (never imported or executed) plus pricebt v1's bridges (`arbs_adapter/*.py`, `tools/arbs_live.py`, `tests/test_arbs_bridge.py`, `docs/research/mdp-feasibility.md`, which this note abbreviates as **FEAS**). Every "verified" claim was checked against source, a read-only file scan, or a standalone rateslib 2.7.1 experiment in the `stir` env, with no ARBS involved. All line numbers refer to the ARBS checkout as of 2026-09-27.

Vocabulary: **market** = the object the asset config's `market:` expression returns (`RLIRSwapCurve`). **trade** = the object its trade expression returns (`rateslib.IRS`).

---

## 1. `IRSwapsMDP`

### 1.1 Import and bootstrap

| Item | Value |
|---|---|
| Import | `from MDP.IRSwaps.IRSwapsMDP import IRSwapsMDP` (`MDP/IRSwaps/IRSwapsMDP.py:53`) |
| Path setup | `sys.path.insert(0, r"C:\Users\chris\clee\ARBS")`. `MDP`, `Query`, `Caching` and `utils` are top-level packages. No cwd requirement (FEAS §2). |
| Python env | `C:\Users\chris\anaconda3\envs\stir\python.exe` (3.13.5, rateslib 2.7.1, QuantLib 1.41). QuantLib is a hard dependency even on the rateslib path: `_validate_eod_curve_request_timestamp` uses `QUANTLIB_CURVE_DEFINITIONS[...]["Calendar"]` (`:817-825`, `:850-864`). |
| Must be set **before** the import | `os.environ["ARBS_SUPABASE_ENABLED"] = "0"` (see §6) |
| Import cost | `import MDP.IRSwaps.IRSwapsMDP`: 1.26 s, +91 MB. Fresh process to first pricer: about 2.5 s, 263 MB RSS (FEAS §2). |
| Warnings | rateslib emits `LicenceNotice` on import. Filter it. |

### 1.2 Constructor

```python
IRSwapsMDP(source: str = "CME_NY_EOD_LIVE-ql_basic", force_refresh_fixings: Optional[bool] = False, **kwargs: Any)   # IRSwapsMDP.py:116
```

- `MarketDataProvider.__init__(source, **kwargs)` stores `self.source = source` and `self.config = kwargs` (`MDP/MarketDataProvider.py`). For Eris, `self.config` is used only by the `timestamp == "live"` branch (`ErisFuturesFetcher(**self.config)`). Pass no kwargs.
- For Eris sources the constructor opens a diskcache (`_RLCurveCache`, a `LayeredCacheMixin` → `DiskCacheMixin`) (`:125-133`). The check is a **substring** test, so `"ERIS_EOD_LIVE-RL_BASIC-NOJUMPS"` matches both `if`s. It opens (and creates, if absent) the `ERIS_EOD_LIVE-RL_BASIC` cache dir first, then replaces `_rl_curve_cache` with `ERIS_EOD_LIVE-RL_BASIC-NOJUMPS`.
- **Construct once per process and reuse.** The instance holds no pricer memo (§1.7), so reuse costs nothing, and FanoutCache objects are registry-cached per directory (`DiskCacheMixin._acquire_cache`).

### 1.3 Sources relevant to a USD SOFR OIS

| `source=` (case-insensitive) | Curve it builds | Read order in `get_pricer` (single date) | Offline history (measured 2026-09-27) | Stamp in `meta()["timestamp"]` |
|---|---|---|---|---|
| **`ERIS_EOD_LIVE-RL_BASIC`** (alias `ERIS_EOD_LIVE_RL_BASIC`) | rateslib `log_linear` curve with a **log-cubic spline** long end (`t=` knots), about 24-25 nodes: FOMC dates + 12 IMM dates + 3/5/7/10/20/30Y (`ErisFuturesFetcher._df_to_curve`, `ErisFuturesFetcher.py:485-556`) | (1) CurveStore asset **`USD-SOFR-1D-RLBASIC`** (`_eris_curve_store_asset`, `IRSwapsMDP.py:1039-1057`), unless `force_refresh`/`ignore_cache`; (2) diskcache `ERIS_EOD_LIVE-RL_BASIC`, key `"{date}-ERIS_EOD_LIVE-rl_basic_ERIS_EOD_LIVE-RL_BASIC-USD-SOFR-1D-{date}"`; (3) network (Eris FTP) | Store: **1,423 partitions, 2020-07-01..2026-08-20**. Against the rateslib `nyc` calendar, **112 business days are missing**: 2020-07-06..08-31, 2020-09-08..09-30, 2020-10-06..10-30, 2020-11-09..11-30, 2020-12-07..12-31 (only the first 1-3 business days of each month exist in H2-2020), and 2026-08-13..08-18. Dense from **2021-01-04**. Three partitions fall on Good Fridays (2021-04-02, 2023-04-07, 2026-04-03). Diskcache: 7 keys, **all 7 are `-bulk` keys** (2026-02-23..27, 2026-08-12, 2026-08-20). Single-date keys end in the date, so `get_pricer` never hits any of them. | Store hit: tz-aware, 15:00 America/New_York (stored as 19:00 UTC in EDT, 20:00 UTC in EST) |
| `ERIS_EOD_LIVE-RL_BASIC-NOJUMPS` | rateslib `log_linear` over **every daily discount factor** Eris publishes (about 18.3k nodes), act360 / nyc / MF, no spline (`ErisFuturesFetcher.py:495-503`) | (1) diskcache `ERIS_EOD_LIVE-RL_BASIC-NOJUMPS`, key `"{date}-ERIS_EOD_LIVE-rl_basic_ERIS_EOD_LIVE-RL_BASIC-NOJUMPS-USD-SOFR-1D-{date}"`; (2) network. **The CurveStore is never read** (`IRSwapsMDP.py:2136-2178` has no store lookup). | **26 dates only** (2022-01-05 .. 2026-08-21, mostly early January plus a few 2026 dates). The 18k-node frames also sit in store asset `USD-SOFR-1D` (below), but this source cannot read them. | Diskcache hit: pytz `America/New_York` 15:00 datetime |
| `GSQUANT-RL` | GS-quant EOD curve, 25 nodes | (1) CurveStore asset = `curve_name`; (2) diskcache `GSQUANT-RL_CURVE_CACHE`; (3) gs_quant API (`GsSession.use` with credentials embedded at `MDP/IRSwaps/GSQUANT/rl_basic/build.py:562`) | See §2 | **bare `datetime.date`** (not tz-aware) |
| `CITIVELO_EXCEL-RL`, `CITIVELO`, `BARCHART_STIRF-RL`, `SDR_*`, `CME_NY_EOD_LIVE-*` | n/a | n/a | **Out of scope.** Citi Excel can launch `EXCEL.EXE` (FEAS §5.2). CME has an empty cache when redirected and needs live HTTP (FEAS §12). Barchart has silent-prior-snapshot hazards (FEAS §8). | n/a |

**Store asset `USD-SOFR-1D` is shared and mixed** (run-length scan of `source_variant`, read-only with pyarrow): `GSQUANT_RL` 2018-08-01..2020-06-19 (471 days); `ERIS_RL_BASIC` 18k-node frames 2020-06-22..2026-08-12, with single `ERIS_RL_BASIC_NOJUMPS` days around each New Year; then a patchwork of `GSQUANT_RL` / `ERIS_*` for 2026-08-13..2026-09-04. Both the GSQUANT-RL store path and `ErisFuturesFetcher._write_curve_store_products` write this asset. **`_load_gsquant_curve_store_history` (`IRSwapsMDP.py:1588-1694`) does not filter on `source_variant`**, so `IRSwapsMDP(source="GSQUANT-RL")` with `curve_name="USD-SOFR-1D"` serves **Eris 18k-node frames** for 2020-06-22..2026-08-12, labelled as GS.

**Verdict:** the Eris feed the user asked for is served offline, over a real history, only by **`ERIS_EOD_LIVE-RL_BASIC`**. NOJUMPS is usable only after a deliberate, network-enabled warm run over the backtest dates, with the store-clobber side effect accepted (§6). The ARBS pricer notebook defaults to NOJUMPS (`notebooks/pricers/irswap_pricer_and_risk_model.ipynb` cell 2) because it is used with `timestamp="live"`.

RL_BASIC vs NOJUMPS pricing: pricebt v1 measured that plain log-linear rebuilt from the RL_BASIC nodes differs from ARBS's own spline curve by about 0.02bp of 10Y par and about $162 NPV per $10mm on 2024-05-24 (`arbs_adapter/wrap.py` docstring). The code comment at `IRSwapsMDP.py:1041-1048` says the two Eris variants diverge by up to about 7.4bp at the long end. They are different curves, so pick one and keep it.

### 1.4 `get_pricer(request)`: the exact contract

```python
def get_pricer(self, request: dict) -> _IRSwapGenericCurve          # :1924  raises RuntimeError if get_data returns None
def get_data(self, request: dict) -> Optional[_IRSwapGenericCurve]  # :1984
    curve_name = request.pop("curve_name")    # :1985  KeyError if absent
    timestamp  = request.pop("timestamp")     # :1986  KeyError if absent
    if not curve_name or not timestamp: raise ValueError("Request must contain 'curve_name' and 'timestamp'.")
    return self._get_curve(curve_name, timestamp, kwargs=request)   # the REMAINDER of the dict becomes kwargs
```

| Request key | Required | Type | Eris behaviour |
|---|---|---|---|
| `curve_name` | yes (popped) | `str` | Must be a key of `RATESLIB_CURVE_DEFINITIONS`, else `AssertionError` (`:2099`, `:2147`). **Only `"USD-SOFR-1D"` is meaningful.** The Eris fetcher only downloads `EOD_DiscountFactors_SOFR` / `Eris_Intraday_DiscountFactors_SOFR` (`ErisFuturesFetcher.py:266-290`). `curve_name` selects conventions and the fixings series only, so `"EUR-ESTR"` would return **a SOFR curve labelled EUR, silently**. |
| `timestamp` | yes (popped) | `datetime.date` (recommended), `datetime.datetime`, `pd.Timestamp`, or `"live"` | Eris branches do `if type(ts)==datetime.datetime or hasattr(ts,"date"): ts = ts.date()` (`:2094-2095`, `:2142-2143`), **with no timezone conversion**. A UTC-aware `2026-08-06 01:00Z` becomes 2026-08-06 although it is 2026-08-05 in New York. **Pass `pd.Timestamp(ts).tz_convert("America/New_York").date()`.** |
| `force_refresh` | no | bool | RL_BASIC: skips store and diskcache, **goes to network** (`:2104`). NOJUMPS: skips diskcache (`:2165`). Never set in a backtest. |
| `ignore_cache` | no | bool | RL_BASIC only: same as `force_refresh`. Never set. |
| `snapshot_policy` | no | n/a | Raises `ValueError` for any non-Citi source (`:2004-2013`). Never set. |

**Fresh dict per call is mandatory.** After the first call the dict has lost both keys, so a second call raises `KeyError: 'curve_name'`. In an expression string, `request=dict(curve_name="USD-SOFR-1D", timestamp=...)` builds a new dict on every evaluation, which satisfies this. **Never hoist the dict into a module-level constant.** `get_grid` shows the idiom ARBS itself uses (`self.get_pricer(dict(request))`, `:1956`).

### 1.4b `bulk_get_data(request)`: the store-only, no-network entry point (RECOMMENDED for backtests)

```python
def bulk_get_data(self, request: dict) -> Dict[Union[datetime.date, datetime.datetime], _IRSwapGenericCurve]   # :3871
    curve_name = request.pop("curve_name", None); timestamps_in = request.pop("timestamps", None)
    ignore_cache = bool(request.pop("ignore_cache", False)); ignore_cache_miss = bool(request.pop("ignore_cache_miss", False))
    n_jobs = request.pop("n_jobs", None); calibration_executor = request.pop("calibration_executor", "thread")   # :3875-3883
```

For `ERIS_EOD_LIVE-RL_BASIC` (`:3998-4070`), the steps are:

1. Dedupe the timestamps. A timestamp `== datetime.date.today()` becomes `"live"`, so never include today.
2. **Drop** non-business days with a logged warning (`:3917-3935`). If all are dropped: `ValueError("Request 'timestamps' resolved to an empty collection.")`.
3. `_build_fixings_cache_for_dates` reads the fixings **once** for the whole batch (`:900-922`), in percent, clipped to `< d` per date.
4. `_load_eris_curve_store_history` reads the store for all dates in one DuckDB scan. If every date hit, return.
5. **Otherwise, `if ignore_cache_miss: return out`, BEFORE any diskcache, network or promote call.**

So

```python
_MDP.bulk_get_data(dict(curve_name="USD-SOFR-1D", timestamps=[d], ignore_cache_miss=True)).get(d)   # RLIRSwapCurve or None
```

**can neither reach the network nor write the store**, and needs no monkeypatching. Result keys are `datetime.date`. A missing date is simply absent; `.get(d)` returns `None`, so translate that to "market unavailable". The same call with the whole backtest's date list preloads every market in one scan (a memory trade-off: each RL_BASIC market is small, about 11 KB, per FEAS §7). `meta()["id"]` on this path is `f"ERIS_EOD_LIVE-RL_BASIC-USD-SOFR-1D-{stamp}"` (it is not rewritten to the `get_pricer` format). The fixings side still calls `_fetch_fixings` (mkdir side effect, §6).

**NOJUMPS has no bulk branch.** It falls to the default loop (`:4863-4874`): per date, `get_data(...)` inside `except Exception: continue`. That path is the network path of §1.5, and failures vanish silently. Do not use it.

On the `get_pricer` path, before any I/O, `_validate_curve_request_timestamp` (`:887-898`) runs: `"EOD" in source`, so `_validate_eod_curve_request_timestamp` checks the QuantLib US calendar. A weekend or holiday raises `ValueError(f"{timestamp} is not a business day in the {calendar}!")`.

### 1.5 What happens inside, `ERIS_EOD_LIVE-RL_BASIC`, historical date `d` (`:2088-2134`)

1. `timestamp → d` (a date). Assert that `curve_name` is in `RATESLIB_CURVE_DEFINITIONS`.
2. `_load_eris_curve_store_history(requested_curve_name, request_dates=[d], max_workers=1)` (`:1483-1586`): `CurveStore.default().read_raw_nodes("USD-SOFR-1D-RLBASIC", start=d, end=d)` (single day → `read_raw_day`, direct pyarrow, about 5 ms), then the **sanity filter** (`Caching/curve_sanity.py`, env `ARBS_CURVE_STORE_SANITY_FILTER`, default on), then `reconstruct_curves_batch`, then `_build_eris_eod_rl_curve` (`:977-1037`).
3. `_build_eris_eod_rl_curve` loads fixings with `_fetch_fixings(as_of_date=d, curve_name="USD-SOFR-1D")`, clips them to dates `< d`, and multiplies by **100** (percent). It sets the calendar from `RATESLIB_CURVE_DEFINITIONS`. It returns `RLIRSwapCurve(rl_curve_id="USD-SOFR-1D", rl_curve_handle=<rl.Curve>, fixings=<pd.Series %>, meta_data={...})`.
4. On a store hit, return (`:2112-2115`). No write.
5. On a store miss (a hole, a sanity-quarantined day, a date past 2026-08-20): try diskcache `_rl_curve_cache.get_eris_eod_live_rl_basic(curve_id=f"ERIS_EOD_LIVE-RL_BASIC-USD-SOFR-1D-{d}", as_of=d, ...)`. On a miss, **`ErisFuturesFetcher().fetch_intraday_discount_curve(...)` → HTTPS to files.erisfutures.com**. That writes the raw-file diskcache `ErisFuturesFetcher-raw.fs`, the curve diskcache, and `CurveStore.write_day("USD-SOFR-1D", d, ..., overwrite=True)` plus analytics (`ErisFuturesFetcher.py:207-264`). Then `_promote_eris_curve_store_day` writes `USD-SOFR-1D-RLBASIC` if that partition is absent (`IRSwapsMDP.py:1069-1166`).

For `…-NOJUMPS` (`:2136-2178`), skip steps 2 and 4: start at step 5 with `no_jumps_just_interp=True` and cache `ERIS_EOD_LIVE-RL_BASIC-NOJUMPS`.

### 1.6 What is returned

`Query.IRSwaps.backends.rateslib.RLIRSwapCurve.RLIRSwapCurve` (a dataclass subclass of `_IRSwapGenericCurve`):

| Accessor | Returns |
|---|---|
| `id()` | `"USD-SOFR-1D"` |
| `handle()` | the underlying `rateslib.Curve` (`meta.convention="act360"`, `meta.calendar` = nyc) |
| `index()` | fixings `pd.Series`, **percent**, naive midnight `DatetimeIndex`, strictly before the reference date, oldest first (USD: about 2,080 rows from 2018-04-02) |
| `meta()` | `dict`: `timestamp` (see §1.3 column), `id` (= `f"{SOURCE}-USD-SOFR-1D-{d}"`), `requested_curve_name`, `curve_name`, `reference_curve_name` (all `"USD-SOFR-1D"`) |
| `reference_date()` | `datetime.datetime` (first node key) at midnight = the trading date `d` |

pricebt should **validate the serve rather than trust it** (v1 `tools/arbs_live.py:460-481`). Check that `pd.Timestamp(meta()["timestamp"]).tz_convert("America/New_York").date() == d` and `reference_date().date() == d`. Raise otherwise.

### 1.7 Caching, cost, threads

- **No in-process memo** of pricers in `IRSwapsMDP`. Every `get_pricer` repeats the store read, reconstruction and fixings CSV read (`_read_cached_if_valid` re-reads CSVs each call). **pricebt must evaluate the market expression once per (asset, timestamp) and reuse the object for every trade and function at that timestamp.** Reuse matters beyond speed: `fair_rate`'s par memo is keyed on the object's identity (§3.2).
- Costs for RL_BASIC (FEAS §4, §6): `get_pricer` 160 ms cold / 16-21 ms warm; `build_irswap` 12-23 ms; `npv` 12-17 ms (rebuilds an `rl.IRS` with fixings on every call); `pv01` 0.1 ms; `fair_rate` about 0 when memoised, else about 5 ms; `carry_bps_running` 13 ms; `roll_bps_running` 14 ms; `theta_components` 41 ms. NOJUMPS (`from_json` of an 18k-node curve): **unmeasured**.
- Thread-safe, but threads give no speedup (FEAS §7). Pricers and `rl.Curve` pickle fine. Parallelism needs processes.

### 1.8 Failure modes

| Situation | What ARBS does | pricebt handling |
|---|---|---|
| Weekend or US holiday | `ValueError("... is not a business day in the ...")` before any I/O | Treat as no data. pricebt's own calendar should not ask. |
| Business day that is a store hole or sanity-quarantined (RL_BASIC) | falls through to diskcache, then **network** | See §6: either patch the network out, or accept fetch and writes |
| Date with no data anywhere (past the Eris archive, or network blocked) | `ErisFuturesFetcher._fetch_eris_ftp_files_helper` retries 3× with backoff, returns `(None, None)`; `fetch_intraday_discount_curve` returns `{}`; `_RLCurveCache` does `rl_curve, ts = {}` → **`ValueError: not enough values to unpack (expected 2, got 0)`** after about 7.6 s (FEAS §8) | Catch `Exception`, translate to pricebt's "market unavailable" |
| `curve_name` not in `RATESLIB_CURVE_DEFINITIONS` | `AssertionError` | config error |
| Reused request dict | `KeyError: 'curve_name'` | never happens with `dict(...)` inline |
| `get_data` returned `None` | `RuntimeError("IRSwapsMDP could not build a curve for request: ...")` | market unavailable |
| `bulk_get_data(..., ignore_cache_miss=True)` on a store miss | the date is absent from the result dict (`.get(d)` → `None`). No exception, no I/O beyond the store and fixings reads. | market unavailable |
| `bulk_get_data` where every timestamp is a non-business day | `ValueError("Request 'timestamps' resolved to an empty collection.")` | market unavailable |
| Guard active (network blocked) | `GuardBlocked`/socket error inside the fetcher's `except Exception`: printed, then the unpack `ValueError` above | market unavailable |

---

## 2. Curve names and currencies

`RATESLIB_CURVE_DEFINITIONS` (`Query/IRSwaps/backends/rateslib/rl_curve_definitions_map.py`) is the registry every rateslib-backed source resolves conventions from. `RLIRSwapCurve._curve_definition()` reads `meta["reference_curve_name"]` (falling back to `id()`).

| Key | `ReferenceRate` (rateslib spec) | `NotionalCurrency` | `DayCounter` | `Calendar` | `SettlementDays` | `PaymentLag` |
|---|---|---|---|---|---|---|
| `USD-SOFR-1D` | `usd_irs` | usd | act360 | nyc | 2 | 2 |
| `USD-SOFR-1D-RISK`, `USD-OIS`, `USD-OIS-STIR`, `USD-OIS-RISK`, `USD-OIS-STIR-RISK`, `USD-FEDFUNDS`, `USD-FEDFUNDS-RISK`, `USD-FEDFUNDS-1D-RISK` | `usd_irs` | usd | act360 | nyc | 2 | 2 |
| `EUR-ESTR` | `eur_irs` | eur | act360 | tgt | 2 | 2 |
| `GBP-SONIA` | `gbp_irs` | gbp | act365f | ldn | **0** | **0** |
| `JPY-TONAR`, `JPY-TONA` | `jpy_irs` | jpy | act365f | tyo | 2 | 2 |
| `CAD-CORRA` | `cad_irs` | cad | act365f | tro | 2 | 2 |
| `CHF-SARON` | `chf_irs` | chf | act360 | zur | 2 | 2 |
| `EUR-EURIBOR-3M` | `eur_irs` | eur | 30e360 | tgt | 2 | 2 |

`MDP/IRSwaps/CITIVELO_EXCEL/curve_definitions.register()` adds 20 `*-1D` names at runtime (`EUR-ESTR-1D`, `GBP-SONIA-1D`, `JPY-TONAR-1D`, `AUD-AONIA-1D`, ...). They are served only by the Citi Excel source (excluded).

**Offline sources per currency** (directory listings of `%LOCALAPPDATA%\ARBS\Cache\curve_store\raw` plus read-only parquet footers):

| Currency | Source that serves it offline through `get_pricer` | `curve_name` | Store coverage | Fixings cached (`%LOCALAPPDATA%\ARBS\MDP\IRSwaps\ARBS\MDP\IRSwaps\Cache\fixings_cache\<name>_fixings\<date>\fixings.csv`) |
|---|---|---|---|---|
| USD | `ERIS_EOD_LIVE-RL_BASIC` | `USD-SOFR-1D` | 2020-07-01..2026-08-20 (dense from 2021-01-04) | newest CSV `2026-09-08/fixings.csv` (later dated dirs are empty) |
| USD | `GSQUANT-RL` | `USD-SOFR-1D`, `USD-OIS` | 2018-08-01..2026-09-04 / 2005-10-24..2026-09-04. **Caution:** `USD-SOFR-1D` rows for 2020-06-22..2026-08-12 are Eris frames served unfiltered (§1.3). | yes |
| EUR | `GSQUANT-RL` | `EUR-ESTR` | 2019-08-23..2026-08-03 (22 nodes) | newest 2026-08-06 |
| GBP | `GSQUANT-RL` | `GBP-SONIA` | 2012-07-31..2026-08-03 (26 nodes) | newest 2026-08-06 |
| JPY | `GSQUANT-RL` | `JPY-TONAR` | 2010-01-04..2026-08-03 (13 nodes) | newest 2026-08-06 |
| CAD | none offline via `get_pricer` (store has only Citi `CAD-CORRA-1D-CITIVELOEXCEL*` and `CAD-CORRA-Q8STIRT`) | n/a | n/a | yes (`CAD-CORRA_fixings`) |

GSQUANT-RL caveats: `assert type(timestamp) == datetime.date` (`IRSwapsMDP.py:2631`) rejects `datetime` and `pd.Timestamp` outright. `meta()["timestamp"]` is a bare date. Resolving the reference name imports `MDP.IRSwaps.GSQUANT.rl_basic.build`, which imports `gs_quant` at module top (`build.py:10-11`). A store and diskcache miss calls `GsSession.use(...)` (network, embedded credentials, `build.py:580-587`). This is acceptable in a user asset config (pricebt core never imports it), but it is a second ARBS path with its own hazards. Non-USD fixings end 2026-08-06, so seasoned non-USD marks after that date lack fixings.

---

## 3. `RLIRSwapCurve`: full public API

Source: `Query/IRSwaps/backends/rateslib/RLIRSwapCurve.py` (441 lines). The trade type throughout is `rateslib.IRS`, built by `build_irswap`.

### 3.1 Generic interfaces

`Query/Base/_GenericPricer.py`: `_GenericPricer[_GP]` with abstract `npv(instrument, /, **kw) -> float`, `build_pricable(/, **kw) -> _GP` and `resolve_pricable(priceable, risk_weight=None) -> _GP`. `Query/IRSwaps/_IRSwapGenericCurve.py:10-75` declares (no bodies) the methods in the table below, plus `set_fixed_rate(irswap, rate_decimal)`. **`RLIRSwapCurve` does not implement `set_fixed_rate`** (it inherits the `...` stub, which returns `None` and does nothing). `Query/IRSwaps/_IRSwapGenericObject.py` is an object-owns-methods variant (`effective_date()`, `nominal()`, `with_notional()`, ...). The rateslib backend does **not** use it: its trades are bare `rl.IRS`.

### 3.2 Method table (m = market, t = trade)

| Method (exact signature) | Returns / units | Sign | Notes and traps |
|---|---|---|---|
| `id()` | `str` curve name | n/a | n/a |
| `reference_date()` | `datetime.datetime` (midnight) | n/a | first node key |
| `spot_date()` | `datetime.datetime` = reference + `SettlementDays` business days (`"2b"` for USD) | n/a | `:244-247` |
| `calendar()` | rateslib `Cal` (`handle().meta.calendar`) | n/a | n/a |
| `calendar_advance(dt1, dt2: str)` | `datetime.datetime` = `rl.add_tenor(dt1, tenor=dt2, modifier="mf", calendar="nyc")` | n/a | `dt2` is a tenor string: `"2b"`, `"1Y"`, `"-3m"` |
| `daycounter()`, `set_fixed_rate(...)` | **not implemented** (inherited stubs return `None`) | n/a | do not call |
| `handle()` | `rl.Curve` | n/a | use for rateslib calls (`t.cashflows(curves=m.handle())`) |
| `index()` | fixings `pd.Series`, **percent** | n/a | n/a |
| `meta()` | `dict` (§1.6) | n/a | n/a |
| `nodes()` | `{datetime.date: float DF}` | n/a | 18k entries for NOJUMPS |
| `effective_date(t)` | `pd.Timestamp` = `min(t.leg1.cashflows()["Acc Start"])` | n/a | verified type. Calls `cashflows()` (a few ms). |
| `maturity_date(t)` | `pd.Timestamp` = `max(... "Acc End")` | n/a | n/a |
| `fixed_rate(t)` | **decimal** (`t.fixed_rate / 100`) | n/a | n/a |
| `notional(t)` | signed float (`t.kwargs.leg1["notional"]`) | **+ = payer** | n/a |
| `fair_rate(t)` | par rate, **decimal** | n/a | Memo: returns the par cached by `build_irswap` only if `owner is self and handle is self._rl_curve_handle` (`:163-173`). Otherwise `t.rate(curves=handle)/100` on the **original object with its original fixings**. Wrong for a trade built on another day's market once it has started: use `remark` (§3.4). |
| `npv(t)` | PV in **curve currency** | holder (+ = in the holder's favour) | Rebuilds `rl.IRS(effective, termination, fixed_rate, spec, notional, fixings=self._fixings)` on **this** market (`:202-217`), so it is safe for a trade built on an earlier market. Includes accrued (dirty PV). |
| `pv01(t)` | `t.analytic_delta(curves=handle)`: ccy per **+1bp** of the fixed rate / par level | holder: **payer > 0** | Verified: 10Y, 1mm notional, payer ≈ +820. It uses the trade's own object; the fixed-leg annuity needs no fixings. For a started swap it still includes the current period's accrued portion, so it slightly overstates rate risk (v1 used the ladder sum there). |
| `dv01(t)` / `gamma(t)` / `dollar_carry(t, horizon)` | **raise `NotImplementedError`** (`:222-242`) | n/a | never reference |
| `carry_bps_running(t, horizon: str)` | bp of rate, static curve: `(R_fwd - R_today)·1e4` | **receiver-positive** | `Query/IRSwaps/_carry_roll.py:164-183`. 0 if not started by the horizon. Raises `ValueError` if the swap matures before the horizon. |
| `roll_bps_running(t, horizon)` | bp: `(R_today - R_aged)·1e4` | receiver-positive | ages both dates by `horizon`, effective floored at spot. Raises if nothing is left. |
| `carry_and_roll_bps_running(t, horizon)` | bp, exactly carry + roll | receiver-positive | n/a |
| `roll_curve(horizon)` | `rl.RolledCurve` (`handle().roll(h)`), memoised per horizon | n/a | n/a |
| `theta_components(t, horizon="1b")` | `dict` in curve ccy: `cashflows, forwarding, rolldown, option(=0), theta`, plus `pv_sod, pv_fwd, pv_eod, horizon_date` | **PV-decay-positive**: `theta = pv_sod - pv_eod`. P&L over the horizon = `-(forwarding + rolldown + option)` | `rl_theta.py:177-240`. Calls `t.cashflows(curves=...)` on the passed trade, so pass a trade built on **this** market. Do **not** add it to the `*_bps_running` family. |
| `resolve_pricable(t, risk_weight=None)` | new `rl.IRS` | n/a | **Trap:** with `risk_weight=None` the direction is `-pv01` (`:378-394`), so a **payer comes back as a receiver** on this backend. Never use it. |
| `build_pricable(**kw)` | = `build_irswap(fwd, tenor, effective_date, maturity_date, fixed_rate=float(kw.get("fixed_rate", -0.0)), notional, bpv)` | n/a | `fixed_rate=None` → `TypeError` |
| `build_irswap(fwd=None, tenor=None, effective_date=None, maturity_date=None, fixed_rate=-0, notional=None, bpv=None)` | `rl.IRS` with `curves=` this handle, `spec=ReferenceRate` (`usd_irs`), fixings attached | notional sign = direction | See §3.3 |
| `build_stirf(fwd=None, tenor=None, effective_date=None, maturity_date=None, fixed_rate=-0, notional=None, bpv=None, is_ser=False, fixings=None)` | `rl.STIRFuture`, `price = 100 - fixed_rate` (fixed_rate here is a **percent/price-rate**, not decimal), `contracts = int(notional/1e6)` | n/a | Not needed for OIS. Relevant later for STIR futures. |

### 3.3 `build_irswap`: exact semantics (`:288-355`)

| Input | Rule |
|---|---|
| Effective date | `fwd` truthy: `"0D"` → **spot** (reference + 2b); any other tenor → `calendar_advance(reference_date, fwd)`, i.e. **from the reference date, not spot** (a "1Y" forward starts 2bd earlier than a 1Y-from-spot forward). `fwd` falsy → `effective_date` used as given, **no business-day adjustment** (it must have `.year/.month/.day`: `date`, `datetime` and `pd.Timestamp` all work). Neither given → `AttributeError`. |
| Termination | `tenor` (string such as `"10Y"`, measured from effective by rateslib) if given, else `rl.dt(maturity_date...)`. Neither given → `AttributeError: 'NoneType' ... 'year'`. |
| Strike | `fixed_rate` is a **decimal** (0.0425 = 4.25%); it is stored as percent in `rl.IRS`. **Par sentinel: `fixed_rate == -0`**. Since `0.0 == -0` is `True`, **any zero strike is silently struck at par**. When struck at par, the par (decimal) is memoised on the object for `fair_rate`. |
| Size | `notional` signed (**+ payer / − receiver**). Else, if `bpv` is given: `notional = bpv / analytic_delta(unit swap)`, so `bpv` is ccy per +1bp, signed (+ payer). Neither given → **1,000,000 (payer)**. |

### 3.4 Seasoned marking rule: build once, re-mark on each day's market

Trades are built **once** on the entry-date market and then marked on later markets.

- **Safe on a later market as-is:** `npv(t)` (rebuilds with the later market's fixings), `pv01(t)` (fixed leg; accrued caveat above), `effective_date(t)`, `maturity_date(t)`, `fixed_rate(t)`, `notional(t)`.
- **Not safe:** `fair_rate`, `carry_*`, `roll_*`, `theta_components`, and any rateslib call on the object (`t.cashflows`, `t.delta`). These use the **entry-date fixings** baked into `t`, which lack every fixing after entry.
- **Fix: re-mark first.**

```python
def remark(m, t):   # same economic swap, rebuilt on market m with m's fixings
    k = m.fixed_rate(t)                      # decimal
    if k == 0.0:                             # par sentinel collision (see 3.3)
        raise ValueError("zero strike cannot be rebuilt through build_irswap (par sentinel)")
    return m.build_irswap(effective_date=m.effective_date(t), maturity_date=m.maturity_date(t),
                          fixed_rate=k, notional=m.notional(t))
```

- **Schedule equivalence (verified, rateslib 2.7.1):** rebuilding from `(min Acc Start, max Acc End)` as explicit dates gives identical leg1 and leg2 payment schedules and a bit-identical NPV for 10Y spot, 5Y from month-end 2026-08-31, 7Y from 2027-02-26, and 2Y from 2026-11-30. ARBS's own `npv` relies on the same rebuild.
- **Cost:** `remark` is a `build_irswap` (12-23 ms). Memoise it per (market, trade), e.g. `m.__dict__.setdefault("_pricebt_remark", {})` keyed by `id(t)` (the same pattern ARBS uses at `RLIRSwapCurve.py:195-199`). Call `theta_components` once and read every key from that one result.
- **Matured swaps:** plain rateslib returns `npv == 0.0` and `analytic_delta == 0.0` on a curve anchored after maturity (verified). With ARBS's fixings attached this is **unverified**, so guard in the expression: `0.0 if m.maturity_date(t).date() <= m.reference_date().date() else m.npv(t)`.
- **Coupon cash between marks:** `npv` drops when a coupon pays. ARBS has no "cash between t0 and t1" method. The data exists on the rateslib object: `remark(m,t).cashflows(curves=m.handle())` has columns `Type, Ccy, Payment, Notional, Period, Convention, DCF, Acc Start, Acc End, DF, Cashflow, NPV, FX Rate, Base Ccy, NPV Ccy, Collateral, Rate, Spread` (verified). Sum `Cashflow` where `t0 <= Payment < t1` (v1 `arbs_adapter/swap.py:157-163`). An open design question for pricebt (§8).

### 3.5 Signs and units, verified (rateslib 2.7.1, standalone, flat-ish USD curve)

| Experiment | Result |
|---|---|
| 10Y `usd_irs`, notional +1e6, struck 50bp **below** par | `npv = +41,001.50`, `analytic_delta = +820.03` → **+notional = payer**, pv01 positive for a payer |
| same, notional −1e6 | `npv = −41,001.50`, `analytic_delta = −820.03` |
| payer at par, curve `shift(+1bp)` | npv 0 → **+852.39** (value rises with rates) |
| `Solver`-calibrated 2Y/5Y/10Y risk curve, `IRS(... notional=1e6, fixed_rate=4.0).delta(solver=sv)` | `{2Y: −0.05, 5Y: −0.22, 10Y: +819.67}`, sum 819.39 vs `analytic_delta` 820.03 → ladder in **ccy per +1bp of each pillar par rate, holder-signed, same sign as pv01** |

`Solver(s=...)` takes **percent**. ARBS `fair_rate` gives decimal, so multiply by 100.

---

## 4. Bucketed delta ladder `{tenor: USD per +1bp}`

The market carries no Solver (`dv01` raises for that reason), so node-level AD on the dense Eris curve is not a par-rate ladder. ARBS's own method (notebook `irswap_pricer_and_risk_model.ipynb` cells 6-9, and `MDP/IRSwaps/BARCHART_STIRF/risk.py:19-144`):

1. For each pillar tenor, build a par swap on the **dense** market: `build_irswap(... tenor)` → `fair_rate`. Its maturity becomes a pillar node, seeded with the dense DF at that maturity.
2. `rl.Curve(nodes={ref:1.0, mat_i: DF_i}, convention=dense.meta.convention, calendar=dense.meta.calendar, interpolation="log_linear", id=f"{id}-RISK")`, wrapped in `RLIRSwapCurve(rl_curve_id=..., rl_curve_handle=risk_curve, fixings=m.index(), meta_data={**m.meta(), "id": ..., "reference_curve_name": m.meta()["reference_curve_name"]})`.
3. Rebuild the pillar swaps on the **risk** handle at their par strikes. `rl.Solver(curves=[risk_curve], instruments=..., s=[par%...], instrument_labels=tenors, func_tol=1e-8, conv_tol=1e-10)`.
4. **"Bucket risk must be priced on the calibrated risk curve, not the dense curve"** (notebook cell 8): rebuild every trade on the risk handle, then `rl.Portfolio(rebuilt).delta(solver=solver)`.

**Option A, ARBS's function:** `from MDP.IRSwaps.BARCHART_STIRF.risk import build_delta_risk_ladder`, signature `build_delta_risk_ladder(queries: list[str | BaseQuery], curve_handle: RLIRSwapCurve, *, stirf_mdp_handle=None, timestamp=None) -> (risk_curve_handle: RLIRSwapCurve, solver: rl.Solver)`. String tenors go through `IRSwapQuery(curve=meta["requested_curve_name"], tenor=q).resolve_query(ts, pricer_or_curve=curve_handle)` with `ts = meta["timestamp"]`. Caveat: the module imports `STIRFutureMDP`, which pulls in the Webull and Barchart fetchers, `requests` and more. Import-only per my reading; not verified network-free. Its Solver prints to stdout.

**Option B (recommended), the same calibration using only verified `RLIRSwapCurve` methods plus rateslib** (mirrors v1 `arbs_adapter/risk_model.py:282-315` and ARBS's function):

```python
def delta_ladder(m, trades, tenors=("2Y", "5Y", "10Y", "30Y")):
    import contextlib, io
    import rateslib as rl
    from Query.IRSwaps.backends.rateslib.RLIRSwapCurve import RLIRSwapCurve
    if not trades:
        return {b: 0.0 for b in tenors}
    dense, spot, ref = m.handle(), m.spot_date(), m.reference_date()
    nodes, pars = {ref: 1.0}, []
    for b in tenors:                                   # must be increasing and map to distinct maturities
        p = m.build_irswap(effective_date=spot, tenor=b, notional=1_000_000)   # par sentinel: strikes at par
        par = float(m.fair_rate(p))                    # decimal
        mat = m.maturity_date(p)                       # pd.Timestamp
        nodes[mat] = float(dense[mat])
        pars.append(par)
    risk = rl.Curve(nodes=dict(sorted(nodes.items())), convention=dense.meta.convention,
                    calendar=dense.meta.calendar, interpolation="log_linear", id=f"{m.id()}-RISK")
    meta = dict(m.meta()); meta["id"] = risk.id
    rh = RLIRSwapCurve(rl_curve_id=risk.id, rl_curve_handle=risk, fixings=m.index(), meta_data=meta)
    insts = [rh.build_irswap(effective_date=spot, tenor=b, fixed_rate=k, notional=1_000_000) for b, k in zip(tenors, pars)]
    with contextlib.redirect_stdout(io.StringIO()):
        sv = rl.Solver(curves=[risk], instruments=insts, s=[k * 100.0 for k in pars],
                       instrument_labels=list(tenors), id=risk.id, func_tol=1e-8, conv_tol=1e-10)
    if sv.result["status"] != "SUCCESS":
        raise RuntimeError(f"risk curve calibration failed: {sv.result}")
    live = [t for t in trades if m.maturity_date(t).date() > ref.date()]
    if not live:
        return {b: 0.0 for b in tenors}
    rebuilt = [rh.build_irswap(effective_date=m.effective_date(t), maturity_date=m.maturity_date(t),
                               fixed_rate=m.fixed_rate(t), notional=m.notional(t)) for t in live]
    d = rl.Portfolio(rebuilt).delta(solver=sv)          # index (type, solver, label); columns (local_ccy, display_ccy)
    blk = d.xs("instruments", level=0) if "instruments" in set(d.index.get_level_values(0)) else d
    s = blk.iloc[:, 0].astype(float); s.index = s.index.get_level_values(-1)
    return {b: float(s.get(b, 0.0)) + 0.0 for b in tenors}   # ccy per +1bp of each pillar par rate, holder-signed
```

- Units: **curve currency per +1bp** move in each pillar par rate, holder-signed (payer positive). The sum ≈ the portfolio's parallel par-rate PV01.
- Cost: one Solver per (market, tenor set). Memoise per timestamp (the portfolio expression runs once per timestamp).
- Gamma, if wanted later: repeat with every `par ± 1e-4` and use `(npv_up + npv_dn − 2·npv_0)/1²` of the rebuilt trades on each risk curve (v1 `arbs_adapter/swap.py:198-206`).
- Zero-strike collision applies to `rebuilt` and to pillar pars (negative-rate EUR/JPY history could cross 0; an exact 0.0 float is very unlikely, but the risk exists).
- Tenor sets must stay inside the dense curve's span. The RL_BASIC spline tail runs to the last node + 20Y.

---

## 5. FX for multi-currency

| Candidate | Verdict |
|---|---|
| `MDP.STIRFutures.FXForwardMDP.FXForwardMDP(source="BARCHART_FXFWD-RL")` | `get_pricer(request)` → `get_data`: reads `request.get("symbols"\|"tickers")` and `request.get("timestamp", "live")` (**not popped**). Returns `Dict[str, List[RLFXForwardPricer]]`; `RLFXForwardPricer.spot()`, `.forward_rate()`, `.points_decimal()`. Transport: **Barchart web scrape** through NordVPN SOCKS hosts (`atlanta.us.socks.nordhold.net`, ..., `FXForwardMDP.py:481-490`; credentials come from `.env` `NORDVPN_USER/PASS`). Local cache `FXForwardPricer_Cache`: 189 keys covering **3 dates** (2024-03-04, 2026-03-03/04). **Not usable offline for history.** |
| Citi Velocity Excel xccy (`MDP/CitiVelocityExcel/xccy`) | Excel/COM path. Excluded. |
| Anything else in ARBS | none found (grep of `MDP/Query/RVUtils/TB/utils/Caching`) |

**Recommendation:** the pricebt FX expression should read a user-supplied table (CSV/parquet of daily fixings, e.g. WM/Reuters 4pm or FRED H.10), keyed by NY date. It should not use ARBS. For a USD-only run: `fx: 1.0`.

---

## 6. Safety: what a backtest must do before touching ARBS

### 6.1 Hazards (all read in source)

| Hazard | Where | Triggered by |
|---|---|---|
| **Production Postgres (Supabase) with credentials baked into source** | `Caching/supabase_engine.py:39-43` (host/user/password defaults), `:67-69` `SUPABASE_ENABLED` read **once at import**, default True | Any `import Caching...`. `CurveStore.write_day(push_l2=True)` default (`curve_store.py:544, 566-573`) background-pushes. `read_raw_day` falls back to an **L2 pull on a local miss** (`curve_store.py:657-664`). `LayeredCacheMixin.L2_ENABLED/L2_READ/L2_WRITE = True` (`layered_cache_mixin.py:437-440`). |
| **Curve-store overwrite** | `ErisFuturesFetcher._write_curve_store_products` → `store.write_day("USD-SOFR-1D", d, [snap], overwrite=True)` (`ErisFuturesFetcher.py:233`). `_atomic_content_write(overwrite=True)` **unlinks every existing parquet in the partition** (`curve_store.py:1372-1374`). | Any Eris network fetch for a past date (both variants). It destroys a GSQUANT_RL row for that day in the shared `USD-SOFR-1D` asset. |
| Promotion writes | `_promote_eris_curve_store_day` → `write_day`, `write_analytics_day` (`IRSwapsMDP.py:1069-1166`) | Eris diskcache or network path, only when the partition is absent |
| Diskcache writes and LRU mutation | `DiskCacheMixin.default_cache_path` mkdirs `<root>/dump` (`DiskCacheMixin.py:58-67`); FanoutCache reads update sqlite access times | Constructing `IRSwapsMDP(source=Eris…)`; every lookup |
| Fixings dir writes | `_fetch_fixings` does `today_dir.mkdir(...)` **unconditionally** (`fixings_cache.py:273`). On the live path it writes `fixings.csv` and `_cleanup_old_cache_dirs(keep_last=3)` **deletes CSVs in older dated dirs** (`:319`, `:35`) | Every `get_pricer` (mkdir). Live path only when the cache lacks the T−1 business-day fixing or `as_of` is today after 08:00 ET. |
| Network (fixings) | `FixingsFetcher().get_fixings` (NY Fed); fallback FRED with an API key embedded at `fixings_cache.py:296` | Same live path. For a historical `as_of` the cache gate needs only that the newest valid CSV contains the row for `_last_usbd_before(as_of)`. That uses `_SOFRPublishCalendar` = US Federal holidays + **Good Friday** (`fixings_cache.py:14-34`), and `_has_date` tests index membership, so a NaN row counts. Historical dates are therefore offline-safe. An *unscheduled* SIFMA closure missing from that calendar could still fail the gate (not checked). The live USD vintage is `USD-SOFR-1D_fixings/2026-09-08/fixings.csv` (47 KB). The `2026-09-09` and `2026-09-27` dirs are **empty** `mkdir` side effects. |
| Network (Eris) + **process-global TLS verification disabled** | `ssl._create_default_https_context = ssl._create_unverified_context` (`ErisFuturesFetcher.py:293`); HTTP/2 client with `verify=False` | Any Eris fetch. It persists for the rest of the process. |
| GS API with embedded credentials | `GSQUANT/rl_basic/build.py:562, 580-587` | `GSQUANT-RL` store+diskcache miss |
| **Excel launch and UI automation** | `CitiVeloExcelCurveFetcher` → `supervisor.launch_excel` via `ctypes.windll.shell32.ShellExecuteW` (FEAS §5.2) | Only `CITIVELO_EXCEL*` sources. Never configure them. |
| Repo writes | bond reference data `update_reference_data` mkdirs and deletes inside the ARBS repo (FEAS §5.4) | Bond MDPs only (not this asset) |

**Orphan-process history** (FEAS §10): an unpatched Citi probe launched `EXCEL.EXE` (PID 12968) plus an anchor workbook. A second, orphaned `EXCEL.EXE` (PID 22608, about 400 MB, parent already exited) was left running by a concurrent research process and never attributed. The Excel add-in's memory only grows until a human restarts it. **A backtest must not use any Citi source.**

### 6.2 Minimum a user must set (in the asset config's import block, before any ARBS import)

```python
import os, sys
os.environ["ARBS_SUPABASE_ENABLED"] = "0"          # FIRST. Read once at import of Caching.supabase_engine.
sys.path.insert(0, r"C:\Users\chris\clee\ARBS")
# do NOT set ARBS_CACHE_DIR: it relocates CurveStore.default() and the fixings cache to an empty dir (curve_store.py:518-520, fixings_cache.py:171-192)
# leave ARBS_CURVE_STORE_SANITY_FILTER unset (default on): quarantined days then MISS (bulk+ignore_cache_miss: None; get_pricer: network path)
from MDP.IRSwaps.IRSwapsMDP import IRSwapsMDP
```

Optional belt-and-braces (all from v1 `tools/arbs_live.py:319-370`, known to work): `Caching.prod_db_guard.install_prod_db_guard()`; replace `MDP.IRSwaps.fixings_cache.fixings_cache._fetch_fixings` **and** `MDP.IRSwaps.IRSwapsMDP._fetch_fixings` with a read-only newest-CSV reader; set `CurveStore.write_day` / `write_analytics_day` to raise. With the last patch, an Eris network fetch still happens, but its store write raises inside `_write_curve_store_products`'s bare `except Exception: pass`, so the curve is still returned. `_promote_eris_curve_store_day` has **no** such try, so a patched `write_day` there propagates as an exception. In a fully offline run, also block sockets, which makes store misses fail fast with the unpack `ValueError`. The patch-and-guard machinery is **pricebt v1 code being stripped**. If kept at all, it belongs in the user's asset config or a user helper module, never in pricebt core.

---

## 7. Draft asset config: `usd_sofr_ois_interest_rate_swap.yaml`

Shape follows the v2 brief (imports / market / trade / pricing / portfolio). The block and variable names (`code:`, `market`, `trade`, `trades`, `trade_kwargs`, `pricebt_timestamp`) are **proposals** for the design author. Every ARBS method used below is verified in §3. The `code:` block is proposed so that helpers (`remark`, `delta_ladder`) need not be crammed into one-line expressions. It is executed once per config load, in the same namespace as `imports:`.

```yaml
# usd_sofr_ois_interest_rate_swap.yaml
asset: usd_sofr_ois_interest_rate_swap
currency: USD                       # every value function below returns USD; pricebt converts with the FX expression
risk_unit: "USD per +1bp"

imports: |
  import os, sys
  os.environ["ARBS_SUPABASE_ENABLED"] = "0"      # MUST precede any ARBS import (Caching/supabase_engine.py:67)
  sys.path.insert(0, r"C:\Users\chris\clee\ARBS")
  import pandas as pd
  from MDP.IRSwaps.IRSwapsMDP import IRSwapsMDP

code: |
  _MDP = IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC")     # one instance per process; store-backed, 2020-07-01..2026-08-20
  # _MDP = IRSwapsMDP(source="ERIS_EOD_LIVE-RL_BASIC-NOJUMPS")  # ONLY after a network warm run: 26 cached dates, and a
  #                                                             # miss overwrites curve-store partition USD-SOFR-1D/date=<d>

  def ny_date(ts):                                       # pricebt timestamp -> the New York trading date ARBS expects
      t = pd.Timestamp(ts)
      return (t.tz_convert("America/New_York") if t.tzinfo else t).date()

  def effective(m, spec):                                # "spot" | forward tenor FROM SPOT | a date
      if spec in (None, "spot"):
          return m.spot_date()                                          # datetime; ref + 2b
      if isinstance(spec, str):
          return m.calendar_advance(m.spot_date(), spec)                # NOT build_irswap(fwd=...), which counts from ref date
      return spec

  def remark(m, t):                                      # same swap rebuilt on market m (m's fixings); see note 06 s3.4
      memo = m.__dict__.setdefault("_pricebt_remark", {})               # per-market memo, dies with the market object
      hit = memo.get(id(t))
      if hit is not None and hit[0] is t:
          return hit[1]
      k = m.fixed_rate(t)                                              # decimal
      if k == 0.0:
          raise ValueError("zero strike collides with ARBS par sentinel")
      r = m.build_irswap(effective_date=m.effective_date(t), maturity_date=m.maturity_date(t),
                         fixed_rate=k, notional=m.notional(t))
      memo[id(t)] = (t, r)
      return r

  def theta(m, t, horizon="1b"):                         # one theta_components call per (market, trade, horizon)
      memo = m.__dict__.setdefault("_pricebt_theta", {})
      key = (id(t), horizon)
      if key not in memo or memo[key][0] is not t:
          memo[key] = (t, m.theta_components(remark(m, t), horizon))
      return memo[key][1]

  def load_market(ts):                                   # store-only: None on a miss, never network, never a store write
      d = ny_date(ts)
      return _MDP.bulk_get_data(dict(curve_name="USD-SOFR-1D", timestamps=[d], ignore_cache_miss=True)).get(d)

  def alive(m, t):
      return m.maturity_date(t).date() > m.reference_date().date()

  # delta_ladder(m, trades, tenors): paste Option B from note 06 s4 verbatim

# (b) evaluated ONCE per timestamp by pricebt; the result is reused for every trade/function at that timestamp.
#     dict(...) is rebuilt on every evaluation: REQUIRED, ARBS pops curve_name/timestamp from the request.
market: 'load_market(pricebt_timestamp)'
#     -> RLIRSwapCurve, or None when the store has no row for that NY date (pricebt: market unavailable)
#     The user's original shape also works but may hit the network and write the store on a miss (note 06 s1.5):
#     market: '_MDP.get_pricer(request=dict(curve_name="USD-SOFR-1D", timestamp=ny_date(pricebt_timestamp)))'

# (c) trade construction from opaque kwargs (pricebt does not interpret them):
#     trade_kwargs: effective ("spot" | "1Y" | date), tenor ("10Y") or maturity (date),
#                   fixed_rate (DECIMAL, e.g. 0.0425) or "par", and notional (USD, + payer / - receiver) or bpv (USD per +1bp, + payer)
trade: >-
  market.build_irswap(
      effective_date=effective(market, trade_kwargs.get("effective", "spot")),
      tenor=trade_kwargs.get("tenor"),
      maturity_date=trade_kwargs.get("maturity"),
      fixed_rate=(-0.0 if trade_kwargs.get("fixed_rate", "par") == "par" else float(trade_kwargs["fixed_rate"])),
      notional=trade_kwargs.get("notional"),
      bpv=trade_kwargs.get("bpv"))
#     -> rateslib.IRS; a literal fixed_rate of 0.0 means PAR (sentinel); reject it upstream if a 0% strike is ever meant

# (d) per-trade functions, evaluated on (market, trade); trade may have been built on an EARLIER market
pricing:
  npv:        '0.0 if not alive(market, trade) else market.npv(trade)'
              # USD, dirty PV, holder sign (+ = asset to holder); safe on a later market (rebuilds with its fixings)
  pv01:       '0.0 if not alive(market, trade) else market.pv01(remark(market, trade))'
              # USD per +1bp parallel par-rate move, holder sign (payer > 0); includes the current period's accrued portion
  par_rate:   'float("nan") if not alive(market, trade) else market.fair_rate(remark(market, trade)) * 1e4'
              # bp (decimal x 1e4); par of the REMAINING swap, including realised fixings of the current period
  fixed_rate: 'market.fixed_rate(trade) * 1e4'
              # bp; the strike
  notional:   'market.notional(trade)'
              # USD, signed (+ payer)
  carry_bps:  '0.0 if not alive(market, trade) else market.carry_bps_running(remark(market, trade), "1m")'
              # bp of rate over 1m, static curve, RECEIVER-positive; raises if the swap matures within 1m
  roll_bps:   '0.0 if not alive(market, trade) else market.roll_bps_running(remark(market, trade), "1m")'
              # bp of rate over 1m, RECEIVER-positive
  carry_ccy:  '-carry_bps * pv01'
              # USD over 1m, holder sign, APPROXIMATE (first order); assumes pricebt lets functions reference earlier results
  theta_1b:   '0.0 if not alive(market, trade) else theta(market, trade, "1b")["theta"]'
              # USD over 1 business day, PV-DECAY-positive (theta = pv_sod - pv_eod); holder P&L = -(forwarding+rolldown)
  rolldown_1b: '0.0 if not alive(market, trade) else theta(market, trade, "1b")["rolldown"]'
              # USD, decay-positive component; do NOT add to *_bps

# (e) portfolio-level functions on (market, trades of THIS asset)
portfolio:
  delta_ladder: 'delta_ladder(market, trades, ("2Y", "5Y", "10Y", "30Y"))'
                # {tenor: USD per +1bp in that pillar par rate}, holder sign (payer > 0); sum ~ total parallel pv01

# FX into the reporting currency: ARBS has none offline (note 06 s5)
fx_to_report: '1.0'        # USD reporting; for other assets read a user FX table keyed by ny_date(pricebt_timestamp)
```

Things the draft deliberately avoids: `dv01`, `gamma`, `dollar_carry` (they raise); `resolve_pricable` (flips direction); `build_irswap(fwd=<tenor>)` (counts from the reference date); `fair_rate` on an un-remarked trade from another day; any Citi or `GSQUANT` source for USD.

---

## 8. Open questions for the design author

1. **Market reuse is part of correctness, not just speed.** pricebt must evaluate `market:` exactly once per (asset, timestamp) and pass the same object to `trade:`, `pricing:` and `portfolio:`. `fair_rate`'s memo only hits for trades built on that same object (identity check), and ARBS has no memo of its own (§1.7).
2. **Coupon cash between marks.** gs-quant-style P&L on a swap needs the cash that left the PV. Should the asset contract gain a `cash(market, trade, t0, t1)` expression (`remark(m,t).cashflows(curves=m.handle())` filtered on `Payment`), or does v2 follow gs_quant exactly (check how `gs_quant.backtests` treats coupons of held swaps)?
3. **NOJUMPS or RL_BASIC.** If the user specifically wants NOJUMPS (the notebook default), someone must run a network-enabled warm over the backtest dates once. That run overwrites `USD-SOFR-1D` store partitions. An alternative is a user-side reader of the 18k-node `ERIS_RL_BASIC` rows already in store asset `USD-SOFR-1D` (2020-06-22..2026-08-12), wrapped in `RLIRSwapCurve`: this is exactly the curve NOJUMPS would build, without the network. It needs the user's approval, since it bypasses `IRSwapsMDP`.
4. **Offline guard ownership.** v1's guard (`tools/arbs_live.py`) is being stripped. Should a copy survive as a user-side helper that asset configs may import, so a CI run can never reach the network, the database or Excel?
5. **Non-USD assets** (`GSQUANT-RL` for EUR/GBP/JPY) import gs_quant inside ARBS and end 2026-08-03 (fixings 2026-08-06). Is that acceptable as the multi-currency test bed, or should non-USD be demonstrated with a synthetic/user-supplied curve?
6. **Timestamp injected by pricebt:** tz-aware `pd.Timestamp` is assumed above (`ny_date` handles both). Pin it in the spec, together with the EOD stamp pricebt assigns (the ARBS stamp is 15:00 NY for Eris).
