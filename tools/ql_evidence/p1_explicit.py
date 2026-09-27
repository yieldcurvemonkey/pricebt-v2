"""Explicit-date maturity: how does rateslib treat a date (adjusted termination round trip; inconsistent dates)?"""
import sys
sys.path.insert(0, "tools/ql_evidence")
from qlswap import *
hol = load_cal("nyc"); rcal = rl_calendar(hol); cal = ql_calendar(hol)
# 1. round trip over all refs: IRS(eff, tenor) vs IRS(eff, termination=<its adjusted termination>)
refs = [d.date() for d in pd.bdate_range("2018-04-02", "2026-09-04") if rcal.is_bus_day(fdt(d.date()))]
bad = 0; n = 0
for r in refs[::3]:
    spot = rcal.add_bus_days(fdt(r), 2, True)
    for t in ("2Y", "5Y", "10Y", "18M", "30M"):
        a = rl.IRS(effective=spot, termination=t.lower(), spec="usd_irs", notional=1e6, fixed_rate=3.0, calendar=rcal)
        b = rl.IRS(effective=spot, termination=a.leg1.schedule.termination, spec="usd_irs", notional=1e6, fixed_rate=3.0, calendar=rcal)
        n += 1
        if [x for x in a.leg1.schedule.aschedule] != [x for x in b.leg1.schedule.aschedule] or [x for x in a.leg1.schedule.pschedule] != [x for x in b.leg1.schedule.pschedule]:
            bad += 1
print(f"round trip (tenor vs its own adjusted-termination date) schedule differences: {bad} of {n}")
# 2. inconsistent explicit dates
for eff, M in [(dt.datetime(2025, 1, 8), dt.datetime(2030, 3, 15)), (dt.datetime(2025, 1, 8), dt.datetime(2030, 1, 9)), (dt.datetime(2025, 1, 8), dt.datetime(2027, 1, 8))]:
    a = rl.IRS(effective=eff, termination=M, spec="usd_irs", notional=1e6, fixed_rate=3.0, calendar=rcal)
    s = a.leg1.schedule
    print(eff.date(), "->", M.date(), "roll", s.roll, "n", s.n_periods, "u:", [x.strftime("%y-%m-%d") for x in s.uschedule], "regular", s.is_regular)
