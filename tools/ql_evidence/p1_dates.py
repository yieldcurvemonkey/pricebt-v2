"""Phase 1 item 1 and 7: date resolution + calendar. Known-answer control first."""
import sys

sys.path.insert(0, "tools/ql_evidence")
from common import *  # noqa

hol = load_cal("nyc")
cal = ql_calendar(hol)
rcal = rl.get_calendar("nyc")

# ---- control: known answers. 2024-07-03 (Wed) + 2 bd: 07-04 is a holiday -> Mon 07-08? (Wed->Thu 4th holiday->Fri 5th=1, Mon 8th=2)
k = cal.advance(qd(dt.date(2024, 7, 3)), 2, ql.Days)
assert pd_(k) == dt.date(2024, 7, 8), pd_(k)
assert cal.isBusinessDay(qd(dt.date(2024, 7, 5))) and not cal.isBusinessDay(qd(dt.date(2024, 7, 4))) and not cal.isBusinessDay(qd(dt.date(2024, 7, 6)))
# leak check: a named calendar must NOT have picked up holidays (addHoliday on a bespoke calendar is private)
assert ql.UnitedStates(ql.UnitedStates.SOFR).isBusinessDay(qd(dt.date(2024, 7, 5)))
plain = ql.WeekendsOnly()
assert plain.isBusinessDay(qd(dt.date(2024, 7, 4))), "bespoke addHoliday leaked into WeekendsOnly"
print("controls ok")

# ---- is addHoliday on a WeekendsOnly calendar global? (documentation of the trap)
w = ql.WeekendsOnly()
w.addHoliday(qd(dt.date(2031, 3, 5)))
print("WeekendsOnly().addHoliday leaks to a new WeekendsOnly():", not ql.WeekendsOnly().isBusinessDay(qd(dt.date(2031, 3, 5))))
w.removeHoliday(qd(dt.date(2031, 3, 5)))

# ---- 1a business day agreement over the whole data window
bad = 0
d = dt.date(2018, 1, 1)
while d < dt.date(2035, 12, 31):
    a = rcal.is_bus_day(fdt(d))
    b = cal.isBusinessDay(qd(d))
    bad += a != b
    d += dt.timedelta(days=1)
print("business-day disagreements rateslib nyc vs ql bespoke(nyc json), 2018..2035:", bad)

# ---- 1b spot date over many reference dates
refs = [d.date() for d in pd.bdate_range("2018-04-02", "2026-09-04") if rcal.is_bus_day(fdt(d.date()))]
mism = 0
for r in refs:
    a = rcal.add_bus_days(fdt(r), 2, True)
    b = pd_(cal.advance(qd(r), 2, ql.Days))
    mism += a.date() != b
print("spot date mismatches (weekdays incl holidays as ref):", mism, "of", len(refs))

# ---- 1c tenor addition and IRS termination
tenors = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "15Y", "20Y", "30Y", "6M", "18M"]
rows = []
tm_mism = 0
n = 0
for r in refs[::7]:
    spot = rcal.add_bus_days(fdt(r), 2, True)
    for t in tenors:
        irs = rl.IRS(effective=spot, termination=t.lower(), spec="usd_irs", notional=1e6)
        term_rl = irs.leg1.schedule.termination
        # rateslib alt route
        term_rl2 = rl.add_tenor(spot, t, "MF", "nyc")
        term_ql = pd_(cal.advance(qd(spot.date()), ql.Period(t), ql.ModifiedFollowing, False))
        n += 1
        if term_rl.date() != term_ql:
            tm_mism += 1
            if tm_mism < 6:
                print("MISMATCH", r, spot.date(), t, "rl sched term", term_rl.date(), "rl add_tenor", term_rl2.date(), "ql", term_ql)
        rows.append((r, t, term_rl.date(), term_rl2.date(), term_ql, irs.leg1.schedule.uschedule[-1].date()))
print("termination mismatches:", tm_mism, "of", n)
df = pd.DataFrame(rows, columns=["ref", "tenor", "rl_sched_term", "rl_add_tenor", "ql", "rl_unadj_last"])
print("add_tenor == sched term:", (df.rl_sched_term == df.rl_add_tenor).mean(), "; unadj last == sched term:", (df.rl_unadj_last == df.rl_sched_term).mean())
__import__("os").makedirs("results_new/ql_evidence", exist_ok=True)
df.to_csv("results_new/ql_evidence/p1_dates.csv", index=False)
