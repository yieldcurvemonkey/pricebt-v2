# MDP feasibility spike: real curves and bond quotes from ARBS, offline

Run date 2026-09-25 (machine clock), interpreter `C:\Users\chris\anaconda3\envs\stir\python.exe` (3.13.5; rateslib 2.7.1, QuantLib 1.41, duckdb 1.4.4, diskcache 5.6.3, pyarrow 21, pandas 2.3.1). All scripts are in `C:\Users\chris\clee\gsquant-temp-claude\pricebt\scratch\mdp_probe\` (index at the end).

## 1. Verdict

**Yes, real data works offline, with zero network, COM, Excel or DB activity, for swaps (EOD and 1-minute) and UST bonds (EOD and 1-minute).** It only works after five patches (numbered in section 5), because the unpatched ARBS MDP layer will (a) open the production Postgres pooler, (b) launch Excel and automate its UI, (c) write into the ARBS repo, and (d) silently return stale or zero-priced data. Every failure below was reproduced, not inferred.

Recommended shape for the pricebt test harness, in order of preference:

1. **Portable fixtures** (5.5 MB, exported by `export_fixtures.py`, verified bit-identical to ARBS): no ARBS, no D: drive. Use for CI and the reported results.
2. **`DirectCurveStore`** (`direct_store.py`, about 90 lines, pyarrow + rateslib only) reading the live store when present. Matches ARBS 10Y par to 9e-16 across four source strings / five store assets (seven request cases).
3. **ARBS `IRSwapsMDP` / `FixedRateBondsMDP` via `arbs_recipes.py`** when you want to exercise the user's real MDP classes. Needs `install()` first.

## 2. Bootstrap facts

| Question | Answer (verified) |
|---|---|
| Python env | `stir`. Needs QuantLib (hard dependency even on the rateslib path: business-day validation and UST calendars), duckdb, diskcache, sqlalchemy, httpx, pytz, platformdirs. |
| cwd required? | No. `sys.path.insert(0, r"C:\Users\chris\clee\ARBS")` is enough; `MDP`, `Query`, `Caching`, `utils` are top-level packages. Ran from the scratch dir. |
| Import time | `import MDP.IRSwaps.IRSwapsMDP` 1.26 s (+91 MB). Fresh process to first IRS pricer: 2.5 s total (0.5 s rateslib/pandas, 1.8 s ARBS imports and patches, 0.12 s first pricer), RSS 263 MB. |
| `.env` | Names only: CHASE_USERNAME, CHASE_PASSWORD, SCHWABDEV_APP_KEY, SCHWABDEV_APP_SECRET, SCHWAB_TOTP_SECRET, NORDVPN_USER, NORDVPN_PASS, SDR_CACHE_PATH, FIXINGS_CACHE_PATH. Nothing under `MDP/ Caching/ Query/ utils/` loads dotenv, and no recipe path needs any of them. |
| Process env already set | `ARBS_DATA_ROOT=D:\ARBS_DATA\repo`, `ARBS_COMPUTED_TS_DIR=...\ARBS\data\ts`. Irrelevant to the recipes. Do NOT set `ARBS_CACHE_DIR`: it would relocate `CurveStore.default()` (`curve_store.py:518-520`) to an empty dir. |
| rateslib licence | Import emits a `LicenceNotice` warning (non-commercial licence). Filter it in tests. |

### Where the data actually lives

`CurveStore.default()` (`Caching/curve_store.py:396-403`, base dir `:517-533`) resolves to `C:\Users\chris\AppData\Local\ARBS\Cache\curve_store`, a symlink to `D:\ARBS_DATA\appdata\Cache\curve_store`. The `ARBS\.cache\curve_store` in the repo is a stale March copy; ignore it.

- Layout (`curve_store.py:386-390`): `raw/asset=<ASSET>/date=<YYYY-MM-DD>/<sha256>.parquet` and `analytics/...`. 104,255 parquet files, raw 3.57 GB. Enumerating it is slow (a `find` took 19 minutes, and `du` on ARBS `.cache`/`data` exceeded 2 minutes): never walk it; list `asset=*/date=*` directories only.
- Raw schema (`curve_store.py:1204-1223`): `timestamp_utc, timestamp_local, trading_date, session_minute, curve_name, cfg_hash, reference_key, interpolation, source_variant, node_dates list<date>, discount_factors list<f64>, spline_knots, spline_endpoints`. That is a complete, self-contained description of an `rl.Curve`.
- Reconstruction (`curve_store.py:909-1006`): `rl.Curve(nodes={rl.dt: df}, id=reference_key, convention, calendar, modifier, interpolation[, t=knots, endpoints])`, conventions from `RATESLIB_CURVE_DEFINITIONS`, fallback `act360/nyc/mf` (correct for USD).
- Fixings: `...\AppData\Local\ARBS\MDP\IRSwaps\ARBS\MDP\IRSwaps\Cache\fixings_cache\<curve>_fixings\<date>\fixings.csv` (platformdirs nests the app name twice). `USD-SOFR-1D`: 2,106 rows 2018-04-02..2026-09-04, decimals, and the newest file is **descending**. Sort it.
- Bonds: `%LOCALAPPDATA%\ARBS\Cache\diskcache\dump\FedInvest_Prices_Cache` (96 MB, keyed by `pd.Timestamp`, value a DataFrame `cusip,type,coupon,offer_price,bid_price,eod_price`); reference table `ARBS\MDP\FixedRateBonds\reference_data_cache\ust_reference_data\fiscaldata\<date>\<date>.parquet` (1,790 rows, 1979..2026 including matured issues, so one file serves any backtest date); Citi minute/daily tag cache under `D:\ARBS_DATA\appdata\ARBS\Cache\citivelo_excel\{MI01,DAILY,HOURLY}\CLOSE\*.parquet`.

## 3. Coverage (directory listing plus parquet footers)

Only USD assets are relevant to the test bed. Days are day-partitions; "rows/day" is snapshots.

| Store asset | Days | First | Last | rows/day | Notes |
|---|---|---|---|---|---|
| `USD-SOFR-1D-CITIVELOEXCEL` (Citi EOD) | 5,524 | 2005-01-03 | 2026-09-04 | 1 | Holes: 2026-09-02, 09-03. Row stamped 17:00 ET; wrapper re-stamps 15:00 NY. 45 nodes. |
| `USD-SOFR-1D-CITIVELOEXCELMIN` (Citi minute) | 1,555 | 2021-09-14 | 2026-09-04 | see below | 1.0-minute step, seconds always 0. Holes: 2026-08-27, 08-28. |
| `USD-SOFR-1D-Q12STIRT` (SR3 12-quarter, minute) | 2,070 | 2018-06-01 | 2026-08-27 | 1,381 | Full CME session 18:00 ET prev day to 17:00 ET, 1-min. One gap >5 days (2018-11). Hole 2026-06-30. |
| `USD-SOFR-1D-Q16STIRT` (minute) | 206 | 2024-01-02 | 2026-08-27 | 961 early, 1,381 later | Five multi-month gaps (2024-07, 2025-01, 2025-05, 2025-09, 2026-04). |
| `USD-SOFR-1D` (GSQUANT EOD, 18,358 nodes) | 2,071 | 2018-08-01 | 2026-09-04 | 1 | No holes in the last 60 days. Stamp 15:00 NY. |
| `USD-OIS` (GSQUANT EOD) | 5,235 | 2005-10-24 | 2026-09-04 | 1 | Hole 2026-08-12. Older partitions store a cache-key string as `reference_key` (e.g. `2012-06-15-GSQUANT-rl_basic_USD-OIS`), so ARBS logs a convention-fallback warning per date (falls back to act360/nyc/mf, correct for USD). |
| `USD-SOFR-1D-RLBASIC` (Eris EOD, 24-node spline) | 1,423 | 2020-07-01 | 2026-08-20 | 1 | Gaps in H2 2020 to Jan 2021 and 2026-08-13..18. |
| `USD-SOFR-1D-CITIVELO` (older Citi workbook, minute) | 930 | 2023-01-02 | 2026-07-24 | 1,320 | Naive ET datetimes. Superseded. |
| `USD-SOFR-1D-ERISLIVE` | 2 | 2026-07-23 | 2026-07-24 | 42 / 992 | Not usable. |
| Also present | | | | | `USD-SOFR-1D-CME`, `-STIR-CME`, `-STIR-LCH`, `USD-OIS-CME`, `USD-OIS-STIR-{CME,LCH}` (EOD, ~2018/2010 to 2026-09-04, 22-29 nodes), `USD-FEDFUNDS-1D-CITIVELOEXCELMIN`, plus CAD/EUR/GBP/JPY Citi EOD and minute. The direct reader reads all of them; ARBS has no source string for the CME/LCH ones. |

**Minute grid of `USD-SOFR-1D-CITIVELOEXCELMIN`** (rows per sampled day): 2021-09-14 239 (20:00-23:58 ET only, the store starts mid-day); 2021-12-15 1,439 (00:00-23:58); 2022-06-15 and 2023-01-17 1,320 (01:00-22:59); 2024-06-14 990 and 2025-03-14 957 (01:00-17:59, shortened session); 2026-01-15 1,290; 2026-08-05 1,319 (01:00-22:59). The session length changes over time, so a minute-grid request must be checked against the day's actual window (a 23:30 ET request on 2026-08-05 correctly raised `SnapshotMiss`).

**Bonds.** FedInvest EOD cache: 4,182 days, 2010-01-04..2026-08-21 (5 stray 2006 days), essentially complete on Fed business days (11 missing weekdays in 16 years, none since mid-2025). **13 dates carry `eod_price == 0.0` for every note and bond** (bid populated): 2014-09-12, 2014-11-21, 2026-07-09/10/13/17/20/24/27, 2026-08-07/18/20/21. Citi tag cache (offline): `MI01` 410 UST PRICE tags (some with 555k one-minute rows 2021-01-24..2026-09-04; median first date 2026-03), `DAILY` 893 tags (back to 2006-2010), `HOURLY` 97 tags. The `USTS_CITIVELO-RL` source serves EOD and intraday from it.

## 4. Working recipes (all verified, zero network / process / COM attempts recorded)

Request dict always `{"curve_name": <name>, "timestamp": <ts>, **extra}`, passed to `mdp.get_pricer(...)`. **`get_data()` pops `curve_name` and `timestamp` from the dict** (`IRSwapsMDP.py:1985-1986`, `FixedRateBondsMDP.py:810-811`): build a fresh dict per call. Timings are fresh-process cold, then warm; "cold" includes the first parquet read of that day.

| Recipe id (`arbs_recipes.IRS`) | `IRSwapsMDP(source=...)` | curve_name | timestamp type | extra kwargs | cold / warm | Returned stamp |
|---|---|---|---|---|---|---|
| `irs.usd_sofr.citi.eod` | `CITIVELO_EXCEL-RL` | `USD-SOFR-1D` | `datetime.date` (pd.Timestamp at exact midnight also means EOD) | `offline=True` | 190 ms / 2.5-4 ms; new dates 9-25 ms | 15:00 NY, tz-aware |
| `irs.usd_sofr.citi.min` | `CITIVELO_EXCEL-RL` | `USD-SOFR-1D` | tz-aware `datetime` (naive is silently localised to NY) | `snapshot_policy=SnapshotPolicy.strict(minutes=5)` | 0.8-1.1 s first minute; 300-525 ms first minute of each new day; **2.3 ms** for later minutes (61 instants in 0.14 s) | the served minute; lag reported in `meta` |
| `irs.usd_sofr.gsquant.eod` | `GSQUANT-RL` | `USD-SOFR-1D` or `USD-OIS` | bare `datetime.date` only (asserts `type(ts)==date`) | none | 1.9-2.1 s / 30-95 ms | date |
| `irs.usd_sofr.eris.eod` | `ERIS_EOD_LIVE-RL_BASIC` | `USD-SOFR-1D` | date | none | 160 ms / 16-21 ms | 19:00 UTC |
| `irs.usd_sofr.stir_q12.min` | `BARCHART_STIRF-RL` | `USD-SOFR-1D-Q12STIRT` (`USD-SOFR-1D` alone raises: four configs share that reference key) | tz-aware datetime; a bare date means 17:00 NY | `ignore_cache_miss=True` (offline-safe, but see hazards) | 0.85 s / 115-165 ms (61 instants: 119 ms each); **evening 18:00-23:59 ET minutes 2.2-24 s** | `handle().timestamp` (meta may echo the request) |
| `irs.usd_sofr.stir_q16.min` | `BARCHART_STIRF-RL` | `USD-SOFR-1D-Q16STIRT` | same | same | ~110 ms warm | same |
| legacy `CITIVELO` | `CITIVELO` | `USD-SOFR-1D` | naive ET datetime | none | 446 ms / 290 ms | works, superseded |

Bonds, `FixedRateBondsMDP(source=...).get_pricer({"cusips": [...], "timestamp": ts})` returns `Dict[alias, RLFixedRateBondPricer]` (aliases `CT<n>`, `O`/`OO`/`OOO<n>`, `Ox<rank><tenor>`, or CUSIPs):

| Recipe | source / ctor | ts | Timing | Notes |
|---|---|---|---|---|
| `frb.ust.fedinvest.eod` | `USTS_FEDINVEST_WSJ_LIVE-RL` | `datetime.date`, 2010-01-04..2026-08-21 | 1.36 s first-ever call, then 13-40 ms for 4 bonds | clean price from FedInvest. Aliases resolve as-of the date (CT10 was 912828LY4 in 2010, 91282CQQ7 in 2026-08). |
| `frb.ust.citi.eod` | `USTS_CITIVELO-RL`, ctor `offline=True` | date | 1.5-3.2 s new date, 8 ms warm | ~400 bond subset; clean price differs from FedInvest by up to 0.06 (CT10 2026-08-05: 98.125 vs 98.0625). |
| `frb.ust.citi.min` | same | tz-aware datetime | 266 ms cold, 8-9 ms warm | quote stamp in `meta["timestamp"]` (served 10:30 and 10:29 for a 10:30 request). |

`bulk_get_data(timestamps=[...], cusips=[...])` exists for the FedInvest source (`FixedRateBondsMDP.py:2330`) and returns `defaultdict(date -> {alias: pricer})`.

## 5. The five patches (plus one tripwire), and why each is mandatory (`arbs_offline.py`, `arbs_offline_frb.py`, `probe_common.py`)

1. **`ARBS_SUPABASE_ENABLED=0` before any `import Caching`.** `Caching/supabase_engine.py:39-67` defaults enabled with a fully credentialed production URL baked into the source; `ensure_schema()` runs DDL from read paths; the flag is read once at import. ARBS's own `tests/conftest.py` says the same. Belt and braces: `Caching.prod_db_guard.install_prod_db_guard()` (hooks `psycopg2.connect`). L2 read/write defaults on in `LayeredCacheMixin` (`layered_cache_mixin.py:427-440`) are neutered only by that flag.
2. **Never let a request reach `CitiVeloExcelCurveFetcher.snapshot`.** Observed twice (my first probe, then a citi-EOD request past the store): the store hit succeeded in 190 ms, then `_citivelo_excel_store_fixings -> fixings_for -> citi_fixings -> CitiVeloQuotes.series(max_staleness=12h)` found a stale tag cache and **launched `EXCEL.EXE /x <anchor>.xlsx` via `ctypes.windll.shell32.ShellExecuteW`** (`supervisor.py:362`, not `subprocess`), then `wait_for_addin` hung for minutes (it also "presses Login" on an already-running Excel). Fixes applied: replace `MDP.IRSwaps.CITIVELO_EXCEL.fixings.citi_fixings` with an empty-series lambda, force every `CitiVeloQuotes` to `offline=True, auto_launch=False`, make `supervisor.launch_excel` raise, and the guard also blocks `ShellExecuteW`, `os.startfile`, `Popen` containing "excel", `win32com` Dispatch/GetActiveObject, and all non-loopback sockets/DNS (recorded in `pc.NET_ATTEMPTS/PROC_ATTEMPTS`, self-tested in `probe_20`).
3. **Replace `_fetch_fixings`** (`MDP/IRSwaps/fixings_cache/fixings_cache.py:237-321`; patch both the module attribute and the copy imported by name into `IRSwapsMDP`). Unpatched it does `mkdir(today)` in the user's cache unconditionally (`:269-273`) and, when the newest cached CSV lacks the last US business day before `as_of_date` (always true for `as_of=today` here: newest cache 2026-09-08, clock 2026-09-25), does a live NY Fed / FRED HTTP pull. The replacement reads the newest cached `fixings.csv` read-only.
4. **Replace `update_reference_data("fiscaldata")`** for bonds (`ust_reference_data.py:71-123`; mkdir `:89`, cleanup `:121`). It keys on `_last_ust_govt_business_day(date.today())`, not the request date, so on a cold clock it (a) `mkdir`s a dated dir **inside the ARBS repo**, (b) HTTP-fetches fiscaldata.treasury.gov, (c) writes a parquet and `_cleanup_old_cache_dirs(keep_last=3)` **deletes older dated dirs**. That cleanup is what produced the ` D .../fiscaldata/2026-08-25..27` lines already in `git status` before this spike. The replacement reads the newest cached parquet.
5. **Redirect every diskcache** with `DiskCacheMixin.CACHE_ROOT = scratch`. Opening or reading a `FanoutCache` mutates sqlite (LRU access times) and the bond MDP writes every pricer it builds into the 8.1 GB `FixedRateBondPricer_Cache`. Do not use `ARBS_CACHE_DIR` for this (see section 2).

## 6. Pricing results

**Swap** (`p.build_irswap(fwd="0D", tenor="10Y", notional=1e6)`; `+notional` is the payer: NPV rose as rates rose). 10Y spot-starting par on 2026-08-05, per source: Citi EOD 4.2091%, Citi 10:30 4.2203%, GSQUANT 4.2279%, Eris 4.2279%, Q12 SR3 curve 4.0712% (it has only about 3-4 years of information: its 10Y is extrapolation, use it for the front end). PV01 ~815 per 1mm. Timings: `build_irswap` 12-23 ms, `npv(swap)` 12-17 ms (rebuilds `rl.IRS` with the fixings series each call, `RLIRSwapCurve.npv` `:202`), `fair_rate` ~0 (memoised), `pv01` 0.1 ms, `carry_bps_running` 13 ms, `roll_bps_running` 14 ms, `carry_and_roll_bps_running` 30 ms, `theta_components` 41 ms (returns cashflows / forwarding / rolldown / option / theta).

**Equity-curve primitive works, including seasoned swaps.** Swap struck at par on 2026-08-05 (K=4.2091%, eff 2026-08-07, mat 2036-08-07, NPV0 = 0.00) marked with each later date's pricer: 08-06 +3,874 (10Y par +4.85bp), 08-07 +2,019, 08-10 +6,172, 08-19 +3,090, 09-02 +16,012, 09-04 +14,843. Seasoned marks need the fixings series on the *marking* pricer (2,083 rows for USD; `p.index()`); no lookahead (last fixing = day before).

**Not available on the rateslib backend:** `dv01`, `gamma`, `dollar_carry` raise `NotImplementedError` (`RLIRSwapCurve.py:222-242`). Only `pv01`, `carry_bps_running`, `roll_bps_running`, `theta_components` exist, which is what a delta/carry/roll decomposition can use.

**UST** (`RLFixedRateBondPricer`, quote-driven): CT10 on 2026-08-05 = 91282CQQ7, cpn 4.375, mat 2036-05-15, clean 98.0625, ytm 4.6220 (independently reproduced from the fixture with `rl.FixedRateBond(spec="us_gb_tsy").ytm`), dirty 99.0493 per 100, mod. duration 7.781, convexity 0.721, ~1 ms each. Traps: `dirty_price(notional=...)` returns per-100 regardless of notional (value = dirty/100 x notional); `bpv(n)` is risk per 100 (7.707), not per notional; **`npv()`, `accured()`, `zspread()`, `resolve_pricable()` raise `NotImplementedError`**; `pv01()` with no notional raises `TypeError`; **`time_to_maturity()` is broken on rateslib 2.7.1** (`ValueError: frequency must be supplied for 'ActActICMA'`). pricebt's bond `value` must be `dirty_price/100 x notional`.

Public surface of `RLIRSwapCurve`: build_irswap, build_pricable, build_stirf, calendar, calendar_advance, carry_and_roll_bps_running, carry_bps_running, daycounter, dollar_carry, dv01, effective_date, fair_rate, fixed_rate, gamma, handle, id, index, maturity_date, meta, nodes, notional, npv, pv01, reference_date, resolve_pricable, roll_bps_running, roll_curve, spot_date, theta_components. `RLFixedRateBondPricer`: accured, bpv, build_fixed_rate_bond, build_pricable, build_schedule, calendar, calendar_advance, clean_price, convexity, coupon, dirty_price, id, issue_date, maturity_date, meta, mod_duration, notional, npv, pv01, reference_date, resolve_pricable, settlement_date, time_to_maturity, ytm, zspread.

## 7. Pickle, threads, memory

- **Pickle works** for both pricers and for `rl.Curve` and `rl.IRS`: Citi minute pricer 53.9 KB (dumps 0.4 ms, loads 0.2 ms, reprice bit-equal); GSQUANT 18k-node pricer 642 KB (10 ms / 4 ms); bond pricer 0.6 KB; `deepcopy` 0.3 ms / 36 ms. `rl.from_json(curve.to_json())` round-trips in 0.4 ms with equal rates. Multiprocessing transport is therefore trivial (send the pricer, or the raw store row).
- **Thread-safe, but no speedup.** 200 swaps on one shared pricer: serial 3.19 s, 8 threads 3.69 s, results bit-identical. `get_pricer` from 8 threads over 64 minutes equals serial. CPU parallelism needs processes. Unlocked memo dicts in `RLIRSwapCurve` (`:195-199`, `:266-274`) are benign races.
- **Memory:** 100 held Citi minute pricers +1.1 MB (about 11 KB each, plus shared numpy); 10 GSQUANT 18k-node pricers +41 MB (4.1 MB each). Fixings series 2,083 rows is copied per pricer by `fixings_for`.

## 8. Miss paths and silent wrong answers (the harness WILL hit these)

| Request | What happens |
|---|---|
| Citi minute, outside coverage, `strict` policy | `SnapshotMiss` (LookupError) in 2-360 ms. Good. Default `legacy` policy is nearest-in-either-direction, unbounded, `allow_future=True`: **lookahead**. Always pass `strict(minutes=N)`. |
| Citi EOD past the store, `offline` | **Silently returns the last cached day** (asked 2026-09-08, served 2026-09-04) with no error. Unpatched, `offline` unset: Excel path. |
| Citi EOD on a store hole (09-02, 09-03) | Built offline from the Citi par-quote tag cache with a rateslib solver (`provenance=rateslib_spec`, 1.9-3.4 s, prints a `levenberg_marquardt` SUCCESS line to stdout). Correct but slow and noisy. |
| Citi EOD on a US holiday (2026-07-03) | Returns a stored curve (the store has some holiday partitions). Use your own calendar. |
| GSQUANT past store / holiday / weekend | Past store: `requests.ConnectionError` to `idfs.gs.com` after 2.8 s (typed). Holiday/weekend: `ValueError ... not a business day` (QuantLib calendar) in 190 ms. |
| Barchart, `ignore_cache_miss=True`, past store | `RuntimeError: IRSwapsMDP could not build a curve` (263 ms). On a **hole day** (2026-06-30) it **silently serves the prior snapshot** (2026-06-29 21:00 UTC): that flag enables a 7-day prior-timestamp lookback. Without the flag a miss goes to `builder.build_curve` and live futures data: measured `requests.ConnectionError` to `www.barchart.com` (`/futures/quotes/BTC/interactive-chart`, the futures-quote scrape) after 596 ms under the guard, typed and fast. |
| Barchart evening minutes (18:00-23:59 ET) | Candidate trading dates differ (CME 17:00-CT roll vs ET date), so `read_raw_nodes` takes the DuckDB glob over all 2,070 partitions: 2.2 s warm, **24 s cold**. `DirectCurveStore` does it in 6 ms. |
| Barchart ambiguous name `USD-SOFR-1D` | `ValueError` listing four candidate configs. |
| Eris past store | `ValueError: not enough values to unpack` after 7.6 s of retries against the Eris FTP (misleading). |
| Bonds: date missing / holiday / pre-2010 / bad CUSIP | **Returns a short or empty dict, does not raise**; logs a warning; 7-15 s of network retries under the guard. |
| Bonds: FedInvest zero-EOD days | Builds a pricer with `clean_price=0` (ytm ~1,800%) silently. Only the WSJ-buffer branch gates `50 <= price <= 250`. |
| Stored curve quality | ARBS filters known-bad snapshots on read (`Caching/curve_sanity.py`, default on): degenerate all-1.0 DFs (2026-07-01), off-modal shape (2026-07-21), anchor-block jump (2026-07-30, and 420 rows of 2022-06-14/15 flagged). The direct reader sees raw rows. |
| Non-USD seasoned marks | GBP/EUR EOD pricers build fine (par 4.4848% / 2.8964%) but have **empty fixings offline** (non-USD official sources are HTTP), so a seasoned `npv` raises `ValueError: effective date ... before the initial node date`. Only `USD-SOFR-1D` has offline fixings unless `_publisher_fixings` is patched to read the cached `GBP-SONIA_fixings` etc. |
| JPY-TONAR-1D EOD | Store ends 2026-08-07; later dates fall through to Excel (hang unpatched). |

`arbs_recipes.py` converts all the silent cases into `StaleSnapshot`/`KeyError` by validating the served stamp against the request (EOD: same day; minute: 0 <= lag <= tolerance, using `handle().timestamp`) and bond clean price in [50, 250].

## 9. Timestamp and unit conventions the harness must normalise

EOD stamps differ per source: Citi wrapper 15:00 NY (store row 17:00 ET), GSQUANT bare date (15:00 NY), Eris 19:00 UTC, Barchart bare date maps to 17:00 NY. `resolve_request` reads exact midnight as EOD; ask for `00:00:01` for a real minute. Fixings are DECIMAL from `_fetch_fixings` and PERCENT inside `RLIRSwapCurve`. `fixed_rate()` returns decimal, `rl.IRS` carries percent, `fair_rate` decimal. Store hole and holiday handling is the caller's job.

## 10. Side effects of this spike (before/after)

- `git -C ARBS status --porcelain`: **identical** before and after (23 lines of the user's in-progress work, untouched). No `.pyc` written into ARBS (`sys.dont_write_bytecode`, checked with a scoped `Get-ChildItem` over MDP/Query/Caching/utils/TB/RVUtils).
- **Pre-patch first probe** (00:00-00:09): `mkdir` of an empty `fixings_cache\USD-SOFR-1D_fixings\2026-09-25` (removed); launched `EXCEL.EXE` (PID 12968, parent = my probe) plus `excel-scratch\velocity_signin_anchor-20260925-000149.xlsx` (Excel killed, file removed).
- **Pre-redirect IRS probes** (00:11): touched `cache.db` LRU timestamps in the user's `ERIS_EOD_LIVE-RL_BASIC`, `GSQUANT-RL_CURVE_CACHE`, `BARCHART_STIRF-RL_CURVE_CACHE` diskcaches (no key changes). After redirect a rerun left their mtimes unchanged. `FixedRateBondPricer_Cache` and `FedInvest_Prices_Cache` never touched (bond probes used a 96 MB scratch copy).
- **Not attributable to this spike:** an orphaned `EXCEL.EXE` (PID 22608, started 00:22:32, parent 40304 already exited, anchor `...-002232.xlsx`) is still running. It appeared after I had killed mine and while sibling research processes were active (e.g. PID 25316 `p07b_pnl_decomp_pv01.py`). At that moment my own guard did not yet cover `ShellExecuteW`, so I cannot rule my code out with certainty; I judged it most likely a sibling's and left it alone. It holds about 400 MB, and ARBS code comments (`FixedRateBondsMDP._citivelo_option`) record the add-in's memory only growing until a human restarts it, so someone should close it.

## 11. Design implications for pricebt

1. **The MDP contract is `get_pricer(request_dict) -> pricer` and it is agnostic in practice**: IRS returns `RLIRSwapCurve`, bonds return `Dict[alias, RLFixedRateBondPricer]`. A pricebt adapter must handle the dict-of-pricers case (one call, many CUSIPs) and the fresh-dict-per-call rule.
2. **Adapters must validate, not trust.** Serve-stamp vs request, price sanity, short dicts. Put a `max_staleness` / `require_exact` field in the MDP config and default it strict.
3. **Cache pricers by (source, name, snapshot key)** and reuse across the swaps of one timestamp: the Citi minute pricer costs 2.3 ms warm but 300-500 ms on the first minute of a day, so iterate a day's minutes in order. Barchart evening minutes and GSQUANT are the slow paths (0.1-24 s); prefer the direct reader for them.
4. **Pricer lookups pricebt must expose** (confirmed needed by seasoned marking): `reference_date`, `calendar`, `calendar_advance`, `fixings` series for the marking date, `spot_date`, `handle`. For bonds: `settlement_date`, `clean_price`, `ytm`, `dirty_price` per 100.
5. **Sub-layer value functions map onto real methods**: delta = `pv01` x dY, carry = `carry_bps_running`, roll = `roll_bps_running` / `theta_components["rolldown"]`; `dv01` and `gamma` are absent on rateslib, so convexity must come from rateslib's own bump (`rl.IRS.gamma`/`delta` with a solver) or a two-curve reprice, not from the ARBS wrapper.
6. **Use intraday-safe policy by default**: strict as-of, no future snapshot. Config keys should be plain data (`{"snapshot_policy": {"method": "asof", "max_lag_minutes": 5}}`), turned into the ARBS objects by a resolver, so YAML stays library-agnostic.
7. **Fixtures make tests deterministic**: export once, commit, and have the test MDP read them with the same loader. Recorded parity with ARBS: par-rate difference 0.0 to 8.9e-16 across Citi EOD/minute, GSQUANT, Eris, Q12 day and evening minutes; UST ytm 4.6220 identical.
8. **Product routing already exists in ARBS**: `MDP/MultiProductMDP.py` maps `request["product"]` (`IRS`, `STIRFUTURE`, `FRB`) to a per-product MDP, strips the key and forwards the rest to `mdp.get_pricer(req)` (`:117-143`). pricebt's `pricer` config (dotted path plus MDP block) is the same pattern made declarative.
9. **Never rely on ARBS being importable on CI**: the harness should `pytest.skip` the live-ARBS tests when `C:\Users\chris\clee\ARBS` or the store is absent.

## 12. Open questions

- The CME/LCH-cleared EOD assets (`USD-SOFR-1D-CME`, `-STIR-LCH`, ...) have no source string in `IRSwapsMDP`; which producer wrote them, and are they the intended "official" curves? (10Y par on 2026-08-05: CME 4.2297%, STIR-LCH 4.0180%.)
- Whether the sanity filter should be replicated in the direct reader (needs `Caching.curve_sanity`, an ARBS import) or replaced by pricebt's own validators.
- STIR/UST futures MDPs were out of scope here (`ust_future_store` exists under the same cache root, not probed).
- **`CME_NY_EOD_LIVE-ql_basic/-rl_basic` were not run, although it is the `IRSwapsMDP` default source and the `config/settings.example.yaml` primary.** Reason, from reading `IRSwapsMDP.py:2013-2086`: both branches build through `CMEFetcherV2(**self.config).build_{ql,rl}_eod_curves`, whose caches are a diskcache under `%LOCALAPPDATA%\ARBS\Cache\diskcache\dump\CMEFetcher_curve_reports` and a 521 MB ZODB file in the repo (`Caching/dump/CMEFetcher_curve_reports.fs`, Sep 2025). With `CACHE_ROOT` redirected that cache is empty, so a call goes to live CME HTTP and hits the guard; running it offline would mean copying that diskcache into the scratch root. I did not, because the store already carries CME-cleared EOD curves (`USD-SOFR-1D-CME`, `USD-OIS-CME`, readable by the direct reader) and the other sources give better coverage. It also needs QuantLib-side fixings (`FixingsFetcher`). Untested, not proven impossible. SDR_INTRADAY: the only local cache (`SDR_INTRADAY-RL_CURVE_CACHE`) has 28 sparse keys 2024-03..2026-08, and a miss builds from SDR tape files, so it cannot serve a date range.

## 13. Script index (`scratch\mdp_probe\`)

`probe_common.py` (env, guard, timers), `arbs_offline.py` and `arbs_offline_frb.py` (patches), **`arbs_recipes.py`** (validated recipes), `direct_store.py` (ARBS-free reader), `export_fixtures.py` -> `fixture_export/` (5.9 MB), `coverage_scan.py`, `probe_01..26_*.py` (import, per-recipe pricers, fixings, ref data, FedInvest coverage/quality, pricing and roll-forward, minute grids, direct-vs-ARBS parity, pickle/threads/memory, miss paths, guard self-test, Excel fall-through, full recipe self-test `probe_22`), logs in `out/`. The FedInvest scratch copy lives in `copies/`.
