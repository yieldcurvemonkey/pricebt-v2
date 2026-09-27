
import numpy as np
import pandas as pd
import pytest

from pricebt.orders import OpenOrder
from pricebt.results import ResultError, build_report, combined_equity, compare, comparison_table, correlation, df_to_markdown, limitations, pnl_matrix
from pricebt.results.report import slug
from test_results_common import D, TZ, forward_run, fwd, make_grid, rich_run, run, synth_result

pytestmark = pytest.mark.core


def mirror_pair():
    t_in, t_out = D("2024-01-10"), D("2024-02-07")
    long = run({t_in: [OpenOrder(fwd(notional=5.0), quantity=1.0, final_ts=t_out)]}, name="long")
    short = run({t_in: [OpenOrder(fwd(notional=5.0), quantity=-1.0, final_ts=t_out)]}, name="short")
    return {"long": long, "short": short}


def test_mirror_strategies_have_correlation_minus_one_and_flat_combination():
    rs = mirror_pair()
    corr = correlation(rs)
    assert corr.loc["long", "short"] == pytest.approx(-1.0) and corr.loc["long", "long"] == pytest.approx(1.0)
    comb = combined_equity(rs)
    assert comb["equity"].abs().max() < 1e-12 and comb["n_active"].eq(2).all()
    table = comparison_table(rs)
    assert table.loc["long", "total_pnl"] == pytest.approx(-table.loc["short", "total_pnl"])
    assert table.loc["long", "sharpe"] == pytest.approx(-table.loc["short", "sharpe"])


def test_pnl_matrix_window_semantics_and_weights():
    idx_a = pd.date_range("2024-01-02 17:00", periods=6, freq="B", tz=TZ)
    a = synth_result([1, 2, 3, 4, 5, 6], index=idx_a, name="A")
    idx_b = idx_a[[2, 3, 5]]  # starts later, has a gap (no point on idx_a[4])
    b = synth_result([10, 20, 30], index=idx_b, name="B")
    m = pnl_matrix({"A": a, "B": b})
    days = pd.DatetimeIndex(idx_a.tz_localize(None).normalize(), name="day")
    assert list(m.index) == list(days)
    assert m["A"].tolist() == [1, 2, 3, 4, 5, 6]
    assert np.isnan(m["B"].iloc[:2]).all()  # before B's own window: NaN
    assert m["B"].iloc[2:].tolist() == [10, 20, 0.0, 30]  # inside the window without a point: 0, not NaN
    comb = combined_equity({"A": a, "B": b}, weights={"B": 0.5})
    assert comb["step_pnl"].tolist() == [1, 2, 3 + 5, 4 + 10, 5, 6 + 15]
    assert comb["equity"].iloc[-1] == 21 + 60 * 0.5 and comb["n_active"].tolist() == [1, 1, 2, 2, 2, 2]
    corr = correlation({"A": a, "B": b}, min_periods=3)
    overlap = pd.DataFrame({"A": [3, 4, 5, 6], "B": [10, 20, 0, 30]})
    assert corr.loc["A", "B"] == pytest.approx(np.corrcoef(overlap["A"], overlap["B"])[0, 1])
    assert np.isnan(correlation({"A": a, "B": b}, min_periods=10).loc["A", "B"])


def test_combined_equity_capital_and_errors():
    a = synth_result([1, 2, 3], 100.0, name="A")
    b = synth_result([1, 1, 1], 50.0, name="B")
    comb = combined_equity({"A": a, "B": b})
    assert comb["equity"].tolist() == [152.0, 155.0, 159.0]
    with pytest.raises(ResultError, match="unknown strategies"):
        combined_equity({"A": a}, weights={"Z": 1.0})
    with pytest.raises(ResultError):
        compare({})


def test_compare_bundle_shapes():
    rs = mirror_pair()
    c = compare(rs)
    assert list(c.table.index) == ["long", "short"] and "sharpe" in c.table.columns and c.table.dtypes.map(pd.api.types.is_numeric_dtype).all()
    assert c.pnl.shape[1] == 2 and c.correlation.shape == (2, 2)
    assert list(c.equity.columns) == ["long", "short"] and len(c.equity) == len(make_grid())
    assert list(c.combined.columns) == ["equity", "step_pnl", "n_active"]
    with pytest.raises(ResultError, match="unknown statistics"):
        comparison_table(rs, stats=("nope",))


def test_df_to_markdown_exact_string():
    df = pd.DataFrame({"a": [1.5, np.nan, 1234.5678], "b": ["x|y", "z", None], "c": [1, 2, 3], "d": [np.inf, -np.inf, 0.25]}, index=pd.Index(["r1", "r2", "r3"], name="k"))
    md = df_to_markdown(df, formats={"d": "{:.1%}"})
    assert md == "\n".join([
        "| k | a | b | c | d |",
        "|---|---|---|---|---|",
        "| r1 | 1.5000 | x\\|y | 1 | inf |",
        "| r2 |  | z | 2 | -inf |",
        "| r3 | 1,234.5678 |  | 3 | 25.0% |",
    ])
    assert df_to_markdown(df[["c"]], index=False) == "| c |\n|---|\n| 1 |\n| 2 |\n| 3 |"


def test_slug_and_collisions(tmp_path):
    assert slug("A b/C") == "a-b-c" and slug("!!") == "run"
    with pytest.raises(ResultError, match="collide"):
        build_report({"A b": forward_run(), "a-b": forward_run()}, tmp_path)
    with pytest.raises(ResultError):
        build_report({}, tmp_path)


def test_limitations_are_data_driven():
    short_flat = synth_result([1.0, 2.0, -1.0], name="tiny")
    txt = "\n".join(limitations({"tiny": short_flat}, extra=("synthetic data only",)))
    assert "tiny: only 3 return periods" in txt and "annualisation factor" in txt and "currency P&L" in txt
    assert "no modelled P&L layers" in txt and "synthetic data only" in txt and "in-sample" in txt
    rich = "\n".join(limitations({"rich": rich_run()}))
    assert "2 positions were still open" in rich and "no transaction costs" not in rich
    nocost = "\n".join(limitations({"n": run({D("2024-01-10"): [OpenOrder(fwd(), final_ts=D("2024-01-31"))]}, name="n")}))
    assert "n: no transaction costs were charged" in nocost


def test_build_report_files_and_content(tmp_path):
    rs = {"Long fwd": forward_run(name="Long fwd"), "Rich": rich_run(name="Rich")}
    b = build_report(rs, tmp_path / "rep", title="Experiment", intro="Two toy runs.", extra_limitations=("Toy market only.",))
    md = b.markdown.read_text(encoding="utf8")
    for needle in ("# Experiment", "Two toy runs.", "## Summary", "## Comparison", "## Long fwd", "## Rich", "### Attribution by layer", "### Integrity (reconcile)",
                   "## Limitations", "- Toy market only.", "identity", "figures/long-fwd_tearsheet.png", "figures/comparison_pnl.png", "| carry |"):
        assert needle in md, needle
    assert all(p.exists() and p.stat().st_size > 1000 for p in b.figures) and len(b.figures) == 3
    assert {p.name for p in b.tables} >= {"comparison.csv", "pnl_correlation.csv", "attribution_layer_rich.csv", "closed_long-fwd.csv", "reconcile_rich.csv"}
    assert pd.read_csv(tmp_path / "rep" / "tables" / "comparison.csv", index_col=0).loc["Long fwd", "total_pnl"] == pytest.approx(rs["Long fwd"].pnl.iloc[-1])
    again = build_report(rs, tmp_path / "rep2", title="Experiment", intro="Two toy runs.", extra_limitations=("Toy market only.",))
    assert again.markdown.read_text(encoding="utf8") == md  # deterministic
    assert (tmp_path / "rep2" / "figures" / "comparison_pnl.png").read_bytes() == (tmp_path / "rep" / "figures" / "comparison_pnl.png").read_bytes()


def test_build_report_single_result_without_figures(tmp_path):
    b = build_report({"solo": forward_run()}, tmp_path, figures=False)
    md = b.markdown.read_text(encoding="utf8")
    assert "## Comparison" not in md and b.figures == () and "![" not in md
