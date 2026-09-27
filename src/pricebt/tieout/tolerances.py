"""Tie-out tolerances (spec X3): declared per level, quantity and asset class; the defaults are the PLACEHOLDERS of spec Appendix B until a run measures the noise floor.

A difference is measured as `|a - b| / max(|reference|, floor)`: relative, but absolute below `floor` (in the unit of the compared value, see `floor_unit`) so that a quantity that is
zero on most days (a coupon cash flow, a financing accrual) has a meaning. A tolerance MUST NOT be widened to turn a run green without a written reason: a declaration carries
`reason` and the report prints it. `expected` marks a known DEFINITION difference between stacks (spec X6, for example carry as forwards-realised versus accrual-net): an
exceedance is reported under its own heading and is not a failure.

Keys are `<level>.<quantity>[.<asset_class>]`. A lookup tries the caller's OWN declarations first (a declaration always beats a shipped default, so `L4.*: 1e-3`
means every L4 quantity), most specific key first (`L1.pv.swap`, then `L1.pv`, then `L1.*`), and only then the shipped defaults in the same order. A per-unit layer
(`L3.<layer>/unit`) takes its own declaration and otherwise the layer's. An explicit argument of `run_tieout` removes every config-declared key it COVERS (`merge_declarations`).

The shipped absolute floors do not scale with the trade size (`L2.delta_ladder` 100 per bucket, `L2.gamma` 1e-2, the per-unit layer floors of 1.0, ...). They assume templates
sized like the shipped ones (per unit of a swap of notional 1mm, or a bond of face 100) and are compared per unit of the position: a run whose units are much smaller MUST
declare its own floors (with a `reason`), otherwise a ladder that is off by a factor of two, but whose every bucket is below the floor, is `noise`. The report prints each floor
with its unit for that reason.

Everything declared is validated by `validate_declaration` (the config loader calls it at load time and `run_tieout` calls it again for what it reads itself): an unknown level or
quantity, a malformed value and a raw `ValueError` out of `float()` are `ConfigError` (code `CFG-TIEOUT`) naming the path.
"""
from __future__ import annotations

import difflib
import math
import numbers
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from ..errors import ConfigError


@dataclass(frozen=True)
class Tol:
    rel: float
    floor: float = 1.0
    expected: bool = False
    strict: bool = False  # an invariant (a position size): a WILDCARD declaration (`L1.*`) does not widen it, only a declaration naming the quantity does
    reason: str = ""  # why this tolerance is what it is (a declared one that is looser than a shipped default MUST say)


#: spec Appendix B (placeholders; the first successful run sets the real values, recorded in tasks/tolerance_ledger.yaml)
DEFAULT_TOLERANCES: Dict[str, Tol] = {
    "L1.pv": Tol(1e-6),
    "L1.pv.bond": Tol(1e-7),
    "L1.cash": Tol(1e-8),
    "L1.financing": Tol(1e-8),
    "L1.quantity": Tol(0.0, 1e-12, strict=True),
    "L2.dv01": Tol(1e-4),
    "L2.gamma": Tol(1e-2, 1e-2),
    "L2.delta_ladder": Tol(1e-3, 100.0),  # per bucket, per unit of the position: small buckets are compared absolutely (the solver noise does not shrink with the bucket)
    "L2.*": Tol(1e-4, 1e-6),
    "L3.tay_delta": Tol(1e-4),
    "L3.tay_convexity": Tol(1e-2, 1e-2),
    "L3.tay_unexplained": Tol(1e-3),
    "L3.*": Tol(1e-3),
    "L4.equity": Tol(1e-5),
    "L4.cash": Tol(1e-5),
    "L4.positions_value": Tol(1e-5),
    "L4.trade_pv": Tol(1e-6),
    "L4.trade_cash": Tol(1e-8),
    "L4.stats": Tol(1e-5, 1e-6),
    "L4.stats_ratios": Tol(5e-2, 1e-6),
    "L4.stats_traded": Tol(1e-5, 1.0),  # currency traded: numerical zero for par swaps, so compared absolutely below one currency unit (measured 6.9e-8 / 8.0e-8 / 2.2e-7)
    "L4.*": Tol(1e-5),
}

#: the quantities the harness compares at the levels whose set is closed. L0 is exact by definition (no tolerance); the measures of L2 and the layers of L3 are named by the run
#: (`audit_measures`, the instruments' layers, the baseline's `tay_*`), so any identifier is a legal quantity there.
QUANTITIES: Dict[str, tuple] = {
    "L1": ("quantity", "pv", "cash", "financing"),
    "L4": ("equity", "cash", "positions_value", "trade_pv", "trade_cash", "stats", "stats_ratios", "stats_traded"),
}
_OPEN_LEVELS = ("L2", "L3")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_\-]*$")
_FIELDS = ("rel", "floor", "expected", "strict", "reason")


def _bad(where: str, key: Any, message: str) -> ConfigError:
    return ConfigError(message, path=f"{where}.{key}" if key is not None else where, code="CFG-TIEOUT")


def _check_key(key: Any, where: str) -> None:
    if not isinstance(key, str):
        raise _bad(where, key, f"a tolerance key is a string `<level>.<quantity>[.<asset class>]`, got {key!r}")
    parts = key.split(".")
    level, shape = parts[0], "`<level>.<quantity>[.<asset class>]` (`<quantity>` may be `*`, which takes no asset class)"
    if level == "L0":
        raise _bad(where, key, f"unknown tolerance key {key!r}: L0 (snapshot digests, resolved terms, conventions) is exact by definition and has no tolerance")
    if level not in ("L1", *_OPEN_LEVELS, "L4"):
        raise _bad(where, key, f"unknown level {level!r} in tolerance key {key!r}: the levels with a tolerance are L1, L2, L3, L4; expected {shape}")
    if len(parts) not in (2, 3) or not parts[1] or (len(parts) == 3 and (parts[1] == "*" or not parts[2])):
        raise _bad(where, key, f"malformed tolerance key {key!r}: expected {shape}")
    quantity = parts[1]
    if quantity != "*":
        if level in QUANTITIES and quantity not in QUANTITIES[level]:
            near = difflib.get_close_matches(quantity, QUANTITIES[level], n=1)
            raise _bad(where, key, f"unknown quantity {quantity!r} for level {level} (it compares {', '.join(QUANTITIES[level])}){f'; did you mean {level}.{near[0]}?' if near else ''}")
        if level in _OPEN_LEVELS and not _NAME.match(quantity.removesuffix("/unit") if level == "L3" else quantity):
            raise _bad(where, key, f"the quantity of a {level} key is a {'layer' if level == 'L3' else 'measure'} name (an identifier{', or `<layer>/unit`' if level == 'L3' else ''}), got {quantity!r}")
    if len(parts) == 3 and not _NAME.match(parts[2]):
        raise _bad(where, key, f"the asset class of {key!r} must be an identifier, got {parts[2]!r}")


def _number(v: Any, what: str, where: str, key: Any, *, positive: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, numbers.Real):
        hint = " (YAML reads `1e-3` without a dot as text: write 1.0e-3)" if isinstance(v, str) and re.fullmatch(r"[+-]?\d+[eE][+-]?\d+", v.strip()) else ""
        raise _bad(where, key, f"`{what}` must be a finite number, got {v!r}{hint}")
    f = float(v)
    if not math.isfinite(f) or f < 0.0 or (positive and f == 0.0):
        raise _bad(where, key, f"`{what}` must be a finite number {'greater than' if positive else 'of at least'} 0{' (it is a divisor)' if positive else ''}, got {v!r}")
    return f


def _check_value(key: str, v: Any, where: str) -> Tol:
    """The `Tol` a declaration means, or a ConfigError naming `<where>.<key>`."""
    if isinstance(v, Tol):
        v = {"rel": v.rel, "floor": v.floor, "expected": v.expected, "strict": v.strict, **({"reason": v.reason} if v.reason else {})}
    if isinstance(v, bool) or isinstance(v, numbers.Real):
        return Tol(_number(v, "rel", where, key))
    if not isinstance(v, Mapping):
        raise _bad(where, key, f"a tolerance must be a number or a mapping {{rel, floor, expected, strict, reason}}, got {v!r}")
    extra = sorted(map(str, set(v) - set(_FIELDS)))
    if extra or "rel" not in v:
        raise _bad(where, key, f"a tolerance mapping needs `rel` (and may give `floor`, `expected`, `strict`, `reason`); got {dict(v)}" + (f", unknown: {extra}" if extra else ""))
    for flag in ("expected", "strict"):
        if not isinstance(v.get(flag, False), bool):
            raise _bad(where, key, f"`{flag}` must be true or false, got {v[flag]!r}")
    reason = v.get("reason", "")
    if "reason" in v and (not isinstance(reason, str) or not reason.strip()):
        raise _bad(where, key, f"`reason` must be a non-empty string (why the tolerance is what it is), got {reason!r}")
    return Tol(_number(v["rel"], "rel", where, key), _number(v["floor"], "floor", where, key, positive=True) if "floor" in v else 1.0, v.get("expected", False), v.get("strict", False), reason)


def validate_declaration(mapping: Any, where: str) -> None:
    """Raise `ConfigError` (code `CFG-TIEOUT`, path `<where>.<key>`) unless `mapping` is a valid tolerance declaration (`tieout.tolerances`): a mapping of `<level>.<quantity>[.<asset class>]`
    keys (level L1-L4, a quantity the harness compares, `*` for the whole level) to a number (the relative tolerance) or a mapping `{rel, floor, expected, strict, reason}` (`rel` required and
    finite and >= 0, `floor` finite and > 0, `expected`/`strict` booleans, `reason` a non-empty string). Returns None; never raises anything else."""
    if not isinstance(mapping, Mapping):
        raise ConfigError(f"must be a mapping {{<level>.<quantity>[.<asset class>]: <number | {{rel, floor, expected, strict, reason}}>}}, got {mapping!r}", path=where, code="CFG-TYPE")
    for key, value in mapping.items():
        _check_key(key, where)
        _check_value(key, value, where)


def declared_tolerances(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """The validated `tieout.tolerances` of a LOADED config (what `run_tieout` reads itself, so it cannot bypass the loader's check)."""
    tie = cfg.get("tieout")
    if tie is None:
        return {}
    if not isinstance(tie, Mapping):
        raise ConfigError("tieout must be a mapping {tolerances: {...}}", path="tieout", code="CFG-TYPE")
    declared = tie.get("tolerances")
    if declared is None:
        return {}
    validate_declaration(declared, "tieout.tolerances")
    return dict(declared)


def _covers(explicit: str, declared: str) -> bool:
    e, d = explicit.split("."), declared.split(".")
    return e == d or (len(e) == 2 and e[0] == d[0] and (e[1] == "*" or e[1] == d[1]))  # `L1.pv` covers `L1.pv.<class>`; `L1.*` covers `L1.<q>` and `L1.<q>.<class>`


def merge_declarations(declared: Mapping[str, Any], explicit: Mapping[str, Any]) -> Dict[str, Any]:
    """`declared` (the config's `tieout.tolerances`) under `explicit` (an argument): an explicit key wins at every specificity for what it NAMES, so it removes every declared key it
    COVERS (`L1.pv` covers `L1.pv.<class>`; `L1.*` covers `L1.<q>` and `L1.<q>.<class>`) and leaves the rest (a more specific explicit key does not remove a broader declared one)."""
    validate_declaration(declared, "tieout.tolerances")
    validate_declaration(explicit, "tolerances")
    return {**{k: v for k, v in declared.items() if not any(_covers(e, k) for e in explicit)}, **explicit}


def floor_unit(level: str, quantity: str) -> str:
    """What a floor is measured in, for the report: the compared value itself (not a market unit; the floor does not scale with the trade size, see the module doc)."""
    q, per_unit = quantity.split("/")[0], quantity.endswith("/unit")
    if (level, q) == ("L1", "quantity"):
        return "position size"
    if (level, q) == ("L4", "stats"):
        return "the statistic's own unit"
    if (level, q) == ("L4", "stats_ratios"):
        return "the ratio (no unit)"
    if (level, q) == ("L4", "stats_traded"):
        return "currency traded"
    if level == "L2":
        return "the measure's own unit, per unit of position" + (", per bucket" if "." in quantity else "")
    return "value" + (", per unit of position" if per_unit else "")


class Tolerances:
    """A lookup over `DEFAULT_TOLERANCES` overridden by a config mapping (`tieout.tolerances`): a number is a relative tolerance, a mapping gives `rel`, `floor`, `expected`, `strict`, `reason`.
    The mapping is validated by `validate_declaration`."""

    def __init__(self, overrides: Optional[Mapping[str, Any]] = None):
        declared = dict(overrides or {})
        validate_declaration(declared, "tolerances")
        self.defaults: Dict[str, Tol] = dict(DEFAULT_TOLERANCES)
        self.declared: Dict[str, Tol] = {k: _check_value(k, v, "tolerances") for k, v in declared.items()}
        self.table: Dict[str, Tol] = {**self.defaults, **self.declared}

    def for_(self, level: str, quantity: str, asset_class: str = "") -> Tol:
        """Most specific first, and a declaration beats a default at the same specificity. A wildcard DECLARATION (`L1.*`) does not override a strict shipped default (a position size).
        A per-unit layer (`carry/unit`) takes its own key first and then the layer's."""
        specific = [k for q in dict.fromkeys((quantity, quantity.split("/")[0])) for k in ((f"{level}.{q}.{asset_class}" if asset_class else ""), f"{level}.{q}") if k]
        wildcard = f"{level}.*"
        for key in specific:  # 1. a declaration that names the quantity
            if key in self.declared:
                return self.declared[key]
        for key in specific:  # 2. a strict shipped default (an invariant) is not widened by a wildcard
            if key in self.defaults and self.defaults[key].strict:
                return self.defaults[key]
        if wildcard in self.declared:  # 3. a declared wildcard beats every other shipped default
            return self.declared[wildcard]
        for key in (*specific, wildcard):  # 4. the shipped defaults, most specific first
            if key in self.defaults:
                return self.defaults[key]
        raise ConfigError(f"no tolerance for {level}.{quantity}; declare `{level}.*` or `{level}.{quantity}`", code="CFG-TIEOUT")
