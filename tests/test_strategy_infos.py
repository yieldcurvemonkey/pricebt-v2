import pytest

from pricebt.errors import TriggerError
from pricebt.strategy import (AddScaledTradeAction, AddTradeAction, AddTradeActionInfo, ExitTradeActionInfo, HedgeAction, ScheduleInfo, TriggerInfo, NO_SCHEDULE)
from pricebt.strategy.infos import match_info, merge_infos, overlay

pytestmark = pytest.mark.core


def test_triggerinfo_bool_and_equality():
    assert TriggerInfo(True) == True  # noqa: E712
    assert TriggerInfo(True) == TriggerInfo(True)
    assert TriggerInfo(True) != TriggerInfo(False)
    assert not TriggerInfo(False) and TriggerInfo(True)
    assert TriggerInfo(True, {"add_trade": AddTradeActionInfo(scaling=-1)}) != TriggerInfo(True)


def test_gs_class_keys_are_normalised_and_lookup_by_class_works():
    ti = TriggerInfo(True, {AddTradeAction: AddTradeActionInfo(scaling=-1)})
    assert list(ti.info) == ["add_trade"]
    assert ti.info[AddTradeAction].scaling == -1  # gs test idiom: info_dict[AddTradeAction].scaling
    assert AddTradeAction in ti.info_dict
    assert ti.info.get(HedgeAction, "none") == "none"


def test_info_values_must_be_action_infos_and_both_forms_rejected():
    with pytest.raises(TriggerError):
        TriggerInfo(True, {"add_trade": 3})
    with pytest.raises(TriggerError):
        TriggerInfo(True, {}, info_dict={})


def test_specific_key_gets_defaults_filled_from_wildcard():
    """B1 of the design review: Periodic ('*': next_schedule) + MeanReversion ('add_trade': scaling) must give the action BOTH fields."""
    sched = TriggerInfo(True, {"*": ScheduleInfo(next_schedule="T")})
    mr = TriggerInfo(True, {"add_trade": AddTradeActionInfo(scaling=-1)})
    merged = merge_infos(sched.info, mr.info)
    eff = match_info(merged, "A", "add_trade")
    assert eff.scaling == -1 and eff.next_schedule == "T"


def test_wildcard_alone_and_specific_alone_pass_through_unchanged():
    w = ScheduleInfo(next_schedule="T")
    s = AddTradeActionInfo(scaling=2)
    assert match_info({"*": w}, "A", "add_trade") is w
    assert match_info({"add_trade": s}, "A", "add_trade") is s
    assert match_info({}, "A", "add_trade") is None


def test_name_beats_kind_and_parent_kinds_and_entry_key():
    a, b = AddTradeActionInfo(scaling=1), AddTradeActionInfo(scaling=2)
    assert match_info({"add_trade": a, "Steepen": b}, "Steepen", "add_trade") is b
    assert match_info({"add_scaled_trade": a}, "x", "early_exit_scaled", ("add_scaled_trade",)) is a
    assert match_info({"entry": a}, "x", "add_scaled_trade") is a
    assert match_info({"entry": a}, "x", "exit") is None


def test_next_schedule_none_is_distinct_from_no_schedule():
    assert ScheduleInfo().next_schedule is NO_SCHEDULE
    assert ScheduleInfo(next_schedule=None).next_schedule is None
    filled = overlay(ScheduleInfo(next_schedule=None), AddTradeActionInfo())
    assert filled.next_schedule is None  # "last date: hold to the end" survives the overlay


def test_merge_shared_key_fieldwise_reasons_join_skip_is_and():
    a = AddTradeActionInfo(scaling=1.0, reason="a", data={"x": 1})
    b = AddTradeActionInfo(next_schedule="T", reason="b", data={"y": 2}, skip=True)
    m = merge_infos({"add_trade": a}, {"add_trade": b})["add_trade"]
    assert (m.scaling, m.next_schedule, m.reason, dict(m.data), m.skip) == (1.0, "T", "a; b", {"x": 1, "y": 2}, False)
    assert merge_infos({"exit": ExitTradeActionInfo()}, {"add_trade": a}).keys() == {"exit", "add_trade"}


def test_scaled_action_reads_scaling_from_scaled_info():
    from pricebt.strategy import AddScaledTradeActionInfo

    assert AddScaledTradeActionInfo(scaling=3).scaling == 3 and AddScaledTradeActionInfo().next_schedule is NO_SCHEDULE
    assert AddScaledTradeAction.kind == "add_scaled_trade"
