import pytest

from pricebt.costs import ConstantCost
from pricebt.orders import OpenOrder, PositionMeta
from pricebt.results import ResultError
from test_results_common import D, forward_run, fwd, rich_run, run, zero

pytestmark = pytest.mark.core

BY = ("layer", "component", "position", "template", "action", "kind", "tag", "day")


def rate(ts):
    from pricebt.testing.toys import ToyMDP

    return ToyMDP().get_pricer(ts).rate


def total_change(res):
    return res.equity["equity"].iloc[-1] - res.initial_capital


def test_by_layer_toyforward_closed_form():
    res = forward_run()
    a = res.attribution("layer")["pnl"]
    t_in, t_out = D("2024-01-10"), D("2024-02-07")
    assert a["carry"] == pytest.approx(3 * 10 * 0.5 * 0.01 * 28.0, abs=1e-9)
    assert a["delta"] == pytest.approx(3 * 10 * (rate(t_out) - rate(t_in)), abs=1e-9)
    assert a["convexity"] == 0.0 and a["unexplained"] == pytest.approx(0.0, abs=1e-9)
    assert a["transactions"] == -3.0 and a["cash_interest"] == 0.0
    assert a["total"] == pytest.approx(total_change(res), abs=1e-9)
    share = res.attribution("layer")["share"]
    assert share.drop("total").sum() == pytest.approx(1.0)


@pytest.mark.parametrize("by", BY)
def test_every_grouping_conserves_equity_change(by):
    res = rich_run()
    t = res.attribution(by)
    col = "pnl" if by in ("layer", "component") else "net_total"
    expected = total_change(res)
    got = t["pnl"].loc["total"] if by in ("layer", "component") else t["net_total"].sum()
    assert got == pytest.approx(expected, abs=1e-9)
    assert (t[col] != 0).any()


@pytest.mark.parametrize("by", ("position", "template", "action", "kind", "tag", "day"))
def test_grouped_columns_sum_to_net_total(by):
    t = run_rich_table(by)
    comps = [c for c in t.columns if c != "net_total"]
    assert (t[comps].sum(axis=1) - t["net_total"]).abs().max() < 1e-9


def run_rich_table(by):
    return rich_run().attribution(by)


def test_unexplained_is_bucket_for_positions_without_layers_and_matches_engine_when_layered():
    res = rich_run(cadence="each")
    pos = res.attribution("position")
    gross = res.positions["pnl_gross"]
    # option and forward have no layers: everything unexplained
    for pid in ("P000001", "P000003"):
        assert pos.loc[pid, "unexplained"] == pytest.approx(gross[pid], abs=1e-9)
        assert pos.loc[pid, ["roll", "delta", "convexity"]].abs().sum() == 0.0
    # layered zero-coupon: identity-derived unexplained equals the engine's own row
    eng = res.record.layers_by_position.set_index(["position", "layer"])["pnl"]
    assert pos.loc["P000002", "unexplained"] == pytest.approx(eng[("P000002", "unexplained")], abs=1e-9)
    assert pos.loc["P000002", ["roll", "delta", "convexity", "unexplained"]].sum() == pytest.approx(gross["P000002"], abs=1e-9)


def test_zero_coupon_layers_explain_price_and_unexplained_is_financing_plus_third_order():
    res = run({D("2024-01-10"): [OpenOrder(zero(layers=("roll", "delta", "convexity")), quantity=10.0)]}, cadence="each")
    a = res.attribution("layer")["pnl"]
    fin = res.equity["financing_cum"].iloc[-1]
    assert fin < 0
    assert a["unexplained"] == pytest.approx(fin, abs=0.02 * abs(fin))  # financing is reserved (never a pricable layer) so it lands here
    assert abs(a["roll"]) > 0 and abs(a["delta"]) > 0 and a["convexity"] > 0  # long zero: convexity gain
    comp = res.attribution("component")["pnl"]
    assert comp["financing"] == pytest.approx(fin) and comp["price"] + comp["cash_flows"] + comp["financing"] == pytest.approx(total_change(res))


def test_no_layers_everything_unexplained_but_component_split_exact():
    res = run({D("2024-01-10"): [OpenOrder(fwd(notional=10.0, carry_bp_per_day=0.5), quantity=3.0, final_ts=D("2024-02-07"), cost_entry=ConstantCost(2.0))]})
    a = res.attribution("layer")["pnl"]
    assert list(a.index) == ["unexplained", "transactions", "cash_interest", "total"]
    assert a["unexplained"] == pytest.approx(res.positions["pnl_gross"].iloc[0]) and a["transactions"] == -4.0
    comp = res.attribution("component")["pnl"]
    assert comp["cash_flows"] == pytest.approx(3 * 10 * 0.5 * 0.01 * 28.0) and comp["price"] == pytest.approx(3 * 10 * (rate(D("2024-02-07")) - rate(D("2024-01-10"))))


def test_tag_split_vs_each_and_untagged():
    script = {D("2024-01-10"): [
        OpenOrder(fwd(notional=2.0), tags=("a", "b"), final_ts=D("2024-02-01")),
        OpenOrder(fwd(notional=5.0), tags=("a",), final_ts=D("2024-02-01")),
        OpenOrder(fwd(notional=7.0), final_ts=D("2024-02-01")),
    ]}
    res = run(script)
    d = rate(D("2024-02-01")) - rate(D("2024-01-10"))
    split = res.attribution("tag")["net_total"]
    assert split["a"] == pytest.approx(0.5 * 2.0 * d + 5.0 * d) and split["b"] == pytest.approx(0.5 * 2.0 * d) and split["(untagged)"] == pytest.approx(7.0 * d)
    assert split.sum() == pytest.approx(total_change(res))
    each = res.attribution("tag", tag_mode="each")["net_total"]
    assert each["a"] == pytest.approx(2.0 * d + 5.0 * d) and each["b"] == pytest.approx(2.0 * d)
    assert each.sum() > split.sum() + 1e-9  # overlapping by construction: not additive


def test_group_by_action_template_kind_and_position_rows():
    script = {D("2024-01-10"): [
        OpenOrder(fwd("f1", notional=2.0), meta=PositionMeta(action="A", kind="add"), final_ts=D("2024-02-01")),
        OpenOrder(fwd("f2", notional=3.0), meta=PositionMeta(action="B", kind="hedge"), final_ts=D("2024-02-01")),
    ]}
    res = run(script)
    d = rate(D("2024-02-01")) - rate(D("2024-01-10"))
    assert res.attribution("action")["net_total"][["A", "B"]].tolist() == pytest.approx([2.0 * d, 3.0 * d])
    assert res.attribution("template")["net_total"]["f2"] == pytest.approx(3.0 * d)
    assert res.attribution("kind")["net_total"]["hedge"] == pytest.approx(3.0 * d)
    assert list(res.attribution("position").index) == ["P000001", "P000002", "(cash)"]


def test_costs_and_interest_are_their_own_buckets():
    res = rich_run()
    a = res.attribution("layer")["pnl"]
    assert a["transactions"] == pytest.approx(-0.5)
    assert a["cash_interest"] == pytest.approx(res.equity["interest_cum"].iloc[-1]) and a["cash_interest"] != 0.0
    grouped = res.attribution("template")
    assert grouped.loc["(cash)", "net_total"] == pytest.approx(a["cash_interest"])


def test_by_day_carry_lumps_over_weekend_and_conserves():
    res = forward_run()
    day = res.attribution("day")
    assert day["net_total"].sum() == pytest.approx(total_change(res))
    assert day.loc["2024-01-11", "carry"] == pytest.approx(3 * 10 * 0.5 * 0.01 * 1.0)
    assert day.loc["2024-01-15", "carry"] == pytest.approx(3 * 10 * 0.5 * 0.01 * 3.0)  # Fri -> Mon: three calendar days of carry
    assert day.loc["2024-01-10", "transactions"] == -2.0 and day.loc["2024-02-07", "transactions"] == -1.0
    assert day["unexplained"].abs().max() < 1e-9  # exact layers at cadence 'each'
    assert (day[["carry", "delta", "convexity", "unexplained", "transactions", "cash_interest"]].sum(axis=1) - day["net_total"]).abs().max() < 1e-9


def test_by_day_with_sparse_cadence_shows_unflushed_interval_as_unexplained():
    res = forward_run(cadence="every_n:5")
    day = res.attribution("day")
    assert day["net_total"].sum() == pytest.approx(total_change(res))
    assert day["unexplained"].abs().max() > 0.0  # days between flushes: P&L booked, layers not yet computed
    assert day["unexplained"].sum() == pytest.approx(0.0, abs=1e-9)  # the flush closes the interval, so it nets out over the run


def test_layer_frames_are_consistent():
    res = rich_run(cadence="each")
    ls = res.layers
    per_pos = res.layers_by_position
    assert ls.iloc[-1]["roll"] == pytest.approx(per_pos["roll"].sum()) and ls.iloc[-1]["delta"] == pytest.approx(per_pos["delta"].sum())
    assert ls.index.equals(res.equity.index) and ls.iloc[0].abs().sum() == 0.0  # cumulative: nothing before the first flush


def test_invalid_arguments():
    res = forward_run()
    with pytest.raises(ResultError):
        res.attribution("bogus")
    with pytest.raises(ResultError):
        res.attribution("tag", tag_mode="both")
