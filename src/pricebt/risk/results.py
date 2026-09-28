"""RiskKey, FloatWithInfo, SeriesWithInfo, DataFrameWithInfo, ErrorValue, MultipleRiskMeasureResult,
PricingFuture, LazyFuture, PortfolioRiskResult.

Ported from gs_quant.risk (`__init__.py`) and gs_quant.risk.results (Apache-2.0; see NOTICE), lines
600-973 of the 2.1.17 source read as the reference for `PortfolioRiskResult`'s semantics. pricebt's
version is deliberately narrower than gs's: no scenarios, no `PortfolioPath`/weakref machinery, no
excel-shaped indexing -- only what DESIGN.md section 8.2 lists. It dispatches on duck typing
(`.priceables`, `.all_instruments`) so it never imports `pricebt.instrument` or
`pricebt.markets.portfolio` (Import DAG, DESIGN.md section 3.2): a "portfolio" here is anything
exposing `.priceables` (direct children, in order) and `.all_instruments` (leaves), and a `+`
operator when portfolios differ.
"""
from __future__ import annotations

import datetime as dt
from collections import namedtuple
from typing import Any, Iterable, Optional

import pandas as pd

RiskKey = namedtuple("RiskKey", ["provider", "date", "market", "params", "scenario", "risk_measure"])

_BUCKET_COLUMNS = ("mkt_type", "mkt_asset", "mkt_class", "mkt_point", "mkt_quoting_style")


class FloatWithInfo(float):
    """A scalar risk value. `+` requires an equal `.unit`; `repr` appends it, e.g. `1500.0 (USD)`."""

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
        return FloatWithInfo(float(self) + float(other), risk_key=self.risk_key, unit=self.unit or other_unit)

    __radd__ = __add__

    def __repr__(self) -> str:
        if self.unit:
            return f"{float(self)} ({next(iter(self.unit))})"
        return f"{float(self)}"


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
    """A bucketed (vector) result. Always has exactly the six columns `mkt_type, mkt_asset,
    mkt_class, mkt_point, mkt_quoting_style, value`; a missing label is `''`, never NaN."""

    @property
    def _constructor(self):
        return DataFrameWithInfo

    @property
    def _constructor_sliced(self):
        return SeriesWithInfo

    @property
    def raw_value(self) -> pd.DataFrame:
        return pd.DataFrame(self)


class ErrorValue:
    """`(risk_key, error)`; `.raw_value` is always `None`."""

    def __init__(self, risk_key: Optional[RiskKey], error):
        self.risk_key = risk_key
        self.error = error
        self.raw_value = None

    def __repr__(self) -> str:
        return f"ErrorValue({self.error!r})"


def make_bucketed_frame(buckets: dict, labels: Optional[dict] = None, risk_key: Optional[RiskKey] = None, unit: Optional[dict] = None) -> DataFrameWithInfo:
    """Build a `DataFrameWithInfo` from a portfolio function's returned `{mkt_point: value}` dict and
    an asset config's static `labels` (DESIGN.md section 4.2). `mkt_point` is used exactly as
    returned (already `;`-joined by the config's own expression if multi-dimensional) -- pricebt
    never parses it. Row order is the dict's insertion order.

    # pricebt DEV-R5: gs sorts bucketed rows with sort_risk/point_sort_order (asset-class regexes
    # over mkt_point, relative to today's date); pricebt keeps the config's own bucket order instead
    # (first appearance), because point labels are opaque strings here, never parsed by pricebt.
    """
    labels = labels or {}
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


def combine_bucketed_frames(frames: Iterable[pd.DataFrame], risk_key: Optional[RiskKey] = None, unit: Optional[dict] = None) -> DataFrameWithInfo:
    """Concatenate bucketed frames and sum by bucket, in first-appearance order.

    # pricebt DEV-R5: groupby(sort=False) keeps first-appearance order (groups in the order they were
    # produced, then buckets in each group's own dict order) instead of gs's point_sort_order.
    """
    frames = list(frames)
    if not frames:
        return DataFrameWithInfo(pd.DataFrame(columns=list(_BUCKET_COLUMNS) + ["value"]), risk_key=risk_key, unit=unit)
    combined = pd.concat([pd.DataFrame(f) for f in frames], ignore_index=True)
    grouped = combined.groupby(list(_BUCKET_COLUMNS), sort=False, dropna=False, as_index=False)["value"].sum()
    return DataFrameWithInfo(grouped, risk_key=risk_key or frames[0].risk_key, unit=unit or getattr(frames[0], "unit", None))


class MultipleRiskMeasureResult(dict):
    """A `{measure: value}` result for one instrument."""

    def transform(self, risk_transformation) -> "MultipleRiskMeasureResult":
        return MultipleRiskMeasureResult((measure, risk_transformation.apply((value,))[0]) for measure, value in self.items())


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


class LazyFuture(PricingFuture):
    """A deferred per-instrument bucketed value, grouped for a single evaluation across the group's
    members (DESIGN.md section 8.2). `service.group_aggregate(group_key, members)` is called once per
    distinct `group_key` by `PortfolioRiskResult.aggregate()`, not by this class: `.result()` runs
    only this instrument's own `thunk` (the single-trade value), memoised, and never evaluates the
    group. `service` is the `PricingService` instance that created it, captured at construction, so a
    late `.result()` (after a `reset()` or after the pricing session has exited) is a cache miss on
    that service, not an error."""

    def __init__(self, thunk, group_key, member, service):
        super().__init__()
        self.thunk = thunk
        self.group_key = group_key
        self.member = member
        self.service = service
        self._evaluated = False

    def result(self):
        if not self._evaluated:
            try:
                self._result = self.thunk()
            except Exception as exc:  # noqa: BLE001 - memoised like any other future
                self._exception = exc
            self._evaluated = True
        return super().result()


def _as_future(value) -> PricingFuture:
    return value if hasattr(value, "result") and hasattr(value, "done") else PricingFuture(value)


def _is_risk_measure(item) -> bool:
    """Duck-types `pricebt.risk.RiskMeasure` without importing it: `pricebt.risk` imports this
    module (for `FloatWithInfo`/etc.), so the reverse import would be circular (Import DAG,
    DESIGN.md section 3.2)."""
    return hasattr(item, "asset_class") and hasattr(item, "measure_type") and hasattr(item, "parameters")


def _dates_of(result) -> set:
    if isinstance(result, PortfolioRiskResult):
        return set(result.dates)
    if isinstance(result, MultipleRiskMeasureResult):
        out: set = set()
        for v in result.values():
            out |= _dates_of(v)
        return out
    if isinstance(result, (pd.Series, pd.DataFrame)):
        try:
            if len(result.index) and all(isinstance(i, dt.date) for i in result.index):
                return set(result.index)
        except TypeError:
            pass
    return set()


class _MultiMeasureFuture(PricingFuture):
    """Lazily merges several single-measure futures (one per risk measure, for one instrument) into
    a `MultipleRiskMeasureResult`, without evaluating any of them until `.result()` is called. Used
    only by `PortfolioRiskResult.__add__` (same-portfolio branch): building this instead of eagerly
    calling `.result()` on each side is what lets a `LazyFuture` slice back out of the sum still
    ungrouped, via `future_for()`, so `PortfolioRiskResult.aggregate()` on `(self + other)[measure]`
    still calls `service.group_aggregate(...)` once per group rather than once per instrument."""

    def __init__(self, measure_futures: dict):
        super().__init__()
        self._measure_futures = measure_futures
        self._evaluated = False

    def result(self):
        if not self._evaluated:
            self._result = MultipleRiskMeasureResult((m, f.result()) for m, f in self._measure_futures.items())
            self._evaluated = True
        return super().result()

    def future_for(self, measure):
        return self._measure_futures.get(measure)


def _measure_futures(future: PricingFuture, measures: tuple) -> dict:
    """`{measure: future}` for one future, without calling `.result()` (see `_MultiMeasureFuture`)."""
    if isinstance(future, _MultiMeasureFuture):
        return dict(future._measure_futures)
    if len(measures) == 1:
        return {measures[0]: future}
    raise TypeError("expected a multi-measure future for more than one risk measure")


def _aggregate_scalars(values) -> FloatWithInfo:
    total = 0.0
    unit = None
    risk_key = None
    for v in values:
        v_unit = getattr(v, "unit", None)
        if unit is None:
            unit = v_unit
            risk_key = getattr(v, "risk_key", None)
        elif v_unit is not None and v_unit != unit:
            raise ValueError("FloatWithInfo unit mismatch")
        total += float(v)
    return FloatWithInfo(total, risk_key=risk_key, unit=unit)


class PortfolioRiskResult:
    """The result of a `Portfolio.calc(...)`. `futures` holds exactly one entry per direct child of
    `portfolio` (`portfolio.priceables` order); a plain value is wrapped as `PricingFuture(value)`."""

    def __init__(self, portfolio, risk_measures: Iterable, futures: Iterable):
        self.portfolio = portfolio
        self.risk_measures = tuple(risk_measures)
        self.futures = tuple(_as_future(f) for f in futures)

    # -- container protocol -----------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.futures)

    def __bool__(self) -> bool:
        return len(self) > 0

    def _result_for(self, future: PricingFuture):
        res = future.result()
        if len(self.risk_measures) == 1 and isinstance(res, MultipleRiskMeasureResult):
            return res[self.risk_measures[0]]
        return res

    def __iter__(self):
        return (self._result_for(f) for f in self.futures)

    @property
    def dates(self) -> tuple:
        found: set = set()
        for f in self.futures:
            if isinstance(f, LazyFuture):
                continue
            found |= _dates_of(f.result())
        try:
            return tuple(sorted(found))
        except TypeError:
            return tuple()

    # -- indexing -----------------------------------------------------------------------------
    def __getitem__(self, item):
        if _is_risk_measure(item):
            return self._by_measure(item)
        if isinstance(item, dt.date):
            return self._by_date(item)
        if isinstance(item, int):
            return self._result_for(self.futures[item])
        return self._by_instrument_or_name(item)

    def _by_measure(self, item) -> "PortfolioRiskResult":
        if item not in self.risk_measures:
            raise ValueError(f"{item} not computed")
        if len(self.risk_measures) == 1:
            return self
        futures = []
        for f in self.futures:
            if isinstance(f, _MultiMeasureFuture):
                sub = f.future_for(item)
                futures.append(sub if sub is not None else PricingFuture(f.result()[item]))
                continue
            res = f.result()
            if isinstance(res, (PortfolioRiskResult, MultipleRiskMeasureResult)):
                futures.append(PricingFuture(res[item]))
            else:
                futures.append(PricingFuture(res))
        return PortfolioRiskResult(self.portfolio, (item,), futures)

    @staticmethod
    def _series_item(series, item):
        """pre-existing bug fix (out of P3.5's own scope, but confirmed blocking): `.loc[item]`
        scalar indexing returns a bare element, not something carrying `.unit`/`.risk_key` --
        SeriesWithInfo/DataFrameWithInfo store those on the SERIES itself (`_metadata`), not per
        element, and `_historical_instrument_value` (assets/pricing.py) builds one FloatWithInfo
        per date and then loses each one's `.unit` the moment it lands in a float64-backed pandas
        Series. Re-wrap a scalar extraction with the series' own unit/risk_key; a per-date bucketed
        (DataFrame) entry already carries its own metadata and is returned as is.
        """
        value = series.loc[item]
        if isinstance(value, (pd.Series, pd.DataFrame)):
            return value
        return FloatWithInfo(value, unit=getattr(series, "unit", None), risk_key=getattr(series, "risk_key", None))

    def _by_date(self, item: dt.date):
        if not self.dates:
            raise RuntimeError("Can only index by date on historical results")
        futures = []
        for f in self.futures:
            res = f.result()
            if isinstance(res, PortfolioRiskResult):
                futures.append(PricingFuture(res[item]))
            elif isinstance(res, MultipleRiskMeasureResult):
                # pre-existing bug fix (out of P3.5's own scope, but confirmed blocking: a
                # MultipleRiskMeasureResult is a dict keyed by RiskMeasure, not by date, so `res[item]`
                # with a date `item` always raised KeyError. This is the shape a multi-measure
                # HistoricalPricingContext.calc() on a plain Instrument produces (assets/pricing.py
                # _historical_instrument_value: one SeriesWithInfo per measure) -- exactly what
                # GenericEngine's HedgeActionImpl/generic_engine.py hit via `p.results[d][p.risk]`
                # whenever more than one risk measure is requested (i.e. essentially always, since
                # the hedge's own risk and the price measure are both always present). Index each
                # measure's per-date Series/DataFrame instead of the dict itself.
                if not all(isinstance(v, (pd.Series, pd.DataFrame)) and item in v.index for v in res.values()):
                    raise RuntimeError("Can only index by date on historical results")
                futures.append(
                    PricingFuture(MultipleRiskMeasureResult((m, self._series_item(v, item)) for m, v in res.items()))
                )
            elif isinstance(res, (pd.Series, pd.DataFrame)) and item in res.index:
                futures.append(PricingFuture(self._series_item(res, item)))
            else:
                raise RuntimeError("Can only index by date on historical results")
        return PortfolioRiskResult(self.portfolio, self.risk_measures, futures)

    def _by_instrument_or_name(self, item):
        name = item if isinstance(item, str) else getattr(item, "name", None)
        for priceable, future in zip(self.portfolio.priceables, self.futures):
            if priceable is item or (name is not None and getattr(priceable, "name", None) == name):
                return self._result_for(future)
        unresolved = getattr(item, "unresolved", None)
        if unresolved is not None:
            return self._by_instrument_or_name(unresolved)
        raise KeyError(str(item))

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
        try:
            self._by_instrument_or_name(item)
            return True
        except KeyError:
            return False

    # -- combining ------------------------------------------------------------------------------
    def __add__(self, other: "PortfolioRiskResult") -> "PortfolioRiskResult":
        if not isinstance(other, PortfolioRiskResult):
            raise ValueError("Can only add instances of PortfolioRiskResult")

        self_instruments = set(self.portfolio.all_instruments)
        other_instruments = set(other.portfolio.all_instruments)
        measures_overlap = not set(self.risk_measures).isdisjoint(other.risk_measures)
        self_dates = self.dates or (None,)
        other_dates = other.dates or (None,)
        dates_overlap = not set(self_dates).isdisjoint(other_dates)
        instruments_overlap = not self_instruments.isdisjoint(other_instruments)
        if measures_overlap and dates_overlap and instruments_overlap:
            raise ValueError("Results overlap on risk measures, instruments or dates")

        if self.portfolio == other.portfolio:
            portfolio = self.portfolio
            futures = [
                _MultiMeasureFuture({**_measure_futures(fs, self.risk_measures), **_measure_futures(fo, other.risk_measures)})
                for fs, fo in zip(self.futures, other.futures)
            ]
        else:
            portfolio = self.portfolio + other.portfolio
            futures = list(self.futures) + list(other.futures)

        # pricebt DEV-E14: an ordered union (self's measures first, then other's new ones), not the
        # gs `set(chain(self.risk_measures, other.risk_measures))`, whose column order is non-deterministic.
        risk_measures = tuple(dict.fromkeys((*self.risk_measures, *other.risk_measures)))
        return PortfolioRiskResult(portfolio, risk_measures, futures)

    # -- transform / aggregate --------------------------------------------------------------------
    def transform(self, risk_transformation=None):
        if risk_transformation is None:
            return self
        if len(self.risk_measures) > 1:
            return MultipleRiskMeasureResult((r, self[r].transform(risk_transformation)) for r in self.risk_measures)
        if len(self.risk_measures) == 1:
            vals = risk_transformation.apply(tuple(self))
            return PortfolioRiskResult(self.portfolio, self.risk_measures, [PricingFuture(v) for v in vals])
        return self

    def _leaf_futures(self) -> list:
        out = []
        for f in self.futures:
            if isinstance(f, LazyFuture):
                out.append(f)
                continue
            res = f.result()
            if isinstance(res, PortfolioRiskResult):
                out.extend(res._leaf_futures())
            else:
                out.append(f)
        return out

    def aggregate(self, allow_mismatch_risk_keys: bool = False, allow_heterogeneous_types: bool = False):
        if len(self.risk_measures) > 1:
            return MultipleRiskMeasureResult((r, self[r].aggregate()) for r in self.risk_measures)

        leaves = self._leaf_futures()
        if leaves and all(isinstance(f, LazyFuture) and f.group_key is not None for f in leaves):
            groups: dict = {}
            for f in leaves:
                groups.setdefault(f.group_key, []).append(f)
            frames = [group[0].service.group_aggregate(key, [f.member for f in group]) for key, group in groups.items()]
            return combine_bucketed_frames(frames)

        values = [f.result() for f in leaves] if leaves else list(self)
        if not values:
            return FloatWithInfo(0.0)
        if isinstance(values[0], (pd.Series, pd.DataFrame)):
            return combine_bucketed_frames(values)
        return _aggregate_scalars(values)

    # -- tabular view -------------------------------------------------------------------------------
    def to_frame(self, values="default", index="default", columns="default", aggfunc="sum"):
        records = []
        for priceable, future in zip(self.portfolio.priceables, self.futures):
            name = getattr(priceable, "name", None) or str(priceable)
            res = future.result()
            items = res.items() if isinstance(res, MultipleRiskMeasureResult) else zip(self.risk_measures, [res])
            for measure, val in items:
                if isinstance(val, pd.DataFrame):
                    v = val["value"].sum() if "value" in val.columns else val.sum().sum()
                elif isinstance(val, pd.Series):
                    v = val.sum()
                else:
                    v = val
                records.append({"instrument_name": name, "risk_measure": measure, "value": v})
        df = pd.DataFrame.from_records(records, columns=["instrument_name", "risk_measure", "value"])
        if values is None and index is None and columns is None:
            return df
        values = "value" if values == "default" else values
        index = "instrument_name" if index == "default" else index
        columns = "risk_measure" if columns == "default" else columns
        return df.pivot_table(values=values, index=index, columns=columns, aggfunc=aggfunc)

    def result(self):
        return self

    def done(self) -> bool:
        return True
