"""Market provider configs, their registry, and the assets that reference them
(docs/v2/MARKET_CONTEXT_DESIGN.md sections 3-4): loading and validation, `requires:` composition,
one market object per (provider, date, csa) shared by every asset that names the provider, and
numbers identical to the same market written inline in the asset config.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pandas.testing import assert_frame_equal

import toylib.rates as tr
from pricebt.assets import MarketConfig, MarketRegistry, load_asset, load_market, yamlio
from pricebt.assets.registry import AssetRegistry
from pricebt.backtests.actions import AddTradeAction
from pricebt.backtests.generic_engine import GenericEngine
from pricebt.backtests.strategy import Strategy
from pricebt.backtests.triggers import PeriodicTrigger, PeriodicTriggerRequirements
from pricebt.errors import AssetEvaluationError, ConfigError, MarketDataUnavailable
from pricebt.instrument import IRSwap, IRSwaption
from pricebt.markets import PricingContext
from pricebt.risk import IRDelta, IRFwdRate, IRVega, Price
from pricebt.session import PricebtSession

ASSETS = Path(__file__).parent / "assets"
MARKETS = Path(__file__).parent / "markets"
D = date(2024, 3, 4)


def _provider(name="p", expr="1", **extra):
    return {"schema_version": 1, "market": name, "expr": expr, **extra}


def _asset(name, market, **extra):
    return {
        "schema_version": 1,
        "asset": name,
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": market,
        "functions": {"f": {"expr": "market", "unit": "number"}},
        "risk_measures": {"Price": "f"},
        **extra,
    }


def on_provider(fixture: str, provider: str, **overrides) -> dict:
    """A toy asset fixture with its inline market replaced by a reference to `provider`: the
    rest of the config (imports, resolve, trade, functions, measures) is untouched."""
    raw = dict(yamlio.load_file(ASSETS / fixture))
    raw["market"] = {"provider": provider}
    raw.update(overrides)
    return raw


def _usd_swap(name="s"):
    return IRSwap("Pay", "10y", "USD", 1_000_000, name=name)


def _swaption(name="o"):
    return IRSwaption("Pay", "10y", "USD", expiration_date="1y", strike="ATM", notional_amount=1_000_000, buy_sell="Buy", name=name)


# ------------------------------------------------------------------------------------ load_market


def test_minimal_provider_loads_and_compiles_without_executing():
    cfg = load_market(_provider("curve", "undefined_name(pricebt_date)", imports="import not_a_module"))
    assert isinstance(cfg, MarketConfig)
    assert (cfg.name, cfg.expr, cfg.requires, cfg.source) == ("curve", "undefined_name(pricebt_date)", (), "<mapping>")
    assert cfg.expr_src("market") == cfg.expr and cfg.code("market") is not None


def test_name_defaults_to_the_file_stem(tmp_path):
    path = tmp_path / "my_curve.yaml"
    path.write_text("schema_version: 1\nexpr: '1'\n", encoding="utf8")
    assert load_market(path).name == "my_curve"
    assert load_market(MARKETS / "toy_usd_ois.yaml").name == "toy_usd_ois"


def test_mapping_without_a_name_is_rejected():
    with pytest.raises(ConfigError, match="no 'market' id"):
        load_market({"schema_version": 1, "expr": "1"})


def test_unknown_key_is_rejected_with_a_suggestion():
    with pytest.raises(ConfigError, match="unknown key 'require'; did you mean 'requires'") as exc_info:
        load_market(_provider(require=["x"]))
    assert exc_info.value.key == "require"


@pytest.mark.parametrize(
    "override, key, text",
    [
        ({"schema_version": 2}, "schema_version", "must be 1"),
        ({"expr": None}, "expr", "non-empty string"),
        ({"expr": "1 +"}, "expr", "syntax error"),
        ({"code": "def f(:"}, "code", "syntax error"),
        ({"imports": 3}, "imports", "must be a string"),
        ({"description": 3}, "description", "must be a string"),
        ({"requires": "other"}, "requires", "list of market provider names"),
        ({"requires": ["a", "a"]}, "requires", "twice"),
        ({"requires": ["p"]}, "requires", "cannot require itself"),
    ],
)
def test_invalid_provider_fields_are_rejected(override, key, text):
    with pytest.raises(ConfigError, match=text) as exc_info:
        load_market({**_provider(), **override})
    assert exc_info.value.key == key


def test_an_asset_config_handed_to_the_market_loader_says_so():
    with pytest.raises(ConfigError, match="looks like an asset config"):
        load_market(_asset("a", {"expr": "1"}))


# ------------------------------------------------------------------------------------ MarketRegistry


def test_registry_add_accepts_a_mapping_a_path_a_config_and_a_directory():
    reg = MarketRegistry()
    assert [c.name for c in reg.add(_provider("a"))] == ["a"]
    assert reg.add_one(load_market(_provider("b"))).name == "b"
    assert [c.name for c in reg.add(MARKETS / "toy_usd_ois.yaml")] == ["toy_usd_ois"]
    assert reg.names() == ["a", "b", "toy_usd_ois"] and len(reg) == 3 and "a" in reg and reg["a"].name == "a"

    whole_dir = MarketRegistry()
    assert [c.name for c in whole_dir.add(MARKETS)] == ["toy_usd_ois", "toy_usd_swaption_vol"]
    with pytest.raises(ConfigError, match="yielded 2 configs"):
        MarketRegistry().add_one(MARKETS)


def test_duplicate_provider_name_raises():
    reg = MarketRegistry()
    reg.add(_provider("a"))
    with pytest.raises(ConfigError, match="duplicate market provider name 'a'"):
        reg.add(_provider("a", "2"))


def test_requires_must_already_be_registered():
    reg = MarketRegistry()
    reg.add(_provider("usd_curve"))
    with pytest.raises(ConfigError, match="requires 'usd_curv' is not a registered market provider.*did you mean 'usd_curve'") as exc_info:
        reg.add(_provider("vol", requires=["usd_curv"]))
    assert exc_info.value.key == "requires" and "vol" not in reg


def test_a_directory_registers_in_dependency_order_whatever_the_file_names(tmp_path):
    (tmp_path / "a_vol.yaml").write_text("schema_version: 1\nrequires: [z_curve]\nexpr: 'markets[\"z_curve\"] + 1'\n", encoding="utf8")
    (tmp_path / "z_curve.yaml").write_text("schema_version: 1\nexpr: '1'\n", encoding="utf8")
    assert [c.name for c in MarketRegistry().add(tmp_path)] == ["z_curve", "a_vol"]


def test_circular_requires_never_loads(tmp_path):
    (tmp_path / "a.yaml").write_text("schema_version: 1\nrequires: [b]\nexpr: '1'\n", encoding="utf8")
    (tmp_path / "b.yaml").write_text("schema_version: 1\nrequires: [a]\nexpr: '1'\n", encoding="utf8")
    with pytest.raises(ConfigError, match="circular"):
        MarketRegistry().add(tmp_path)


# ------------------------------------------------------------------------------------ the asset side


def test_asset_naming_a_provider_has_no_market_expression_of_its_own():
    cfg = load_asset(_asset("a", {"provider": "curve"}))
    assert (cfg.market_provider, cfg.market_expr, cfg.market_key) == ("curve", None, "curve")
    with pytest.raises(KeyError):
        cfg.code("market")
    inline = load_asset(_asset("b", {"expr": "1"}))
    assert (inline.market_provider, inline.market_expr, inline.market_key) == (None, "1", "b")


@pytest.mark.parametrize(
    "market, key, text",
    [
        ({"provider": "curve", "expr": "1"}, "market.expr", "cannot be combined"),
        ({"provider": "curve", "key": "k"}, "market.key", "cannot be combined"),
        ({"provider": ""}, "market.provider", "non-empty string"),
        ({"provider": 3}, "market.provider", "non-empty string"),
        ({}, "market.expr", "market.expr .* or market.provider"),
        ({"key": "k"}, "market.expr", "market.expr .* or market.provider"),
        ({"providr": "curve"}, "market.providr", "did you mean 'provider'"),
    ],
)
def test_invalid_market_sections_are_rejected(market, key, text):
    with pytest.raises(ConfigError, match=text) as exc_info:
        load_asset(_asset("a", market))
    assert exc_info.value.key == key


def test_unknown_provider_fails_when_the_asset_registers():
    markets = MarketRegistry()
    markets.add(_provider("usd_curve"))
    reg = AssetRegistry(markets)
    with pytest.raises(ConfigError, match="market.provider 'usd_curv' is not a registered market provider.*did you mean 'usd_curve'") as exc_info:
        reg.add(_asset("a", {"provider": "usd_curv"}))
    assert exc_info.value.key == "market.provider" and "a" not in reg
    with pytest.raises(ConfigError, match="not a registered market provider"):
        AssetRegistry().add(_asset("a", {"provider": "usd_curve"}))  # a registry with no providers at all


def test_assets_on_one_provider_need_not_share_imports_or_code():
    """The inline rule (assets sharing a market.key must have identical imports/code/market.expr)
    does not apply: the provider has its own namespace."""
    session = PricebtSession.use(
        markets=[_provider("curve", "7")],
        assets=[_asset("a", {"provider": "curve"}, code="X = 1"), _asset("b", {"provider": "curve"}, code="X = 2", imports="import math")],
    )
    assert session.registry.names() == ["a", "b"] and session.markets.names() == ["curve"]
    assert session.registry.markets is session.markets


def test_session_add_market_then_add_asset():
    session = PricebtSession.use()
    with pytest.raises(ConfigError, match="not a registered market provider"):
        session.add_asset(_asset("a", {"provider": "late"}))
    assert session.add_market(_provider("late", "5")).name == "late"
    assert session.add_asset(_asset("a", {"provider": "late"})).market_provider == "late"


# ------------------------------------------------------------------------------------ pricing


def test_a_provider_asset_prices_exactly_like_the_same_market_inline():
    dates = [date(2024, 3, 4), date(2024, 3, 5), date(2024, 6, 3)]
    measures = (Price, IRDelta(aggregation_level="Type"), IRFwdRate)

    def values():
        out = {}
        for d in dates:
            with PricingContext(d):
                res = _usd_swap().calc(measures)
            out[d] = [float(res.result()[m]) for m in measures]
        return out

    PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])
    inline = values()
    PricebtSession.use(markets=[MARKETS / "toy_usd_ois.yaml"], assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois")])
    assert values() == inline


def test_bucketed_measure_on_a_provider_asset_matches_inline():
    PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])
    with PricingContext(D):
        inline = _usd_swap().calc(IRDelta).result()
    PricebtSession.use(markets=[MARKETS], assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois")])
    with PricingContext(D):
        ladder = _usd_swap().calc(IRDelta).result()
    assert_frame_equal(ladder, inline)


def test_two_assets_on_one_provider_evaluate_the_market_once_per_date_and_csa():
    session = PricebtSession.use(
        markets=[MARKETS / "toy_usd_ois.yaml"],
        assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois"), on_provider("toy_usd_irs_full.yaml", "toy_usd_ois")],
    )
    usd, full = session.registry["toy_usd_irs"], session.registry["toy_usd_irs_full"]
    assert session.pricing.market(usd, D, None) is session.pricing.market(full, D, None)
    assert tr.EVAL_COUNTS["market"] == 1
    session.pricing.market(full, D, "CSA_X")
    session.pricing.market(usd, D, "CSA_X")
    assert tr.EVAL_COUNTS["market"] == 2  # csa is part of the provider's cache key
    assert [(k, c) for k, _d, c in tr.CSA_SEEN] == [("market", None), ("market", "CSA_X")]
    session.reset_caches()
    session.pricing.market(usd, D, None)
    assert tr.EVAL_COUNTS["market"] == 3


def test_a_derived_provider_builds_on_the_object_of_the_one_it_requires():
    """A swap on the curve provider and a swaption on the vol provider that requires it: one curve
    object per date (inline, each asset loads its own)."""
    session = PricebtSession.use(
        markets=[MARKETS],
        assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois"), on_provider("toy_usd_swaption.yaml", "toy_usd_swaption_vol")],
    )
    with PricingContext(D):
        swap_price = _usd_swap().price()
        option = _swaption().calc((Price, IRVega(aggregation_level="Type")))
    derived = [float(swap_price.result()), *(float(v) for v in option.result().values())]
    assert (tr.EVAL_COUNTS["market"], tr.EVAL_COUNTS["vol_market"]) == (1, 1)
    vol_market = session.pricing.market(session.registry["toy_usd_swaption"], D, None)
    assert vol_market.curve is session.pricing.market(session.registry["toy_usd_irs"], D, None)

    tr.reset_recorders()
    PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml", ASSETS / "toy_usd_swaption.yaml"])
    with PricingContext(D):
        swap_price = _usd_swap().price()
        option = _swaption().calc((Price, IRVega(aggregation_level="Type")))
    assert [float(swap_price.result()), *(float(v) for v in option.result().values())] == derived
    assert tr.EVAL_COUNTS["market"] == 2  # inline: the swap's curve and the swaption's own copy of it


def test_a_missing_required_market_makes_the_derived_one_missing_without_evaluating_it():
    session = PricebtSession.use(markets=[MARKETS], assets=[on_provider("toy_usd_swaption.yaml", "toy_usd_swaption_vol")])
    asset = session.registry["toy_usd_swaption"]
    tr.HOLES.add(D)
    assert session.pricing.has_market(asset, D, None) is False
    assert tr.EVAL_COUNTS["vol_market"] == 0
    with pytest.raises(MarketDataUnavailable, match="no market for 'toy_usd_swaption'"):
        session.pricing.market(asset, D, None)
    with PricingContext(D), pytest.raises(MarketDataUnavailable):
        _swaption().price()
    assert session.pricing.has_market(asset, date(2024, 3, 5), None) is True


def test_provider_expression_sees_the_base_names_and_its_required_markets():
    names = "(pricebt_date, pricebt_datetime.hour, str(pricebt_timestamp.tz), pricebt_csa, pricebt_location, sorted(markets))"
    session = PricebtSession.use(
        markets=[_provider("base", "1"), _provider("probe", names, requires=["base"])],
        assets=[_asset("a", {"provider": "probe"})],
        tz="UTC",
        eod_time="16:30",
    )
    assert session.pricing.market(session.registry["a"], D, "CSA_X") == (D, 16, "UTC", "CSA_X", "LDN", ["base"])


def test_provider_expression_does_not_see_asset_names():
    session = PricebtSession.use(markets=[_provider("probe", "pricebt_asset")], assets=[_asset("a", {"provider": "probe"})])
    with pytest.raises(AssetEvaluationError) as exc_info:
        session.pricing.market(session.registry["a"], D, None)
    assert isinstance(exc_info.value.__cause__, NameError)
    assert "'probe'" in str(exc_info.value)  # the error names the provider, not the asset that asked


def test_provider_namespace_is_its_own_and_runs_once():
    """`imports`/`code` run once, lazily, and a helper there is invisible to the asset (and the
    asset's helpers to the provider)."""
    session = PricebtSession.use(
        markets=[_provider("p", "boot()", imports="import toylib.rates as tr", code="def boot():\n    tr.EVAL_COUNTS['boot'] += 1\n    return 3\ntr.EVAL_COUNTS['exec'] += 1")],
        assets=[_asset("a", {"provider": "p"}, functions={"f": {"expr": "market", "unit": "number"}, "g": {"expr": "boot()", "unit": "number"}})],
    )
    assert tr.EVAL_COUNTS["exec"] == 0  # loading and registering executed nothing
    asset = session.registry["a"]
    inst_market = [session.pricing.market(asset, d, None) for d in (D, date(2024, 3, 5))]
    assert inst_market == [3, 3] and (tr.EVAL_COUNTS["exec"], tr.EVAL_COUNTS["boot"]) == (1, 2)
    from pricebt.instrument import ConfigInstrument

    with pytest.raises(AssetEvaluationError) as exc_info:
        session.pricing.unit_value(ConfigInstrument(pricebt_asset="a"), D, "g", None)
    assert isinstance(exc_info.value.__cause__, NameError)


def test_a_provider_and_an_asset_may_share_a_name():
    session = PricebtSession.use(markets=[_provider("same", "11")], assets=[_asset("same", {"provider": "same"}, code="Y = 1")])
    assert session.pricing.market(session.registry["same"], D, None) == 11


# ------------------------------------------------------------------------------------ the engine


def _backtest():
    trigger = PeriodicTrigger(
        trigger_requirements=PeriodicTriggerRequirements(frequency="1m", end_date=date(2024, 4, 2)),
        actions=[AddTradeAction(_usd_swap("swap"), "1m")],
    )
    return GenericEngine().run_backtest(Strategy(None, [trigger]), start=date(2024, 1, 2), end=date(2024, 4, 2), frequency="1b", show_progress=False)


def test_a_backtest_on_a_provider_asset_equals_the_inline_backtest():
    PricebtSession.use(assets=[ASSETS / "toy_usd_irs.yaml"])
    inline = _backtest().result_summary
    PricebtSession.use(markets=[MARKETS / "toy_usd_ois.yaml"], assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois")])
    assert_frame_equal(_backtest().result_summary, inline)


def test_a_backtest_drops_the_dates_a_provider_has_no_market_for():
    PricebtSession.use(markets=[MARKETS / "toy_usd_ois.yaml"], assets=[on_provider("toy_usd_irs.yaml", "toy_usd_ois")])
    hole = date(2024, 2, 19)
    tr.HOLES.add(hole)
    with pytest.warns(UserWarning):
        summary = _backtest().result_summary
    assert hole not in summary.index and date(2024, 2, 20) in summary.index
