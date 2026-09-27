"""Known answers of the `data/fixtures` and small helpers shared by the tests of the test-support providers (contains no tests)."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import pytest

NY = "America/New_York"
#: the fixtures root, following the same environment variables as `support.common.default_fixtures_root` (which raises when it is absent; this does not)
FIXTURES = Path(os.environ.get("PRICEBT_DATA") or os.environ.get("PRICEBT_FIXTURES") or Path(__file__).resolve().parents[2] / "data" / "fixtures")
EOD = "USD-SOFR-1D-CITIVELOEXCEL"
MIN = "USD-SOFR-1D-CITIVELOEXCELMIN"
GSQ = "USD-SOFR-1D"
Q12 = "USD-SOFR-1D-Q12STIRT"

#: 10Y spot-starting par rate (percent) of the curve rows, computed by the maintainer's own pricing infrastructure (provenance of the fixtures)
ARBS_PAR_10Y = {
    (EOD, "2026-08-05 17:00"): 4.2090600004,
    (EOD, "2024-06-14 17:00"): 3.8032399939,
    (EOD, "2019-03-15 17:00"): 2.3064899990,
    (MIN, "2026-08-05 10:30"): 4.2203106792,
    (MIN, "2026-03-09 09:30"): 3.6955899792,
    (MIN, "2025-11-03 10:00"): 3.6737496859,
    (GSQ, "2026-08-05 17:00"): 4.2278877956,
}
#: served / dropped row counts of each curve asset under its built-in profile
CURVE_COUNTS = {EOD: (2104, 79), MIN: (30552, 2100), Q12: (6815, 90), GSQ: (5, 0)}
#: the 2026 holiday rows of the EOD store (node_dates[0] = previous business day)
HOLIDAY_ROWS_2026 = ("2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03")
FEDINVEST_ZERO_DAYS = (
    "2014-09-12", "2014-11-21", "2026-07-09", "2026-07-10", "2026-07-13", "2026-07-17", "2026-07-20", "2026-07-24", "2026-07-27", "2026-08-07",
    "2026-08-18", "2026-08-20", "2026-08-21",
)
FEDINVEST_SERVED = 4151  # panel days served with the zero-price gate only
FEDINVEST_SERVED_PARTIAL = 4146  # ... and with `partial_day_min_missing=10`
FEDINVEST_ROWS_OUT_OF_BAND = 4457
FEDINVEST_PARTIAL_DAYS_2023 = ("2023-02-10", "2023-06-15", "2023-08-01", "2023-11-20", "2023-12-04")
#: price-file CUSIPs that the reference table does not know (a quote needs reference data, so a snapshot cannot carry them)
FEDINVEST_UNREFERENCED = ("912810ES3", "912810FM5", "912810PU6", "912810PX0")
WEBULL_FLAT = ("2026-02-16", "2026-02-21", "2026-02-22", "2026-02-28", "2026-03-01", "2026-03-07", "2026-03-08", "2026-03-14", "2026-03-15")
WEBULL_SERVED_MINUTES = 11419
WEBULL_FILTERED_COUNTS = {"912810UQ9": 3, "91282CPJ4": 1, "91282CPQ8": 2, "91282CPR6": 9, "91282CPW5": 1, "91282CPY1": 14, "91282CPZ8": 3, "91282CQA2": 4}
#: on-the-run bonds: (reference date, alias) -> cusip (verified against the maintainer's infrastructure)
ON_THE_RUN = {("2026-08-14", "CT10"): "91282CQQ7", ("2026-08-17", "CT10"): "91282CQQ7", ("2026-08-19", "CT10"): "91282CRF0", ("2026-08-07", "CT10"): "91282CQQ7",
              ("2026-08-18", "CT10"): "91282CRF0"}


def have_fixtures() -> bool:
    return (FIXTURES / "MANIFEST.json").is_file()


needs_fixtures = pytest.mark.skipif(not have_fixtures(), reason="data/fixtures missing (spec G6)")


def ny(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz=NY)


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


#: weekday holidays of the swap calendar 2020-11 .. 2026-01 (fixture calendar `nyc.json`): the calendar the synthetic market's pinned values were generated with
NYC_HOLIDAYS_2020_2026 = (
    "2020-11-11", "2020-11-26", "2020-12-25", "2021-01-01", "2021-01-18", "2021-02-15", "2021-04-02", "2021-05-31", "2021-07-05", "2021-09-06", "2021-10-11", "2021-11-11",
    "2021-11-25", "2021-12-24", "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30", "2022-06-20", "2022-07-04", "2022-09-05", "2022-10-10", "2022-11-11", "2022-11-24",
    "2022-12-26", "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29", "2023-06-19", "2023-07-04", "2023-09-04", "2023-10-09", "2023-11-23", "2023-12-25",
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02", "2024-10-14", "2024-11-11", "2024-11-28", "2024-12-25",
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01", "2025-10-13", "2025-11-11", "2025-11-27", "2025-12-25",
    "2026-01-01",
)
