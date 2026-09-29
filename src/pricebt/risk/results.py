"""RiskKey, FloatWithInfo, StringWithInfo, DictWithInfo, SeriesWithInfo, DataFrameWithInfo,
ErrorValue, UnsupportedValue, MultipleRiskMeasureResult, PricingFuture, LazyFuture, PortfolioPath,
PortfolioRiskResult.

Ported from gs_quant.risk.core (the value classes) and gs_quant.risk.results (Apache-2.0; see
NOTICE): 1.5.4 signatures, 2.1.17 behaviour, research note R10 section 2. No scenarios. It
dispatches on `pricebt.base.Priceable` and duck typing, so it never imports `pricebt.instrument` or
`pricebt.markets.portfolio` (Import DAG, DESIGN.md section 3.2): a "portfolio" here is anything
exposing `.priceables` (direct children, in order) and `.all_instruments` (leaves), plus `+` when
portfolios differ and a `(priceables, name=...)` constructor for `subset`. `pricebt.risk.core` is
imported only inside function bodies (IR_RISK_DESIGN R2-22).
"""
from __future__ import annotations

import copy
import datetime as dt
from collections import namedtuple
from collections.abc import Mapping
from itertools import chain
from typing import Any, Iterable, Optional

import pandas as pd

from pricebt.base import Priceable
from pricebt.errors import NotSupportedError

RiskKey = namedtuple("RiskKey", ["provider", "date", "market", "params", "scenario", "risk_measure"])

_BUCKET_COLUMNS = ("mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style")
_BUCKET_ROW_KEYS = frozenset(_BUCKET_COLUMNS) | {"value"}


def _historical_key(key: Optional[RiskKey]) -> Optional[RiskKey]:
    return key._replace(date=None) if key is not None else None


def _date_of(value):
    return getattr(getattr(value, "risk_key", None), "date", None)


# ============================================================================= values


class FloatWithInfo(float):
    """A scalar risk value. `+` with another FloatWithInfo requires an equal `.unit` and combines
    the two risk keys (`combine_risk_key`); `*` keeps `risk_key`/`unit` (gs); `-`, `/` and unary `-`
    give a plain float (gs). `repr` appends the unit, e.g. `1500.0 (USD)`.

    # pricebt DEV-R12: the constructor takes the value first, `FloatWithInfo(value, risk_key=None,
    # unit=None, error=None)`; gs's order is `(risk_key, value, unit, error, request_id)`. Every
    # pricebt producer passes the value positionally, so the order is kept (StringWithInfo and
    # DictWithInfo follow it too).
    """

    def __new__(cls, value, risk_key: Optional[RiskKey] = None, unit: Optional[dict] = None, error=None):
        obj = super().__new__(cls, value)
        obj.risk_key = risk_key
        obj.unit = unit
        obj.error = error
        return obj

    @property
    def raw_value(self) -> float:
        return float(self)

    def __add__(self, other):
        other_unit = getattr(other, "unit", None)
        if self.unit is not None and other_unit is not None and other_unit != self.unit:
            raise ValueError("FloatWithInfo unit mismatch")
        key = self.risk_key
        if isinstance(other, FloatWithInfo) and key is not None and other.risk_key is not None:
            from pricebt.risk.core import combine_risk_key  # lazy: core imports this module

            key = combine_risk_key(key, other.risk_key)
        return FloatWithInfo(float(self) + float(other), risk_key=key, unit=self.unit or other_unit)

    __radd__ = __add__

    def __mul__(self, other):
        product = float.__mul__(self, other)
        if product is NotImplemented:
            return NotImplemented
        key = self.risk_key
        if isinstance(other, FloatWithInfo) and key is not None and other.risk_key is not None:
            from pricebt.risk.core import combine_risk_key

            key = combine_risk_key(key, other.risk_key)
        return FloatWithInfo(product, risk_key=key, unit=self.unit)

    def to_frame(self):
        return self

    @staticmethod
    def compose(components: Iterable) -> "SeriesWithInfo":
        """Single-date scalars -> one `SeriesWithInfo` indexed by each component's `risk_key.date`."""
        components = tuple(components)
        series = pd.Series([c.raw_value for c in components], index=[_date_of(c) for c in components])
        return SeriesWithInfo(series, risk_key=_historical_key(components[0].risk_key), unit=components[0].unit)

    def __repr__(self) -> str:
        if self.unit:
            return f"{float(self)} ({next(iter(self.unit))})"
        return f"{float(self)}"


class StringWithInfo(str):
    """A string risk value (gs); value first, like FloatWithInfo (pricebt DEV-R12)."""

    def __new__(cls, value, risk_key: Optional[RiskKey] = None, unit: Optional[dict] = None, error=None):
        obj = super().__new__(cls, value)
        obj.risk_key, obj.unit, obj.error = risk_key, unit, error
        return obj

    @property
    def raw_value(self) -> str:
        return str(self)

    def __repr__(self) -> str:
        return self.error if self.error else str.__repr__(self)


class DictWithInfo(dict):
    """A dict risk value (gs); value first, like FloatWithInfo (pricebt DEV-R12)."""

    def __init__(self, value, risk_key: Optional[RiskKey] = None, unit: Optional[dict] = None, error=None):
        super().__init__(value)
        self.risk_key, self.unit, self.error = risk_key, unit, error

    @property
    def raw_value(self) -> dict:
        return dict(self)

    def __repr__(self) -> str:
        return self.error if self.error else dict.__repr__(self)


class _InfoFrameMixin:
    """Shared pandas-subclass plumbing for SeriesWithInfo/DataFrameWithInfo: `risk_key`/`unit`/`error`
    survive slicing (pandas' `_metadata` + `__finalize__` protocol)."""

    _metadata = ["risk_key", "unit", "error"]

    def __init__(self, data=None, *args, risk_key: Optional[RiskKey] = None, unit: Optional[dict] = None, error=None, **kwargs):
        super().__init__(data, *args, **kwargs)
        self.risk_key = risk_key
        self.unit = unit
        self.error = error

    @property
    def raw_value(self):
        raise NotImplementedError


class SeriesWithInfo(_InfoFrameMixin, pd.Series):
    """A historical scalar result, indexed by date."""

    @property
    def _constructor(self):
        return SeriesWithInfo

    @property
    def _constructor_expanddim(self):
        return DataFrameWithInfo

    @property
    def raw_value(self) -> pd.Series:
        return pd.Series(self)


class DataFrameWithInfo(_InfoFrameMixin, pd.DataFrame):
    """A bucketed (vector) result, with exactly the six columns `mkt_type, mkt_asset, mkt_class,
    mkt_point, mkt_quoting_style, value` (a missing label is `''`, never NaN); a historical bucketed
    result is the same columns indexed by `date` (gs `compose`) -- or, when `pricebt_table` is True,
    a table result (`make_table_frame`, docs/v2/IR_RISK_DESIGN.md R2-15) with the asset function's
    own columns (historical: a `date` column first)."""

    _metadata = _InfoFrameMixin._metadata + ["pricebt_table"]
    pricebt_table = False  # a class default, so a bucketed frame never falls into column lookup

    @property
    def _constructor(self):
        return DataFrameWithInfo

    @property
    def _constructor_sliced(self):
        return SeriesWithInfo

    @property
    def raw_value(self) -> pd.DataFrame:
        """A plain DataFrame; a date index becomes a leading `dates` column (gs)."""
        if self.empty or not isinstance(self.index.values[0], dt.date):
            return pd.DataFrame(self)
        return pd.DataFrame(self).rename_axis("dates").reset_index()

    def to_frame(self):
        return self

    @staticmethod
    def compose(components: Iterable) -> "DataFrameWithInfo":
        """Single-date frames -> one frame indexed by `date` (each component's `risk_key.date`), in
        component order (gs `DataFrameWithInfo.compose`)."""
        components = tuple(components)
        frames = [pd.DataFrame(c).assign(date=_date_of(c)) for c in components]
        df = pd.concat([f for f in frames if len(f)] or frames).set_index("date")
        return DataFrameWithInfo(df, risk_key=_historical_key(components[0].risk_key), unit=components[0].unit)


class ErrorValue:
    """`(risk_key, error)`; `.raw_value` is always `None`; any other missing attribute raises an
    `AttributeError` naming the error (gs)."""

    def __init__(self, risk_key: Optional[RiskKey], error):
        self.risk_key = risk_key
        self.error = error
        self.raw_value = None

    def __getattr__(self, item):
        # only reached for a missing attribute; __dict__ (not self.error) so a half-built instance
        # (copy/pickle probes) cannot recurse
        raise AttributeError(f"ErrorValue object has no attribute {item}.  Error was {self.__dict__.get('error')}")

    def __repr__(self) -> str:
        return f"ErrorValue({self.error!r})"


class UnsupportedValue:
    """A measure that does not apply (gs); hidden by `to_frame` unless `display_options.show_na`."""

    def __init__(self, risk_key: Optional[RiskKey]):
        self.risk_key = risk_key
        self.unit = None
        self.error = None

    @property
    def raw_value(self) -> str:
        return "Unsupported Value"

    def __repr__(self) -> str:
        return "Unsupported Value"


def _bucket_row(row, labels: dict) -> dict:
    """One per-row bucket (docs/v2/IR_RISK_DESIGN.md R2-14) as a full six-column row: a coordinate
    the row omits comes from `labels`, else `''`."""
    if not isinstance(row, Mapping) or "value" not in row or not set(row) <= _BUCKET_ROW_KEYS:
        raise ValueError(f"a bucket row must be a dict with a 'value' and coordinates among {list(_BUCKET_COLUMNS)}, got {row!r}")
    out = {c: row.get(c, labels.get(c, "")) for c in _BUCKET_COLUMNS}
    out["mkt_point"] = str(out["mkt_point"])
    out["value"] = row["value"]
    return out


def make_bucketed_frame(buckets, labels: Optional[dict] = None, risk_key: Optional[RiskKey] = None, unit: Optional[dict] = None) -> DataFrameWithInfo:
    """Build a `DataFrameWithInfo` from a portfolio function's returned buckets and an asset
    config's static `labels` (DESIGN.md section 4.2). `buckets` is a `{mkt_point: value}` dict, or
    a list of per-row dicts whose keys are among the six columns, `value` required, and whose
    omitted coordinates come from `labels` (docs/v2/IR_RISK_DESIGN.md R2-14). `mkt_point` is used
    exactly as returned (already `;`-joined by the config's own expression if multi-dimensional)
    -- pricebt never parses it. Row order is the dict's insertion order (the list's order).

    # pricebt DEV-R5: gs sorts bucketed rows with sort_risk/point_sort_order (asset-class regexes
    # over mkt_point, relative to today's date); pricebt keeps the config's own bucket order instead
    # (first appearance), because point labels are opaque strings here, never parsed by pricebt.
    """
    labels = labels or {}
    if isinstance(buckets, (list, tuple)):
        rows = [_bucket_row(r, labels) for r in buckets]
    else:
        rows = [
            {
                "mkt_type": labels.get("mkt_type", ""),
                "mkt_asset": labels.get("mkt_asset", ""),
                "mkt_class": labels.get("mkt_class", ""),
                "mkt_point": str(point),
                "mkt_quoting_style": labels.get("mkt_quoting_style", ""),
                "value": value,
            }
            for point, value in buckets.items()
        ]
    df = pd.DataFrame(rows, columns=list(_BUCKET_COLUMNS) + ["value"])
    return DataFrameWithInfo(df, risk_key=risk_key, unit=unit)


def make_table_frame(rows, risk_key: Optional[RiskKey] = None, unit: Optional[dict] = None, scale_columns: Iterable[str] = (), factor: float = 1.0) -> DataFrameWithInfo:
    """Build a table result (docs/v2/IR_RISK_DESIGN.md R2-15) from an asset function's `returns:
    frame` value -- a DataFrame or a list of per-row dicts -- for one position. Only the
    `scale_columns` are multiplied by `factor` (the position's quantity, or 1.0); every other
    column is kept as returned. `rows` is never mutated (the pricing layer caches it). A frame
    with rows must have every scale column. The result carries `pricebt_table = True`, which is how
    consumers tell a table from a bucketed frame."""
    if isinstance(rows, pd.DataFrame):
        df = pd.DataFrame(rows, copy=True)
    elif isinstance(rows, (list, tuple)) and all(isinstance(r, Mapping) for r in rows):
        df = pd.DataFrame(list(rows))
    else:
        raise TypeError(f"a frame result must be a DataFrame or a list of dicts, got {type(rows).__name__}")
    scale_columns = tuple(scale_columns)
    if len(df):
        missing = [c for c in scale_columns if c not in df.columns]
        if missing:
            raise ValueError(f"scale_columns {missing} are not columns of the frame {list(df.columns)}")
        if factor != 1.0:
            for c in scale_columns:
                df[c] = df[c] * factor
    out = DataFrameWithInfo(df, risk_key=risk_key, unit=unit)
    out.pricebt_table = True
    return out


def _as_table(df, risk_key=None, unit=None) -> DataFrameWithInfo:
    out = DataFrameWithInfo(pd.DataFrame(df), risk_key=risk_key, unit=unit)
    out.pricebt_table = True
    return out


# ============================================================================= helpers


def _is_table(value) -> bool:
    return isinstance(value, pd.DataFrame) and getattr(value, "pricebt_table", False) is True


def _is_portfolio(x) -> bool:
    """Anything exposing `.priceables`, checked on the type or the instance dict -- never through
    `getattr`, because an instrument's attribute lookup can reach its asset config."""
    return hasattr(type(x), "priceables") or "priceables" in getattr(x, "__dict__", ())


def _is_instrument(x) -> bool:
    return isinstance(x, Priceable) and not _is_portfolio(x)


def _is_risk_measure(item) -> bool:
    """Duck-types `pricebt.risk.RiskMeasure` (on the type, see `_is_portfolio`) without importing it:
    `pricebt.risk` imports this module, so the reverse import would be circular."""
    return all(hasattr(type(item), a) for a in ("asset_class", "measure_type", "parameters"))


def _is_measures(item) -> bool:
    return _is_risk_measure(item) or (isinstance(item, (list, tuple)) and len(item) > 0 and all(_is_risk_measure(i) for i in item))


def _is_dates(item) -> bool:
    return isinstance(item, dt.date) or (isinstance(item, (list, tuple)) and len(item) > 0 and all(isinstance(i, dt.date) for i in item))


def _show_na(display_options) -> bool:
    return bool(getattr(display_options, "show_na", False))


def _value_dates(value) -> set:
    """The dates a historical value spans (gs `dates`): a PRR/MRMR's own, a table's `date` column,
    a Series'/frame's index when every entry is a date; empty for a single-date value."""
    if isinstance(value, (PortfolioRiskResult, MultipleRiskMeasureResult)):
        return set(value.dates)
    if _is_table(value) and "date" in value.columns:
        return set(value["date"])
    if isinstance(value, (pd.Series, pd.DataFrame)) and len(value.index) and all(isinstance(i, dt.date) for i in value.index):
        return set(value.index)
    return set()


def _with_info(value, risk_key, unit, error):
    if isinstance(value, (ErrorValue, UnsupportedValue)):
        return value
    if isinstance(value, pd.DataFrame):
        return DataFrameWithInfo(value, risk_key=risk_key, unit=unit, error=error)
    if isinstance(value, pd.Series):
        return SeriesWithInfo(value, risk_key=risk_key, unit=unit, error=error)
    return FloatWithInfo(value, risk_key=risk_key, unit=unit, error=error)


def _value_for_date(result, date):
    """One date's (or a list of dates') slice of a historical value (gs `_value_for_date`), carrying
    the selected date(s) in its risk key. A date-indexed frame is selected with
    `df[df.index == date]` (IR_RISK_DESIGN R2-27), so a date with no rows gives an empty
    `DataFrameWithInfo` rather than a KeyError; a single date drops the date index. A table selects
    on its `date` column (a single date drops the column)."""
    if result.empty:
        return result
    single = isinstance(date, dt.date)
    key = getattr(result, "risk_key", None)
    risk_key = key._replace(date=date if single else tuple(date)) if key is not None else None
    unit, error = getattr(result, "unit", None), getattr(result, "error", None)
    if isinstance(result, pd.DataFrame):
        if _is_table(result):
            rows = result[result["date"] == date] if single else result[result["date"].isin(date)]
            return _as_table(rows.drop(columns="date").reset_index(drop=True) if single else rows, risk_key, unit)
        rows = result[result.index == date] if single else result[result.index.isin(date)]
        return DataFrameWithInfo(pd.DataFrame(rows.reset_index(drop=True) if single else rows), risk_key=risk_key, unit=unit, error=error)
    raw = result.loc[date] if single else result.loc[list(date)]
    if isinstance(raw, DataFrameWithInfo):
        # one date's frame held in a Series (the pricing layer's pre-R2-27 historical bucketed
        # shape): it already carries its own date, unit and key
        return raw
    return _with_info(raw, risk_key, unit, error)


def _compose(lhs, rhs):
    """gs `_compose`: stitch two results of the same instrument and measure over dates; the right
    side wins where both have a date."""
    if isinstance(lhs, MultipleRiskMeasureResult) and isinstance(rhs, MultipleRiskMeasureResult):
        return lhs + rhs
    if _is_table(lhs) and _is_table(rhs):
        lhs, rhs = (t if "date" in t.columns else t.assign(date=_date_of(t))[["date", *t.columns]] for t in (lhs, rhs))
        rows = pd.concat([lhs[~lhs["date"].isin(rhs["date"])], rhs]).sort_values("date", kind="stable")
        return _as_table(rows.reset_index(drop=True), _historical_key(lhs.risk_key), lhs.unit)
    if isinstance(lhs, DataFrameWithInfo) and isinstance(rhs, DataFrameWithInfo):
        lhs, rhs = (f if f.index.name == "date" else DataFrameWithInfo.compose((f,)) for f in (lhs, rhs))
        # gs uses DataFrame.append, which pandas 2 removed; pd.concat is the same operation
        rows = pd.concat([lhs[~lhs.index.isin(rhs.index)], rhs]).sort_index(kind="stable")
        return DataFrameWithInfo(pd.DataFrame(rows), risk_key=lhs.risk_key, unit=lhs.unit)
    if isinstance(lhs, FloatWithInfo) and isinstance(rhs, FloatWithInfo):
        return rhs if _date_of(lhs) == _date_of(rhs) else FloatWithInfo.compose((lhs, rhs))
    lhs_s = FloatWithInfo.compose((lhs,)) if isinstance(lhs, FloatWithInfo) else lhs
    rhs_s = FloatWithInfo.compose((rhs,)) if isinstance(rhs, FloatWithInfo) else rhs
    if isinstance(lhs_s, SeriesWithInfo) and isinstance(rhs_s, SeriesWithInfo):
        key, unit = (lhs_s.risk_key, lhs_s.unit) if isinstance(lhs, SeriesWithInfo) else (rhs_s.risk_key, rhs_s.unit)
        return SeriesWithInfo(rhs_s.combine_first(lhs_s).sort_index(), risk_key=key, unit=unit)
    raise RuntimeError(f"{lhs} and {rhs} cannot be composed")


def _map_value(value, fn):
    """Apply a scalar operation to a result: a frame's `value` column only (gs would also repeat the
    string label columns), a Series or scalar directly, an MRMR/PRR per value."""
    if isinstance(value, (MultipleRiskMeasureResult, PortfolioRiskResult)):
        return value._map(fn)
    if isinstance(value, pd.DataFrame):
        out = value.copy()
        out["value"] = fn(out["value"])
        return out
    return fn(value)


def _check_number(other) -> None:
    if not isinstance(other, (int, float)):
        # pricebt DEV-R9: gs *returns* this ValueError instead of raising it
        raise ValueError("Can only multiply by an int or float")


def _records_of(value, extra: dict, display_options=None, drop_tables: bool = False) -> list:
    """gs `_to_records` for one leaf value: one record per scalar, per date of a Series, per row of
    a frame (a date index becomes `dates`), per measure of an MRMR (plus `risk_measure`)."""
    if isinstance(value, MultipleRiskMeasureResult):
        return [dict(r, risk_measure=rm) for rm in value for r in _records_of(dict.__getitem__(value, rm), extra, display_options, drop_tables)]
    if isinstance(value, pd.DataFrame):
        if drop_tables and _is_table(value):
            return []
        if value.empty:
            return [{**extra, "value": None}] if _show_na(display_options) else []
        raw = value.raw_value if isinstance(value, DataFrameWithInfo) else value
        return [dict(item, **extra) for item in raw.to_dict("records")]
    if isinstance(value, pd.Series):
        return [{"dates": d, "value": v, **extra} for d, v in value.items()]
    if isinstance(value, UnsupportedValue) and not _show_na(display_options):
        return []
    return [{**extra, "value": value}]


def _pivot_to_frame(df, values, index, columns, aggfunc):
    """gs `pivot_to_frame`: `pivot_table`, then rows and columns back in first-appearance order."""
    try:
        pivot_df = df.pivot_table(values=values, index=index, columns=columns, aggfunc=aggfunc)
    except ValueError:
        raise RuntimeError("Unable to successfully pivot data")
    try:
        if index is not None:
            pivot_df = pivot_df.reindex(index=df.set_index(list(pivot_df.index.names)).index.unique())
        if columns is not None:
            pivot_df = pivot_df.reindex(columns=df.set_index(list(pivot_df.columns.names)).index.unique())
        return pivot_df
    except KeyError:
        return pivot_df


def _label(obj, idx: int) -> str:
    """gs `Portfolio._to_records` naming: the object's name, else `Portfolio_{idx}` for a
    sub-portfolio and `{type}_{idx}` for a leaf (`type_.name`, e.g. the gs AssetType)."""
    name = getattr(obj, "name", None)
    if name is not None:
        return name
    if _is_portfolio(obj):
        return f"Portfolio_{idx}"
    type_ = getattr(type(obj), "type_", None)
    return f"{getattr(type_, 'name', None) or type_ or type(obj).__name__}_{idx}"


# ============================================================================= MultipleRiskMeasureResult


class MultipleRiskMeasureResult(dict):
    """A `{measure: value}` result for one instrument (or, from a multi-measure `aggregate()` or
    `transform()`, for the portfolio), gs constructor `(instrument, dict_values)`."""

    def __init__(self, instrument, dict_values: Iterable):
        super().__init__(dict_values)
        self._instrument = instrument

    @property
    def instrument(self):
        return self._instrument

    def __getitem__(self, item):
        if _is_dates(item):
            if not all(isinstance(v, (pd.DataFrame, pd.Series)) for v in self.values()):
                raise ValueError("Can only index by date on historical results")
            return MultipleRiskMeasureResult(self._instrument, ((k, _value_for_date(v, item)) for k, v in self.items()))
        return super().__getitem__(item)

    @property
    def dates(self) -> tuple:
        return tuple(sorted(set().union(*(_value_dates(v) for v in self.values()))))

    def _map(self, fn) -> "MultipleRiskMeasureResult":
        return MultipleRiskMeasureResult(self._instrument, ((k, _map_value(v, fn)) for k, v in self.items()))

    def __mul__(self, other):
        _check_number(other)
        return self._map(lambda v: v * other)

    def __add__(self, other):
        if isinstance(other, (int, float)):
            return self._map(lambda v: v + other)
        if not isinstance(other, MultipleRiskMeasureResult):
            raise ValueError("Can only add instances of MultipleRiskMeasureResult or int, float")
        instruments_equal = self._instrument == other._instrument
        self_dt = self.dates or (_date_of(next(iter(self.values()), None)),)
        other_dt = other.dates or (_date_of(next(iter(other.values()), None)),)
        if not set(self).isdisjoint(other) and instruments_equal and not set(self_dt).isdisjoint(other_dt):
            raise ValueError("Results overlap on risk measures, instruments or dates")
        if not instruments_equal:
            # pricebt DEV-R9: gs returns a PortfolioRiskResult over Portfolio((i1, i2)); this module
            # may not import the Portfolio class (Import DAG, DESIGN.md section 3.2)
            raise NotSupportedError("adding results of different instruments: calc on Portfolio((i1, i2)) instead")
        results: dict = {}
        # pricebt DEV-E14: an ordered key union (gs iterates a set)
        for result in (self, other):
            for key, value in result.items():
                results[key] = _compose(results[key], value) if key in results else value
        return MultipleRiskMeasureResult(self._instrument, results)

    def transform(self, risk_transformation) -> "MultipleRiskMeasureResult":
        return MultipleRiskMeasureResult(self._instrument, ((m, risk_transformation.apply((v,))[0]) for m, v in self.items()))

    def _to_records(self, extra_dict: dict, display_options=None) -> list:
        return _records_of(self, extra_dict, display_options)

    def to_frame(self, values="default", index="default", columns="default", aggfunc="sum", display_options=None):
        df = pd.DataFrame.from_records(self._to_records({}, display_options))
        if values is None and index is None and columns is None:
            return df
        if values == "default" and index == "default" and columns == "default":
            if "mkt_type" in df.columns:
                return df.set_index("risk_measure")
            values, columns, index = "value", "risk_measure", ("dates" if "dates" in df.columns else None)
        else:
            values = "value" if values in ("default", ["value"]) else values
            index = None if index == "default" else index
            columns = None if columns == "default" else columns
        return _pivot_to_frame(df, values, index, columns, aggfunc)


# ============================================================================= futures


class PricingFuture:
    """Wraps an already-known value (or exception); pricebt has no async pricing at this layer."""

    def __init__(self, result: Any = None, exception: Optional[BaseException] = None):
        self._result = result
        self._exception = exception

    def result(self):
        if self._exception is not None:
            raise self._exception
        return self._result

    def done(self) -> bool:
        return True


class _DeferredFuture(PricingFuture):
    """A value computed on first `.result()` by `fn()`, memoised (an exception too)."""

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self._evaluated = False

    def result(self):
        if not self._evaluated:
            try:
                self._result = self._fn()
            except Exception as exc:  # noqa: BLE001 - memoised like any other future
                self._exception = exc
            self._evaluated = True
        return super().result()


class LazyFuture(_DeferredFuture):
    """A deferred per-instrument bucketed value, grouped for a single evaluation across the group's
    members (DESIGN.md section 8.2). `service.group_aggregate(group_key, members)` is called once per
    distinct `group_key` by `PortfolioRiskResult.aggregate()`, not by this class: `.result()` runs
    only this instrument's own `thunk` (the single-trade value), memoised, and never evaluates the
    group. `service` is the `PricingService` instance that created it, captured at construction, so a
    late `.result()` (after a `reset()` or after the pricing session has exited) is a cache miss on
    that service, not an error. `group_key[2]` is the pricing date."""

    def __init__(self, thunk, group_key, member, service):
        super().__init__(thunk)
        self.thunk = thunk
        self.group_key = group_key
        self.member = member
        self.service = service


class _MultiMeasureFuture(PricingFuture):
    """Lazily merges several single-measure futures (one per risk measure, for one instrument) into
    a `MultipleRiskMeasureResult`, without evaluating any of them until `.result()` is called. Built
    by the pricing layer (one per multi-measure instrument) and by `PortfolioRiskResult.__add__`, so
    a `LazyFuture` slices back out still ungrouped, via `future_for()`, and
    `PortfolioRiskResult.aggregate()` still calls `service.group_aggregate(...)` once per group."""

    def __init__(self, measure_futures: dict, instrument=None):
        super().__init__()
        self._measure_futures = measure_futures
        self.instrument = instrument
        self._evaluated = False

    def result(self):
        if not self._evaluated:
            self._result = MultipleRiskMeasureResult(self.instrument, ((m, f.result()) for m, f in self._measure_futures.items()))
            self._evaluated = True
        return super().result()

    def future_for(self, measure):
        return self._measure_futures.get(measure)


def _as_future(value) -> PricingFuture:
    return value if hasattr(value, "result") and hasattr(value, "done") else PricingFuture(value)


def _sub_prr(future) -> Optional["PortfolioRiskResult"]:
    """The nested result a direct child's future holds (a sub-portfolio's), without evaluating any
    leaf future; None for a leaf."""
    if isinstance(future, PortfolioRiskResult):
        return future
    if type(future) is PricingFuture and future._exception is None and isinstance(future._result, PortfolioRiskResult):
        return future._result
    return None


def _measure_futures(future: PricingFuture, measures: tuple) -> dict:
    """`{measure: future}` for one leaf future, without calling `.result()` on a lazy one."""
    if isinstance(future, _MultiMeasureFuture):
        return dict(future._measure_futures)
    if len(measures) == 1:
        return {measures[0]: future}
    res = future.result()  # a multi-measure leaf that is not lazy (e.g. historical)
    if isinstance(res, MultipleRiskMeasureResult):
        return {m: PricingFuture(v) for m, v in res.items()}
    raise TypeError("expected a multi-measure future for more than one risk measure")


def _future_dates(future) -> set:
    """`_value_dates` of a leaf future; a LazyFuture is a single-date value and is never evaluated
    (IR_RISK_DESIGN R2-26)."""
    if isinstance(future, LazyFuture):
        return set()
    if isinstance(future, _MultiMeasureFuture):
        return set().union(*(_future_dates(f) for f in future._measure_futures.values()))
    return _value_dates(future.result())


def _future_date(future):
    """A single-date leaf's date without evaluating a LazyFuture (its `group_key[2]`; a
    multi-measure future's first sub-future; IR_RISK_DESIGN R2-26)."""
    if isinstance(future, LazyFuture):
        return future.group_key[2] if future.group_key else None
    if isinstance(future, _MultiMeasureFuture):
        return _future_date(next(iter(future._measure_futures.values()))) if future._measure_futures else None
    res = future.result()
    if isinstance(res, MultipleRiskMeasureResult):
        res = next(iter(res.values()), None)
    return _date_of(res)


# ============================================================================= PortfolioPath


class PortfolioPath:
    """A path of child indices from a portfolio (or a PortfolioRiskResult, or a tuple of futures)
    down to one of its members (gs results.py `PortfolioPath`)."""

    def __init__(self, path):
        self._path = (path,) if isinstance(path, int) else tuple(path)

    def __repr__(self) -> str:
        return repr(self._path)

    def __iter__(self):
        return iter(self._path)

    def __len__(self) -> int:
        return len(self._path)

    def __add__(self, other: "PortfolioPath") -> "PortfolioPath":
        return PortfolioPath(self._path + other._path)

    def __eq__(self, other) -> bool:
        return self._path == other._path

    def __hash__(self) -> int:
        return hash(self._path)

    @property
    def path(self) -> tuple:
        return self._path

    def __call__(self, target, rename_to_parent: Optional[bool] = False):
        """Walk `target` along the path: a PRR by its `.futures`, a portfolio by its `.priceables`,
        anything else by `[]`. Intermediate futures are evaluated; the last one is returned as is.
        With `rename_to_parent`, a sub-portfolio target is a copy renamed to its parent's name."""
        parent = None
        path = list(self._path)
        while path:
            elem = path.pop(0)
            parent = target if len(self) - len(path) > 1 else None
            if isinstance(target, PortfolioRiskResult):
                target = target.futures[elem]
            elif _is_portfolio(target):
                target = target.priceables[elem]
            else:
                target = target[elem]
            if isinstance(target, PricingFuture) and path:
                target = target.result()
        if rename_to_parent and parent is not None and getattr(parent, "name", None) and _is_portfolio(target):
            target = copy.copy(target)
            target.name = parent.name
        return target


def _all_paths(portfolio) -> tuple:
    """gs `Portfolio.all_paths`: every leaf, level by level, each level's direct leaves first."""
    paths: tuple = ()
    stack = [(None, portfolio)]
    while stack:
        parent, p = stack.pop()
        for idx, child in enumerate(p.priceables):
            path = parent + PortfolioPath(idx) if parent is not None else PortfolioPath(idx)
            if _is_portfolio(child):
                stack.insert(0, (path, child))
            else:
                paths += (path,)
    return paths


def _find_paths(portfolio, key) -> tuple:
    """gs `Portfolio.paths`: this level's matches first (by name for a str, else by equality or by
    `.unresolved`), then each sub-portfolio's, prefixed."""
    if isinstance(key, str):
        own = [i for i, c in enumerate(portfolio.priceables) if key and c and getattr(c, "name", None) == key]
    else:
        own = [i for i, c in enumerate(portfolio.priceables) if c is key or c == key or getattr(c, "unresolved", None) == key]
    paths = tuple(PortfolioPath(i) for i in own)
    for i, child in enumerate(portfolio.priceables):
        if _is_portfolio(child):
            paths += tuple(PortfolioPath(i) + sub for sub in _find_paths(child, key))
    return paths


# ============================================================================= PortfolioRiskResult


class PortfolioRiskResult:
    """The result of a `Portfolio.calc(...)`. `futures` holds exactly one entry per direct child of
    `portfolio` (`portfolio.priceables` order; a sub-portfolio's entry holds a nested
    PortfolioRiskResult); a plain value is wrapped as `PricingFuture(value)`. `len` counts direct
    children, iteration yields leaf values in `all_paths` order, `to_frame` records are depth-first
    (gs's three orders, R10 section 5)."""

    def __init__(self, portfolio, risk_measures: Iterable, futures: Iterable):
        self.portfolio = portfolio
        self.risk_measures = tuple(risk_measures)
        self.futures = tuple(_as_future(f) for f in futures)

    # -- container protocol -----------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.futures)

    def __bool__(self) -> bool:
        return len(self) > 0

    def __iter__(self):
        return iter(self._results())

    def __repr__(self) -> str:
        name = getattr(self.portfolio, "name", None)
        return f"{self.risk_measures} Results" + (f" for {name}" if name else "") + f" ({len(self)})"

    def result(self):
        return self

    def done(self) -> bool:
        return True

    # -- paths and leaves ---------------------------------------------------------------------
    def _all_paths(self) -> tuple:
        return _all_paths(self.portfolio)

    def _leaf_futures(self) -> list:
        return [p(self.futures) for p in self._all_paths()]

    def _value(self, future, risk_measure=None):
        res = future.result()
        if risk_measure is None and len(self.risk_measures) == 1:
            risk_measure = self.risk_measures[0]
        return res[risk_measure] if risk_measure is not None and isinstance(res, (MultipleRiskMeasureResult, PortfolioRiskResult)) else res

    def _result(self, path: PortfolioPath, risk_measure=None):
        return self._value(path(self.futures), risk_measure)

    def _paths(self, items) -> tuple:
        if isinstance(items, int):
            return (PortfolioPath(items),)
        if isinstance(items, slice):
            return tuple(PortfolioPath(i) for i in range(len(self.futures))[items])
        paths = _find_paths(self.portfolio, items)
        unresolved = None if isinstance(items, str) else getattr(items, "unresolved", None)
        if not paths and unresolved is not None:
            # a resolved instrument looked up in a result over its unresolved form (gs also checks
            # the resolution context; pricebt's RiskKey carries none to compare)
            paths = _find_paths(self.portfolio, unresolved)
            if not paths:
                raise KeyError(f"{items} not in portfolio")
        return paths

    def _results(self, items=None):
        if items is None:
            return tuple(self._value(f) for f in self._leaf_futures())
        paths = self._paths(items)
        if not paths:
            raise KeyError(f"{items}")
        return self.subset(paths) if isinstance(items, slice) else self._result(paths[0])

    @property
    def dates(self) -> tuple:
        found = set().union(*(_future_dates(f) for f in self._leaf_futures()))
        try:
            return tuple(sorted(found))
        except TypeError:
            return tuple()

    # -- indexing -----------------------------------------------------------------------------
    def __getitem__(self, item):
        if _is_measures(item):
            return self._by_measures(item)
        if _is_dates(item):
            return self._by_date(item)
        if isinstance(item, PortfolioPath):
            # IR_RISK_DESIGN section 5.2 (gs's own __getitem__ raises KeyError for a path)
            return self._result(item)
        if isinstance(item, (list, tuple)) and len(item) > 0 and all(_is_instrument(i) for i in item):
            return self.subset(item)
        if isinstance(item, list) and len(item) == 1:
            return self._results(item[0])
        return self._results(item)

    def _by_measures(self, item) -> "PortfolioRiskResult":
        items = tuple(item) if isinstance(item, (list, tuple)) else (item,)
        if any(i not in self.risk_measures for i in items):
            raise ValueError(f"{item} not computed")
        if len(self.risk_measures) == 1:
            return self
        return PortfolioRiskResult(self.portfolio, items, [self._measure_future(f, item, items) for f in self.futures])

    @staticmethod
    def _measure_future(future, item, items) -> PricingFuture:
        sub = _sub_prr(future)
        if sub is not None:
            return PricingFuture(sub[item])
        if isinstance(future, _MultiMeasureFuture):
            if len(items) > 1:
                return _MultiMeasureFuture({m: future.future_for(m) for m in items}, future.instrument)
            one = future.future_for(items[0])
            return one if one is not None else PricingFuture(future.result()[items[0]])
        res = future.result()
        if not isinstance(res, MultipleRiskMeasureResult):
            return PricingFuture(res)
        if len(items) > 1:
            return PricingFuture(MultipleRiskMeasureResult(res.instrument, ((m, res[m]) for m in items)))
        return PricingFuture(res[items[0]])

    def _by_date(self, item) -> "PortfolioRiskResult":
        if not self.dates:
            raise RuntimeError("Can only index by date on historical results")
        futures = []
        for f in self.futures:
            res = f.result()
            if isinstance(res, (MultipleRiskMeasureResult, PortfolioRiskResult)):
                futures.append(PricingFuture(res[item]))
            elif isinstance(res, (pd.Series, pd.DataFrame)):
                futures.append(PricingFuture(_value_for_date(res, item)))
            else:
                raise RuntimeError("Can only index by date on historical results")
        return PortfolioRiskResult(self.portfolio, self.risk_measures, futures)

    def get(self, item, default=None):
        try:
            return self[item]
        except (KeyError, ValueError):
            return default

    def __contains__(self, item) -> bool:
        if _is_risk_measure(item):
            return item in self.risk_measures
        if isinstance(item, dt.date):
            return item in self.dates
        if not isinstance(item, (str, Priceable)):
            return False
        # pricebt DEV-P1: a match at any depth (gs's Portfolio.__contains__ stops at depth 1)
        return bool(_find_paths(self.portfolio, item))

    def subset(self, items: Iterable, name: Optional[str] = None) -> "PortfolioRiskResult":
        """A flat result over the given members (int, str, instrument or PortfolioPath), one future
        per matched path."""
        paths = tuple(chain.from_iterable((i,) if isinstance(i, PortfolioPath) else self._paths(i) for i in items))
        if len(paths) == 1 and _is_portfolio(paths[0](self.portfolio)):
            # pricebt DEV-R7: gs pairs that sub-portfolio with the single future of its path, so the
            # futures no longer line up with its children; return the nested result itself
            return paths[0](self.futures).result()
        portfolio = type(self.portfolio)(tuple(p(self.portfolio, rename_to_parent=True) for p in paths), name=name)
        return PortfolioRiskResult(portfolio, self.risk_measures, [p(self.futures) for p in paths])

    # -- combining ------------------------------------------------------------------------------
    def _first_date(self):
        leaves = self._leaf_futures()
        return _future_date(leaves[0]) if leaves else None

    def __add__(self, other: "PortfolioRiskResult") -> "PortfolioRiskResult":
        if not isinstance(other, PortfolioRiskResult):
            # pricebt DEV-R8: gs's int/float branch adds per future and always raises RuntimeError
            # ('... cannot be composed'); pricebt rejects a number up front
            raise ValueError("Can only add instances of PortfolioRiskResult")

        self_instruments = set(self.portfolio.all_instruments)
        other_instruments = set(other.portfolio.all_instruments)
        measures_overlap = not set(self.risk_measures).isdisjoint(other.risk_measures)
        # a single-date result's date is its first leaf's (gs), read without evaluating a LazyFuture
        self_dates = self.dates or (self._first_date(),)
        other_dates = other.dates or (other._first_date(),)
        dates_overlap = not set(self_dates).isdisjoint(other_dates)
        instruments_overlap = not self_instruments.isdisjoint(other_instruments)
        if measures_overlap and dates_overlap and instruments_overlap:
            raise ValueError("Results overlap on risk measures, instruments or dates")

        if self.portfolio is other.portfolio or self.portfolio == other.portfolio:
            portfolio = self.portfolio
            futures = [
                self._add_futures(fs, fo, other.risk_measures, priceable)
                for fs, fo, priceable in zip(self.futures, other.futures, self.portfolio.priceables)
            ]
        else:
            portfolio = self.portfolio + other.portfolio
            futures = list(self.futures) + list(other.futures)

        # pricebt DEV-E14: an ordered union (self's measures first, then other's new ones), not the
        # gs `set(chain(self.risk_measures, other.risk_measures))`, whose column order is non-deterministic.
        risk_measures = tuple(dict.fromkeys((*self.risk_measures, *other.risk_measures)))
        return PortfolioRiskResult(portfolio, risk_measures, futures)

    def _add_futures(self, fs, fo, other_measures, priceable) -> PricingFuture:
        """Same-portfolio `+` for one direct child: nested results add recursively; a leaf merges
        per measure, composing a measure both sides have over their dates (gs `_compose`), lazily."""
        sub_s, sub_o = _sub_prr(fs), _sub_prr(fo)
        if sub_s is not None and sub_o is not None:
            return PricingFuture(sub_s + sub_o)
        merged = _measure_futures(fs, self.risk_measures)
        for m, f in _measure_futures(fo, other_measures).items():
            merged[m] = _DeferredFuture(lambda a=merged[m], b=f: _compose(a.result(), b.result())) if m in merged else f
        return _MultiMeasureFuture(merged, priceable)

    def _map(self, fn) -> "PortfolioRiskResult":
        futures = []
        for f in self.futures:
            sub = _sub_prr(f)
            futures.append(PricingFuture(sub._map(fn)) if sub is not None else _DeferredFuture(lambda f=f: _map_value(f.result(), fn)))
        return PortfolioRiskResult(self.portfolio, self.risk_measures, futures)

    def __mul__(self, other) -> "PortfolioRiskResult":
        _check_number(other)
        return self._map(lambda v: v * other)

    # -- transform / aggregate --------------------------------------------------------------------
    def transform(self, risk_transformation=None):
        if risk_transformation is None:
            return self
        if len(self.risk_measures) > 1:
            return MultipleRiskMeasureResult(self.portfolio, ((r, self[r].transform(risk_transformation)) for r in self.risk_measures))
        if len(self.risk_measures) == 1:
            paths = self._all_paths()
            values = risk_transformation.apply(tuple(self._value(p(self.futures)) for p in paths))
            return self._rebuild(dict(zip((p.path for p in paths), values)))
        return self

    def _rebuild(self, by_path: dict, prefix: tuple = ()) -> "PortfolioRiskResult":
        # pricebt DEV-R7: gs rebuilds one future per LEAF under the nested portfolio, so a nested
        # result's futures no longer line up with its children; pricebt keeps the tree (a nested
        # result per sub-portfolio)
        futures = []
        for idx, f in enumerate(self.futures):
            sub = _sub_prr(f)
            futures.append(PricingFuture(sub._rebuild(by_path, prefix + (idx,)) if sub is not None else by_path[prefix + (idx,)]))
        return PortfolioRiskResult(self.portfolio, self.risk_measures, futures)

    def aggregate(self, allow_mismatch_risk_keys: bool = False, allow_heterogeneous_types: bool = False):
        """gs `aggregate`: several measures -> an MRMR of per-measure aggregates (the flags are not
        forwarded, as in gs); one measure -> `aggregate_results` over the leaves (its error, type,
        unit and key checks). Lazy bucketed leaves are summed per group (DESIGN.md section 8.2),
        the checks applied to the group results. A table is concatenated with an
        `instrument_name` column (IR_RISK_DESIGN R2-15). No leaves -> `FloatWithInfo(0.0)`."""
        from pricebt.risk.core import aggregate_results  # lazy: core imports this module (R2-22)

        if len(self.risk_measures) > 1:
            return MultipleRiskMeasureResult(self.portfolio, ((r, self[r].aggregate()) for r in self.risk_measures))

        paths = self._all_paths()
        leaves = [p(self.futures) for p in paths]
        if leaves and all(isinstance(f, LazyFuture) and f.group_key is not None for f in leaves):
            groups: dict = {}
            for f in leaves:
                groups.setdefault(f.group_key, []).append(f)
            values = [group[0].service.group_aggregate(key, [f.member for f in group]) for key, group in groups.items()]
        else:
            values = [self._value(f) for f in leaves]
            if not values:
                return FloatWithInfo(0.0)
            if all(_is_table(v) for v in values):
                values = [_named_table(v, _label(p(self.portfolio), p.path[-1])) for v, p in zip(values, paths)]
        return aggregate_results(values, allow_mismatch_risk_keys=allow_mismatch_risk_keys, allow_heterogeneous_types=allow_heterogeneous_types)

    # -- tabular view -------------------------------------------------------------------------------
    def _to_records(self, display_options=None, drop_tables: bool = False) -> list:
        """Records depth-first in `futures` order, each labelled from its own path.

        # pricebt DEV-R6: gs zips these depth-first leaf records against Portfolio._to_records'
        # breadth-first labels, which mislabels every row once a level mixes leaves and
        # sub-portfolios; pricebt labels each leaf from its own path.
        """
        records: list = []

        def walk(prr: PortfolioRiskResult, labels: dict, depth: int) -> None:
            for idx, (priceable, future) in enumerate(zip(prr.portfolio.priceables, prr.futures)):
                sub = _sub_prr(future)
                if sub is not None:
                    walk(sub, {**labels, f"portfolio_name_{depth}": _label(priceable, idx)}, depth + 1)
                else:
                    extra = {**labels, "instrument_name": _label(priceable, idx)}
                    records.extend(_records_of(prr._value(future), extra, display_options, drop_tables))

        walk(self, {}, 0)
        return records

    def to_frame(self, values="default", index="default", columns="default", aggfunc="sum", display_options=None):
        """gs `to_frame`: `(None, None, None)` gives the raw records; all three `'default'` gives
        gs's default layout (bucketed/table results indexed by their labels, else a pivot chosen by
        dates/measures/nesting); anything else pivots as asked. Rows and columns keep first
        appearance. `display_options.show_na` keeps empty frames and unsupported values. None when
        there is nothing to show."""
        raw = values is None and index is None and columns is None
        default = values == "default" and index == "default" and columns == "default"
        # pricebt DEV-R11: a pivot on `value` leaves table results out (they have no value column)
        value_pivot = not raw and not default and values in ("default", "value", ["value"])
        records = self._to_records(display_options, drop_tables=value_pivot)
        if not records:
            return None
        ori_df = pd.DataFrame.from_records(records)
        if "risk_measure" not in ori_df.columns:
            ori_df["risk_measure"] = self.risk_measures[0]
        df_cols = list(ori_df.columns)
        cols_except_value = [c for c in df_cols if c != "value"]
        ori_df[cols_except_value] = ori_df[cols_except_value].fillna("N/A")  # shallower leaves, scalar rows next to bucket rows

        has_dt = "dates" in df_cols
        other_cols = sorted(c for c in df_cols if "portfolio" in c) + ["instrument_name", "risk_measure"] + (["dates"] if has_dt else [])
        val_cols = [c for c in df_cols if c not in other_cols]
        if "value" in val_cols:
            val_cols = [c for c in val_cols if c != "value"] + ["value"]
        ori_df = ori_df[other_cols + val_cols]

        if raw:
            return ori_df
        if default:
            if "mkt_type" in df_cols or self._has_table():
                return ori_df.set_index(other_cols)
            portfolio_names = [c for c in df_cols if "portfolio_name_" in c]
            names = portfolio_names + ["instrument_name"]
            multi = len(self.risk_measures) > 1
            if has_dt:
                values, index, columns = "value", "dates", names + (["risk_measure"] if multi else [])
            elif not multi and max(len(p) for p in self._all_paths()) > 1:
                values, index, columns = "value", portfolio_names, "instrument_name"
            else:
                values, index, columns = "value", names, "risk_measure"
        else:
            values = "value" if values in ("default", ["value"]) else values
        return _pivot_to_frame(ori_df, values, index, columns, aggfunc)

    def _has_table(self) -> bool:
        return any(_is_table(v) or (isinstance(v, MultipleRiskMeasureResult) and any(_is_table(x) for x in v.values())) for v in self._results())


def _named_table(table, name: str) -> DataFrameWithInfo:
    out = table.copy()
    out.insert(0, "instrument_name", name)
    return _as_table(out, table.risk_key, table.unit)
