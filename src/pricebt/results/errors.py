"""Errors and warnings of the results package."""
from __future__ import annotations

from typing import Any

from ..errors import PricebtError


class ResultError(PricebtError):
    """Base class of results-package errors."""


class StatsError(ResultError):
    """Invalid statistics configuration or an input from which a statistic cannot be defined."""


class ReconcileError(ResultError):
    """`reconcile(strict=True)` found a broken accounting identity. `report` carries the full ReconcileReport."""

    def __init__(self, message: str, report: Any = None):
        self.report = report
        super().__init__(message)


class ResultIntegrityError(ResultError):
    """A persisted result directory is missing, unreadable or inconsistent."""


class ResultWarning(UserWarning):
    """A statistic or table is computed but should be read with care."""
