"""The dotted-path allow-list (spec B3): config strings are code execution, so a path may only reach code OWNED by an allowed package.

Found by the adversarial review of the contracts package: checking only the MODULE prefix let `allowed.mod:yaml.unsafe_load` walk through an imported
foreign module. Every object on the attribute path must be owned by an allowed prefix, and private names are never reachable.
"""
from __future__ import annotations

import pytest

from pricebt.errors import RegistryError
from pricebt.registry import resolve_dotted

pytestmark = pytest.mark.core

ALLOW = ("pricebt", "spec_helpers")


def code(path, allow=ALLOW):
    with pytest.raises(RegistryError) as ei:
        resolve_dotted(path, allow=allow)
    return ei.value.code


def test_legitimate_paths_still_resolve():
    from pricebt.contracts.spec import Kit
    from pricebt.pricer import PricerBase

    assert resolve_dotted("pricebt.contracts.spec:Kit", allow=ALLOW) is Kit
    assert resolve_dotted("pricebt.pricer:PricerBase.lookup", allow=ALLOW) is PricerBase.lookup  # class attribute chains are how `Cls.build` factories are named
    assert callable(resolve_dotted("spec_helpers:weird_factory", allow=ALLOW))
    assert resolve_dotted("pricebt.contracts.binding:KINDS", allow=ALLOW) == ("method", "function", "pricer_method", "attribute")  # module-level plain data
    assert code("pricebt.contracts.binding:TENOR") == "CFG-ALLOW"  # ... but an object owned by the stdlib (a compiled regex) is not ours to hand out


def test_the_control_a_module_outside_the_allow_list_is_refused():
    assert code("os:getcwd") == "CFG-ALLOW"


@pytest.mark.parametrize("path", [
    "pricebt.contracts.schema:yaml.unsafe_load",  # re-exported foreign module, then its function
    "pricebt.contracts.schema:yaml",  # the foreign module itself
    "pricebt.registry:importlib.import_module",
    "pricebt.contracts.binding:pd.read_pickle",
    "pricebt.contracts.binding:inspect",
])
def test_an_attribute_path_may_not_walk_into_code_owned_by_a_foreign_package(path):
    assert code(path) == "CFG-ALLOW"


def test_a_function_imported_into_an_allowed_module_is_owned_by_its_defining_module():
    assert code("pricebt.contracts.binding:difflib.get_close_matches") == "CFG-ALLOW"
    assert callable(resolve_dotted("pricebt.contracts.binding:resolve_dotted", allow=ALLOW))  # defined in pricebt.registry, which is allowed


@pytest.mark.parametrize("path", ["pricebt.contracts.spec:Kit.__init__", "pricebt.contracts.spec:Kit.__dict__", "pricebt.contracts.spec:_err", "spec_helpers:Weird.__class__"])
def test_private_and_dunder_names_are_never_reachable(path):
    assert code(path) == "CFG-ALLOW"


def test_the_allow_list_is_checked_on_dotted_prefix_boundaries_not_string_prefixes():
    assert code("pricebt_evil.mod:x") == "CFG-ALLOW"
    assert code("spec_helpers_other:x") == "CFG-ALLOW"


def test_a_missing_attribute_is_a_clean_error():
    assert code("pricebt.contracts.spec:NoSuchThing") == "CFG-IMPORT"
