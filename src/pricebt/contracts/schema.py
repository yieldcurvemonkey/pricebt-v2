"""Neutral asset-class schemas: data files that say WHAT pricebt expects, never HOW a library provides it (spec S1-S7, T2).

A schema declares, per asset class: `terms` (the per-trade parameters and their direction sign), `methods` (the value contract), `measures` (dollar delta,
gamma, rate, delta ladder ...), `layers` (P&L decomposition rows with id and version), a `conventions` vocabulary (data only) and optional
`pricer_capabilities`. Every entry carries kind, neutral argument names, return type, unit, sign, `required`, synonyms and a `doc`. A schema never names
a callable: that is a binding's job (`contracts.binding`). Unknown keys are errors; `extends:` merges a parent entry by entry (a child field that is
left empty inherits the parent's); the resolved schema must give every method, measure and layer a return type and a unit (S2).
"""
from __future__ import annotations

import datetime as dt
import difflib
import importlib.resources as ir
import math
import numbers
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd
import yaml

from ..errors import ConfigError
from ..types import RESERVED_LAYERS

SCHEMA_VERSION = 1
_TOP = {"schema_version", "asset_class", "extends", "doc", "terms", "direction", "methods", "measures", "layers", "default_layers", "conventions",
        "pricer_capabilities", "reserved_rows"}
_ENTRY = {
    "methods": ("method", {"kind", "required", "args", "returns", "unit", "sign", "synonyms", "doc"}),
    "measures": ("measure", {"kind", "required", "args", "returns", "unit", "sign", "synonyms", "derived", "doc"}),
    "layers": ("layer", {"kind", "id", "version", "required", "returns", "unit", "sign", "needs", "synonyms", "doc"}),
    "pricer_capabilities": ("lookup", {"kind", "required", "args", "returns", "doc"}),
    "conventions": ("convention_key", {"kind", "type", "values", "required", "doc"}),
}
_TERM_KEYS = {"kind", "type", "required", "default", "aliases", "values", "tokens", "unit", "positive", "doc"}
TERM_TYPES = ("enum", "date_or_tenor", "number", "number_or_token", "string", "mapping")
CONVENTION_TYPES = ("name", "int", "bool", "token")  # a calendar NAME, a non-negative integer, a boolean, one of `values`
_TENOR = re.compile(r"\d+[dDwWmMyY]")
_TERM_REF = re.compile(r"@(signal|param|trigger)\.[A-Za-z][A-Za-z0-9_]*")  # the roots an action's terms may reference (T3)


def is_term_ref(v: Any) -> bool:
    return isinstance(v, str) and v.startswith("@") and not v.startswith("@@")


def has_term_ref(v: Any) -> bool:
    """A reference anywhere in a term value, including inside a mapping or list value (for example `extras`)."""
    if is_term_ref(v):
        return True
    if isinstance(v, Mapping):
        return any(has_term_ref(x) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(has_term_ref(x) for x in v)
    return False


def is_number(v: Any) -> bool:
    return isinstance(v, numbers.Real) and not isinstance(v, bool) and type(v).__name__ != "bool_"


def _err(source: str, path: str, msg: str, code: str = "CFG-SCHEMA") -> ConfigError:
    return ConfigError(msg, path=f"{source}{'.' if source and path else ''}{path}", code=code)


@dataclass(frozen=True)
class NameSpec:
    """One expected name: a method, measure, layer, lookup or convention key."""

    name: str
    kind: str
    required: bool = False
    args: Mapping[str, Any] = field(default_factory=dict)
    returns: str = ""
    unit: str = ""
    sign: str = ""
    synonyms: Tuple[str, ...] = ()
    derived: str = ""
    doc: str = ""
    id: str = ""
    version: int = 0
    needs: Tuple[str, ...] = ()
    type: str = ""
    values: Tuple[Any, ...] = ()


@dataclass(frozen=True)
class TermSpec:
    name: str
    type: str
    required: bool = False
    has_default: bool = False
    default: Any = None
    aliases: Mapping[str, str] = field(default_factory=dict)
    values: Tuple[str, ...] = ()
    tokens: Tuple[str, ...] = ()
    unit: str = ""
    positive: bool = False
    doc: str = ""


@dataclass(frozen=True)
class Direction:
    """The one sign convention of the asset class: `sign[<value of term>]` in {+1, -1}; +1 is long the primary exposure."""

    term: str
    sign: Mapping[str, int]


@dataclass(frozen=True)
class AssetSchema:
    asset_class: str
    extends: Optional[str] = None
    doc: str = ""
    terms: Mapping[str, TermSpec] = field(default_factory=dict)
    direction: Optional[Direction] = None
    methods: Mapping[str, NameSpec] = field(default_factory=dict)
    measures: Mapping[str, NameSpec] = field(default_factory=dict)
    layers: Mapping[str, NameSpec] = field(default_factory=dict)
    default_layers: Tuple[str, ...] = ()
    conventions: Mapping[str, NameSpec] = field(default_factory=dict)
    capabilities: Mapping[str, NameSpec] = field(default_factory=dict)
    reserved_rows: Mapping[str, str] = field(default_factory=dict)

    # ---- names
    def required_names(self) -> Dict[str, List[str]]:
        """Names the engine, triggers or hedges cannot work without (methods and measures; derived measures included)."""
        return {"method": sorted(n for n, s in self.methods.items() if s.required), "measure": sorted(n for n, s in self.measures.items() if s.required)}

    def required_layers(self) -> List[str]:
        """Layers every instrument of this class must provide: bound at load (B6), like any other required name."""
        return sorted(n for n, s in self.layers.items() if s.required)

    def bindable_names(self, kind: str) -> List[str]:
        """Names a binding may implement: derived measures (pv, notional) are computed by pricebt and take no binding."""
        section = {"method": self.methods, "measure": self.measures, "layer": self.layers}[kind]
        return [n for n, s in section.items() if not s.derived]

    def layer_ids(self) -> Dict[str, str]:
        return {n: f"{s.id}@{s.version}" for n, s in self.layers.items()}

    def spec_of(self, name: str) -> Optional[NameSpec]:
        for section in (self.methods, self.measures, self.layers):
            if name in section:
                return section[name]
        return None

    def suggest(self, name: str, candidates: Sequence[str]) -> List[str]:
        """Did-you-mean for an unbound schema name: candidates that are declared synonyms of it first, then close edit-distance matches."""
        spec = self.spec_of(name)
        syn = {s.lower() for s in (spec.synonyms if spec else ())}
        out = [c for c in candidates if c.lower() in syn]
        for c in difflib.get_close_matches(name, [c for c in candidates if c not in out], n=3):
            out.append(c)
        return out

    def unknown_conventions(self, conventions: Mapping[str, Any]) -> List[str]:
        """Convention keys outside this schema's vocabulary. pricebt itself never interprets a convention; an adapter calls this (T5) and MUST error on any."""
        return sorted(str(k) for k in conventions if k not in self.conventions)

    def check_conventions(self, conventions: Mapping[str, Any], *, require_all: bool = False) -> Dict[str, Any]:
        """Validate a `conventions` block against this schema's vocabulary (T5) and return a plain copy. Refused with `CFG-CONVENTION`: an unknown key, a value of the
        wrong type, a token outside the entry's `values`, and (with `require_all`, or for an entry marked `required`) a missing key. pricebt still applies none of
        them: an adapter calls this, then decides which values it can honour and errors on the rest."""
        if not isinstance(conventions, Mapping):
            raise ConfigError(f"`conventions` must be a mapping, got {type(conventions).__name__}", code="CFG-CONVENTION")
        bad = [k for k in conventions if not isinstance(k, str)]
        if bad:
            raise ConfigError(f"convention keys must be strings, got {bad}", code="CFG-CONVENTION")
        unknown = self.unknown_conventions(conventions)
        if unknown:
            hints = {k: difflib.get_close_matches(k, list(self.conventions), n=1) for k in unknown}
            said = "; ".join(f"{k}: did you mean {h[0]!r}?" for k, h in hints.items() if h)
            raise ConfigError(f"unknown convention keys {unknown} for asset class {self.asset_class!r}; the vocabulary is {sorted(self.conventions)}" + (f" ({said})" if said else ""),
                              code="CFG-CONVENTION")
        missing = sorted(n for n, c in self.conventions.items() if (require_all or c.required) and n not in conventions)
        if missing:
            raise ConfigError(f"missing convention keys {missing} for asset class {self.asset_class!r}: there are no implicit defaults", code="CFG-CONVENTION")
        for key, v in conventions.items():
            spec = self.conventions[key]
            ok = (
                isinstance(v, str) and bool(v.strip()) if spec.type == "name"
                else isinstance(v, int) and not isinstance(v, bool) and v >= 0 if spec.type == "int"
                else isinstance(v, bool) if spec.type == "bool"
                else v in spec.values if spec.type == "token"
                else v is not None
            )
            if not ok:
                want = {"name": "a non-empty name", "int": "a non-negative integer", "bool": "true or false", "token": f"one of {list(spec.values)}"}.get(spec.type, "a value")
                raise ConfigError(f"convention {key!r} must be {want}, got {v!r}", code="CFG-CONVENTION")
        return {k: (v.strip() if self.conventions[k].type == "name" else v) for k, v in conventions.items()}

    # ---- terms
    def _terms_error(self, msg: str) -> ConfigError:
        schema = "; ".join(f"{n}({t.type}{', required' if t.required else ''})" for n, t in self.terms.items())
        return ConfigError(f"asset class {self.asset_class!r}: {msg}. Terms: {schema}", code="CFG-TERMS")

    def normalise_terms(self, terms: Mapping[str, Any], *, allow_refs: bool = False) -> Tuple[Dict[str, Any], Optional[int]]:
        """Validate per-trade terms against the schema, fill defaults, canonicalise enum aliases and tokens. Returns (terms, direction sign).
        Dates and tenors pass through UNTOUCHED (the library resolves them). With `allow_refs`, a `@signal.<n>`, `@param.<n>` or `@trigger.<n>` string (a
        reference resolved later) is accepted for any term, also inside a mapping value, and the direction is None while `side` is a reference. `@@x` is the
        literal string `@x`. The result is not input: it is not idempotent (the literal `@x` would read as a reference), so a caller that derives new terms
        starts from the terms it was GIVEN, as `TradeTemplate` does."""
        unknown = sorted(set(terms) - set(self.terms))
        if unknown:
            raise self._terms_error(f"unknown terms {unknown}")
        out: Dict[str, Any] = {}
        for name, spec in self.terms.items():
            if name in terms:
                v = terms[name]
            elif spec.has_default:
                v = spec.default
            elif spec.required:
                raise self._terms_error(f"missing required term {name!r}")
            else:
                continue
            if has_term_ref(v):
                if not allow_refs:
                    raise self._terms_error(f"term {name!r} holds a reference, which is only allowed in an action's terms")
                self._check_refs(name, v)
                out[name] = v
                continue
            out[name] = self._coerce(spec, self._unescape(v))
        direction = None
        if self.direction is not None:
            side = out.get(self.direction.term)
            if side is not None and not is_term_ref(side):
                direction = int(self.direction.sign[side])
        return out, direction

    def check_term(self, name: str, value: Any) -> Any:
        """Validate ONE term value by its own rules (type, finiteness, positivity, date or tenor syntax, enum values) and return it normalised (a date or tenor string
        stripped, a token lower-cased, an enum alias canonical). For a caller that has to reject a bad argument before it holds an instrument spec; a reference is refused."""
        spec = self.terms.get(name)
        if spec is None:
            raise self._terms_error(f"unknown term {name!r}")
        if has_term_ref(value):
            raise self._terms_error(f"term {name!r} holds a reference, which is only allowed in an action's terms")
        return self._coerce(spec, self._unescape(value))

    @staticmethod
    def _unescape(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("@@"):
            return v[1:]
        return v

    def _check_refs(self, name: str, v: Any) -> None:
        if is_term_ref(v):
            if not _TERM_REF.fullmatch(v):
                raise self._terms_error(f"term {name!r}: {v!r} is not a valid reference (allowed roots: @signal.<n>, @param.<n>, @trigger.<n>)")
        elif isinstance(v, Mapping):
            for x in v.values():
                self._check_refs(name, x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                self._check_refs(name, x)

    def _coerce(self, spec: TermSpec, v: Any) -> Any:
        t, n = spec.type, spec.name
        if t == "enum":
            if not isinstance(v, str):
                raise self._terms_error(f"term {n!r} must be one of {list(spec.values)} (aliases {dict(spec.aliases)}), got {v!r}")
            key = v.strip().lower()
            key = spec.aliases.get(key, key)
            if key not in spec.values:
                raise self._terms_error(f"term {n!r} must be one of {list(spec.values)} (aliases {dict(spec.aliases)}), got {v!r}")
            return key
        if t in ("number", "number_or_token"):
            if not is_number(v):
                if t == "number_or_token" and isinstance(v, str) and v.strip().lower() in spec.tokens:
                    return v.strip().lower()
                raise self._terms_error(f"term {n!r} must be a number{' or ' + '/'.join(spec.tokens) if spec.tokens else ''}, got {v!r}")
            if not math.isfinite(float(v)):
                raise self._terms_error(f"term {n!r} must be finite, got {v!r}")
            if spec.positive and not float(v) > 0:
                raise self._terms_error(f"term {n!r} must be positive (direction is `{self.direction.term if self.direction else 'side'}`, never the sign of the size), got {v!r}")
            return v
        if t == "date_or_tenor":
            if isinstance(v, (dt.date, dt.datetime, pd.Timestamp)):
                if pd.isna(v):
                    raise self._terms_error(f"term {n!r} must be a real date, got {v!r}")
                return v
            if isinstance(v, str):
                w = v.strip()
                if w.lower() in spec.tokens:
                    return w.lower()
                if _TENOR.fullmatch(w):
                    return w
                if len(w) == 10:
                    try:
                        dt.date.fromisoformat(w)
                        return w
                    except ValueError:
                        pass
            raise self._terms_error(f"term {n!r} must be a date, a valid ISO date string, a tenor like 10Y or one of {list(spec.tokens)}, got {v!r}")
        if t == "string":
            if not isinstance(v, str) or not v.strip():
                raise self._terms_error(f"term {n!r} must be a non-empty string, got {v!r}")
            return v
        if t == "mapping":
            if not isinstance(v, Mapping):
                raise self._terms_error(f"term {n!r} must be a mapping, got {type(v).__name__}")
            return dict(v)
        raise self._terms_error(f"term {n!r} has unknown type {t!r}")

    def check_resolved(self, given: Mapping[str, Any], resolved: Mapping[str, Any]) -> Dict[str, Any]:
        """Merge the terms a factory RESOLVED over the given ones (T4) and re-validate them: schema terms must still be valid values, the ones that define
        the trade (`side` and any positive size) must not change, and a relative date or the `par` token must have become a concrete value. Extra keys
        the library reports (for example the id of the curve it used) are kept."""
        merged = {**dict(given), **dict(resolved)}
        for name, spec in self.terms.items():
            if name not in merged:
                continue
            v = merged[name]
            if spec.type == "number_or_token" and isinstance(v, str):
                raise ConfigError(f"term {name!r} came back unresolved ({v!r}): the library must return a concrete number (T4)", code="CFG-TERMS-UNRESOLVED")
            if spec.type == "date_or_tenor" and isinstance(v, str) and (_TENOR.fullmatch(v.strip()) or v.strip().lower() in spec.tokens):
                raise ConfigError(f"term {name!r} came back unresolved ({v!r}): the library must return a concrete date (T4)", code="CFG-TERMS-UNRESOLVED")
            merged[name] = self._coerce(spec, v)
            if (spec.type == "enum" or spec.positive) and name in given and merged[name] != given[name]:
                raise self._terms_error(f"the factory changed term {name!r} from {given[name]!r} to {merged[name]!r}; only relative dates and tokens are resolved")
        return merged


# ------------------------------------------------------------------------------ parsing
def _check_keys(d: Mapping[str, Any], allowed: set, source: str, path: str) -> None:
    for k in d:
        if k not in allowed:
            close = difflib.get_close_matches(str(k), sorted(allowed), n=1)
            raise _err(source, path, f"unknown key {k!r}" + (f" (did you mean {close[0]!r}?)" if close else f"; allowed {sorted(allowed)}"), "CFG-UNKNOWN-KEY")


def _tuple(v: Any, source: str, path: str) -> Tuple[Any, ...]:
    if v is None:
        return ()
    if not isinstance(v, (list, tuple)):
        raise _err(source, path, f"expected a list, got {type(v).__name__}")
    return tuple(v)


def _bool(raw: Mapping[str, Any], key: str, source: str, path: str) -> bool:
    v = raw.get(key, False)
    if not isinstance(v, bool):
        raise _err(source, path, f"`{key}` must be true or false, got {v!r}")
    return v


def _name_spec(section: str, name: str, raw: Any, source: str) -> NameSpec:
    kind, allowed = _ENTRY[section]
    path = f"{section}.{name}"
    raw = {} if raw is None else raw
    if not isinstance(raw, Mapping):
        raise _err(source, path, f"expected a mapping, got {type(raw).__name__}")
    _check_keys(raw, allowed, source, path)
    if raw.get("kind", kind) != kind:
        raise _err(source, path, f"kind {raw.get('kind')!r} does not match the section (expected {kind!r})")
    args = raw.get("args", {}) or {}
    if not isinstance(args, Mapping):
        raise _err(source, path, "`args` must be a mapping of neutral argument names")
    ident, version = raw.get("id", ""), raw.get("version", 0)
    if section == "layers":
        if name in RESERVED_LAYERS:
            raise _err(source, path, f"{name!r} is reserved for the engine and cannot be a layer")
        if not isinstance(ident, str) or not ident:
            raise _err(source, path, "a layer needs a non-empty `id` (for example swap.carry)")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise _err(source, path, "a layer needs an integer `version` >= 1")
    ctype = str(raw.get("type", ""))
    values = _tuple(raw.get("values"), source, f"{path}.values")
    if section == "conventions":
        if values and ctype in ("", "token"):
            ctype = "token"
        if ctype not in ("", *CONVENTION_TYPES):
            raise _err(source, path, f"`type` must be one of {list(CONVENTION_TYPES)}, got {ctype!r}")
        if values and ctype != "token":
            raise _err(source, path, f"`values` (the accepted tokens) belong on a token entry, not on type {ctype!r}")
        if ctype == "token" and not values:
            raise _err(source, path, "a token entry needs `values`: the list of accepted tokens")
    return NameSpec(
        name=name, kind=kind, required=_bool(raw, "required", source, path), args=dict(args), returns=str(raw.get("returns", "")), unit=str(raw.get("unit", "")),
        sign=str(raw.get("sign", "")), synonyms=tuple(str(s) for s in _tuple(raw.get("synonyms"), source, f"{path}.synonyms")), derived=str(raw.get("derived", "")),
        doc=str(raw.get("doc", "")), id=str(ident), version=int(version) if version else 0, needs=tuple(str(s) for s in _tuple(raw.get("needs"), source, f"{path}.needs")),
        type=ctype, values=values,
    )


def _term_spec(name: str, raw: Any, source: str) -> TermSpec:
    path = f"terms.{name}"
    if not isinstance(raw, Mapping):
        raise _err(source, path, "expected a mapping")
    _check_keys(raw, _TERM_KEYS, source, path)
    if raw.get("kind", "term") != "term":
        raise _err(source, path, "kind must be 'term'")
    t = raw.get("type")
    if t not in TERM_TYPES:
        raise _err(source, path, f"`type` must be one of {list(TERM_TYPES)}, got {t!r}")
    values = tuple(str(v) for v in _tuple(raw.get("values"), source, f"{path}.values"))
    aliases = raw.get("aliases", {}) or {}
    if not isinstance(aliases, Mapping):
        raise _err(source, path, "`aliases` must be a mapping alias -> canonical value")
    if t == "enum":
        if not values:
            raise _err(source, path, "an enum term needs `values`")
        if any(v != v.lower() for v in values):
            raise _err(source, path, f"enum values must be lower-case (input is matched case-insensitively): {list(values)}")
        bad = {a: c for a, c in aliases.items() if c not in values}
        if bad:
            raise _err(source, path, f"aliases {bad} point at values outside {list(values)}")
        clash = sorted(str(a) for a in aliases if str(a).lower() in values)
        if clash:
            raise _err(source, path, f"aliases {clash} are also values: an alias may not shadow a value")
    return TermSpec(name=name, type=t, required=_bool(raw, "required", source, path), has_default="default" in raw, default=raw.get("default"),
                    aliases={str(a).lower(): str(c) for a, c in aliases.items()}, values=values, tokens=tuple(str(x).lower() for x in _tuple(raw.get("tokens"), source, f"{path}.tokens")),
                    unit=str(raw.get("unit", "")), positive=_bool(raw, "positive", source, path), doc=str(raw.get("doc", "")))


def _direction_shape(raw: Any, source: str) -> Optional[Direction]:
    if raw is None:
        return None
    path = "direction"
    if not isinstance(raw, Mapping):
        raise _err(source, path, "expected {term: <name>, sign: {<value>: 1|-1}}")
    _check_keys(raw, {"term", "sign"}, source, path)
    sign = raw.get("sign") or {}
    if not isinstance(raw.get("term"), str) or not isinstance(sign, Mapping) or any(s not in (1, -1) or isinstance(s, bool) for s in sign.values()):
        raise _err(source, path, "expected {term: <name>, sign: {<value>: 1|-1}}")
    return Direction(raw["term"], {str(k): int(v) for k, v in sign.items()})


def _check_direction(schema: AssetSchema, source: str) -> None:
    d = schema.direction
    if d is None:
        return
    spec = schema.terms.get(d.term)
    if spec is None or spec.type != "enum":
        raise _err(source, "direction", f"`term` must name a declared enum term, got {d.term!r} (terms: {sorted(schema.terms)})")
    if set(d.sign) != set(spec.values):
        raise _err(source, "direction", f"`sign` needs +1 or -1 for every value of {d.term!r}: {list(spec.values)}; got {sorted(d.sign)}")


def parse_schema(raw: Mapping[str, Any], source: str = "") -> AssetSchema:
    """Validate one schema mapping (as loaded from YAML). Own content only: `extends` is resolved by the registry."""
    if not isinstance(raw, Mapping):
        raise _err(source, "", "a schema must be a mapping")
    _check_keys(raw, _TOP, source, "")
    if raw.get("schema_version") != SCHEMA_VERSION or isinstance(raw.get("schema_version"), bool):
        raise _err(source, "schema_version", f"schema_version must be {SCHEMA_VERSION}, got {raw.get('schema_version')!r}")
    ac = raw.get("asset_class")
    if not isinstance(ac, str) or not ac:
        raise _err(source, "asset_class", "asset_class must be a non-empty string")
    ext = raw.get("extends")
    if ext is not None and (not isinstance(ext, str) or not ext):
        raise _err(source, "extends", f"extends must be the name of one asset class, got {ext!r}")
    terms = {n: _term_spec(n, r, source) for n, r in (raw.get("terms") or {}).items()}
    sections = {s: {n: _name_spec(s, n, r, source) for n, r in (raw.get(s) or {}).items()} for s in _ENTRY}
    schema = AssetSchema(
        asset_class=ac, extends=ext, doc=str(raw.get("doc", "")), terms=terms, direction=_direction_shape(raw.get("direction"), source),
        methods=sections["methods"], measures=sections["measures"], layers=sections["layers"],
        default_layers=tuple(str(x) for x in _tuple(raw.get("default_layers"), source, "default_layers")), conventions=sections["conventions"],
        capabilities=sections["pricer_capabilities"], reserved_rows={str(k): str(v) for k, v in (raw.get("reserved_rows") or {}).items()},
    )
    if not schema.extends:
        _check_default_layers(schema, source)
        _check_direction(schema, source)
    return schema


def _check_default_layers(schema: AssetSchema, source: str) -> None:
    missing = [n for n in schema.default_layers if n not in schema.layers]
    if missing:
        raise _err(source, "default_layers", f"{missing} are not declared layers of {schema.asset_class!r}: {sorted(schema.layers)}")
    dup = sorted({n for n in schema.default_layers if schema.default_layers.count(n) > 1})
    if dup:
        raise _err(source, "default_layers", f"duplicate default layers {dup}")


def _merge_name(base: NameSpec, child: NameSpec) -> NameSpec:
    """A child entry inherits every field it leaves empty (returns, unit, sign, doc, args, synonyms, ...); it can raise `required` but not lower it."""
    return NameSpec(
        name=child.name, kind=child.kind, required=child.required or base.required, args={**dict(base.args), **dict(child.args)}, returns=child.returns or base.returns,
        unit=child.unit or base.unit, sign=child.sign or base.sign, synonyms=child.synonyms or base.synonyms, derived=child.derived or base.derived, doc=child.doc or base.doc,
        id=child.id or base.id, version=child.version or base.version, needs=child.needs or base.needs, type=child.type or base.type, values=child.values or base.values)


def _merge_section(base: Mapping[str, NameSpec], child: Mapping[str, NameSpec]) -> Dict[str, NameSpec]:
    out = dict(base)
    for n, c in child.items():
        out[n] = _merge_name(base[n], c) if n in base else c
    return out


def _merge(base: AssetSchema, child: AssetSchema) -> AssetSchema:
    merged = AssetSchema(
        asset_class=child.asset_class, extends=child.extends, doc=child.doc or base.doc, terms={**base.terms, **child.terms},
        direction=child.direction or base.direction, methods=_merge_section(base.methods, child.methods), measures=_merge_section(base.measures, child.measures),
        layers=_merge_section(base.layers, child.layers), default_layers=child.default_layers or base.default_layers,
        conventions=_merge_section(base.conventions, child.conventions), capabilities=_merge_section(base.capabilities, child.capabilities),
        reserved_rows={**base.reserved_rows, **child.reserved_rows},
    )
    _check_default_layers(merged, child.asset_class)
    _check_direction(merged, child.asset_class)
    return merged


def _check_s2(schema: AssetSchema) -> None:
    """S2: every method, measure and layer declares a return type; every measure and layer a unit (after inheritance)."""
    for sec in (schema.methods, schema.measures, schema.layers):
        for n, s in sec.items():
            if not s.returns:
                raise ConfigError(f"asset class {schema.asset_class!r}: {s.kind} {n!r} declares no `returns` (spec S2: every name declares its return type)", code="CFG-SCHEMA")
            if s.kind in ("measure", "layer") and not s.unit:
                raise ConfigError(f"asset class {schema.asset_class!r}: {s.kind} {n!r} declares no `unit` (spec S2: every measure and layer declares its unit)", code="CFG-SCHEMA")


class _StrictLoader(yaml.SafeLoader):
    """safe_load that refuses duplicate mapping keys (the last one would otherwise win silently)."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> Dict[Any, Any]:
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", k.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_schema_text(text: str, source: str = "") -> Any:
    try:
        return yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as e:
        raise ConfigError(f"cannot read schema {source}: {e}", code="CFG-SCHEMA") from e


class SchemaRegistry:
    """Shipped schemas (package data) plus any registered from files or mappings. Instance-owned; no module-level cache."""

    def __init__(self) -> None:
        self._raw: Dict[str, AssetSchema] = {}
        self._resolved: Dict[str, AssetSchema] = {}

    @classmethod
    def default(cls) -> "SchemaRegistry":
        reg = cls()
        for res in sorted(ir.files("pricebt.contracts.schemas").iterdir(), key=lambda r: r.name):
            if res.name.endswith(".yaml"):
                reg.register(load_schema_text(res.read_text(encoding="utf8"), res.name), source=res.name)
        if not reg._raw:
            raise ConfigError("no schemas found in the package data of pricebt.contracts.schemas (is the package built without its *.yaml files?)", code="CFG-SCHEMA")
        return reg

    def register(self, raw: Mapping[str, Any], source: str = "") -> AssetSchema:
        s = parse_schema(raw, source)
        self._raw[s.asset_class] = s
        self._resolved.clear()
        return s

    def register_file(self, path: Union[str, Path]) -> AssetSchema:
        return self.register(load_schema_text(Path(path).read_text(encoding="utf8"), Path(path).name), source=Path(path).name)

    def extended(self, raw: Mapping[str, Any], source: str = "") -> "SchemaRegistry":
        """A NEW registry holding this one's schemas plus `raw` (an adapter's extension schema, usually `extends` one of the shipped classes)."""
        out = SchemaRegistry()
        out._raw = dict(self._raw)
        out.register(raw, source)
        return out

    def get(self, name: str, _trail: Tuple[str, ...] = ()) -> AssetSchema:
        if name in self._resolved:
            return self._resolved[name]
        if name not in self._raw:
            raise ConfigError(f"unknown asset class {name!r}; known: {self.names()}", code="CFG-SCHEMA")
        if name in _trail:
            raise ConfigError(f"schema extends cycle: {' -> '.join((*_trail, name))}", code="CFG-SCHEMA")
        s = self._raw[name]
        if s.extends:
            s = _merge(self.get(s.extends, (*_trail, name)), s)
        _check_s2(s)
        self._resolved[name] = s
        return s

    def names(self) -> List[str]:
        return sorted(self._raw)
