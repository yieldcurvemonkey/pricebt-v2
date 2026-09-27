"""Runner ordering, registries, gs positional field order, config-style flat constructors."""
import dataclasses

import pandas as pd
import pytest

from pricebt.errors import RegistryError, TriggerError
from pricebt.registries import ACTIONS, SIGNALS, TRIGGERS
from pricebt.strategy import (AddTradeAction, AddTradeActionInfo, CustomAction, CustomTrigger, IntradayTriggerRequirements, PeriodicTrigger, ScheduleInfo, Strategy,
                              TriggerInfo)
from pricebt.strategy import requirements as R
from pricebt.strategy.triggers import Trigger
from pricebt.types import Phase
from test_strategy_support import D, FakeView, fwd, mk_ctx

pytestmark = pytest.mark.core


def recorder(log, tag, phase):
    return CustomAction(lambda ts, view, info, log=log, tag=tag: log.append(tag) or [], name=tag, phase=phase)


def always(calc):
    return CustomTrigger(target=lambda ts, view: True, calc=calc)


def order_of(mode, calcs):
    log = []
    t0, t1 = always(calcs[0]), always(calcs[1])
    t0.actions = (recorder(log, "add0", Phase.ADD), recorder(log, "hedge0", Phase.HEDGE), recorder(log, "exit0", Phase.EXIT), recorder(log, "adj0", Phase.ADJUST))
    t1.actions = (recorder(log, "add1", Phase.ADD), recorder(log, "exit1", Phase.EXIT), recorder(log, "hedge1", Phase.HEDGE), recorder(log, "adj1", Phase.ADJUST))
    run = Strategy(None, [t0, t1], eval_mode=mode).start(mk_ctx("2024-01-02", "2024-02-29"))
    report = run.step(D("2024-01-03"), FakeView(), lambda o: None)
    return log, report


def test_snapshot_mode_orders_actions_by_phase_then_trigger_then_action_index():
    log, report = order_of("snapshot", ("simple", "simple"))
    assert log == ["exit0", "exit1", "add0", "add1", "adj0", "adj1", "hedge0", "hedge1"]  # EXIT < ADD < ADJUST < HEDGE
    assert [f[0] for f in report.fired] == [0, 1] and report.n_instructions == 0


def test_staged_mode_evaluates_path_dependent_triggers_in_place_after_the_earlier_slots():
    log, _ = order_of("staged", ("simple", "path_dependent"))
    # stage-0 t0: EXIT, ADD, then (list order) t0's ADJUST, then t1 (stage 1) runs ALL its actions in list order, then t0's HEDGE
    assert log == ["exit0", "add0", "adj0", "add1", "exit1", "hedge1", "adj1", "hedge0"]
    both0, _ = order_of("staged", ("simple", "simple"))
    assert both0 == order_of("snapshot", ("simple", "simple"))[0]  # identical when nothing is path dependent


def test_step_submits_each_order_before_the_next_action_applies():
    seen = []

    def first(ts, view, info):
        from pricebt.orders import CloseOrder

        return [CloseOrder()]

    def second(ts, view, info):
        seen.append(len(submitted))
        return []

    submitted = []
    t = always("simple")
    t.actions = (CustomAction(first, name="a"), CustomAction(second, name="b"))
    Strategy(None, t).start(mk_ctx("2024-01-02", "2024-02-29")).step(D("2024-01-03"), FakeView(), submitted.append)
    assert seen == [1]  # the later action (and later stage-1 triggers, via include_pending) sees the earlier order


def test_unmatched_user_info_key_raises_but_builtin_emissions_are_lenient():
    strat = Strategy(None, CustomTrigger(target=lambda ts, view: TriggerInfo(True, {"typo_action": AddTradeActionInfo()}), actions=AddTradeAction(fwd(), name="A")))
    with pytest.raises(TriggerError) as e:
        strat.start(mk_ctx("2024-01-02", "2024-02-29")).step(D("2024-01-03"), FakeView(), lambda o: None)
    assert "typo_action" in str(e.value) and "add_trade" in str(e.value)  # the message lists the valid keys
    lenient = Strategy(None, CustomTrigger(target=lambda ts, view: TriggerInfo(True, {"typo_action": AddTradeActionInfo()}, strict=False), actions=AddTradeAction(fwd(), name="A")))
    lenient.start(mk_ctx("2024-01-02", "2024-02-29")).step(D("2024-01-03"), FakeView(), lambda o: None)


def test_info_key_by_action_name_reaches_only_that_action():
    got = {}

    def act(tag):
        return CustomAction(lambda ts, view, info, tag=tag: got.__setitem__(tag, info) or [], name=tag)

    t = CustomTrigger(target=lambda ts, view: TriggerInfo(True, {"second": AddTradeActionInfo(scaling=-1.0), "*": ScheduleInfo(next_schedule=None)}))
    t.actions = (act("first"), act("second"))
    Strategy(None, t).start(mk_ctx("2024-01-02", "2024-02-29")).step(D("2024-01-03"), FakeView(), lambda o: None)
    assert isinstance(got["first"], ScheduleInfo) and got["second"].scaling == -1.0 and got["second"].next_schedule is None  # wildcard fills the named info


# ------------------------------------------------------------------ registries and flat construction
def test_trigger_registry_names_and_class_name_resolution():
    for name in ("periodic", "intraday_periodic", "date", "mkt", "crossing", "strategy_risk", "portfolio", "trade_count", "aggregate", "not", "mean_reversion", "event",
                 "orders_generator", "custom", "risk_band", "rebalance_trigger"):
        assert TRIGGERS.resolve(name) is not None, name
    assert TRIGGERS.resolve("PeriodicTrigger") is TRIGGERS.resolve("periodic") is PeriodicTrigger
    assert TRIGGERS.resolve("periodic_trigger") is PeriodicTrigger
    assert TRIGGERS.resolve("periodic_requirements") is R.PeriodicTriggerRequirements
    assert TRIGGERS.resolve("RiskBandTrigger").requirements_cls is R.RiskBandTriggerRequirements
    with pytest.raises(RegistryError):
        TRIGGERS.resolve("no_such_trigger")


def test_action_and_signal_registry_names():
    for name in ("add_trade", "add_scaled_trade", "add_weighted_trade", "hedge", "exit_trade", "exit_all", "rebalance", "early_exit_scaled", "custom", "exit", "exit_all_positions"):
        assert ACTIONS.resolve(name) is not None, name
    assert ACTIONS.resolve("AddTradeAction").kind == "add_trade" and ACTIONS.resolve("exit_all") is ACTIONS.resolve("ExitAllPositionsAction")
    for name in ("series", "pricer", "measure", "derived", "constant", "step_table"):
        assert SIGNALS.resolve(name) is not None, name


def test_flat_config_style_construction_equals_nested_gs_form():
    t = fwd()  # a template compares by identity: both forms take the same one
    flat = TRIGGERS.resolve("periodic")(frequency="1w", start_date="2024-01-02", actions=[ACTIONS.resolve("add_trade")(priceables=t, trade_duration="1w", name="A")])
    nested = PeriodicTrigger(R.PeriodicTriggerRequirements("2024-01-02", None, "1w"), AddTradeAction(t, "1w", "A"))
    assert flat == nested
    with pytest.raises(Exception):
        PeriodicTrigger(R.PeriodicTriggerRequirements(None, None, "1w"), frequency="1w")  # both forms at once
    with pytest.raises(Exception):
        Trigger(frequency="1w")
    req_cls = TRIGGERS.resolve("mkt_requirements")
    assert req_cls is R.MktTriggerRequirements


def test_every_registered_trigger_builds_from_keywords_and_a_strategy_accepts_it():
    from pricebt.strategy import SeriesSource

    src = SeriesSource(pd.Series({pd.Timestamp("2024-01-02").date(): 1.0}), name="s")
    kw = {
        "periodic": {"frequency": "1w"}, "intraday_periodic": {"frequency": 30}, "date": {"dates": ["2024-01-05"]}, "mkt": {"data_source": src, "trigger_level": 1, "direction": "above"},
        "crossing": {"data_source": src, "trigger_level": 1, "direction": "any"}, "strategy_risk": {"risk": "dv01", "trigger_level": 0, "direction": "ABOVE"},
        "portfolio": {"trigger_level": 0, "direction": "equal"}, "trade_count": {"trade_count": 3, "direction": "below"},
        "mean_reversion": {"data_source": src, "z_score_bound": 2, "rolling_mean_window": 5, "rolling_std_window": 5}, "event": {"event_name": "x", "events": {"x": ["2024-01-09"]}},
        "custom": {"target": lambda ts, view: False}, "risk_band": {"measure": "dv01", "band": 5.0}, "rebalance_trigger": {"measure": "dv01", "band": {"abs": 5.0}},
    }
    for name, args in kw.items():
        t = TRIGGERS.resolve(name)(actions=[AddTradeAction(fwd(), name=f"a_{name}")], **args)
        Strategy(None, t).start(mk_ctx("2024-01-02", "2024-02-29")).timeline_extras()
    agg = TRIGGERS.resolve("aggregate")(triggers=[R.MktTriggerRequirements(src, 1, R.TriggerDirection.ABOVE)], aggregate_type="ANY_OF")
    assert agg.trigger_requirements.aggregate_type is R.AggType.ANY_OF
    assert TRIGGERS.resolve("not")(trigger=R.PeriodicTriggerRequirements(None, None, "1w")).trigger_requirements.trigger.frequency == "1w"


# ------------------------------------------------------------------ gs positional field order
def positional(cls):
    return [f.name for f in dataclasses.fields(cls) if f.init and not f.kw_only]


@pytest.mark.parametrize("cls, expected", [
    (R.PeriodicTriggerRequirements, ["start_date", "end_date", "frequency", "calendar"]),
    (R.IntradayTriggerRequirements, ["start_time", "end_time", "frequency"]),
    (R.DateTriggerRequirements, ["dates", "entire_day"]),
    (R.MktTriggerRequirements, ["data_source", "trigger_level", "direction"]),
    (R.StrategyRiskTriggerRequirements, ["risk", "trigger_level", "direction", "risk_transformation"]),
    (R.PortfolioTriggerRequirements, ["data_source", "trigger_level", "direction"]),
    (R.TradeCountTriggerRequirements, ["trade_count", "direction"]),
    (R.AggregateTriggerRequirements, ["triggers", "aggregate_type"]),
    (R.NotTriggerRequirements, ["trigger"]),
    (R.MeanReversionTriggerRequirements, ["data_source", "z_score_bound", "rolling_mean_window", "rolling_std_window"]),
    (R.EventTriggerRequirements, ["event_name", "offset_days", "country", "currency", "source", "start", "end", "data_source"]),
    (R.RiskBandTriggerRequirements, ["measure", "band", "target", "scope"]),
])
def test_requirements_positional_field_order_is_gs_quant_order(cls, expected):
    assert positional(cls) == expected


def test_action_positional_field_order_is_gs_quant_order():
    from pricebt import strategy as S

    assert positional(S.AddTradeAction) == ["priceables", "trade_duration", "name", "transaction_cost", "transaction_cost_exit", "holiday_calendar"]
    assert positional(S.AddScaledTradeAction) == ["priceables", "trade_duration", "name", "scaling_type", "scaling_risk", "scaling_level", "transaction_cost",
                                                  "transaction_cost_exit", "holiday_calendar"]
    assert positional(S.AddWeightedTradeAction) == ["priceables", "trade_duration", "name", "scaling_risk", "total_size", "transaction_cost", "transaction_cost_exit", "holiday_calendar"]
    assert positional(S.HedgeAction) == ["risk", "priceables", "trade_duration", "name", "csa_term", "scaling_parameter", "transaction_cost", "transaction_cost_exit",
                                         "risk_transformation", "holiday_calendar", "risk_percentage"]
    assert positional(S.ExitTradeAction) == ["priceable_names", "name", "transaction_cost"]
    assert positional(S.RebalanceAction)[:3] == ["priceable", "size_parameter", "method"]


def test_intraday_requirements_alias_names():
    assert IntradayTriggerRequirements.kind == "intraday_periodic"
    from pricebt.strategy import IntradayPeriodicTriggerRequirements

    assert IntradayPeriodicTriggerRequirements is IntradayTriggerRequirements
