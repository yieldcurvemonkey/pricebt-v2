"""MeanReversionTriggerRequirements golden test (IMPLEMENTATION_PLAN.md P3.4; research/01 section
6.5.9's 19-row table, produced by running the real gs_quant 2.1.17 code -- reproduced here row by
row through pricebt's port fed by P3.1's real GenericDataSource).

Under 1.5.4 the same input raises TypeError on 01-07 (a `_current_position` typo plus a wrong `>`
that never lets a short position close); 2.1.17 fixed both, which is why DESIGN.md decision 0.1
ports 2.1.17's behaviour here, not 1.5.4's.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.data_sources import GenericDataSource, MissingDataStrategy
from pricebt.backtests.triggers import MeanReversionTriggerRequirements

PRICES = [0, 1, 0, 1, 0, 1, 5, 4, 0.4, 0.2, -5, -4, 0, 1, 2, -6, -1, 3, 3]
START = dt.date(2024, 1, 1)
DATES = [START + dt.timedelta(days=i) for i in range(len(PRICES))]

# research/01 section 6.5.9: (date offset from START, triggered, scaling, current_position AFTER
# the call). rolling_mean/rolling_std are not re-quoted here -- they are derived below directly
# from PRICES via pandas, the same way GenericDataSource computes them, and cross-checked against
# the table's rounded values so nothing here is a copied-in magic constant.
GOLDEN = [
    (0, False, None, 0),
    (1, False, None, 0),
    (2, False, None, 0),
    (3, False, None, 0),
    (4, False, None, 0),
    (5, False, None, 0),
    (6, True, -1, -1),
    (7, False, None, -1),
    (8, True, 1, 0),
    (9, False, None, 0),
    (10, True, 1, 1),
    (11, False, None, 1),
    (12, True, -1, 0),
    (13, False, None, 0),
    (14, False, None, 0),
    (15, True, 1, 1),
    (16, True, -1, 0),
    (17, False, None, 0),
    (18, False, None, 0),
]

# research/01 section 6.5.9's rounded rolling_mean/rolling_std, quoted for the cross-check below
# (not used to drive the assertions against the port -- those come from GOLDEN/pandas directly).
GOLDEN_STATS = [
    (float('nan'), float('nan')),
    (0.0000, float('nan')),
    (0.5000, 0.7071),
    (0.3333, 0.5774),
    (0.5000, 0.5774),
    (0.4000, 0.5477),
    (0.6000, 0.5477),
    (1.4000, 2.0736),
    (2.2000, 2.1679),
    (2.0800, 2.2654),
    (2.1200, 2.2208),
    (0.9200, 3.9360),
    (-0.8800, 3.6513),
    (-1.6800, 2.6023),
    (-1.5600, 2.7328),
    (-1.2000, 3.1145),
    (-1.4000, 3.4351),
    (-0.8000, 3.1145),
    (-0.2000, 3.5637),
]

assert len(GOLDEN) == 19 and len(GOLDEN_STATS) == 19


def _source() -> GenericDataSource:
    series = pd.Series(PRICES, index=DATES)
    return GenericDataSource(series, MissingDataStrategy.fill_forward)


def _requirements(source) -> MeanReversionTriggerRequirements:
    return MeanReversionTriggerRequirements(
        data_source=source, z_score_bound=1.5, rolling_mean_window=5, rolling_std_window=5
    )


def test_golden_rolling_stats_match_the_research_note():
    """Sanity-check the fixture itself: rolling_mean/std, computed straight from PRICES via
    pandas (the last 5 points strictly before each date), match the table's rounded values."""
    source = _source()
    for offset, (expected_mean, expected_std) in zip(range(19), GOLDEN_STATS):
        state = DATES[offset]
        window_mean = source.get_data_range(state, 5).mean()
        window_std = source.get_data_range(state, 5).std()
        if expected_mean != expected_mean:  # NaN
            assert window_mean != window_mean
        else:
            assert window_mean == pytest.approx(expected_mean, abs=5e-5)
        if expected_std != expected_std:  # NaN
            assert window_std != window_std
        else:
            assert window_std == pytest.approx(expected_std, abs=5e-5)


def test_mean_reversion_golden_table_row_by_row():
    source = _source()
    req = _requirements(source)

    for offset, expected_triggered, expected_scaling, expected_position_after in GOLDEN:
        state = DATES[offset]
        info = req.has_triggered(state)

        assert info.triggered is expected_triggered, f"day {offset} ({state}): triggered"
        if expected_triggered:
            assert info.info_dict is not None, f"day {offset}: expected an info_dict"
            action_info = info.info_dict[AddTradeAction]
            assert action_info.scaling == expected_scaling, f"day {offset}: scaling"
            assert action_info.next_schedule is None, f"day {offset}: next_schedule"
        else:
            assert info.info_dict is None, f"day {offset}: expected no info_dict"
        assert req.current_position == expected_position_after, f"day {offset}: current_position after"


def test_golden_day_one_and_two_do_not_fire_on_nan_zscore():
    """Explicit callout for the 'a NaN does not fire' acceptance item: day 0 has an empty window
    (mean/std both NaN) and day 1 has a 1-point window (std NaN); abs(nan) > bound is False both
    times, so neither fires."""
    source = _source()
    req = _requirements(source)

    assert req.has_triggered(DATES[0]).triggered is False
    assert req.has_triggered(DATES[1]).triggered is False
    assert req.current_position == 0
