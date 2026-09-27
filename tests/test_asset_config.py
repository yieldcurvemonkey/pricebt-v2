"""Tests for pricebt.assets: load_asset/load_fx, validation, expression compilation,
AssetNamespace's lazy exec, and validate_resolved (DESIGN.md section 4, IMPLEMENTATION_PLAN P1.4).

Configs here are self-contained toy dicts using only stdlib expressions (no toylib dependency:
P1.5 owns that library and may not exist yet when this task runs in parallel).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from pricebt.assets import AssetNamespace, load_asset, load_fx, validate_resolved
from pricebt.assets.fx import FxEvaluator
from pricebt.errors import AssetEvaluationError, ConfigError


def _minimal(**overrides):
    cfg = {
        "schema_version": 1,
        "asset": "minimal_asset",
        "instrument": "ConfigInstrument",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"f": {"expr": "market * 2", "unit": "ccy"}},
        "risk_measures": {"Price": "f"},
    }
    cfg.update(overrides)
    return cfg


# ------------------------------------------------------------------------------------ load_asset: happy path


def test_minimal_valid_config_loads():
    cfg = load_asset(_minimal())
    assert cfg.name == "minimal_asset"
    assert cfg.currency == "USD"
    assert cfg.market_key == "minimal_asset"  # defaults to the asset name
    assert set(cfg.functions) == {"f"}
    assert cfg.functions["f"].unit == "ccy"
    assert cfg.functions["f"].scale_with_quantity is True  # ccy is extensive
    assert cfg.risk_measures["Price"] == cfg.risk_measures["Price"].__class__(scalar="f", bucketed=None)


def test_asset_name_defaults_to_file_stem(tmp_path):
    p = tmp_path / "my_stem_name.yaml"
    raw = _minimal()
    del raw["asset"]
    import yaml

    p.write_text(yaml.safe_dump(raw), encoding="utf8")
    cfg = load_asset(p)
    assert cfg.name == "my_stem_name"


def test_intensive_unit_defaults_scale_with_quantity_false():
    cfg = load_asset(_minimal(functions={"r": {"expr": "1.0", "unit": "bp"}}, risk_measures={"Price": "r"}))
    assert cfg.functions["r"].scale_with_quantity is False


def test_portfolio_function_and_bucketed_risk_measure():
    cfg = load_asset(
        _minimal(
            portfolio_functions={"ladder": {"expr": "{'2Y': 1.0}", "unit": "ccy_per_bp", "returns": "buckets", "labels": {"mkt_type": "IR"}}},
            risk_measures={"Price": "f", "SomeDelta": {"scalar": "f", "bucketed": "ladder"}},
        )
    )
    assert cfg.portfolio_functions["ladder"].returns == "buckets"
    assert cfg.portfolio_functions["ladder"].labels == {"mkt_type": "IR"}
    rm = cfg.risk_measures["SomeDelta"]
    assert rm.scalar == "f" and rm.bucketed == "ladder"


def test_market_key_can_be_overridden():
    cfg = load_asset(_minimal(market={"expr": "1", "key": "shared_key"}))
    assert cfg.market_key == "shared_key"


# ------------------------------------------------------------------------------------ validation errors


def test_unknown_top_level_key_names_the_key_and_suggests():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(currenc="USD"))  # typo for 'currency'
    err = exc_info.value
    assert err.asset == "minimal_asset"
    assert err.key == "currenc"  # not ".currenc" -- top-level keys get no leading dot
    assert "currenc" in str(err)
    assert "currency" in str(err)  # did-you-mean suggestion


def test_bad_unit_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(functions={"f": {"expr": "1.0", "unit": "furlongs"}}))
    assert "unit" in str(exc_info.value)
    assert exc_info.value.key == "functions.f.unit"


def test_missing_price_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(risk_measures={"IRDeltaParallel": "f"}))
    assert "Price" in str(exc_info.value)


def test_dangling_function_reference_in_risk_measures():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(risk_measures={"Price": "f", "Other": "nope"}))
    err = exc_info.value
    assert err.key == "risk_measures.Other.scalar"
    assert "nope" in str(err)


def test_dangling_bucketed_reference():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(risk_measures={"Price": "f", "Other": {"bucketed": "nope"}}))
    assert exc_info.value.key == "risk_measures.Other.bucketed"


def test_size_attribute_must_be_a_key_of_attributes():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(attributes={"foo": "1"}, size_attribute="bar"))
    assert exc_info.value.key == "size_attribute"


def test_size_attribute_ok_when_present():
    cfg = load_asset(_minimal(attributes={"foo": "1"}, size_attribute="foo"))
    assert cfg.size_attribute == "foo"


def test_schema_version_must_be_1():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(schema_version=2))
    assert exc_info.value.key == "schema_version"


def test_missing_functions_is_rejected():
    raw = _minimal()
    del raw["functions"]
    with pytest.raises(ConfigError) as exc_info:
        load_asset(raw)
    assert exc_info.value.key == "functions"


def test_implausible_currency_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(currency="US"))
    assert exc_info.value.key == "currency"


def test_function_and_attribute_name_collision_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(attributes={"f": "1"}))  # 'f' is already a function name
    assert exc_info.value.key == "attributes"


def test_invalid_returns_value_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(portfolio_functions={"pf": {"expr": "1.0", "unit": "ccy", "returns": "buckets2"}}))
    assert exc_info.value.key == "portfolio_functions.pf.returns"


def test_invalid_build_on_value_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(trade={"expr": "1", "build_on": "never"}))
    assert exc_info.value.key == "trade.build_on"


def test_risk_measure_dict_with_neither_scalar_nor_bucketed_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(risk_measures={"Price": "f", "Other": {}}))
    assert exc_info.value.key == "risk_measures.Other"


def test_missing_market_key_is_rejected():
    raw = _minimal()
    del raw["market"]
    with pytest.raises(ConfigError) as exc_info:
        load_asset(raw)
    assert exc_info.value.key == "market"


def test_invalid_function_currency_override_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(functions={"f": {"expr": "1.0", "unit": "ccy", "currency": "US"}}))
    assert exc_info.value.key == "functions.f.currency"


def test_unknown_key_under_market_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(market={"expr": "1", "bogus": True}))
    assert exc_info.value.key == "market.bogus"


def test_unknown_key_under_trade_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(trade={"expr": "1", "bogus": True}))
    assert exc_info.value.key == "trade.bogus"


def test_unknown_key_under_function_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(functions={"f": {"expr": "1.0", "unit": "ccy", "bogus": True}}))
    assert exc_info.value.key == "functions.f.bogus"


def test_unknown_key_under_portfolio_function_labels_is_rejected():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(portfolio_functions={"pf": {"expr": "1.0", "unit": "ccy", "returns": "scalar", "labels": {"bogus": "x"}}}))
    assert exc_info.value.key == "portfolio_functions.pf.labels.bogus"


def test_bare_string_risk_measure_may_reference_a_scalar_portfolio_function():
    # DESIGN section 8.1 rule 4: a bare string may name a `returns: scalar` portfolio function,
    # not only a `functions:` entry.
    cfg = load_asset(
        _minimal(
            portfolio_functions={"agg": {"expr": "1.0", "unit": "ccy", "returns": "scalar"}},
            risk_measures={"Price": "f", "SomeMeasure": "agg"},
        )
    )
    rm = cfg.risk_measures["SomeMeasure"]
    assert rm.scalar == "agg" and rm.bucketed is None


def test_bare_string_risk_measure_may_reference_a_bucketed_portfolio_function():
    # DESIGN section 8.1 rule 4: a bare string naming a `returns: buckets` portfolio function
    # becomes {bucketed: g}, not {scalar: g}.
    cfg = load_asset(
        _minimal(
            portfolio_functions={"ladder": {"expr": "{'2Y': 1.0}", "unit": "ccy_per_bp", "returns": "buckets"}},
            risk_measures={"Price": "f", "SomeDelta": "ladder"},
        )
    )
    rm = cfg.risk_measures["SomeDelta"]
    assert rm.scalar is None and rm.bucketed == "ladder"


def test_dict_form_scalar_may_reference_a_scalar_portfolio_function():
    # The explicit dict form must accept exactly what the bare-string form normalises to
    # (DESIGN section 8.1 rule 4) -- {scalar: agg} must be as valid as the bare string "agg".
    cfg = load_asset(
        _minimal(
            portfolio_functions={"agg": {"expr": "1.0", "unit": "ccy", "returns": "scalar"}},
            risk_measures={"Price": "f", "SomeMeasure": {"scalar": "agg"}},
        )
    )
    assert cfg.risk_measures["SomeMeasure"].scalar == "agg"


def test_dict_form_bucketed_rejects_a_scalar_portfolio_function():
    # The converse of the above: a `returns: scalar` portfolio function is never a valid
    # `bucketed:` target, even though it is a valid bare-string / `scalar:` target.
    with pytest.raises(ConfigError) as exc_info:
        load_asset(
            _minimal(
                portfolio_functions={"agg": {"expr": "1.0", "unit": "ccy", "returns": "scalar"}},
                risk_measures={"Price": "f", "SomeMeasure": {"bucketed": "agg"}},
            )
        )
    assert exc_info.value.key == "risk_measures.SomeMeasure.bucketed"


# ------------------------------------------------------------------------------------ compile-time syntax errors


def test_syntax_error_in_function_expr_names_its_key():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(functions={"f": {"expr": "1 +", "unit": "ccy"}}))
    err = exc_info.value
    assert err.key == "f"
    assert "syntax error" in str(err)


def test_syntax_error_in_code_names_its_key():
    with pytest.raises(ConfigError) as exc_info:
        load_asset(_minimal(code="def bad(:\n  pass"))
    assert exc_info.value.key == "code"


# ------------------------------------------------------------------------------------ lazy execution


def test_raising_imports_does_not_fail_load_only_first_eval(capsys):
    cfg = load_asset(_minimal(imports="import sys\nsys.stderr.write('BOOM\\n')\nraise RuntimeError('nope')"))
    ns = AssetNamespace(cfg)

    d1, d2 = dt.date(2024, 1, 1), dt.date(2024, 1, 2)
    with pytest.raises(AssetEvaluationError) as exc_info:
        ns.eval("f", pricebt_date=d1, pricebt_csa="csa-1", market=1)
    err1 = exc_info.value
    assert err1.key == "imports"
    assert err1.date == d1
    assert err1.csa == "csa-1"
    assert isinstance(err1.__cause__, RuntimeError)

    # second call: cached and re-raised, imports must NOT run again
    with pytest.raises(AssetEvaluationError) as exc_info2:
        ns.eval("f", pricebt_date=d2, pricebt_csa="csa-2", market=1)
    assert exc_info2.value.key == "imports"

    assert capsys.readouterr().err.count("BOOM") == 1


def test_lazy_exec_runs_once_across_multiple_eval_calls(capsys):
    cfg = load_asset(_minimal(code="import sys\nsys.stderr.write('EXEC\\n')"))
    ns = AssetNamespace(cfg)
    assert capsys.readouterr().err == ""  # load_asset never executes code
    ns.eval("f", pricebt_date=None, pricebt_csa=None, market=3)
    ns.eval("f", pricebt_date=None, pricebt_csa=None, market=3)
    assert capsys.readouterr().err.count("EXEC") == 1


# ------------------------------------------------------------------------------------ namespace isolation


def test_namespace_isolation_between_two_different_assets():
    cfg_a = load_asset(_minimal(asset="asset_a", code="X = 1", functions={"f": {"expr": "X", "unit": "number"}}, risk_measures={"Price": "f"}))
    cfg_b = load_asset(_minimal(asset="asset_b", code="X = 2", functions={"f": {"expr": "X", "unit": "number"}}, risk_measures={"Price": "f"}))
    ns_a = AssetNamespace(cfg_a)
    ns_b = AssetNamespace(cfg_b)
    assert ns_a.eval("f", pricebt_date=None, pricebt_csa=None) == 1
    assert ns_b.eval("f", pricebt_date=None, pricebt_csa=None) == 2


def test_namespace_isolation_between_two_instances_of_the_same_config():
    cfg = load_asset(
        _minimal(
            code="counter = []",
            functions={"bump": {"expr": "len(counter.append(1) or counter)", "unit": "number"}, "f": {"expr": "len(counter)", "unit": "number"}},
            risk_measures={"Price": "f"},
        )
    )
    ns1 = AssetNamespace(cfg)
    ns2 = AssetNamespace(cfg)
    ns1.eval("bump", pricebt_date=None, pricebt_csa=None)
    ns1.eval("bump", pricebt_date=None, pricebt_csa=None)
    assert ns1.eval("f", pricebt_date=None, pricebt_csa=None) == 2
    assert ns2.eval("f", pricebt_date=None, pricebt_csa=None) == 0  # ns2's own exec, untouched by ns1


# ------------------------------------------------------------------------------------ AssetEvaluationError contents


def test_asset_evaluation_error_contents_on_a_raising_expression():
    cfg = load_asset(_minimal(functions={"f": {"expr": "1/0", "unit": "number"}}, risk_measures={"Price": "f"}))
    ns = AssetNamespace(cfg)
    d = dt.date(2025, 6, 1)
    with pytest.raises(AssetEvaluationError) as exc_info:
        ns.eval("f", pricebt_date=d, pricebt_csa="csa-x")
    err = exc_info.value
    assert err.asset == "minimal_asset"
    assert err.key == "f"
    assert err.expr == "1/0"
    assert err.date == d
    assert err.csa == "csa-x"
    assert isinstance(err.__cause__, ZeroDivisionError)
    assert "ZeroDivisionError" in str(err)


# ------------------------------------------------------------------------------------ validate_resolved


def test_validate_resolved_accepts_plain_data():
    validate_resolved({"a": "x", "b": 1, "c": 1.5, "d": True, "e": None, "f": dt.date(2024, 1, 1), "g": dt.datetime(2024, 1, 1), "h": pd.Timestamp("2024-01-01"), "i": (1, "x", None)})


def test_validate_resolved_rejects_a_list_value():
    with pytest.raises(ConfigError):
        validate_resolved({"a": [1, 2, 3]})


def test_validate_resolved_rejects_an_arbitrary_object_value():
    class Thing:
        pass

    with pytest.raises(ConfigError):
        validate_resolved({"a": Thing()})


def test_validate_resolved_rejects_a_non_plain_item_inside_a_tuple():
    with pytest.raises(ConfigError):
        validate_resolved({"a": (1, [2])})


# ------------------------------------------------------------------------------------ FX config


def _minimal_fx(**overrides):
    cfg = {
        "schema_version": 1,
        "fx": "test_fx",
        "code": "_T = {('EUR', 'USD'): 1.1}",
        "rate": "_T.get((base, quote))",
    }
    cfg.update(overrides)
    return cfg


def test_fx_config_loads_and_evaluator_returns_rate():
    fx_cfg = load_fx(_minimal_fx())
    evaluator = FxEvaluator(fx_cfg)
    assert evaluator.rate("EUR", "USD", dt.date(2024, 1, 1)) == 1.1
    assert evaluator.rate("GBP", "USD", dt.date(2024, 1, 1)) is None


def test_fx_config_requires_fx_id():
    raw = _minimal_fx()
    del raw["fx"]
    with pytest.raises(ConfigError):
        load_fx(raw)


def test_fx_config_unknown_key_suggests():
    with pytest.raises(ConfigError) as exc_info:
        load_fx(_minimal_fx(rat="_T.get((base, quote))"))
    assert exc_info.value.key == "rat"  # not ".rat" -- top-level keys get no leading dot
    assert "rate" in str(exc_info.value)


def test_fx_config_accepts_a_description():
    fx_cfg = load_fx(_minimal_fx(description="a toy FX table"))
    assert fx_cfg.description == "a toy FX table"


def test_load_fx_accepts_the_real_toy_fx_fixture():
    # Regression: load_fx must not choke on tests/assets/toy_fx.yaml (P1.5's fixture, owned by a
    # sibling task and not asserted on here beyond "it loads"). A `description:` top-level key is
    # valid for FX configs just like asset configs (DESIGN section 4.2); see
    # test_fx_config_accepts_a_description for that behaviour in isolation.
    fx_cfg = load_fx(Path(__file__).parent / "assets" / "toy_fx.yaml")
    assert fx_cfg.name == "toy_fx"
