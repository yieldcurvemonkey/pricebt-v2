"""Portfolio.

A tree of priceables (instruments and nested Portfolios), matching the gs constructor forms,
de-duplication rules and traversal helpers (DESIGN.md section 5, research/04 section 5). `calc`
and `resolve` delegate to the same engine seams as `pricebt.instrument.Instrument`
(`pricebt.markets._engine_calc` / `_engine_resolve`).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable, List, Optional, Tuple

import pandas as pd

from pricebt.base import Priceable
from pricebt.markets import _engine_calc, _engine_resolve
from pricebt.risk import DollarPrice, Price

__all__ = ["Portfolio"]


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
        direct = list(self.portfolios)
        nested: List["Portfolio"] = []
        for c in direct:
            nested.extend(c.all_portfolios)
        return tuple(direct + nested)

    # --- container protocol ---------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self._priceables)

    def __iter__(self):
        return iter(self._priceables)

    def paths(self, key: Any) -> Tuple[Any, ...]:
        if isinstance(key, str):
            # gs: `if i and i.name` (markets/portfolio.py Portfolio.priceables setter, which backs
            # gs's name index) -- a falsy child (e.g. an empty, len==0 nested Portfolio) or a falsy
            # name (None or "") never matches by name. This is the single predicate every
            # name-lookup caller (`__getitem__`, `__contains__`, `pop`) routes through via `paths`.
            pred = lambda c: bool(c) and bool(getattr(c, "name", None)) and c.name == key  # noqa: E731
        elif isinstance(key, Priceable):
            pred = lambda c: c == key or getattr(c, "unresolved", None) == key  # noqa: E731
        else:
            raise ValueError(f"cannot match Portfolio children by {key!r}")
        result: List[Any] = []

        def walk(p: "Portfolio") -> None:
            for c in p._priceables:
                if pred(c):
                    result.append(c)
                if isinstance(c, Portfolio):
                    walk(c)

        walk(self)
        return tuple(result)

    def __getitem__(self, item: Any):
        # gs: `values = tuple(...); return values[0] if len(values) == 1 else values` -- no match
        # is `()`, not KeyError, and exactly one match (scalar OR list key) unwraps to the bare
        # object rather than a 1-tuple (verified against gs_quant markets/portfolio.py
        # Portfolio.__getitem__).
        if isinstance(item, (int, slice)):
            return self._priceables[item]
        if isinstance(item, list):
            out: List[Any] = []
            for k in item:
                r = self[k]
                out.extend(r) if isinstance(r, tuple) else out.append(r)
            values: Tuple[Any, ...] = tuple(out)
        else:
            values = self.paths(item)
        return values[0] if len(values) == 1 else values

    def __contains__(self, item: Any) -> bool:
        if isinstance(item, (str, Priceable)):
            return len(self.paths(item)) > 0
        return False

    def _leaf_paths(self, prefix: Tuple[int, ...] = ()) -> Iterable[Tuple[int, ...]]:
        """Index-tuples to every leaf of this Portfolio, depth-first (R04 section 5 `all_paths`)."""
        for i, c in enumerate(self._priceables):
            path = prefix + (i,)
            if isinstance(c, Portfolio):
                yield from c._leaf_paths(path)
            else:
                yield path

    def _at_path(self, path: Tuple[int, ...]) -> Any:
        target: Any = self
        for idx in path:
            target = target[idx]  # Portfolio[int] -> child; a leaf isn't subscriptable -> TypeError
        return target

    def __eq__(self, other: Any) -> bool:
        # gs: for every leaf path of self, the same path indexed into other must match (names are
        # not compared at the portfolio level). This is asymmetric -- a shorter/shallower self can
        # equal a longer/deeper other but not vice versa (research/04 section 5, verified against
        # gs_quant markets/portfolio.py Portfolio.__eq__/all_paths).
        if not isinstance(other, Portfolio):
            return False
        for path in self._leaf_paths():
            try:
                if self._at_path(path) != other._at_path(path):
                    return False
            except (IndexError, TypeError):
                return False
        return True

    def __hash__(self) -> int:
        # gs XORs in hash(self.__id), its Marquee persistence id -- always None for an in-memory
        # (never network-persisted) Portfolio, so pricebt hashes None in its place (R04 section 5:
        # id/quote_id "Not needed (network)"). Using id(self) here would break equal portfolios
        # hashing equal, since two distinct `==` Portfolio objects would then hash differently.
        h = hash(self.name) ^ hash(None)
        for c in self._priceables:
            h ^= hash(c)
        return h

    def __repr__(self) -> str:
        return f"Portfolio({self.name})" if self.name else "Portfolio"

    # --- mutation --------------------------------------------------------------------------------
    def __add__(self, other: "Portfolio") -> "Portfolio":
        if not isinstance(other, Portfolio):
            raise ValueError("Can only add instances of Portfolio")
        return Portfolio(self._priceables + other._priceables)

    def append(self, priceables: Any) -> None:
        # gs: `(priceables,) if isinstance(priceables, PriceableImpl) else tuple(priceables)` --
        # an iterable of priceables is appended as multiple direct children, not one nested list.
        # Param renamed from `p` to `priceables` to match gs's signature (MUST-2: same constructor/
        # method parameter names, so a keyword call `append(priceables=...)` works either way).
        new = (priceables,) if isinstance(priceables, Priceable) else tuple(priceables)
        self._set_priceables(self._priceables + new)

    def extend(self, portfolio: Iterable[Any]) -> None:
        # Param renamed from `it` to `portfolio` to match gs's signature (MUST-2, same as append above).
        self._set_priceables(self._priceables + tuple(portfolio))

    def pop(self, item: Any) -> Any:
        # gs rebuilds from `self.instruments` (DIRECT leaf children only), not `all_instruments`
        # -- a nested Portfolio child is dropped whole rather than flattened, and the popped
        # priceable is returned (verified against gs_quant markets/portfolio.py Portfolio.pop).
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
        return _engine_calc(self, risk_measure_or_iterable, fn)

    def price(self, currency: Any = None):
        return self.calc(Price(currency=currency) if currency else Price)

    def dollar_price(self):
        return self.calc(DollarPrice)

    # --- reporting ------------------------------------------------------------------------------
    def to_frame(self, mappings: Optional[dict] = None) -> pd.DataFrame:
        # gs's `mappings` (markets/portfolio.py Portfolio.to_frame): a str value aliases an
        # existing column under a new name, a callable value is applied row-wise (`df.apply(...,
        # axis=1)`) to compute a new column. Ported as-is -- cheap and gs's own semantics.
        rows: List[dict] = []

        def walk(p: "Portfolio", parent_name: Any) -> None:
            for c in p._priceables:
                if isinstance(c, Portfolio):
                    walk(c, c.name)
                else:
                    # pricebt DEV-I2: as_dict() carries a quantity_ column (see instrument/__init__.py)
                    # that gs's own static-data dict doesn't have.
                    d = dict(c.as_dict())
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
