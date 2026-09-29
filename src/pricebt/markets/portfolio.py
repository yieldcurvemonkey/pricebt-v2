"""Portfolio and Grid.

A tree of priceables (instruments and nested Portfolios), matching the gs constructor forms,
de-duplication rules, paths and traversal helpers (DESIGN.md section 5, research/04 section 5,
research/10 section 1). `calc` and `resolve` delegate to the same engine seams as
`pricebt.instrument.Instrument` (`pricebt.markets._engine_calc` / `_engine_resolve`). gs's
server-side persistence surface (`get`, the `from_*` loaders, `save*`, `market`) raises
NotSupportedError; `id` and `quote_id` are always None.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from typing import Any, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from pricebt import instrument as _instrument
from pricebt.base import Priceable
from pricebt.common import AssetClass, AssetType
from pricebt.errors import NotSupportedError
from pricebt.instrument import ConfigInstrument
from pricebt.instrument._gs_fields import GS_FIELDS
from pricebt.markets import _engine_calc, _engine_resolve
from pricebt.risk import DollarPrice, Price
from pricebt.risk.results import PortfolioPath, _all_paths, _find_paths

__all__ = ["Portfolio", "Grid"]

# (asset_class, type) -> generated instrument class: gs `Instrument.from_dict`'s lookup (R2-31)
_CLASS_BY_KIND = {(c.asset_class, c.type_): c for c in (getattr(_instrument, n) for n in GS_FIELDS)}
# from_frame columns that are never a ConfigInstrument term
_NOT_TERMS = frozenset({"asset_class", "type", "$type", "name", "quantity_", "pricebt_asset", "instrument", "portfolio"})
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _server_side(name: str) -> NotSupportedError:
    return NotSupportedError(f"Portfolio.{name} is GS server-side (portfolio persistence); pricebt portfolios live in memory only")


def _plain(value: Any) -> Any:
    """A frame cell as gs `from_dict` decodes it: numpy scalars to Python, ISO date strings to dates."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        return dt.date.fromisoformat(value)
    return value


class Portfolio(Priceable):
    def __init__(self, priceables: Any = (), name: Any = None):
        self.name = name
        if priceables is None:
            priceables = ()
        if isinstance(priceables, Mapping):
            priceables = dict(priceables)
            for k, v in priceables.items():
                v.name = k
            children: Tuple[Any, ...] = tuple(priceables.values())
        elif isinstance(priceables, Priceable):
            # A single instrument or Portfolio wraps as ONE child -- never flattened.
            children = (priceables,)
        else:
            children = tuple(priceables)
        self._set_priceables(children)

    def _set_priceables(self, children: Tuple[Any, ...]) -> None:
        self._priceables: Tuple[Any, ...] = children

    # --- children ------------------------------------------------------------------------------
    @property
    def priceables(self) -> Tuple[Any, ...]:
        return self._priceables

    @priceables.setter
    def priceables(self, value: Any) -> None:
        Portfolio.__init__(self, value, self.name)

    @priceables.deleter
    def priceables(self) -> None:
        self._set_priceables(())

    @property
    def instruments(self) -> Tuple[Any, ...]:
        """Direct children that are leaves (not nested Portfolios), de-duplicated in order."""
        seen: List[Any] = []
        seen_set: set = set()
        for c in self._priceables:
            if isinstance(c, Portfolio) or c in seen_set:
                continue
            seen.append(c)
            seen_set.add(c)
        return tuple(seen)

    @property
    def all_instruments(self) -> Tuple[Any, ...]:
        """Direct leaves, then those of every nested Portfolio, de-duplicated (identity and name,
        via the leaf's own `__eq__`/`__hash__`)."""
        direct = [c for c in self._priceables if not isinstance(c, Portfolio)]
        nested: List[Any] = []
        for c in self._priceables:
            if isinstance(c, Portfolio):
                nested.extend(c.all_instruments)
        seen: List[Any] = []
        seen_set: set = set()
        for x in direct + nested:
            if x not in seen_set:
                seen.append(x)
                seen_set.add(x)
        return tuple(seen)

    @property
    def portfolios(self) -> Tuple["Portfolio", ...]:
        return tuple(c for c in self._priceables if isinstance(c, Portfolio))

    @property
    def all_portfolios(self) -> Tuple["Portfolio", ...]:
        # pricebt DEV-P1: every nested level, de-duplicated; gs's loop skips each popped portfolio
        # it has already listed, so it only ever returns the direct sub-portfolios
        direct = list(self.portfolios)
        nested: List["Portfolio"] = []
        for c in direct:
            nested.extend(c.all_portfolios)
        return tuple(dict.fromkeys(direct + nested))

    @property
    def all_paths(self) -> Tuple[PortfolioPath, ...]:
        """A path to every leaf, level by level, each level's direct leaves first (gs)."""
        return _all_paths(self)

    @property
    def id(self) -> None:
        return None  # gs: the server-side portfolio id

    @property
    def quote_id(self) -> None:
        return None  # gs: the server-side quote id

    # --- container protocol ---------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._priceables)

    def __iter__(self):
        return iter(self._priceables)

    def paths(self, key: Any) -> Tuple[PortfolioPath, ...]:
        """Paths to every member matching `key` (a name, or an instrument/Portfolio by equality or
        by its `.unresolved`): this level's matches first, then each sub-portfolio's (gs). A
        falsy child (an empty Portfolio) or name never matches by name (gs `if i and i.name`)."""
        if not isinstance(key, (str, Priceable)):
            raise ValueError("key must be a name or Instrument or Portfolio")
        return _find_paths(self, key)

    def __getitem__(self, item: Any):
        # gs: `values[0] if len(values) == 1 else values` -- no match is `()`, not KeyError, and
        # exactly one match (scalar OR list key) unwraps to the bare object
        if isinstance(item, (int, slice)):
            return self._priceables[item]
        if isinstance(item, PortfolioPath):
            return item(self, rename_to_parent=True)
        keys = item if isinstance(item, list) else (item,)
        values = tuple(self[p] for key in keys for p in self.paths(key))
        return values[0] if len(values) == 1 else values

    def __contains__(self, item: Any) -> bool:
        # pricebt DEV-P1: a match at any depth; gs only looks in itself and its all_portfolios,
        # which never descend below the direct sub-portfolios
        return isinstance(item, (str, Priceable)) and bool(self.paths(item))

    def subset(self, paths: Iterable[PortfolioPath], name=None):
        """The members at `paths` as a flat Portfolio named `name`; a single path to a
        sub-portfolio returns that sub-portfolio (gs)."""
        paths_tuple = tuple(paths)
        if len(paths_tuple) == 1 and isinstance(self[paths_tuple[0]], Portfolio):
            return self[paths_tuple[0]]
        return Portfolio(tuple(self[p] for p in paths_tuple), name=name)

    def __eq__(self, other: Any) -> bool:
        # gs: for every leaf path of self, the same path indexed into other must match (names are
        # not compared at the portfolio level). This is asymmetric -- a shorter/shallower self can
        # equal a longer/deeper other but not vice versa (research/04 section 5).
        if not isinstance(other, Portfolio):
            return False
        for path in self.all_paths:
            try:
                if path(self) != path(other):
                    return False
            except (IndexError, TypeError):  # other is shorter, or a leaf where self has a sub-portfolio
                return False
        return True

    def __hash__(self) -> int:
        # gs XORs in hash(self.__id), its server-side persistence id -- always None for an
        # in-memory Portfolio, so pricebt hashes None in its place (R04 section 5). Using id(self)
        # here would break equal portfolios hashing equal.
        h = hash(self.name) ^ hash(None)
        for c in self._priceables:
            h ^= hash(c)
        return h

    def __repr__(self) -> str:
        count = f"{len(self.all_instruments) if self._priceables else 0} instrument(s)"
        return f"Portfolio({self.name}, {count})" if self.name else f"Portfolio({count})"

    # --- mutation --------------------------------------------------------------------------------
    def __add__(self, other: "Portfolio") -> "Portfolio":
        if not isinstance(other, Portfolio):
            raise ValueError("Can only add instances of Portfolio")
        return Portfolio(self._priceables + other._priceables)

    def append(self, priceables: Any) -> None:
        # gs: `(priceables,) if isinstance(priceables, PriceableImpl) else tuple(priceables)` --
        # an iterable of priceables is appended as multiple direct children, not one nested list.
        new = (priceables,) if isinstance(priceables, Priceable) else tuple(priceables)
        self._set_priceables(self._priceables + new)

    def extend(self, portfolio: Iterable[Any]) -> None:
        self._set_priceables(self._priceables + tuple(portfolio))

    def pop(self, item: Any) -> Any:
        # gs rebuilds from `self.instruments` (DIRECT leaf children only), not `all_instruments`
        # -- a nested Portfolio child is dropped whole rather than flattened, and the popped
        # priceable is returned (gs markets/portfolio.py Portfolio.pop).
        priceable = self[item]
        self._set_priceables(tuple(x for x in self.instruments if x != priceable))
        return priceable

    def clone(self, clone_instruments: bool = False) -> "Portfolio":
        new_children = []
        for c in self._priceables:
            if isinstance(c, Portfolio):
                new_children.append(c.clone(clone_instruments=clone_instruments))
            elif clone_instruments:
                new_children.append(c.clone())
            else:
                new_children.append(c)
        return Portfolio(tuple(new_children), name=self.name)

    def scale(self, scaling: Any, in_place: bool = True):
        if in_place:
            for inst in self.all_instruments:
                inst.scale(scaling, True)
            return None
        return Portfolio([inst.scale(scaling, False) for inst in self.all_instruments])

    # --- pricing (DESIGN.md section 6.5) --------------------------------------------------------
    def resolve(self, in_place: bool = True):
        return _engine_resolve(self, in_place)

    def calc(self, risk_measure_or_iterable, fn=None):
        """A PortfolioRiskResult; `fn` is applied to each instrument's own result (gs)."""
        return _engine_calc(self, risk_measure_or_iterable, fn)

    def price(self, currency: Any = None):
        return self.calc(Price(currency=currency) if currency else Price)

    def dollar_price(self):
        return self.calc(DollarPrice)

    def market(self):
        raise _server_side("market")

    # --- frames ---------------------------------------------------------------------------------
    def to_frame(self, mappings: Optional[dict] = None) -> pd.DataFrame:
        # gs's `mappings` (markets/portfolio.py Portfolio.to_frame): a str value aliases an
        # existing column under a new name, a callable value is applied row-wise (`df.apply(...,
        # axis=1)`) to compute a new column.
        rows: List[dict] = []

        def walk(p: "Portfolio", parent_name: Any) -> None:
            for c in p._priceables:
                if isinstance(c, Portfolio):
                    walk(c, c.name)
                else:
                    # pricebt DEV-I2: as_dict() carries a quantity_ column (see instrument/__init__.py)
                    # that gs's own static-data dict doesn't have, and a leaf that names its asset
                    # adds a pricebt_asset column, so from_frame can rebuild it (IR_RISK_DESIGN R2-31).
                    d = dict(c.as_dict())
                    if c.pricebt_asset is not None:
                        d["pricebt_asset"] = c.pricebt_asset
                    d["instrument"] = c
                    d["portfolio"] = parent_name
                    rows.append(d)

        walk(self, self.name)
        df = pd.DataFrame(rows)
        df = df.set_index(["portfolio", "instrument"])
        front = [c for c in ("asset_class", "type") if c in df.columns]
        rest = sorted(c for c in df.columns if c not in front)
        df = df[front + rest]

        for key, value in (mappings or {}).items():
            if isinstance(value, str):
                df[key] = df[value]
            elif callable(value):
                df[key] = len(df) * [None]
                df[key] = df.apply(value, axis=1)
        return df

    def to_csv(self, csv_file: str, mappings: Optional[dict] = None, ignored_cols: Optional[list] = None):
        port_df = self.to_frame(mappings or {})
        port_df = port_df[np.setdiff1d(port_df.columns, ignored_cols or [])]  # gs: this also sorts the columns
        port_df = port_df.reset_index(drop=True)
        port_df.to_csv(csv_file)

    @classmethod
    def from_frame(cls, data: pd.DataFrame, mappings: dict = None):
        """One instrument per row with any non-null value (gs 2.1.17): the generated class named
        by its `asset_class` and `type` columns, built from its gs fields, else a ConfigInstrument
        when a `pricebt_asset` column names the asset (every other non-empty column a term);
        `quantity_` and `pricebt_asset` carry over. `mappings` maps an attribute to a column name
        or to a callable of the row (gs). The Portfolio is flat and unnamed."""
        mappings = mappings or {}
        mapped_columns = {v for v in mappings.values() if isinstance(v, str)}

        def get_value(row: pd.Series, attribute: str) -> Any:
            value = mappings.get(attribute, attribute)
            return _plain(value(row) if callable(value) else row.get(value))

        data = data[data.notnull().any(axis=1)]
        data = data.astype(object).where(data.notnull(), None)
        instruments = []
        for _, row in data.iterrows():
            kind = (get_value(row, "asset_class"), get_value(row, "type"))
            asset = get_value(row, "pricebt_asset")
            quantity = get_value(row, "quantity_")
            extras = {} if quantity is None else {"quantity_": quantity}
            if all(kind):
                inst_cls = _CLASS_BY_KIND.get((AssetClass(kind[0]), AssetType(kind[1])))
                if inst_cls is None:
                    raise ValueError(f"no pricebt instrument class has asset_class {kind[0]} and type {kind[1]}")
                values = ((f, get_value(row, f)) for f, _default, _tag in GS_FIELDS[inst_cls.__name__]["fields"])
                fields = {f: v for f, v in values if v is not None}  # an empty cell keeps the field's default
                instruments.append(inst_cls(**fields, pricebt_asset=asset, **extras))
            elif asset is not None:
                # pricebt: a ConfigInstrument row (gs has no such class). pandas names the
                # index column to_csv writes "Unnamed: 0"; that is never a term.
                names = [n for n in dict.fromkeys((*row.index, *mappings)) if n not in _NOT_TERMS and n not in mapped_columns and not str(n).startswith("Unnamed: ")]
                terms = {n: v for n in names if (v := get_value(row, n)) is not None}
                instruments.append(ConfigInstrument(asset, name=get_value(row, "name"), **extras, **terms))
            else:
                raise ValueError("Neither asset_class/type nor pricebt_asset specified")
        return cls(instruments)

    @classmethod
    def from_csv(cls, csv_file: str, mappings: Optional[dict] = None):
        data = pd.read_csv(csv_file, skip_blank_lines=True)
        reg = re.compile(r"\.[0-9]")
        dupelist = [re.sub(reg, "", word) for word in data.columns if reg.search(word)]
        if len(dupelist):
            raise ValueError(f"Duplicate column values {dupelist}")
        return cls.from_frame(data, mappings)

    # --- GS server-side persistence (gs signatures; never available in pricebt) ------------------
    @staticmethod
    def from_eti(eti: str):
        raise _server_side("from_eti")

    @staticmethod
    def from_book(book: str, book_type: str = "risk", activity_type: str = "position"):
        raise _server_side("from_book")

    @staticmethod
    def from_asset_id(asset_id: str, date=None):
        raise _server_side("from_asset_id")

    @staticmethod
    def from_asset_name(name: str):
        raise _server_side("from_asset_name")

    @classmethod
    def get(cls, portfolio_id: str = None, portfolio_name: str = None, query_instruments: Optional[bool] = False):
        raise _server_side("get")

    @classmethod
    def from_portfolio_id(cls, portfolio_id: str):
        raise _server_side("from_portfolio_id")

    @classmethod
    def from_portfolio_name(cls, name: str):
        raise _server_side("from_portfolio_name")

    @staticmethod
    def from_quote(quote_id: str):
        raise _server_side("from_quote")

    def save(self, overwrite: Optional[bool] = False):
        raise _server_side("save")

    def save_as_quote(self, overwrite: Optional[bool] = False) -> str:
        raise _server_side("save_as_quote")

    def save_to_shadowbook(self, name: str):
        raise _server_side("save_to_shadowbook")


class Grid(Portfolio):
    """A grid of similar instruments (gs `Grid`): one sub-portfolio per `y_values` entry, named by
    that value, each holding `priceable` cloned with `x_param` set to every `x_values` entry (the
    leaf named by it) and `y_param` set to the sub-portfolio's value."""

    def __init__(self, priceable: Priceable, x_param: str, x_values: Iterable, y_param: str, y_values: Iterable, name: Optional[str] = None):
        x_overrides = [{x_param: v, "name": v} for v in x_values]
        y_overrides = [{y_param: v} for v in y_values]
        super().__init__([Portfolio([priceable.clone(**{**x, **y}) for x in x_overrides], name=next(iter(y.values()))) for y in y_overrides], name)
