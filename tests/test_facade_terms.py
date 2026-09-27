"""The gs_quant-style constructors produce TERMS on a spec the session registered (spec F-1, F-2, F-6), with no library anywhere.

The 'library' is `spec_helpers.Weird`: a swap in somebody else's vocabulary, reached only through bindings (the same double the contracts tests use).
"""
import copy
import datetime as dt

import pandas as pd
import pytest

from pricebt.backtests import AddTradeAction, GenericEngine, PeriodicTrigger, PeriodicTriggerRequirements, Strategy
from pricebt.common import Currency, PayReceive
from pricebt.contracts.spec import InstrumentSpec, Stack
from pricebt.errors import ConfigError, NotSupportedError
from pricebt.instrument import IRSwap, IRSwaption, InstrumentTemplate
from pricebt.risk import IRDeltaParallel
from pricebt.session import PricebtSession
from spec_helpers import BIND, Market, weird_kit

pytestmark = pytest.mark.core

NY = "America/New_York"
CONV = {"calendar": "test", "day_count": "test"}  # opaque to pricebt: hashed, handed to the factory verbatim

STACK = Stack(
    name="weird",
    instruments={"usd_ois": {"factory": weird_kit, "conventions": CONV}, "usd_ois_b": {"factory": weird_kit, "conventions": {"calendar": "other"}}},
    defaults={"swap:USD": "usd_ois"},
    accepts_gs={"floating_rate_option": ["USD-SOFR-COMPOUND"]},
)


class Provider:
    """Snapshot pricers with a level of 4.0 percent rising 1bp a business day; only `.level` is read (through the bindings)."""

    def get_pricer(self, ts, request=None):
        m = Market(4.0 + 0.01 * (pd.Timestamp(ts).date() - dt.date(2024, 1, 2)).days)
        m.ts = pd.Timestamp(ts)
        m.reference_date = m.ts.date()
        m.describe = lambda: {"type": "Market"}
        m.lookup = lambda name, **kw: None
        return m


@pytest.fixture(autouse=True)
def _no_session_leaks():
    PricebtSession.reset()
    yield
    PricebtSession.reset()


def session(**kw):
    return PricebtSession.use(market=Provider(), stack=STACK, **kw)


# ------------------------------------------------------------------ terms, not objects
def test_irswap_is_terms_on_the_registered_spec_and_builds_nothing():
    session()
    t = IRSwap("Pay", "10y", "USD", 5e7, name="p10")
    assert isinstance(t, InstrumentTemplate) and t.name == "p10" and t.gs_name == "p10" and t.notional_amount == 5e7
    assert t.spec.name == "usd_ois" and t.spec.conventions == CONV and t.direction == +1
    assert t.terms["side"] == "pay" and t.terms["maturity"] == "10y" and t.terms["notional"] == 5e7
    assert t.terms["fixed_rate"] == "par" and t.terms["effective"] == "spot", "ATMF is the token `par`; the library resolves it"
    assert IRSwap("Receive", "5y", Currency.USD).direction == -1


def test_gs_decimals_become_percent_in_the_facade_and_nowhere_else():
    session()
    assert IRSwap("Pay", "10y", "USD", fixed_rate=0.04).terms["fixed_rate"] == pytest.approx(4.0)
    assert IRSwap("Pay", "10y", "USD", fixed_rate=-0.005).terms["fixed_rate"] == pytest.approx(-0.5)
    with pytest.raises(ConfigError, match="decimals"):
        IRSwap("Pay", "10y", "USD", fixed_rate=4.0)
    off = IRSwap("Pay", "10y", "USD", fixed_rate="ATMF+25")
    assert off.terms["fixed_rate"] == "par" and off.terms["extras"] == {"par_spread_bp": 25.0}
    assert IRSwap("Pay", "10y", "USD", fixed_rate="ATMF-10bp").terms["extras"] == {"par_spread_bp": -10.0}
    with pytest.raises(ConfigError):
        IRSwap("Pay", "10y", "USD", fixed_rate="cheap")


def test_a_negative_notional_flips_the_direction_and_dates_pass_through_untouched():
    session()
    t = IRSwap("Pay", dt.date(2034, 1, 2), "USD", -1e6, effective_date="1y")
    assert t.terms["side"] == "receive" and t.terms["notional"] == 1e6 and t.terms["effective"] == "1y" and t.terms["maturity"] == dt.date(2034, 1, 2)
    with pytest.raises(ConfigError, match="notional_amount"):
        IRSwap("Pay", "10y", "USD", 0)
    with pytest.raises(ConfigError):
        IRSwap("Straddle", "10y", "USD")
    with pytest.raises(ConfigError, match="termination_date"):
        IRSwap("Pay", None, "USD")


def test_the_factory_resolves_par_and_the_resolved_terms_come_back():
    session()
    p = Provider().get_pricer(pd.Timestamp("2024-01-10 17:00", tz=NY))
    built = IRSwap("Pay", "10y", "USD").build(p, p.ts)
    assert built.terms["fixed_rate"] == pytest.approx(p.level) and isinstance(built.terms["effective"], dt.date) and built.terms["direction"] == +1


def test_clone_repr_and_deepcopy_keep_the_gs_attributes():
    session()
    t = IRSwap("Pay", "10y", "USD", 1e6, name="a")
    c = t.clone(name="b", notional_amount=2e6)
    assert isinstance(c, InstrumentTemplate) and c.name == "b" and c.notional_amount == 2e6 and c.terms["maturity"] == "10y"
    d = copy.deepcopy(t)
    assert type(d) is InstrumentTemplate and d.notional_amount == 1e6 and d.gs_name == "a" and d.spec is t.spec and d.terms == t.terms and d.terms is not t.terms
    assert "IRSwap(" in repr(t) and "Pay" in repr(t)
    r = t.renamed("zz")
    assert type(r) is InstrumentTemplate and r.name == "zz" and r.instrument_type == "IRSwap"
    assert type(t.with_terms(notional=3e6)) is InstrumentTemplate and t.with_terms(notional=3e6).terms["notional"] == 3e6


# ------------------------------------------------------------------ what the session registers
def test_no_session_no_spec_and_other_currencies_are_clear_errors():
    with pytest.raises(ConfigError, match="session"):
        IRSwap("Pay", "10y", "USD")
    PricebtSession.use(market=Provider())
    with pytest.raises(NotSupportedError, match="swap:USD"):
        IRSwap("Pay", "10y", "USD")
    session()
    with pytest.raises(NotSupportedError, match="reporting currency"):
        IRSwap("Pay", "10y", "EUR")


def test_the_session_can_pick_another_shipped_spec_by_name_or_supply_a_raw_one():
    PricebtSession.use(market=Provider(), stack=STACK, instruments={"swap:USD": "usd_ois_b"})
    assert IRSwap("Pay", "10y", "USD").spec.conventions == {"calendar": "other"}
    PricebtSession.use(market=Provider(), stack=STACK, instruments={"swap:USD": {"factory": weird_kit, "conventions": {"calendar": "mine"}, "bind": {"gamma": {**BIND["gamma"], "kwargs": {"market": "@pricer", "bump_bp": 5.0}}}}})
    t = IRSwap("Pay", "10y", "USD")
    assert t.spec.conventions == {"calendar": "mine"} and t.spec.bindings["gamma"].kwargs["bump_bp"] == 5.0
    PricebtSession.use(market=Provider(), stack=STACK, instruments={"swap:USD": "nope"})
    with pytest.raises(ConfigError, match="nope"):
        IRSwap("Pay", "10y", "USD")


def test_a_spec_of_the_wrong_asset_class_is_refused():
    from pricebt.testing.toys import forward

    PricebtSession.use(market=Provider(), instruments={"swap:USD": {"factory": forward, "asset_class": "toy"}})
    with pytest.raises(ConfigError, match="has asset class 'toy'"):
        IRSwap("Pay", "10y", "USD")


def test_a_stack_must_be_a_stack_and_a_dotted_one_is_allow_listed():
    with pytest.raises(ConfigError, match="Stack"):
        PricebtSession(market=Provider(), stack=object())
    with pytest.raises(ConfigError, match="Stack"):
        PricebtSession(market=Provider(), stack="pricebt.testing.toys:forward")  # resolves, but a Kit is not a Stack
    with pytest.raises(ConfigError, match="allow"):
        PricebtSession(market=Provider(), stack="os:path")  # outside the allow-list


def test_gs_only_swap_fields_are_refused_unless_the_stack_accepts_the_value():
    session()
    IRSwap("Pay", "10y", "USD", floating_rate_option="USD-SOFR-COMPOUND", fee=0)
    with pytest.raises(NotSupportedError, match="fixed_rate_frequency"):
        IRSwap("Pay", "10y", "USD", fixed_rate_frequency="3m")
    with pytest.raises(NotSupportedError, match="floating_rate_option"):
        IRSwap("Pay", "10y", "USD", floating_rate_option="EUR-EURIBOR")
    with pytest.raises(NotSupportedError, match="fee"):
        IRSwap("Pay", "10y", "USD", fee=5)
    with pytest.raises(TypeError, match="unexpected keyword"):
        IRSwap("Pay", "10y", "USD", notional_ammount=1)  # a typo is not a gs field
    with pytest.raises(TypeError, match="unexpected keyword"):
        IRSwap("Pay", "10y", "USD", risk_model="anything")


def test_swaptions_and_the_other_gs_instruments_are_unsupported_with_a_pointer():
    session()
    with pytest.raises(NotSupportedError, match="options workflow"):
        IRSwaption("Pay", "10y", "USD", expiration_date="1m")
    from pricebt.instrument import Bond, FXOption

    with pytest.raises(NotSupportedError, match="bond spec"):
        Bond()
    with pytest.raises(NotSupportedError):
        FXOption()


def test_the_stack_wrap_reaches_the_market_and_an_explicit_wrap_wins():
    seen = []

    def tag(p):
        seen.append(p)
        return p

    s = PricebtSession.use(market=Provider(), stack=Stack(name="w", wrap=tag))
    md = s.market_data()
    md.clock.advance(pd.Timestamp("2024-01-10 17:00", tz=NY))
    md.pricer(pd.Timestamp("2024-01-10 17:00", tz=NY))
    assert len(seen) == 1
    other = []
    s2 = PricebtSession(market={"primary": Provider(), "aux": Provider()}, stack=Stack(name="w", wrap=tag), wrap=lambda p: other.append(p) or p)
    md2 = s2.market_data()
    md2.clock.advance(pd.Timestamp("2024-01-10 17:00", tz=NY))
    md2.pricer(pd.Timestamp("2024-01-10 17:00", tz=NY))
    assert len(other) == 1 and len(seen) == 1, "wrap= replaces the stack's; a mapping of roles gets it on the primary role only"
    assert s2.market_data().bindings["aux"].wrap is None and s2.market_data().bindings["primary"].wrap is not None


# ------------------------------------------------------------------ through the engine
def test_a_gs_style_backtest_runs_on_a_registered_spec_and_reconciles():
    session(calendar="weekends")
    swap = IRSwap("Pay", "10y", "USD", 1e7, name="p10")
    trig = PeriodicTrigger(PeriodicTriggerRequirements(start_date=dt.date(2024, 1, 2), end_date=dt.date(2024, 3, 29), frequency="1m"), AddTradeAction(swap, "2w"))
    bt = GenericEngine().run_backtest(Strategy(None, trig), start=dt.date(2024, 1, 2), end=dt.date(2024, 3, 29), frequency="1b", risks=[IRDeltaParallel], show_progress=False)
    p = bt.result.positions
    assert len(p) >= 2 and bt.result.reconcile().ok
    assert set(p["template"]) == {"p10"}
    assert bt.result_summary["dv01"].abs().max() > 0, "the bound dv01 was recorded"
    led = bt.result.trades  # T4: the factory's RESOLVED terms are on the trade ledger
    first = led.iloc[0]
    assert first["instrument"] == "usd_ois" and first["term_side"] == "pay" and first["term_notional"] == 1e7 and first["term_direction"] == 1
    assert first["term_fixed_rate"] == pytest.approx(4.0), "`par` was resolved by the factory on the trade date's market"
    assert not isinstance(first["term_maturity"], str), "the tenor was resolved to a date by the library"


def test_currency_codes_are_case_insensitive_for_the_lookup_and_the_reporting_currency():
    session()
    assert IRSwap("Pay", "10y", "usd").spec.name == "usd_ois"
    assert PricebtSession.get_current().instrument_spec("swap", "usd").name == "usd_ois"
    PricebtSession.use(market=Provider(), stack=STACK, reporting_currency="usd")
    assert IRSwap("Pay", "10y", "USD").spec.name == "usd_ois"


def test_a_binding_keeps_its_own_wrap_and_the_request_is_merged_into_the_binding_request():
    from pricebt.market import Binding

    seen_requests = []

    class Recording(Provider):
        def get_pricer(self, ts, request=None):
            seen_requests.append(dict(request or {}))
            return super().get_pricer(ts, request)

    own = lambda p: p  # noqa: E731
    s = PricebtSession(market=Binding(Recording(), {"a": 1}, own), stack=Stack(name="w", wrap=lambda p: p))
    md = s.market_data({"csa_term": "x"})
    assert md.bindings["primary"].wrap is own, "a binding's own wrap is not replaced by the stack's"
    md.clock.advance(pd.Timestamp("2024-01-10 17:00", tz=NY))
    md.pricer(pd.Timestamp("2024-01-10 17:00", tz=NY))
    assert seen_requests == [{"a": 1, "csa_term": "x"}]


def test_argument_problems_are_reported_without_a_session_only_a_valid_call_needs_one():
    """gs argument validation (typos, unsupported fields, decimals, notional) does not depend on what is registered; the spec lookup comes last."""
    with pytest.raises(TypeError, match="unexpected keyword"):
        IRSwap("Pay", "10y", "USD", not_a_gs_field=1)
    with pytest.raises(NotSupportedError, match="fixed_rate_frequency"):
        IRSwap("Pay", "10y", "USD", fixed_rate_frequency="3m")
    with pytest.raises(NotSupportedError, match="EUR"):
        IRSwap("Pay", "10y", "EUR")
    with pytest.raises(ConfigError, match="decimals"):
        IRSwap("Pay", "10y", "USD", fixed_rate=4.0)
    with pytest.raises(ConfigError, match="termination_date"):
        IRSwap("Pay", None, "USD")
    with pytest.raises(ConfigError, match="session"):
        IRSwap("Pay", "10y", "USD")


def test_result_ccy_follows_the_engines_own_session_not_only_the_current_one():
    from pricebt.testing.toys import ToyMDP

    PricebtSession.reset()  # no current session at all
    eng = GenericEngine(market=ToyMDP(), reporting_currency="EUR")
    strat = Strategy(None, [])
    ok = eng.run_backtest(strat, start=dt.date(2024, 1, 2), end=dt.date(2024, 1, 12), frequency="1b", result_ccy="EUR", show_progress=False)
    assert len(ok.result.equity) > 0
    with pytest.raises(NotSupportedError, match="reporting currency 'EUR'"):
        eng.run_backtest(strat, start=dt.date(2024, 1, 2), end=dt.date(2024, 1, 12), frequency="1b", result_ccy="USD", show_progress=False)
