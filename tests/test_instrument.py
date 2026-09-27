"""P2.1: pricebt.instrument (DESIGN.md section 5)."""
from __future__ import annotations

import copy
import inspect
import json
import pickle
from pathlib import Path

import pytest

from pricebt.errors import ConfigError, PricebtError
from pricebt import instrument as instrument_mod
from pricebt.instrument import (
    BuySell,
    Cash,
    ConfigInstrument,
    Currency,
    EqOption,
    FXForward,
    FXOption,
    InflationSwap,
    IRSwap,
    IRSwaption,
    OptionStyle,
    OptionType,
    PayReceive,
    instrument_identity,
)
from pricebt.instrument._gs_fields import GS_FIELDS

DATA = Path(__file__).parent / "data" / "gs_instruments_1_5_4.json"


def _expected_irswap_params():
    """Derive the expected first-31 parameter list from the gs snapshot (never hand-transcribed)."""
    spec = json.loads(DATA.read_text())["dataclasses"]["IRSwap"]
    return [f[0] for f in spec["fields"] if f[1]]  # f = (name, is_init_field, default_repr, type_str)


# --------------------------------------------------------------------------------- signature parity


def test_irswap_signature_matches_gs_snapshot():
    expected = _expected_irswap_params()
    assert expected[-1] == "name"
    actual = list(inspect.signature(IRSwap).parameters)[: len(expected)]
    assert actual == expected


def test_irswap_signature_then_has_pricebt_extensions():
    params = inspect.signature(IRSwap).parameters
    names = list(params)
    n = len(_expected_irswap_params())
    assert names[n : n + 3] == ["pricebt_asset", "quantity_", "kwargs"]
    assert params["pricebt_asset"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["quantity_"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["kwargs"].kind == inspect.Parameter.VAR_KEYWORD


def test_positional_construction():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    assert swap.pay_or_receive is PayReceive.Pay
    assert swap.termination_date == "10y"
    assert swap.notional_currency is Currency.USD
    assert swap.notional_amount == 1e4


# --------------------------------------------------------------------------------- kwargs handling (section 5.2)


def test_camel_case_kwargs_convert_to_snake_case():
    o = EqOption("SX5E", underlier_type="BBID", expirationDate="3m", strikePrice="ATM", optionType="Call", optionStyle="European")
    assert o.expiration_date == "3m"
    assert o.strike_price == "ATM"
    assert o.option_type is OptionType.Call
    assert o.option_style is OptionStyle.European


def test_camel_and_snake_both_specified_raises():
    with pytest.raises(ValueError):
        EqOption("SX5E", option_type="Put", optionType="Call")


def test_enum_coercion_and_invalid_value():
    swap = IRSwap(pay_or_receive="receiver")  # gs alias, case-insensitive
    assert swap.pay_or_receive is PayReceive.Receive
    with pytest.raises(ValueError):
        IRSwap(pay_or_receive="Payy")


def test_unknown_extras_pass_through_untouched():
    swap = IRSwap(termination_date="10y", solve_for="par")
    assert swap.solve_for == "par"


# --------------------------------------------------------------------------------- eq / hash (section 5.1 item 3)


def test_eq_hash_include_name():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    b = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    c = IRSwap("Pay", "10y", "USD", 1e4, name="b")
    assert a == b and hash(a) == hash(b)
    assert a != c


def test_hash_changes_on_set_resolution_but_stable_across_a_no_op_clone():
    """DESIGN.md section 5.1 item 3: the hash tuple includes frozen `resolved_terms`, which starts
    as None. `_set_resolution` is what changes it (not "being priced": PricingService.value on an
    UNRESOLVED instrument resolves a *clone*, per section 6.2 rule 2, never mutating the caller's
    object -- that is the sense in which "the hash never changes when first priced"). So resolving
    an instrument in place DOES change its hash; a `clone()` that changes nothing keeps it stable."""
    swap = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    h_before = hash(swap)
    swap._set_resolution({"termination_date": "2035-01-01"}, None, None, None)
    h_after = hash(swap)
    assert h_after != h_before

    # Stable (unchanged) hash/eq across a clone that touches nothing.
    same = swap.clone()
    assert same == swap and hash(same) == hash(swap)

    # clone(name=...) keeps the resolution state, but is a DIFFERENT instrument (name is in the
    # hash tuple too) -- "stable" is about the resolution state surviving the clone, not the hash
    # value itself staying put when a hashed field (name) is deliberately changed.
    renamed = swap.clone(name="b")
    assert renamed.resolved_terms == swap.resolved_terms
    assert renamed.resolution_key == swap.resolution_key
    assert renamed != swap


def test_clone_name_keeps_resolution_state():
    swap = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    swap._set_resolution({"termination_date": "2035-01-01"}, "key1", "USD-1", None)
    renamed = swap.clone(name="b")
    assert renamed.name == "b"
    assert renamed.resolved_terms == {"termination_date": "2035-01-01"}
    assert renamed.resolution_key == "key1"
    assert renamed.resolution_csa == "USD-1"


def test_clone_gs_field_on_resolved_instrument_raises():
    swap = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    swap._set_resolution({"termination_date": "2035-01-01"}, None, None, None)
    with pytest.raises(ValueError):
        swap.clone(termination_date="2040-01-01")


def test_clone_gs_field_on_unresolved_instrument_updates_kwargs():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    updated = swap.clone(notional_amount=2e4)
    assert updated.notional_amount == 2e4
    assert swap.notional_amount == 1e4  # original untouched


# --------------------------------------------------------------------------------- scale (section 5.1 item 5)


def test_scale_unresolved_raises_by_default():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    with pytest.raises(RuntimeError):
        swap.scale(2)


def test_scale_unresolved_allowed_with_check_resolved_false():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    result = swap.scale(2, check_resolved=False)
    assert result is None
    assert swap.quantity_ == 2.0


def test_scale_none_returns_self():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    assert swap.scale(None) is swap


def test_scale_in_place_true_mutates_and_returns_none():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    swap._set_resolution({"termination_date": "2035-01-01"}, None, None, None)
    result = swap.scale(2)
    assert result is None
    assert swap.quantity_ == 2.0


def test_scale_not_in_place_returns_scaled_deepcopy():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    swap._set_resolution({"termination_date": "2035-01-01"}, None, None, None)
    result = swap.scale(3, in_place=False)
    assert result.quantity_ == 3.0
    assert swap.quantity_ == 1.0
    assert result is not swap


def test_flip_is_scale_minus_one():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    swap._set_resolution({"termination_date": "2035-01-01"}, None, None, None)
    swap.flip()
    assert swap.quantity_ == -1.0


# --------------------------------------------------------------------------------- deepcopy / pickle / getattr


def test_deepcopy_and_pickle_round_trip_no_session():
    swap = IRSwap("Pay", "10y", "USD", 1e4, name="x")
    copied = copy.deepcopy(swap)
    assert copied == swap and copied is not swap
    unpickled = pickle.loads(pickle.dumps(swap))
    assert unpickled == swap


def test_hasattr_on_unknown_field_is_false():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    assert hasattr(swap, "1m") is False
    assert hasattr(swap, "not_a_real_field") is False


def test_getattr_underscore_field_raises_immediately():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    with pytest.raises(AttributeError):
        swap._not_a_thing


def test_asset_config_without_session_raises_pricebt_error():
    swap = IRSwap("Pay", "10y", "USD", 1e4)
    with pytest.raises(PricebtError):
        swap.asset_config


def test_getattr_falls_through_to_resolved_terms_when_asset_cannot_be_matched(monkeypatch):
    """DESIGN.md section 5.1 item 2 step (4): a ConfigError from asset matching means "step 1 is
    ... skipped", not that __getattr__ raises ConfigError out of a plain attribute read."""
    swap = IRSwap("Pay", "10y", "USD", 1e4, name="a")
    swap._set_resolution({"termination_date": "2035-01-01"}, None, None, None)

    monkeypatch.setattr(instrument_mod, "_current_session", lambda: object())

    def _unmatched(self):
        raise ConfigError("no asset matches IRSwap(...)")

    monkeypatch.setattr(type(swap), "asset_config", property(_unmatched))

    assert swap.termination_date == "2035-01-01"
    # step (4): a field in neither resolved_terms nor _kwargs still ends in a plain AttributeError,
    # not a ConfigError, even with an (unmatchable) session active.
    assert hasattr(swap, "not_a_field") is False


# --------------------------------------------------------------------------------- dict views


def test_to_dict_has_no_name_as_dict_has_one():
    swap = IRSwap("Pay", "10y", "USD", 1e4, name="x")
    assert "name" not in swap.to_dict()
    assert swap.as_dict()["name"] == "x"


def test_as_dict_shape():
    swap = IRSwap("Pay", "10y", "USD", 1e4, name="x")
    d = swap.as_dict()
    assert d["asset_class"] is swap.asset_class
    assert d["type"] is swap.type_
    assert d["quantity_"] == 1.0
    assert d["notional_amount"] == 1e4


def test_as_dict_uses_resolved_terms_once_resolved():
    swap = IRSwap("Pay", "10y", "USD", 1e4, name="x")
    swap._set_resolution({"termination_date": "2035-01-01"}, None, None, None)
    d = swap.as_dict()
    assert d["termination_date"] == "2035-01-01"
    assert "notional_amount" not in d  # not in resolved_terms


# --------------------------------------------------------------------------------- other instrument classes


def test_irswaption_and_fx_and_eq_and_cash_construct():
    IRSwaption(pay_or_receive="Receiver", termination_date="10y", notional_currency="USD", expiration_date="1y", strike="A-50", name="s0")
    FXOption(buy_sell=BuySell.Buy, option_type=OptionType.Put, pair="USDCNH", strike_price="ATMF", expiration_date="1m", name="1m_put", premium=0)
    FXForward(pair="EURUSD", settlement_date="1y", notional_amount=1e5, name="1y_forward")
    InflationSwap(pay_or_receive="Pay", termination_date="30y", notional_currency="EUR", notional_amount=100e6, name="30yhedge")
    Cash(currency="USD", notional_amount=100)


def test_config_instrument():
    ci = ConfigInstrument("my_asset", name="c1", rate="ATM")
    assert ci.pricebt_asset == "my_asset"
    assert ci.asset_class is None and ci.type_ is None
    assert ci.rate == "ATM"


def test_instrument_identity():
    a = IRSwap("Pay", "10y", "USD", 1e4, name="a", pricebt_asset="usd_irs")
    b = IRSwap("Pay", "10y", "USD", 1e4, name="b", pricebt_asset="usd_irs")
    assert instrument_identity(a) == instrument_identity(b)  # name excluded from identity
    c = IRSwap("Pay", "5y", "USD", 1e4, name="a", pricebt_asset="usd_irs")
    assert instrument_identity(a) != instrument_identity(c)


def test_extension_names_do_not_collide():
    banned = {"pricebt_asset", "quantity_", "instrument_quantity", "kwargs", "asset_config", "resolved_terms", "position_meta"}
    for cls_name, spec in GS_FIELDS.items():
        field_names = {f for f, _default, _tag in spec["fields"]}
        collision = field_names & banned
        assert not collision, f"{cls_name} has a field colliding with a pricebt extension: {collision}"
