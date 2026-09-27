"""`pricebt.testing.synthetic.SyntheticMarket`: the library-free synthetic market (Nelson-Siegel factor walks, seeded fixings, a yield-quoted bond panel) emitting
plain snapshots. Known answers are hand-derived (flat curves, closed-form yields); parity with the old class was checked once against the old tree (docs/design/11-refactor-changelog.md, section 7)."""
import datetime as dt
import math
import os
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pricebt.errors import ConfigError, MarketDataUnavailable
from pricebt.market import MarketData
from pricebt.pricer import MarketDataProvider
from pricebt.snapshot import CalendarData, CurveSnapshot, SnapshotPricer
from pricebt.testing.synthetic import DEFAULT_BONDS, SyntheticMarket, ns_zero
from pricebt.timeutil import Calendar, Clock
from support.known import NYC_HOLIDAYS_2020_2026

pytestmark = pytest.mark.core
D = dt.date
NY = "America/New_York"
HOL = Calendar([D(2024, 1, 1), D(2024, 1, 15), D(2024, 2, 19), D(2024, 5, 27), D(2024, 6, 19), D(2024, 7, 4), D(2024, 9, 2), D(2024, 11, 28), D(2024, 12, 25)], name="unit")
SRC = Path(__file__).resolve().parents[1] / "src"


def eod(day: str, hour: int = 17, minute: int = 0) -> pd.Timestamp:
    return pd.Timestamp(f"{day} {hour:02d}:{minute:02d}", tz=NY)


@pytest.fixture(scope="module")
def mdp():
    return SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7)


def curve_of(p: SnapshotPricer) -> CurveSnapshot:
    return p.snapshot.curves["sofr"]


def zero_pct(c: CurveSnapshot, k: int) -> float:
    """Continuously compounded zero (percent) at node k, ACT/365, from its discount factor."""
    return -math.log(c.values[k]) / ((c.node_dates[k] - c.reference_date).days / 365.0) * 100.0


def test_nelson_siegel_zero_curve_known_answers():
    z = ns_zero(np.array([1e-6, 2.0, 1e6]), 4.0, -1.0, 0.5, 2.0)
    assert z[0] == pytest.approx(4.0 - 1.0 + 0.0, abs=1e-4)  # T->0: b0 + b1
    assert z[2] == pytest.approx(4.0, abs=1e-3)  # T->inf: b0
    x = 1.0  # T = lam
    f = 1 - np.exp(-x)
    assert ns_zero(np.array([2.0]), 4.0, -1.0, 0.5, 2.0)[0] == pytest.approx(4.0 - 1.0 * f + 0.5 * (f - np.exp(-x)), abs=1e-12)


def test_pinned_values_of_the_default_market_are_those_of_the_original_implementation():
    """Numbers generated ONCE by the class this one replaced (default window 2024-01-02..2025-12-31, seed 7, the swap calendar of the fixtures) and by the
    survey of it: a change of the RNG order, the walk, the Nelson-Siegel formula, the node grid or the fixings shows here without any library."""
    cal = Calendar([D.fromisoformat(h) for h in NYC_HOLIDAYS_2020_2026], name="cal")
    m = SyntheticMarket(calendar=cal)
    assert len(m._days) == 1247 and m._days[0] == D(2021, 1, 4) and m._days[-1] == D(2025, 12, 31)
    assert list(m._f[m._pos[D(2024, 3, 1)]]) == pytest.approx([4.246536063930576, -0.4189745579314688, 0.18320157892759845], abs=1e-12)
    assert list(m._f[-1]) == pytest.approx([3.685687851783328, -0.3329074562465984, 0.7346640091656936], abs=1e-12)
    assert m.fixings.loc["2024-03-01"] == pytest.approx(3.8319034148806765, abs=1e-12) and list(m.fixings.iloc[:3]) == pytest.approx([3.694461138787079, 3.708880246834342, 3.666162050811106], abs=1e-12)
    c = curve_of(m.get_pricer(eod("2024-03-01")))
    assert (c.node_dates[1], c.node_dates[10], c.node_dates[-1]) == (D(2024, 3, 8), D(2026, 3, 1), D(2054, 3, 2))
    assert (c.values[1], c.values[10], c.values[-1]) == pytest.approx((0.9992656639589459, 0.9225607418196612, 0.2807831412928759), abs=1e-12)
    qs = m.get_pricer(eod("2024-06-12")).snapshot.quotes.quotes
    want = {"SYN002027": 4.074571321488157, "SYN012029": 4.1615470039678915, "SYN032032": 4.131955400915415, "SYN042050": 4.209359708849725}
    assert {k: v.ytm for k, v in qs.items()} == pytest.approx(want, abs=1e-12)


def test_is_a_market_data_provider_emitting_snapshot_pricers(mdp):
    assert isinstance(mdp, MarketDataProvider)
    p = mdp.get_pricer(eod("2024-06-12"))
    assert isinstance(p, SnapshotPricer) and p.ts == eod("2024-06-12") and p.reference_date == D(2024, 6, 12) and p.digest == p.snapshot.digest()
    assert list(p.snapshot.curves) == ["sofr"] and list(p.snapshot.fixings) == ["USD-SOFR-1D"] and list(p.snapshot.calendars) == ["usd_fed"]
    assert p.snapshot.provenance == {"source": "synthetic", "seed": 7} and p.describe()["source"] == "synthetic"


def test_deterministic_for_a_seed_and_different_across_seeds(mdp):
    ts = eod("2024-06-12")
    a, b = mdp.get_pricer(ts), SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7).get_pricer(ts)
    c = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=8).get_pricer(ts)
    assert a.digest == b.digest and a.snapshot == b.snapshot and curve_of(a).values == curve_of(b).values
    assert a.digest != c.digest and curve_of(a).values != curve_of(c).values and a.snapshot.fixings != c.snapshot.fixings
    assert mdp.get_pricer(ts).digest == a.digest, "same instance, same answer (no hidden RNG state)"
    assert mdp.get_pricer(eod("2024-06-13")).digest != a.digest


def test_a_flat_market_has_hand_derived_discount_factors_and_yields():
    """No random walk (daily_bp 0), level 4%, no slope or curvature: the zero curve is flat at 4% continuously compounded."""
    m = SyntheticMarket("2024-06-03", "2024-06-28", seed=1, level=4.0, slope=0.0, curvature=0.0, daily_bp=(0.0, 0.0, 0.0), bond_spread_bp=6.0, history_years=0.1)
    p = m.get_pricer(eod("2024-06-12"))
    c = curve_of(p)
    assert len(c.node_dates) == 21 and c.node_dates[0] == D(2024, 6, 12) and c.values[0] == 1.0
    for k, y in enumerate((0.0, 1 / 52, 2 / 52, 1 / 12, 2 / 12, 3 / 12, 6 / 12, 9 / 12, 1, 1.5, 2, 3, 4, 5, 7, 10, 12, 15, 20, 25, 30)):
        days = int(round(y * 365.25))
        assert c.node_dates[k] == D(2024, 6, 12) + dt.timedelta(days=days), "nodes sit at round(years * 365.25) calendar days, unadjusted"
        assert c.values[k] == pytest.approx(math.exp(-0.04 * days / 365.0), abs=1e-15)
    flat_ytm = 200.0 * (math.exp(0.02) - 1.0)  # the semi-annual yield equivalent to a 4% continuous zero
    qs = p.snapshot.quotes
    spreads = {c_: bp for c_, bp in zip(m.refs, (-0.06, 0.0, 0.06, -0.06, 0.0))}
    assert sorted(qs.quotes) == [c_ for c_ in m.refs if c_ != "SYN022034"]
    for cusip, q in qs.quotes.items():
        assert q.ytm == pytest.approx(flat_ytm + spreads[cusip], abs=1e-12) and q.clean is None


def test_curve_shape_and_snapshot_conventions(mdp):
    for day in ("2024-01-02", "2024-06-12", "2024-12-31"):
        p = mdp.get_pricer(eod(day))
        c = curve_of(p)
        assert p.reference_date == D.fromisoformat(day) and c.values[0] == 1.0 and all(b < a for a, b in zip(c.values, c.values[1:]))
        assert c.interpolation == "log_linear_df" and c.value_kind == "discount_factor" and c.reference_date == p.reference_date
        assert 0.5 < zero_pct(c, 14) < 9.0, "the 10y zero of the factor walk stays in a plausible range"


def test_factor_random_walk_has_the_configured_daily_volatility():
    m = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=3, daily_bp=(4.0, 2.0, 3.0), history_years=6)
    df = np.diff(m._f, axis=0)
    assert df[:, 0].std() == pytest.approx(0.04, rel=0.1) and df[:, 1].std() == pytest.approx(0.02, rel=0.1)
    assert abs(m._f[:, 0].mean() - 4.0) < 1.5, "mean reversion keeps the level near its anchor"
    assert m._days[0] == D(2018, 1, 2), "history_years=6 before 2024-01-02: int(365.25 * 6) = 2191 days"


def test_the_business_day_calendar_is_data_a_calendar_or_snapshot_calendar_data():
    hol_data = CalendarData("cal", tuple(sorted(HOL.holidays)), "Mon Tue Wed Thu Fri")
    a = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL)
    b = SyntheticMarket("2024-01-02", "2024-12-31", calendar=hol_data)
    w = SyntheticMarket("2024-01-02", "2024-12-31")
    assert a._days == b._days == HOL.business_days(a._days[0], a._days[-1]) and D(2024, 7, 4) not in a._days and D(2024, 7, 4) in w._days
    assert w._days == Calendar.weekends_only().business_days(w._days[0], w._days[-1])
    assert list(b.get_pricer(eod("2024-06-12")).snapshot.calendars) == ["cal"] and b.get_pricer(eod("2024-06-12")).snapshot.calendars["cal"] == hol_data
    assert a.get_pricer(eod("2024-06-12")).snapshot.calendars["usd_fed"].holidays == tuple(sorted(HOL.holidays))
    named = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, calendar_name="mine", curve_name="ois", fixings_name="fx")
    s = named.get_pricer(eod("2024-06-12")).snapshot
    assert list(s.calendars) == ["mine"] and list(s.curves) == ["ois"] and list(s.fixings) == ["fx"]
    assert a._days[0] > D(2020, 12, 1) and a._days[0] == HOL.following(D(2024, 1, 2) - dt.timedelta(days=int(365.25 * 3.0))), "history_years of earlier business days feed the fixings"


def test_non_business_days_outside_window_and_naive_stamps_fail_fast(mdp):
    with pytest.raises(MarketDataUnavailable, match="business day"):
        mdp.get_pricer(eod("2024-07-04"))  # holiday
    with pytest.raises(MarketDataUnavailable, match="business day"):
        mdp.get_pricer(eod("2024-06-15"))  # Saturday
    with pytest.raises(MarketDataUnavailable, match="outside"):
        mdp.get_pricer(eod("2025-01-02"))
    with pytest.raises(MarketDataUnavailable, match="outside"):
        mdp.get_pricer(eod("2023-12-29"))
    with pytest.raises(ConfigError, match="tz-aware"):
        mdp.get_pricer(pd.Timestamp("2024-06-12 17:00"))
    with pytest.raises(ConfigError, match="pre_publish"):
        SyntheticMarket(pre_publish="ffill")


def test_fixings_are_contiguous_percent_and_publication_filtered(mdp):
    full = mdp.fixings
    assert 0.0 < full.min() and 0.5 < full.loc["2024-06-11"] < 12.0 and full.index.tz is None
    days = [t.date() for t in full.index]
    assert days == HOL.business_days(days[0], days[-1]), "one fixing on every business day of the calendar"
    late, early = mdp.get_pricer(eod("2024-06-12", 17)), mdp.get_pricer(eod("2024-06-12", 6))
    fl, fe = late.snapshot.fixings["USD-SOFR-1D"], early.snapshot.fixings["USD-SOFR-1D"]
    assert fl.unit == "percent" and fl.dates[-1] == D(2024, 6, 11) and fl.values[-1] == full.loc["2024-06-11"] and fl.proxied == ()
    assert fe.dates == fl.dates and fe.values[-1] == full.loc["2024-06-10"] and fe.proxied == (D(2024, 6, 11),), "before 08:00 the newest fixing is proxied by the last published one"
    assert fl.dates[-1] < late.reference_date and fl.dates[0] == days[0]
    at8 = mdp.get_pricer(eod("2024-06-12", 8, 0)).snapshot.fixings["USD-SOFR-1D"]
    assert at8.proxied == () and mdp.get_pricer(eod("2024-06-12", 7, 59)).snapshot.fixings["USD-SOFR-1D"].proxied == (D(2024, 6, 11),)


def test_fixings_never_fall_below_the_floor_of_one_basis_point():
    m = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7, level=0.0, slope=0.0, curvature=0.0, daily_bp=(0.0, 0.0, 0.0))
    assert m.fixings.min() == 0.01 and (m.fixings >= 0.01).all() and (m.fixings == 0.01).sum() > 100 and m.fixings.max() > 0.01, "N(0, 0.005) noise around 0 is floored at 0.01 percent"


def test_the_fixings_lag_skips_holidays_and_the_policy_is_configurable():
    m = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7)
    # Wed 2024-06-19 is a holiday: the fixing of Tue 06-18 is public from Thu 06-20 08:00, so on Thu 06-20 the newest required fixing (06-18) is proxied before 08:00
    assert m.get_pricer(eod("2024-06-20", 7, 59)).snapshot.fixings["USD-SOFR-1D"].proxied == (D(2024, 6, 18),)
    assert m.get_pricer(eod("2024-06-20", 8, 0)).snapshot.fixings["USD-SOFR-1D"].proxied == ()
    slow = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7, publish_after_bdays=2, publish_at="09:30")
    assert slow.get_pricer(eod("2024-06-12", 17)).snapshot.fixings["USD-SOFR-1D"].proxied == (D(2024, 6, 11),)
    assert slow.get_pricer(eod("2024-06-13", 9, 29)).snapshot.fixings["USD-SOFR-1D"].proxied == (D(2024, 6, 12),)
    strict = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7, pre_publish="strict")
    with pytest.raises(ValueError, match="not published"):
        strict.get_pricer(eod("2024-06-12", 6))
    assert strict.get_pricer(eod("2024-06-12", 8)).snapshot.fixings["USD-SOFR-1D"].proxied == ()
    first = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7, history_years=0.0).get_pricer(eod("2024-01-02", 6)).snapshot.fixings["USD-SOFR-1D"]
    assert first.dates == () and first.proxied == (), "no history before the first day: nothing to publish, nothing to proxy"


def test_bond_quotes_are_yields_alive_between_issue_and_maturity_and_the_reference_data_is_complete(mdp):
    ts = eod("2024-06-12")
    qs = mdp.get_pricer(ts).snapshot.quotes
    assert sorted(qs.quotes) == ["SYN002027", "SYN012029", "SYN032032", "SYN042050"] and "SYN022034" not in qs.quotes, "the 2034 note is issued 2024-11-15"
    assert sorted(qs.securities) == ["SYN002027", "SYN012029", "SYN022034", "SYN032032", "SYN042050"] and qs.aliases == {} and qs.price_convention == "unspecified"
    s = qs.securities["SYN032032"]
    assert (s.coupon, s.issue_date, s.maturity_date, s.label) == (4.125, D(2022, 8, 15), D(2032, 8, 15), "SYN 4.125 Aug 32")
    for k, (cpn, iss, mat) in enumerate(DEFAULT_BONDS):
        sec = qs.securities[f"SYN{k:02d}{mat.year:04d}"]
        assert (sec.coupon, sec.issue_date, sec.maturity_date) == (cpn, iss, mat)
    y = qs.quotes["SYN032032"].ytm
    assert qs.quotes["SYN032032"].clean is None
    want = mdp.ytm_at(D(2024, 6, 12), (D(2032, 8, 15) - D(2024, 6, 12)).days / 365.25, ts) + mdp._spread["SYN032032"]
    assert y == pytest.approx(want, abs=1e-12) and 2.0 < y < 9.0
    late = mdp.get_pricer(eod("2024-12-02")).snapshot.quotes
    assert "SYN022034" in late.quotes
    edge = mdp.get_pricer(eod("2024-11-15")).snapshot.quotes
    assert "SYN022034" not in edge.quotes, "not quoted ON its issue date"
    gone = SyntheticMarket("2027-11-10", "2027-11-30", calendar=HOL, seed=1, history_years=0.1)
    on_maturity = gone.get_pricer(eod("2027-11-15")).snapshot.quotes.quotes["SYN002027"].ytm
    assert on_maturity == pytest.approx(gone.ytm_at(D(2027, 11, 15), 0.05, eod("2027-11-15")) + gone._spread["SYN002027"], abs=1e-12), "time to maturity is floored at 0.05 years"
    assert "SYN002027" in gone.get_pricer(eod("2027-11-15")).snapshot.quotes.quotes and "SYN002027" not in gone.get_pricer(eod("2027-11-16")).snapshot.quotes.quotes, "quoted through its maturity date"


def test_intraday_noise_is_reproducible_and_scaled():
    m = SyntheticMarket("2024-03-01", "2024-03-29", calendar=HOL, intraday_bp=1.5, seed=5)
    stamps = [pd.Timestamp("2024-03-06 09:30", tz=NY) + pd.Timedelta(minutes=k) for k in range(40)]
    z = [zero_pct(curve_of(m.get_pricer(t)), 12) for t in stamps]
    assert z == [zero_pct(curve_of(m.get_pricer(t)), 12) for t in stamps]
    diffs = np.diff(z)
    assert (diffs != 0).all(), "every minute has its own noise"
    assert diffs.std() > 0 and 0.3 < diffs.std() / (0.015 * np.sqrt(2)) < 3.0, "minute-to-minute zero-rate noise is of the configured 1.5bp order"
    quiet = SyntheticMarket("2024-03-01", "2024-03-29", calendar=HOL, intraday_bp=0.0, seed=5)
    assert curve_of(quiet.get_pricer(stamps[0])).values == curve_of(quiet.get_pricer(stamps[5])).values
    assert m.factors(D(2024, 3, 6), stamps[0])[0] != m.factors(D(2024, 3, 6))[0] and m.factors(D(2024, 3, 6))[0] == m._f[m._pos[D(2024, 3, 6)]][0]


def test_available_timestamps_are_business_day_closes(mdp):
    ts = mdp.available_timestamps(eod("2024-07-01"), eod("2024-07-09"))
    assert [t.date().isoformat() for t in ts] == ["2024-07-01", "2024-07-02", "2024-07-03", "2024-07-05", "2024-07-08", "2024-07-09"]
    assert all(t.hour == 17 for t in ts) and all(t.tz is not None for t in ts)
    assert [t.hour for t in mdp.available_timestamps(eod("2024-07-01", 0), eod("2024-07-02", 23), close=dt.time(15, 30))] == [15, 15]
    assert len(mdp.available_timestamps(eod("2023-01-01"), eod("2023-12-31"))) == 0, "outside the window: only the history feeds fixings"


def test_market_data_facade_accepts_it_and_everything_pickles(mdp):
    clock = Clock()
    md = MarketData({"primary": mdp}, clock)
    clock.advance(eod("2024-06-12"))
    p = md.pricer(eod("2024-06-12"))
    assert p.ts == eod("2024-06-12") and md.n_fetches == 1
    md.pricer(eod("2024-06-12"))
    assert md.n_hits == 1
    q = pickle.loads(pickle.dumps(p))
    assert q.digest == p.digest and q.snapshot == p.snapshot
    assert pickle.loads(pickle.dumps(mdp)).get_pricer(eod("2024-06-12")).digest == p.digest


def test_digests_change_with_every_input():
    base = SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7).get_pricer(eod("2024-06-12")).digest
    for over in ({"level": 4.5}, {"lam": 3.0}, {"slope": -0.2}, {"curvature": 0.4}, {"bond_spread_bp": 8.0}, {"daily_bp": (3.0, 2.0, 4.0)}):
        assert SyntheticMarket("2024-01-02", "2024-12-31", calendar=HOL, seed=7, **over).get_pricer(eod("2024-06-12")).digest != base, over


def test_the_module_needs_no_library_at_import_time():
    """Imported under the import blocker (rateslib and the other banned roots refuse to load): no attempt is even made."""
    code = (
        "import sys; sys.path[:0] = [%r, %r]; from guards import blocker; f = blocker.install(); import pricebt.testing.synthetic as s; "
        "print(len(f.attempts), sorted(m for m in sys.modules if m.split('.')[0] in ('rateslib', 'QuantLib')))"
    ) % (str(SRC), str(SRC.parent / "tests"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, cwd=str(SRC.parent), timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    assert out.stdout.strip() == "0 []", out.stdout
