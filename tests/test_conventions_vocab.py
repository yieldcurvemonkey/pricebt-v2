"""The conventions vocabulary is DATA in the shipped schemas (spec T5, S6): keys, types and tokens. `AssetSchema.check_conventions` is what an adapter (and the loader)
calls to refuse an unknown key, a wrong type, an unknown token and, when asked, a missing key. pricebt applies none of them."""
import pytest

from pricebt.contracts.schema import SchemaRegistry
from pricebt.errors import ConfigError

pytestmark = pytest.mark.core

OIS = {
    "calendar": "nyc_like", "spot_lag_days": 2, "day_count": "act360", "frequency": "annual", "business_day_convention": "modified_following", "payment_lag_days": 2,
    "compounding": "daily_compounded", "stub": "short_front", "end_of_month": False, "fixing_lag_days": 1, "time_accrual": "lump",
}
UST = {
    "calendar": "nyc_like", "settlement_lag_days": 0, "day_count": "actact_icma", "frequency": "semiannual", "business_day_convention": "unadjusted",
    "payment_business_day_convention": "following", "payment_lag_days": 0, "stub": "short_front", "end_of_month": True, "compounding": "semiannual",
    "yield_convention": "treasury", "ex_dividend_days": 0, "financing_day_count": "act360", "time_accrual": "lump",
}


@pytest.fixture(scope="module")
def reg():
    return SchemaRegistry.default()


def test_a_complete_block_passes_and_comes_back_as_a_plain_copy(reg):
    out = reg.get("swap").check_conventions(OIS, require_all=True)
    assert out == OIS and out is not OIS
    assert reg.get("bond").check_conventions(UST, require_all=True) == UST


def test_every_key_is_optional_unless_all_are_required(reg):
    assert reg.get("swap").check_conventions({"day_count": "act360"}) == {"day_count": "act360"}
    assert reg.get("swap").check_conventions({}) == {}
    with pytest.raises(ConfigError, match="missing") as e:
        reg.get("swap").check_conventions({"day_count": "act360"}, require_all=True)
    assert "payment_lag_days" in str(e.value) and e.value.code == "CFG-CONVENTION"


def test_unknown_keys_are_refused_with_a_suggestion(reg):
    with pytest.raises(ConfigError, match="unknown convention keys") as e:
        reg.get("swap").check_conventions({**OIS, "daycount": "act360"})
    assert "did you mean 'day_count'" in str(e.value) and e.value.code == "CFG-CONVENTION"


@pytest.mark.parametrize("key,bad", [
    ("day_count", "act_360"), ("frequency", "yearly"), ("business_day_convention", "mf"), ("compounding", "simple"), ("stub", "front"), ("time_accrual", "eod"),
    ("spot_lag_days", -1), ("spot_lag_days", 1.5), ("spot_lag_days", True), ("spot_lag_days", "2"), ("end_of_month", "no"), ("end_of_month", 0), ("calendar", ""), ("calendar", 3),
])
def test_wrong_types_and_unknown_tokens_are_refused(reg, key, bad):
    with pytest.raises(ConfigError) as e:
        reg.get("swap").check_conventions({**OIS, key: bad}, require_all=True)
    assert key in str(e.value) and e.value.code == "CFG-CONVENTION"


def test_a_calendar_name_is_stripped_and_a_block_is_never_mutated(reg):
    given = {"calendar": "  nyc_like  ", "day_count": "act360"}
    assert reg.get("swap").check_conventions(given) == {"calendar": "nyc_like", "day_count": "act360"} and given["calendar"] == "  nyc_like  "


def test_bond_vocabulary_has_its_own_tokens(reg):
    bond = reg.get("bond")
    with pytest.raises(ConfigError, match="yield_convention"):
        bond.check_conventions({**UST, "yield_convention": "japanese"})
    with pytest.raises(ConfigError, match="unknown convention keys"):
        bond.check_conventions({**UST, "fixing_lag_days": 1})  # a swap key
    assert bond.check_conventions({**UST, "yield_convention": "street"})["yield_convention"] == "street"


def test_the_block_must_be_a_mapping_with_string_keys(reg):
    with pytest.raises(ConfigError, match="mapping"):
        reg.get("swap").check_conventions([("day_count", "act360")])
    with pytest.raises(ConfigError, match="strings"):
        reg.get("swap").check_conventions({2: "x"})


def test_the_schema_vocabulary_is_data_with_types_and_tokens(reg):
    vocab = reg.get("swap").conventions
    assert vocab["day_count"].values and "act360" in vocab["day_count"].values and vocab["spot_lag_days"].type == "int" and vocab["end_of_month"].type == "bool"
    assert vocab["calendar"].type == "name"
    assert set(OIS) == set(vocab) and set(UST) == set(reg.get("bond").conventions)


def test_a_schema_file_cannot_declare_an_unknown_convention_type_or_a_token_on_a_non_token(tmp_path):
    from pricebt.contracts.schema import load_schema_text

    base = "schema_version: 1\nasset_class: t\nextends: generic\nconventions:\n  k: {type: %s%s}\n"
    reg = SchemaRegistry.default()
    reg.register(load_schema_text(base % ("int", ""), "t"))
    with pytest.raises(ConfigError, match="type"):
        reg.register(load_schema_text(base % ("colour", ""), "t"))
    with pytest.raises(ConfigError, match="values"):
        reg.register(load_schema_text(base % ("int", ", values: [1, 2]"), "t"))
    with pytest.raises(ConfigError, match="needs `values`"):
        reg.register(load_schema_text(base % ("token", ""), "t"))
