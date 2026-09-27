import json
import sys

import pandas as pd
import pytest

from pricebt.errors import OptionalDependencyError
from pricebt.results import BacktestResult, ResultIntegrityError
from pricebt.results.io import FRAMES, from_parquet, to_parquet
from test_results_common import D, forward_run, rich_run, synth_result

pytestmark = pytest.mark.core

pytest.importorskip("pyarrow")


def test_round_trip_frames_stats_and_manifest(tmp_path):
    res = rich_run(measures=("dv01",), initial_capital=500.0)
    out = res.to_parquet(tmp_path / "run")
    assert (out / "manifest.json").exists() and all((out / f"{n}.parquet").exists() for n in FRAMES)
    back = BacktestResult.from_parquet(out)
    pd.testing.assert_frame_equal(back.equity, res.equity)  # tz-aware index, dtypes and every value survive
    for name in ("trades", "orders", "errors", "layers_by_position"):
        pd.testing.assert_frame_equal(getattr(back.record, name).reset_index(drop=True), getattr(res.record, name).reset_index(drop=True), check_dtype=False)
    pd.testing.assert_frame_equal(back.positions, res.positions, check_dtype=False)
    assert back.config_hash == "abc123" and back.grid_info == res.grid_info and back.name == res.name
    assert back.record.settings.initial_capital == 500.0 and back.record.settings.measures == ("dv01",) and back.record.settings.layers == ()
    assert back.record.manifest["n_points"] == res.record.manifest["n_points"]
    pd.testing.assert_series_equal(back.summary_stats(), res.summary_stats())
    pd.testing.assert_frame_equal(back.attribution("layer"), res.attribution("layer"))
    assert back.reconcile().ok


def test_events_detail_survives_as_dict(tmp_path):
    from test_results_common import run
    from pricebt.orders import OpenOrder
    from test_results_common import fwd, make_grid

    res = run({make_grid().end: [OpenOrder(fwd())]}, fill_lag=1)  # last-point order: an event with a dict payload
    assert len(res.events) == 1
    back = from_parquet(to_parquet(res, tmp_path / "e"))
    assert back.events["kind"].tolist() == ["unfilled_order_at_end"] and back.events["detail"].iloc[0]["action"] == res.events["detail"].iloc[0]["action"]


def test_manifest_json_content_and_written_last(tmp_path):
    res = forward_run()
    out = res.to_parquet(tmp_path / "r")
    body = json.loads((out / "manifest.json").read_text(encoding="utf8"))
    assert body["schema_version"] == 1 and body["config_hash"] == "abc123" and body["frames"]["equity"] == len(res.equity)
    assert body["settings"]["cadence"] == "each" and body["grid_info"]["n_points"] == len(res.equity)
    assert not (out / "manifest.json.tmp").exists()


def test_overwrite_replaces_old_result(tmp_path):
    a, b = forward_run(), rich_run()
    a.to_parquet(tmp_path / "r")
    b.to_parquet(tmp_path / "r")
    assert len(from_parquet(tmp_path / "r").positions) == len(b.positions)


def test_missing_or_corrupt_directories_are_reported(tmp_path):
    with pytest.raises(ResultIntegrityError, match="no manifest"):
        from_parquet(tmp_path)
    out = forward_run().to_parquet(tmp_path / "r")
    (out / "trades.parquet").unlink()
    with pytest.raises(ResultIntegrityError, match="missing trades"):
        from_parquet(out)
    out2 = forward_run().to_parquet(tmp_path / "r2")
    (out2 / "manifest.json").write_text("{ truncated", encoding="utf8")
    with pytest.raises(ResultIntegrityError, match="unreadable"):
        from_parquet(out2)
    out3 = forward_run().to_parquet(tmp_path / "r3")
    body = json.loads((out3 / "manifest.json").read_text(encoding="utf8"))
    body["frames"]["equity"] += 1
    (out3 / "manifest.json").write_text(json.dumps(body), encoding="utf8")
    with pytest.raises(ResultIntegrityError, match="rows"):
        from_parquet(out3)
    body["schema_version"] = 99
    (out3 / "manifest.json").write_text(json.dumps(body), encoding="utf8")
    with pytest.raises(ResultIntegrityError, match="schema_version"):
        from_parquet(out3)


def test_missing_pyarrow_gives_clear_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)  # makes `import pyarrow` raise ImportError
    with pytest.raises(OptionalDependencyError, match="pyarrow"):
        to_parquet(forward_run(), tmp_path / "x")
    with pytest.raises(OptionalDependencyError, match="pip install pyarrow"):
        from_parquet(tmp_path)


def test_empty_frames_round_trip(tmp_path):
    res = synth_result([1.0, -2.0, 3.0], 100.0)
    back = from_parquet(to_parquet(res, tmp_path / "s"))
    assert back.positions.empty and back.record.trades.empty
    pd.testing.assert_frame_equal(back.equity, res.equity, check_freq=False)
    assert back.summary_stats()["total_pnl"] == pytest.approx(2.0)
