"""Loading and validating one asset config (DESIGN.md section 4): parses the mapping (via
`assets.yamlio`), checks it against the section 4.2 schema, compiles every expression string, and
returns a frozen `AssetConfig`. Nothing here executes a config's `imports` or `code` -- that is
`AssetNamespace`'s job (`assets.namespace`), done lazily and only at first evaluation.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd

from ..errors import ConfigError
from . import yamlio

# ------------------------------------------------------------------------------------ schema constants

UNITS = {"ccy", "ccy_per_bp", "ccy_per_bp2", "bp", "pct", "decimal", "number", "date"}
EXTENSIVE_UNITS = {"ccy", "ccy_per_bp", "ccy_per_bp2", "number"}
RETURNS_VALUES = {"scalar", "buckets"}
BUILD_ON_VALUES = {"each_market", "resolve_date"}
LABEL_KEYS = {"mkt_type", "mkt_asset", "mkt_class", "mkt_quoting_style"}
_ISO_CODE_RE = re.compile(r"^[A-Za-z]{3}$")

_TOP_KEYS = {
    "schema_version", "asset", "description", "instrument", "match", "currency", "defaults",
    "imports", "code", "market", "resolve", "trade", "functions", "portfolio_functions",
    "attributes", "size_attribute", "risk_measures",
}
_MARKET_KEYS = {"expr", "key"}
_RESOLVE_KEYS = {"expr"}
_TRADE_KEYS = {"expr", "build_on"}
_FUNCTION_KEYS = {"expr", "unit", "currency", "scale_with_quantity"}
_PORTFOLIO_FUNCTION_KEYS = _FUNCTION_KEYS | {"returns", "labels"}
_RISK_MEASURE_DICT_KEYS = {"scalar", "bucketed"}

_RESERVED_EVAL_KEYS = {"market", "resolve", "trade"}


# ------------------------------------------------------------------------------------ dataclasses


@dataclass(frozen=True)
class FunctionSpec:
    """One `functions:` or `portfolio_functions:` entry (DESIGN.md section 4.2)."""

    expr: str
    unit: str
    currency: Optional[str]
    scale_with_quantity: bool
    returns: str
    labels: Dict[str, str]


@dataclass(frozen=True)
class RiskMapping:
    """One `risk_measures:` entry: a gs risk-measure name mapped to a scalar function (a
    `functions:` entry, or a `portfolio_functions:` entry with `returns: scalar`) and/or a
    bucketed one (a `portfolio_functions:` entry with `returns: buckets`)."""

    scalar: Optional[str] = None
    bucketed: Optional[str] = None


@dataclass(frozen=True)
class AssetConfig:
    """One loaded, validated, fully-compiled asset config. Immutable and self-contained: every
    expression string it holds has already been compiled, but none has been executed."""

    name: str
    source: str
    description: Optional[str]
    instrument: str
    match: Dict[str, Any]
    currency: str
    defaults: Dict[str, Any]
    market_expr: str
    market_key: str
    resolve_expr: Optional[str]
    trade_expr: Optional[str]
    build_on: str
    functions: Dict[str, FunctionSpec]
    portfolio_functions: Dict[str, FunctionSpec]
    attributes: Dict[str, str]
    size_attribute: Optional[str]
    risk_measures: Dict[str, RiskMapping]
    imports_src: str
    code_src: str
    imports_code: Any = field(repr=False)
    code_code: Any = field(repr=False)
    _compiled: Dict[str, Tuple[Any, str]] = field(repr=False)

    def code(self, key: str) -> Any:
        """The compiled (`"eval"` mode) code object for `key` -- a function/portfolio-function/
        attribute name, or `"market"`/`"resolve"`/`"trade"`."""
        return self._compiled[key][0]

    def expr_src(self, key: str) -> str:
        """The original expression source text for `key`, for error messages."""
        return self._compiled[key][1]


# ------------------------------------------------------------------------------------ small helpers


def _suggest(bad: str, candidates: Iterable[str]) -> str:
    matches = difflib.get_close_matches(bad, list(candidates), n=1)
    return f"; did you mean {matches[0]!r}?" if matches else ""


def _fail(name: Optional[str], key: str, message: str, candidates: Optional[Iterable[str]] = None, bad: Optional[str] = None) -> None:
    suggestion = _suggest(bad, candidates) if candidates is not None and bad is not None else ""
    raise ConfigError(message + suggestion, asset=name, key=key)


def _check_unknown_keys(mapping: Mapping[str, Any], allowed: Sequence[str], name: Optional[str], key_prefix: str) -> None:
    for k in mapping:
        if k not in allowed:
            full_key = f"{key_prefix}.{k}" if key_prefix else k
            _fail(name, full_key, f"unknown key {k!r}", candidates=allowed, bad=k)


def _require_mapping(value: Any, name: Optional[str], key: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(name, key, f"must be a mapping, got {type(value).__name__}")
    return value


def _require_str(value: Any, name: Optional[str], key: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(name, key, f"must be a non-empty string, got {value!r}")
    return value


def _iso_code_ok(code: str) -> bool:
    return bool(_ISO_CODE_RE.match(code))


def _compile_expr(src: str, name: str, key: str, mode: str) -> Any:
    try:
        return compile(src, f"<asset {name}:{key}>", mode)
    except SyntaxError as exc:
        _fail(name, key, f"syntax error at line {exc.lineno}: {exc.msg} (in {src[:200]!r})")


# ------------------------------------------------------------------------------------ section parsers


def _parse_function_entry(raw: Any, name: str, key: str, allowed_keys: Sequence[str], *, is_portfolio: bool) -> FunctionSpec:
    d = _require_mapping(raw, name, key)
    _check_unknown_keys(d, allowed_keys, name, key)
    expr = _require_str(d.get("expr"), name, f"{key}.expr")
    unit = d.get("unit")
    if unit not in UNITS:
        _fail(name, f"{key}.unit", f"unit {unit!r} is not one of {sorted(UNITS)}")
    currency = d.get("currency")
    if currency is not None and not (isinstance(currency, str) and _iso_code_ok(currency)):
        _fail(name, f"{key}.currency", f"currency {currency!r} is not a plausible ISO code")
    scale_with_quantity = d.get("scale_with_quantity")
    if scale_with_quantity is None:
        scale_with_quantity = unit in EXTENSIVE_UNITS
    elif not isinstance(scale_with_quantity, bool):
        _fail(name, f"{key}.scale_with_quantity", f"must be a bool, got {scale_with_quantity!r}")
    returns = "scalar"
    labels: Dict[str, str] = {}
    if is_portfolio:
        returns = d.get("returns", "scalar")
        if returns not in RETURNS_VALUES:
            _fail(name, f"{key}.returns", f"returns {returns!r} is not one of {sorted(RETURNS_VALUES)}")
        labels_raw = d.get("labels", {})
        labels_raw = _require_mapping(labels_raw, name, f"{key}.labels")
        _check_unknown_keys(labels_raw, LABEL_KEYS, name, f"{key}.labels")
        labels = dict(labels_raw)
    return FunctionSpec(expr=expr, unit=unit, currency=currency, scale_with_quantity=scale_with_quantity, returns=returns, labels=labels)


def _risk_target_kind(target: str, functions: Mapping[str, "FunctionSpec"], portfolio_functions: Mapping[str, "FunctionSpec"]) -> Optional[str]:
    """Classify a name referenced from `risk_measures:` (DESIGN.md section 8.1 rule 4): a
    `functions:` entry is always scalar; a `portfolio_functions:` entry is scalar or bucketed by
    its own `returns`. Returns `None` if `target` is neither."""
    if target in functions:
        return "scalar"
    pf = portfolio_functions.get(target)
    if pf is not None:
        return "bucketed" if pf.returns == "buckets" else "scalar"
    return None


def _parse_risk_measure(raw: Any, name: str, key: str, functions: Mapping[str, "FunctionSpec"], portfolio_functions: Mapping[str, "FunctionSpec"]) -> RiskMapping:
    if isinstance(raw, str):
        # A bare string normalises to {scalar: raw} or {bucketed: raw} per its own kind.
        kind = _risk_target_kind(raw, functions, portfolio_functions)
        if kind == "bucketed":
            return RiskMapping(scalar=None, bucketed=raw)
        if kind == "scalar":
            return RiskMapping(scalar=raw, bucketed=None)
        _fail(name, f"{key}.scalar", f"references unknown function {raw!r}", candidates=set(functions) | set(portfolio_functions), bad=raw)

    d = _require_mapping(raw, name, key)
    _check_unknown_keys(d, _RISK_MEASURE_DICT_KEYS, name, key)
    scalar = d.get("scalar")
    bucketed = d.get("bucketed")
    if scalar is None and bucketed is None:
        _fail(name, key, "must give 'scalar' and/or 'bucketed'")
    # The explicit dict form must accept exactly what a bare string normalises to (rule 4), so a
    # `scalar:` reference may also be a `returns: scalar` portfolio function, and `bucketed:` is
    # valid only for a `returns: buckets` one -- never a `functions:` entry.
    if scalar is not None and _risk_target_kind(scalar, functions, portfolio_functions) != "scalar":
        scalar_candidates = set(functions) | {n for n, f in portfolio_functions.items() if f.returns == "scalar"}
        _fail(name, f"{key}.scalar", f"references unknown function {scalar!r}", candidates=scalar_candidates, bad=scalar)
    if bucketed is not None and _risk_target_kind(bucketed, functions, portfolio_functions) != "bucketed":
        bucketed_candidates = {n for n, f in portfolio_functions.items() if f.returns == "buckets"}
        _fail(name, f"{key}.bucketed", f"references unknown portfolio function {bucketed!r}", candidates=bucketed_candidates, bad=bucketed)
    return RiskMapping(scalar=scalar, bucketed=bucketed)


# ------------------------------------------------------------------------------------ load_asset


def load_asset(source: Union[str, Path, Mapping[str, Any]]) -> AssetConfig:
    """Load, validate and compile one asset config from a file path or an in-memory mapping."""
    if isinstance(source, Mapping):
        raw_top: Any = source
        src_label = "<mapping>"
        default_name = None
    else:
        path = Path(source)
        raw_top = yamlio.load_file(path)
        src_label = str(path)
        default_name = path.stem

    raw = _require_mapping(raw_top, default_name, "<root>")
    name = raw.get("asset", default_name) or default_name
    _check_unknown_keys(raw, _TOP_KEYS, name, "")
    if not name:
        raise ConfigError("config has no 'asset' id and no file path to default it from", asset=default_name, key="asset")

    schema_version = raw.get("schema_version")
    if schema_version != 1:
        _fail(name, "schema_version", f"must be 1, got {schema_version!r}")

    description = raw.get("description")
    if description is not None and not isinstance(description, str):
        _fail(name, "description", f"must be a string, got {description!r}")

    instrument = _require_str(raw.get("instrument"), name, "instrument")

    match = _require_mapping(raw.get("match", {}), name, "match")

    currency = _require_str(raw.get("currency"), name, "currency")
    if not _iso_code_ok(currency):
        _fail(name, "currency", f"currency {currency!r} is not a plausible ISO code")

    defaults = _require_mapping(raw.get("defaults", {}), name, "defaults")

    imports_src = raw.get("imports") or ""
    if not isinstance(imports_src, str):
        _fail(name, "imports", f"must be a string, got {imports_src!r}")
    code_src = raw.get("code") or ""
    if not isinstance(code_src, str):
        _fail(name, "code", f"must be a string, got {code_src!r}")
    imports_code = _compile_expr(imports_src, name, "imports", "exec")
    code_code = _compile_expr(code_src, name, "code", "exec")

    market = _require_mapping(raw.get("market"), name, "market")
    _check_unknown_keys(market, _MARKET_KEYS, name, "market")
    market_expr = _require_str(market.get("expr"), name, "market.expr")
    market_key = market.get("key", name)
    if not isinstance(market_key, str) or not market_key:
        _fail(name, "market.key", f"must be a non-empty string, got {market_key!r}")

    resolve_raw = raw.get("resolve")
    resolve_expr: Optional[str] = None
    if resolve_raw is not None:
        resolve = _require_mapping(resolve_raw, name, "resolve")
        _check_unknown_keys(resolve, _RESOLVE_KEYS, name, "resolve")
        resolve_expr = _require_str(resolve.get("expr"), name, "resolve.expr")

    trade_raw = raw.get("trade")
    trade_expr: Optional[str] = None
    build_on = "each_market"
    if trade_raw is not None:
        trade = _require_mapping(trade_raw, name, "trade")
        _check_unknown_keys(trade, _TRADE_KEYS, name, "trade")
        trade_expr = _require_str(trade.get("expr"), name, "trade.expr")
        build_on = trade.get("build_on", "each_market")
        if build_on not in BUILD_ON_VALUES:
            _fail(name, "trade.build_on", f"build_on {build_on!r} is not one of {sorted(BUILD_ON_VALUES)}")

    functions_raw = _require_mapping(raw.get("functions"), name, "functions")
    if not functions_raw:
        _fail(name, "functions", "must declare at least one function")
    functions = {fname: _parse_function_entry(fspec, name, f"functions.{fname}", _FUNCTION_KEYS, is_portfolio=False) for fname, fspec in functions_raw.items()}

    portfolio_functions_raw = _require_mapping(raw.get("portfolio_functions", {}), name, "portfolio_functions")
    portfolio_functions = {
        fname: _parse_function_entry(fspec, name, f"portfolio_functions.{fname}", _PORTFOLIO_FUNCTION_KEYS, is_portfolio=True) for fname, fspec in portfolio_functions_raw.items()
    }

    reserved_clash = _RESERVED_EVAL_KEYS & (functions.keys() | portfolio_functions.keys())
    if reserved_clash:
        _fail(name, "functions", f"name(s) {sorted(reserved_clash)} collide with the reserved keys {sorted(_RESERVED_EVAL_KEYS)}")
    both = functions.keys() & portfolio_functions.keys()
    if both:
        _fail(name, "portfolio_functions", f"name(s) {sorted(both)} are declared in both functions and portfolio_functions")

    attributes_raw = _require_mapping(raw.get("attributes", {}), name, "attributes")
    attributes = {aname: _require_str(aexpr, name, f"attributes.{aname}") for aname, aexpr in attributes_raw.items()}
    attr_clash = _RESERVED_EVAL_KEYS & attributes.keys()
    if attr_clash:
        _fail(name, "attributes", f"name(s) {sorted(attr_clash)} collide with the reserved keys {sorted(_RESERVED_EVAL_KEYS)}")
    fn_attr_clash = attributes.keys() & (functions.keys() | portfolio_functions.keys())
    if fn_attr_clash:
        _fail(name, "attributes", f"name(s) {sorted(fn_attr_clash)} are declared as both a function and an attribute")

    size_attribute = raw.get("size_attribute")
    if size_attribute is not None:
        if not isinstance(size_attribute, str) or size_attribute not in attributes:
            _fail(name, "size_attribute", f"size_attribute {size_attribute!r} is not a key of attributes", candidates=attributes, bad=str(size_attribute))

    risk_measures_raw = _require_mapping(raw.get("risk_measures"), name, "risk_measures")
    if "Price" not in risk_measures_raw:
        _fail(name, "risk_measures", "must include 'Price'", candidates=risk_measures_raw, bad="Price")
    risk_measures = {rname: _parse_risk_measure(rspec, name, f"risk_measures.{rname}", functions, portfolio_functions) for rname, rspec in risk_measures_raw.items()}

    compiled: Dict[str, Tuple[Any, str]] = {
        "market": (_compile_expr(market_expr, name, "market", "eval"), market_expr),
    }
    if resolve_expr is not None:
        compiled["resolve"] = (_compile_expr(resolve_expr, name, "resolve", "eval"), resolve_expr)
    if trade_expr is not None:
        compiled["trade"] = (_compile_expr(trade_expr, name, "trade", "eval"), trade_expr)
    for fname, fspec in functions.items():
        compiled[fname] = (_compile_expr(fspec.expr, name, fname, "eval"), fspec.expr)
    for fname, fspec in portfolio_functions.items():
        compiled[fname] = (_compile_expr(fspec.expr, name, fname, "eval"), fspec.expr)
    for aname, aexpr in attributes.items():
        compiled[aname] = (_compile_expr(aexpr, name, aname, "eval"), aexpr)

    return AssetConfig(
        name=name,
        source=src_label,
        description=description,
        instrument=instrument,
        match=dict(match),
        currency=currency,
        defaults=dict(defaults),
        market_expr=market_expr,
        market_key=market_key,
        resolve_expr=resolve_expr,
        trade_expr=trade_expr,
        build_on=build_on,
        functions=functions,
        portfolio_functions=portfolio_functions,
        attributes=attributes,
        size_attribute=size_attribute,
        risk_measures=risk_measures,
        imports_src=imports_src,
        code_src=code_src,
        imports_code=imports_code,
        code_code=code_code,
        _compiled=compiled,
    )


# ------------------------------------------------------------------------------------ validate_resolved

_PLAIN_SCALAR_TYPES = (str, int, float, bool, type(None), dt.date, dt.datetime, pd.Timestamp)


def _is_plain_scalar(v: Any) -> bool:
    return isinstance(v, _PLAIN_SCALAR_TYPES)


def validate_resolved(resolved: Mapping[str, Any]) -> None:
    """Raise `ConfigError` unless every value in `resolved` is hashable plain data: `str`, `int`,
    `float`, `bool`, `None`, `date`, `datetime`, `pandas.Timestamp`, or a `tuple` of those
    (DESIGN.md section 4.4)."""
    if not isinstance(resolved, Mapping):
        raise ConfigError(f"must be a mapping, got {type(resolved).__name__}")
    for k, v in resolved.items():
        if isinstance(v, tuple):
            bad = [item for item in v if not _is_plain_scalar(item)]
            if bad:
                raise ConfigError(f"[{k!r}] tuple element {bad[0]!r} ({type(bad[0]).__name__}) is not plain data", key=str(k))
            continue
        if not _is_plain_scalar(v):
            raise ConfigError(f"[{k!r}] value {v!r} ({type(v).__name__}) is not hashable plain data", key=str(k))
