"""DataFrequency, Dataset (stub), measure_series (a pricebt extension) (DESIGN.md section 10).

`measure_series` is P4.3's job and is not defined here yet -- see IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from ..base import EnumBase
from ..errors import NotSupportedError

__all__ = ["DataFrequency", "Dataset"]


class DataFrequency(EnumBase, str, Enum):
    DAILY = "daily"
    REAL_TIME = "realTime"
    ANY = "any"


class Dataset:
    """gs-compatible stub: pricebt has no server-side dataset store, so construction always
    raises (DESIGN.md section 10)."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotSupportedError(
            "gs Dataset reads GS server-side data, which pricebt does not have; build a pandas "
            "Series from your own data or use pricebt.data.measure_series(...)"
        )
