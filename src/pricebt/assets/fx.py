"""FX config loading and rate evaluation (DESIGN.md section 4.5).

`FxEvaluator` is a thin, uncached wrapper: the cache and the equal-currency/None-handling rules
described in DESIGN.md section 4.5 belong to the pricing service (a later phase), not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from ..errors import ConfigError
from . import yamlio
from .config import _check_unknown_keys, _compile_expr, _fail, _require_mapping, _require_str
from .namespace import AssetNamespace

_TOP_KEYS = {"schema_version", "fx", "description", "imports", "code", "rate"}


@dataclass(frozen=True)
class FxConfig:
    """One loaded, validated, compiled FX config."""

    name: str
    source: str
    description: Optional[str]
    rate_expr: str
    imports_src: str
    code_src: str
    imports_code: Any = field(repr=False)
    code_code: Any = field(repr=False)
    _compiled: Dict[str, Tuple[Any, str]] = field(repr=False)

    def code(self, key: str) -> Any:
        return self._compiled[key][0]

    def expr_src(self, key: str) -> str:
        return self._compiled[key][1]


def load_fx(source: Union[str, Path, Mapping[str, Any]]) -> FxConfig:
    """Load, validate and compile one FX config from a file path or an in-memory mapping."""
    if isinstance(source, Mapping):
        raw_top: Any = source
        src_label = "<mapping>"
    else:
        path = Path(source)
        raw_top = yamlio.load_file(path)
        src_label = str(path)

    raw = _require_mapping(raw_top, None, "<root>")
    _check_unknown_keys(raw, _TOP_KEYS, None, "")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        _fail(None, "schema_version", f"must be 1, got {schema_version!r}")

    name = _require_str(raw.get("fx"), None, "fx")

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        _fail(name, "description", f"must be a string, got {description!r}")

    imports_src = raw.get("imports") or ""
    if not isinstance(imports_src, str):
        _fail(name, "imports", f"must be a string, got {imports_src!r}")
    code_src = raw.get("code") or ""
    if not isinstance(code_src, str):
        _fail(name, "code", f"must be a string, got {code_src!r}")
    imports_code = _compile_expr(imports_src, name, "imports", "exec")
    code_code = _compile_expr(code_src, name, "code", "exec")

    rate_expr = _require_str(raw.get("rate"), name, "rate")
    rate_code = _compile_expr(rate_expr, name, "rate", "eval")

    return FxConfig(
        name=name,
        source=src_label,
        description=description,
        rate_expr=rate_expr,
        imports_src=imports_src,
        code_src=code_src,
        imports_code=imports_code,
        code_code=code_code,
        _compiled={"rate": (rate_code, rate_expr)},
    )


class FxEvaluator:
    """Evaluates one FX config's `rate` expression. No caching (see the module docstring)."""

    def __init__(self, fx_config: FxConfig):
        self._namespace = AssetNamespace(fx_config)

    def rate(self, base: str, quote: str, date: Any) -> Optional[float]:
        """Quote-currency units per 1 base-currency unit on `date`, or `None` if unavailable."""
        return self._namespace.eval("rate", base=base, quote=quote, pricebt_date=date)
