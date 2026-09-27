import math

import numpy as np
import pandas as pd
import pytest

from pricebt.results import StatsConfig, StatsError, annualisation, compute_stats
from pricebt.results import stats as S
from test_results_common import TZ, synth_result

pytestmark = pytest.mark.core

SPY = 365.25 * 5.0 / 7.0


def stats(inc, K=0.0, **cfg):
    return synth_result(inc, K).summary_stats(**cfg)


def test_pnl_basis_known_answers_hand_computed():
    # daily P&L [10, -5, 10, -5], A = 252
    s = stats([10, -5, 10, -5], annualisation=252.0)
    assert s["basis"] == "pnl" and s["n_periods"] == 4
    assert s["mean"] == pytest.approx(2.5)
    assert s["std"] == pytest.approx(math.sqrt(75.0))  # deviations 7.5, -7.5, 7.5, -7.5 -> 225/3
    assert s["sharpe"] == pytest.approx(2.5 / math.sqrt(75.0) * math.sqrt(252.0))  # 4.5826
    assert s["sortino"] == pytest.approx(2.5 / math.sqrt(12.5) * math.sqrt(252.0))  # DD^2 = (25 + 25) / 4
    assert s["ann_return"] == pytest.approx(2.5 * 252.0)
    assert s["ann_vol"] == pytest.approx(math.sqrt(75.0) * math.sqrt(252.0))
    assert s["max_drawdown"] == -5.0  # path 0,10,5,15,10: peak 10 -> 5, peak 15 -> 10
    assert s["max_dd_duration_points"] == 2 and s["max_dd_duration_days"] == 2
    assert s["calmar"] == pytest.approx(2.5 * 252.0 / 5.0)  # 126
    assert s["hit_rate"] == 0.5 and s["pct_positive"] == 50.0
    assert s["profit_factor"] == pytest.approx(20.0 / 10.0)
    assert s["best"] == 10.0 and s["worst"] == -5.0
    assert s["total_pnl"] == 10.0 and s["peak_pnl"] == 15.0 and s["current_drawdown"] == -5.0


def test_nav_basis_known_answers_hand_computed():
    K = 1000.0
    rets = [0.01, 0.03, -0.01, 0.02]
    levels, lv = [], K
    for r in rets:
        lv *= 1.0 + r
        levels.append(lv)
    inc = np.diff([K, *levels])
    res = synth_result(inc, K)
    s = res.summary_stats(annualisation=252.0)
    assert s["basis"] == "nav"
    mu = sum(rets) / 4
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / 3)
    assert s["mean"] == pytest.approx(mu, abs=1e-12) and s["std"] == pytest.approx(sd, abs=1e-12)
    assert sd == pytest.approx(0.017078, abs=1e-6)
    assert s["sharpe"] == pytest.approx(mu / sd * math.sqrt(252.0), rel=1e-9)
    assert s["max_drawdown"] == pytest.approx(-0.01, abs=1e-12)  # 1040.3 -> 1029.897
    years = (res.equity.index[-1] - res.equity.index[0]).total_seconds() / (365.25 * 86400)
    assert s["cagr"] == pytest.approx((levels[-1] / K) ** (1.0 / years) - 1.0, rel=1e-9)
    assert s["calmar"] == pytest.approx(s["cagr"] / 0.01, rel=1e-9)
    assert s["total_pnl"] == pytest.approx(levels[-1] - K)


def test_nav_with_rf_and_mar():
    K = 100.0
    inc = np.diff([K, 101.0, 103.0, 102.0, 105.0])
    s = synth_result(inc, K).summary_stats(annualisation=4.0, rf=0.04, mar=0.02)
    r = np.array([101 / 100 - 1, 103 / 101 - 1, 102 / 103 - 1, 105 / 102 - 1])
    mu, sd = r.mean(), r.std(ddof=1)
    assert s["sharpe"] == pytest.approx((mu - 0.04 / 4) / sd * 2.0)
    dd = math.sqrt(np.mean(np.minimum(r - 0.02 / 4, 0.0) ** 2))
    assert s["sortino"] == pytest.approx((mu - 0.02 / 4) / dd * 2.0)


def test_var_cvar_known_answer():
    r = [(i - 50) / 1000 for i in range(1, 101)]  # -0.049 .. 0.05
    s = stats(r, annualisation=252.0)
    # np.quantile(r, .05): position 4.95 between -0.045 and -0.044 -> -0.04405
    assert s["var"] == pytest.approx(0.04405, abs=1e-12)
    assert s["cvar"] == pytest.approx(0.047, abs=1e-12)  # mean of -0.049 .. -0.045
    s99 = stats(r, annualisation=252.0, var_level=0.99)
    assert s99["var"] == pytest.approx(0.04801, abs=1e-12)  # position 0.99 between -0.049 and -0.048
    assert s99["cvar"] == pytest.approx(0.049, abs=1e-12)


def test_profit_factor_cases():
    assert S.profit_factor(np.array([5, -2, 3, -1, 4])) == pytest.approx(4.0)
    assert S.profit_factor(np.array([1.0, 2.0])) == math.inf
    assert math.isnan(S.profit_factor(np.array([])))
    assert math.isnan(S.profit_factor(np.array([0.0, 0.0])))
    assert S.profit_factor(np.array([-1.0, -3.0])) == 0.0


def test_degenerate_series_gives_nan_ratios_not_inf():
    for inc in ([1e-4] * 250, [0.0] * 10, [0.25] * 10):
        s = stats(inc, annualisation=252.0)
        assert math.isnan(s["sharpe"]) and math.isnan(s["sortino"]) and math.isnan(s["calmar"])
    rng = np.random.default_rng(0)
    s = stats(rng.normal(1e-4, 5e-5, 250), annualisation=252.0)
    assert np.isfinite(s["sharpe"])


def test_nav_cash_only_constant_accrual_is_degenerate():
    K = 1e6
    levels = K * (1 + 1e-4) ** np.arange(1, 251)
    s = synth_result(np.diff([K, *levels]), K).summary_stats(annualisation=252.0)
    assert math.isnan(s["sharpe"])
    assert s["ann_return"] == pytest.approx(1e-4 * 252, rel=1e-6)


def test_drawdown_duration_unrecovered_and_days():
    # levels 0,10,5,3,4 -> unrecovered episode from peak idx 1 through the last obs idx 4: 3 points, 3 days
    s = stats([10, -5, -2, 1], annualisation=252.0)
    assert s["max_drawdown"] == -7.0 and s["max_dd_duration_points"] == 3 and s["max_dd_duration_days"] == 3
    flat_up = stats([1, 1, 1], annualisation=252.0)
    assert flat_up["max_drawdown"] == 0.0 and flat_up["max_dd_duration_points"] == 0
    assert math.isnan(flat_up["calmar"])


def test_first_period_includes_base_so_entry_cost_is_a_return():
    s = stats([-2.0, 1.0, 1.0], annualisation=252.0)
    assert s["worst"] == -2.0 and s["n_periods"] == 3 and s["max_drawdown"] == -2.0


def test_nonpositive_nav_returns_are_dropped_not_divided():
    s = synth_result(np.diff([100.0, 50.0, -10.0, 20.0]), 100.0).summary_stats(annualisation=252.0)
    assert s["n_periods"] == 2  # 100->50, 50->-10 valid; -10->20 has non-positive base


def test_session_aggregation_of_intraday_bars():
    idx = pd.DatetimeIndex(
        [f"2024-01-0{d} {h}:00" for d in (2, 3, 4) for h in (9, 12, 16)], tz=TZ, name="ts")
    inc = [1, 2, 3, -4, 0, 1, 5, 5, 5]
    r = synth_result(inc, index=idx)
    ses = r.summary_stats(freq="session", annualisation=252.0)
    bar = r.summary_stats(freq="bar", annualisation=252.0)
    assert ses["n_periods"] == 3 and bar["n_periods"] == 9
    assert ses["mean"] == pytest.approx((6 + -3 + 15) / 3)  # session P&L 6, -3, 15
    assert ses["worst"] == -3.0 and bar["worst"] == -4.0
    assert bar["max_drawdown"] == -4.0 and ses["max_drawdown"] == -3.0
    assert list(r.returns().values) == [6.0, -3.0, 15.0]


def test_annualisation_daily_weekday_grid_is_exact():
    ts = pd.date_range("2024-01-01", "2024-12-31", freq="B", tz=TZ) + pd.Timedelta(hours=17)
    a, src = annualisation(ts, "session")
    assert src == "grid" and a == pytest.approx(SPY, rel=1e-12)
    assert annualisation(ts, "bar")[0] == pytest.approx(SPY, rel=1e-12)


def test_annualisation_short_span_has_no_fence_post_bias():
    ts = pd.date_range("2024-01-01", periods=20, freq="B", tz=TZ)
    assert annualisation(ts, "session")[0] == pytest.approx(SPY, rel=1e-12)  # a naive (n-1)/years gives ~277


def test_annualisation_weekly_and_monthly():
    fridays = pd.date_range("2024-01-05", periods=52, freq="W-FRI", tz=TZ)
    assert annualisation(fridays, "session")[0] == pytest.approx(SPY / 5.0, rel=1e-12)  # 52.18
    month_end = pd.date_range("2023-01-31", periods=24, freq="BME", tz=TZ)
    assert annualisation(month_end, "session")[0] == pytest.approx(12.0, rel=0.02)


def test_annualisation_intraday_bars_count_points_per_business_day():
    days = pd.date_range("2024-03-04", periods=5, freq="B", tz=TZ)
    ts = pd.DatetimeIndex([d + pd.Timedelta(hours=8, minutes=m) for d in days for m in range(541)])
    assert annualisation(ts, "bar")[0] == pytest.approx(541 * SPY, rel=1e-12)
    # 1 year / median step would be 525,960 per year: wrong by an order of magnitude on gapped intraday grids
    session_ts = pd.DatetimeIndex([d + pd.Timedelta(hours=16) for d in days])
    assert annualisation(session_ts, "session")[0] == pytest.approx(SPY, rel=1e-12)


def test_annualisation_seven_day_grid_and_override():
    ts = pd.date_range("2024-01-01", periods=100, freq="D", tz=TZ)
    assert annualisation(ts, "session")[0] == pytest.approx(365.25, rel=1e-12)
    assert annualisation(ts, "session", 12.0) == (12.0, "override")


def test_annualisation_scales_ann_return_and_vol():
    inc = [1, -1, 2, -2, 3]
    a = stats(inc, annualisation=100.0)
    b = stats(inc, annualisation=400.0)
    assert b["ann_return"] == pytest.approx(4 * a["ann_return"])
    assert b["ann_vol"] == pytest.approx(2 * a["ann_vol"])
    assert b["sharpe"] == pytest.approx(2 * a["sharpe"])


def test_config_validation():
    with pytest.raises(StatsError):
        StatsConfig(freq="week")
    with pytest.raises(StatsError):
        StatsConfig(basis="x")
    with pytest.raises(StatsError):
        StatsConfig(var_level=1.0)
    with pytest.raises(StatsError):
        StatsConfig(annualisation=0.0)
    with pytest.raises(StatsError):
        stats([1, 2, 3], K=0.0, basis="nav")
    with pytest.raises(StatsError):
        stats([1, 2, 3], K=0.0, rf=0.01)  # rf undefined on the currency basis


def test_rolling_sharpe_matches_manual_window():
    rng = np.random.default_rng(1)
    inc = rng.normal(0.5, 2.0, 30)
    res = synth_result(inc)
    rs = res.rolling_sharpe(window=10, annualisation=252.0)
    assert rs.iloc[:9].isna().all()
    w = inc[5:15]
    assert rs.iloc[14] == pytest.approx(w.mean() / w.std(ddof=1) * math.sqrt(252.0))


def test_time_in_market_time_weighted():
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-05", "2024-01-06"], tz=TZ)
    n = pd.Series([0, 1, 1, 0])
    # intervals of 1, 2, 1 days; held over the intervals that start after a row with n>0: (1d: n=0), (2d: n=1), (1d: n=1)
    assert S.time_in_market(idx, n) == pytest.approx(3.0 / 4.0)


def test_drawdown_series_matches_path():
    res = synth_result([10, -5, 10, -5])
    assert list(res.drawdown().values) == [0.0, -5.0, 0.0, -5.0]
    nav = synth_result(np.diff([100.0, 110.0, 99.0, 99.0]), 100.0)
    assert list(nav.drawdown().values) == pytest.approx([0.0, -0.1, -0.1])


def test_compute_stats_rejects_empty():
    with pytest.raises(StatsError):
        compute_stats(pd.Series([], dtype=float, index=pd.DatetimeIndex([], tz=TZ)), 0.0)
