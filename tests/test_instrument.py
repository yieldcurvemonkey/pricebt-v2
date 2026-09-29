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
    Bond,
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


def test_bond_is_a_generated_class_that_scales_via_quantity():
    # IR_RISK_DESIGN.md section 4.1: gs Bond, fields from the 1.5.4 snapshot (the parity test pins
    # the exact signature); pricebt DEV-I1 scales it through quantity_ (gs Bond.scale() raises).
    from pricebt.common import AssetClass, AssetType, UnderlierType

    bond = Bond("buy", "US912810TM08", "isin", 1e6, name="ust")
    assert (bond.buy_sell, bond.identifier_type, bond.size) == (BuySell.Buy, UnderlierType.ISIN, 1e6)
    assert (Bond.asset_class, Bond.type_) == (AssetClass.Cross_Asset, AssetType.Bond)
    bond._set_resolution({"size": 1e6}, None, None, None)
    bond.scale(-2)
    assert bond.quantity_ == -2.0
    assert bond.size == 1e6  # size fields are never edited (DEV-I1)


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


# --------------------------------------------------------------------------------- __setattr__ (IR_RISK_DESIGN R2-29, DEV-I14)


def test_setting_a_gs_field_writes_kwargs_coerced_and_none_deletes():
    """gs Base.__setattr__ (gs notebook 03_set-a-property): the new value is what gets priced, so it
    lands in _kwargs (and therefore as_dict and the identity), not in a shadowing attribute."""
    swap = IRSwap("Pay", "7y", "USD", 1e4, name="a")
    before = hash(swap)
    swap.termination_date = "10y"
    swap.payOrReceive = "Receive"  # camelCase names the snake_case field (gs)
    assert swap.kwargs["termination_date"] == "10y" and swap.as_dict()["termination_date"] == "10y"
    assert swap.pay_or_receive is PayReceive("Receive")  # enum-coerced like the constructor
    assert "termination_date" not in vars(swap)  # no shadowing instance attribute
    assert hash(swap) != before
    swap.fixed_rate = 0.02
    swap.fixed_rate = None
    assert "fixed_rate" not in swap.kwargs
    with pytest.raises(ValueError):
        swap.notional_currency = "not a currency"


def test_setting_a_gs_field_on_a_resolved_instrument_raises():
    swap = IRSwap("Pay", "7y", "USD", 1e4, name="a")
    swap._set_resolution({"termination_date": "2031-01-01"}, None, None, None)
    with pytest.raises(ValueError, match="resolved"):
        swap.termination_date = "10y"
    swap.name = "b"  # name and quantity_ stay plain attributes, resolved or not
    swap.quantity_ = 2.0
    assert (swap.name, swap.quantity_) == ("b", 2.0)


@pytest.mark.parametrize("key", ["asset_class", "assetClass", "type_", "type"])
def test_asset_class_and_type_cannot_be_set(key):
    with pytest.raises(ValueError, match=f"^{key} cannot be set$"):
        setattr(IRSwap("Pay", "7y", "USD", 1e4), key, "x")


def test_non_field_names_are_plain_attributes():
    swap = IRSwap("Pay", "7y", "USD", 1e4)
    swap.anything_else = 1
    swap.camelExtra = 2  # not a gs field: stored under the name given (gs)
    assert vars(swap)["anything_else"] == 1 and vars(swap)["camelExtra"] == 2
    assert "anything_else" not in swap.kwargs
    ci = ConfigInstrument("toy", foo=1)
    ci.foo = 2  # ConfigInstrument has no gs fields
    assert ci.kwargs == {"foo": 1} and vars(ci)["foo"] == 2


def test_camel_case_reads_resolve_to_the_snake_case_field():
    swap = IRSwap("Pay", "7y", "USD", 1e4, fixed_rate=0.03)
    assert swap.fixedRate == 0.03 and swap.terminationDate == "7y"
    with pytest.raises(AttributeError):
        swap.notAField


def test_identity_key_is_memoised_when_resolved_and_dropped_on_reassignment():
    swap = IRSwap("Pay", "7y", "USD", 1e4, name="a")
    swap._identity_key()
    assert "_identity" not in vars(swap)  # unresolved: never memoised
    swap._set_resolution({"termination_date": "2031-01-01"}, None, None, None)
    key = swap._identity_key()
    assert vars(swap)["_identity"] is key
    other = swap.clone()
    for attr, value in (("name", "b"), ("quantity_", 3.0), ("pricebt_asset", "x")):
        setattr(swap, attr, value)
        assert "_identity" not in vars(swap)
        assert swap != other and swap._identity_key() == (IRSwap, swap.pricebt_asset, other._identity_key()[2], swap.quantity_, swap.name, other._identity_key()[5])
        setattr(swap, attr, getattr(other, attr))
        assert swap == other
    swap._set_resolution({"termination_date": "2032-01-01"}, None, None, None)
    assert swap != other
    scaled = other.scale(2.0, in_place=False)  # a deepcopy carries the memo; reassigning quantity_ drops it
    assert scaled.quantity_ == 2.0 and scaled._identity_key()[3] == 2.0 and scaled != other


def test_setting_a_field_changes_the_toy_price():
    from datetime import date

    from pricebt.markets import PricingContext
    from pricebt.session import PricebtSession

    PricebtSession.use(assets=[Path(__file__).parent / "assets" / "toy_usd_irs.yaml"])
    swap = IRSwap("Pay", "7y", "USD", 1e6, fixed_rate=0.03)
    with PricingContext(date(2024, 3, 4)):
        seven = swap.price().result()
        swap.termination_date = "10y"
        ten = swap.price().result()
    assert seven != ten
    with PricingContext(date(2024, 3, 4)):
        assert ten == IRSwap("Pay", "10y", "USD", 1e6, fixed_rate=0.03).price().result()
