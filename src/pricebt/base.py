"""EnumBase, Priceable marker, static_field, field_metadata, exclude_none, get_enum_value.

Ported from gs_quant.base (Apache-2.0; see NOTICE), minus the JSON/dataclasses_json plumbing that
gs uses for its own server-side serialisation: pricebt has no such wire format, so
`field_metadata` is `None` ("no metadata") instead of a `dataclasses_json` config object.

This module sits near the root of the import DAG (DESIGN.md 3.2): it imports nothing of
pricebt's own.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import field

logger = logging.getLogger(__name__)


class EnumBase:
    """Mixin placed first in an enum's MRO (`class X(EnumBase, str, Enum)`) so that its methods
    win over `str`'s and `Enum`'s: case-insensitive construction from a string, and `str`/`repr`
    that print the value rather than `ClassName.MEMBER`."""

    @classmethod
    def _missing_(cls, value):
        # str() on both sides: most pricebt enums are `(EnumBase, str, Enum)` with string values,
        # but DESIGN.md also uses this mixin on a plain int-valued enum (`Environment`, P2.3), so
        # this must not assume `member.value` is itself a string.
        lowered = str(value).lower()
        for member in cls.__members__.values():
            if str(member.value).lower() == lowered:
                return member
        return None

    def __str__(self) -> str:
        return str(self.value)

    def __repr__(self) -> str:
        return str(self)

    def __lt__(self, other) -> bool:
        other_value = other.value if isinstance(other, EnumBase) else other
        return self.value < other_value


class Priceable:
    """Marker for anything an asset config or the pricing service can price or resolve
    (instruments, `Portfolio`). Real behaviour (`resolve`/`calc`/`scale`/...) is added on top of
    this in P2.1/P2.2; it exists now only so those modules have a common base to inherit from."""


def static_field(val):
    """A dataclass field that is not a constructor argument, fixed at `val` (ported:
    `gs_quant.base.static_field`). Used for `class_type` fields on ported dataclasses."""
    return field(init=False, default=val)


# gs's `field_metadata` carries a `dataclasses_json` `config(exclude=exclude_none)` for GS
# server-side JSON encoding. pricebt does not serialise to that wire format, so this is `None`
# ("no metadata") -- DESIGN.md section 9.2's import map for `gs_quant.base.field_metadata`.
field_metadata = None


def exclude_none(o) -> bool:
    """Ported: `gs_quant.base.exclude_none`. True when a field should be dropped from a dict/JSON
    view because it is unset."""
    return o is None


class DataContract:
    """The gs `Base` object contract a ported dataclass keeps (as the instruments do, DESIGN.md
    section 5.1): `as_dict`, `to_dict`, `clone`. gs's JSON/schema plumbing is not ported (no wire
    format): `to_dict` is `as_dict`, snake_case keys and native values."""

    def as_dict(self, as_camel_case: bool = False) -> dict:
        out = {f.name: getattr(self, f.name) for f in dataclasses.fields(self) if getattr(self, f.name) is not None}
        if as_camel_case:
            out = {k.split("_")[0] + "".join(p.title() for p in k.split("_")[1:]): v for k, v in out.items()}
        return out

    def to_dict(self, encode_json: bool = False) -> dict:
        return self.as_dict()

    def clone(self, **kwargs):
        return dataclasses.replace(self, **kwargs)


_SCENARIO_STACK: list = []  # entered scenarios, outermost first


class _ScenarioMeta(type):
    @property
    def path(cls) -> tuple:
        """The entered scenarios, innermost first (gs `ContextBase.path`)."""
        return tuple(reversed(_SCENARIO_STACK))

    @property
    def current(cls):
        """The innermost entered scenario, else None (gs raises `MqUninitialisedError`)."""
        return _SCENARIO_STACK[-1] if _SCENARIO_STACK else None


class Scenario(DataContract, metaclass=_ScenarioMeta):
    """Base of the gs scenario classes (`gs_quant.base.Scenario`; the classes live in
    `pricebt.risk.scenarios`). A scenario is a context manager: `with scenario:` pushes it onto
    `Scenario.path`, and every `calc` inside prices under the stack, outermost first (gs
    `CompositeScenario(reversed(path))`). pricebt prices nothing itself: each asset config's
    `scenarios:` section maps a `scenario_type` to a market transform (docs/v2/SCENARIOS.md)."""

    @property
    def is_entered(self) -> bool:
        return any(s is self for s in _SCENARIO_STACK)

    def __enter__(self):
        _SCENARIO_STACK.append(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        for i in range(len(_SCENARIO_STACK) - 1, -1, -1):
            if _SCENARIO_STACK[i] is self:
                del _SCENARIO_STACK[i]
                break
        return False

    def __repr__(self) -> str:
        name = getattr(self, "name", None)
        if name:
            return name
        params = {k: v for k, v in vars(self).items() if v is not None and k not in ("name", "scenario_type")}
        body = ", ".join(f"{k}:{params[k]!r}" for k in sorted(params, key=str.lower))
        return f"{self.scenario_type}({body})"

    def __lt__(self, other) -> bool:
        # pricebt DEV-S6: total order on the repr (gs compares `name`s, so two unnamed scenarios
        # raise TypeError and `to_frame` cannot pivot on them)
        return repr(self) < repr(other)

    def _roll_target(self, current):
        """The valuation date this scenario moves `current` to, or None when it does not move it
        (pricebt DEV-S2: only `RollFwd` and `CarryScenario` roll; a custom subclass may override)."""
        return None


def get_enum_value(enum_type: type, value):
    """Lenient enum coercion (ported: `gs_quant.base.get_enum_value`). `None` stays `None`; an
    already-valid member passes through unchanged; an invalid value is logged and returned as-is
    rather than raising. Direct enum construction (`SomeEnum(value)`) is strict and DOES raise --
    this helper is only for the call sites gs explicitly makes lenient (R04 section 4)."""
    if value is None:
        return None
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError:
        logger.warning("Setting value to %r, which is not a valid entry in %s", value, enum_type)
        return value
