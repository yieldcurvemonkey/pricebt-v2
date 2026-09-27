"""Every adapter's layers pass the SAME independent identities (spec L4) on the library-free worlds: full revaluation vs delta + convexity, mirror symmetry,
cash-sweep continuity, and a bounded unexplained share. The kit itself is proven on sabotaged specs in `test_layer_conformance.py`."""
import datetime as dt
import importlib

import pytest

from pricebt.contracts.schema import SchemaRegistry
from pricebt.contracts.spec import build_spec
from pricebt.snapshot import Security
from pricebt.testing.layer_conformance import Setup, flat_bond_world, flat_swap_world, run_kit

HOL = (dt.date(2024, 3, 29), dt.date(2024, 5, 27), dt.date(2024, 7, 4))
SEC = Security("KIT001", 4.0, dt.date(2023, 11, 15), dt.date(2033, 11, 15))
ADAPTERS = [pytest.param("rateslib", marks=pytest.mark.adapter_rateslib), pytest.param("quantlib", marks=pytest.mark.adapter_quantlib)]


def adapter(name):
    pytest.importorskip("rateslib" if name == "rateslib" else "QuantLib")
    return importlib.import_module(f"pricebt.contrib.{name}")


def swap_setup(mod, **kw):
    conv = {**mod.USD_SOFR_OIS_CONVENTIONS, "calendar": "cal"}
    spec = build_spec("ois", {"factory": mod.swap, "conventions": conv}, schemas=SchemaRegistry.default())
    args = dict(spec=spec, wrap=mod.wrap, terms={"side": "pay", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2},
                mirror_terms={"side": "receive", "maturity": "5Y", "notional": 1e7, "fixed_rate": 4.2},
                world=flat_swap_world("cal", holidays=HOL), t0=dt.date(2024, 3, 4), t1=dt.date(2024, 3, 5))
    args.update(kw)
    return Setup(**args)


def bond_setup(mod, **kw):
    conv = {**mod.UST_CONVENTIONS, "calendar": "cal"}
    spec = build_spec("ust", {"factory": mod.bond, "conventions": conv}, schemas=SchemaRegistry.default())
    args = dict(spec=spec, wrap=mod.wrap, terms={"side": "buy", "security": SEC.id, "notional": 1e7}, mirror_terms={"side": "sell", "security": SEC.id, "notional": 1e7},
                world=flat_bond_world(SEC, "cal", holidays=HOL), t0=dt.date(2024, 6, 3), t1=dt.date(2024, 6, 4))
    args.update(kw)
    return Setup(**args)


@pytest.mark.parametrize("name", ADAPTERS)
def test_the_swap_layers_conform(name):
    run_kit(swap_setup(adapter(name))).assert_ok()


@pytest.mark.parametrize("name", ADAPTERS)
def test_the_seasoned_swap_layers_conform_across_a_payment_date(name):
    mod = adapter(name)
    setup = swap_setup(mod, terms={"side": "pay", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0}, mirror_terms={"side": "receive", "maturity": "3Y", "notional": 2e7, "fixed_rate": 4.0},
                       birth=dt.date(2023, 5, 15), t0=dt.date(2024, 5, 20), t1=dt.date(2024, 5, 21), payment_window=(dt.date(2024, 5, 21), dt.date(2024, 5, 23)))
    rep = run_kit(setup)
    rep.assert_ok()
    assert any(r.check == "cash_sweep" and r.quantity.startswith("value drop") for r in rep.rows)


@pytest.mark.parametrize("name", ADAPTERS)
def test_the_bond_layers_conform_including_the_coupon_sweep(name):
    rep = run_kit(bond_setup(adapter(name), payment_window=(dt.date(2024, 5, 14), dt.date(2024, 5, 16)), birth=dt.date(2024, 3, 4)))
    rep.assert_ok()
    assert any(r.check == "cash_sweep" and r.quantity.startswith("value drop") for r in rep.rows)
