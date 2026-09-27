"""EnumBase, Priceable marker, static_field, field_metadata, exclude_none, get_enum_value.

Ported from gs_quant.base (Apache-2.0; see NOTICE), minus the JSON/dataclasses_json plumbing that
gs uses for its own server-side serialisation: pricebt has no such wire format, so
`field_metadata` is `None` ("no metadata") instead of a `dataclasses_json` config object.

This module sits near the root of the import DAG (DESIGN.md 3.2): it imports nothing of
pricebt's own.
"""
from __future__ import annotations

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
