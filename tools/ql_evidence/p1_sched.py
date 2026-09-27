"""Phase 1 items 1-2: schedules, termination dates, payment dates, then at-inception NPV/par/leg PVs. Same holiday set on both sides."""
import sys

sys.path.insert(0, "tools/ql_evidence")
from qlswap import *  # noqa

hol = load_cal("nyc")
cal = ql_calendar(hol)
rcal = rl_calendar(hol)
JS_LAST = hol[-1]

# -------- control: known answer for the tenor->termination. eff 2024-07-03 (Wed), 1Y -> 2025-07-03 (Thu, bus day) ; eff 2024-06-28 10Y -> 2034-06-28
s = make_schedule2(dt.date(2024, 7, 3), "1Y", cal)
assert [pd_(x) for x in s] == [dt.date(2024, 7, 3), dt.date(2025, 7, 3)], list(s)
# 18M: backward from 2026-01-03 (Sat -> MF Mon 01-05): dates 2026-01-05, 2025-01-03, then stub start 2024-07-03
s = make_schedule2(dt.date(2024, 7, 3), "18M", cal)
print("18M control:", [pd_(x).isoformat() for x in s], "unadjusted:", [pd_(x).isoformat() for x in s.dates()][:0])
assert [pd_(x) for x in s] == [dt.date(2024, 7, 3), dt.date(2025, 1, 3), dt.date(2026, 1, 5)]

refs = [d.date() for d in pd.bdate_range("2018-04-02", "2026-09-04") if rcal.is_bus_day(fdt(d.date()))]
tenors = ["6M", "1Y", "18M", "2Y", "30M", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y"]
n = bad_term = bad_sched = bad_pay = bad_acc = 0
ex = []
for r in refs:
    spot = rcal.add_bus_days(fdt(r), 2, True)
    for t in tenors:
        irs = rl.IRS(effective=spot, termination=t.lower(), spec="usd_irs", notional=1e6, fixed_rate=3.0, calendar=rcal)
        sc = irs.leg1.schedule
        qs = make_schedule2(spot.date(), t, cal)
        qa = [pd_(x) for x in qs]
        ra = [x.date() for x in sc.aschedule]
        n += 1
        if sc.termination.date() != qa[-1]:
            bad_term += 1
        if qa != ra:
            bad_sched += 1
            if len(ex) < 4:
                ex.append((r, t, ra[:3], qa[:3], ra[-2:], qa[-2:]))
        # payment dates: QL swap fixed leg
        sw = make_ois2(spot.date(), t, 1, 1e6, 3.0, cal, make_index(cal, ql.YieldTermStructureHandle()))
        qpay = [pd_(c.date()) for c in sw.fixedLeg()]
        rpay = [p.settlement_params.payment.date() for p in irs.leg1.periods]
        if qpay != rpay:
            bad_pay += 1
        qfl = [pd_(c.date()) for c in sw.overnightLeg()]
        rfl = [p.settlement_params.payment.date() for p in irs.leg2.periods]
        if qfl != rfl:
            bad_pay += 1
        # accrual periods of the fixed leg (dcf)
        qdcf = [ql.Actual360().yearFraction(c.accrualStartDate(), c.accrualEndDate()) for c in map(ql.as_fixed_rate_coupon, sw.fixedLeg())]
        rdcf = [float(p.period_params.dcf) for p in irs.leg1.periods]
        if max(abs(a - b) for a, b in zip(qdcf, rdcf)) > 1e-15 or len(qdcf) != len(rdcf):
            bad_acc += 1
print(f"cases {n}: termination mismatches {bad_term}, accrual-schedule mismatches {bad_sched}, payment-date mismatches {bad_pay}, dcf mismatches {bad_acc}")
for e in ex:
    print("  ", e)
print("calendar horizon: json ends", JS_LAST, "; latest schedule date checked", max(pd_(x) for x in make_schedule2(refs[-1], "30Y", cal)))
