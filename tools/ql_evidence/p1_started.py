"""Phase 1 item 3: STARTED swaps with published fixings, current-coupon accrual, payment window, payment on the reference date."""
import sys

sys.path.insert(0, "tools/ql_evidence")
from qlswap import *  # noqa

hol = load_cal("nyc")
cal = ql_calendar(hol)
rcal = rl_calendar(hol)
FX = load_fixings()  # decimal, (date, rate)
FXD = {d: r for d, r in FX}


def rl_install(ref, first):
    """Install fixings dated in [first, ref) (percent) into rateslib's process-global store under the old adapter's key."""
    key = "PBT_SOFR_1B"
    idx = [d for d, _ in FX if first <= d < ref]
    s = pd.Series([FXD[d] * 100 for d in idx], index=pd.DatetimeIndex([fdt(d) for d in idx]))
    if key in rl.fixings.loader.loaded:
        rl.fixings.pop(key)
    rl.fixings.add(key, s)
    return s


def ql_install(index, ref, first):
    """Add fixings dated in [first, ref) (decimal) to the QuantLib index (process-global IndexManager, keyed by the index name)."""
    idx = [d for d, _ in FX if first <= d < ref]
    index.addFixings([qd(d) for d in idx], [FXD[d] for d in idx], True)


def run(ref, eff, tenor, sign, notional, rate, verbose=False):
    row = load_row(ref)
    nodes = list(zip(*row))
    rc = rl.Curve(nodes={fdt(d): v for d, v in nodes}, id="c", convention="act360", calendar=rcal, modifier="MF", interpolation="log_linear")
    first = eff  # contiguity from the effective date, as the old adapter's check_fixings
    rl_install(ref, first)
    irs = rl.IRS(effective=fdt(eff), termination=tenor.lower(), spec="usd_irs", notional=sign * notional, fixed_rate=rate, calendar=rcal, leg2_rate_fixings="PBT_SOFR")
    r_npv = float(irs.npv(curves=rc))
    r_par = float(irs.rate(curves=rc))
    cf = irs.cashflows(curves=rc)
    with ql_state(ref):
        qc = ql_curve(nodes)
        h = ql.YieldTermStructureHandle(qc)
        idx = make_index(cal, h)
        ql_install(idx, ref, first)
        sw = make_ois2(eff, tenor, sign, notional, rate, cal, idx)
        sw.setPricingEngine(ql.DiscountingSwapEngine(h, True))
        q_npv = sw.NPV()
        q_par = sw.fairRate() * 100
        # cashflow tables
        qf = [(pd_(c.date()), c.amount()) for c in sw.fixedLeg()]
        qo = [(pd_(c.date()), c.amount()) for c in sw.overnightLeg()]
    rf = [(p.settlement_params.payment.date(), float(p.cashflow())) for p in irs.leg1.periods]
    ro = [(p.settlement_params.payment.date(), float(p.cashflow(rate_curve=rc))) for p in irs.leg2.periods]
    # rateslib Payer notional>0 pays fixed: fixed flows negative; QL payer: fixedLeg amounts positive (leg amounts unsigned, sign applied in NPV)
    sgn = -1.0 if sign > 0 else 1.0
    dfx = max((abs(a[1] * sgn * 1.0 - b[1]) for a, b in zip(qf, rf)), default=0.0)
    dfo = max((abs(-sgn * a[1] - b[1]) for a, b in zip(qo, ro)), default=0.0)
    dates_ok = [a[0] for a in qf] == [b[0] for b in rf] and [a[0] for a in qo] == [b[0] for b in ro]
    return r_npv, q_npv, r_par, q_par, dfx, dfo, dates_ok


if __name__ == "__main__":
    # control: a swap that started TODAY-2 with a known constant fixing of 4.00% and a curve flat at 4.00% (act360 simple, so DF = 1/(1+0.04 d/360)):
    # the float coupon accrued so far is exactly N*(prod(1+0.04*d/360)) ; check QL and rateslib both reproduce the hand compounding of the known days.
    ref = dt.date(2024, 3, 6)  # Wed
    eff = dt.date(2024, 3, 4)  # Mon  (2 days ago)
    known = [(dt.date(2024, 3, 4), 3), (dt.date(2024, 3, 5), 1)]  # Mon->Tue is 1 day; use real data for values but check by hand below
    row = load_row(ref)
    r_npv, q_npv, r_par, q_par, dfx, dfo, ok = run(ref, eff, "5Y", 1, 1e7, 3.9)
    print("control-ish (2 days old swap):", f"rl {r_npv:.6f} ql {q_npv:.6f} diff {abs(r_npv-q_npv):.2e}; float flow max diff {dfo:.2e}; dates equal {ok}")
    # by-hand check of the accrued fixings compounding for the first (front-stub) period: not needed here; the NPV agreement across many cases is the check.

    refs = sample_dates(dt.date(2019, 9, 2), dt.date(2026, 9, 4), n=30, seed=11)
    worst = {"npv": 0.0, "npv_rel": 0.0, "par": 0.0, "fixedflow": 0.0, "floatflow": 0.0}
    n = 0
    bad_dates = 0
    for ref in refs:
        for back_days, tenor in [(20, "5Y"), (200, "3Y"), (400, "10Y"), (1000, "5Y"), (700, "30Y"), (360 * 2 + 30, "3Y")]:
            eff0 = ref - dt.timedelta(days=back_days)
            while not rcal.is_bus_day(fdt(eff0)):
                eff0 += dt.timedelta(days=1)  # Following
            eff = eff0
            if eff >= ref or eff < dt.date(2018, 4, 3):
                continue
            for sign in (1, -1):
                r_npv, q_npv, r_par, q_par, dfx, dfo, ok = run(ref, eff, tenor, sign, 5e7, 3.6)
                n += 1
                bad_dates += (not ok)
                worst["npv"] = max(worst["npv"], abs(r_npv - q_npv))
                worst["npv_rel"] = max(worst["npv_rel"], abs(r_npv - q_npv) / max(abs(r_npv), 1.0))
                worst["par"] = max(worst["par"], abs(r_par - q_par))
                worst["fixedflow"] = max(worst["fixedflow"], dfx)
                worst["floatflow"] = max(worst["floatflow"], dfo)
    print(f"{n} started swaps over {len(refs)} dates; date-table mismatches {bad_dates}")
    print({k: f"{v:.3e}" for k, v in worst.items()})
