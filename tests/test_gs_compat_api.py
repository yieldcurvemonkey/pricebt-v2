"""gs_quant facade, API level (no engine runs, no rateslib): import map, risk-measure mapping, enums, sessions, cost-model conversion, stubs."""
import copy
import datetime as dt
import pickle
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from pricebt.costs import AggregateCost, ConstantCost, CostContext, ScaledCost
from pricebt.errors import ConfigError, NotSupportedError
from pricebt.timeutil import Calendar

pytestmark = pytest.mark.core

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ section 1: the import map
def test_section1_import_map_resolves_under_pricebt():
    from pricebt.backtests.strategy import Strategy  # noqa: F401
    from pricebt.backtests.triggers import (AggregateTrigger, AggregateTriggerRequirements, AggType, DateTrigger, DateTriggerRequirements,  # noqa: F401
                                            MeanReversionTrigger, MeanReversionTriggerRequirements, MktTrigger, MktTriggerRequirements, NotTrigger,
                                            NotTriggerRequirements, PeriodicTrigger, PeriodicTriggerRequirements, PortfolioTrigger, PortfolioTriggerRequirements,
                                            RiskTriggerRequirements, StrategyRiskTrigger, TriggerDirection)
    from pricebt.backtests.actions import (AddScaledTradeAction, AddTradeAction, EnterPositionQuantityScaledAction, ExitAllPositionsAction,  # noqa: F401
                                           ExitTradeAction, HedgeAction, RebalanceAction, ScalingActionType, BacktestTradingQuantityType)
    from pricebt.backtests.generic_engine import GenericEngine  # noqa: F401
    from pricebt.backtests.equity_vol_engine import EquityVolEngine  # noqa: F401
    from pricebt.backtests.predefined_asset_engine import PredefinedAssetEngine  # noqa: F401
    from pricebt.backtests.data_sources import GenericDataSource, GsDataSource, MissingDataStrategy  # noqa: F401
    from pricebt.backtests.backtest_objects import (AggregateTransactionModel, BackTest, Backtest, ConstantTransactionModel,  # noqa: F401
                                                    ScaledTransactionModel, TransactionAggType)
    from pricebt.risk import DollarPrice, FXDelta, IRDelta, IRDeltaParallel, Price  # noqa: F401
    from pricebt.common import AggregationLevel, BuySell, Currency, PayReceive  # noqa: F401
    from pricebt.session import GsSession, PricebtSession  # noqa: F401
    from pricebt.instrument import EqOption, FXForward, IRSwap, IRSwaption, OptionStyle, OptionType  # noqa: F401
    import pricebt.backtests as bts
    import pricebt.strategy as pbs

    # the facade triggers/strategy ARE the pricebt classes (no parallel implementation to drift)
    assert bts.PeriodicTrigger is pbs.PeriodicTrigger and bts.Strategy is pbs.Strategy and bts.RiskTriggerRequirements is pbs.StrategyRiskTriggerRequirements
    assert BackTest is Backtest and issubclass(bts.AddTradeAction, pbs.AddTradeAction) and issubclass(bts.HedgeAction, pbs.HedgeAction)


def test_importing_the_facade_never_imports_rateslib():
    code = ("import sys, pricebt.backtests, pricebt.backtests.generic_engine, pricebt.backtests.actions, pricebt.backtests.backtest_objects, "
            "pricebt.backtests.data_sources, pricebt.backtests.triggers, pricebt.backtests.strategy, pricebt.backtests.equity_vol_engine, "
            "pricebt.backtests.predefined_asset_engine, pricebt.instrument, pricebt.risk, pricebt.common, pricebt.session;"
            "print(sorted(m for m in sys.modules if m.split('.')[0] == 'rateslib' or m.startswith('pricebt.contrib.rateslib')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": "src"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


def guide_blocks():
    import re

    text = (ROOT / "docs" / "guides" / "backtesting.md").read_text(encoding="utf8")
    return re.findall(r"```python\n(.*?)```", text, flags=re.S)


def test_guide_code_blocks_parse_and_every_pricebt_import_exists():
    import ast
    import importlib

    def importable(name):  # a blocked or absent library: find_spec returns None or the blocker raises
        try:
            return importlib.util.find_spec(name) is not None
        except ModuleNotFoundError:
            return False

    blocks = guide_blocks()
    assert len(blocks) >= 25
    checked = 0
    for b in blocks:
        tree = ast.parse(b)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("gs_quant"), node.module
                if node.module.startswith("pricebt"):
                    parts = node.module.split(".")
                    lib = {"rateslib": "rateslib", "quantlib": "QuantLib"}.get(parts[2]) if parts[:2] == ["pricebt", "contrib"] and len(parts) > 2 else None
                    if lib is not None and not importable(lib):
                        continue
                    mod = importlib.import_module(node.module)
                    for a in node.names:
                        assert hasattr(mod, a.name), f"{node.module}.{a.name}"
                        checked += 1
    assert checked > 60


def test_instrument_constructors_import_no_library_and_need_a_registered_spec():
    """With every pricing library blocked the facade imports; IRSwap names what is missing (a session, then a spec) and IRSwaption is unsupported."""
    code = ("import sys\n"
            "for m in ('rateslib', 'QuantLib', 'gs_quant'): sys.modules[m] = None\n"
            "import pricebt.instrument as I\n"
            "from pricebt.errors import ConfigError, NotSupportedError\n"
            "from pricebt.session import PricebtSession\n"
            "from pricebt.testing.toys import ToyMDP\n"
            "def kind(f):\n"
            "    try:\n        f(); return 'BUILT'\n"
            "    except (ConfigError, NotSupportedError) as e:\n        return type(e).__name__\n"
            "print(kind(lambda: I.IRSwap('Pay', '10y', 'USD')))\n"
            "PricebtSession.use(market=ToyMDP())\n"
            "print(kind(lambda: I.IRSwap('Pay', '10y', 'USD')))\n"
            "print(kind(lambda: I.IRSwaption('Pay', '10y', 'USD', expiration_date='1m')))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": "src"})
    assert out.stdout.split() == ["ConfigError", "NotSupportedError", "NotSupportedError"], out.stdout + out.stderr


# ------------------------------------------------------------------ risk measures
def test_risk_measure_mapping_table():
    from pricebt.common import AggregationLevel as AL
    from pricebt.risk import (RISK_MEASURE_MAP, DollarPrice, EqDelta, FXDelta, FXGamma, FXVega, IRDelta, IRDeltaLocalCcy, IRDeltaParallel, IRGamma,
                              IRGammaParallel, IRVega, IRVegaParallel, Price, Theta)

    assert Price == "pv" and DollarPrice == "pv" and Price.is_price and not IRDeltaParallel.is_price
    for lvl in (AL.Type, AL.Asset, AL.Class, "Type", "Asset"):
        m = IRDelta(aggregation_level=lvl, currency="USD")
        assert m == "dv01" and not m.vector and m.name == "IRDelta"
    for m in (IRDelta, IRDelta(), IRDelta(aggregation_level=AL.Point), IRDeltaLocalCcy, IRDelta(currency="local")):
        assert m == "delta_ladder" and m.vector
    assert IRDeltaParallel == "dv01" and IRDeltaParallel.aggregation_level is AL.Asset
    assert (IRGamma, IRGammaParallel, IRVega, IRVegaParallel, Theta) == ("gamma", "gamma", "vega", "vega", "theta")
    assert (FXDelta, FXGamma, FXVega, EqDelta) == ("fx_delta", "fx_gamma", "fx_vega", "eq_delta") and not FXDelta.supported
    assert FXDelta(aggregation_level="Type", currency="USD") == "fx_delta"
    assert RISK_MEASURE_MAP["IRDeltaParallel"] == "dv01" and RISK_MEASURE_MAP["Price"] == "pv" and RISK_MEASURE_MAP["IRDelta"] == "delta_ladder"
    assert repr(IRDelta(aggregation_level=AL.Type, currency="local")) == "IRDelta(aggregation_level=Type, currency=local)"


def test_risk_measure_is_a_str_usable_wherever_a_measure_name_is():
    from pricebt.common import AggregationLevel as AL
    from pricebt.risk import IRDelta, as_risk_measure

    m = IRDelta(aggregation_level=AL.Type, currency="local")
    assert isinstance(m, str) and hash(m) == hash("dv01") and {m: 1}["dv01"] == 1 and f"measure_{m}" == "measure_dv01"
    assert m.measure == "dv01" and type(m.measure) is str
    assert copy.deepcopy(m) is m and copy.copy(m) is m
    back = pickle.loads(pickle.dumps(m))
    assert back == "dv01" and back.aggregation_level is AL.Type and back.currency == "local" and back.name == "IRDelta"
    assert as_risk_measure("IRDeltaParallel") == "dv01" and as_risk_measure("dv01") == "dv01" and not as_risk_measure("my_ladder").vector and as_risk_measure("delta_ladder").vector  # the schema decides, not the spelling
    df = pd.DataFrame({"Price": [1.0], m: [2.0]})
    assert df[m].iloc[0] == 2.0 and df.loc[:, m].iloc[0] == 2.0 and df["dv01"].iloc[0] == 2.0
    from pricebt.risk import Price

    assert df[Price].iloc[0] == 1.0, "a price measure selects the 'Price' column even on a plain DataFrame"


def test_risk_measure_rejects_what_it_cannot_compute():
    from pricebt.risk import IRDelta, Price

    with pytest.raises(NotSupportedError):
        IRDelta(aggregation_level="Type", currency="EUR")
    with pytest.raises(NotSupportedError):
        IRDelta(aggregation_level="Type", mkt_type="IR")
    with pytest.raises(NotSupportedError):
        Price(aggregation_level="Type")
    with pytest.raises(TypeError):
        Price(1, 2)
    with pytest.raises(TypeError, match="keyword"):
        IRDelta(["Type"])  # a list has an .index method: must not be mistaken for a pandas key
    with pytest.raises(ConfigError):
        IRDelta(aggregation_level="Sideways")


def test_enums_coerce_like_gs():
    from pricebt.common import AggregationLevel, BuySell, Currency, PayReceive

    assert PayReceive.coerce("Pay") is PayReceive.Pay and PayReceive.coerce("straddle") is PayReceive.Straddle and PayReceive.Receiver.is_receive
    assert BuySell.coerce("Sell") is BuySell.Sell and Currency.coerce("usd") is Currency.USD and AggregationLevel.coerce("type") is AggregationLevel.Type
    assert {m.value for m in AggregationLevel} == {"Type", "Asset", "Class", "Point"}
    with pytest.raises(ConfigError):
        BuySell.coerce("Hold")


# ------------------------------------------------------------------ sessions
def test_session_use_is_the_default_and_the_context_manager_restores_it():
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.session import PricebtSession, current_session
    from pricebt.testing.toys import ToyMDP

    PricebtSession.reset()
    with pytest.raises(ConfigError):
        _ = GenericEngine().session
    a, b = ToyMDP(), ToyMDP()
    s1 = PricebtSession.use(market=a)
    try:
        assert current_session() is s1 and GenericEngine().session is s1
        with PricebtSession.use(market=b, tz="Europe/London") as s2:
            assert current_session() is s2 and GenericEngine().session.tz == "Europe/London"
            own = GenericEngine(market=a, tz="Asia/Tokyo")
            assert own.session.market is a and own.session.tz == "Asia/Tokyo" and current_session() is s2
        assert current_session() is s1
        with PricebtSession(market=b) as s3:
            assert current_session() is s3
        assert current_session() is s1
    finally:
        s1.close()
        PricebtSession.reset()
    assert current_session() is None


def test_gs_session_needs_a_market_and_ignores_gs_credentials():
    from pricebt.session import GsSession, PricebtSession
    from pricebt.testing.toys import ToyMDP

    with pytest.raises(NotSupportedError, match="market"):
        GsSession.use()
    with pytest.raises(NotSupportedError, match="client_id"):
        GsSession.use(client_id=None, client_secret=None, scopes=("run_analytics",))
    s = GsSession.use(client_id=None, client_secret=None, scopes=("run_analytics",), market=ToyMDP())
    try:
        assert PricebtSession.get_current() is s
    finally:
        s.close()
        PricebtSession.reset()


def test_session_calendars_and_time_context(tmp_path):
    from pricebt.session import PricebtSession, resolve_calendar
    from pricebt.testing.toys import ToyMDP

    assert resolve_calendar(None).holidays == frozenset()
    assert dt.date(2024, 7, 4) in resolve_calendar([dt.date(2024, 7, 4)]).holidays
    p = tmp_path / "cal.json"
    p.write_text('{"holidays": ["2024-12-25"], "name": "x"}', encoding="utf8")
    assert dt.date(2024, 12, 25) in resolve_calendar(str(p)).holidays
    with pytest.raises(ConfigError):
        resolve_calendar("nowhere")
    s = PricebtSession(market=ToyMDP(), calendar=[dt.date(2024, 1, 15)], session_close=dt.time(16, 0))
    ctx = s.time_context(holiday_calendar=[dt.date(2024, 2, 19)])
    assert not ctx.calendar.is_business_day(dt.date(2024, 1, 15)) and not ctx.calendar.is_business_day(dt.date(2024, 2, 19))
    assert ctx.at(dt.date(2024, 1, 16)) == pd.Timestamp("2024-01-16 16:00", tz="America/New_York") and s.daily_time == dt.time(16, 0)
    with pytest.raises(ConfigError):
        PricebtSession(market=None)


def test_named_calendars_resolve_through_the_registry_only():
    """Core knows no calendar by name: an adapter (or config) registers one; an unregistered name is an error that says so."""
    from pricebt.registries import CALENDARS
    from pricebt.session import resolve_calendar

    with pytest.raises(ConfigError, match="registered"):
        resolve_calendar("mlk_holidays")
    CALENDARS.register("mlk_holidays", lambda: [dt.date(2024, 1, 15)])
    try:
        cal = resolve_calendar("mlk_holidays")
    finally:
        CALENDARS._items.pop("mlk_holidays")
    assert not cal.is_business_day(dt.date(2024, 1, 15)) and cal.is_business_day(dt.date(2024, 1, 16))


# ------------------------------------------------------------------ transaction and cash models
def _ctx(qty=3.0, pv=-1000.0):
    return CostContext(pd.Timestamp("2024-01-02 17:00", tz="America/New_York"), qty, lambda n: {"pv": pv, "notional": 2e6}[n])


def test_transaction_models_convert_to_pricebt_costs_with_gs_semantics():
    from pricebt.backtests.backtest_objects import (AggregateTransactionModel, ConstantTransactionModel, ScaledTransactionModel, TransactionAggType,
                                                    to_cost_model)
    from pricebt.risk import Price

    c = to_cost_model(ConstantTransactionModel(500))
    assert isinstance(c, ConstantCost) and c.cost(_ctx()) == 500.0
    s = to_cost_model(ScaledTransactionModel(scaling_type="notional_amount", scaling_level=0.0001))
    assert isinstance(s, ScaledCost) and s.cost(_ctx()) == pytest.approx(0.0001 * 2e6 * 3.0)
    p = to_cost_model(ScaledTransactionModel(scaling_type=Price, scaling_level=0.01))
    assert p.scaling_type == "measure:pv" and p.cost(_ctx()) == pytest.approx(0.01 * 1000.0 * 3.0)
    parts = (ConstantTransactionModel(100), ScaledTransactionModel("notional_amount", 0.00005))  # 100 and 0.00005*2e6*3 = 300
    for agg, want in ((TransactionAggType.SUM, 400.0), (TransactionAggType.MAX, 300.0), (TransactionAggType.MIN, 100.0)):
        m = to_cost_model(AggregateTransactionModel(transaction_models=parts, aggregate_type=agg))
        assert isinstance(m, AggregateCost) and m.cost(_ctx()) == pytest.approx(want), agg
    assert to_cost_model(AggregateTransactionModel(transaction_models=parts)).cost(_ctx()) == pytest.approx(400.0), "SUM is the default"
    assert to_cost_model(None) is None and to_cost_model(c) is c


def test_scaling_by_a_vector_risk_is_refused():
    from pricebt.backtests.backtest_objects import ScaledTransactionModel, to_cost_model
    from pricebt.risk import IRDelta

    with pytest.raises(NotSupportedError):
        to_cost_model(ScaledTransactionModel(IRDelta, 0.01))


def test_cash_accrual_models_follow_gs_compounding():
    from pricebt.backtests.backtest_objects import ConstantCashAccrualModel, DataCashAccrualModel
    from pricebt.backtests.data_sources import GenericDataSource

    t0, t1 = pd.Timestamp("2024-01-02 17:00", tz="America/New_York"), pd.Timestamp("2024-01-05 17:00", tz="America/New_York")
    assert ConstantCashAccrualModel(0.05).interest(1e6, t0, t1) == pytest.approx(1e6 * ((1 + 0.05 / 365) ** 3 - 1))
    assert ConstantCashAccrualModel(0.001, annual=False).interest(1e6, t0, t1) == pytest.approx(1e6 * ((1.001) ** 3 - 1))
    ds = GenericDataSource(pd.Series([0.04, 0.05], index=[dt.date(2024, 1, 2), dt.date(2024, 1, 5)]))
    assert DataCashAccrualModel(ds).interest(1e6, t0, t1) == pytest.approx(1e6 * ((1 + 0.04 / 365) ** 3 - 1)), "rate read at the interval START"


# ------------------------------------------------------------------ stubs
@pytest.mark.parametrize("make", [
    lambda: __import__("pricebt.backtests.equity_vol_engine", fromlist=["x"]).EquityVolEngine(),
    lambda: __import__("pricebt.backtests.predefined_asset_engine", fromlist=["x"]).PredefinedAssetEngine(),
    lambda: __import__("pricebt.backtests.actions", fromlist=["x"]).EnterPositionQuantityScaledAction(priceables=None, trade_duration="1m", trade_quantity=1000),
    lambda: __import__("pricebt.backtests.data_sources", fromlist=["x"]).GsDataSource("dataset", "coord"),
    lambda: __import__("pricebt.backtests.backtest_objects", fromlist=["x"]).OisFixingCashAccrualModel(),
    lambda: __import__("pricebt.instrument", fromlist=["x"]).FXForward(pair="EURUSD", settlement_date="1y", name="hedge_fwd"),
    lambda: __import__("pricebt.instrument", fromlist=["x"]).FXOption(pair="EURUSD", premium=0),
    lambda: __import__("pricebt.instrument", fromlist=["x"]).EqOption(".STOXX50E", expiration_date="3m", strike_price="ATM"),
    lambda: __import__("pricebt.instrument", fromlist=["x"]).EqVarianceSwap(".SPX"),
])
def test_gs_service_backed_objects_are_clear_stubs(make):
    with pytest.raises(NotSupportedError) as e:
        make()
    msg = str(e.value)
    assert any(w in msg for w in ("GenericEngine", "pricable", "GenericDataSource", "ConstantCashAccrualModel")), msg


def test_generic_engine_rejects_gs_handler_maps_and_non_price_price_measures():
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.risk import DollarPrice, IRDeltaParallel

    GenericEngine(price_measure=DollarPrice)
    with pytest.raises(NotSupportedError):
        GenericEngine(action_impl_map={object: object})
    with pytest.raises(NotSupportedError):
        GenericEngine(price_measure=IRDeltaParallel)
