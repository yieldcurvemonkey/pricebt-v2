"""Test helper: build a toy TradeTemplate the way the tests used to build a template from a class and keyword arguments.

`toy_tpl(name, target, kwargs, layers=(), bind=None)` constructs `target(**kwargs)` and wraps it with the toy stack's default bindings
(`pricebt.testing.toys.toy_template`); `bind` adds or overrides bindings by schema name, so a test that needs a measure the toy does not bind says so.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from pricebt.contracts.spec import TradeTemplate
from pricebt.testing.toys import toy_template


def bind_method(method: str, **extra: Any) -> dict:
    """A binding of a schema name to a method taking the mark context."""
    return {"target": {"method": method}, "kwargs": {"ctx": "@ctx"}, **extra}


def toy_tpl(name: str, target: type, kwargs: Optional[Mapping[str, Any]] = None, layers: Sequence[str] = (), bind: Optional[Mapping[str, Any]] = None, pricer: str = "primary",
            pricers: Optional[Mapping[str, str]] = None, params: Optional[Mapping[str, Any]] = None, extra: Optional[Mapping[str, str]] = None,
            resolved: Sequence[str] = ()) -> TradeTemplate:
    return toy_template(target(**dict(kwargs or {})), name=name, layers=tuple(layers), bind=bind, pricer=pricer, pricers=pricers, params=params, extra=extra, resolved=resolved)
