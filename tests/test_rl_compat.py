import datetime as dt

import pandas as pd
import pytest

pytest.importorskip("rateslib")
pytestmark = pytest.mark.adapter_rateslib

from test_rl_common import TZ, eod, par_curve, rl, seeded_fixings  # noqa: E402

from pricebt.contrib.rateslib import _compat as C  # noqa: E402


def test_import_side_effects_are_contained():
    import os

    assert os.environ["MPLBACKEND"]  # set (or already set by the user) before rateslib imported


def test_to_dt_and_to_date_accept_all_date_likes():
    d = dt.date(2025, 1, 2)
    assert C.to_dt(d) == dt.datetime(2025, 1, 2)
    assert C.to_dt(pd.Timestamp("2025-01-02 17:00", tz=TZ)) == dt.datetime(2025, 1, 2)
    assert C.to_dt(dt.datetime(2025, 1, 2, 13, 5)) == dt.datetime(2025, 1, 2)
    assert C.to_date(dt.datetime(2025, 1, 2, 9)) == d
    with pytest.raises(TypeError):
        C.to_dt("2025-01-02")


def test_finite_strips_ad_and_rejects_nan():
    assert C.finite(rl.Dual(3.5, ["x"], [1.0])) == 3.5
    with pytest.raises(ValueError):
        C.finite(float("nan"))
    with pytest.raises(ValueError):
        C.finite(float("inf"))


def test_nyc_calendar_matches_rateslib_business_days():
    cal = C.nyc_calendar()
    days = pd.date_range("2024-01-01", "2024-12-31")
    rl_cal = C.calendar()
    assert all(cal.is_business_day(d.date()) == rl_cal.is_bus_day(C.to_dt(d)) for d in days)
    assert not cal.is_business_day(dt.date(2024, 7, 4)) and cal.is_business_day(dt.date(2024, 7, 5))


# ------------------------------------------------------------------ the fixings guard: each failure mode, and a clean pass
def _good():
    fx = seeded_fixings()
    return fx[fx.index < pd.Timestamp("2025-01-16")]


def test_guard_passes_clean_series():
    C.check_fixings(_good(), dt.datetime(2024, 1, 8), dt.datetime(2025, 1, 16))


def test_guard_catches_missing_last_business_day():
    s = _good()
    with pytest.raises(ValueError, match="missing"):
        C.check_fixings(s.iloc[:-1], dt.datetime(2024, 1, 8), dt.datetime(2025, 1, 16))


def test_guard_catches_mid_series_gap_and_time_of_day_and_lookahead():
    s = _good()
    with pytest.raises(ValueError, match="missing"):
        C.check_fixings(s.drop(pd.Timestamp("2024-06-03")), dt.datetime(2024, 1, 8), dt.datetime(2025, 1, 16))
    ts = s.copy()
    ts.index = ts.index + pd.Timedelta(hours=8)
    with pytest.raises(ValueError, match="midnight"):
        C.check_fixings(ts, dt.datetime(2024, 1, 8), dt.datetime(2025, 1, 16))
    with pytest.raises(ValueError, match="strictly before"):
        C.check_fixings(seeded_fixings(), dt.datetime(2024, 1, 8), dt.datetime(2025, 1, 16))
    on_asof = pd.concat([s, pd.Series([4.3], index=pd.DatetimeIndex(["2025-01-16"]))])
    with pytest.raises(ValueError, match="strictly before"):
        C.check_fixings(on_asof, dt.datetime(2024, 1, 8), dt.datetime(2025, 1, 16))  # a fixing dated ON the as-of date is not public yet


def test_the_guarded_trap_is_real_missing_last_fixings_misprice_silently():
    """Why the guard exists: rateslib itself accepts a store missing the last two fixings and returns a wildly wrong NPV."""
    c = par_curve(dt.datetime(2025, 1, 16), cid="c")
    inst = rl.IRS(effective=dt.datetime(2024, 1, 8), termination="3y", spec="usd_irs", fixed_rate=4.2, notional=50e6, leg2_rate_fixings="PBT_G")
    good = _good()
    C.install_fixings("PBT_G", good)
    inst.reset_fixings()
    v_ok = float(inst.npv(curves=c))
    C.install_fixings("PBT_G", good.iloc[:-2])
    inst.reset_fixings()
    v_bad = float(inst.npv(curves=c))
    C.clear_fixings("PBT_G")
    assert abs(v_bad - v_ok) > 1e6
    with pytest.raises(ValueError):
        C.check_fixings(good.iloc[:-2], dt.datetime(2024, 1, 8), dt.datetime(2025, 1, 16))


def test_install_fixings_is_idempotent_and_replaces():
    C.install_fixings("PBT_T", pd.Series([4.0], index=pd.DatetimeIndex(["2025-01-02"])))
    C.install_fixings("PBT_T", pd.Series([5.0], index=pd.DatetimeIndex(["2025-01-02"])))
    assert C.fixings_key("PBT_T") in rl.fixings.loader.loaded
    C.clear_fixings("PBT_T")
    assert C.fixings_key("PBT_T") not in rl.fixings.loader.loaded
    C.clear_fixings("PBT_T")


# ------------------------------------------------------------------ solver output and curve conversion
def test_solver_stdout_is_swallowed(capsys):
    par_curve(dt.datetime(2025, 1, 2))
    assert "SUCCESS" not in capsys.readouterr().out


def test_float_curve_roundtrip_and_ad_curve_variable_names():
    c = par_curve(dt.datetime(2025, 1, 2))
    f = C.to_float_curve(c, "other")
    d = C.curve_dates(c)[3]
    assert float(f[d]) == pytest.approx(float(c[d]), abs=1e-15) and f.id == "other"
    ad = C.ad_curve(c, {k: float(v) for k, v in c.nodes.nodes.items()}, "zz", 2)
    v = rl.IRS(effective=dt.datetime(2025, 2, 3), termination="2y", spec="usd_irs", fixed_rate=4.0, notional=1e6).npv(curves=ad)
    assert {n for n in v.vars} <= {f"zz{i}" for i in range(c.nodes.n)}


def test_importing_pricebt_or_contrib_does_not_import_rateslib():
    import os
    import subprocess
    import sys
    from pathlib import Path

    import pricebt

    env = {**os.environ, "PYTHONPATH": str(Path(pricebt.__file__).resolve().parents[1])}
    code = "import sys, pricebt, pricebt.contrib; assert 'rateslib' not in sys.modules, 'rateslib leaked into core'"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    # the adapter imports the library and ships its kits and stack ONLY as objects reached through a dotted path (no registered short names, no text sniffing)
    code = ("import sys, pricebt.contrib.rateslib as m; assert 'rateslib' in sys.modules; "
            "assert m.swap.asset_class == 'swap' and m.bond.asset_class == 'bond' and m.STACK.name == 'rateslib' and callable(m.wrap); "
            "from pricebt.registry import resolve_dotted; "
            "assert resolve_dotted('pricebt.contrib.rateslib:swap') is m.swap and resolve_dotted('pricebt.contrib.rateslib:STACK') is m.STACK")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
