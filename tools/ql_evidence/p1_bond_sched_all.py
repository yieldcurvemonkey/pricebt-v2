"""Every UST in the reference table (1789 with a coupon): coupon/payment dates and amounts, QuantLib vs rateslib us_gb_tsy (same holiday set)."""
import sys
sys.path.insert(0, "tools/ql_evidence")
from p1_bond import *

HOL = load_cal("nyc")
QCAL = ql_calendar(HOL)
RCAL = rl.Cal(holidays=[fdt(h) for h in HOL], week_mask=[5, 6])
n = bad_dates = bad_amt = fail = 0
worst = 0.0
ex = []
for _, r in REF.iterrows():
    issue, mat, cpn = r.issue_date, r.maturity_date, float(r.cpn)
    if cpn <= 0 or issue < dt.date(2000, 1, 1) or mat > dt.date(2060, 1, 1):
        continue
    try:
        rb = rl.FixedRateBond(effective=fdt(issue), termination=fdt(mat), spec="us_gb_tsy", fixed_rate=cpn, notional=-1e6, ex_div=0, calendar=RCAL)
    except Exception as e:
        fail += 1
        continue
    qb, dc = make_bond(issue, mat, cpn)
    rf = [(p.settlement_params.payment.date(), float(p.cashflow()) / 1e6 * 100) for p in rb.leg1.periods]
    qf = [(pd_(QCAL.adjust(c.date(), ql.Following)), c.amount()) for c in qb.cashflows()]
    n += 1
    if [a[0] for a in rf] != [b[0] for b in qf]:
        bad_dates += 1
        if len(ex) < 3:
            ex.append((r.cusip, issue, mat, rf[:2], qf[:2]))
    elif len(rf) == len(qf):
        d = max(abs(a[1] - b[1]) for a, b in zip(rf, qf))
        worst = max(worst, d)
        bad_amt += d > 1e-9
print(f"bonds compared {n} (rateslib could not build {fail}); payment-date mismatches {bad_dates}; amount mismatches > 1e-9 per 100: {bad_amt}; worst amount diff {worst:.2e}")
for e in ex:
    print(e)
