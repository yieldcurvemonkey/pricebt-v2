"""Injected names are visible inside nested scopes of a config expression (DESIGN.md section 4.4).

`AssetNamespace.eval` evaluates in a per-call copy of the asset's namespace updated with the injected
names, so a generator, comprehension or lambda inside an expression sees `market`, `trades`,
`weights`, `pricebt_date`, ... (as eval *locals* they were invisible there: NameError). The shared
namespace is never mutated, and helpers defined in `code:` keep their own module globals.
Mutation check: back to `eval(code, globals_, dict(injected))` fails every test in this file except
`test_helpers_keep_their_own_module_globals`.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pricebt.assets import AssetNamespace, load_asset
from pricebt.instrument import ConfigInstrument
from pricebt.markets import CloseMarket, PricingContext
from pricebt.risk import Annuity, IRDelta, IRVega, PnlExplain, Price
from pricebt.session import PricebtSession

D = date(2024, 3, 4)

FUNCTIONS = {
    "genexpr": {"expr": "sum(market + pricebt_date.day for _ in (1,))", "unit": "ccy"},  # 2 + 4
    "lam": {"expr": "(lambda: market * pricebt_date.month)()", "unit": "ccy"},  # 2 * 3
}
PORTFOLIO = {
    # gs-style book functions: the body of the generator / lambda reads the injected names
    "book": {"expr": "sum(market * w for t, w in zip(trades, weights))", "unit": "ccy_per_bp", "returns": "scalar"},
    "book_lam": {"expr": "(lambda: len(trades) * weights[0] * market + pricebt_date.day)()", "unit": "ccy_per_bp", "returns": "scalar"},
}


def _cfg(**over):
    cfg = {
        "schema_version": 1, "asset": "scopes", "instrument": "ConfigInstrument", "currency": "USD",
        "market": {"expr": "2.0"}, "functions": FUNCTIONS, "portfolio_functions": PORTFOLIO,
        "risk_measures": {"Price": "genexpr", "Annuity": "lam", "IRDelta": {"scalar": "book"}, "IRVega": {"scalar": "book_lam"}},
    }
    cfg.update(over)
    return cfg


def _value(measure, quantity=1.0):
    session = PricebtSession.use(assets=[_cfg()])
    return float(session.pricing.value(ConfigInstrument(pricebt_asset="scopes", name="x", quantity_=quantity), D, measure, None))


def test_generator_and_lambda_in_a_function_see_market_and_pricebt_date():
    assert _value(Price) == 6.0
    assert _value(Annuity) == 6.0


def test_generator_and_lambda_in_a_portfolio_function_see_market_trades_and_weights():
    assert _value(IRDelta(aggregation_level="Type")) == 2.0
    assert _value(IRDelta(aggregation_level="Type"), quantity=2.5) == 5.0  # weights=[1.0], then x quantity_
    assert _value(IRVega(aggregation_level="Type")) == 6.0  # 1 * 1.0 * 2 + 4


def test_the_shared_namespace_is_never_mutated_by_an_evaluation():
    ns = AssetNamespace(load_asset(_cfg(code="K = 5")))
    assert ns.eval("genexpr", pricebt_date=D, pricebt_csa=None, market=2.0) == 6.0
    before = dict(ns._globals)
    assert "market" not in before and "pricebt_date" not in before
    assert ns.eval("lam", pricebt_date=D, pricebt_csa=None, market=3.0) == 9.0
    assert ns._globals == before and ns._globals.keys() == before.keys()


def test_helpers_keep_their_own_module_globals():
    """An injected name never leaks into a `code:` helper: a helper reads the asset's namespace only
    (pass what it needs as an argument), and a same-named config global is shadowed only in the
    expression itself."""
    code = "market = 'config'\ndef helper():\n    return market\n"
    ns = AssetNamespace(load_asset(_cfg(code=code, functions={**FUNCTIONS, "h": {"expr": "(helper(), market)", "unit": "ccy"}})))
    assert ns.eval("h", pricebt_date=D, pricebt_csa=None, market=7.0) == ("config", 7.0)


def test_discovery_sees_a_name_used_only_in_a_nested_scope():
    """pricebt finds a pass-through parameter, `market_to` and an attribute's `market` in the
    expression's nested code objects too, so each of these prices instead of being refused
    (NotSupportedError) or left without its market (NameError)."""
    cfg = _cfg(
        functions={**FUNCTIONS, "bumped": {"expr": "(lambda: float(pricebt_bump_size))()", "unit": "ccy_per_bp"}},
        portfolio_functions={**PORTFOLIO, "explain": {"expr": "[{'mkt_type': 'IR', 'value': sum(w * market_to for w in weights)}]", "unit": "ccy", "returns": "buckets"}},
        attributes={"level": "next(market for _ in (1,))"},
        risk_measures={"Price": "genexpr", "IRDelta": {"scalar": "bumped"}, "PnlExplain": "explain"},
    )
    PricebtSession.use(assets=[cfg])
    inst = ConfigInstrument(pricebt_asset="scopes", name="x")
    with PricingContext(pricing_date=D):
        assert float(inst.calc(IRDelta(aggregation_level="Type", bump_size=3.0)).result()) == 3.0
        explain = inst.calc(PnlExplain(CloseMarket(date=D + timedelta(days=1)))).result()
        inst.resolve()
    assert list(explain["value"]) == [2.0]
    assert inst.level == 2.0


@pytest.mark.parametrize("expr", ["sum(market for _ in (1,))", "next(pricebt_date for _ in (1,)).day"])
def test_the_former_name_error_example_now_evaluates(expr):
    ns = AssetNamespace(load_asset(_cfg(functions={**FUNCTIONS, "bad": {"expr": expr, "unit": "ccy"}})))
    assert ns.eval("bad", pricebt_date=D, pricebt_csa=None, market=4.0) == 4.0
