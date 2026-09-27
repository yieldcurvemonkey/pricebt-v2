"""The shared convention vocabulary as the QuantLib adapter reads it (spec T5): strict, explicit, no implicit defaults."""
import copy

import pytest

pytest.importorskip("QuantLib")
pytestmark = pytest.mark.adapter_quantlib

from pricebt.contracts.schema import SchemaRegistry  # noqa: E402
from pricebt.contrib.quantlib import BOND_KEYS, SWAP_KEYS, USD_SOFR_OIS_CONVENTIONS, UST_CONVENTIONS  # noqa: E402
from pricebt.contrib.quantlib.conventions import bond_conventions, swap_conventions  # noqa: E402
from pricebt.errors import ConfigError  # noqa: E402


def swap_with(**kw):
    return {**USD_SOFR_OIS_CONVENTIONS, **kw}


def bond_with(**kw):
    return {**UST_CONVENTIONS, **kw}


def test_the_shipped_blocks_are_complete_and_accepted():
    s, b = swap_conventions(USD_SOFR_OIS_CONVENTIONS), bond_conventions(UST_CONVENTIONS)
    assert (s.calendar, s.spot_lag_days, s.payment_lag_days, s.day_count, s.frequency, s.stub, s.end_of_month, s.fixing_lag_days) == ("nyc", 2, 2, "act360", "annual", "short_front", False, 1)
    assert (b.day_count, b.yield_convention, b.end_of_month, b.payment_business_day_convention, b.financing_day_count) == ("actact_icma", "treasury", True, "following", "act360")
    assert s.freq_months == 12


@pytest.mark.parametrize("make, conv", [(swap_conventions, USD_SOFR_OIS_CONVENTIONS), (bond_conventions, UST_CONVENTIONS)])
def test_an_unknown_key_is_an_error_never_ignored(make, conv):
    with pytest.raises(ConfigError, match="unknown convention keys") as e:
        make({**conv, "dayCount": "act360"})
    assert e.value.code == "CFG-CONVENTION" and "dayCount" in str(e.value)


@pytest.mark.parametrize("make, conv", [(swap_conventions, USD_SOFR_OIS_CONVENTIONS), (bond_conventions, UST_CONVENTIONS)])
def test_a_missing_key_is_an_error_there_are_no_implicit_defaults(make, conv):
    for key in conv:
        cut = {k: v for k, v in conv.items() if k != key}
        with pytest.raises(ConfigError, match="missing convention keys") as e:
            make(cut)
        assert key in str(e.value)


def test_an_unknown_value_token_is_rejected_with_the_vocabulary():
    with pytest.raises(ConfigError, match="must be one of") as e:
        swap_conventions(swap_with(day_count="Actual360"))  # a library spelling, not a neutral token
    assert "act360" in str(e.value) and "'Actual360'" in str(e.value) and e.value.code == "CFG-CONVENTION"
    with pytest.raises(ConfigError, match="must be one of"):
        swap_conventions(swap_with(business_day_convention="MF"))
    with pytest.raises(ConfigError, match="must be one of"):
        bond_conventions(bond_with(yield_convention="us_gb_tsy"))


def test_a_vocabulary_token_this_adapter_does_not_support_names_the_reason():
    with pytest.raises(ConfigError, match="does not support it") as e:
        bond_conventions(bond_with(yield_convention="street"))
    assert e.value.code == "CFG-CONVENTION-UNSUPPORTED" and "final coupon period" in str(e.value)
    with pytest.raises(ConfigError, match="linear intraday"):
        swap_conventions(swap_with(time_accrual="linear"))
    with pytest.raises(ConfigError, match="does not support it"):
        swap_conventions(swap_with(day_count="thirty360"))
    with pytest.raises(ConfigError, match="not verified against the reference adapter") as e2:  # in the vocabulary, never compared against rateslib: refused, not silently accepted
        swap_conventions(swap_with(day_count="act365f"))
    assert e2.value.code == "CFG-CONVENTION-UNSUPPORTED"
    with pytest.raises(ConfigError, match="forward value"):  # a later settlement would need a forward value
        bond_conventions(bond_with(settlement_lag_days=1))


def test_types_are_checked():
    for bad in (True, -1, 2.0, "2"):
        with pytest.raises(ConfigError, match="non-negative integer"):
            swap_conventions(swap_with(spot_lag_days=bad))
    with pytest.raises(ConfigError, match="true or false"):
        bond_conventions(bond_with(end_of_month="yes"))
    with pytest.raises(ConfigError, match="non-empty name"):
        swap_conventions(swap_with(calendar=""))
    with pytest.raises(ConfigError, match="must be a mapping"):
        swap_conventions(["act360"])


def test_lags_other_than_the_default_are_accepted_for_the_swap():
    c = swap_conventions(swap_with(spot_lag_days=1, payment_lag_days=0, frequency="semiannual", business_day_convention="following"))
    assert (c.spot_lag_days, c.payment_lag_days, c.freq_months, c.business_day_convention) == (1, 0, 6, "following")


def test_the_published_key_lists_are_the_vocabulary():
    assert set(SWAP_KEYS) == set(USD_SOFR_OIS_CONVENTIONS) and set(BOND_KEYS) == set(UST_CONVENTIONS)
    assert "act360" in SWAP_KEYS["day_count"] and "treasury" in BOND_KEYS["yield_convention"]


def test_the_vocabulary_is_the_shipped_schemas_and_every_key_has_a_stated_support_decision():
    from pricebt.contrib.quantlib.conventions import _BOND_SUPPORTED, _SWAP_SUPPORTED

    reg = SchemaRegistry.default()
    for ac, keys, supported in (("swap", SWAP_KEYS, _SWAP_SUPPORTED), ("bond", BOND_KEYS, _BOND_SUPPORTED)):
        declared = set(reg.get(ac).conventions)
        assert declared and declared == set(keys) == set(supported), (ac, sorted(declared ^ set(supported)))


def test_the_shipped_dicts_are_not_mutated_by_validation():
    before = copy.deepcopy((dict(USD_SOFR_OIS_CONVENTIONS), dict(UST_CONVENTIONS)))
    swap_conventions(USD_SOFR_OIS_CONVENTIONS)
    bond_conventions(UST_CONVENTIONS)
    assert (dict(USD_SOFR_OIS_CONVENTIONS), dict(UST_CONVENTIONS)) == before
