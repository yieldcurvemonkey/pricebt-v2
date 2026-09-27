"""Bindings: the ONE way pricebt calls into user or library code (spec B1-B3, Z3).

A binding maps a schema name to a callable and declares exactly the arguments it receives:

    bind:
      dv01:  {target: {method: pv01}}                                         # a method of the built instrument
      value: {target: {method: npv}, kwargs: {curves: "@pricer.curve"}}
      carry: {target: {function: "pkg.mod:carry"}, kwargs: {ctx: "@ctx"}}      # an allow-listed dotted path
      rate:  {target: {method: par}, scale: 100.0}                             # decimals -> the schema's percent
      delta_ladder: {target: {method: lad}, kwargs: {buckets: [2Y, 10Y]}, keys: [2Y, 10Y], reduce: series_to_tenor_dict}

Nothing is injected by parameter name and nothing is looked up by a schema name: a call receives the `args`/`kwargs` written in the binding, with `@...`
references resolved from a small CLOSED grammar (no eval): `@pricer[.a.b]`, `@ctx[.<field>]` (a MarkContext field), `@instrument[.a.b]`, `@terms.<name>`,
`@state.<key>`; path segments start with a letter (private and dunder names are unreachable). `@@x` is the literal string `@x`. Post-processing is
`sign * (scale * reduce(x) + offset)` (a dict result is converted value by value). `keys` declares the exact result keys of a ladder (S4) so pricebt can
check them without guessing which kwarg carries the tenors.
"""
from __future__ import annotations

import copy
import difflib
import inspect
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

import pandas as pd

from ..errors import ConfigError, MethodCallError
from ..registry import DEFAULT_ALLOW, resolve_dotted

KINDS = ("method", "function", "pricer_method", "attribute")
_KEYS = {"target", "args", "kwargs", "reduce", "scale", "offset", "sign", "keys", "doc"}
_REF = re.compile(r"@(pricer|ctx|instrument|terms|state)((?:\.[A-Za-z][A-Za-z0-9_]*)*)")
_MAPPING_ROOTS = ("terms", "state")  # these roots are mappings: a key is required
_CTX_FIELDS = ("ts", "pricer", "pricers", "prev_ts", "prev_pricer", "entry_ts", "entry_pricer", "state", "cache", "params")  # MarkContext
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
TENOR = re.compile(r"\d+[DWMY]")


class FrozenDict(dict):
    """A read-only dict: a loaded binding cannot be changed by whoever still holds a reference to it."""

    def _blocked(self, *a: Any, **k: Any) -> Any:
        raise TypeError("a loaded binding is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _blocked  # type: ignore[assignment]

    def __reduce__(self) -> Tuple[Any, ...]:
        return (FrozenDict, (dict(self),))

    def __deepcopy__(self, memo: Dict[int, Any]) -> "FrozenDict":
        return self

    def __copy__(self) -> "FrozenDict":
        return self


@dataclass(frozen=True)
class Env:
    """What references resolve against: the pricer at the mark, the mark context, the built instrument, its resolved terms, its state."""

    pricer: Any = None
    ctx: Any = None
    instrument: Any = None
    terms: Mapping[str, Any] = field(default_factory=dict)
    state: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Binding:
    name: str
    kind: str
    target: str
    args: Tuple[Any, ...] = ()
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    reduce: Optional[str] = None
    scale: float = 1.0
    offset: float = 0.0
    sign: float = 1.0
    keys: Tuple[str, ...] = ()  # the exact result keys of a ladder (empty: none declared)
    fn: Optional[Callable[..., Any]] = field(default=None, compare=False, repr=False)  # resolved callable for kind 'function'
    reducer: Optional[Callable[[Any], Any]] = field(default=None, compare=False, repr=False)


# ------------------------------------------------------------------------------ reducers
def _float(x: Any) -> float:
    f = float(x)
    if not math.isfinite(f):
        raise ValueError(f"not finite: {f}")
    return f


def _real(x: Any) -> float:
    f = float(x.real)
    if not math.isfinite(f):
        raise ValueError(f"not finite: {f}")
    return f


def _identity(x: Any) -> Any:
    return x


def _items(x: Any) -> Sequence[Tuple[Any, Any]]:
    if isinstance(x, Mapping):
        return list(x.items())
    if isinstance(x, pd.Series):
        return list(x.items())  # items(), not to_dict(): a duplicate label must not collapse silently
    raise MethodCallError(f"expected a mapping or a Series, got {type(x).__name__}")


def _dict_of_floats(x: Any) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for k, v in _items(x):
        f = float(v)
        if not math.isfinite(f):
            raise MethodCallError(f"value for {k!r} is not finite ({f})")
        if str(k) in out:
            raise MethodCallError(f"duplicate key {str(k)!r}")
        out[str(k)] = f
    return out


def _series_to_tenor_dict(x: Any) -> Dict[str, float]:
    """A Series (or mapping) labelled by tenor -> {'2Y': 1.0, ...}: keys upper-cased and checked against the tenor grammar `<int><D|W|M|Y>`."""
    out: Dict[str, float] = {}
    bad = []
    for k, v in _items(x):
        t = str(k).strip().upper()
        if not TENOR.fullmatch(t):
            bad.append(k)
            continue
        f = float(v)
        if not math.isfinite(f):
            raise MethodCallError(f"value for {k!r} is not finite ({f})")
        if t in out:
            raise MethodCallError(f"duplicate tenor {t!r} (labels differ only by case or repeat)")
        out[t] = f
    if bad:
        raise MethodCallError(f"series_to_tenor_dict: {bad} are not tenors (expected <int><D|W|M|Y>, e.g. '3M', '10Y')")
    return out


REDUCERS: Dict[str, Callable[[Any], Any]] = {
    "float": _float,
    "real": _real,
    "identity": _identity,
    "dict_of_floats": _dict_of_floats,
    "series_to_tenor_dict": _series_to_tenor_dict,
}


def register_reducer(name: str, fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Adapters register library-specific reducers here (idempotent for the same function; a different function under a taken name is an error)."""
    old = REDUCERS.get(name)
    if old is not None and old is not fn:
        raise ConfigError(f"reducer {name!r} is already registered", code="CFG-REDUCER")
    REDUCERS[name] = fn
    return fn


# ------------------------------------------------------------------------------ references
def _walk(value: Any, fn: Callable[[str], Any]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, fn) for v in value)
    if isinstance(value, Mapping):
        return {k: _walk(v, fn) for k, v in value.items()}
    return value


def _check_ref(s: str, name: str) -> str:
    if s.startswith("@@"):
        return s
    if s.startswith("@"):
        m = _REF.fullmatch(s)
        ok = m is not None and not (m.group(1) in _MAPPING_ROOTS and not m.group(2))
        if ok and m.group(1) == "ctx" and m.group(2):
            ok = m.group(2).split(".")[1] in _CTX_FIELDS
        if not ok:
            raise ConfigError(f"binding {name!r}: {s!r} is not a valid reference (allowed: @pricer[.attr], @ctx[.{'|'.join(_CTX_FIELDS)}], @instrument[.attr], @terms.<name>, @state.<key>; "
                              "segments start with a letter; write @@ for a literal '@')", path=f"bind.{name}", code="CFG-REF")
    return s


def _resolve(value: Any, env: Env, b: Binding) -> Any:
    def one(s: str) -> Any:
        if s.startswith("@@"):
            return s[1:]
        if not s.startswith("@"):
            return s
        m = _REF.fullmatch(s)
        assert m is not None  # validated at parse time
        root, path = m.group(1), [p for p in m.group(2).split(".") if p]
        obj = getattr(env, root)
        if obj is None:
            raise MethodCallError(f"binding {b.name!r}: reference {s} needs {root!r}, which this call does not provide")
        for i, part in enumerate(path):
            if isinstance(obj, Mapping):
                if part not in obj:
                    raise MethodCallError(f"binding {b.name!r}: reference {s} - {root} has no key {part!r}; available: {sorted(map(str, obj))[:20]}")
                obj = obj[part]
            else:
                if not hasattr(obj, part):
                    raise MethodCallError(f"binding {b.name!r}: reference {s} - {'.'.join([root] + path[:i])} ({type(obj).__name__}) has no attribute {part!r}")
                obj = getattr(obj, part)
        return obj

    return _walk(value, one)


# ------------------------------------------------------------------------------ parsing
def _cfg(name: str, msg: str, code: str = "CFG-BINDING") -> ConfigError:
    return ConfigError(f"binding {name!r}: {msg}", path=f"bind.{name}", code=code)


def _number(name: str, key: str, v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise _cfg(name, f"`{key}` must be a finite number, got {v!r}")
    return float(v)


def parse_binding(name: str, raw: Any, *, allow: Sequence[str] = DEFAULT_ALLOW) -> Binding:
    """Validate one `bind:` entry and resolve its dotted paths (allow-listed). A bare string is shorthand for `{target: {method: <string>}}`."""
    if isinstance(raw, str):
        raw = {"target": {"method": raw}}
    if not isinstance(raw, Mapping):
        raise _cfg(name, f"expected a mapping, got {type(raw).__name__}")
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise _cfg(name, f"unknown keys {unknown}; allowed {sorted(_KEYS)}", "CFG-UNKNOWN-KEY")
    tgt = raw.get("target")
    if not isinstance(tgt, Mapping) or len(tgt) != 1:
        raise _cfg(name, f"`target` must be a mapping with exactly one of {list(KINDS)}, got {tgt!r}")
    (kind, target), = tgt.items()
    if kind not in KINDS or not isinstance(target, str) or not target:
        raise _cfg(name, f"`target` must be one of {list(KINDS)} with a non-empty string, got {dict(tgt)!r}")
    if kind != "function" and not _NAME.fullmatch(target):
        raise _cfg(name, f"{kind} target {target!r} must be a public name (letters, digits, underscores; no leading underscore)")
    args, kwargs = raw.get("args", ()), raw.get("kwargs", {})
    if not isinstance(args, (list, tuple)):
        raise _cfg(name, f"`args` must be a list, got {type(args).__name__}")
    if not isinstance(kwargs, Mapping):
        raise _cfg(name, f"`kwargs` must be a mapping, got {type(kwargs).__name__}")
    if any(not isinstance(k, str) for k in kwargs):
        raise _cfg(name, "`kwargs` keys must be strings")
    if kind == "attribute" and (args or kwargs):
        raise _cfg(name, "an `attribute` target is read, not called: it takes no args/kwargs")
    _walk(list(args), lambda s: _check_ref(s, name))
    _walk(dict(kwargs), lambda s: _check_ref(s, name))
    fn = None
    if kind == "function":
        try:
            fn = resolve_dotted(target, allow=allow)
        except ConfigError as e:
            raise ConfigError(str(e), path=f"bind.{name}.target.function", code=getattr(e, "code", "CFG-IMPORT")) from e
        if not callable(fn):
            raise _cfg(name, f"{target!r} is not callable")
    reduce = raw.get("reduce")
    reducer = None
    if reduce is not None:
        if not isinstance(reduce, str):
            raise _cfg(name, f"`reduce` must be a reducer name or a dotted path, got {reduce!r}", "CFG-REDUCER")
        if reduce in REDUCERS:
            reducer = REDUCERS[reduce]
        elif ":" in reduce:
            try:
                reducer = resolve_dotted(reduce, allow=allow)
            except ConfigError as e:
                raise ConfigError(str(e), path=f"bind.{name}.reduce", code=getattr(e, "code", "CFG-IMPORT")) from e
        else:
            close = difflib.get_close_matches(reduce, REDUCERS, n=2)
            raise _cfg(name, f"unknown reducer {reduce!r}; known {sorted(REDUCERS)}" + (f" (did you mean {close}?)" if close else ""), "CFG-REDUCER")
        if not callable(reducer):
            raise _cfg(name, f"reducer {reduce!r} is not callable", "CFG-REDUCER")
        try:
            inspect.signature(reducer).bind(object())
        except TypeError as e:
            raise _cfg(name, f"reducer {reduce!r} must take exactly one argument (the callable's result): {e}", "CFG-REDUCER") from e
        except ValueError:
            pass  # builtins without an introspectable signature
    keys_raw = raw.get("keys", ())
    if keys_raw is None or isinstance(keys_raw, str) or not isinstance(keys_raw, (list, tuple)):
        raise _cfg(name, f"`keys` must be a list of tenors, got {keys_raw!r}")
    bad_keys = [k for k in keys_raw if not isinstance(k, str) or not TENOR.fullmatch(k)]
    if bad_keys or len(set(keys_raw)) != len(keys_raw):
        raise _cfg(name, f"`keys` must be unique canonical tenors (upper-case <int><D|W|M|Y>); got {list(keys_raw)}")
    sign = _number(name, "sign", raw.get("sign", 1.0))
    scale = _number(name, "scale", raw.get("scale", 1.0))
    if sign not in (1.0, -1.0):
        raise _cfg(name, f"`sign` must be +1 or -1, got {sign}")
    if scale == 0.0:
        raise _cfg(name, "`scale` must not be 0")
    return Binding(
        name=name, kind=kind, target=target, args=copy.deepcopy(tuple(args)), kwargs=FrozenDict(copy.deepcopy(dict(kwargs))), reduce=reduce,
        scale=scale, offset=_number(name, "offset", raw.get("offset", 0.0)), sign=sign, keys=tuple(keys_raw), fn=fn, reducer=reducer,
    )


# ------------------------------------------------------------------------------ execution
def public_names(obj: Any) -> Sequence[str]:
    return sorted(n for n in dir(obj) if not n.startswith("_"))


def _missing(b: Binding, holder: str, obj: Any) -> MethodCallError:
    names = public_names(obj)
    close = difflib.get_close_matches(b.target, names, n=3)
    return MethodCallError(f"binding {b.name!r}: {holder} {type(obj).__name__} has no {b.kind.replace('_', ' ')} {b.target!r}; public names: {names[:30]}"
                           + (f"; did you mean {close}?" if close else ""))


def _post(b: Binding, x: Any) -> Any:
    try:
        if b.reducer is not None:
            x = b.reducer(x)
        if b.scale == 1.0 and b.offset == 0.0 and b.sign == 1.0:
            return x
        if isinstance(x, Mapping):
            return {k: b.sign * (b.scale * v + b.offset) for k, v in x.items()}
        return b.sign * (b.scale * x + b.offset)
    except MethodCallError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, ArithmeticError) as e:
        raise MethodCallError(f"binding {b.name!r}: post-processing (reduce={b.reduce!r}, scale={b.scale}, offset={b.offset}, sign={b.sign}) failed: {type(e).__name__}: {e}") from e


def call_binding(b: Binding, env: Env) -> Any:
    """Execute a binding: resolve its references, call the target with exactly those arguments, post-process."""
    args = tuple(_resolve(list(b.args), env, b))
    kwargs = dict(_resolve(dict(b.kwargs), env, b))
    if b.kind == "attribute":
        if not hasattr(env.instrument, b.target):
            raise _missing(b, "instrument", env.instrument)
        return _post(b, getattr(env.instrument, b.target))
    if b.kind == "function":
        fn = b.fn
    elif b.kind == "method":
        if not callable(getattr(env.instrument, b.target, None)):
            raise _missing(b, "instrument", env.instrument)
        fn = getattr(env.instrument, b.target)
    else:
        if not callable(getattr(env.pricer, b.target, None)):
            raise _missing(b, "pricer", env.pricer)
        fn = getattr(env.pricer, b.target)
    try:
        raw = fn(*args, **kwargs)
    except TypeError as e:  # the callable could not take exactly what the binding declared (or raised a TypeError itself)
        raise MethodCallError(f"binding {b.name!r}: {b.kind} {b.target!r} called with args={list(args)!r} kwargs={sorted(kwargs)}: {e}") from e
    return _post(b, raw)


def validate_signature(b: Binding, fn: Callable[..., Any]) -> Optional[str]:
    """Validation, not injection (B4): can `fn` bind exactly the declared arguments? References are opaque placeholders. None = fine."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None  # builtins and C callables: nothing to check
    try:
        sig.bind(*(object() for _ in b.args), **{k: object() for k in b.kwargs})
    except TypeError as e:
        return f"binding {b.name!r}: {b.target!r}{sig} cannot take args={len(b.args)} kwargs={sorted(b.kwargs)}: {e}"
    return None
