# rateslib 2.7.1 research notes (pricing library for pricebt tests)

Everything below was executed in the `stir` env (Python 3.13.5, rateslib 2.7.1, pandas 2.3.1). Probe scripts:
`C:\Users\chris\clee\gsquant-temp-claude\pricebt\scratch\rateslib_probe\p01..p25_*.py`; the reference attribution module is
`...\rateslib_probe\rl_decomp.py` (verified by `p15_decomp_verify.py`). Timings are medians on this machine, warm caches, float (AD0) curves.
ARBS was only read (Read/Grep); `git status --porcelain` is byte-identical before/after; no ARBS module was imported.

## 0. Top 12 things the architects must know

1. **Value = PV at the curve's initial node.** `inst.npv(curves=c)` discounts to `c.nodes.initial`; `c[d]` is 0.0 for `d < initial`, 1.0 at initial. Cashflows paid ON the valuation date are included (df=1), the day after they vanish. There is no "valuation date" argument: the *curve is the clock* (`curves.py:264-267`, `periods/protocols/npv.py:1002`).
2. **Seasoned floating legs need fixings via the GLOBAL store + string identifier.** Passing a pandas Series as `leg2_rate_fixings` is only used for periods that are already fully fixed at construction (`periods/parameters/rate.py:155-188`); an in-progress period silently drops the Series and then raises `ValueError: effective date ... before the initial node`. Working recipe: `rl.fixings.add("NAME_1B", series)` and `IRS(..., leg2_rate_fixings="NAME")` (identifier + `_1B`). p04 A/B.
3. **Three silent-misprice traps around fixings** (all reproduced): (a) missing the last 1-2 business days before as-of => NPV -10.3M instead of -72.7k, no error. Mechanism (`fixings.py:3287`): `unpopulated_index = rate_curve[unpopulated.index[0]] / rate_curve[end]`; when the first unpopulated date is before the curve's initial node, `curve[date]` is 0.0 (curves.py:267), so the unfixed part multiplies the known index by zero and the period rate collapses to `-100/sum(dcf)`. (b) fixings dated >= as-of are consumed => look-ahead, no warning; (c) an index with a time-of-day (08:00) => `fixing_rates.update` matches nothing => every date "unpopulated" => same zero-DF collapse (NPV -10.4M). Mid-series gaps DO raise. The Pricer must guard (recipe R5).
4. **Solver prints `SUCCESS: ...` to stdout unconditionally** (`dual/newton.py:47-52`, called with `log=True` at `solver.py:1734`). It will trash a tqdm bar. Wrap in `contextlib.redirect_stdout`.
5. **Solver leaves the curve at `ad=1` (Dual nodes)**; npv is 2.6x slower and returns `Dual`. Convert to a float curve by rebuilding (21 us, R2). Calling private `curve._set_ad_order(0)` on the solver-owned curve silently makes `solver.delta()` all zeros (p11) - never mutate it.
6. **Sign conventions:** IRS positive notional = PAY fixed / receive float (leg1 fixed cashflow is negative). FixedRateBond/Bill: NEGATIVE notional = LONG (npv of `notional=-10e6` is +10.18mm; positive notional = issuer/short). All pricing methods (`npv/rate/cashflows/analytic_delta/...`) are keyword-only (`sw.npv(curves=c)`; positional raises `TypeError`); constructors still accept `(effective, termination, frequency)` positionally.
7. **Unpriced instruments re-strike on every call:** `IRS(fixed_rate=None).npv()` returns 0.0 for ANY curve (`irs.py:584-593` re-sets the mid rate each call because `kwargs.leg1["fixed_rate"]` stays NoInput). Lock numeric strike/rate at trade inception.
8. **Cost profile:** curve build 13 us, unseasoned 5y IRS npv 82 us, bond npv 82 us, but a *seasoned* RFR period costs 1.7 ms/npv (pandas Series rebuilt per call) and `cashflows()` 2.7-8.7 ms. Build instruments once (0.28-2.7 ms each; deepcopy costs the same as building).
9. **No thread speed-up** (GIL held; 8 threads = 0.94x) but thread-safe for reads in tests; `rl.fixings` and `rl.defaults` are process-global singletons -> use multiprocessing, populate fixings per process.
10. **Curve kwargs are silently swallowed** (`Curve(nodes, c=[1,2])` works; `_WithMutability.__init__(**kwargs)`, curves.py:1484). Config-driven construction must validate keys itself.
11. **Import side effects:** `LicenceNotice` UserWarning + INFO log on `import rateslib` (no licence file => non-commercial only), 1.1-1.45 s import, matplotlib imported (backend `tkagg`; set `MPLBACKEND=Agg` headless), creates empty `%APPDATA%\rateslib`.
12. **Exact P&L attribution is achievable** with scenario curves `c0.translate(t1)` (carry), `c0.roll(N).translate(t1)` (roll), then AD in zero-rate space (delta/convexity); sums to the total to 1e-10 (section 8).

## 1. Import / licence / environment

```python
import os, warnings
os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore", message=r"(?s).*Rateslib is source-available.*")   # BEFORE import; category class lives inside rateslib
import rateslib as rl
```
`verify.py:105-120`: with no `RATESLIB_LICENCE` env var and no `%APPDATA%\rateslib\rateslib_licence.txt`, status NO_LICENCE => `warnings.warn(LicenceNotice, stacklevel=4)` and `logging.getLogger("rateslib.verify").info(...)` (visible if root logger is INFO). Text: use is "permitted for non-commercial purposes only (at-home or university based academic use)" - flag to the user (open question). Module-level singletons: `rl.defaults` (mutable dict-like, `rl.default_context("convention","act365f")` context manager), `rl.fixings` (global store), `rl.calendars`. Calendars available: `nyc`, `fed`, `tgt`, `ldn`, unions `"nyc,fed"`/`"nyc|fed"`; no `sifma`. NYC treats 2025-01-09 (Carter day) as a business day.

## 2. Curves (2.x architecture)

`rateslib/curves/curves.py`: `_BaseCurve(ABC)` with `_meta` (`_CurveMeta`: calendar, convention, modifier, collateral, index_*), `_nodes` (`_CurveNodes`: `.initial .final .n .keys .nodes`), `_interpolator`, `_ad`, `__getitem__`. Concrete: `Curve` (DF nodes), `LineCurve` (rate nodes), `CompositeCurve`, `MultiCsaCurve`, `ProxyCurve`, `CreditImpliedCurve`, wrappers `ShiftedCurve/TranslatedCurve/RolledCurve` (returned by `.shift/.translate/.roll`), academic `NelsonSiegelCurve/NelsonSiegelSvenssonCurve/SmithWilsonCurve` (`rl.NelsonSiegelCurve(dates=(d0,d1), parameters=(b0,b1,b2,lam), convention="act360", calendar="nyc")`, build 10 us; default convention is ActActISDA, must set act360 for SOFR).

```python
c = rl.Curve(nodes={dt(2025,1,2):1.0, dt(2026,1,2):0.96, ...}, id="sofr", convention="act360", calendar="nyc",
             modifier="MF", interpolation="log_linear")
```
- Interpolations (`interpolation.py:131-141`): `log_linear` (default), `linear_zero_rate`, `linear`, `linear_index`, `spline` (log-cubic, 29 us build), `flat_forward/flat_backward` (not for DF curves; give df=0.96/0.90 nonsense), callable `f(date, curve)`. Verified 3m fwd rates differ by interpolation (3.196 log_linear, 3.412 linear_zero_rate).
- `c[d]` DF; `d<initial` -> 0.0; beyond last node extrapolates last log-linear segment. Interpolation runs on **posix seconds**, so datetimes with time-of-day interpolate sub-day (p01, p05).
- `c.rate(effective, termination_or_tenor)` returns **percent**, `None` if effective < initial; raises `ZeroDivisionError` when equal. `c.rate(d,"1b")`, `c.rate(d, dt)`.
- `c.shift(bp)` -> `ShiftedCurve`: adds bp as a geometric daily-compounded rate, so a +10 shift moves a 1y simple Act360 rate by 10.33bp. 24 us to create.
- `c.translate(t1)` -> `TranslatedCurve`: DFs re-based (`DF(d)/DF(t1)`), forward rates preserved (verified exact). 4 us.
- `c.roll(N|"6m")` -> `RolledCurve`: rate space moved right N *calendar days*; gap [initial, initial+N) filled with the first overnight rate (curves.py:1381-1406). `c.roll(N).translate(t1)` == `Curve(nodes={t1:1.0, d_j+N: DF0_j})` to 1e-16 (log-linear) and the node version is **10x faster** (80 us vs 830 us per 5y swap npv; wrapper DFs are 16-50 us, uncached). `translate().roll()` commutes.
- `c.copy()` = pickle round trip (48 us); `to_json/rl.from_json` 218 us. `update_node(date, df)` mutates + clears cache. DF cache: per-curve OrderedDict, `defaults.curve_caching_max=1000`.
- Intraday initial node (`dt(2025,1,2,10,30)`) works but shifts all PVs by sub-day discounting and DROPS a payment dated the same day at 00:00 (df=0). Use date-only initial nodes for daily semantics; treat intraday time-of-day theta as an explicit, optional feature.
- Deprecations: only `DeprecationWarning` for numeric `fx` args (`periods/utils.py:145`) and IMM string entries (`scheduling/imm.py:87`). 1.x -> 2.x differences hit: `leg2_fixings` -> `leg2_rate_fixings`; keyword-only pricing methods; Series fixings semantics above.

## 3. Solver and curve fitting

Pattern used by ARBS (`rl_usd_sofr_intraday_builder.py:159-222`), reproduced in `_curves.py`:
```python
def build_sofr_curve(ref, par, cid="sofr", calendar="nyc", spot_lag=2):
    cal = rl.get_calendar(calendar); spot = cal.add_bus_days(ref, spot_lag, True)
    insts = {t: rl.IRS(effective=spot, termination=t.lower(), spec="usd_irs", curves=cid, fixed_rate=r) for t, r in par.items()}
    mats = {t: i.leg1.schedule.termination for t, i in insts.items()}; order = sorted(insts, key=mats.get)
    crv = rl.Curve(nodes={ref: 1.0, **{mats[t]: 1.0 for t in order}}, id=cid, convention="act360",
                   calendar=calendar, modifier="MF", interpolation="log_linear")
    with contextlib.redirect_stdout(io.StringIO()):                     # R1: solver prints
        sv = rl.Solver(curves=[crv], instruments=[insts[t] for t in order], s=[par[t] for t in order], id=cid,
                       func_tol=1e-12, conv_tol=1e-12)
    assert sv.result["status"] == "SUCCESS"
    return crv, sv
def to_float_curve(c, cid=None):                                          # R2 (21 us)
    return rl.Curve(nodes={k: float(v) for k, v in c.nodes.nodes.items()}, id=cid or c.id, convention=c.meta.convention,
                    calendar=c.meta.calendar, modifier=c.meta.modifier, interpolation=c.interpolator.local_name)
```
12 par swaps (1M..30Y): 7 iterations, **43 ms** solver-only (59 ms with instrument+curve construction), par repricing error 2e-7 bp. `solver.result = {status,state,g,iterations,time}`. Bond curve fit (7 UST, `metric="clean_price"` or `"ytm"`, curve `convention="act365f", modifier="none"`, instruments built with `curves="ust"`): 16 ms, 4-7 iterations, repricing error 0.0bp (p07). `Solver.delta(solver=...)` needs instruments constructed with `curves="<id>"`.

## 4. IRS

`rl.IRS(effective, termination, frequency, *, spec="usd_irs", fixed_rate, notional, curves, leg2_rate_fixings, leg2_fixing_method, payment_lag, ...)`. `spec="usd_irs"` (data/__instrument_spec.csv:11) = annual, act360, nyc, MF, payment_lag 2, `rfr_payment_delay`, 12M/`shortfront`; `usd_irs_lt_2y` = eom TRUE. Explicit args reproduce spec NPV to 1e-11. Curve forms: `curves=c | [c] | [rate, disc] | {"disc_curve":..,"leg2_rate_curve":..}`; pass `curves=` at call time (keeps the instrument curve-free).

Methods (5y fwd-start, 10mm, fixed 4.00, par 3.948): `npv(curves=c)` -22,596 (float via `float()`); `rate(curves=c)` 3.9480 (percent); `spread()` 5.20bp; `analytic_delta()` 4,342.71/bp (FD-verified to 1e-9); `cashflows()` DataFrame cols `Type,Ccy,Payment,Notional,Period,Convention,DCF,Acc Start,Acc End,DF,Cashflow,NPV,...,Rate,Spread`; `cashflows_table()` pivot; `delta(solver=)`.

**Valuation-date semantics** (p05): npv = PV at `curve.nodes.initial`. `forward=t1` returns `npv/DF(t1)` exactly (diff 1e-12); `settlement=s` zeroes periods whose `ex_dividend` (default = payment date) < s (npv changes -33.7k -> -38.6k when s is after the first payment) - it is an ex-div screen, not a valuation date. Payment on the valuation date is included.

**Forward-starting**: just future `effective`, no fixings needed (but an *identifier* must still exist in the store if `leg2_rate_fixings="X"` is given, else `ValueError`, p04 C).

**Seasoned swap recipe (verified against a hand compounding calc, diff 0.0):**
```python
key = "PB_SOFR_1B"                                       # identifier + "_1B" suffix is mandatory
def install(asof, full: pd.Series):                       # R3: strictly < asof, date-only index
    if key in rl.fixings.loader.loaded: rl.fixings.pop(key)
    rl.fixings.add(key, full[full.index < asof])
sw = rl.IRS(effective=dt(2024,7,8), termination="2y", spec="usd_irs", fixed_rate=4.5, notional=10e6, leg2_rate_fixings="PB_SOFR")
```
Semi-efficient calc (fixings.py:3263-3289): `rate = (Π(1+d_i r_i) * DF(first_unfixed)/DF(end) - 1)/Σd`. Known fixings must be contiguous from period start through the last business day < as-of. `rl.fixings.add` duplicate check compares un-uppercased names (add "X_1B" twice raises; "x_1b" twice silently overwrites). A fully-determined `RFRFixing` caches its value forever (`_BaseFixing.value`): if time can go backwards on the same instrument call `inst.reset_fixings()` (108 us) - or build a fresh instrument. Reuse-vs-fresh across four as-of dates: max diff 0.0. Per-period scalar fixings (`leg2_rate_fixings=[4.31]`) work for periods that have ended (no store needed).

**Global-state-free alternative (verified diff 0.0):** subclass `DefaultFixingsLoader`, override `__getitem__(name)` to return `(hash((name, asof)), full[full.index < asof], bounds)` with `asof` from a `ContextVar`, and set `rl.fixings.loader = AsOfLoader(...)` (p18).

**SOFR conventions:** the swap's `FloatRateSeries` (calendar `nyc`, Act360) is checked against the curve: an Act365F curve on a SOFR swap raises `ValueError ... 'convention' Got: 'Act365F' and 'Act360'`; curve `calendar` does not change act360 SOFR NPV but matters for tenor arithmetic. Fixing methods: `rfr_payment_delay` (default, 41 us), `rfr_payment_delay_avg`, `rfr_observation_shift(n)`, `rfr_lookback(n)`, `rfr_lockout(n)`. **Lookback/shift need curve DFs before the swap start**: `rfr_lookback(5)` on a spot-start swap gives **NaN** with RuntimeWarnings; `rfr_observation_shift(5)` raises; `(2)` works (start-2b == initial). Non-efficient methods cost ~2 ms/npv. Assert `math.isfinite`.

**Guard (R5, verified: catches last-2-missing, mid-gap, time-of-day; passes clean data):**
```python
def fixings_guard(series, first_obs, asof, cal):
    if (series.index != series.index.normalize()).any(): raise ValueError("fixings index must be midnight dates")
    last = cal.lag_bus_days(asof, -1, False)             # prev business day; works for non-bus asof (add_bus_days does not)
    if last >= first_obs:
        missing = [d for d in cal.bus_date_range(first_obs, last) if d not in series.index]   # bus_date_range needs both ends = business days
        if missing: raise ValueError(f"{len(missing)} fixings missing, first {missing[0]}")
```

## 5. Bonds, bills, futures

`rl.FixedRateBond(effective, termination, spec="us_gb", fixed_rate, notional=-10e6, calc_mode=..., ex_div=..., settle=...)`. `spec="us_gb"`: semi-annual, ActActICMA, nyc, `modifier none`, payment_lag 0 (payment date rolls to next business day: Nov-15-2025 -> Nov-17), settle=1, ex_div="-1b", calc_mode `us_gb`. Calc-mode aliases (`conventions/__init__.py:734-757`): `us_gb`=`ust`, `us_gb_tsy`=`ust_31bii` (differs: clean 97.2447 vs 97.2397 at 4.6%), also `us_corp`, `us_muni`. Bills: `rl.Bill(effective, termination, spec="us_gbb")`, metrics `price|discount_rate|simple_rate|ytm` (99.264 / 4.272 / 4.304 / 4.328), 17 us.

Verified (10y UST 4.25% Nov-34, settle 2025-01-03): `accrued` 0.57528; `price(ytm=4.60, settlement, dirty=False)` 97.24469, dirty 97.81997 (diff == accrued to 1e-15); `ytm(price, settlement, dirty=False)` round-trips 4.59999999999997; `duration(ytm, settle, "risk"|"modified"|"duration")` 7.7302 / 7.9025 / 8.0843 (per 1.00% yield; FD-checked); `convexity(ytm, settle, "risk"|"convexity")` 0.73106 / 0.74735; these return floats only (not AD safe, ytm.py TODO). Curve-implied: `rate(curves=c, metric="clean_price"|"dirty_price"|"ytm")` = 101.2456 / 101.8209 / 4.0947; `settlement` defaults to `calendar.lag_bus_days(curve.initial, settle, True)`. **`npv(curves=c)` = dirty(settle) * N/100 * DF(settle)** (diff 0.0) - a full-value PV (includes accrued), for long `notional<0` positive.
Ex-div: `npv()` screens coupons with `settlement > ex_dividend` (payment-1b for us_gb): on 2025-05-14 with a coupon on 05-15 default npv drops by the whole coupon a day early (10,124,280 vs 10,336,755). **Recipe for smooth marks:** build the bond with `ex_div=0` and call `npv(curves=c, settlement=t)`; then V includes every flow with pay >= t and cash is booked when paid (p09 table; V(05-15)=10,337,929 incl. coupon, V(05-16)=10,126,548 after). Repo: `fwd_from_repo(price, settlement, forward_settlement, repo_rate, convention="act360", dirty=False, method="proceeds"|"compounded")` (18 us) and exact inverse `repo_from_fwd` (round trip 4.33 -> 4.32999999999983); constant-yield carry over 1M: clean +0.0178, accrued +0.3640 vs financing 4.33%*97.82*31/360=0.365.
`BondFuture(delivery=(d0,d1), coupon=6.0, basket=[FixedRateBond...], nominal=100000, contracts=100, calc_mode="ust_long"|"ust_short", curves=c)`: `cfs`, `rate(curves)` 111.19, `npv` 11.12mm (full notional value - for futures P&L use variation margin (price-entry)*multiplier, not npv), `dlv(...)`, `ctd_index`, `gross_basis`, `net_basis`, `implied_repo`; 227 us. `STIRFuture(spec="usd_stir", price=95.5)`: metric `rate|price`, npv is vs traded price.

## 6. (removed: options are out of scope, Q15)

## 7. AD, floats, timing, concurrency

- `float(x)`, `x.real`, `rateslib.dual.utils._dual_float(x)` all strip AD (all three work on Dual/Dual2/float). `np.float64(Dual)` works; `np.asarray([Dual])` -> object dtype; pandas keeps object dtype. Serialise with `float()`.
- Orders: `Curve(ad=0|1|2)`; 5y swap npv 91 us / 237 us / 284 us. `rl.gradient(x, vars, order=1)` gradient, `order=2` full **Hessian** (`x.dual2` raw = Hessian/2; verified on x*x*y + y*y). Var names `f"{curve.id}{i}"`, i over ALL nodes incl. node 0.
- Timing table (us): Curve 13; NS 10; `c[d]` 0.2 cached / 2.6 cold; `c.rate` 7.5; IRS build 5y 615 / 30y 2,698; FRB build 281; IRS npv 5y 82 (AD1 216); 30y 463; 100-swap loop 16,500 (Portfolio same); `rate()` 95; `analytic_delta` 21; seasoned 2y npv 1,698 (36x unseasoned); bond `price` 38, `ytm` 315, `accrued` 5; Solver12 43,000; bond fit 16,000; deepcopy IRS10y 1,537 / bond 845; pickle IRS 1,458 (16.6KB), bond (9.6KB), curve 53 (1.5KB).
- Multiprocessing: curves and instruments pickle (IRS loses nothing; seasoned IRS holds only the identifier). Each worker must `rl.fixings.add` its own store. Threads: 0.94x speed-up, results identical (also with eviction churn).

## 8. Exact P&L attribution between two valuation dates (numerically verified)

Define per scenario k: **X_k = PV at t1 of flows paid >= t1 (scenario DFs) + face cash paid in [t0,t1)**; `V0 = npv(c0)`; `V1 = npv(c1)`; total = `V1 + cash_actual - V0` (cash = sum of `Cashflow` for `t0 <= Payment < t1` from `cashflows()` or, 4x faster, `period.cashflow(rate_curve=c, disc_curve=c)` over `leg.periods` with `p.settlement_params.payment`; identical numbers). Chain (`rl_decomp.decompose`):

| layer | definition | rateslib calls |
|---|---|---|
| carry | X(c0.translate(t1)) - V0 : pure passage of time, forwards realised in date space (~ financing of the MTM) | flows from `cashflows(curves=c0)` at t0-world; `X = sum(CF*c0[pay])/c0[t1] + cash` |
| roll | X(c0.roll(N)) - X(carry) : curve static in tenor space (N=(t1-t0).days) | `c0.roll(N)` tables at t0-world |
| fixings | roll curve valued with ACTUAL elapsed fixings (t1 world) - roll scenario | `install(t1); inst.reset_fixings(); npv(c0.roll(N).translate(t1))` |
| resample | roll curve re-expressed on c1's node dates (log-linear) - exact roll curve | `Curve(nodes={d: c_roll1[d] for d in c1 nodes})` |
| delta | sum_j (dV/dz_j) dz_j in **zero-rate space** z_j=-ln DF_j/tau_j at c1's nodes | `Curve(..., ad=2)`, `npv`, `rl.gradient(v,vars,1)`, chain rule `dDF/dz=-tau*DF` |
| convexity | 1/2 dz' Gamma dz, `Gamma = H*outer(J,J)+diag(g*tau^2*DF)` from the Dual2 Hessian | `rl.gradient(v,vars,2)` |
| residual | V1 - V(c_base) - delta - convexity | ~1 cent |

**Canonical 4-bucket fold for the config default** (the seven raw terms always sum to the total): `carry` = carry; `roll` = roll + fixings; `delta` = delta + resample; `convexity` = convexity + residual. `fixings` folds into roll because it is the front-end realised-vs-assumed difference under the static-curve hypothesis (the roll scenario holds today's overnight rate flat over [t0,t1)); `resample` is only a re-parametrisation of the delta base curve, so it belongs with delta; `residual` is third-order and higher. Keep the raw terms available under sub-layer names for diagnostics.

Key facts: (i) swap value is ~linear in DFs (DF-space convexity 0.0026) so delta/convexity MUST be measured in zero/par-rate space; (ii) drop AD var of node 0 (initial node, tau=0); (iii) `sum(layers) == total` holds by construction (telescoping), so validation uses independent identities: `X_fwd` from the cashflow table == `npv(c0, forward=t1) - sum_{paid in window} CF*(DF0(pay)/DF0(t1)-1)` (diff 5e-10), reuse-vs-fresh instrument V1 diff 0.0, FD full reval at shifted zeros (51,172.52) vs delta+convexity (51,172.51), and a mutation test (+1000 cash corruption -> identity diff 2000). Results (USD):
- Seasoned 3y payer 50mm, coupon paid in window (t0 2025-01-02, t1 01-16): V0 -369,827.43, V1 -212,114.86, cash -86,490.36, **total 71,222.20** = carry -561.82 + roll 12,159.98 + fixings -10,627.91 + resample 111.52 + delta 70,190.64 + convexity -50.23 + residual 0.02 (DV01 9,746/bp).
- UST 4.25 Nov-34 long 10mm, coupon in window (05-12 -> 05-20): total -59,770.13 = carry 9,778.86 + roll -385.97 + resample -0.93 + delta -69,431.35 + convexity 269.99 + residual -0.72.
- Trader "carry+roll" (unchanged curve) = X_roll - V0 (seasoned swap: 11,753 vs curve-move 51,172 in the first probe). Accrual-only carry can be split out analytically (N*dcf*(ON-fixed)) with roll as the plug.
- **Bond in yield space with repo** (p19): `mark(y,t)=|N|/100*price(y, settlement=t, dirty=True)` (ex_div=0 bond); financing = -V0*repo*dcf; carry = cash + mark(y0,t1) - V0; delta = -N/100*duration("risk")*dy; convexity = 1/2*N/100*convexity("risk")*dy^2. Example: total -52,389.01 = financing -9,535.93 + carry 9,695.94 + delta -52,717.33 + convexity 168.70 + residual -0.39.
- Cost per instrument per bar: seasoned IRS ~46-58 ms, bond ~16-21 ms (3 `cashflows()` + 5-6 npv + AD). Use the period API, shifted-node roll curve, compute layers lazily/EOD, and cache the t0-world tables from the previous bar. Fixings term is zero for bonds and unseasoned swaps.

## 9. Design implications for pricebt

- **Pricer for rateslib = a bundle**: `{curves by role (disc/rate/proj), valuation_date (=curve.initial), fixings-as-of handle, optional vol, optional per-bond yields}`. `valuation_date` must be an explicit pricer attribute, checked equal to `curve.nodes.initial`.
- Pricable factory builds rateslib instruments once from config (`kwargs` validated against `inspect.signature`), with numeric lock-in of `fixed_rate`/`strike` at trade time, `curves=` never stored on the instrument.
- Keep ALL rateslib globals inside one adapter module: warnings filter, solver stdout redirect, fixings install/guard/reset, float-curve normalisation, `math.isfinite` assertions, futures margin instead of npv.
- Value function returns floats + named layers dict; heavy layers (`carry/roll/delta/convexity`) opt-in by name; sums checked to tolerance in tests.
- Financing/cash belongs to the pricable's value (coupon cash on pay date, repo funding) or to a ledger; avoid double counting with `npv` that already contains accrued.
- For minutely runs: curve build (Solver 43 ms) dominates; NS/parametric or per-minute float curves are 10 us; `curve[date]` caches are cold on each new curve; keep instruments alive across minutes; fixings are constant within a date (install once per date).
- Do not rely on `Curve` kwargs validation, Solver stdout, positional args, or `rl.fixings.add` duplicate errors.

## 10. Open questions

1. Licence: rateslib is non-commercial unless a licence is registered - confirm the user's intended use.
2. Desk "carry" (accrual minus funding, spot ON rate held) vs the forwards-realised carry used here: which label should the config expose by default? Both are derivable (section 8).
3. Cash reinvestment: attribution books cash at face on its payment date; accreting it at the OIS forward would change layers slightly.
4. Intraday: date-only curve initial node (recommended) vs time-of-day node (theta inside the day).
5. ARBS builders pick `ref = previous business day` for curve initial; confirm which date the MDP treats as valuation date for intraday snapshots.
6. SOFR fixing source for backtests (ARBS store vs synthetic); rateslib ships no usable data: `data/historical/sofr.csv` is 1,333 rows of the placeholder -500 and `usd_rfr.csv` is 9 rows from Jul-Aug 2019 (p26).
