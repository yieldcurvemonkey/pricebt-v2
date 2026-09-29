"""Transformer (base), GenericResultWithInfoTransformer, ResultWithInfoAggregator.

Ported from gs_quant.risk.transform (Apache-2.0; see NOTICE). `risk.results` never imports this
module (Import DAG, DESIGN.md section 3.2): `PortfolioRiskResult.transform` duck-types
`risk_transformation.apply(...)` instead.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

from pricebt.risk.results import DataFrameWithInfo, FloatWithInfo, SeriesWithInfo


class Transformer:
    """Base class of a `PortfolioRiskResult.transform(...)` argument."""

    def apply(self, data: Iterable[Any], *args, **kwargs):
        raise NotImplementedError


class GenericResultWithInfoTransformer(Transformer):
    """`apply(data, *args, **kwargs)` is `fn(data, *args, **kwargs)` (gs)."""

    def __init__(self, fn: Callable):
        self._fn = fn

    def apply(self, data, *args, **kwargs):
        return self._fn(data, *args, **kwargs)


class ResultWithInfoAggregator(Transformer):
    """Collapses each per-instrument result to a single `FloatWithInfo`: a scalar stays a scalar; a
    bucketed/historical result becomes `FloatWithInfo(result[risk_col].sum(), unit=..., risk_key=...)`,
    optionally filtered first by `filter_coord` (a `{column: value}` equality mask, `DataFrameWithInfo`
    only). Returns a **list**, one entry per input result, not a scalar."""

    def __init__(self, risk_col: str = "value", filter_coord: Optional[dict] = None):
        self.risk_col = risk_col
        self.filter_coord = filter_coord

    def apply(self, results: Iterable[Any], *args, **kwargs) -> list:
        out = []
        for r in results:
            if isinstance(r, DataFrameWithInfo):
                df = r
                if self.filter_coord:
                    mask = None
                    for k, v in self.filter_coord.items():
                        col_mask = df[k] == v
                        mask = col_mask if mask is None else (mask & col_mask)
                    df = df[mask]
                out.append(FloatWithInfo(df[self.risk_col].sum(), risk_key=r.risk_key, unit=r.unit))
            elif isinstance(r, SeriesWithInfo):
                out.append(FloatWithInfo(r.sum(), risk_key=r.risk_key, unit=r.unit))
            elif isinstance(r, FloatWithInfo):
                out.append(r)
            else:
                out.append(float(r))
        return out
