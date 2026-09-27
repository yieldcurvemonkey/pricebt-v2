# 11 - QuantLib convention translation (Phase 1 evidence) and the shared convention vocabulary

Status: Phase 1 complete 2026-09-26; the adapter (Phase 2, built and tested against this document) is `src/pricebt/contrib/quantlib/`; sections 4, 5, 9, 10 and 11 were corrected by
what building it found (each correction is marked). Everything below was measured, not assumed: every number
names the script in `tools/ql_evidence/` that produced it (run from the project root with `PYTHONPATH=src`, `C:\Users\chris\anaconda3\envs\stir\python.exe`).
Library versions: QuantLib 1.41, rateslib 2.7.1. Curves: real rows of `data/fixtures/curves/asset=USD-SOFR-1D-CITIVELOEXCEL` (one row = node dates +
discount factors, log-linear in DF, anchor = node_dates[0]); fixings: `data/fixtures/fixings/USD-SOFR-1D.parquet`; bonds: `ust/reference_fiscaldata.parquet`.

Every comparison uses **the same node table, the same holiday set, and the same fixings** on both sides. Where a difference survives it is listed in
section 9 with its size and cause.

## 0. Headline

| Quantity | Sample | Worst disagreement QuantLib vs rateslib | Script |
|---|---|---|---|
| calendar: business-day set, spot date | 18 years of days; 2106 reference dates | 0 days; 0 of 2106 | `p1_dates.py` |
| swap schedule: termination, accrual dates, payment dates (both legs), fixed-leg accrual fractions | 2106 dates x 12 tenors = 25,272 swaps | 0 differences | `p1_sched.py` |
| at-inception swap: NPV, par rate, fixed leg PV, float leg PV, PV01 | 320 swaps (40 dates x 8 tenors, 1Y-30Y) | rel 6.4e-13 (NPV); par 1.2e-14 percent; legs rel 2.6e-15 / 6.3e-16; PV01 rel 4.3e-16 | `p1_npv.py` |
| started swap (published fixings, current coupon, all flows) | 344 swaps, payer and receiver | NPV abs 5.2e-8 on 50mm (rel 3.0e-13); every flow: fixed 3.0e-9, float 2.3e-8; dates equal | `p1_started.py` |
| payment ON the reference date | swap whose flow is paid on D | 0.0 once `includeSettlementDateFlows=True` is passed to the engine AND `Settings.includeReferenceDateEvents` is on (127,914 off otherwise; see section 4 for the expired-swap case) | `p1_paydate.py`, `tests/test_quantlib_swap.py` |
| golden seasoned 3y payer 50mm (V0, V1, cash, carry, roll, delta, convexity, residual) | the A2 golden case | V0/V1/cash to 0.01; carry -561.82, roll 1532.07, delta 70302.17 (gold .16), convexity -50.23, residual 0.02 | `p1_layers.py` |
| swap DV01 fresh (analytic fixed-leg PV01) | 72 swaps (48 at par, 24 off par) | rel 3.9e-16 | `p1_dv01.py` |
| swap DV01 aged (market dv01 = par-curve ladder sum) | 45 aged swaps | QL ladder sum vs rateslib ladder sum rel 3.8e-8 | `p1_dv01.py` |
| delta ladder, 11 pillars and 4 pillars | 155 positions (fresh, off-pillar, aged, payer/receiver) over 20 dates | max bucket diff / max bucket 2.3e-6 (11) and 2.0e-6 (4); sum of ladder 4.3e-6 | `p1_ladder.py` |
| parallel par-bump gamma (second difference, all pillars +-1bp) | 10Y and 5Y payers, 100mm, real curve | 1.3e-7 relative once rateslib's solver is run to 1e-20; 22% with the shipped ladder tolerance (solver noise, 9.7) | `tests/test_quantlib_tieout.py` |
| UST cash flows: payment dates and amounts | 1230 bonds (all with issue >= 2000) | 0 date differences; amounts 1.1e-14 per 100 | `p1_bond_sched_all.py` |
| UST dirty price from yield (treasury convention) | 250 bonds, 11,552 price points, coupon dates included | 6.3e-13 per 100 | `p1_bond2.py` |
| UST yield from clean price | same | QuantLib round trip 1.0e-11 percent; rateslib's own round trip 1.7e-7 percent (rateslib solver tolerance) | `p1_bond2.py` |
| UST risk (dv01 per 100) and convexity | same | FD of QuantLib price vs FD of rateslib price 1.6e-9 / 1.7e-9; 4th-order FD of the QuantLib price vs rateslib ANALYTIC risk 6.5e-12, convexity 4.3e-8 | `p1_bond2.py`, `p1_misc.py` |

So with the constructions below **the two libraries agree to machine precision on every pricing quantity**; what remains are data-horizon effects (section 9.1),
two rateslib 2.7.1 quirks (9.3, 9.4) and one convention QuantLib cannot express (9.5).

## 1. Method and controls

Each script first runs on an input whose answer is known (the standing rule): a 1Y payer at its own par has NPV 0 (`p1_npv.py`); an 18M schedule with a short
front stub is exactly `[2024-07-03, 2025-01-03, 2026-01-05]` (`p1_sched.py`); a 5Y pillar par swap has all of its risk in the 5Y bucket (`p1_ladder.py`);
a bond at yield = coupon on a coupon date prices at 100 in both libraries (`p1_bond.py`); the scratch rateslib ladder equals the shipped adapter's
`build_par_swap_ladder` to 0.0 (`p1_ladder_control.py`); bespoke calendars do not leak into named calendars (`p1_dates.py`). Two of the first measurements were
*wrong for a reason the controls could not catch* and are recorded in section 9 so nobody repeats them (calendar horizon; `endOfMonth` default).

## 2. Item 1: dates, calendar, schedule (spot, tenor addition, termination)

### 2.1 The calendar (item 7)

Build a QuantLib calendar from the snapshot's `CalendarData`, never from a named QuantLib calendar:

```python
cal = ql.BespokeCalendar(name)                    # owns its implementation
for wd in weekend_days_from(weekmask): cal.addWeekend(wd)   # weekmask "Mon Tue Wed Thu Fri" -> Saturday, Sunday
for h in holidays: cal.addHoliday(ql.Date(h.day, h.month, h.year))
```

* `addHoliday` on a NAMED calendar (`ql.WeekendsOnly()`, `ql.UnitedStates(...)`) mutates a process-global singleton implementation: measured, a holiday added to one
  `WeekendsOnly()` is visible in every later one (`p1_dates.py`). `BespokeCalendar` is independent even between two instances with the same name (measured), so
  nothing leaks. QuantLib calendar `==` compares NAMES, so never use `==` to test that two bespoke calendars hold the same holidays.
* Weekends: from the `weekmask` string of `CalendarData` (days NOT in the mask become `addWeekend`). Holidays that fall on a weekend are harmless.
* Business-day agreement, `BespokeCalendar` built from `nyc.json` vs rateslib's built-in `nyc`, every day 2018-01-01 .. 2035-12-30: **0 disagreements**.
* Do not fall back to `ql.UnitedStates(SOFR)`: it disagrees with `nyc` on 2027-06-18 and 2032-06-18 (measured by comparing the two holiday sets over 2018-2035), inside 10Y swaps traded 2018-2022.
* **Horizon**: `nyc.json` ends 2035-12-25 and rateslib's built-in `nyc` runs to 2200, so a schedule date after 2035 sees no holiday in the snapshot. See 9.1.
  All comparisons in this document give rateslib the same holiday set: `rl.Cal(holidays=[...json...], week_mask=[5, 6])`.

### 2.2 Spot date, tenor addition, termination

| Step | rateslib | QuantLib construction that matches | Measured |
|---|---|---|---|
| spot date | `calendar.add_bus_days(ref, 2, True)` (ref must be a business day) | `cal.advance(ref, 2, ql.Days)` | 0 / 2106 differences |
| termination of `'10Y'` | `Schedule(...).termination` = `add_tenor(effective, tenor, 'MF', cal)`: the effective date plus the tenor with the effective day-of-month as the roll (clamped to the month), then Modified-Following adjusted | the same arithmetic in adapter code: unadjusted end = effective + n months, roll = effective day clamped to the month; `cal.adjust(end, ql.ModifiedFollowing)` | 0 / 25,272 |
| unadjusted schedule | `uschedule`: roll dates, short stub at the FRONT (`stub: shortfront`), `eom: False` | generate the dates yourself (see below) and pass `ql.Schedule(list_of_adjusted_dates, cal, ql.ModifiedFollowing)` | 0 / 25,272 |
| accrual dates | `aschedule` = `uschedule` adjusted MF | `cal.adjust(d, ql.ModifiedFollowing)` on each date | 0 / 25,272 |
| payment dates | `pschedule` = `aschedule` + `payment_lag` (2) business days | `ql.OvernightIndexedSwap(..., paymentLag=2, paymentAdjustment=ql.ModifiedFollowing, paymentCalendar=cal)` | 0 / 25,272 (fixed AND float leg) |
| fixed-leg accrual fraction | ACT/360 of the accrual dates | `ql.Actual360()` | max 1e-15 |

**Do not use `ql.Schedule(effective, termination, tenor, cal, conv, conv, ql.DateGeneration.Backward, False)` for the swap.** Backward generation subtracts whole
periods from the TERMINATION date, so a roll day that was clamped in the termination month (29/30/31 in February, or 18M/30M tenors landing in February) is
lost for the earlier periods, whereas rateslib keeps the roll day: 7 of 5064 schedules differed (a one-day difference in one accrual period, i.e. up to one day of interest). The adapter therefore generates the unadjusted dates itself:

```
roll = effective.day
end = add_months(effective, n_months(tenor), roll)              # clamped to the month
dates = [end]; k = 1
while add_months(end, -12*k, roll) > effective: dates.append(add_months(end, -12*k, roll)); k += 1
dates.append(effective); dates.reverse()                         # short front stub falls out
```

**Explicit maturity dates.** rateslib given a DATE termination treats it as follows (measured, `p1_explicit.py`): for whole-year tenors the round trip
tenor -> adjusted termination date -> IRS gives the identical schedule (all 3510 tested); when the date is not consistent with the effective day the roll comes from
the termination date and the odd period becomes a short front stub (eff 2025-01-08, termination 2030-03-15 gives roll 15). For 18M/30M tenors the round trip
DIFFERS in 425 of 3510 cases (roll 12 -> 15). The QuantLib adapter reproduces this rule (10 explicit-date cases against rateslib in `tests/test_quantlib_tieout.py`, including 2023-08-29 -> 2025-02-28 whose roll stays 29): candidate unadjusted end = the effective day placed in the
maturity's month; if the candidate IS the maturity (any month distance: a front stub falls out), or adjusts to it over a whole number of periods, the roll is the effective day; otherwise the maturity is the unadjusted end with its own roll. Consequence for tie-out: **the resolved term `maturity` (an adjusted date) does not determine the schedule for stub tenors**
(section 9.2).

## 3. Item 2: an at-inception swap (`p1_npv.py`, 320 swaps)

Construction: `ql.OvernightIndexedSwap(type, notional, schedule, fixed_rate, ql.Actual360(), index, 0.0, 2, ql.ModifiedFollowing, cal, False)` with
`index = ql.OvernightIndex(name, 0, ql.USDCurrency(), cal, ql.Actual360(), curve_handle)` and `ql.DiscountingSwapEngine(handle, True)`.

| Quantity | max abs | max rel | note |
|---|---|---|---|
| NPV at fixed rate 4.0% (10mm payer) | 1.1e-8 | 6.4e-13 | |
| par rate (percent) | 1.2e-14 | 1.0e-14 | `swap.fairRate()` vs `IRS.rate()` |
| fixed leg PV | 1.1e-8 | 2.6e-15 | sign: payer fixed leg negative in both |
| floating leg PV | 2.8e-9 | 6.3e-16 | |
| NPV at the (agreed) par rate | 1.9e-8 | - | zero to numerical noise |
| PV01 of the fixed leg | 1.1e-11 | 4.3e-16 | rateslib `analytic_delta(leg=1)` vs QuantLib `-swap.fixedLegBPS()` |

Settings that closed the gap (each measured):

1. **`ql.Sofr(handle)` is NOT usable**: it hard-wires `UnitedStates(SOFR)` as its fixing calendar. Use the generic `ql.OvernightIndex` with the bespoke calendar; the
   calendar sets the daily value dates of the compounding.
2. **Pay lag and adjustment**: `paymentLag=2` with `paymentCalendar=cal`; the last argument `telescopicValueDates=False` (measured: `True` gives the identical NPV on a started swap, 349,654.9157 both ways, and is not needed; False is the safe choice and costs nothing measurable).
3. **Rate averaging**: `ql.RateAveraging.Compound` (the default) = rateslib `rfr_payment_delay` with ACT/360 daily compounding. On a curve both telescope to
   `DF(start)/DF(end) - 1`.
4. **Curve**: `ql.DiscountCurve(dates, dfs, ql.Actual360())` + `enableExtrapolation()`; log-linear DF as verified before (1.1e-16). The day counter only maps dates to
   times; log-linear in time is invariant to it.

## 4. Item 3: a STARTED swap with published fixings (`p1_started.py`, `p1_paydate.py`, `p1_layers.py`)

Fixings. QuantLib: `index.addFixings([ql dates], [DECIMAL rates], True)`; the history lives in the process-global `IndexManager` keyed by the index NAME
(`familyName + "ON Actual/360"`; two `OvernightIndex` objects with the same family name share one history, measured). rateslib: `rl.fixings.add("PBT_SOFR_1B", Series in
PERCENT)`, also process-global. Both need only fixings dated strictly before the reference date (a fixing dated D is published on the next business day, the
snapshot rule). The fixings of D itself must NOT be present: QuantLib would use them (it forecasts only when none is stored).

| Quantity | worst (344 started swaps, 30 dates, payer and receiver, 20 days to 3y old) |
|---|---|
| NPV | abs 5.2e-8 on notional 5e7 (rel 3.0e-13) |
| par rate of the remaining swap | 9.8e-15 percent |
| every fixed-leg flow (amount) | 3.0e-9 |
| every floating-leg flow (amount; includes the current coupon accrued from fixings + projected) | 2.3e-8 |
| payment-date tables (both legs) | equal in all 344 |

The current coupon's floating amount is `notional * (prod_fixings(1 + r d/360) * DF(ref)/DF(end) - 1)`; both libraries produce the same number, so the "accrued
floating amount" needs no adapter arithmetic: `overnight_leg[i].amount()` is it.

**Payment ON the reference date** (rateslib: a flow paid on D is inside V(D) and is swept as cash at D+1). Result of `p1_paydate.py` (3y swap, first coupon paid 2025-01-10):

| reference date | engine `DiscountingSwapEngine(h, True)` | `DiscountingSwapEngine(h)` / `(h, False)` with default Settings |
|---|---|---|
| 2025-01-09 | 0.0 | 0.0 |
| 2025-01-10 (pay date) | **0.0** | -127,914.04 (the flow paid today is dropped) |
| 2025-01-13 | 0.0 | 0.0 |

**Correction found while testing the adapter**: the engine flag is necessary but NOT sufficient. A swap whose LAST flow is paid on the evaluation date is `isExpired()` under the default `Settings.includeReferenceDateEvents = False`, and
`Swap::calculate` then returns NPV 0 before the engine is consulted, so the final flows dropped out of V(D) one mark early (a spurious jump at the maturity: `pv(D) = 0`, cash swept at D+1). The guard therefore also turns
`includeReferenceDateEvents` on for the duration of a call and restores it (a second QuantLib global to own, section 8). The 3y swap of the table above was never affected because it still has later flows.

Cash paid in `[t0, t1)`: the holder-signed leg amounts with `t0 <= payment < t1` (payer: `-fixed + float`). Golden check (`p1_layers.py`): V0 = -369,827.43, V1 = -212,114.86,
cash = -86,490.36, total = 71,222.20, equal to the rateslib golden numbers to 0.01.

Missing fixings: QuantLib raises `RuntimeError: 2nd leg: Missing <index name>ON Actual/360 fixing for January 3rd, 2024` for a date before the evaluation date without a stored fixing (measured, `p1_misc.py`); a fixing stored for the evaluation date itself IS used
(measured: an absurd 50% fixing dated today moved the NPV by 1,354). The adapter mirrors the reference guard exactly (so both stacks raise on the same data): every business day of the snapshot calendar from the effective date to the day before the
reference date must have a fixing, else `MarketDataUnavailable` ("N SOFR fixings missing between A and B; first D"); a started swap with no fixings series at all raises the same type. A swap that has matured (last payment before the reference date) is worth 0.

## 5. Item 4: DV01 (`p1_dv01.py`; 12 dates, 117 positions)

| Definition | QuantLib | rateslib | disagreement |
|---|---|---|---|
| fresh swap, analytic fixed-leg PV01 | `-swap.fixedLegBPS()` on the dense curve | `analytic_delta(leg=1)` | rel 3.9e-16 |
| par-rate-space market dv01, all pillar par rates +1bp together | central bump +-0.5bp of every helper quote, re-bootstrap, revalue | sum of the delta ladder | QL parallel vs QL ladder sum 2.5e-8 (aged), 4.0e-7 (fresh) |
| aged swap: sum of the risk-curve ladder | 22 bumps | `RLSwap.dv01` (started) | QL vs rateslib ladder sum 3.8e-8 |
| aged swap: analytic fixed-leg PV01 | | | **overstates market dv01 by up to 25%** (rateslib analytic / ladder = 1.252) |

* At par, analytic == ladder sum to 9.4e-4 (worst over 2Y-30Y; 10Y 1.3e-4 as in carryover 1.7), in BOTH libraries; the two ladder sums agree with each other to 5.5e-6. It is not a QuantLib vs rateslib
  difference: it is the difference between the annuity and the response of a par-swap curve.
* Off par (fixed 3.9% against a 4.5% market) the analytic PV01 differs from the market dv01 by up to 34% (the `(S - K) dA/dS` term). A not-yet-started swap is
  built at par, so rateslib reports the analytic value; a started swap reports the ladder sum. The QuantLib adapter follows the same rule so that L2 compares like with like
  (definition: `dv01` = analytic PV01 while the swap has not started; once started, the SUM OF ITS DELTA LADDER over the bound `tenors`, exactly the reference definition). The default bindings pass the same `tenors` to `dv01`, `gamma` and
  `delta_ladder`, so `sum(delta_ladder) == dv01` holds exactly for a started swap. The pillar set matters: 4 pillars against 11 pillars change an aged swap's dv01 by up to 9.8e-3 (median 1.8e-3, 39 aged swaps, `p1_pillarsets.py`),
  so a user who overrides `tenors` on one binding must override it on the others. (An earlier draft summed nothing and used a parallel par bump, which agrees with the ladder sum to 2.5e-8; it was dropped to keep the definition literal.)
* Sign: for a payer `-fixedLegBPS()` is positive, receiver negative; pinned by the fixed-rate bump test (`NPV(K - 1bp) - NPV(K) = dv01`).

## 6. Item 5: the risk-curve delta ladder (`p1_ladder.py`, `p1_ladder_eom.py`, `p1_ladder_feb29.py`, `p1_timing.py`)

rateslib (`build_par_swap_ladder`): nodes at the terminations of the spot-start pillar swaps, DFs seeded from the dense curve, solved so each pillar swap reprices at
the dense curve's par; ladder = `delta(curves=risk_curve, solver=solver)`. QuantLib construction that reproduces it:

```python
helpers = [ql.OISRateHelper(2, ql.Period(t), ql.QuoteHandle(q_t), overnight_index, ql.YieldTermStructureHandle(), False, 2, ql.ModifiedFollowing, ql.Annual, cal,
                             ql.Period(0, ql.Days), 0.0, pillar=ql.Pillar.CustomDate, customPillarDate=termination_t, endOfMonth=False,
                             fixedCalendar=cal, overnightCalendar=cal)
           for t in tenors]                       # q_t = the dense curve's par rate of the pillar swap
risk = ql.PiecewiseLogLinearDiscount(ref, helpers, ql.Actual360())
```

Two settings are essential (each measured, worst bucket difference against rateslib over the same 155 positions):

| setting | wrong default | effect | with the fix |
|---|---|---|---|
| `pillar=ql.Pillar.CustomDate, customPillarDate=<termination>` | `LastRelevantDate`: the node sits at the LAST PAYMENT date (termination + 2 business days) | up to 1.2e-2 relative bucket error | 2.3e-6 |
| `endOfMonth=False` | defaults to `calendar.isEndOfMonth(startDate)`: turned ON whenever the spot date is the last business day of a month | 2.3e-3 (ref 2025-02-26: leap-year February 2028 rolls to the 29th) | 7.9e-8 |

Bump: central +-0.5bp per pillar (`quote.setValue(par +- 0.5e-4)`, re-bootstrap), value the position on each static scenario curve, `ladder_i = (V(+) - V(-)) per bp`.
Measured, 155 positions (fresh spot swaps 2Y..30Y and off-pillar 4Y, aged 3Y/5Y/10Y swaps, payer and receiver, 20 reference dates):

| pillar set | max bucket difference / max bucket | sum of the ladder | QuantLib bootstrap residual |
|---|---|---|---|
| 11 pillars (3M 6M 1Y 2Y 3Y 5Y 7Y 10Y 15Y 20Y 30Y) | 2.3e-6 | 4.3e-6 | 7e-9 bp |
| 4 pillars (2Y 5Y 10Y 30Y) | 2.0e-6 | 2.5e-6 | 5e-9 bp |
| Feb-29 spot date (2024-02-27), 11 / 4 pillars | 1.7e-6 / 3.1e-7 | | |

(the target was 1e-3.) The remaining 2e-6 is rateslib's solver tolerance (it reprices pillars to ~0.01bp; QuantLib's bootstrap is exact). Cost: risk-curve build 86 ms per
snapshot (bootstrap + 25 static scenario curves), then 4 ms per position for all 22 revaluations; so the per-snapshot curves are memoised on the wrapped pricer.

**Lifetime trap.** `OISRateHelper` and `PiecewiseYieldCurve` observe `Settings.evaluationDate` and RE-INITIALISE their dates when it changes; restoring the date while they are
alive raised `pillar date ... must be later than or equal to the instrument's earliest date` (the date IS restored, the exception comes from the observer notification;
`p1_evalguard.py`). Therefore the adapter builds the bootstrap inside the guarded call, extracts plain `ql.DiscountCurve` scenario curves (static: reference date = first
node) and DESTROYS the helpers before the guard exits.

## 7. Item 6: bonds (`p1_bond*.py`)

Construction, US Treasury `us_gb_tsy` with pricebt's `ex_div=0` and settlement = reference date:

```python
sched = ql.Schedule(issue, maturity, ql.Period(ql.Semiannual), ql.NullCalendar(), ql.Unadjusted, ql.Unadjusted, ql.DateGeneration.Backward, True)   # eom True
dc    = ql.ActualActual(ql.ActualActual.ISMA, sched)
bond  = ql.FixedRateBond(0, 100.0, sched, [coupon / 100.0], dc, ql.Unadjusted, 100.0, issue)          # settlementDays 0, UNADJUSTED payment
dirty = bond.dirtyPrice(y / 100.0, dc, ql.SimpleThenCompounded, ql.Semiannual, settle)                 # Treasury: simple interest in the first fractional period
```

| Quantity | worst disagreement | sample |
|---|---|---|
| payment dates: `Following` adjustment on the holiday set (below) | 0 of 1230 bonds | all bonds with issue >= 2000 |
| coupon / redemption amounts per 100 | 1.1e-14 | same |
| dirty price from yield, treasury convention | 6.3e-13 per 100 | 250 bonds, 11,552 points |
| accrued, off coupon dates | 1.1e-14 | 15 bonds (`p1_bond.py`) |
| clean price, off coupon dates | 4.8e-13 | same |
| yield from a clean price | QuantLib round trip 1.0e-11 percent; rateslib round trip 1.7e-7 percent | 11,552 points |
| risk `-dP/dy` (per 100 per 1 percent point), FD of the price | 1.6e-9 relative (QL 2nd-order FD vs rateslib 2nd-order FD); 6.5e-12 (QL 4th-order FD vs rateslib ANALYTIC) | |
| convexity `d2P/dy2`, FD of the price | 1.7e-9 (floor 1) | |

Settings and findings:

1. **Yield convention**: `ql.SimpleThenCompounded` = rateslib `us_gb_tsy` (3.7e-13 on 320 random points). `ql.Compounded` differs by up to 9.8e-3 per 100. `ql.Compounded` equals rateslib's
   street convention `us_gb` to 3.7e-13 EXCEPT when the settlement lies in the final coupon period (up to 8.8e-3): `us_gb` uses simple interest in the last period (`compounding_final_simple`),
   which QuantLib has no compounding enum for. The adapter honours `yield_convention: treasury` and rejects `street` (9.5).
2. **Schedule**: `Backward` from the maturity with `eom=True` on a `NullCalendar` reproduces rateslib's front-stub and month-end schedules for all 1230 bonds (622 of the reference bonds have an
   off-cycle issue date, 1084 have month-end maturities, all covered). The calendar must be `NullCalendar` for the SCHEDULE: `endOfMonth` uses `calendar.isEndOfMonth(seed)`, i.e. the last BUSINESS day
   with a business calendar, which would not treat a Sunday 30 April maturity as month-end.
3. **Payment dates versus yield math**: rateslib accrues on UNADJUSTED coupon dates but PAYS on the next business day of the calendar (`payment_lag 0`, Following), e.g. coupon Sunday 2021-01-31 is paid Monday
   2021-02-01. QuantLib's yield math discounts at `cashflow.date()` = the payment date and `ActualActual(ISMA, schedule)` raises `Dates out of range of schedule` for an adjusted payment date after
   the schedule end. So the pricing bond is built with UNADJUSTED payment dates (agrees to 6e-13) and the payment dates for the cash sweep are `cal.adjust(coupon_date, ql.Following)` outside the yield
   math (0 differences in 1230 bonds). No business day lies strictly between a coupon date and its payment date on the same calendar, so V(D) is never ambiguous.
4. **Settlement on a coupon date**: `bond.dirtyPrice(y, ..., settle)` excludes a coupon paid ON the settlement date (the seller keeps it), same as rateslib `price(dirty=True)`. `bond.accruedAmount(coupon_date)` is
   **0**: QuantLib natively implements the MARKET clean convention (see 9.3 for the rateslib quirk), so no `market_clean` special case is needed; the V(D) = dirty + flows paid on D rule is adapter arithmetic.
5. **Risk measures**: FD of `dirtyPrice` in the yield. `ql.BondFunctions.duration(..., Modified)` with `SimpleThenCompounded` is NOT reliable (5.2e-3 relative vs rateslib), so it is not used. a 2nd-order central difference at 0.5bp is 3.3e-7 from
   rateslib's analytic risk, a 4th-order stencil is 6.5e-12 (so rateslib's analytic risk is exact and the 3.3e-7 was truncation error); the adapter uses the 4th-order stencil.
6. **Financing**: adapter arithmetic, replicated from the shipped bond adapter: `-sign * notional/100 * dirty(prev) * (1 - haircut) * (gc - specialness_bp/100)/100 * seconds/(360*86400)`, seconds elapsed between the two mark
   timestamps, GC = newest fixing strictly before the reference date (fixing unit converted: percent), older than 10 calendar days raises.

## 8. The two libraries' process-global state (what the guard must own)

| | rateslib | QuantLib |
|---|---|---|
| valuation date | none (curve initial node) | `Settings.instance().evaluationDate` (a date change re-initialises every live relative-date object and can raise from an observer; the date is still set) |
| fixings | `rl.fixings` store, named `PBT_SOFR_1B` | `IndexManager`, keyed by the index name; `ql.IndexManager.instance().clearHistories()` would wipe EVERY index in the process: clear only our own name |
| flow-on-reference-date switch | none | `Settings.includeReferenceDateEvents`: turned ON inside the guard and restored (with it off, a swap whose last flow is paid today is `isExpired()` and worth 0) |
| calendars | `rl.get_calendar` returns shared objects; custom `Cal` objects are private | named calendars are singletons whose `addHoliday` mutates global state; `BespokeCalendar` is private |
| licence banner | printed once on import | none |

Adapter policy: `_compat.guard(eval_date)` sets the evaluation date and `includeReferenceDateEvents` for the duration of one call and restores both in `finally`; the fixings history of OUR index name is cleared on entry and on exit, so no state survives
a call and time moving backwards (a new run in the same process) can never see a later run's fixings; the evaluation-date restore tolerates the exception a FOREIGN live relative-date helper raises from its notification (the date is still set; asserted);
no bootstrap object of ours outlives its call: the risk-curve bootstrap runs in a function frame that ends before the guard exits (`_risk._bootstrap`), so nothing of ours can raise from a later date change.

## 9. Gaps that remain, each characterised

### 9.1 Calendar horizon (data, not convention)
`nyc.json` (the fixture calendar, and hence any `CalendarData` built from it) holds holidays to 2035-12-25; rateslib's built-in `nyc` extends to 2200. A snapshot-driven refit of
the rateslib adapter sees the JSON, not the built-in, so schedule dates after 2035 (20Y and 30Y swaps from 2018 pay to 2056) see no holiday. Measured effect against the built-in calendar (25 dates,
`p1_horizon.py`, 25 random dates): 15Y 0.93, 20Y 5.06, 30Y 16.46 currency on a 10mm payer (1.6e-6 relative); 10Y 0.00. The worst case is larger: when a coupon date after 2035 lands on a holiday the swap moves by up to ONE DAY OF INTEREST on that
coupon (514 on 10mm in the 8-date sample of `tests/test_quantlib_tieout.py`, bound 1,400 = 10mm x 5% / 360). 65 of 3311 termination dates differed before the calendars were aligned, all >= 2036-01-22.
Recommendation: the provider extends `CalendarData` past the longest maturity of the run, and `CalendarData` carries `first`/`last` coverage so an adapter can refuse a date outside it (the adapter
raises when `provenance['last']` is present and a schedule date exceeds it). The rateslib adapter refit must read the snapshot calendar, not `get_calendar('nyc')`.

### 9.2 Resolved terms do not pin the schedule for stub tenors
Two rateslib swaps with the same effective and adjusted termination dates can have different rolls (18M/30M tenors: 425 of 3510 differ). The resolved terms of T4 carry only the two dates.
Both adapters must therefore build from the SAME rule (tenor -> schedule) and the harness should compare `roll`/unadjusted maturity too. Whole-year tenors are unaffected.

### 9.3 rateslib 2.7.1: accrued interest on a coupon date
On an accrual end date rateslib's `accrued()` returns the FULL coupon (2.19 in the sample) while QuantLib returns 0 (market convention). Dirty prices agree (excluded coupon); clean prices differ by exactly the
coupon. Also `ytm(clean, dirty=False)` on such a date is wrong in rateslib. Documented in `contrib/rateslib/bond.py mark()`; the QuantLib adapter needs no special case.

### 9.4 rateslib's analytic convexity and yield solver tolerance
`rateslib` `ytm()` round-trips a price to 1.7e-7 percent (about 1.7e-5 bp): its own solver tolerance, not a convention. Effect on a 10mm PV: 1e-8 relative. QuantLib's bootstrap of yields is exact to 1e-11.
rateslib's analytic risk is exact (a 4th-order FD of the QuantLib price agrees to 6.5e-12; a 2nd-order FD at 0.5bp is off by 3.3e-7, i.e. truncation). The tie-out tolerances of Appendix B (1e-4 / 1e-2 for dv01 / gamma) absorb both.

### 9.5 `yield_convention: street` (rateslib `us_gb`) cannot be expressed in QuantLib
Compounded first period, simple FINAL period; QuantLib's `Compounding` enum has `SimpleThenCompounded` and `CompoundedThenSimple` (the latter is compounded-then-simple after the FIRST period) but no "simple in the
last period". Outside the final coupon period `ql.Compounded` equals `us_gb` to 3.7e-13. The adapter rejects `street` with `ConfigError` naming this reason rather than silently diverging by up to 8.8e-3 per 100.

### 9.6 `time_accrual: linear`
Neither adapter of the old code implemented intraday carry accrual; the QuantLib adapter honours `lump` (carry and roll at the date change, intraday marks on the same reference date carry no time layers) and
rejects `linear` with `ConfigError`.

### 9.7 Second differences of rateslib re-solved curves carry solver noise (a trap for the reference side)
The par-space `gamma` (second difference of the PV for a parallel move of all pillar par rates) evaluated with rateslib by RE-SOLVING the risk curve at +-1bp with the shipped tolerance (`func_tol=1e-9, conv_tol=1e-11`) is off by 22% (-102.07 against -83.40 on a 100mm 10Y payer): the solver's
residual (~3e-3 bp) is larger than the second difference. Run to `func_tol=conv_tol=1e-20` it is -83.39935 against QuantLib's -83.39936 (1.3e-7). First derivatives are unaffected (the ladder agrees to 2e-6). Any finite-difference measure of the refit rateslib adapter that re-solves must tighten the tolerance.

### 9.8 QuantLib traps the constructions above avoid (each measured)
`Schedule(..., Backward)` loses a clamped roll day (7 of 5064); `OISRateHelper` defaults `pillar` to the last payment date (up to 1.2e-2 ladder error) and `endOfMonth` to `calendar.isEndOfMonth(start)` (2.3e-3); `addHoliday` on a named calendar is global; `ql.Sofr` hard-wires a named calendar;
the bond yield math discounts at the payment date (an adjusted payment date raises inside `ActualActual(ISMA, schedule)`); `ql.BondFunctions.duration(Modified)` with `SimpleThenCompounded` is 5.2e-3 off; a swap whose last flow is paid today is expired (section 4); a relative-date helper alive while the evaluation date changes raises from the notification.

## 10. Proposed shared convention vocabulary (T5)

One dict, handed verbatim to the rateslib adapter, the QuantLib adapter and a reference stack. Tokens are neutral spellings (no library class names). An adapter MUST raise `ConfigError` on an unknown KEY,
on an unknown VALUE token, on a known but unsupported value (naming the reason), and on a missing required key. There are NO implicit defaults: an explicit block is what makes the tie-out honest and
is hashed into the manifest. Defaults are shipped as constants in the adapter (`USD_SOFR_OIS_CONVENTIONS`, `UST_CONVENTIONS`).

The tokens, types and key lists live in the shipped `swap` and `bond` schemas (`conventions:`) and are enforced by `AssetSchema.check_conventions(raw, require_all=True)`; the adapter calls it first and then applies its own supported-subset check (`conventions.py`), so the
column "supported by the QuantLib adapter" below is what the adapter honours on top of the shared vocabulary. Every non-default supported value was verified against rateslib in `tests/test_quantlib_tieout.py` (frequency semiannual, payment lag 0/1/2, business-day convention following, spot lag 1: all agree to 4e-9 on the NPV).

Common tokens
* `day_count`: `act360`, `act365f`, `actact_icma`, `actact_isda`, `thirty360`
* `business_day_convention`: `unadjusted`, `following`, `modified_following`, `preceding`, `modified_preceding`
* `frequency`, bond `compounding`: `monthly`, `quarterly`, `semiannual`, `annual`
* `stub`: `short_front`, `long_front`, `short_back`, `long_back`
* booleans are YAML booleans; integers are YAML integers.

### 10.1 Swap (`asset_class: swap`)

| key | type / tokens | meaning | rateslib `usd_irs` | QuantLib construction | supported by the QuantLib adapter |
|---|---|---|---|---|---|
| `calendar` | str: the NAME of a `CalendarData` in the snapshot | schedule, spot and payment calendar | `calendar` | `BespokeCalendar` from the holidays | yes (required) |
| `spot_lag_days` | int | business days trade date -> effective | spot via `add_bus_days(2)` | `cal.advance(ref, n, Days)` | any n >= 0 (0, 1 and 2 verified against rateslib) |
| `day_count` | day-count token | accrual of BOTH legs | `convention: act360` | `Actual360()` | `act360` (`act365f` was not verified against rateslib: rejected) |
| `frequency` | frequency token | payment frequency of both legs | `frequency: a` | period of the schedule | `annual`, `semiannual` |
| `business_day_convention` | convention token | accrual date adjustment | `modifier: mf` | `Schedule` adjust | `modified_following`, `following` |
| `payment_lag_days` | int | business days after the accrual end | `payment_lag: 2` | `paymentLag` | any n >= 0 |
| `compounding` | `daily_compounded`, `daily_average` | floating leg accrual of daily rates within a period | `leg2_fixing_method: rfr_payment_delay` | `RateAveraging.Compound` | `daily_compounded` |
| `stub` | stub token | where an irregular period goes | `stub: shortfront` | own date generation | `short_front` |
| `end_of_month` | bool | month-end roll | `eom: False` | Schedule / helper `endOfMonth` (ALWAYS explicit) | `false` |
| `fixing_lag_days` | int | business days between the date a rate applies to and its publication; the snapshot's fixings are strictly before the reference date | provider policy `after_bdays: 1` | index fixing days 0, fixings < today | `1` |
| `time_accrual` | `lump`, `linear` | intraday time in carry/roll | lump | - | `lump` |

`USD_SOFR_OIS_CONVENTIONS = {calendar: nyc, spot_lag_days: 2, day_count: act360, frequency: annual, business_day_convention: modified_following, payment_lag_days: 2, compounding: daily_compounded, stub: short_front, end_of_month: false, fixing_lag_days: 1, time_accrual: lump}`.

### 10.2 Bond (`asset_class: bond`)

| key | type / tokens | meaning | rateslib `us_gb_tsy` (+ pricebt overrides) | QuantLib construction | supported by the QuantLib adapter |
|---|---|---|---|---|---|
| `calendar` | str: name of a `CalendarData` | payment-date adjustment (and settlement days if > 0) | `calendar: nyc` | `BespokeCalendar` | yes (required) |
| `settlement_lag_days` | int | business days trade -> settlement; pricebt marks at the reference date | settle at the reference date | `settlementDays 0` | `0` |
| `day_count` | day-count token | coupon accrual | `convention: actacticma` | `ActualActual(ISMA, schedule)` | `actact_icma` |
| `frequency` | frequency token | coupons per year | `frequency: s` | `Semiannual` | `semiannual` |
| `business_day_convention` | convention token | accrual date adjustment | `modifier: none` | `Unadjusted` | `unadjusted` |
| `payment_business_day_convention` | convention token | payment date adjustment when the lag is 0 | (implicit Following) | adapter arithmetic `cal.adjust` | `following` |
| `payment_lag_days` | int | business days after the accrual end | `payment_lag: 0` | 0 | `0` |
| `stub` | stub token | | `stub: shortfront` | `Backward` | `short_front` |
| `end_of_month` | bool | | `eom: True` | `Schedule(..., endOfMonth=True)` on `NullCalendar` | `true` |
| `compounding` | frequency token | yield quotation frequency | semiannual | `ql.Semiannual` | `semiannual` |
| `yield_convention` | `treasury`, `street` | first-period rule of the price/yield conversion | `calc_mode: us_gb_tsy` | `SimpleThenCompounded` | `treasury` (`street`: 9.5) |
| `ex_dividend_days` | int | ex-coupon period | `ex_div: 0` | none (0) | `0` |
| `financing_day_count` | day-count token | repo accrual | ACT/360 on elapsed seconds | adapter arithmetic | `act360` |
| `time_accrual` | `lump`, `linear` | | lump | | `lump` |

`UST_CONVENTIONS = {calendar: nyc, settlement_lag_days: 0, day_count: actact_icma, frequency: semiannual, business_day_convention: unadjusted, payment_business_day_convention: following, payment_lag_days: 0, stub: short_front, end_of_month: true, compounding: semiannual, yield_convention: treasury, ex_dividend_days: 0, financing_day_count: act360, time_accrual: lump}`.

Market-data names that are NOT conventions and are therefore arguments of the adapter's `wrap(pricer, curve=..., fixings=...)`: the name of the discount/projection curve and of the fixings series.
Per-trade financing terms are `extras`: bond `extras: {repo: {gc_rate: <percent number> | pricer, specialness_bps: <number>, haircut: <fraction>}}` (any other key is a `ConfigError`); swap `extras: {par_spread_bp: <number>}` (the fixed rate is `par` plus the spread in basis points, resolved to a PERCENT rate;
`IRSwap(fixed_rate='ATMF+25')` arrives as `fixed_rate: par` + `par_spread_bp: 25`; a spread with a numeric fixed rate, a non-numeric spread and any other extras key are `ConfigError`s).

## 11. Layer definitions of the QuantLib adapter (design, verified on the golden case by `p1_layers.py`)

QuantLib has no automatic differentiation; all layers are bump-and-revalue in ONE relinkable-handle world per date (no swap is rebuilt per scenario), with two extra revaluations for the two directional derivatives and none of them a Hessian:

Per unit, interval `(t0, t1]`, `V(curve, date, fixings)` = the position's PV in the world made of a discount/projection curve anchored at `date`, evaluation date `date` and the fixings published by `date`;
`cash` = flows paid in `[t0, t1)` at the t1 world.

* `X_fwd` = PV at t1 of the t0-world flows: `sum(flow_p * DF0(p) / DF0(t1) for paid >= t1) + sum(flows paid in [t0, t1))`, flows projected on the t0 curve with t0 fixings.
* `X_roll` = `V(rolled curve, t1, t1 fixings) + cash`, where `rolled curve` is anchored at t1 with `DF(t1 + tau) = DF0(t0 + tau)` (tenor space static).
* `V_base` = `V(rolled curve resampled at the t1 curve's node dates, t1)`.
* `carry = X_fwd - V0`: value change with the market static in DATE space, including the flows paid in the interval.
* `roll = X_roll - X_fwd`: tenor-space roll-down PLUS the difference between realised and assumed fixings (the schema wording).
* `delta = (V_base + cash - X_roll) + g . dz` and `convexity = 1/2 dz' H dz`, with `dz` the realised move of the ZERO rates at the t1 curve's node dates from the resampled base. `g . dz` and `1/2 dz' H dz` are
  the first and second DIRECTIONAL derivatives along `dz`, obtained from two revaluations: `V_eps = V(base zero rates + eps * dz)`, `g.dz = (V_h - V_-h)/(2h)`, `dz'Hdz/2 = (V_h + V_-h - 2 V_0)/(2 h^2)` with `h = 0.1`.
  This is the exact rateslib definition (the AD gradient and Hessian contracted with `dz`) without ever forming the ~45-node Hessian.
* `unexplained` is the engine's: `V1 - V_base - g.dz - dz'Hdz/2` (measured 0.02 on the golden case).

Golden case result (`p1_layers.py`): carry -561.82, roll 1532.07, delta 70302.17, convexity -50.23, residual 0.02, total 71,222.20 (rateslib: -561.82, 1532.07, 70302.16, -50.23, 0.02, 71,222.20).

The `dv01`/`gamma`/`delta_ladder` measures are in PAR-rate space (per-pillar and parallel +-1bp bumps of the par-swap risk curve of section 6); the `delta` layer is in ZERO-rate space along the realised move. The two spaces are
different risk factors (as they are in the shipped rateslib adapter, carryover 6.3), so `delta ~ dv01 * (realised par move)` only to first order.

`gamma` is the SECOND DIFFERENCE IN THE SAME VARIABLE AS `dv01` (schema: "the same +1bp move as dv01"), i.e. par space: (V(+1bp) + V(-1bp) - 2 V0) per bp^2 on the risk curve of the bound `tenors`. The shipped rateslib adapter's `convexity` is the zero-rate parallel shift on the dense curve
(carryover 6.3 flags the mismatch). For a like-for-like comparison the adapter also ships `dv01_zero` and `gamma_zero` (central differences of full revaluations under a parallel shift of the continuously-compounded zero rates of the dense curve, the formula of
`test_rl_swap.py`), and a user can rebind: `bind: {gamma: {target: {method: gamma_zero}, kwargs: {ctx: "@ctx"}}}`. On a 10Y par swap 100mm: `gamma` -83.40, `gamma_zero` -81.02 (2.9%); `dv01` 8,081 vs `dv01_zero` 8,294 (2.6%).

## 12. Contract gaps found (for the orchestrator)

1. `CalendarData` has no coverage fields (`first`, `last`): a schedule date beyond the last holiday is silently treated as a business day (9.1). The fixture calendar stops in 2035 while 30Y swaps pay to 2056.
2. Resolved terms (`effective`, `maturity`, `fixed_rate`) cannot express the roll or the unadjusted maturity, and two schedules with the same adjusted dates can differ for stub tenors (9.2). The harness compares the dates first (L0); it should also compare `roll`.
3. `Kit` has no default-conventions field: the complete blocks ship as constants (`USD_SOFR_OIS_CONVENTIONS`, `UST_CONVENTIONS`) and a user pastes them into `conventions:`. (The vocabulary itself is now in the schemas, `check_conventions`; done.)
4. Nothing in the contract says who wraps: this adapter assumes `market.pricers.<role>.wrap` produces a `QLPricer` for BOTH `pricer` and `prev_pricer`, and it rejects an unwrapped pricer with the fix in the message. The `Stack` object for `PricebtSession.use` is the coordinator's to add (`wrap`, `swap`, `bond`, the two convention constants are exported).
5. The engine deep-copies positions: adapter objects are plain data (dates, percent rate, notional, frozen convention dataclass) and rebuild their QuantLib instruments per call; a `QLPricer` drops its QuantLib cache when pickled.
6. There is no place to ship an extension schema `doc:` for adapter extension names (`Kit.extra`: `dv01_zero`, `gamma_zero`); the layer and measure definitions are in the module docstrings, `Kit.doc` and section 11.
7. The `unspecified` price convention (the `QuoteSet` default) has no defined meaning for a clean price on a coupon date: the adapter raises exactly on that intersection (`PRICE-CONVENTION`) and accepts everything else (off coupon dates the conventions agree; yield quotes have none).
8. Pricer lookups the old suites used (`par_rate`, `calendar_advance`, `spot_date`) need the conventions block, which a pricer does not own; `QLPricer` exposes only the data lookups `security` and `quote`. A strategy signal that needs a par rate must call the instrument's `rate` measure or a bound function.
9. The rateslib adapter refit must read holidays from the snapshot (not `get_calendar('nyc')`), tighten solver tolerances before any re-solving finite difference (9.7), and note that `RLSwap`'s `from_maturity`-style rebuild from an adjusted date changes the roll for stub tenors (9.2).

## 13. Evidence for the adapter (tests and mutation checks)

`python -m pytest tests/test_quantlib_*.py -q`: 151 passed, 0 skipped, 39 s (conventions 12, wrap 15, globals 10, swap 54, bond 22, tie-out 37, real-fixture steps 1; the fixture-dependent tests skip with an explicit reason when `data/fixtures` is absent).
Markers: every test is `adapter_quantlib`; the tie-out file is also `adapter_rateslib`; fixture tests are also `fixtures`. rateslib is evaluated directly (`tests/quantlib_support/rl_reference.py`, public API only), never through the shipped adapter, so the tie-out survives the rateslib refit.

Acceptance numbers asserted directly (not adapter against adapter): the seasoned 3y payer 50mm (V0 -369,827.43, V1 -212,114.86, cash -86,490.36, carry -561.82, roll 1,532.07, delta 70,302.16 to 0.02, convexity -50.23, residual 0.02) and the 10mm long UST
(pv 9,979,417.302 / 9,724,039.126, financing -9,535.8876, carry 136.580, delta -52,718.946, convexity 168.692).

Mutation checks (`tools/mutcheck.py`, specs `tests/support/mutcheck/quantlib/mut_*.json`, round 2 `mut2_*`/`mut3_*` (the round-1 bond spec was superseded by later edits and is not kept)): 162 one-line mutations of the adapter (unit conversions, payer sign, cash window and payment-on-reference-date rules, evaluation-date restore and fixings clearing, calendar built from the holiday set,
yield convention, ladder bucket keys and bumps, every layer formula, conventions vocabulary). Round 1: 142 killed, 20 survived. 14 survivors were genuine test gaps (a maturity before the effective date, an unverified day count, duplicate ladder maturities, the ladder's spot lag, a window with two
payment dates, a flow paid on the layer interval's end, shared mark contexts, the pull-down curve of the PREVIOUS snapshot and its 365.25 day years, off-the-run aliases, the last payment date of a bond, a business calendar in the bond schedule): each got a test and each is now killed (14 of 14).
6 are equivalent mutants, left as they are:

| mutant | why it cannot change an observable |
|---|---|
| `tenor-grammar-unchecked` (`_risk.clean_tenors`) | the same grammar is enforced again by `_schedule.tenor_months` when the pillar swaps are built; the message and error type are identical |
| `fixings-before-effective-kept` | fixings dated before the effective date are never read and the contiguity check starts at the effective date |
| `ladder-priced-on-dense-curve` | the seed curve of the ladder world is relinked before any NPV is taken |
| `gamma-scale` (`** 2` -> `** 1`) | the parallel bump is exactly 1bp, so the divisor is 1 either way |
| `zero-space-days-360` (layers) | the day count of the zero rate cancels in `dz * tau`: only the direction of the move enters the directional derivatives |
| `gc-fixing-not-strictly-before` (bond) | the snapshot admits no fixing dated on or after its reference date, so `<=` and `<` select the same fixing |

