"""Evaluating one position's bindings: `value`, measures (scalars, derived, dict ladders) and layers, plus the plain-dict vector maths.

Everything goes through `binding.call_binding`; nothing is looked up by a schema name. Measures the schema marks `derived` are computed by pricebt from
data it already holds (`value.pv` from the bound value, `terms.<name>` from the resolved terms) and take no binding. A vector measure (schema return type
`dict[...]`) is a plain `dict[str, float]` keyed by tenor strings `<int><D|W|M|Y>`; if the binding declares `keys` the result must have exactly those
keys (S4), otherwise every key must still parse as a tenor. No pandas type crosses this boundary.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence, Tuple

from ..errors import MeasureError, MeasureNotBound, MethodCallError
from ..pricable import as_valuation
from ..types import RESERVED_LAYERS
from .binding import TENOR, Binding, Env, call_binding
from .schema import NameSpec
from .spec import InstrumentSpec

_UNIT_ORDER = {"D": 1, "W": 7, "M": 31, "Y": 372}  # an ordering key for columns only (12M ties 1Y); not a pricing convention


def is_vector(measure: NameSpec) -> bool:
    return measure.returns.strip().lower().startswith("dict")


def tenor_sort_key(tenor: str) -> Tuple[int, str]:
    if not isinstance(tenor, str) or not TENOR.fullmatch(tenor):
        raise MeasureError(f"{tenor!r} is not a tenor (expected <int><D|W|M|Y>, e.g. '3M', '10Y')")
    return int(tenor[:-1]) * _UNIT_ORDER[tenor[-1]], tenor


# ------------------------------------------------------------------------------ vector maths (plain dicts)
def check_ladder(x: Any, tenors: Optional[Sequence[str]], name: str) -> Dict[str, float]:
    """Validate a ladder: a mapping of canonical tenor keys to finite floats, exactly `tenors` if given. Returns a fresh dict in tenor order."""
    if not isinstance(x, Mapping):
        raise MeasureError(f"measure {name!r} must return a dict[str, float] keyed by tenor, got {type(x).__name__} (bind a reducer such as series_to_tenor_dict)")
    out: Dict[str, float] = {}
    for k, v in x.items():
        if not isinstance(k, str) or not TENOR.fullmatch(k):
            raise MeasureError(f"measure {name!r}: key {k!r} is not a canonical tenor (upper-case <int><D|W|M|Y>, e.g. '3M', '10Y')")
        try:
            f = float(v)
        except (TypeError, ValueError) as e:
            raise MeasureError(f"measure {name!r}: value for {k!r} is not a number: {v!r}") from e
        if not math.isfinite(f):
            raise MeasureError(f"measure {name!r}: value for {k!r} is not finite ({f})")
        out[k] = f
    if tenors is not None:
        missing = [t for t in tenors if t not in out]
        extra = [k for k in out if k not in tenors]
        if missing or extra:
            raise MeasureError(f"measure {name!r} must return exactly the bound tenors {list(tenors)}: missing {missing}; extra {extra}")
    return {k: out[k] for k in sorted(out, key=tenor_sort_key)}


def scalar_of(x: Any) -> float:
    """A per-unit measure as one number: a ladder collapses to the sum over its buckets (the parallel dv01)."""
    if isinstance(x, Mapping):
        return float(sum(x.values()))
    return float(x)


def vector_of(x: Any, name: str = "") -> Dict[str, float]:
    if not isinstance(x, Mapping):
        raise MeasureError(f"measure {name!r} is not a vector (got {type(x).__name__}); vector measures return dict[str, float] keyed by tenor")
    return {str(k): float(v) for k, v in x.items()}


def vector_add(a: Mapping[str, float], b: Mapping[str, float], scale: float = 1.0) -> Dict[str, float]:
    """a + scale*b over the union of buckets (a missing bucket is 0), keeping a's order and appending b's new buckets in first-seen order."""
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0.0) + scale * v
    return out


def vector_scale(a: Mapping[str, float], s: float) -> Dict[str, float]:
    return {k: s * v for k, v in a.items()}


# ------------------------------------------------------------------------------ evaluation
def _binding(spec: InstrumentSpec, name: str, what: str) -> Binding:
    b = spec.bindings.get(name)
    if b is None:
        raise MeasureNotBound(f"{what} {name!r} is not bound for instrument {spec.name!r} (asset class {spec.asset_class!r}); bound names: {sorted(spec.bindings)}")
    return b


def evaluate_value(spec: InstrumentSpec, env: Env) -> Any:
    return call_binding(_binding(spec, "value", "method"), env)


def _ts(env: Env) -> Any:
    return getattr(env.ctx, "ts", None) if env.ctx is not None else getattr(env.pricer, "ts", None)



def evaluate_measure(spec: InstrumentSpec, name: str, env: Env, *, cache: Optional[MutableMapping[Any, Any]] = None) -> Any:
    """One per-unit measure of a built instrument: a float, or a dict[str, float] for a vector measure. Memoised in `cache` (returned dicts are copies)."""
    key = ("measure", name)
    if cache is not None and key in cache:
        got = cache[key]
        return dict(got) if isinstance(got, dict) else got
    ms = spec.schema.measures.get(name)
    if ms is not None and ms.derived:
        kind, _, field_name = ms.derived.partition(".")
        if kind == "value":
            out: Any = float(as_valuation(evaluate_value(spec, env), _ts(env)).pv)
        elif kind == "terms":
            if field_name not in env.terms:
                raise MeasureError(f"measure {name!r} derives from term {field_name!r}, which the instrument's resolved terms do not carry: {sorted(env.terms)}")
            out = float(env.terms[field_name])
        else:
            raise MeasureError(f"measure {name!r}: unknown derivation {ms.derived!r}")
    else:
        b = _binding(spec, name, "measure")
        try:
            raw = call_binding(b, env)
        except MethodCallError as e:
            raise MeasureError(str(e)) from e
        if ms is not None and is_vector(ms):
            out = check_ladder(raw, list(b.keys) or None, name)  # `keys` is declared in the binding: pricebt never guesses which kwarg carries the tenors
        else:
            try:
                out = float(raw)
            except (TypeError, ValueError) as e:
                raise MeasureError(f"measure {name!r} must be a number, got {type(raw).__name__}: {raw!r}") from e
            if not math.isfinite(out):
                raise MeasureError(f"measure {name!r} is not finite ({out})")
    if cache is not None:
        cache[key] = out
    return dict(out) if isinstance(out, dict) else out


def evaluate_layer(spec: InstrumentSpec, name: str, env: Env) -> float:
    """One layer's per-unit P&L over the interval since the previous cadence point (the engine computes `unexplained` itself)."""
    if name in RESERVED_LAYERS:
        raise MeasureError(f"{name!r} is an engine-owned row and cannot be evaluated as a layer")
    b = _binding(spec, name, "layer")
    try:
        raw = call_binding(b, env)
    except MethodCallError as e:
        raise MeasureError(str(e)) from e
    if raw is None:
        raise MeasureError(f"layer {name!r} of instrument {spec.name!r} returned None")
    try:
        out = float(raw)
    except (TypeError, ValueError) as e:
        raise MeasureError(f"layer {name!r} must be a number, got {raw!r}") from e
    if not math.isfinite(out):
        raise MeasureError(f"layer {name!r} of instrument {spec.name!r} is not finite ({out})")
    return out
