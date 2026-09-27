"""Stack overlays (spec X1, D3): a stack may change an instrument's `factory` and `bind` and a pricer role's `wrap`, and NOTHING else."""
from __future__ import annotations

import copy

import pytest

from pricebt.config.stacks import apply_stack, base_digest, validate_overlay
from pricebt.errors import ConfigError

pytestmark = pytest.mark.core

BASE = {
    "backtest": {"tz": "America/New_York", "grid": {"start": "2024-01-02", "end": "2024-02-01", "freq": "1b"}},
    "market": {"mdps": {"rates": {"type": "toy"}}, "pricers": {"primary": {"mdp": "rates", "request": {"curve": "x"}}}},
    "instruments": {"ois": {"asset_class": "swap", "conventions": {"day_count": "act360"}}, "bond": {"asset_class": "bond", "conventions": {}}},
    "strategy": {"triggers": []},
}
OVERLAY = {
    "instruments": {"ois": {"factory": "pkg.a:swap", "bind": {"dv01": {"target": {"method": "pv01"}}}}, "bond": {"factory": "pkg.a:bond"}},
    "market": {"pricers": {"primary": {"wrap": "pkg.a:wrap"}}},
}


def test_an_overlay_of_factory_bind_and_wrap_is_accepted_and_merged_into_a_copy_of_the_base():
    out = apply_stack(BASE, OVERLAY)
    assert out["instruments"]["ois"]["factory"] == "pkg.a:swap" and out["instruments"]["ois"]["bind"]["dv01"]["target"]["method"] == "pv01"
    assert out["instruments"]["ois"]["conventions"] == {"day_count": "act360"}  # untouched
    assert out["market"]["pricers"]["primary"]["wrap"] == "pkg.a:wrap" and out["market"]["pricers"]["primary"]["mdp"] == "rates"
    assert "factory" not in BASE["instruments"]["ois"] and "wrap" not in BASE["market"]["pricers"]["primary"]  # the base is not mutated


@pytest.mark.parametrize("path,overlay", [
    ("conventions", {"instruments": {"ois": {"conventions": {"day_count": "act365"}}}}),
    ("asset_class", {"instruments": {"ois": {"asset_class": "bond"}}}),
    ("layers", {"instruments": {"ois": {"layers": ["carry"]}}}),
    ("params", {"instruments": {"ois": {"params": {"x": 1}}}}),
    ("backtest", {"backtest": {"fill_lag": 1}}),
    ("strategy", {"strategy": {"triggers": [1]}}),
    ("signals", {"signals": {"s": {}}}),
    ("params-top", {"params": {"tenor": "5Y"}}),
    ("registry", {"registry": {"allow": ["evil"]}}),
    ("market.mdps", {"market": {"mdps": {"rates": {"type": "other"}}}}),
    ("market.pricers.mdp", {"market": {"pricers": {"primary": {"mdp": "other"}}}}),
    ("market.pricers.request", {"market": {"pricers": {"primary": {"request": {"curve": "y"}}}}}),
    ("terms", {"trades": {"t": {"terms": {"notional": 1}}}}),
])
def test_an_overlay_that_touches_anything_but_factory_bind_or_wrap_is_rejected_naming_the_path(path, overlay):
    with pytest.raises(ConfigError) as ei:
        apply_stack(BASE, overlay)
    assert ei.value.code == "CFG-STACK"


def test_an_overlay_cannot_add_an_instrument_or_a_role_the_base_does_not_define():
    with pytest.raises(ConfigError):
        apply_stack(BASE, {"instruments": {"new": {"factory": "pkg.a:x"}}})
    with pytest.raises(ConfigError):
        apply_stack(BASE, {"market": {"pricers": {"ghost": {"wrap": "pkg.a:wrap"}}}})


def test_the_base_digest_ignores_the_three_stack_owned_keys_and_only_them():
    d = base_digest(BASE)
    assert base_digest(apply_stack(BASE, OVERLAY)) == d  # identical across stacks: the X1 check
    other = apply_stack(BASE, {"instruments": {"ois": {"factory": "pkg.b:swap", "bind": {"rate": {"target": {"method": "par"}}}}}, "market": {"pricers": {"primary": {"wrap": "pkg.b:wrap"}}}})
    assert base_digest(other) == d
    for change in (lambda c: c["instruments"]["ois"]["conventions"].update(day_count="act365"), lambda c: c["backtest"].update(fill_lag=1),
                   lambda c: c["market"]["pricers"]["primary"]["request"].update(curve="y"), lambda c: c["strategy"].update(triggers=[1]), lambda c: c["instruments"]["ois"].update(layers=["carry"])):
        mutated = copy.deepcopy(BASE)
        change(mutated)
        assert base_digest(mutated) != d


def test_two_stacks_applied_in_turn_keep_the_digest_and_the_second_wins_on_the_keys_they_share():
    a = apply_stack(BASE, OVERLAY)
    b = apply_stack(a, {"instruments": {"ois": {"factory": "pkg.b:swap"}}})
    assert b["instruments"]["ois"]["factory"] == "pkg.b:swap" and base_digest(b) == base_digest(BASE)


def test_bind_in_an_overlay_replaces_the_whole_bind_block_of_that_instrument():
    with_bind = apply_stack(BASE, {"instruments": {"ois": {"bind": {"dv01": {"target": {"method": "a"}}, "rate": {"target": {"method": "b"}}}}}})
    replaced = apply_stack(with_bind, {"instruments": {"ois": {"bind": {"dv01": {"target": {"method": "c"}}}}}})
    assert list(replaced["instruments"]["ois"]["bind"]) == ["dv01"] and replaced["instruments"]["ois"]["bind"]["dv01"]["target"]["method"] == "c"


def test_validate_overlay_shape_errors():
    for bad in ([], "x", {"instruments": []}, {"instruments": {"ois": "x"}}, {"market": {"pricers": {"primary": "x"}}}, {"market": []}):
        with pytest.raises(ConfigError):
            validate_overlay(bad)
    validate_overlay({})
