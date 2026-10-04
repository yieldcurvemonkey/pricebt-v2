"""skills/pricebt-asset-config-cookbook: every literal message fragment the error catalogue quotes for the
IR measure contract, measure parameters, frames, contexts, PnlExplain and P&L definitions is raised here for real, and must
appear both in the catalogue and in the raised message, so the catalogue cannot drift from the code."""
from __future__ import annotations

import warnings
from datetime import date
from pathlib import Path

import pytest

from pricebt.assets.config import load_asset
from pricebt.backtests.backtest_objects import PnlAttribute, ir_pnl_definition
from pricebt.errors import PricebtError
from pricebt.instrument import ConfigInstrument, IRSwap
from pricebt.markets import CloseMarket, PricingContext
from pricebt.markets.portfolio import Portfolio
from pricebt.risk import Cashflows, IRBasis, IRDelta, IRVanna, IRVega, PnlExplain, PnlExplainClose, PnlExplainLive, contracts
from pricebt.session import PricebtSession

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = (ROOT / "skills" / "pricebt-asset-config-cookbook" / "references" / "error-catalogue.md").read_text(encoding="utf-8")
TEMPLATE = ROOT / "skills" / "pricebt-connect-pricing-library" / "references" / "config-template.yaml"
D, T = date(2024, 3, 4), date(2024, 3, 11)
REASON = "test: not computed"


# ------------------------------------------------------------------ load time: a Bond and an IRSwap (both strict), a full toy bond
def _bond_cfg(functions=None, risk_measures=None, unsupported=None, **top):
    """A Bond (strict like every contract class: IR_STRICT_CONTRACT R3-0, BOND_DESIGN 4.1) mapping
    Price and `risk_measures`: every other contract row is a gap, so the one load error lists the
    slot problem a fragment names next to the gaps."""
    cfg = {
        "schema_version": 1, "asset": "bond_asset", "instrument": "Bond", "currency": "USD", "market": {"expr": "1"},
        "functions": {"npv": {"expr": "0.0", "unit": "ccy"}, **(functions or {})},
        "risk_measures": {"Price": "npv", **(risk_measures or {})},
        "unsupported_measures": unsupported or {},
    }
    cfg.update(top)
    return cfg


def _toy_bond(unsupported):
    """tests/assets/toy_usd_bond.yaml (maps the whole Bond contract, so it loads) plus declarations."""
    from pricebt.assets import yamlio

    raw = yamlio.load_file(ROOT / "tests" / "assets" / "toy_usd_bond.yaml")
    raw["unsupported_measures"] = unsupported
    return raw


def _config_instrument(unsupported):
    """A class without a contract: declarations load, and a stale one (also mapped) only warns (R2-9)."""
    return {"schema_version": 1, "asset": "ci", "instrument": "ConfigInstrument", "currency": "USD", "market": {"expr": "1"},
            "functions": {"npv": {"expr": "0.0", "unit": "ccy"}, "zero": {"expr": "0.0", "unit": "ccy_per_bp"}},
            "risk_measures": {"Price": "npv", "IRVega": {"scalar": "zero"}}, "unsupported_measures": unsupported}


def _swap_cfg(unsupported=None):
    """An IRSwap (strict) mapping only Price: every other contract measure is a gap."""
    return {"schema_version": 1, "asset": "swap_asset", "instrument": "IRSwap", "currency": "USD", "market": {"expr": "1"},
            "functions": {"npv": {"expr": "0.0", "unit": "ccy"}}, "risk_measures": {"Price": "npv"}, "unsupported_measures": unsupported or {}}


def _load_error(cfg) -> str:
    with pytest.raises(PricebtError) as exc:
        load_asset(cfg)
    return str(exc.value)


def _load_warnings(cfg) -> str:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_asset(cfg)
    return " | ".join(str(w.message) for w in caught)


_FRAME = {"expr": "[]", "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount"]}

LOAD = {
    "measure-contract problem(s) for instrument": lambda: _load_error(_bond_cfg()),
    "level must be intensive (set scale_with_quantity: false)": lambda: _load_error(_bond_cfg(
        {"t": {"expr": "1.0", "unit": "number"}}, {"ExpiryInYears": "t"})),
    "this measure is a table: map a functions: entry with": lambda: _load_error(_bond_cfg(
        {"cf": {"expr": "0.0", "unit": "ccy"}}, {"Cashflows": "cf"})),
    "returns a frame; this measure needs a number": lambda: _load_error(_bond_cfg({"f": _FRAME}, {"IRDelta": {"scalar": "f"}})),
    "must include ['payment_amount']": lambda: _load_error(_bond_cfg({"cf": dict(_FRAME, scale_columns=["notional"])}, {"Cashflows": "cf"})),
    "its amounts must scale with the position": lambda: _load_error(_bond_cfg({"cf": dict(_FRAME, scale_with_quantity=False)}, {"Cashflows": "cf"})),
    "must be a non-empty reason string (why the library cannot compute it)": lambda: _load_error(_config_instrument({"IRVega": " "})),
    "must give a reason, or at least one of": lambda: _load_error(_config_instrument({"IRVega": {}})),
    "is not a class exported by pricebt.instrument": lambda: _load_error(_bond_cfg(instrument="IRSwapp")),
    "is allowed only with returns: frame": lambda: _load_error(_bond_cfg({"x": {"expr": "0.0", "unit": "ccy", "scale_columns": ["a"]}})),
    "must list the columns that scale with quantity": lambda: _load_error(_bond_cfg({"x": {"expr": "[]", "unit": "ccy", "returns": "frame"}})),
    "returns 'frame' is not one of ['buckets', 'scalar']": lambda: _load_error(_bond_cfg(
        portfolio_functions={"pf": {"expr": "[]", "unit": "ccy", "returns": "frame"}})),
    # every contract class is strict: only a mapping satisfies a row, a declaration is itself an error
    "not mapped (Bond/IRSwap/IRSwaption require a mapping for every contract measure)": lambda: _load_error(_swap_cfg()),
    "Map each missing measure (merge into functions:, portfolio_functions: and risk_measures:": lambda: _load_error(_bond_cfg()),
    "Bond/IRSwap/IRSwaption configs must map every contract measure; unsupported_measures cannot satisfy them": lambda: _load_error(
        _toy_bond({"FinancingToDate": REASON})),
    "(a preset or fallback of IRDelta)": lambda: _load_error(_swap_cfg({"IRDeltaParallel": REASON})),
    "syntax error at line 1: invalid syntax (in '... TODO')": lambda: _load_error({  # a mapping-skeleton stub pasted unchanged
        "schema_version": 1, "asset": "stub", "instrument": "ConfigInstrument", "currency": "USD", "market": {"expr": "1"},
        "functions": {"npv": {"expr": contracts.SKELETON_EXPR, "unit": "ccy"}}, "risk_measures": {"Price": "npv"}}),
    # warnings: the config loads
    "the mapping is used -- remove or narrow the stale declaration": lambda: _load_warnings(_config_instrument({"IRVega": REASON})),
    "is a preset or fallback of": lambda: _load_warnings(_toy_bond({"InflationDeltaParallel": REASON})),
    "nor a pricebt.risk measure": lambda: _load_warnings(_toy_bond({"IRVegaa": REASON})),
}


# ------------------------------------------------------------------ evaluation and pricing: a ConfigInstrument asset
def _session(functions=None, risk_measures=None, unsupported=None, portfolio_functions=None):
    cfg = {
        "schema_version": 1, "asset": "decl", "instrument": "ConfigInstrument", "currency": "USD", "market": {"expr": "1"},
        "functions": {"one": {"expr": "1.0", "unit": "ccy_per_bp"}, **(functions or {})},
        "portfolio_functions": portfolio_functions or {},
        "risk_measures": {"Price": "one", **(risk_measures or {})},
        "unsupported_measures": unsupported or {},
    }
    return PricebtSession.use(assets=[cfg])


def _pricing_error(session, measure) -> str:
    with pytest.raises(PricebtError) as exc:
        session.pricing.value(ConfigInstrument(pricebt_asset="decl", name="x"), D, measure, None)
    return str(exc.value)


def _raised(call) -> str:
    with pytest.raises(PricebtError) as exc:
        call()
    return str(exc.value)


_EXPLAIN = {"pnl": {"expr": "[]", "unit": "ccy", "returns": "buckets"},
            "pnl_to": {"expr": "[] if market_to is None else []", "unit": "ccy", "returns": "buckets"}}

PRICING = {
    "NotImplementedError: TODO (asset-config template)": lambda: _raised(
        lambda: PricebtSession.use(assets=[TEMPLATE]).pricing.resolve(IRSwap("Pay", "10y", "USD", 1e6), D, None)),
    "is declared unsupported (every form)": lambda: _pricing_error(_session(unsupported={"IRVega": "no vol model"}), IRVega(aggregation_level="Type")),
    "has no bucketed mapping; request": lambda: _pricing_error(_session(risk_measures={"IRVanna": "one"}), IRVanna),
    "does not reference pricebt_bump_size": lambda: _pricing_error(
        _session(risk_measures={"IRDelta": {"scalar": "one"}}), IRDelta(aggregation_level="Type", bump_size=1)),
    "it is honoured GS server-side and pricebt cannot pass it to an asset config": lambda: _pricing_error(
        _session(risk_measures={"IRDelta": {"scalar": "one"}}), IRDelta(aggregation_level="Type", mkt_marking_options="Mode")),
    "frame is missing required column(s)": lambda: _pricing_error(
        _session({"cf": dict(_FRAME, expr="[{'payment_amount': 1.0}]")}, {"Cashflows": "cf"}), Cashflows),
    "are not columns of the frame": lambda: _pricing_error(_session({"cf": dict(_FRAME, expr="[{'x': 1.0}]")}, {"Cashflows": "cf"}), Cashflows),
    "a frame result must be a DataFrame or a list of dicts": lambda: _pricing_error(_session({"cf": dict(_FRAME, expr="1.0")}, {"Cashflows": "cf"}), Cashflows),
    "is not supported: pass a CloseMarket(date=...)": lambda: _raised(lambda: PricingContext(pricing_date=D, market=object())),
    "references neither market_to nor pricebt_to_date": lambda: _pricing_error(
        _session(portfolio_functions=_EXPLAIN, risk_measures={"PnlExplain": "pnl"}), PnlExplain(CloseMarket(date=T))),
    "explains to the pricing date's own close": lambda: _pricing_error(
        _session(portfolio_functions=_EXPLAIN, risk_measures={"PnlExplain": "pnl_to"}), PnlExplainClose()),
    "explains to the live market, which is GS server-side": lambda: _raised(PnlExplainLive),
    "is GS server-side (portfolio persistence)": lambda: _raised(lambda: Portfolio().save()),
}


# ------------------------------------------------------------------ P&L definitions: plain ValueError, not PricebtError
def _value_error(call) -> str:
    with pytest.raises(ValueError) as exc:
        call()
    return str(exc.value)


def _explain_table(currencies, attributes):
    """pnl_explain_table() of a one-week toy swap book (one declaration-only toy swap per currency)."""
    from pricebt.backtests.actions import AddTradeAction
    from pricebt.backtests.backtest_objects import PnlDefinition
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements

    PricebtSession.use(assets=[ROOT / "tests" / "assets" / f"toy_{c.lower()}_irs.yaml" for c in currencies])
    book = [IRSwap("Pay", "10y", c, 1e6, name=c) for c in currencies]
    trigger = DateTrigger(DateTriggerRequirements(dates=[D]), [AddTradeAction(book, name="Add")])
    bt = GenericEngine().run_backtest(Strategy(None, trigger), start=D, end=T, frequency="1b", show_progress=False,
                                      pnl_explain=PnlDefinition(attributes))
    return bt.pnl_explain_table()


def _delta_attribute(name="d"):
    from pricebt.risk import IRDeltaParallel, IRFwdRate

    return PnlAttribute(name, IRDeltaParallel, IRFwdRate, 1.0)


def _definition_for_a_config_instrument_declaring_theta():
    """Only a class without a contract can still declare a measure the definition reads (a bond, swap
    or swaption config with a gap does not load): a ConfigInstrument copy of the toy bond."""
    import sys

    sys.path.insert(0, str(ROOT / "skills" / "pricebt-pnl-attribution" / "scripts"))
    import attribution

    raw = _toy_bond({"Theta": REASON})
    raw.update(instrument="ConfigInstrument")
    raw.pop("match")
    del raw["risk_measures"]["Theta"]
    return attribution.definition_for([raw])


P_AND_L = {
    "every held asset must map every measure the definition reads": lambda: _value_error(_definition_for_a_config_instrument_declaring_theta),
    "Cannot aggregate results with different units on": lambda: _value_error(lambda: _explain_table(["USD", "EUR"], [_delta_attribute()])),
    "PnlAttribute names must be unique": lambda: _value_error(lambda: _explain_table(["USD"], [_delta_attribute(), _delta_attribute()])),
    "rate_unit must be one of": lambda: _value_error(lambda: ir_pnl_definition(rate_unit="bps")),
    "second_order cannot be combined with cross_market_data_metric": lambda: _value_error(
        lambda: PnlAttribute("x", IRDelta, IRDelta, 1.0, second_order=True, cross_market_data_metric=IRDelta)),
}


@pytest.mark.parametrize("fragment", list(LOAD) + list(PRICING) + list(P_AND_L))
def test_catalogue_quotes_the_raised_message(fragment):
    message = (LOAD.get(fragment) or PRICING.get(fragment) or P_AND_L[fragment])()
    assert fragment in message, f"the code now says: {message}"
    assert fragment in CATALOGUE, "add the fragment to skills/pricebt-asset-config-cookbook/references/error-catalogue.md"


def test_the_fixed_nested_scope_name_error_is_gone():
    """Injected names reach generators and lambdas in an expression (tests/test_namespace_scopes.py)."""
    assert "NameError: name 'market' is not defined" not in CATALOGUE
    s = _session({"gen": {"expr": "sum(market for _ in (1,))", "unit": "ccy_per_bp"}}, {"IRBasis": "gen"})
    assert float(s.pricing.value(ConfigInstrument(pricebt_asset="decl", name="x"), D, IRBasis(aggregation_level="Type"), None)) == 1.0


def test_the_stale_dev_i8_text_is_gone():
    """DEV-I10 narrowed DEV-I8: parameters pass through to functions that name them."""
    assert "pricebt passes only aggregation_level and currency" not in CATALOGUE
