import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pricebt.errors import LookAheadError, MissingDataError, NotSupportedError, SignalError
from pricebt.strategy import (ConstantSource, DerivedSignal, MeasureSignal, MissingDataStrategy, PricerSignal, RingBuffer, SeriesSource, SignalStore, StepTable)
from pricebt.strategy.signals import zscore_of
from pricebt.testing.toys import ToyMDP, ToyPricer
from test_strategy_support import D, FakeView, fwd

pytestmark = pytest.mark.core

ts_at = lambda s, h="10:00": pd.Timestamp(f"{s} {h}", tz="America/New_York")
S = pd.Series({dt.date(2021, 10, 4): 11.0, dt.date(2021, 10, 5): 12.0, dt.date(2021, 10, 8): 15.0})


# ------------------------------------------------------------------ RingBuffer
def test_ring_window_matches_list_oracle_through_wraparound():
    r = RingBuffer(5)
    ref = []
    rng = np.random.default_rng(1)
    for k in range(23):
        v = float(rng.normal())
        assert r.append(k, v) is True
        ref.append(v)
        for n in (1, 3, 5, 9):
            assert list(r.window(n, inclusive=True)) == ref[-n:][-5:]
            assert list(r.window(n)) == ref[:-1][-n:][-4:]


def test_ring_dedupes_equal_key_rejects_older_and_returns_copies():
    r = RingBuffer(4)
    r.append(1, 1.0)
    assert r.append(1, 99.0) is False and list(r.window(5, inclusive=True)) == [1.0]
    with pytest.raises(SignalError):
        r.append(0, 0.0)
    r.append(2, 2.0)
    w = r.window(1, inclusive=True)
    r.append(3, 3.0)
    assert list(w) == [2.0]  # a held window is not a live view


# ------------------------------------------------------------------ SeriesSource
def test_series_date_keys_become_session_close_no_same_day_lookahead():
    s = SeriesSource(S, MissingDataStrategy.fill_forward)
    assert s.get(ts_at("2021-10-05", "10:00")) == 11.0  # the 10-05 close is not yet visible at 10:00
    assert s.get(ts_at("2021-10-05", "17:00")) == 12.0


def test_naive_midnight_datetimeindex_is_date_only_not_lookahead():
    idx = pd.date_range("2021-10-01", periods=3)
    s = SeriesSource(pd.Series([1.0, 2.0, 3.0], index=idx), "fill_forward")
    assert s.get(ts_at("2021-10-02", "10:00")) == 1.0
    never = SeriesSource(pd.Series([1.0, 2.0, 3.0], index=idx), "fill_forward", date_only="never")
    assert never.get(ts_at("2021-10-02", "10:00")) == 2.0


def test_series_missing_data_strategies_and_bounds():
    ff, fail = SeriesSource(S, "fill_forward"), SeriesSource(S, "fail")
    assert ff.get(ts_at("2021-10-06", "17:00")) == 12.0  # carried
    with pytest.raises(MissingDataError):
        fail.get(ts_at("2021-10-06", "17:00"))  # no observation on that date
    assert fail.get(ts_at("2021-10-08", "17:00")) == 15.0
    with pytest.raises(MissingDataError):
        ff.get(ts_at("2021-10-04", "10:00"))  # before the first visible observation: never back-filled
    bounded = SeriesSource(S, "fill_forward", max_staleness="1D")
    with pytest.raises(MissingDataError):
        bounded.get(ts_at("2021-10-07", "17:00"))
    with pytest.raises(NotSupportedError):
        SeriesSource(S, "interpolate")


def test_series_is_immutable_and_lag_delays_visibility():
    src = pd.Series({dt.date(2021, 10, 4): 1.0, dt.date(2021, 10, 5): 2.0})
    s = SeriesSource(src, "fill_forward")
    before = s.get(ts_at("2021-10-05", "17:00"))
    src.iloc[1] = 99.0  # caller mutates its own data afterwards
    assert s.get(ts_at("2021-10-05", "17:00")) == before == 2.0
    assert not s._vals.flags.writeable and not s._ns.flags.writeable
    s.get(ts_at("2021-10-04", "17:00"))
    assert len(s) == 2  # lookups never grow the series (gs GenericDataSource did)
    lagged = SeriesSource(src, "fill_forward", lag="16h")
    with pytest.raises(MissingDataError):
        lagged.get(ts_at("2021-10-05", "07:00"))  # 10-04 17:00 + 16h = 10-05 09:00
    assert lagged.get(ts_at("2021-10-05", "09:30")) == 1.0


def test_series_rejects_nan_and_duplicates():
    with pytest.raises(ValueError):
        SeriesSource(pd.Series([1.0, np.nan], index=[dt.date(2021, 1, 4), dt.date(2021, 1, 5)]))
    with pytest.raises(ValueError):
        SeriesSource(pd.Series([1.0, 2.0], index=["2021-01-04", "2021-01-04"]))


def test_series_window_is_strictly_before_current_and_inclusive_option():
    s = SeriesSource(S, "fill_forward")
    t = ts_at("2021-10-08", "17:00")
    assert list(s.window_at(t, 5)) == [11.0, 12.0]
    assert list(s.window_at(t, 5, inclusive=True)) == [11.0, 12.0, 15.0]
    assert list(s.window_at(ts_at("2021-10-09", "17:00"), 1)) == [12.0]  # stale current (10-08) excluded


def test_series_from_file_parses_dates_as_strings(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text("date,close\n2021-10-04,11\n2021-10-05,12\n")
    s = SeriesSource.from_file(str(p), column="close", missing_data_strategy="fill_forward")
    assert s.get(ts_at("2021-10-05", "10:00")) == 11.0


# ------------------------------------------------------------------ StepTable
def test_steptable_gs_interpolate_signal_values_and_after_last_rule():
    m = {dt.date(2021, 12, 6): 13.0, dt.date(2021, 12, 10): 21.0}
    z, hold = StepTable(m), StepTable(m, after_last="hold")
    got = [z.value_at(D(f"2021-12-{d:02d}")) for d in (6, 7, 8, 9, 10, 11)]
    assert got == [13, 13, 13, 13, 21, 0]  # gs: [13,13,13,13,21] then 0 outside [min, max]
    assert hold.value_at(D("2021-12-11")) == 21.0
    assert z.value_at(ts_at("2021-12-05", "23:59")) == 0.0  # before_first
    assert z.value_at(ts_at("2021-12-10", "20:00")) == 21.0 and z.value_at(ts_at("2021-12-11", "00:00")) == 0.0
    assert z.value_at(ts_at("2021-12-06", "09:35")) == 13.0  # a date key is effective from local midnight


# ------------------------------------------------------------------ store
def test_store_series_value_window_and_lookahead_guard():
    src = SeriesSource(S, "fill_forward", name="s")
    st = SignalStore([src])
    st.observe(D("2021-10-05"), FakeView())
    assert st.value(src) == 12.0 and st.value("s") == 12.0
    assert list(st.window(src, 3)) == [11.0]
    with pytest.raises(LookAheadError):
        st.value(src, D("2021-10-06"))
    assert st.value(src, D("2021-10-04")) == 11.0  # history-backed sources serve past instants


def test_store_requires_observation_before_reads():
    with pytest.raises(SignalError):
        SignalStore().value(ConstantSource(1.0))


def test_derived_zscore_equals_pandas_oracle_and_excludes_current():
    rng = np.random.default_rng(7)
    vals = np.cumsum(rng.normal(size=40)) + 50
    days = pd.bdate_range("2024-01-02", periods=40)
    src = SeriesSource(pd.Series(vals, index=days), "fill_forward", name="x")
    z = DerivedSignal("zscore", (src,), window=10, name="z")
    st = SignalStore([z])
    got = []
    for d in days:
        st.observe(pd.Timestamp(f"{d.date()} 17:00", tz="America/New_York"), FakeView())
        try:
            got.append(st.value(z))
        except MissingDataError:
            got.append(np.nan)
    x = pd.Series(vals)
    prior = x.shift(1)
    oracle = ((x - prior.rolling(10).mean()) / prior.rolling(10).std()).to_numpy()
    assert np.isnan(got[:10]).all()  # warm-up: needs 10 PRIOR observations
    np.testing.assert_allclose(got[10:], oracle[10:], rtol=1e-10)


def test_zscore_oracle_check_is_not_vacuous_inclusive_window_differs():
    rng = np.random.default_rng(3)
    vals = rng.normal(size=30)
    x = pd.Series(vals)
    excl = ((x - x.shift(1).rolling(10).mean()) / x.shift(1).rolling(10).std()).iloc[15]
    incl = ((x - x.rolling(10).mean()) / x.rolling(10).std()).iloc[15]
    assert abs(excl - incl) > 1e-3


def test_derived_ops_combo_spread_fly_diff_rolling_ratio():
    a = SeriesSource(pd.Series([1.0, 2.0, 4.0, 8.0], index=pd.bdate_range("2024-01-02", periods=4)), "fill_forward", name="a")
    b = SeriesSource(pd.Series([1.0, 1.0, 2.0, 2.0], index=pd.bdate_range("2024-01-02", periods=4)), "fill_forward", name="b")
    sig = {
        "spread": DerivedSignal("spread", (a, b)),
        "fly": DerivedSignal("fly", (a, b, a), name="f"),
        "combo": DerivedSignal("combo", (a, b), weights=(2.0, 3.0)),
        "ratio": DerivedSignal("ratio", (a, b)),
        "diff": DerivedSignal("diff", (a,), lag=1),
        "rmean": DerivedSignal("rolling_mean", (a,), window=2),
        "rstd": DerivedSignal("rolling_std", (a,), window=2),
        "neg": DerivedSignal("neg", (a,)),
    }
    st = SignalStore(list(sig.values()))
    out = {k: [] for k in sig}
    for d in pd.bdate_range("2024-01-02", periods=4):
        st.observe(pd.Timestamp(f"{d.date()} 17:00", tz="America/New_York"), FakeView())
        for k, s in sig.items():
            try:
                out[k].append(st.value(s))
            except MissingDataError:
                out[k].append(None)
    assert out["spread"] == [0, 1, 2, 6] and out["combo"] == [5, 7, 14, 22] and out["ratio"] == [1, 2, 2, 4]
    assert out["fly"] == [0, -2, -4, -12]  # -a + 2b - a
    assert out["diff"] == [None, 1, 2, 4] and out["neg"] == [-1, -2, -4, -8]
    assert out["rmean"] == [None, None, 1.5, 3.0]  # prior two: (1,2) then (2,4)
    assert out["rstd"][2] == pytest.approx(np.std([1, 2], ddof=1))


def test_derived_fly_uses_weights_minus_one_two_minus_one():
    a, b, c = (ConstantSource(v) for v in (1.0, 5.0, 2.0))
    f = DerivedSignal("fly", (a, b, c))
    st = SignalStore([f])
    st.observe(D("2024-01-02"), FakeView())
    assert st.value(f) == -1 + 10 - 2


def test_zero_std_window_rule_uses_relative_epsilon():
    prior = np.full(10, 3.7)
    assert zscore_of(3.7, prior, mean_n=10, std_n=10) == 0.0
    assert zscore_of(9.0, prior, mean_n=10, std_n=10) == np.inf
    assert zscore_of(-9.0, prior, mean_n=10, std_n=10) == -np.inf
    tiny = 100.0 + np.array([0, 1e-14, -1e-14, 0, 1e-14])
    assert zscore_of(100.0, tiny, mean_n=5, std_n=5) == 0.0  # numerically flat plateau is not a spurious 0.9


def test_store_missing_current_raises_on_value_but_not_in_observe_and_window_uses_ring():
    src = SeriesSource(S, "fail", name="s")
    d = DerivedSignal("rolling_mean", (src,), window=2, name="m")
    st = SignalStore([d])
    for t in (D("2021-10-04"), D("2021-10-05"), D("2021-10-06"), D("2021-10-08")):
        st.observe(t, FakeView())  # 10-06 has no observation under `fail`
    assert st.value(d) == pytest.approx(11.5)  # mean of the two PRIOR observations 11, 12 at 10-08


def test_pricer_signal_reads_lookup_dedupes_snapshots_and_spread():
    mdp = ToyMDP(path=lambda ts: {"rate": 4.0 + ts.day * 0.1}, mapping=None)
    ps = PricerSignal("level", {"name": "rate"}, scale=100.0, name="r")
    spread = PricerSignal.spread("level", {"name": "rate"}, {"name": "funding"}, scale=1.0, name="sp")

    class V(FakeView):
        def pricer(self, ts=None, role="primary", request=None):
            return mdp.get_pricer(ts)

    st = SignalStore([ps, spread])
    for day in (2, 3):
        t = D(f"2024-01-0{day}")
        st.observe(t, V(now=t))
    assert st.value(ps) == pytest.approx(100.0 * (4.0 + 0.3))
    assert st.value(spread) == pytest.approx((4.0 + 0.3) - 4.0)  # funding is 4.0 on the toy pricer
    assert list(st.window(ps, 5)) == [pytest.approx(420.0)]


def test_pricer_signal_skips_lookup_when_snapshot_did_not_advance():
    calls = []

    class P(ToyPricer):
        def level(self, name="rate"):
            calls.append(1)
            return super().level(name)

    fixed = P(D("2024-01-02"), 4.0)

    class V(FakeView):
        def pricer(self, ts=None, role="primary", request=None):
            return fixed

    ps = PricerSignal("level", {"name": "rate"}, name="r")
    st = SignalStore([ps])
    for d in ("2024-01-02", "2024-01-03", "2024-01-04"):
        st.observe(D(d), V())
    assert len(calls) == 1  # one lookup per DISTINCT snapshot
    assert st.value(ps) == 4.0 and len(st.window(ps, 5)) == 0  # ring holds ONE observation; carried points add nothing


def test_measure_signal_uses_measure_of():
    v = FakeView(units={("fwd", "dv01"): 0.5})
    ms = MeasureSignal(fwd(), "dv01", quantity=4.0, name="m")
    st = SignalStore([ms])
    st.observe(D("2024-01-02"), v)
    assert st.value(ms) == 2.0


def test_store_resets_between_runs_and_names_must_be_unique():
    c = DerivedSignal("rolling_mean", (ConstantSource(2.0, name="c"),), window=2, name="m")
    st = SignalStore([c])
    for d in ("2024-01-02", "2024-01-03", "2024-01-04"):
        st.observe(D(d), FakeView())
    assert list(st.window(c, 5, inclusive=True)) == [2.0]  # rolling needs 2 prior observations: the first emission is the 3rd point
    st.observe(D("2024-01-02"), FakeView())  # a second run starts earlier: state must reset, not raise
    assert len(st.window(c, 5, inclusive=True)) == 0
    with pytest.raises(SignalError):
        SignalStore([ConstantSource(1.0, name="z"), ConstantSource(2.0, name="z")])


def test_signals_survive_deepcopy_by_identity():
    import copy

    s = SeriesSource(S, "fill_forward")
    assert copy.deepcopy(s) is s and copy.deepcopy(StepTable({})) is not None


# ------------------------------------------------------------------ book-level signals (showcase W1.4)
class BookView(FakeView):
    """A FakeView whose book has a scalar measure per scope and a ladder per scope."""

    def __init__(self, vectors=None, **kw):
        super().__init__(**kw)
        self.vectors = vectors or {}
        self.calls = []

    def measure(self, name, scope="portfolio", *, include_pending=False):
        self.calls.append((name, scope, include_pending))
        return super().measure(name, scope, include_pending=include_pending)

    def measure_vector(self, name, scope="portfolio", *, include_pending=False, missing="raise"):
        self.calls.append((name, scope, include_pending, missing))
        v = self.vectors[name]
        return dict(v(scope) if callable(v) else v)


def test_book_measure_signal_reads_the_books_measure_in_scope():
    from pricebt.strategy import BookMeasureSignal

    v = BookView(measures={"dv01": lambda scope: {"portfolio": 1.5e6, "action:hedge": -2.0e5}[scope]})
    tot, hedge = BookMeasureSignal("dv01", name="book"), BookMeasureSignal("dv01", "action:hedge", include_pending=True, name="hedge")
    st = SignalStore([tot, hedge])
    st.observe(D("2024-01-02"), v)
    assert st.value(tot) == 1.5e6 and st.value(hedge) == -2.0e5
    assert ("dv01", "portfolio", False) in v.calls and ("dv01", "action:hedge", True) in v.calls


def test_book_measure_signal_refuses_a_non_finite_measure():
    from pricebt.strategy import BookMeasureSignal

    st = SignalStore([BookMeasureSignal("dv01", name="b")])
    with pytest.raises(SignalError, match="non-finite"):
        st.observe(D("2024-01-02"), BookView(measures={"dv01": float("nan")}))


def test_book_ladder_signal_reads_one_bucket_and_an_empty_book_is_flat():
    from pricebt.strategy import BookLadderSignal

    v = BookView(vectors={"delta_ladder": {"2Y": 10.0, "10Y": -4.0}})
    b10, b2 = BookLadderSignal("delta_ladder", "10Y", name="b10"), BookLadderSignal("delta_ladder", "2Y", name="b2")
    st = SignalStore([b10, b2])
    st.observe(D("2024-01-02"), v)
    assert st.value(b10) == -4.0 and st.value(b2) == 10.0
    st2 = SignalStore([BookLadderSignal("delta_ladder", "10Y", name="e")])
    st2.observe(D("2024-01-02"), BookView(vectors={"delta_ladder": {}}))
    assert st2.value("e") == 0.0, "no positions, no risk in any bucket"


def test_book_ladder_signal_names_the_buckets_the_book_has_when_one_is_missing():
    from pricebt.strategy import BookLadderSignal

    st = SignalStore([BookLadderSignal("delta_ladder", "7Y", name="b7")])
    with pytest.raises(SignalError, match="7Y") as e:
        st.observe(D("2024-01-02"), BookView(vectors={"delta_ladder": {"2Y": 1.0, "10Y": 2.0}}))
    assert "2Y" in str(e.value) and "10Y" in str(e.value)
    with pytest.raises(SignalError, match="tenor"):
        BookLadderSignal("delta_ladder", "front")  # not a tenor


def test_book_ladder_signal_passes_scope_pending_and_missing_policy_through():
    from pricebt.strategy import BookLadderSignal

    v = BookView(vectors={"delta_ladder": {"10Y": 3.0}})
    s = BookLadderSignal("delta_ladder", "10Y", "action:x", include_pending=True, missing="zero", name="s")
    SignalStore([s]).observe(D("2024-01-02"), v)
    assert v.calls == [("delta_ladder", "action:x", True, "zero")]


def test_book_signals_are_registered_for_config_and_track_the_engine_book():
    """Through a real engine run: the book dv01 signal equals the recorded measure of the PREVIOUS point (signals are read before the step's orders execute)."""

    from pricebt.engine import Engine, EngineSettings
    from pricebt.market import MarketData
    from pricebt.orders import OpenOrder
    from pricebt.registries import SIGNALS
    from pricebt.strategy import BookMeasureSignal
    from pricebt.testing.scripted import ScriptedStrategy
    from pricebt.testing.toys import ToyMDP
    from pricebt.timeutil import Clock, TimeContext, TimeGrid

    assert SIGNALS.resolve("book_measure") is BookMeasureSignal and SIGNALS.resolve("book_ladder")
    sig = BookMeasureSignal("dv01", name="book")
    seen = {}

    def fn(ts, view, submit):
        seen[ts] = view.signal(sig)

    strat = ScriptedStrategy({D("2024-01-10"): [OpenOrder(fwd(notional=10.0), quantity=2.0)]}, fn=fn)
    grid = TimeGrid.daily("2024-01-02", "2024-02-29", "1b", TimeContext())
    eng = Engine(grid, MarketData({"primary": ToyMDP()}, Clock()), strat, EngineSettings(show_progress=False, measures=("dv01",), record_signals=("book",)), signals=SignalStore([sig]))
    rec = eng.run()
    dv = rec.equity["measure_dv01"]
    pts = list(dv.index)
    for a, b in zip(pts[:-1], pts[1:]):
        assert seen[b] == pytest.approx(dv.loc[a]), b
    assert dv.iloc[-1] == pytest.approx(0.2) and seen[pts[1]] == 0.0
    assert list(rec.equity["signal_book"]) == [seen[t] for t in pts], "record_signals stores the value the strategy saw at each point"
