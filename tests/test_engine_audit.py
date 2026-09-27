"""The run manifest and the optional audit trail the tie-out harness compares (spec L5, D5, X2): layer definition ids, conventions digests, the snapshot digest of
every input, and per-position marks, measures and layer amounts."""
import hashlib

import pandas as pd
import pytest

from conftest import make_engine
from pricebt.engine import Engine, EngineSettings
from pricebt.market import Binding, MarketData
from pricebt.orders import CloseOrder, OpenOrder
from pricebt.pricer import FunctionMDP
from pricebt.testing.scripted import ScriptedStrategy
from pricebt.testing.toys import ToyForward, ToyMDP, ToyPricer, ToyZero
from pricebt.timeutil import Clock, TimeContext, TimeGrid
from toy_helpers import toy_tpl

pytestmark = pytest.mark.core

D = lambda s: pd.Timestamp(f"{s} 17:00", tz="America/New_York")


def digest_of(ts, tag="s"):
    return hashlib.sha256(f"{tag}|{pd.Timestamp(ts).value}".encode()).hexdigest()


class Tagged(ToyPricer):
    """A snapshot-shaped pricer: it carries the digest of the data it was built from."""

    def __init__(self, ts, rate, tag="s", **kw):
        super().__init__(ts, rate, **kw)
        self.digest = digest_of(ts, tag)


def provider(tag="s"):
    inner = ToyMDP()
    return FunctionMDP(lambda ts, req: Tagged(ts, inner.get_pricer(ts).rate, tag))


def run(strategy, *, mdp=None, wrap=None, **settings):
    grid = TimeGrid.daily("2024-01-02", "2024-01-31", "1b", TimeContext())
    md = MarketData({"primary": Binding(mdp or provider(), {}, wrap)}, Clock())
    settings.setdefault("show_progress", False)
    eng = Engine(grid, md, strategy, EngineSettings(**settings))
    return eng.run(), md


def fwd(**kw):
    return toy_tpl(name="fwd", target=ToyForward, kwargs={"strike": 4.0, "notional": 10.0, "carry_bp_per_day": 0.5, **kw}, layers=("carry", "delta"))


STRAT = lambda: ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd(), quantity=3.0)], D("2024-01-22"): [CloseOrder()]})


# ------------------------------------------------------------------ manifest (always on)
def test_the_manifest_names_every_instrument_its_conventions_digest_and_its_layer_ids():
    rec, _ = run(STRAT(), layers=("carry", "delta"))
    m = rec.manifest["instruments"]["fwd"]
    assert m["asset_class"] == "toy" and len(m["conventions_digest"]) == 16 and m["factory"]
    assert m["layers"] == {"carry": "toy.carry@1", "delta": "toy.delta@1"}


def test_the_conventions_digest_changes_with_the_conventions_and_ignores_key_order():
    from pricebt.contracts.spec import conventions_digest

    a = conventions_digest({"day_count": "x", "calendar": "y"})
    assert a == conventions_digest({"calendar": "y", "day_count": "x"}) and a != conventions_digest({"day_count": "z", "calendar": "y"})


def test_two_instruments_are_listed_separately():
    zero = toy_tpl(name="zero", target=ToyZero, kwargs={"maturity": D("2024-06-28"), "notional": 100.0}, layers=("roll",))
    strat = ScriptedStrategy({D("2024-01-08"): [OpenOrder(fwd()), OpenOrder(zero)]})
    rec, _ = run(strat, layers=("carry", "roll"))
    assert set(rec.manifest["instruments"]) == {"fwd", "zero"} and rec.manifest["instruments"]["zero"]["layers"] == {"roll": "toy.roll@1"}


def test_extension_layers_have_no_schema_id_and_say_so():
    from pricebt.testing.toys import toy_template

    class Ext(ToyForward):
        def value_ext(self, ctx):
            return 0.0

    ext = toy_template(Ext(strike=4.0), name="ext", layers=("carry",), extra={"toy.ext": "layer"}, bind={"toy.ext": {"target": {"method": "value_ext"}, "kwargs": {"ctx": "@ctx"}}})
    rec, _ = run(ScriptedStrategy({D("2024-01-08"): [OpenOrder(ext)]}), layers=("carry",))
    assert rec.manifest["instruments"]["ext"]["layers"]["carry"] == "toy.carry@1"


# ------------------------------------------------------------------ audit (opt in)
def test_audit_is_off_by_default_and_costs_nothing():
    rec, md = run(STRAT())
    assert rec.audit == {} and md.audit is None


def test_audit_inputs_hold_one_digest_per_distinct_fetch_and_the_digest_is_the_providers_not_the_wrappers():
    seen = []

    def wrap(p):
        w = ToyPricer(p.ts, p.rate)  # a library pricer with NO digest of its own
        seen.append(p.digest)
        return w

    rec, md = run(STRAT(), audit=True, wrap=wrap)
    inp = rec.audit["inputs"]
    assert list(inp.columns) == ["ts", "role", "digest", "stamp"] and len(inp) == md.n_fetches and len(inp) > 0
    assert (inp["role"] == "primary").all() and inp["digest"].str.len().eq(64).all()
    assert list(inp["digest"]) == [digest_of(t) for t in inp["ts"]], "the digest is the SNAPSHOT's, recorded before the adapter wrapped it"
    assert set(seen) <= set(inp["digest"])


def test_audit_inputs_without_a_digest_are_recorded_as_none():
    rec, _ = run(STRAT(), audit=True, mdp=ToyMDP())
    assert rec.audit["inputs"]["digest"].isna().all()


def test_audit_marks_carry_pv_cash_financing_and_requested_measures_per_position_per_point():
    rec, _ = run(STRAT(), audit=True, audit_measures=("dv01", "rate", "vega"))
    marks = rec.audit["marks"]
    assert {"ts", "position", "quantity", "pv", "cash", "financing", "measure_dv01", "measure_rate", "measure_vega"} <= set(marks.columns)
    pid = rec.positions["id"].iloc[0]
    mine = marks[marks["position"] == pid].set_index("ts")
    assert mine.index[0] == D("2024-01-08") and mine.index[-1] == D("2024-01-22") and (mine["quantity"] == 3.0).all()
    eq = rec.equity["positions_value"]
    for ts, row in mine.iterrows():
        if ts != mine.index[-1]:
            assert eq.loc[ts] == pytest.approx(row["quantity"] * row["pv"]), ts
    assert mine["measure_dv01"].to_numpy() == pytest.approx(10.0 * 0.01)
    assert mine["measure_rate"].to_numpy() == pytest.approx([ToyMDP().get_pricer(t).rate for t in mine.index]), "`rate` is the toy market's own rate, in percent"
    assert mine["measure_vega"].isna().all(), "`vega` is not bound on a toy forward: NaN, not an error"
    assert len(rec.errors) == 0, "an UNBOUND measure is the one silent NaN cell (MeasureNotBound); nothing is recorded for it"


def test_audit_marks_are_one_row_per_position_and_point_even_when_a_mark_is_asked_twice():
    rec, _ = run(STRAT(), audit=True)
    marks = rec.audit["marks"]
    assert not marks.duplicated(["ts", "position"]).any()


def test_audit_layers_amounts_plus_unexplained_equal_the_interval_pnl():
    rec, _ = run(STRAT(), audit=True, layers=("carry", "delta"), cadence="each")
    lay = rec.audit["layers"]
    assert {"ts", "position", "layer", "unit", "amount"} <= set(lay.columns) and {"carry", "delta", "unexplained"} <= set(lay["layer"])
    total = lay.groupby("layer")["amount"].sum()
    assert total["carry"] == pytest.approx(rec.equity["layer_carry"].iloc[-1]) and total["unexplained"] == pytest.approx(rec.equity["layer_unexplained"].iloc[-1])
    pos = rec.positions.iloc[0]
    assert pos["tcost"] == 0.0 and lay["amount"].sum() == pytest.approx(pos["pnl"], abs=1e-9), "every interval of the position is explained (layers plus unexplained) and sums to its P&L"


def test_audit_survives_a_second_run_of_the_same_engine_without_accumulating():
    strat = STRAT()
    grid = TimeGrid.daily("2024-01-02", "2024-01-31", "1b", TimeContext())
    md = MarketData({"primary": provider()}, Clock())
    eng = Engine(grid, md, strat, EngineSettings(show_progress=False, audit=True))
    a, b = eng.run(), eng.run()
    assert len(a.audit["inputs"]) == len(b.audit["inputs"]) and len(a.audit["marks"]) == len(b.audit["marks"])


def test_audit_and_settings_survive_the_parquet_round_trip(tmp_path):
    from pricebt.results import BacktestResult
    from pricebt.results.io import from_parquet, to_parquet

    rec, _ = run(STRAT(), audit=True, audit_measures=("dv01",), record_signals=(), layers=("carry",))
    back = from_parquet(to_parquet(BacktestResult(rec), tmp_path / "r"))
    assert set(back.record.audit) == {"inputs", "marks", "layers"} and back.record.settings.audit_measures == ("dv01",)
    pd.testing.assert_frame_equal(back.record.audit["marks"].reset_index(drop=True), rec.audit["marks"].reset_index(drop=True))
    assert back.record.manifest["instruments"]["fwd"]["layers"]["carry"] == "toy.carry@1"
    rec2, _ = run(STRAT())
    assert from_parquet(to_parquet(BacktestResult(rec2), tmp_path / "r2")).record.audit == {}


def test_audit_marks_record_the_financing_of_the_interval_per_unit():
    zero = toy_tpl(name="zero", target=ToyZero, kwargs={"maturity": D("2024-06-28"), "notional": 100.0}, layers=("roll",))
    rec, _ = run(ScriptedStrategy({D("2024-01-08"): [OpenOrder(zero, quantity=2.0)]}), audit=True)
    marks = rec.audit["marks"]
    fin = marks["financing"].to_numpy()
    assert (fin[1:] != 0.0).all() and fin[0] == 0.0, "the entry mark has no interval; every later one carries the funding accrual"
    assert rec.positions["financing"].iloc[0] == pytest.approx(2.0 * fin.sum())


def test_audit_layer_rows_are_per_unit_and_amount_is_quantity_times_unit_including_unexplained():
    rec, _ = run(STRAT(), audit=True, layers=("carry", "delta"), cadence="each")
    lay = rec.audit["layers"]
    assert lay["amount"].to_numpy() == pytest.approx(3.0 * lay["unit"].to_numpy())


def test_a_nonzero_unexplained_is_recorded_per_unit_too():
    class Off(ToyForward):
        def value_delta(self, ctx):  # a deliberately wrong layer: the engine's `unexplained` must absorb the difference
            return 1.0

    off = toy_tpl(name="off", target=Off, kwargs={"strike": 4.0, "notional": 10.0}, layers=("delta",))
    rec, _ = run(ScriptedStrategy({D("2024-01-08"): [OpenOrder(off, quantity=3.0)], D("2024-01-22"): [CloseOrder()]}), audit=True, layers=("delta",), cadence="each")
    un = rec.audit["layers"].query("layer == 'unexplained'")
    assert (un["amount"].abs() > 1e-6).any() and un["amount"].to_numpy() == pytest.approx(3.0 * un["unit"].to_numpy())


# ------------------------------------------------------------------ E4 (doubt cycle 3): a BOUND measure that fails is a NaN cell AND one recorded error; only an UNBOUND one is silent
class Broken(ToyForward):
    def nan_gamma(self, ctx):
        return float("nan")

    def upper_case_ladder(self, ctx):
        return {"10y": 1.0}  # a real bug in a binding: a lower-case tenor key

    def not_a_number(self, ctx):
        return "n/a"


def _broken_run(bind, measure, **settings):
    from toy_helpers import bind_method

    t = toy_tpl(name="fwd", target=Broken, kwargs={"strike": 4.0, "notional": 10.0}, bind={k: bind_method(v) for k, v in bind.items()})
    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(t, quantity=3.0)], D("2024-01-24"): [CloseOrder()]})
    grid = TimeGrid.daily("2024-01-02", "2024-01-31", "1b", TimeContext())
    return Engine(grid, MarketData({"primary": ToyMDP()}, Clock()), strat, EngineSettings(show_progress=False, audit=True, audit_measures=(measure,), **settings)).run()


@pytest.mark.parametrize("on_error", ["raise", "record"])
@pytest.mark.parametrize("bind,measure,column", [({"gamma": "nan_gamma"}, "gamma", "measure_gamma"), ({"delta_ladder": "upper_case_ladder"}, "delta_ladder", "measure_delta_ladder"),
                                                 ({"gamma": "not_a_number"}, "gamma", "measure_gamma")], ids=["non-finite", "bad-ladder-key", "not-a-number"])
def test_e4_a_bound_measure_that_fails_is_a_nan_cell_and_one_recorded_error_per_mark(bind, measure, column, on_error):
    """R6a: the audit swallowed every MeasureError without a trace, so a bound-but-broken measure looked exactly like an unbound one. The run still completes."""
    rec = _broken_run(bind, measure, on_error=on_error)
    marks = rec.audit["marks"]
    assert marks[column].isna().all() and len(marks) >= 5
    errs = rec.errors[rec.errors["where"] == "audit"]
    assert len(errs) == len(marks), "one error per audited mark: the failure is not silent"
    assert measure in errs["error"].iloc[0] and set(errs["position"]) == {"P000001"}
    assert len(rec.trades) == 2 and (rec.positions["status"] == "closed").all(), "and the audit never aborts or half-books"


def test_e4_an_unbound_measure_stays_the_one_silent_nan_cell():
    """The other half of C2: `vega` is not bound on a toy forward, so it is NaN and NOTHING is recorded."""
    rec = _broken_run({}, "vega", on_error="record")
    assert rec.audit["marks"]["measure_vega"].isna().all() and len(rec.errors) == 0
