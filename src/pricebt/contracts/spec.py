"""Instrument specs, kits and trade templates (spec T1-T7, B4-B6).

    instruments:                                   # defined ONCE per kind of instrument
      usd_sofr_ois:
        asset_class: swap
        factory: "pkg.mod:kit"                     # a Kit (factory + default bindings) or a plain callable
        conventions: {...}                         # opaque to pricebt, passed verbatim, hashed into the manifest
        bind: {dv01: {...}}                        # overrides the kit's default block name by name

    actions:
      - {type: add_trade, instrument: usd_sofr_ois, terms: {side: receive, maturity: 10Y, notional: 1e7}}     # supplied per trade

`factory(pricer, ts, *, terms, conventions) -> Built(obj, resolved_terms)`: the library resolves dates and `par` and returns what it resolved. Strict mode:
every required schema name (methods, measures AND layers) must be bound; a name is never resolved by a same-named method.
"""
from __future__ import annotations

import copy
import difflib
import functools
import hashlib
import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from ..errors import ConfigError
from ..registry import DEFAULT_ALLOW, resolve_dotted
from ..types import BASELINE_LAYERS, RESERVED_LAYERS
from .binding import Binding, parse_binding, public_names, validate_signature
from .schema import AssetSchema, SchemaRegistry, has_term_ref

_SPEC_KEYS = {"asset_class", "factory", "conventions", "bind", "pricer", "pricers", "layers", "params", "doc"}
_EXTRA_KINDS = ("measure", "layer")


@dataclass(frozen=True)
class Built:
    """What a factory returns: the library object and the RESOLVED terms (concrete dates, resolved rate)."""

    obj: Any
    terms: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Kit:
    """What an adapter ships for one instrument: the factory, a default binding block, and the class of what it builds (for load-time checks)."""

    factory: Callable[..., Built]
    asset_class: str
    default_bind: Mapping[str, Any] = field(default_factory=dict)
    cls: Optional[type] = None
    extra: Mapping[str, str] = field(default_factory=dict)  # extension names the adapter binds beyond the schema: name -> 'measure' | 'layer'
    schema: Optional[Mapping[str, Any]] = None  # an extension schema (raw mapping) defining `asset_class`, usually `extends: swap`; registered for this kit only
    doc: str = ""

    def __call__(self, pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
        return self.factory(pricer, ts, terms=terms, conventions=conventions)

    @property
    def factory_path(self) -> str:
        """`module:qualname` of the callable behind this kit: stable across runs, never an address."""
        return factory_path(self.factory)


def factory_path(factory: Any) -> str:
    """`module:qualname` of what a factory is made of: a Kit's own factory, a partial's wrapped function, the class of a callable instance. Never a `repr()`, which
    embeds a memory address and would make two identical runs write different manifests."""
    f = factory
    while isinstance(f, (Kit, functools.partial)):
        f = f.factory if isinstance(f, Kit) else f.func
    target = f if hasattr(f, "__qualname__") else type(f)
    return f"{getattr(target, '__module__', None) or '?'}:{target.__qualname__}"


def _canonical(v: Any) -> Any:
    """A JSON-safe canonical form: sets become sorted lists, mapping keys must be strings (a hash-order or type-order dependent digest is not a digest)."""
    if isinstance(v, Mapping):
        bad = [k for k in v if not isinstance(k, str)]
        if bad:
            raise ConfigError(f"conventions keys must be strings, got {bad}", code="CFG-CONVENTIONS")
        return {k: _canonical(x) for k, x in v.items()}
    if isinstance(v, (set, frozenset)):
        return sorted((_canonical(x) for x in v), key=repr)
    if isinstance(v, (list, tuple)):
        return [_canonical(x) for x in v]
    return v


def layer_ref(spec: "InstrumentSpec", layer: str) -> str:
    """`<id>@<version>` of a layer as the spec's schema defines it (spec L5, for example `swap.carry@1`); an extension layer has no schema id: `<name>@ext`; the engine's
    baseline rows are `baseline.<name>@1`."""
    if layer in BASELINE_LAYERS:
        return f"baseline.{layer}@1"
    s = spec.schema.layers.get(layer)
    return f"{s.id}@{s.version}" if s is not None and s.id else f"{layer}@ext"


def conventions_digest(conventions: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_canonical(dict(conventions)), sort_keys=True, default=str).encode("utf8")).hexdigest()[:16]


@dataclass(frozen=True)
class Stack:
    """An adapter's bundle for the Python facade: the `wrap` that turns a provider's snapshot pricer into the adapter's pricer, and the instrument
    specs it ships, as raw `instruments:` entries (factory Kit, conventions, optional bind overrides) keyed by NAME. `defaults` maps
    `"<asset_class>:<CCY>"` to a name, which is how `IRSwap('Pay', '10y', 'USD')` finds its spec (spec F-2). The same three things a config stack overlay
    may set (X1), plus the conventions the adapter's default spec carries. `accepts_gs` speaks for the specs the stack ships (its defaults, or one the session picks
    by name), never for a raw spec or an InstrumentSpec the session substitutes: those carry conventions of their own."""

    name: str
    wrap: Optional[Callable[[Any], Any]] = None
    instruments: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    defaults: Mapping[str, str] = field(default_factory=dict)
    accepts_gs: Mapping[str, Sequence[str]] = field(default_factory=dict)  # gs constructor field -> values the shipped specs' conventions already imply (e.g. a floating-rate option name)


@dataclass(frozen=True)
class InstrumentSpec:
    name: str
    asset_class: str
    factory: Callable[..., Built]
    schema: AssetSchema
    bindings: Mapping[str, Binding]
    conventions: Mapping[str, Any] = field(default_factory=dict)
    pricer: str = "primary"
    pricers: Mapping[str, str] = field(default_factory=dict)
    layers: Tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)
    factory_path: str = ""
    extra: Mapping[str, str] = field(default_factory=dict)

    @property
    def conventions_digest(self) -> str:
        return conventions_digest(self.conventions)

    def roles(self) -> Dict[str, str]:
        out = {"primary": self.pricer}
        out.update(self.pricers)
        return out


# ------------------------------------------------------------------------------ building a spec from config
def _err(name: str, msg: str, code: str, path: str = "") -> ConfigError:
    return ConfigError(f"instrument {name!r}: {msg}", path=path or f"instruments.{name}", code=code)


def _check_method_target(name: str, b: Binding, cls: type) -> None:
    if not callable(getattr(cls, b.target, None)):
        names = [n for n in public_names(cls) if callable(getattr(cls, n, None))]
        close = difflib.get_close_matches(b.target, names, n=3)
        raise _err(name, f"binding {b.name!r}: {cls.__name__} has no method {b.target!r}; it has {names[:30]}" + (f"; did you mean {close}?" if close else ""),
                   "CFG-BINDING", f"instruments.{name}.bind.{b.name}")
    static = inspect.getattr_static(cls, b.target)
    fn = getattr(cls, b.target) if isinstance(static, (staticmethod, classmethod)) else functools.partial(getattr(cls, b.target), object())
    problem = validate_signature(b, fn)
    if problem:
        raise _err(name, problem, "CFG-BINDING", f"instruments.{name}.bind.{b.name}")


def _check_factory(name: str, factory: Callable[..., Any]) -> None:
    """Validation, not injection: the factory must accept exactly `(pricer, ts, *, terms, conventions)`."""
    target = factory.factory if isinstance(factory, Kit) else factory
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return
    try:
        sig.bind(None, None, terms={}, conventions={})
    except TypeError as e:
        raise _err(name, f"the factory must accept (pricer, ts, *, terms, conventions): {e}", "CFG-FACTORY", f"instruments.{name}.factory") from e


def _as_list(name: str, key: str, v: Any) -> Tuple[str, ...]:
    if v is None:
        return ()
    if isinstance(v, str) or not isinstance(v, (list, tuple)):
        raise _err(name, f"`{key}` must be a list of names, got {v!r}", "CFG-TYPE", f"instruments.{name}.{key}")
    out = tuple(str(x) for x in v)
    if len(set(out)) != len(out):
        raise _err(name, f"`{key}` has duplicates: {list(out)}", "CFG-TYPE", f"instruments.{name}.{key}")
    return out


def build_spec(name: str, raw: Mapping[str, Any], *, schemas: SchemaRegistry, allow: Sequence[str] = DEFAULT_ALLOW) -> InstrumentSpec:
    """Validate one `instruments:` entry (strict: every required schema name bound; no implicit resolution) and resolve its factory and bindings."""
    if not isinstance(raw, Mapping):
        raise _err(name, f"expected a mapping, got {type(raw).__name__}", "CFG-TYPE")
    for k in raw:
        if k not in _SPEC_KEYS:
            close = difflib.get_close_matches(str(k), sorted(_SPEC_KEYS), n=1)
            raise _err(name, f"unknown key {k!r}" + (f" (did you mean {close[0]!r}?)" if close else f"; allowed {sorted(_SPEC_KEYS)}"), "CFG-UNKNOWN-KEY", f"instruments.{name}.{k}")
    factory = raw.get("factory")
    path = ""
    if isinstance(factory, str):
        path = factory
        try:
            factory = resolve_dotted(factory, allow=allow)
        except ConfigError as e:
            raise ConfigError(str(e), path=f"instruments.{name}.factory", code=getattr(e, "code", "CFG-IMPORT")) from e
    if not callable(factory):
        raise _err(name, f"`factory` must be a callable or a Kit (or a dotted path to one), got {factory!r}", "CFG-REQUIRED", f"instruments.{name}.factory")
    _check_factory(name, factory)
    kit = factory if isinstance(factory, Kit) else None
    ac = raw.get("asset_class") or (kit.asset_class if kit else None)
    if not ac:
        raise _err(name, "a plain-callable factory needs `asset_class`", "CFG-REQUIRED", f"instruments.{name}.asset_class")
    if kit and raw.get("asset_class") and raw["asset_class"] != kit.asset_class:
        raise _err(name, f"asset_class {raw['asset_class']!r} does not match the kit's {kit.asset_class!r}", "CFG-SCHEMA")
    if kit is not None and kit.schema is not None:
        if kit.schema.get("asset_class") != ac:
            raise _err(name, f"the kit's extension schema defines asset class {kit.schema.get('asset_class')!r}, not {ac!r}", "CFG-SCHEMA")
        schemas = schemas.extended(kit.schema, f"{name} (extension schema)")
    schema = schemas.get(ac)
    cls = kit.cls if kit else None
    extra = dict(kit.extra) if kit else {}
    schema_names = {n for sec in (schema.methods, schema.measures, schema.layers, schema.capabilities) for n in sec}
    for en, ek in extra.items():
        if ek not in _EXTRA_KINDS:
            raise _err(name, f"kit extension {en!r} has kind {ek!r}; allowed {list(_EXTRA_KINDS)}", "CFG-UNKNOWN-BINDING")
        if en in RESERVED_LAYERS or en in schema_names:
            raise _err(name, f"kit extension {en!r} collides with a reserved row or a name of asset class {ac!r}; extension names must be namespaced (for example 'lib.name')", "CFG-UNKNOWN-BINDING")

    bind_raw = raw.get("bind") or {}
    if not isinstance(bind_raw, Mapping):
        raise _err(name, f"`bind` must be a mapping of schema name -> binding, got {type(bind_raw).__name__}", "CFG-TYPE", f"instruments.{name}.bind")
    merged = {**(dict(kit.default_bind) if kit else {}), **dict(bind_raw)}
    bindings: Dict[str, Binding] = {}
    kinds = {**{n: "method" for n in schema.bindable_names("method")}, **{n: "measure" for n in schema.bindable_names("measure")},
             **{n: "layer" for n in schema.bindable_names("layer")}, **extra}
    derived = sorted(n for section in (schema.methods, schema.measures) for n, s in section.items() if s.derived)
    for bname, braw in merged.items():
        if bname in derived:
            raise _err(name, f"{bname!r} is derived by pricebt ({schema.spec_of(bname).derived}) and takes no binding", "CFG-UNKNOWN-BINDING", f"instruments.{name}.bind.{bname}")
        if bname not in kinds:
            close = difflib.get_close_matches(bname, sorted(kinds), n=3)
            raise _err(name, f"binding {bname!r} is not a name of asset class {ac!r}; bindable: {sorted(kinds)}" + (f"; did you mean {close}?" if close else ""),
                       "CFG-UNKNOWN-BINDING", f"instruments.{name}.bind.{bname}")
        b = parse_binding(bname, braw, allow=allow)
        if b.kind == "function":  # resolvable at load whatever the factory is: validate it (B4)
            problem = validate_signature(b, b.fn)  # type: ignore[arg-type]
            if problem:
                raise _err(name, problem, "CFG-BINDING", f"instruments.{name}.bind.{bname}")
        elif b.kind == "method" and cls is not None:
            _check_method_target(name, b, cls)
        bindings[bname] = b

    req = schema.required_names()
    missing = [n for k in ("method", "measure") for n in req[k] if n not in derived and n not in bindings]
    missing_layers = [n for n in schema.required_layers() if n not in bindings]
    if missing or missing_layers:
        hints = []
        if cls is not None:
            names = [n for n in public_names(cls) if callable(getattr(cls, n, None))]
            for n in [*missing, *missing_layers]:
                s = schema.suggest(n, names)
                if s:
                    hints.append(f"{n}: did you mean {s}?")
        raise _err(name, f"required names of asset class {ac!r} are not bound: {[*missing, *missing_layers]}" + (" (" + "; ".join(hints) + ")" if hints else "")
                   + ". Bind each in `bind:` (pricebt never resolves a schema name by a same-named method)", "CFG-REQUIRED-BINDING", f"instruments.{name}.bind")

    layers = _as_list(name, "layers", raw.get("layers"))
    for lname in layers:
        if kinds.get(lname) != "layer":
            raise _err(name, f"layer {lname!r} is not a layer of asset class {ac!r} (layers: {sorted(n for n, k in kinds.items() if k == 'layer')})", "CFG-UNKNOWN-BINDING", f"instruments.{name}.layers")
        if lname not in bindings:
            raise _err(name, f"layer {lname!r} is requested by the instrument but not bound", "CFG-REQUIRED-BINDING", f"instruments.{name}.layers")
    conv = raw.get("conventions") or {}
    if not isinstance(conv, Mapping):
        raise _err(name, "`conventions` must be a mapping", "CFG-TYPE", f"instruments.{name}.conventions")
    _canonical(dict(conv))  # keys must be strings: fails here rather than at the first digest
    pricers = raw.get("pricers") or {}
    if not isinstance(pricers, Mapping):
        raise _err(name, "`pricers` must be a mapping role -> market name", "CFG-TYPE", f"instruments.{name}.pricers")
    params = raw.get("params") or {}
    if not isinstance(params, Mapping):
        raise _err(name, "`params` must be a mapping", "CFG-TYPE", f"instruments.{name}.params")
    return InstrumentSpec(
        name=name, asset_class=ac, factory=factory, schema=schema, bindings=bindings, conventions=copy.deepcopy(dict(conv)), pricer=str(raw.get("pricer", "primary")),
        pricers={str(k): str(v) for k, v in pricers.items()}, layers=layers, params=copy.deepcopy(dict(params)), factory_path=path or factory_path(factory), extra=extra,
    )


# ------------------------------------------------------------------------------ trades
class _Copies:
    """Factory of a ready-made object: every position gets its own deep copy; `resolved` names attributes copied into the resolved terms (explicitly)."""

    def __init__(self, obj: Any, resolved: Sequence[str] = ()):
        self.obj = obj
        self.resolved = tuple(resolved)

    def __call__(self, pricer: Any, ts: Any, *, terms: Mapping[str, Any], conventions: Mapping[str, Any]) -> Built:
        o = copy.deepcopy(self.obj)
        return Built(o, {n: getattr(o, n) for n in self.resolved})


class TradeTemplate:
    """An instrument spec plus the terms of one trade. `name` identifies the trade template (position selectors, ledger); it defaults to the spec's name."""

    def __init__(self, name: str, spec: InstrumentSpec, terms: Optional[Mapping[str, Any]] = None, *, allow_refs: bool = False):
        self.name = name
        self.spec = spec
        self.allow_refs = allow_refs
        self._given: Dict[str, Any] = copy.deepcopy(dict(terms or {}))  # the terms as written (`@@x` escapes and references intact): what a derived template starts from
        if spec.schema.terms:
            self.terms, self.direction = spec.schema.normalise_terms(copy.deepcopy(self._given), allow_refs=allow_refs)
        else:
            if terms:
                raise ConfigError(f"instrument {spec.name!r} (asset class {spec.asset_class!r}) declares no terms, got {sorted(terms)}", code="CFG-TERMS")
            self.terms, self.direction = {}, None

    @classmethod
    def wrap(cls, obj: Any, *, bind: Mapping[str, Any], schemas: SchemaRegistry, name: Optional[str] = None, asset_class: str = "generic", pricer: str = "primary",
             pricers: Optional[Mapping[str, str]] = None, layers: Sequence[str] = (), params: Optional[Mapping[str, Any]] = None, resolved: Sequence[str] = (),
             allow: Sequence[str] = DEFAULT_ALLOW, extra: Optional[Mapping[str, str]] = None, schema: Optional[Mapping[str, Any]] = None) -> "TradeTemplate":
        """A ready-made object as a template (each position deep-copies it). It has no terms; its bindings are written out in `bind`; `resolved` names
        attributes of the copy to expose as resolved terms (for example an expiry date used by `trade_duration`)."""
        nm = name or type(obj).__name__
        kit = Kit(factory=_Copies(obj, resolved), asset_class=asset_class, default_bind={}, cls=type(obj), extra=dict(extra or {}), schema=schema)
        sp = build_spec(nm, {"factory": kit, "bind": dict(bind), "pricer": pricer, "pricers": dict(pricers or {}), "layers": list(layers), "params": dict(params or {})}, schemas=schemas, allow=allow)
        return cls(nm, sp, {})

    # ---- derived
    @property
    def asset_class(self) -> str:
        return self.spec.asset_class

    @property
    def ref_terms(self) -> Dict[str, Any]:
        """The terms that still hold a reference (`@signal.x`, `@param.x`, `@trigger.x`), by name. Read off the terms as GIVEN: once normalised, an escaped literal
        (`@@x` -> `@x`) is indistinguishable from a reference, so scanning `terms` for `@` would mistake it for one."""
        return {k: self.terms[k] for k, v in self._given.items() if has_term_ref(v) and k in self.terms}

    @property
    def has_refs(self) -> bool:
        return bool(self.ref_terms)

    def roles(self) -> Dict[str, str]:
        return self.spec.roles()

    def _derived(self, name: str, changes: Mapping[str, Any]) -> "TradeTemplate":
        """A copy (of the same subclass, with its extra attributes) under another name and with `changes` written over the terms as GIVEN. The given terms, not
        the normalised ones, are validated again: normalising is not idempotent for an escaped literal (`@@x` becomes `@x`, which would read as a reference)."""
        new = copy.copy(self)
        new.name = name
        given = copy.deepcopy({**self._given, **changes})
        if not self.spec.schema.terms and given:
            raise ConfigError(f"instrument {self.spec.name!r} (asset class {self.spec.asset_class!r}) declares no terms, got {sorted(given)}", code="CFG-TERMS")
        new.terms, new.direction = self.spec.schema.normalise_terms(copy.deepcopy(given), allow_refs=self.allow_refs) if self.spec.schema.terms else ({}, None)
        new._given = given
        return new

    def with_terms(self, **changes: Any) -> "TradeTemplate":
        return self._derived(self.name, changes)

    def renamed(self, name: str) -> "TradeTemplate":
        return self._derived(name, {})

    # ---- building
    def build(self, pricer: Any, ts: Any) -> Built:
        """Call the spec's factory with the terms (plus the direction sign) and the verbatim conventions; return the object and the RESOLVED terms."""
        if self.has_refs:
            raise ConfigError(f"trade template {self.name!r} still holds unresolved references {sorted(self.ref_terms)}", code="CFG-TERMS")
        given = dict(self.terms)
        call_terms = {**copy.deepcopy(given), "direction": self.direction} if self.direction is not None else copy.deepcopy(given)
        out = self.spec.factory(pricer, ts, terms=call_terms, conventions=copy.deepcopy(dict(self.spec.conventions)))
        if not isinstance(out, Built):
            raise ConfigError(f"factory of instrument {self.spec.name!r} must return Built(obj, resolved_terms), got {type(out).__name__}", code="CFG-FACTORY")
        resolved_in = {k: v for k, v in dict(out.terms).items() if k != "direction"}
        resolved = self.spec.schema.check_resolved(given, resolved_in) if self.spec.schema.terms else {**given, **resolved_in}
        if self.direction is not None:
            resolved["direction"] = self.direction
        return Built(out.obj, resolved)

    def __repr__(self) -> str:
        return f"TradeTemplate({self.name!r}, instrument={self.spec.name!r}, terms={self.terms})"

    def __deepcopy__(self, memo: Dict[int, Any]) -> "TradeTemplate":
        """The spec is shared (immutable by convention: factories and bindings are not copied); everything else, subclass attributes included, is copied."""
        new = type(self).__new__(type(self))
        memo[id(self)] = new
        for k, v in self.__dict__.items():
            new.__dict__[k] = v if k == "spec" else copy.deepcopy(v, memo)
        return new
