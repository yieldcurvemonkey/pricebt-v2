"""skills/pricebt-research-methodology/scripts/research_stats.py against known answers taken from the
worked numbers in the reference books (see skills/pricebt-research-methodology/references/formulas.md)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "pricebt-research-methodology" / "scripts"))
import research_stats as rs  # noqa: E402


def test_years_needed_and_t_stat():
    assert rs.years_needed(0.5) == pytest.approx(16.0)       # IR 0.5 needs 16 years for t = 2
    assert rs.years_needed(1.0) == pytest.approx(4.0)
    assert rs.t_stat_from_sharpe(0.5, 16.0) == pytest.approx(2.0)


def test_probability_of_being_up():
    assert rs.prob_positive(0.5, 1 / 12) == pytest.approx(0.56, abs=0.005)   # ~56% in a month
    assert rs.prob_positive(0.5, 5.0) == pytest.approx(0.87, abs=0.005)      # ~87% over five years


def test_multiple_testing():
    assert rs.false_positive_prob(20) == pytest.approx(0.64, abs=0.005)      # 20 worthless variants
    assert rs.bonferroni_threshold(1) == pytest.approx(1.96, abs=0.01)
    assert rs.bonferroni_threshold(20) > rs.bonferroni_threshold(1)


def test_ic_hit_rate_round_trip():
    assert rs.ic_to_hit_rate(0.0577) == pytest.approx(0.52885, abs=1e-5)
    assert rs.hit_rate_to_ic(rs.ic_to_hit_rate(0.1)) == pytest.approx(0.1)


def test_kelly_example():
    f = rs.kelly_fraction(0.1123 - 0.04, 0.1691)                              # index-ETF worked example
    assert f == pytest.approx(2.528, abs=0.002)
    assert rs.levered_growth(0.04, 0.1123 - 0.04, 0.1691, f) == pytest.approx(0.1314, abs=0.0005)


def test_implied_breadth():
    assert rs.implied_breadth(2.24, 0.1) == pytest.approx(501.76, rel=1e-6)   # IC 0.1 x sqrt(500) ~ 2.24


def test_half_life_recovers_a_known_ou_and_is_undefined_for_a_trend():
    rng = np.random.default_rng(0)
    theta = math.log(2) / 10.0                                                 # true half-life 10 steps
    z = [0.0]
    for _ in range(20000):
        z.append(z[-1] - theta * z[-1] + rng.normal(0, 1))
    assert rs.half_life(pd.Series(z)) == pytest.approx(10.0, rel=0.1)
    assert math.isnan(rs.half_life(pd.Series(np.arange(100.0))))             # trending: beta >= 0


def test_max_drawdown_on_a_hand_series():
    cum = pd.Series([0.0, 5.0, 3.0, 8.0, 2.0, 4.0, 9.0])
    out = rs.max_drawdown(cum)
    assert out["max_drawdown"] == pytest.approx(-6.0)
    assert out["peak"] == 3 and out["trough"] == 4
    assert out["longest_underwater"] == 2


def test_sharpe_of_constant_and_known_series():
    assert math.isnan(rs.annualised_sharpe(pd.Series([1.0] * 10)))            # zero volatility: undefined
    x = pd.Series([1.0, -1.0] * 126)
    assert rs.annualised_sharpe(x) == pytest.approx(0.0, abs=1e-12)
    s = rs.sharpe_summary(pd.Series(np.r_[np.ones(126) * 2, np.ones(126) * -1]), n_trials=10)
    assert set(s) >= {"sharpe", "t_stat", "se_sharpe", "false_positive_prob", "significant_after_trials"}
