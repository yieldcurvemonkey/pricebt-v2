"""Stack overlays (spec X1, D3): run ONE base config under different pricing libraries.

A stack overlay may set exactly three things: an instrument's `factory`, an instrument's `bind` block, and a pricer role's `wrap`. Everything else (the
conventions, the terms, the strategy, the grid, the signals, the costs, the market provider and its request) belongs to the base and is shared, so a
difference between two runs cannot be a convention or a strategy difference. `base_digest` hashes the config WITHOUT those three keys: equal digests prove two
runs share their base.

    base.yaml     instruments: {usd_sofr_ois: {asset_class: swap, conventions: {...}}}      # no library named
    stack.yaml    instruments: {usd_sofr_ois: {factory: "pkg.mod:kit"}}, market: {pricers: {primary: {wrap: "pkg.mod:wrap"}}}
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Mapping

from ..errors import ConfigError

INSTRUMENT_KEYS = ("factory", "bind")
PRICER_KEYS = ("wrap",)


def _bad(msg: str, path: str) -> ConfigError:
    return ConfigError(msg, path=path, code="CFG-STACK")


def validate_overlay(overlay: Any) -> None:
    """Raise CFG-STACK unless `overlay` touches only instruments.<n>.{factory,bind} and market.pricers.<r>.wrap."""
    if not isinstance(overlay, Mapping):
        raise _bad(f"a stack overlay must be a mapping, got {type(overlay).__name__}", "")
    for top, body in overlay.items():
        if top not in ("instruments", "market"):
            raise _bad(f"a stack may not set {top!r}: only instruments.<name>.factory / .bind and market.pricers.<role>.wrap (everything else is shared by every stack)", str(top))
        if not isinstance(body, Mapping):
            raise _bad(f"{top} must be a mapping", str(top))
        if top == "instruments":
            for name, spec in body.items():
                if not isinstance(spec, Mapping):
                    raise _bad(f"instruments.{name} must be a mapping", f"instruments.{name}")
                for k in spec:
                    if k not in INSTRUMENT_KEYS:
                        raise _bad(f"a stack may not set instruments.{name}.{k}: only {list(INSTRUMENT_KEYS)} (conventions and terms are shared so a difference cannot be a convention)", f"instruments.{name}.{k}")
        else:
            for k, v in body.items():
                if k != "pricers":
                    raise _bad(f"a stack may not set market.{k}: only market.pricers.<role>.wrap", f"market.{k}")
                if not isinstance(v, Mapping):
                    raise _bad("market.pricers must be a mapping", "market.pricers")
                for role, r in v.items():
                    if not isinstance(r, Mapping):
                        raise _bad(f"market.pricers.{role} must be a mapping", f"market.pricers.{role}")
                    for kk in r:
                        if kk not in PRICER_KEYS:
                            raise _bad(f"a stack may not set market.pricers.{role}.{kk}: only {list(PRICER_KEYS)}", f"market.pricers.{role}.{kk}")


def apply_stack(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> Dict[str, Any]:
    """A copy of `base` with the overlay's factory / bind / wrap set. An overlay cannot add an instrument or a role the base does not define."""
    validate_overlay(overlay)
    out = copy.deepcopy(dict(base))
    for name, spec in (overlay.get("instruments") or {}).items():
        tgt = (out.get("instruments") or {}).get(name)
        if tgt is None:
            raise _bad(f"the base defines no instrument {name!r} (a stack cannot add one); it defines {sorted(out.get('instruments') or {})}", f"instruments.{name}")
        tgt.update(copy.deepcopy(dict(spec)))  # `bind` replaces the base's whole bind block: a stack owns it
    for role, r in ((overlay.get("market") or {}).get("pricers") or {}).items():
        tgt = ((out.get("market") or {}).get("pricers") or {}).get(role)
        if tgt is None:
            raise _bad(f"the base defines no pricer role {role!r} (a stack cannot add one); it defines {sorted(((out.get('market') or {}).get('pricers') or {}))}", f"market.pricers.{role}")
        tgt.update(copy.deepcopy(dict(r)))
    return out


def base_digest(cfg: Mapping[str, Any]) -> str:
    """sha256 (16 hex) of the config with every instruments.<n>.factory, instruments.<n>.bind and market.pricers.<r>.wrap removed."""
    c = copy.deepcopy(dict(cfg))
    for spec in (c.get("instruments") or {}).values():
        if isinstance(spec, dict):
            for k in INSTRUMENT_KEYS:
                spec.pop(k, None)
    for r in ((c.get("market") or {}).get("pricers") or {}).values():
        if isinstance(r, dict):
            for k in PRICER_KEYS:
                r.pop(k, None)
    return hashlib.sha256(json.dumps(c, sort_keys=True, default=str).encode("utf8")).hexdigest()[:16]
