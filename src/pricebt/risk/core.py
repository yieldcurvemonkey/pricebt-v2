"""aggregate_risk, aggregate_results, subtract_risk, sort_risk, combine_risk_key.

Ported from gs_quant.risk.core (Apache-2.0; see NOTICE), 1.5.4 signatures and 2.1.17 behaviour
(IR_RISK_DESIGN.md section 1.8). The result classes gs keeps in the same module live in
`pricebt.risk.results`; this module imports them at top level, and `risk.results` imports this
module only inside function bodies (IR_RISK_DESIGN R2-22). Re-exported from `pricebt.risk`.
"""
from __future__ import annotations

import itertools
from typing import Iterable, Optional, Tuple

import pandas as pd

from pricebt.risk.results import DataFrameWithInfo, FloatWithInfo, PricingFuture, RiskKey, SeriesWithInfo, _table_like

__all__ = ["aggregate_risk", "aggregate_results", "subtract_risk", "sort_risk", "combine_risk_key"]

_RISK_COLUMNS = ("date", "time", "mkt_type", "mkt_asset", "mkt_class", "mkt_point")


def aggregate_risk(results: Iterable, threshold: Optional[float] = None, allow_heterogeneous_types: bool = False) -> pd.DataFrame:
    """Combine the results of multiple `calc()` calls (bucketed frames, or futures of them) into
    one frame: concatenate, fill NaN with 0, sum `value` by every other column, then drop rows with
    `abs(value) <= threshold` when a threshold is given. A historical (date-indexed) frame groups by
    its `dates` column too. `allow_heterogeneous_types` turns a Series into a one-row frame first.

    # pricebt DEV-R5: groups are kept in first-appearance order (groupby sort=False, dropna=False)
    # and `sort_risk` reorders columns only -- gs sorts rows by point_sort_order over mkt_point.
    """

    def get_df(result_obj):
        if isinstance(result_obj, PricingFuture):
            result_obj = result_obj.result()
        if isinstance(result_obj, pd.Series) and allow_heterogeneous_types:
            return pd.DataFrame(result_obj.raw_value).T
        return result_obj.raw_value

    dfs = [get_df(r) for r in results]
    result = pd.concat([df for df in dfs if len(df)] or dfs, ignore_index=True).fillna(0)
    result = result.groupby([c for c in result.columns if c != "value"], sort=False, dropna=False, as_index=False).sum()
    if threshold is not None:
        result = result[result.value.abs() > threshold]
    return sort_risk(result)


def aggregate_results(results: Iterable, allow_mismatch_risk_keys=False, allow_heterogeneous_types=False):
    """Sum per-instrument results (gs error contract): `None` for no results; `ValueError` for a
    result in error, for mixed types (unless `allow_heterogeneous_types`), for different units, and
    for different risk keys, i.e. different dates or measures (unless `allow_mismatch_risk_keys`).
    Scalars give a `FloatWithInfo`, historical scalars a `SeriesWithInfo` summed per date (index
    aligned), bucketed frames a `DataFrameWithInfo` via `aggregate_risk`, dicts recurse per key.
    A table result (`pricebt_table`, IR_RISK_DESIGN R2-15) is concatenated, never summed."""
    unit = None
    risk_key = None
    results = tuple(results)
    if not len(results):
        return None

    for result in results:
        if getattr(result, "error", None):
            raise ValueError("Cannot aggregate results in error")
        if not allow_heterogeneous_types and not isinstance(result, type(results[0])):
            raise ValueError(f"Cannot aggregate heterogeneous types: {type(result)} vs {type(results[0])}")
        result_unit = getattr(result, "unit", None)
        # `is not None` (gs tests truthiness): pricebt's dimensionless unit is `{}` (DESIGN.md section 4.2)
        if result_unit is not None:
            if unit is not None and unit != result_unit:
                raise ValueError(f"Cannot aggregate results with different units for {getattr(result.risk_key, 'risk_measure', None)}")
            unit = result_unit
        result_key = getattr(result, "risk_key", None)
        # pricebt's RiskKey carries only date and risk_measure (params/market are always None), so
        # the whole key is gs's `ex_historical_diddle`
        if not allow_mismatch_risk_keys and risk_key and risk_key != result_key:
            raise ValueError("Cannot aggregate results with different pricing keys")
        risk_key = risk_key or result_key

    inst = results[0]
    if isinstance(inst, dict):
        return dict((k, aggregate_results([r[k] for r in results])) for k in inst.keys())
    if isinstance(inst, tuple):
        return tuple(set(itertools.chain.from_iterable(results)))
    if isinstance(inst, float):
        # pricebt DEV-R15: a plain float (e.g. a transformer's output) sums like a FloatWithInfo (gs
        # raises AttributeError reading `.error` on it). Left to right, as gs's sum() over float
        # subclasses does (builtin sum() of exact floats is compensated, which would change the
        # last digits).
        total = 0.0
        for r in results:
            total += float(r)
        return FloatWithInfo(total, risk_key=risk_key, unit=unit)
    if isinstance(inst, SeriesWithInfo):
        return SeriesWithInfo(sum(results), risk_key=risk_key, unit=unit)
    if isinstance(inst, DataFrameWithInfo):
        if all(getattr(r, "pricebt_table", False) for r in results):
            # pricebt DEV-R11: a table result is concatenated (its rows are records, not buckets)
            dates = tuple(dict.fromkeys(itertools.chain.from_iterable(r.pricebt_dates or () for r in results))) or None
            out = _table_like(pd.concat([pd.DataFrame(r) for r in results], ignore_index=True), inst, risk_key, dates)
            out.unit = unit  # the checked unit, not only the first table's
            return out
        return DataFrameWithInfo(aggregate_risk(results, allow_heterogeneous_types=allow_heterogeneous_types), risk_key=risk_key, unit=unit)
    return None


def subtract_risk(left: DataFrameWithInfo, right: DataFrameWithInfo) -> pd.DataFrame:
    """Subtract bucketed risk. Dimensions must be identical.

    # pricebt DEV-R10: gs asserts `'value' in left.columns.names`, which fails for every ordinary
    # frame (its column index is unnamed), so gs's subtract_risk always raises AssertionError. This
    # implements the intent, `aggregate_risk((left, -right))`, and requires identical columns.
    """
    if list(left.columns) != list(right.columns) or "value" not in left.columns:
        raise ValueError(f"subtract_risk needs identical columns including 'value': {list(left.columns)} vs {list(right.columns)}")
    right_negated = right.copy()
    right_negated["value"] = right_negated["value"] * -1
    return aggregate_risk((left, right_negated))


def sort_risk(df: pd.DataFrame, by: Tuple[str, ...] = _RISK_COLUMNS) -> pd.DataFrame:
    """Sort bucketed risk: the `by` columns first (in `by` order), then the rest; a `date` column
    becomes the index.

    # pricebt DEV-R5: rows keep their first-appearance order; gs sorts them by the `by` columns with
    # point_sort_order over mkt_point (pricebt never parses point labels).
    """
    columns = tuple(df.columns)
    df_fields = [f for f in by if f in columns]
    df_fields.extend(f for f in columns if f not in df_fields)
    result = pd.DataFrame(df)[df_fields]
    if "date" in result:
        result = result.set_index("date")
    return result


def combine_risk_key(key_1: RiskKey, key_2: RiskKey) -> RiskKey:
    """Combine two risk keys (key_1, key_2) into a new RiskKey: each field is kept where the two
    keys agree, else None."""

    def get_field_value(field_name: str):
        return getattr(key_1, field_name) if getattr(key_1, field_name) == getattr(key_2, field_name) else None

    return RiskKey(*(get_field_value(f) for f in RiskKey._fields))
