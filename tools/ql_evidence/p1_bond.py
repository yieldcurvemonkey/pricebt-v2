"""Phase 1 item 6: UST fixed-rate bonds, QuantLib vs rateslib us_gb_tsy (ex_div=0, settle = reference date), on real reference data."""
import sys

sys.path.insert(0, "tools/ql_evidence")
from qlbond import *  # noqa

REF = pd.read_parquet(FIX / "ust" / "reference_fiscaldata.parquet")
REF["issue_date"] = pd.to_datetime(REF["issue_date"]).dt.date
REF["maturity_date"] = pd.to_datetime(REF["maturity_date"]).dt.date
REF = REF[REF["cpn"].notna()].reset_index(drop=True)


def rl_bond(issue, maturity, cpn, notional=-1e6):
    return rl.FixedRateBond(effective=fdt(issue), termination=fdt(maturity), spec="us_gb_tsy", fixed_rate=cpn, notional=notional, ex_div=0)


def coupon_dates(b):
    return [pd_(c.date()) for c in b.cashflows() if not ql.as_fixed_rate_coupon(c) is None][:0]


def ql_flows(b):
    out = []
    for c in b.cashflows():
        out.append((pd_(c.date()), c.amount()))
    return out


def run(sample_n=60, seed=7):
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(REF))
    rows_c, rows_d = [], []
    n_bonds = 0
    stub_bonds = eom_bonds = 0
    for i in idx:
        r = REF.iloc[i]
        issue, mat, cpn = r["issue_date"], r["maturity_date"], float(r["cpn"])
        if issue < dt.date(2005, 1, 1) or mat > dt.date(2060, 1, 1) or cpn <= 0.0:
            continue
        try:
            rb = rl_bond(issue, mat, cpn)
        except Exception as e:  # rateslib refuses some odd schedules
            print("rateslib could not build", r["cusip"], issue, mat, cpn, str(e)[:60])
            continue
        qb, qdc = make_bond(issue, mat, cpn)
        n_bonds += 1
        stub_bonds += issue.day != mat.day
        eom_bonds += (mat + dt.timedelta(days=1)).day == 1
        # cash flows
        rf = [(p.settlement_params.payment.date(), abs(float(p.cashflow())) / 1e6 * 100) for p in rb.leg1.periods]
        qf = ql_flows(qb)
        # rateslib periods exclude the final principal? include Cashflow (redemption) periods in leg1.periods (type Cashflow)
        rows_c.append(dict(cusip=r["cusip"], n_rl=len(rf), n_ql=len(qf), same_dates=[a[0] for a in rf] == [b[0] for b in qf], maxdiff=max((abs(a[1] - b[1]) for a, b in zip(rf, qf)), default=0.0) if len(rf) == len(qf) else np.nan))
        # settlement dates: a few generic dates inside the life + every coupon date, day before, day after (first 3 coupons)
        cds = [d for d, _ in qf][:-1]
        life_start = max(issue + dt.timedelta(days=1), dt.date(2010, 1, 5))
        generic = [life_start + dt.timedelta(days=int(k)) for k in rng.integers(0, max(1, (mat - life_start).days - 1), 5)]
        special = []
        for d in cds[2:6]:
            special += [d - dt.timedelta(days=1), d, d + dt.timedelta(days=1)]
        for s in sorted(set(generic + special)):
            if not (issue < s < mat):
                continue
            for y in (0.5, 2.0, 4.3, 7.5):
                try:
                    r_dirty = float(rb.price(y, fdt(s), dirty=True))
                    r_clean = float(rb.price(y, fdt(s), dirty=False))
                    r_acc = float(rb.accrued(fdt(s)))
                except Exception as e:
                    continue
                with ql_settings(s):
                    q_dirty = dirty_from_yield(qb, qdc, y, s)
                    q_clean = clean_from_yield(qb, qdc, y, s)
                    q_acc = qb.accruedAmount(qd(s))
                    y_back = yield_from_clean(qb, qdc, q_clean, s)
                rows_d.append(dict(cusip=r["cusip"], settle=s, y=y, on_coupon=s in cds, r_dirty=r_dirty, q_dirty=q_dirty, r_clean=r_clean, q_clean=q_clean, r_acc=r_acc, q_acc=q_acc, y_back=y_back,
                                   stub=issue.day != mat.day, eom=(mat + dt.timedelta(days=1)).day == 1))
        if n_bonds >= sample_n:
            break
    return pd.DataFrame(rows_c), pd.DataFrame(rows_d), stub_bonds, eom_bonds


class ql_settings:
    def __init__(self, d):
        self.d = d

    def __enter__(self):
        s = ql.Settings.instance()
        self.old = s.evaluationDate
        s.evaluationDate = qd(self.d)

    def __exit__(self, *a):
        try:
            ql.Settings.instance().evaluationDate = self.old
        except RuntimeError:
            pass


if __name__ == "__main__":
    # ---- control: a 10Y bond issued and settled on a coupon date with y == coupon prices at par (dirty = clean = 100) in QL, and rateslib agrees
    b, dc = make_bond(dt.date(2024, 11, 15), dt.date(2034, 11, 15), 4.25)
    rb = rl_bond(dt.date(2024, 11, 15), dt.date(2034, 11, 15), 4.25)
    with ql_settings(dt.date(2025, 5, 15)):
        assert abs(dirty_from_yield(b, dc, 4.25, dt.date(2025, 5, 15)) - 100.0) < 1e-9, dirty_from_yield(b, dc, 4.25, dt.date(2025, 5, 15))
    assert abs(float(rb.price(4.25, dt.datetime(2025, 5, 15), dirty=True)) - 100.0) < 1e-9
    print("control ok: par bond at y == coupon on a coupon date prices at 100 in both libraries")
    dc_, dd, stubs, eoms = run(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
    print(f"bonds {len(dc_)} (off-cycle issue {stubs}, month-end maturity {eoms}); cash flow tables: same dates {dc_.same_dates.mean():.3f}, max abs flow diff per 100 {np.nanmax(dc_.maxdiff):.2e}; length mismatches {(dc_.n_rl != dc_.n_ql).sum()}")
    __import__("os").makedirs("results_new/ql_evidence", exist_ok=True)
    dd.to_csv("results_new/ql_evidence/p1_bond_rows.csv", index=False)
    print("price points", len(dd), " on coupon dates:", int(dd.on_coupon.sum()))
    for name, g in (("off coupon dates", dd[~dd.on_coupon]), ("on coupon dates", dd[dd.on_coupon])):
        print(f"[{name}] n={len(g)} max|dirty diff| {np.abs(g.q_dirty-g.r_dirty).max():.3e}  max|clean diff| {np.abs(g.q_clean-g.r_clean).max():.3e}  max|accrued diff| {np.abs(g.q_acc-g.r_acc).max():.3e}  "
              f"max|y from clean - y| {np.abs(g.y_back-g.y).max():.3e}")
