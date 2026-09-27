from types import SimpleNamespace

import pandas as pd
import pytest

from pricebt.costs import ConstantCost, ScaledCost
from pricebt.errors import DurationError, NotSupportedError, SizingError, StrategyError
from pricebt.orders import CloseOrder, OpenOrder, PositionMeta, PositionSelector, ResizeOrder
from pricebt.contracts.spec import Built, TradeTemplate
from pricebt.strategy import (INHERIT, AddScaledTradeAction, AddTradeAction, AddTradeActionInfo, AddWeightedTradeAction, Bundle, CustomAction, EarlyExitPositionLimitScaledAction,
                              ExitAllPositionsAction, ExitTradeAction, ExitTradeActionInfo, HedgeAction, HedgeActionInfo, Leg, RebalanceAction, RebalanceActionInfo,
                              ScalingActionType, ScheduleInfo, SeriesSource, SignalStore, SubmitOrdersAction, SubmitOrdersInfo)
from pricebt.testing.toys import ToyForward, toy_template
from test_strategy_support import D, FakeView, NY, fwd, mk_ctx

pytestmark = pytest.mark.core

CTX = mk_ctx("2024-01-02", "2025-12-31")
T0 = D("2024-01-31")


def bind(action, name="A", ti=0, ai=0):
    action.bind(name=name, trigger_idx=ti, action_idx=ai)
    action.bind_ctx(CTX)
    return action


def view(**units):
    """FakeView with per-unit measures: view(dv01={"fwd": 0.1}, pv={"fwd": 4.0})."""
    u = {(t, m): v for m, d in units.items() for t, v in d.items()}
    return FakeView(now=T0, units=u)


def opens(orders):
    return [o for o in orders if isinstance(o, OpenOrder)]


# ------------------------------------------------------------------ AddTrade
def test_add_trade_builds_one_open_order_with_structured_metadata():
    a = bind(AddTradeAction(fwd(), "1m", quantity=3.0, tags=("core",), transaction_cost=ConstantCost(1.0)), name="Carry", ti=2, ai=1)
    (o,) = a.apply(T0, view(), None)
    assert isinstance(o, OpenOrder) and o.quantity == 3.0 and o.final_ts == pd.Timestamp("2024-02-29 17:00", tz=NY)
    assert o.tags == ("core",) and o.cost_entry == ConstantCost(1.0) and o.cost_exit is None
    m = o.meta
    assert (m.action, m.kind, m.trigger_idx, m.action_idx, m.template, m.tags) == ("Carry", "add", 2, 1, "fwd", ("core",))
    assert m.extra["name"] == "Carry_fwd_2024-01-31" and m.extra["action_kind"] == "add_trade" and m.extra["firing"] == f"{T0.value}:2:1"


def test_add_trade_scaling_from_info_offsets_and_zero_is_dropped():
    a = bind(AddTradeAction(fwd(), None, quantity=2.0))
    v = view()
    assert a.apply(T0, v, AddTradeActionInfo(scaling=-1.0))[0].quantity == -2.0  # the offsetting trade of mean reversion
    assert a.apply(T0, v, ScheduleInfo(next_schedule=None))[0].quantity == 2.0  # a schedule info has no scaling: defaults to 1
    assert a.apply(T0, v, AddTradeActionInfo(scaling=0.0)) == []
    assert v.events[-1][0] == "order_skipped" and v.events[-1][1]["reason"] == "zero_quantity"
    assert a.apply(T0, v, AddTradeActionInfo(skip=True)) == []


def test_add_trade_bundle_legs_signed_weights_share_firing_id():
    seagull = Bundle("Seagull", [Leg(fwd("o0"), -1.0), Leg(fwd("o1"), 1.0), Leg(fwd("o2"), -1.0)])
    a = bind(AddTradeAction(seagull, "4w", quantity=2.0))
    orders = a.apply(T0, view(), None)
    assert [o.quantity for o in orders] == [-2.0, 2.0, -2.0] and [o.meta.template for o in orders] == ["o0", "o1", "o2"]
    assert len({o.meta.extra["firing"] for o in orders}) == 1
    lst = bind(AddTradeAction([fwd("x"), fwd("y")], None))  # a plain list is an anonymous bundle of weight-1 legs
    assert [o.meta.template for o in lst.apply(T0, view(), None)] == ["x", "y"]


def test_add_trade_needs_a_template_a_raw_object_has_no_bindings():
    """A raw object is refused (B6: nothing is bound implicitly); TradeTemplate.wrap is the explicit way, and every position gets its own copy."""
    raw = ToyForward(strike=4.0, notional=2.0)
    with pytest.raises(StrategyError, match="TradeTemplate"):
        AddTradeAction(raw)
    (o,) = bind(AddTradeAction(toy_template(raw, name="wrapped"))).apply(T0, view(), None)
    built = o.template.build(None, T0).obj
    assert isinstance(o.template, TradeTemplate) and built.notional == 2.0 and built is not raw
    with pytest.raises(StrategyError):
        AddTradeAction("a_name_needs_the_config_resolver")


def test_add_trade_duration_errors_and_past_exit_policy():
    with pytest.raises(DurationError):
        bind(AddTradeAction(fwd(), "next schedule")).apply(T0, view(), None)  # no info: the trigger cannot supply a schedule
    (o,) = bind(AddTradeAction(fwd(), "next schedule")).apply(T0, view(), ScheduleInfo(next_schedule=D("2024-02-07")))
    assert o.final_ts == D("2024-02-07")
    past = bind(AddTradeAction(fwd(), D("2024-01-30")))
    with pytest.raises(DurationError):
        past.apply(T0, view(), None)
    v = view()
    assert bind(AddTradeAction(fwd(), D("2024-01-30"), on_past_exit="skip")).apply(T0, v, None) == []
    assert v.events[-1][1]["reason"] == "past_exit"


def test_add_trade_dated_priceables_replace_the_action_priceables_for_that_firing():
    a = bind(AddTradeAction(fwd("base"), None, dated_priceables={pd.Timestamp("2024-01-31").date(): fwd("dated")}))
    assert a.apply(T0, view(), None)[0].meta.template == "dated"
    assert a.apply(D("2024-02-01"), view(), None)[0].meta.template == "base"


def test_attribute_duration_builds_the_pricable_through_the_view():
    class V(FakeView):
        def build(self, template, *, request=None):  # the factory's RESOLVED terms are what a duration name refers to
            return Built(SimpleNamespace(), {"expiration_date": pd.Timestamp("2024-06-14")})

    (o,) = bind(AddTradeAction(fwd(), "expiration_date")).apply(T0, V(now=T0), None)
    assert o.final_ts == pd.Timestamp("2024-06-14 17:00", tz=NY)


# ------------------------------------------------------------------ AddScaled / AddWeighted
def test_scaled_size_scalar_stepdict_and_signal():
    v = view()
    assert bind(AddScaledTradeAction(fwd(), None, "s", ScalingActionType.size, None, 7)).apply(T0, v, None)[0].quantity == 7.0
    assert bind(AddScaledTradeAction(fwd(), None, scaling_level={T0.date(): 13.0}, quantity=2.0)).apply(T0, v, None)[0].quantity == 26.0
    assert bind(AddScaledTradeAction(fwd(), None, scaling_level={pd.Timestamp("2024-01-30").date(): 13.0})).apply(T0, v, None) == []  # 0 after the last key: no order
    src = SeriesSource(pd.Series({pd.Timestamp("2024-01-31").date(): 5.0}), "fill_forward", name="lvl")
    store = SignalStore([src])
    store.observe(T0, FakeView())
    sv = FakeView(now=T0, store=store)
    assert bind(AddScaledTradeAction(fwd(), None, scaling_level=src)).apply(T0, sv, None)[0].quantity == 5.0
    assert bind(AddScaledTradeAction(fwd(), None, scaling_level="lvl")).apply(T0, sv, None)[0].quantity == 5.0
    assert bind(AddScaledTradeAction(fwd(), None, scaling_level=4.0)).apply(T0, v, AddTradeActionInfo(scaling=-0.5))[0].quantity == -2.0


def test_scaled_risk_measure_hits_the_risk_level_and_flips_for_negative_unit_risk():
    v = view(vega={"fwd": 0.25, "short": -0.25})
    a = bind(AddScaledTradeAction(fwd(), None, "v", ScalingActionType.risk_measure, "vega", 100))
    (o,) = a.apply(T0, v, None)
    assert o.quantity * 0.25 == pytest.approx(100.0)
    (o2,) = bind(AddScaledTradeAction(fwd("short"), None, scaling_type="risk_measure", scaling_risk="vega", scaling_level=100)).apply(T0, v, None)
    assert o2.quantity * -0.25 == pytest.approx(100.0) and o2.quantity < 0
    with pytest.raises(SizingError):
        bind(AddScaledTradeAction(fwd("z"), None, scaling_type="risk_measure", scaling_risk="vega", scaling_level=1)).apply(T0, view(vega={"z": 0.0}), None)
    assert a.requires_measures() == ("vega",)


def test_scaled_nav_spends_the_pot_including_costs_and_recycles_action_cash():
    cost = ScaledCost("notional", 0.01)
    price, notional = 4.0, 10.0

    a = bind(AddScaledTradeAction(fwd(), None, "n", ScalingActionType.NAV, None, 100.0, transaction_cost=cost))
    v = FakeView(now=T0, units={("fwd", "pv"): price, ("fwd", "notional"): notional})  # the cost scales by the bound `notional` measure
    (o,) = a.apply(T0, v, None)
    assert o.quantity * price + 0.01 * abs(notional * o.quantity) == pytest.approx(100.0)  # q*P + C(q) == L0
    assert o.meta.kind == "scaled"
    v._cash["n"] = -60.0  # 60 already spent, no exits yet: pot is 40
    (o2,) = a.apply(T0, v, None)
    assert o2.quantity * price + 0.01 * notional * o2.quantity == pytest.approx(40.0)
    v._cash["n"] = -100.0
    assert a.apply(T0, v, None) == []  # nothing left
    v._cash["n"] = 30.0  # the previous trade unwound at a profit: the pot is refilled (chained cash)
    (o3,) = a.apply(T0, v, None)
    assert o3.quantity * price + 0.01 * notional * o3.quantity == pytest.approx(130.0)


def test_scaled_nav_constant_cost_above_pot_gives_no_order_and_zero_price_policy():
    class V(FakeView):
        def build(self, template, *, request=None):
            return ToyForward(strike=0.0)

    v = V(now=T0, units={("fwd", "pv"): 4.0})
    assert bind(AddScaledTradeAction(fwd(), None, scaling_type="NAV", scaling_level=5.0, transaction_cost=ConstantCost(10.0))).apply(T0, v, None) == []
    z = V(now=T0, units={("fwd", "pv"): 0.0})
    with pytest.raises(SizingError):
        bind(AddScaledTradeAction(fwd(), None, scaling_type="NAV", scaling_level=5.0)).apply(T0, z, None)
    sk = bind(AddScaledTradeAction(fwd(), None, scaling_type="NAV", scaling_level=5.0, on_zero_price="skip"))
    assert sk.apply(T0, z, None) == [] and z.events[-1][1]["reason"] == "zero_price"


def test_scaled_validation():
    with pytest.raises(StrategyError):
        AddScaledTradeAction(fwd(), None, scaling_type="risk_measure")
    with pytest.raises(StrategyError):
        AddScaledTradeAction(fwd(), None, scaling_type="NAV", scaling_level={T0.date(): 1.0})


def test_weighted_split_by_risk_and_scaling_multiplier():
    v = view(dv01={"a": 0.01, "b": 0.03})
    a = bind(AddWeightedTradeAction([fwd("a"), fwd("b")], None, "w", "dv01", 100.0))
    q = [o.quantity for o in a.apply(T0, v, None)]
    assert q == pytest.approx([25.0, 75.0]) and sum(q) == pytest.approx(100.0)
    assert [o.quantity for o in a.apply(T0, v, AddTradeActionInfo(scaling=-1.0))] == pytest.approx([-25.0, -75.0])
    with pytest.raises(StrategyError):
        bind(AddWeightedTradeAction(Bundle("b", [Leg(fwd("a"), 2.0)]), None, scaling_risk="dv01")).apply(T0, v, None)
    with pytest.raises(StrategyError):
        AddWeightedTradeAction(fwd(), None)  # needs scaling_risk


def test_weighted_accepts_next_schedule_info():
    """gs omitted the schedule info for AddWeighted, so 'next schedule' raised there."""
    a = bind(AddWeightedTradeAction([fwd("a")], "next schedule", scaling_risk="dv01"))
    (o,) = a.apply(T0, view(dv01={"a": 1.0}), ScheduleInfo(next_schedule=D("2024-02-07")))
    assert o.final_ts == D("2024-02-07")


# ------------------------------------------------------------------ Hedge
def hedge_view(net, unit, **kw):
    return FakeView(now=T0, measures={"dv01": net}, units={("hedge", "dv01"): unit}, **kw)


@pytest.mark.parametrize("net, unit, target", [(225.9, 0.45, 0.0), (-16.42, 0.45, 25.0), (100.0, -2.0, -10.0)])
def test_hedge_postcondition_net_plus_q_times_unit_equals_target(net, unit, target):
    a = bind(AddTradeAction(fwd()), name="X")
    h = bind(HedgeAction("dv01", fwd("hedge"), None, "H"), name="H")
    (o,) = h.apply(T0, hedge_view(net, unit), HedgeActionInfo(target=target))
    assert net + o.quantity * unit == pytest.approx(target)
    assert o.meta.kind == "hedge" and o.tags == ("hedge",) and o.meta.group == "hedge:H"


def test_hedge_percentage_target_default_and_scope_from_info():
    h = bind(HedgeAction("dv01", fwd("hedge"), None, "H", risk_percentage=50.0, target=10.0), name="H")
    (o,) = h.apply(T0, hedge_view(110.0, 2.0), None)
    assert o.quantity == pytest.approx(-(110.0 - 10.0) / 2.0 * 0.5)
    seen = []
    v = FakeView(now=T0, measures={"dv01": lambda scope: seen.append(scope) or 50.0}, units={("hedge", "dv01"): 1.0})
    bind(HedgeAction("dv01", fwd("hedge"), None, "H2"), name="H2").apply(T0, v, HedgeActionInfo(target=0.0, data={"scope": "tag:core"}))
    assert seen == ["tag:core"]  # the band was measured over this scope, so the hedge sizes over it too
    seen.clear()
    bind(HedgeAction("dv01", fwd("hedge"), None, "H3", scope="action:x"), name="H3").apply(T0, v, None)
    assert seen == ["action:x"]


def test_hedge_zero_unit_risk_raises_or_skips_and_zero_residual_is_skipped():
    v = hedge_view(10.0, 0.0)
    with pytest.raises(SizingError):
        bind(HedgeAction("dv01", fwd("hedge"), None, "H")).apply(T0, v, None)
    sk = bind(HedgeAction("dv01", fwd("hedge"), None, "H", on_zero_hedge_risk="skip"))
    assert sk.apply(T0, v, None) == [] and v.events[-1][0] == "hedge_skipped"
    w = hedge_view(0.8 - 0.8 + 3e-16, 1.0)
    assert bind(HedgeAction("dv01", fwd("hedge"), None, "H")).apply(T0, w, None) == [] and w.events[-1][1]["reason"] == "zero_residual_risk"


def test_hedge_unsupported_options_and_csa_term_sugar():
    with pytest.raises(NotSupportedError):
        HedgeAction("dv01", fwd("hedge"), None, "H", scaling_parameter="other")
    with pytest.raises(NotSupportedError):
        HedgeAction("dv01", fwd("hedge"), None, "H", risk_transformation="abs")
    h = HedgeAction("dv01", fwd("hedge"), None, "H", csa_term="EUR-OIS")
    assert h.pricer_request == {"csa_term": "EUR-OIS"}
    with pytest.raises(StrategyError):
        HedgeAction(None, fwd("hedge"))
    with pytest.raises(StrategyError):
        HedgeAction("dv01", [fwd("a"), fwd("b")], mode="resize")


def test_hedge_resize_mode_targets_the_residual_and_flips_via_close_open():
    pos = lambda q, i="P1": SimpleNamespace(id=i, quantity=q, entry_ts=T0, final_ts=None)

    class V(FakeView):
        def __init__(self, net, held, hedge_measure):
            super().__init__(now=T0, measures={"dv01": lambda scope: hedge_measure if isinstance(scope, PositionSelector) else net}, units={("hedge", "dv01"): 1.0})
            self.held = held

        def positions(self, scope="portfolio", *, include_pending=False):
            return tuple(self.held)

    h = bind(HedgeAction("dv01", fwd("hedge"), None, "H", mode="resize"), name="H")
    assert opens(h.apply(T0, V(-30.0, [], 0.0), None))[0].quantity == pytest.approx(30.0)  # no hedge yet: open one
    # held +30 hedge: net (incl. hedge) is 0 -> residual without the hedge is -30 -> target quantity stays +30 -> nothing to do
    assert h.apply(T0, V(0.0, [pos(30.0)], 30.0), None) == []
    (r,) = h.apply(T0, V(-10.0, [pos(30.0)], 30.0), None)  # underlying moved: residual -40 -> want +40 -> resize by +10
    assert isinstance(r, ResizeOrder) and r.delta_quantity == pytest.approx(10.0) and r.position_id == "P1"
    out = h.apply(T0, V(60.0, [pos(30.0)], 30.0), None)  # residual +30 -> want -30: a sign flip is close + open, never a resize
    assert isinstance(out[0], CloseOrder) and opens(out)[0].quantity == pytest.approx(-30.0)


# ------------------------------------------------------------------ Exit
P = lambda id, tpl="swap", action="A", kind="add", tags=(): SimpleNamespace(id=id, tags=tuple(tags), meta=PositionMeta(action=action, kind=kind, template=tpl, tags=tuple(tags)))


class PV(FakeView):
    def __init__(self, *pos):
        super().__init__(now=T0)
        self.pos = pos

    def positions(self, scope="portfolio", *, include_pending=False):
        return tuple(p for p in self.pos if (scope if isinstance(scope, PositionSelector) else PositionSelector()).matches(p))


def selected(action, pos, info=None):
    return [p.id for p in pos if action.selector(info).matches(p)]


def test_exit_selector_matrix_names_tags_ids_actions_kinds_select():
    pos = [P("1", "swap1", tags=("a",)), P("2", "swap2", tags=("a", "b")), P("3", "swap1", "B", "hedge", ("b",)), P("4", "swap10")]
    assert selected(ExitTradeAction(), pos) == ["1", "2", "3", "4"]  # no criteria: everything
    assert selected(ExitTradeAction("swap1"), pos) == ["1", "3"]  # a bare string is ONE name, not a substring test ("swap10" not matched)
    assert selected(ExitTradeAction(["swap1", "swap2"]), pos) == ["1", "2", "3"]
    assert selected(ExitTradeAction(tags=("b",)), pos) == ["2", "3"]
    assert selected(ExitTradeAction(tags=("a", "b"), match_tags="all"), pos) == ["2"]
    assert selected(ExitTradeAction(position_ids=("4",)), pos) == ["4"]
    assert selected(ExitTradeAction(), pos, ExitTradeActionInfo(position_ids=("2",))) == ["2"]  # ids also come from the info
    assert selected(ExitTradeAction(actions=("B",)), pos) == ["3"]
    assert selected(ExitTradeAction(kinds=("add",)), pos) == ["1", "2", "4"]  # excludes hedges
    assert selected(ExitTradeAction("swap1", kinds=("add",)), pos) == ["1"]  # AND across criteria
    assert selected(ExitTradeAction(select=lambda p: p.id > "2"), pos) == ["3", "4"]
    assert selected(ExitTradeAction("my_swap_1"), [P("1", "my_swap_1")]) == ["1"]  # underscores in a name are harmless (gs parsed names)


def test_exit_apply_close_order_reason_cost_and_nothing_matched():
    v = PV(P("1"), P("2", "other"))
    (c,) = bind(ExitTradeAction("swap", transaction_cost=ConstantCost(2.0))).apply(T0, v, None)
    assert isinstance(c, CloseOrder) and c.reason == "signal" and c.cost_model == ConstantCost(2.0) and c.selector.templates == ("swap",)
    (call,) = bind(ExitAllPositionsAction()).apply(T0, v, None)
    assert call.reason == "exit_all" and call.cost_model is None  # None = the position's own exit model (gs ignored the exit cost entirely)
    assert bind(ExitTradeAction("nothing")).apply(T0, v, None) == []
    assert bind(ExitTradeAction(reason="custom:why")).apply(T0, v, None)[0].reason == "custom:why"
    assert bind(ExitTradeAction()).apply(T0, v, ExitTradeActionInfo(skip=True)) == []
    assert ExitAllPositionsAction.info_kinds == ("exit",) and ExitAllPositionsAction.kind == "exit_all"


# ------------------------------------------------------------------ Rebalance
class RV(FakeView):
    def __init__(self, held, measure_x=0.0, unit=1.0, size_unit=1.0):
        super().__init__(now=T0, measures={"dv01": lambda scope: measure_x, "number_of_options": lambda scope: sum(p.quantity * p.size for p in held)},
                         units={("reb", "dv01"): unit, ("reb", "number_of_options"): size_unit})
        self.held, self.size_unit = held, size_unit

    def positions(self, scope="portfolio", *, include_pending=False):
        return tuple(self.held)

def pos(id, q, final, size=1.0):
    return SimpleNamespace(id=id, quantity=q, entry_ts=T0, final_ts=final, size=size)


def test_rebalance_method_mode_sizes_the_delta_and_inherits_parent_and_final():
    held = [pos("P1", 4.0, D("2024-03-01")), pos("P2", 2.0, D("2024-04-01"))]
    a = bind(RebalanceAction(fwd("reb"), "quantity", lambda ts, view, info: 10.0), name="R")
    (o,) = a.apply(T0, RV(held), None)
    assert o.quantity == pytest.approx(4.0) and o.meta.kind == "rebalance" and o.meta.parent == "P2" and o.final_ts == D("2024-04-01")
    assert o.meta.group == "rebalance:R"
    held2 = [pos("P1", 4.0, None)]
    assert bind(RebalanceAction(fwd("reb"), method=lambda *a: 10.0)).apply(T0, RV(held2), None)[0].final_ts is None  # any position held forever -> forever
    over = bind(RebalanceAction(fwd("reb"), method=lambda *a: 10.0, trade_duration="1w")).apply(T0, RV(held), None)[0]
    assert over.final_ts == pd.Timestamp("2024-02-07 17:00", tz=NY)
    assert bind(RebalanceAction(fwd("reb"), method=lambda *a: 6.0)).apply(T0, RV(held), None) == []  # already at target: no order


def test_rebalance_size_parameter_attribute_reproduces_gs_040308():
    held = [pos("P1", 3.0, None, size=10.0)]  # 30 options held (3 positions-units x 10 options)
    a = bind(RebalanceAction(fwd("reb"), "number_of_options", lambda ts, view, info: 50.0))
    (o,) = a.apply(T0, RV(held, size_unit=10.0), None)
    assert o.quantity == pytest.approx(2.0)  # (50 - 30)/10


def test_rebalance_measure_mode_uses_info_target_and_info_scope_over_own():
    seen = []
    v = RV([pos("P1", 4.0, D("2024-03-01"))], measure_x=400.0, unit=100.0)
    v.measures["dv01"] = lambda scope: seen.append(scope) or 1400.0
    a = bind(RebalanceAction(fwd("reb"), measure="dv01"), name="R")
    (o,) = a.apply(T0, v, RebalanceActionInfo(target=0.0, data={"scope": "portfolio"}))
    assert o.quantity == pytest.approx(-14.0) and seen == ["portfolio"]  # the WHOLE book is brought to the target, not just the template's holdings
    with pytest.raises(StrategyError):
        a.apply(T0, v, None)  # no target anywhere
    assert bind(RebalanceAction(fwd("reb"), measure="dv01", target=1400.0)).apply(T0, v, None) == []


def test_rebalance_resize_mode_and_validation():
    held = [pos("P1", 4.0, None), pos("P2", 2.0, None)]
    (r,) = bind(RebalanceAction(fwd("reb"), method=lambda *a: 10.0, mode="resize")).apply(T0, RV(held), None)
    assert isinstance(r, ResizeOrder) and r.delta_quantity == pytest.approx(4.0) and r.position_id in ("P1", "P2")
    with pytest.raises(StrategyError):
        RebalanceAction(fwd("reb"))
    with pytest.raises(StrategyError):
        RebalanceAction(fwd("reb"), method=lambda *a: 1, measure="dv01")
    assert RebalanceAction(fwd("reb"), method=lambda *a: 1).trade_duration is INHERIT


# ------------------------------------------------------------------ EarlyExit
def test_early_exit_caps_final_ts_and_limits_concurrent_positions():
    class V(FakeView):
        def __init__(self, n_open, pending=()):
            super().__init__(now=T0)
            self.n_open, self.pending_orders = n_open, tuple(pending)

        def positions(self, scope="portfolio", *, include_pending=False):
            return tuple(range(self.n_open))

    a = bind(EarlyExitPositionLimitScaledAction(fwd(), "3m", "E", ScalingActionType.size, None, 1, early_exits=[D("2024-03-15"), D("2024-02-20")], max_concurrent_pos=2), name="E")
    (o,) = a.apply(T0, V(1), None)
    assert o.final_ts == D("2024-02-20") and o.meta.extra["exit_reason"] == "early_exit"  # the FIRST early exit strictly after the entry
    v = V(2)
    assert a.apply(T0, v, None) == [] and v.events[-1][1]["reason"] == "position_limit"  # the whole firing is dropped
    pend = OpenOrder(fwd(), 1.0, meta=PositionMeta(action="E"))
    assert a.apply(T0, V(1, [pend]), None) == []  # pending orders of this action count toward the limit
    assert a.info_kinds == ("add_scaled_trade",) and a.kind == "early_exit_scaled"
    after = bind(EarlyExitPositionLimitScaledAction(fwd(), "1w", scaling_level=1, early_exits=[D("2024-06-01")]))
    assert after.apply(T0, V(0), None)[0].final_ts == D("2024-02-07") and "exit_reason" not in after.apply(T0, V(0), None)[0].meta.extra


# ------------------------------------------------------------------ Custom / SubmitOrders / phases
def test_custom_and_submit_orders_complete_blank_metadata():
    raw = OpenOrder(fwd(), 2.0)
    c = bind(CustomAction(lambda ts, view, info, k=1: [raw, CloseOrder()], "C", kwargs={"k": 2}), name="C", ti=1, ai=3)
    o, close = c.apply(T0, view(), None)
    assert (o.meta.action, o.meta.trigger_idx, o.meta.action_idx, o.meta.kind) == ("C", 1, 3, "add") and isinstance(close, CloseOrder)
    s = bind(SubmitOrdersAction(), name="S")
    (o2,) = s.apply(T0, view(), SubmitOrdersInfo(orders=(raw,)))
    assert o2.meta.action == "S" and s.apply(T0, view(), None) == []
    keep = OpenOrder(fwd(), 1.0, meta=PositionMeta(action="mine", trigger_idx=9))
    assert s.apply(T0, view(), SubmitOrdersInfo(orders=(keep,)))[0].meta.action == "mine"  # an order that already has metadata is left alone
    with pytest.raises(StrategyError):
        CustomAction(None)


def test_default_phases_and_phase_override_and_initial_rejected():
    from pricebt.types import Phase

    assert ExitTradeAction().effective_phase is Phase.EXIT and AddTradeAction(fwd()).effective_phase is Phase.ADD
    assert RebalanceAction(fwd(), method=lambda *a: 1).effective_phase is Phase.ADJUST and HedgeAction("dv01", fwd()).effective_phase is Phase.HEDGE
    assert AddTradeAction(fwd(), phase=Phase.HEDGE).effective_phase is Phase.HEDGE
    with pytest.raises(StrategyError):
        AddTradeAction(fwd(), phase=Phase.INITIAL)


def test_gs_positional_signatures():
    """gs call patterns: AddScaledTradeAction(portfolio, '1m', name, scaling_type, scaling_risk, level); HedgeAction(risk, priceable, duration, name)."""
    a = AddScaledTradeAction(fwd(), "1m", "s", ScalingActionType.risk_measure, "vega", 100)
    assert (a.trade_duration, a.name, a.scaling_type, a.scaling_risk, a.scaling_level) == ("1m", "s", ScalingActionType.risk_measure, "vega", 100)
    h = HedgeAction("delta", fwd("f"), "2b", "H1")
    assert (h.risk, h.trade_duration, h.name, h.priceables.legs[0].name) == ("delta", "2b", "H1", "f")
    e = ExitTradeAction("swap1", "ExitAction1")
    assert e.priceable_names == ("swap1",) and e.name == "ExitAction1"
    w = AddWeightedTradeAction([fwd("a")], "1m", "w", "dv01", 500.0)
    assert (w.scaling_risk, w.total_size) == ("dv01", 500.0)
    r = RebalanceAction(fwd("x"), "number_of_options", lambda *a: 1.0, None, None, "reb")
    assert r.size_parameter == "number_of_options" and r.name == "reb"
