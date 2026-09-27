"""Toys for the Z3 guards (contains no tests): a swap whose method and argument names do NOT match the schema, behind bindings.

`Mismatched` wraps a reference-stack swap. Two of its method names CROSS the schema (the schema's `gamma` is this class's `convexity` and the schema's `convexity`
layer is its `gamma`, so a by-name resolution would silently return the wrong quantity) and every other schema name exists as a DECOY that raises when called. Arguments
are named `zzz` and `qqq`. Every call is recorded in `CALLS` so a test can prove that a binding passed exactly the declared keyword arguments and nothing else.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

import pandas as pd

from pricebt.contracts.spec import Built, Kit
from pricebt.testing import refstack as R

CALLS: List[Tuple[str, Tuple[Any, ...], Tuple[str, ...]]] = []  # (method, positional args, sorted keyword names)


def _decoy(name: str):
    def decoy(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"the decoy `{name}` was called: a method is reached only through a binding, never because its name matches a schema name")

    decoy.__name__ = name
    return decoy


def _recorded(name: str, fn):
    def call(self: Any, *args: Any, **kwargs: Any) -> Any:
        CALLS.append((name, args, tuple(sorted(kwargs))))
        return fn(self, *args, **kwargs)

    call.__name__ = name
    return call


class Mismatched:
    """A reference-stack swap behind names that do not match the schema."""

    asset_class = "swap"

    def __init__(self, inner: Any):
        self._inner = inner

    price_it = _recorded("price_it", lambda self, zzz: self._inner.value(ctx=zzz))
    one_bp = _recorded("one_bp", lambda self, zzz, qqq: self._inner.dv01(ctx=zzz, tenors=qqq))
    level_of = _recorded("level_of", lambda self, zzz: self._inner.rate(ctx=zzz))
    # a library-shaped return (a pandas Series, not a dict): the binding's reducer turns it into the plain dict[str, float] the schema asks for
    buckets = _recorded("buckets", lambda self, zzz, qqq: pd.Series(self._inner.delta_ladder(ctx=zzz, tenors=qqq)))
    lay_a = _recorded("lay_a", lambda self, zzz: self._inner.carry(ctx=zzz))
    lay_b = _recorded("lay_b", lambda self, zzz: self._inner.roll(ctx=zzz))
    lay_c = _recorded("lay_c", lambda self, zzz: self._inner.delta(ctx=zzz))
    # the crossed pair: the schema's `gamma` is `convexity` here, and the schema's `convexity` layer is `gamma` here
    convexity = _recorded("convexity", lambda self, zzz, qqq: self._inner.gamma(ctx=zzz, tenors=qqq))
    gamma = _recorded("gamma", lambda self, zzz: self._inner.convexity(ctx=zzz))
    # decoys: right schema name, never bound
    value = _decoy("value")
    dv01 = _decoy("dv01")
    rate = _decoy("rate")
    delta_ladder = _decoy("delta_ladder")
    carry = _decoy("carry")
    roll = _decoy("roll")
    delta = _decoy("delta")


TENORS = list(R.DEFAULT_TENORS)


def _b(method: str, **kw: Any) -> Dict[str, Any]:
    return {"target": {"method": method}, "kwargs": {"zzz": "@ctx", **kw}}


MISMATCHED_BIND: Dict[str, Any] = {
    "value": _b("price_it"),
    "dv01": _b("one_bp", qqq=TENORS),
    "gamma": _b("convexity", qqq=TENORS),  # the schema's gamma is this class's `convexity`
    "rate": _b("level_of"),
    "delta_ladder": {**_b("buckets", qqq=TENORS), "keys": TENORS, "reduce": "series_to_tenor_dict"},
    "carry": _b("lay_a"),
    "roll": _b("lay_b"),
    "delta": _b("lay_c"),
    "convexity": _b("gamma"),  # the schema's convexity layer is this class's `gamma`
}


def mismatched_factory(pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
    built = R.swap_factory(pricer, ts, terms=terms, conventions=conventions)
    return Built(Mismatched(built.obj), built.terms)


mismatched_swap = Kit(factory=mismatched_factory, asset_class="swap", default_bind=MISMATCHED_BIND, cls=Mismatched, doc="a swap whose names cross the schema's (Z3 guard toy)")
