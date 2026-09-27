"""Findings of the second adversarial review of the gs-style facade (`pricebt.instrument`, `pricebt.session`): argument checks before the session (M2), the
`instruments=` keys (M3), whose `accepts_gs` applies (M4), and the LOW findings (template sync after `with_terms`, session stack discipline, calendar names,
reporting currency, holiday iterables, the documented `stack=` form)."""
from __future__ import annotations

import datetime as dt
import enum
import re

import numpy as np
import pandas as pd
import pytest

from pricebt.common import Currency, PayReceive
from pricebt.contracts.spec import Stack
from pricebt.errors import ConfigError, NotSupportedError
from pricebt.instrument import IRSwap, InstrumentTemplate
from pricebt.registries import CALENDARS
from pricebt.session import PricebtSession, current_session, resolve_calendar

import pricebt.session as session_module
from spec_helpers import weird_kit
from test_facade_terms import CONV, STACK, Provider, session

pytestmark = pytest.mark.core

RAW = {"factory": weird_kit, "conventions": {"calendar": "MINE"}}


@pytest.fixture(autouse=True)
def _no_session_leaks():
    PricebtSession.reset()
    yield
    PricebtSession.reset()


# ------------------------------------------------------------------ M2: argument problems are reported BEFORE a session is needed, naming the argument
BAD_ARGS = [
    ("junk tenor", dict(termination_date="banana"), "termination_date"),
    ("empty tenor", dict(termination_date="  "), "termination_date"),
    ("bad iso date", dict(termination_date="2034-13-45"), "termination_date"),
    ("integer date", dict(termination_date=10), "termination_date"),
    ("NaT date", dict(termination_date=pd.NaT), "termination_date"),
    ("spot is not a maturity", dict(termination_date="spot"), "termination_date"),
    ("junk effective", dict(termination_date="10y", effective_date="whenever"), "effective_date"),
    ("empty effective", dict(termination_date="10y", effective_date=""), "effective_date"),
    ("nan notional", dict(termination_date="10y", notional_amount=float("nan")), "notional_amount"),
    ("inf notional", dict(termination_date="10y", notional_amount=float("inf")), "notional_amount"),
    ("-inf notional", dict(termination_date="10y", notional_amount=float("-inf")), "notional_amount"),
    ("bool notional", dict(termination_date="10y", notional_amount=True), "notional_amount"),
    ("numpy bool notional", dict(termination_date="10y", notional_amount=np.bool_(True)), "notional_amount"),
    ("text notional", dict(termination_date="10y", notional_amount="abc"), "notional_amount"),
    ("bool rate", dict(termination_date="10y", fixed_rate=True), "fixed_rate"),
    ("nan rate", dict(termination_date="10y", fixed_rate=float("nan")), "fixed_rate"),
    ("text fee", dict(termination_date="10y", fee="x"), "fee"),
    ("nan fee", dict(termination_date="10y", fee=float("nan")), "fee"),
    ("bool fee", dict(termination_date="10y", fee=True), "fee"),
    ("integer name", dict(termination_date="10y", name=5), "name"),
]


@pytest.mark.parametrize("label,kw,arg", BAD_ARGS, ids=[b[0] for b in BAD_ARGS])
def test_a_bad_argument_is_a_config_error_naming_it_with_or_without_a_session(label, kw, arg):
    with pytest.raises(ConfigError, match=arg) as no_session:
        IRSwap("Pay", notional_currency="USD", **kw)
    assert "session" not in str(no_session.value).lower(), "the argument problem comes first, not 'create a session'"
    session()
    with pytest.raises(ConfigError, match=arg) as with_session:
        IRSwap("Pay", notional_currency="USD", **kw)
    assert type(with_session.value) is type(no_session.value)


def test_a_pay_or_receive_that_is_neither_is_refused_before_a_session_too():
    with pytest.raises(ConfigError, match="banana"):
        IRSwap("banana", "10y", "USD")


def test_valid_arguments_still_need_a_session_and_the_spec_lookup_still_comes_last():
    with pytest.raises(ConfigError, match="create a session first"):
        IRSwap("Pay", "10y", "USD", effective_date="spot", notional_amount=np.float64(5e6), fixed_rate=np.float64(0.04))
    session()
    t = IRSwap("Pay", "10y", "USD", effective_date="spot", notional_amount=np.float64(5e6), fixed_rate=np.float64(0.04))
    assert t.terms["notional"] == 5e6 and t.terms["fixed_rate"] == pytest.approx(4.0)
    assert IRSwap("Pay", "10y", "USD", notional_amount="1e7").terms["notional"] == 1e7, "a numeric string is still a number, as before"


def test_a_tenor_or_iso_date_string_reaches_the_factory_stripped():
    session()
    t = IRSwap("Pay", "  10y ", "USD", effective_date=" 1y ")
    assert t.terms["maturity"] == "10y" and t.terms["effective"] == "1y"
    assert IRSwap("Pay", " 2034-01-02 ", "USD").terms["maturity"] == "2034-01-02"


# ------------------------------------------------------------------ M3: instruments= keys are normalised and validated
@pytest.mark.parametrize("key", ["swap:usd", "Swap:USD", " swap : usd ", "SWAP:Usd"])
def test_an_instruments_key_written_in_another_case_still_overrides_the_stack_default(key):
    PricebtSession.use(market=Provider(), stack=STACK, instruments={key: RAW})
    assert IRSwap("Pay", "10y", "USD").spec.conventions == {"calendar": "MINE"}


@pytest.mark.parametrize("key", ["swap:USDD", "swap:US", "swap", "swap:", ":USD", "swap:USD:x", "", "swap:U$D", 5, None])
def test_a_malformed_instruments_key_is_refused_when_the_session_is_made(key):
    with pytest.raises(ConfigError, match="instruments") as ei:
        PricebtSession(market=Provider(), stack=STACK, instruments={key: RAW})
    assert ei.value.code == "SESSION" and "<asset_class>:<CCY>" in str(ei.value), "the message shows the accepted form"
    with pytest.raises(ConfigError, match="instruments"):
        PricebtSession.use(market=Provider(), stack=STACK, instruments={key: RAW})


@pytest.mark.parametrize("instruments", [["swap:USD"], "swap:USD", 5])
def test_instruments_must_be_a_mapping(instruments):
    with pytest.raises(ConfigError, match="instruments must be a mapping"):
        PricebtSession(market=Provider(), stack=STACK, instruments=instruments)


def test_two_keys_that_normalise_to_the_same_one_are_refused_instead_of_one_winning_silently():
    with pytest.raises(ConfigError, match="swap:USD.*twice|twice.*swap:USD"):
        PricebtSession(market=Provider(), stack=STACK, instruments={"swap:usd": RAW, "SWAP:USD": "usd_ois_b"})


def test_the_lookup_normalises_the_asset_class_as_well_as_the_currency():
    s = PricebtSession(market=Provider(), stack=STACK)
    assert s.instrument_spec("SWAP", "usd").name == "usd_ois"
    with pytest.raises(NotSupportedError, match="swap:EUR"):
        s.instrument_spec("Swap", "eur")


# ------------------------------------------------------------------ M4: the gs values a stack accepts follow the spec actually in force
def test_without_a_session_a_gs_field_is_refused_saying_no_session_knows_what_it_accepts():
    with pytest.raises(NotSupportedError, match="floating_rate_option") as ei:
        IRSwap("Pay", "10y", "USD", floating_rate_option="USD-SOFR-COMPOUND")
    assert "no session" in str(ei.value) and "registered instrument spec" not in str(ei.value), "not 'the registered spec defines it': nothing is registered yet"


def test_the_stacks_accepted_values_apply_to_the_spec_it_ships_but_not_to_a_raw_spec_the_session_substitutes():
    PricebtSession.use(market=Provider(), stack=STACK)
    IRSwap("Pay", "10y", "USD", floating_rate_option="USD-SOFR-COMPOUND")
    PricebtSession.use(market=Provider(), stack=STACK, instruments={"swap:USD": "usd_ois_b"})
    IRSwap("Pay", "10y", "USD", floating_rate_option="USD-SOFR-COMPOUND")  # a spec the stack ships: the stack vouches for its own specs
    PricebtSession.use(market=Provider(), stack=STACK, instruments={"swap:USD": RAW})
    with pytest.raises(NotSupportedError, match="floating_rate_option"):
        IRSwap("Pay", "10y", "USD", floating_rate_option="USD-SOFR-COMPOUND")
    spec = PricebtSession(market=Provider(), stack=STACK, instruments={"swap:USD": RAW}).instrument_spec("swap", "USD")
    PricebtSession.use(market=Provider(), stack=STACK, instruments={"swap:USD": spec})  # the same for a ready-made InstrumentSpec
    with pytest.raises(NotSupportedError, match="floating_rate_option"):
        IRSwap("Pay", "10y", "USD", floating_rate_option="USD-SOFR-COMPOUND")
    IRSwap("Pay", "10y", "USD", fee=0)  # what needs no acceptance stays free


# ------------------------------------------------------------------ LOW: a derived template's gs attributes follow its terms
def test_with_terms_keeps_the_gs_attributes_in_step_so_clone_does_not_resurrect_the_old_notional():
    session()
    t = IRSwap("Pay", "10y", "USD", 1e7, name="a")
    t2 = t.with_terms(notional=5e6)
    assert t2.terms["notional"] == 5e6 and t2.notional_amount == 5e6 and t2.inputs["notional_amount"] == 5e6
    assert "notional_amount=5000000.0" in repr(t2)
    assert t2.clone(name="c").terms["notional"] == 5e6 and t2.clone().notional_amount == 5e6
    assert t.notional_amount == 1e7 and t.inputs["notional_amount"] == 1e7 and t.terms["notional"] == 1e7, "the original is untouched"


CHANGES = [
    dict(notional=2e6), dict(side="receive"), dict(side="receive", notional=3e6), dict(maturity="5y"), dict(maturity=dt.date(2034, 1, 2)), dict(effective="1y"),
    dict(fixed_rate=4.0), dict(fixed_rate=-0.5), dict(fixed_rate="par"), dict(fixed_rate="par", extras={"par_spread_bp": 25.0}), dict(fixed_rate="par", extras={"par_spread_bp": -0.5}),
]


def trade_terms(t):
    """The terms that define the trade: a spread over par (`par_spread_bp`) means nothing next to a numeric rate, and a gs argument cannot carry it."""
    return {k: v for k, v in t.terms.items() if not (k == "extras" and t.terms["fixed_rate"] != "par")}


@pytest.mark.parametrize("start", [dict(), dict(fixed_rate=0.05, effective_date="1y"), dict(fixed_rate="ATMF+10", pay_or_receive="Receive", notional_amount=-2e6)],
                         ids=["par", "fixed", "offset-receiver"])
@pytest.mark.parametrize("change", CHANGES, ids=[str(sorted(c)) + "-" + str(i) for i, c in enumerate(CHANGES)])
def test_cloning_a_template_changed_by_with_terms_rebuilds_the_same_terms(start, change):
    session()
    t = IRSwap(**{"pay_or_receive": "Pay", "termination_date": "10y", "notional_currency": "USD", "name": "a", **start})
    t2 = t.with_terms(**change)
    assert type(t2) is InstrumentTemplate
    c = t2.clone()
    assert trade_terms(c) == trade_terms(t2) and c.notional_amount == t2.notional_amount and c.direction == t2.direction
    assert trade_terms(c.clone(name="z")) == trade_terms(t2)


def test_the_gs_arguments_after_with_terms_read_as_the_user_would_write_them():
    session()
    t = IRSwap("Pay", "10y", "USD", 1e7, fixed_rate=0.05)
    assert t.with_terms(fixed_rate="par").inputs["fixed_rate"] == "ATMF"
    assert t.with_terms(fixed_rate="par", extras={"par_spread_bp": 25.0}).inputs["fixed_rate"] == "ATMF+25"
    assert t.with_terms(fixed_rate="par", extras={"par_spread_bp": -0.5}).inputs["fixed_rate"] == "ATMF-0.5"
    assert t.with_terms(fixed_rate=4.0).inputs["fixed_rate"] == pytest.approx(0.04)
    r = t.with_terms(side="receive", notional=2e6)
    assert (r.inputs["pay_or_receive"], r.inputs["notional_amount"]) == (PayReceive.Receive, 2e6)
    assert t.with_terms(notional=1e7).inputs == t.inputs, "an unchanged term leaves the argument the user wrote as it was"
    assert t.renamed("z").inputs == t.inputs and t.with_terms(maturity="5y").inputs["fixed_rate"] == 0.05, "fixed_rate=0.05 is not rewritten as 5.000000000000001 / 100"


def test_the_shipped_swap_schema_is_read_once_not_on_every_constructor_call(monkeypatch):
    import pricebt.instrument as inst
    from pricebt.contracts.schema import SchemaRegistry

    calls = []
    real = SchemaRegistry.default
    monkeypatch.setattr(SchemaRegistry, "default", lambda: (calls.append(1), real())[1])
    inst._swap_schema.cache_clear()
    for _ in range(3):
        with pytest.raises(ConfigError, match="create a session first"):
            IRSwap("Pay", "10y", "USD")
    assert len(calls) == 1, "25 ms per constructor call would make a loop of templates unusable"
    inst._swap_schema.cache_clear()


def test_renamed_and_clone_with_changes_still_work_after_with_terms():
    session()
    t = IRSwap("Pay", "10y", "USD", 1e7, name="a").with_terms(maturity="5y")
    assert t.renamed("r").terms == t.terms and t.renamed("r").name == "r"
    assert t.clone(termination_date="7y").terms["maturity"] == "7y" and t.clone(notional_amount=1e6).terms["notional"] == 1e6


# ------------------------------------------------------------------ LOW: session stack discipline
def test_closing_sessions_out_of_order_never_makes_a_closed_session_current_again():
    s1 = PricebtSession.use(market=Provider(), stack=STACK)
    s2 = PricebtSession.use(market=Provider(), stack=STACK)
    s1.close()
    s2.close()
    assert current_session() is None and not s1._active and not s2._active


def test_the_first_open_session_below_the_closed_ones_is_restored():
    s0 = PricebtSession.use(market=Provider(), stack=STACK)
    s1 = PricebtSession.use(market=Provider(), stack=STACK)
    s2 = PricebtSession.use(market=Provider(), stack=STACK)
    s1.close()
    assert current_session() is s2
    s2.close()
    assert current_session() is s0
    s0.close()
    assert current_session() is None


def test_the_context_managers_still_nest_and_a_closed_session_can_be_used_again():
    with PricebtSession.use(market=Provider(), stack=STACK) as outer:
        with PricebtSession.use(market=Provider(), stack=STACK) as inner:
            assert current_session() is inner
        assert current_session() is outer
    assert current_session() is None
    with outer:
        assert current_session() is outer
    assert current_session() is None


# ------------------------------------------------------------------ LOW: calendar names, reporting currency, holiday iterables, the documented stack form
def test_a_registered_calendar_name_is_found_however_its_hyphens_and_case_are_written(monkeypatch):
    monkeypatch.setattr(CALENDARS, "_items", dict(CALENDARS._items))
    CALENDARS.register("us-hol-test", lambda: [dt.date(2024, 7, 4)])
    for name in ("us-hol-test", "us_hol_test", "US-Hol-Test"):
        assert not resolve_calendar(name).is_business_day(dt.date(2024, 7, 4)), name
    with pytest.raises(ConfigError, match="unknown calendar"):
        resolve_calendar("us-hol-nope")


def test_the_reporting_currency_is_a_three_letter_code_and_none_means_the_default():
    assert PricebtSession(market=Provider(), reporting_currency=None).reporting_currency == "USD"
    assert PricebtSession(market=Provider(), reporting_currency="eur").reporting_currency == "EUR"
    assert PricebtSession(market=Provider(), reporting_currency=Currency.EUR).reporting_currency == "EUR"

    class Ccy(enum.Enum):  # another library's enum: its `str()` is 'Ccy.EUR', its value is the code
        EUR = "EUR"

    assert PricebtSession(market=Provider(), reporting_currency=Ccy.EUR).reporting_currency == "EUR"
    for bad in ("", "US", "USDD", 5, "U$D"):
        with pytest.raises(ConfigError, match="reporting_currency"):
            PricebtSession(market=Provider(), reporting_currency=bad)


@pytest.mark.parametrize("make", [lambda h: pd.DatetimeIndex(h), lambda h: np.array(h), lambda h: tuple(h), lambda h: set(h), lambda h: pd.Series(h)],
                         ids=["DatetimeIndex", "ndarray", "tuple", "set", "Series"])
def test_time_context_takes_any_iterable_of_extra_holidays(make):
    s = PricebtSession(market=Provider(), stack=STACK)
    hol = [dt.date(2024, 7, 4), dt.date(2024, 7, 5)]
    cal = s.time_context(make(hol)).calendar
    assert not cal.is_business_day(dt.date(2024, 7, 4)) and not cal.is_business_day(dt.date(2024, 7, 5)) and cal.is_business_day(dt.date(2024, 7, 8))
    assert s.time_context(pd.DatetimeIndex([])).calendar is s.calendar and s.time_context(None).calendar is s.calendar and s.time_context([]).calendar is s.calendar


def test_the_stack_forms_the_session_documents_are_forms_that_work():
    shown = re.findall(r"stack=\"([^\"]+)\"|stack='([^']+)'|\('([\w.]+:[\w.]+)'\)", (session_module.__doc__ or "") + (PricebtSession.__init__.__doc__ or ""))
    paths = [p for group in shown for p in group if p]
    assert all(p.split(":")[0].split(".")[0] == "pricebt" for p in paths), f"a documented dotted stack path is refused by the allow-list unless it is under pricebt: {paths}"
    s = PricebtSession(market=Provider(), stack="pricebt.testing.refstack:STACK")
    assert isinstance(s.stack, Stack)
    doc = (session_module.__doc__ or "") + (PricebtSession.__init__.__doc__ or "")
    assert "pkg.adapter" not in doc


def test_the_facade_docstrings_name_no_library_and_no_unsupported_instrument():
    import pricebt.backtests as backtests
    import pricebt.backtests.actions as actions
    import pricebt.instrument as instrument

    for mod in (backtests, actions, instrument, session_module):
        doc = (mod.__doc__ or "").lower()
        assert not any(w in doc for w in ("rateslib", "quantlib", "calendar='nyc'", "contrib.stores")), mod.__name__
    assert "IRSwaption" not in (backtests.__doc__ or "") + (actions.__doc__ or ""), "swaptions are not supported: the docstrings do not list them as instruments to build with"
