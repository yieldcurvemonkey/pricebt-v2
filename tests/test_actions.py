"""pricebt.backtests.actions (IMPLEMENTATION_PLAN.md P3.3; DESIGN.md section 9.3).

Field order/defaults are cross-checked against the committed tests/data/gs_api_1_5_4.json snapshot
(the P1.6 tool's introspection of the real gs_quant 1.5.4 dataclasses) rather than hand-transcribed,
so a typo here can't quietly agree with a typo there. `AddScaledTradeAction.dated_priceables` and
the whole of `AddWeightedTradeAction`/`EarlyExitPositionLimitScaledAction` are 2.1.17 additions
(DESIGN.md decision 0.1) absent from that 1.5.4 snapshot, so they are checked against the real
gs_quant 2.1.17 source instead (research/02 section 2.3), not the JSON.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import json
from pathlib import Path

import pytest

import pricebt.backtests.actions as actions_module
from pricebt.backtests.actions import (
    AddScaledTradeAction,
    AddTradeAction,
    AddWeightedTradeAction,
    EarlyExitPositionLimitScaledAction,
    EnterPositionQuantityScaledAction,
    ExitAllPositionsAction,
    ExitPositionAction,
    ExitTradeAction,
    HedgeAction,
    RebalanceAction,
)
from pricebt.backtests.backtest_objects import ConstantTransactionModel
from pricebt.backtests.backtest_utils import CalcType
from pricebt.common import RiskMeasure
from pricebt.instrument import IRSwap
from pricebt.markets.portfolio import Portfolio

ROOT = Path(__file__).resolve().parents[1]
API_JSON = ROOT / "tests" / "data" / "gs_api_1_5_4.json"
_SNAPSHOT = json.loads(API_JSON.read_text(encoding="utf8"))["symbols"]


def _snapshot_field_names(class_name: str) -> list:
    return [name for name, _init, _default_repr in _SNAPSHOT[f"gs_quant.backtests.actions.{class_name}"]["fields"]]


def _own_field_names(cls) -> list:
    return [f.name for f in dataclasses.fields(cls)]


# `tools/gs_api_snapshot.py` (the tool that produced tests/data/gs_api_1_5_4.json) has these same
# two helpers, but importing that module imports gs_quant at module level -- forbidden for a test
# (DESIGN.md decision 0.1: "No pricebt module and no test imports gs_quant"). Reimplemented here,
# not imported, for that reason.
def _default_repr(default) -> str:
    if default is inspect.Signature.empty or default is dataclasses.MISSING:
        return "NODEFAULT"
    return repr(default)


def _field_default_repr(f: dataclasses.Field) -> str:
    if f.default_factory is not dataclasses.MISSING:
        return "FACTORY"
    return _default_repr(f.default)


def _snapshot_fields(class_name: str) -> list:
    """(name, init, default_repr) triples, exactly as the snapshot stored them -- order, the
    static/plain (`init`) flag, and the default all in one comparable value."""
    return [tuple(x) for x in _SNAPSHOT[f"gs_quant.backtests.actions.{class_name}"]["fields"]]


def _own_fields(cls) -> list:
    return [(f.name, f.init, _field_default_repr(f)) for f in dataclasses.fields(cls)]


def _snapshot_signature(class_name: str) -> list:
    """(name, kind, default_repr) triples for the real __init__ signature -- `init=False` (static)
    fields are correctly absent, exactly as inspect.signature() also omits them."""
    return [tuple(x) for x in _SNAPSHOT[f"gs_quant.backtests.actions.{class_name}"]["signature"]]


def _own_signature(cls) -> list:
    return [(p.name, str(p.kind), repr(p.default)) for p in inspect.signature(cls).parameters.values()]


def _risk_measure() -> RiskMeasure:
    return RiskMeasure(name="IRDelta")


def _resolved_swap(**kw) -> IRSwap:
    """A swap that satisfies RebalanceAction's `priceable.unresolved is None` -> raise guard."""
    swap = IRSwap(**kw)
    swap._set_resolution(dict(kw), None, None, swap)
    return swap


# --------------------------------------------------------------------------------- field order/defaults
# research/02 section 2.3; cross-checked against the real gs_api_1_5_4.json snapshot, not
# hand-transcribed.


_SNAPSHOTTED_ACTIONS = [
    (AddTradeAction, "AddTradeAction"),
    (EnterPositionQuantityScaledAction, "EnterPositionQuantityScaledAction"),
    (ExitPositionAction, "ExitPositionAction"),
    (ExitTradeAction, "ExitTradeAction"),
    (ExitAllPositionsAction, "ExitAllPositionsAction"),
    (HedgeAction, "HedgeAction"),
    (RebalanceAction, "RebalanceAction"),
]


@pytest.mark.parametrize("cls,class_name", _SNAPSHOTTED_ACTIONS)
def test_fields_match_gs_snapshot_exactly(cls, class_name):
    """Order, the static-vs-plain (`init`) flag, and the default -- all three, for every field,
    including `class_type` (which `test_signature_matches_gs_snapshot_exactly` below can't see,
    since a static/`init=False` field is never part of the constructor signature)."""
    assert _own_fields(cls) == _snapshot_fields(class_name)


@pytest.mark.parametrize("cls,class_name", _SNAPSHOTTED_ACTIONS)
def test_signature_matches_gs_snapshot_exactly(cls, class_name):
    """The real `__init__` signature: name, POSITIONAL_OR_KEYWORD/KEYWORD_ONLY kind (hence
    positional order), and default repr -- including the `default_factory` sentinel repr'ing as
    the literal string `<factory>` and an Enum default repr'ing per pricebt.base.EnumBase (bare
    value) or plain enum.Enum (`<Class.member: 'value'>`), whichever the real field uses."""
    assert _own_signature(cls) == _snapshot_signature(class_name)


def test_add_scaled_trade_action_field_order_matches_1_5_4_plus_the_2_1_17_dated_priceables_field():
    """dated_priceables sits between holiday_calendar and class_type (2.1.17 addition, research/02
    section 9's delta table); every other field/position is exactly the 1.5.4 snapshot's."""
    own = _own_field_names(AddScaledTradeAction)
    snapshot = _snapshot_field_names("AddScaledTradeAction")
    assert own == snapshot[:-1] + ["dated_priceables"] + snapshot[-1:]


def test_add_scaled_trade_action_field_defaults_match_1_5_4_plus_the_2_1_17_dated_priceables_field():
    """Same full (name, init, default_repr) triples that test_fields_match_gs_snapshot_exactly checks
    for the other 7 snapshotted classes -- order, `init` flag AND default, not just names -- with the
    2.1.17-only `dated_priceables` field (default `None`, a plain/init=True field) spliced in between
    holiday_calendar and class_type. Catches a mutated default on scaling_type/scaling_risk/
    scaling_level/transaction_cost_exit/holiday_calendar/class_type that the names-only test above
    can't."""
    own = _own_fields(AddScaledTradeAction)
    snapshot = _snapshot_fields("AddScaledTradeAction")
    assert own == snapshot[:-1] + [("dated_priceables", True, "None")] + snapshot[-1:]


def test_early_exit_position_limit_scaled_action_extends_add_scaled_trade_action():
    """2.1.17 addition (no 1.5.4 snapshot entry): early_exits/max_concurrent_pos appended after
    AddScaledTradeAction's own fields, class_type overridden last (research/02 section 9)."""
    assert _own_field_names(EarlyExitPositionLimitScaledAction) == _own_field_names(AddScaledTradeAction) + [
        "early_exits",
        "max_concurrent_pos",
    ]


def test_early_exit_position_limit_scaled_action_field_defaults_match_2_1_17_source():
    """Full (name, init, default_repr) triples, hand-derived from the real 2.1.17
    gs_quant/backtests/actions.py (no 1.5.4 snapshot entry exists for this class): every
    AddScaledTradeAction field keeps its own default (dataclass field redeclaration -- verified
    against actual Python dataclass behaviour -- overrides a field's VALUE in place, it does not
    move the field to the end), except class_type's own override; early_exits and
    max_concurrent_pos both default to None."""
    assert _own_fields(EarlyExitPositionLimitScaledAction) == [
        ("priceables", True, "None"),
        ("trade_duration", True, "None"),
        ("name", True, "None"),
        ("scaling_type", True, "<ScalingActionType.size: 'size'>"),
        ("scaling_risk", True, "None"),
        ("scaling_level", True, "1"),
        ("transaction_cost", True, "FACTORY"),
        ("transaction_cost_exit", True, "None"),
        ("holiday_calendar", True, "None"),
        ("dated_priceables", True, "None"),
        ("class_type", False, "'early_exit_position_limit_scaled_action'"),
        ("early_exits", True, "None"),
        ("max_concurrent_pos", True, "None"),
    ]


def test_add_weighted_trade_action_fields_match_2_1_17_source():
    """2.1.17 addition (no 1.5.4 snapshot entry) -- field order per research/02 section 9's delta table."""
    assert _own_field_names(AddWeightedTradeAction) == [
        "priceables",
        "trade_duration",
        "name",
        "scaling_risk",
        "total_size",
        "transaction_cost",
        "transaction_cost_exit",
        "holiday_calendar",
        "class_type",
    ]


def test_add_weighted_trade_action_field_defaults_match_2_1_17_source():
    """Full (name, init, default_repr) triples, hand-derived from the real 2.1.17
    gs_quant/backtests/actions.py (no 1.5.4 snapshot entry exists for this class)."""
    assert _own_fields(AddWeightedTradeAction) == [
        ("priceables", True, "None"),
        ("trade_duration", True, "None"),
        ("name", True, "None"),
        ("scaling_risk", True, "None"),
        ("total_size", True, "100000.0"),
        ("transaction_cost", True, "FACTORY"),
        ("transaction_cost_exit", True, "None"),
        ("holiday_calendar", True, "None"),
        ("class_type", False, "'add_weighted_trade_action'"),
    ]


def test_transaction_cost_factory_default_actually_produces_a_zero_constant_model():
    """The snapshot tests above prove the `<factory>` sentinel matches gs's; this proves the real
    factory behind it (default_transaction_cost) produces the right object, not just a matching repr."""
    a = AddTradeAction(IRSwap())
    assert isinstance(a.transaction_cost, ConstantTransactionModel)
    assert a.transaction_cost.cost == 0


@pytest.mark.parametrize(
    "make,expected",
    [
        (lambda: AddTradeAction(IRSwap()), CalcType.simple),
        (lambda: AddScaledTradeAction(IRSwap()), CalcType.simple),
        (lambda: EnterPositionQuantityScaledAction(IRSwap()), CalcType.simple),
        (lambda: ExitTradeAction("x"), CalcType.simple),
        (lambda: ExitAllPositionsAction("x"), CalcType.path_dependent),
        (lambda: HedgeAction(_risk_measure(), IRSwap()), CalcType.semi_path_dependent),
        (lambda: AddWeightedTradeAction(IRSwap()), CalcType.semi_path_dependent),
        (
            lambda: RebalanceAction(_resolved_swap(number_of_options=1), "number_of_options", lambda *a: 1),
            CalcType.path_dependent,
        ),
    ],
)
def test_calc_type_per_action(make, expected):
    assert make().calc_type == expected


def test_exit_position_action_class_type_is_a_plain_field_not_static():
    """Unlike every other action's static (init=False) class_type, gs's ExitPositionAction default
    is a plain, overridable field (research/02 section 2.3; the exact `init` flag is already
    checked against the snapshot by test_fields_match_gs_snapshot_exactly above -- this proves the
    field is actually settable at construction, not just that its `init` flag says so)."""
    assert ExitPositionAction(class_type="whatever").class_type == "whatever"


# ------------------------------------------------------------------------------------------ naming: R1
# research/02 section 2.4's worked examples table. Each action is given an explicit `name=` so the
# expected priceable name doesn't depend on the process-global action_count counter.


def test_r1_named_priceable_gets_the_action_prefix():
    a = AddTradeAction(IRSwap(name="swap_10y"), name="Action1")
    assert a.priceables[0].name == "Action1_swap_10y"


def test_r1_unnamed_priceable_becomes_priceable_n():
    a = AddTradeAction(IRSwap(), name="Action2")
    assert a.priceables[0].name == "Action2_Priceable0"


def test_r1_priceable_already_prefixed_with_the_action_name_is_kept_unprefixed():
    a = AddTradeAction(IRSwap(name="Action1x"), name="Action1")
    assert a.priceables[0].name == "Action1x"


def test_r4_hedge_action_wraps_a_single_priceable_into_a_portfolio_named_after_it():
    inner = IRSwap(name="30yhedge")
    h = HedgeAction(_risk_measure(), inner, name="Action2")
    assert isinstance(h.priceables, Portfolio)
    assert h.priceables.name == "30yhedge"
    assert len(h.priceables) == 1
    assert h.priceables[0].name == "Action2_Priceable0"


def test_rebalance_action_construction_names_its_priceable_action_underscore_priceable_name():
    """RebalanceAction.__post_init__'s own construction-time rename -- research/02 section 2.4's
    rule table names this R7 for a *different*, later step (generic_engine.py:571-573's
    execution-time order rename, owned by P3.5); this one has no rule id of its own, it's just
    RebalanceAction's `__post_init__` body (research/02 section 3, ~line 179)."""
    swap = _resolved_swap(name="spx", number_of_options=10)
    r = RebalanceAction(swap, "number_of_options", lambda *a: 1, name="Rb")
    assert r.priceable.name == "Rb_spx"


# --------------------------------------------------------------------------------- DEV-E5: position_meta


def test_position_meta_set_by_add_trade_action():
    a = AddTradeAction(IRSwap(name="swap_10y"), name="Action1")
    assert a.priceables[0].position_meta == ("Action1", "swap_10y", None)


def test_position_meta_set_by_add_trade_action_on_an_unnamed_priceable():
    a = AddTradeAction(IRSwap(), name="Action2")
    assert a.priceables[0].position_meta == ("Action2", "Priceable0", None)


def test_position_meta_not_set_by_hedge_action_on_its_inner_priceable():
    # DEV-E5: hedges are never matched by name (DESIGN.md section 11), so a hedge leg keeps
    # position_meta as the Instrument.__init__ default of None -- if this set position_meta the way
    # other actions do, its third element (create date) would never be filled in (R4 embeds the date
    # into the wrapping portfolio's name instead, at execution time in P3.5), and any
    # ExitTradeAction(priceable_names=[...]) containing the generic 'Priceable0' name would crash
    # comparing None <= a date.
    h = HedgeAction(_risk_measure(), IRSwap(name="30yhedge"), name="Action2")
    assert h.priceables[0].position_meta is None


def test_position_meta_set_by_rebalance_action():
    swap = _resolved_swap(name="spx", number_of_options=10)
    r = RebalanceAction(swap, "number_of_options", lambda *a: 1, name="Rb")
    assert r.priceable.position_meta == ("Rb", "spx", None)


def test_position_meta_set_by_add_scaled_trade_action():
    a = AddScaledTradeAction(IRSwap(name="opt"), name="Action5")
    assert a.priceables[0].position_meta == ("Action5", "opt", None)


# ---------------------------------------------------------------------- transaction_cost_exit defaulting


def test_transaction_cost_exit_defaults_to_the_entry_model():
    a = AddTradeAction(IRSwap(), transaction_cost=ConstantTransactionModel(5))
    assert a.transaction_cost_exit is a.transaction_cost


def test_transaction_cost_exit_is_kept_when_given_explicitly():
    exit_model = ConstantTransactionModel(9)
    a = AddTradeAction(IRSwap(), transaction_cost=ConstantTransactionModel(5), transaction_cost_exit=exit_model)
    assert a.transaction_cost_exit is exit_model


def test_add_trade_action_null_transaction_cost_is_replaced_by_the_default():
    """AddTradeAction alone re-guards a None transaction_cost (research/02 section 2.3 notes
    AddScaledTradeAction does NOT null-guard it)."""
    a = AddTradeAction(IRSwap(), transaction_cost=None)
    assert isinstance(a.transaction_cost, ConstantTransactionModel)
    assert a.transaction_cost.cost == 0


# --------------------------------------------------------------------------------------------- DEV-E7


def test_hedge_action_none_priceables_raises_runtime_error():
    with pytest.raises(RuntimeError, match="hedge action only accepts one trade or one portfolio"):
        HedgeAction(_risk_measure(), None)


def test_hedge_action_non_priceable_raises_runtime_error():
    with pytest.raises(RuntimeError, match="hedge action only accepts one trade or one portfolio"):
        HedgeAction(_risk_measure(), "not a priceable")


def test_hedge_action_deprecation_warning_on_non_default_scaling_parameter():
    with pytest.warns(DeprecationWarning):
        HedgeAction(_risk_measure(), IRSwap(name="h"), scaling_parameter="something_else")


# -------------------------------------------------------------------------------------------- DEV-E10


def test_exit_trade_action_bare_string_becomes_a_one_element_list():
    assert ExitTradeAction("x").priceable_names == ["x"]


def test_exit_trade_action_iterable_of_names_is_kept_as_a_list():
    assert ExitTradeAction(["x", "y"]).priceable_names == ["x", "y"]


def test_exit_trade_action_none_stays_none():
    """Not normalised to `[]`: ExitTradeActionImpl.apply_action (P3.5) branches on
    `priceable_names is None` (exit every currently-held trade) vs. truthiness (exit only the
    named ones) -- collapsing `None` to the falsy-but-not-None `[]` would satisfy neither branch."""
    assert ExitTradeAction().priceable_names is None


def test_exit_all_positions_action_default_args_keeps_the_none_sentinel():
    """Regression: ExitAllPositionsAction() (no args, the documented "exit everything" usage)
    inherits ExitTradeAction.__post_init__ unmodified. Before this fix, the unconditional
    `make_list(None) == []` here made ExitTradeActionImpl.apply_action's `is None` check (line 440)
    and its truthiness check (line 453) both false, hitting an UnboundLocalError on
    `current_trade_names` the first time this ran through the engine (P3.5)."""
    assert ExitAllPositionsAction().priceable_names is None


# --------------------------------------------------------------------------------------------- DEV-T16


def test_list_holiday_calendar_is_normalised_to_a_tuple_on_every_action_that_has_the_field():
    hc = [dt.date(2024, 1, 1)]
    assert isinstance(AddTradeAction(IRSwap(), holiday_calendar=hc).holiday_calendar, tuple)
    assert isinstance(AddScaledTradeAction(IRSwap(), holiday_calendar=hc).holiday_calendar, tuple)
    assert isinstance(AddWeightedTradeAction(IRSwap(), holiday_calendar=hc).holiday_calendar, tuple)
    assert isinstance(HedgeAction(_risk_measure(), IRSwap(), holiday_calendar=hc).holiday_calendar, tuple)


def test_tuple_holiday_calendar_is_left_alone():
    hc = (dt.date(2024, 1, 1),)
    assert AddTradeAction(IRSwap(), holiday_calendar=hc).holiday_calendar == hc


# ----------------------------------------------------------------------------- global action_count counter

# Captured once, at module import (collection) time -- before any test in the whole session has run,
# and therefore before any test could have bumped it. The conftest.py isolation fixture (extended by
# this task) saves/restores this module global around every test; if it did not, a test running
# after one of the tests below would see whatever that earlier test left behind instead.
_ACTION_COUNT_AT_IMPORT = actions_module.action_count


def test_action_count_matches_the_pre_session_baseline_at_the_start_of_this_test():
    assert actions_module.action_count == _ACTION_COUNT_AT_IMPORT


def test_action_count_increments_once_per_auto_named_action():
    before = actions_module.action_count
    a1 = AddTradeAction(IRSwap())
    a2 = AddTradeAction(IRSwap())
    h = HedgeAction(_risk_measure(), IRSwap())
    assert [a1.name, a2.name, h.name] == [f"Action{before}", f"Action{before + 1}", f"Action{before + 2}"]
    assert actions_module.action_count == before + 3


def test_action_count_is_untouched_by_an_action_given_an_explicit_name():
    before = actions_module.action_count
    AddTradeAction(IRSwap(), name="ExplicitlyNamed")
    assert actions_module.action_count == before


def test_action_count_was_reset_between_this_test_and_the_incrementing_one_above():
    """If conftest.py's isolation fixture did not restore action_count after
    test_action_count_increments_once_per_auto_named_action, this would see `before + 3` instead."""
    assert actions_module.action_count == _ACTION_COUNT_AT_IMPORT
