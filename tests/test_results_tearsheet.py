import base64
import struct
import subprocess
import sys

import numpy as np
import pytest

from conftest import make_engine
from pricebt.errors import OptionalDependencyError
from pricebt.results import BacktestResult, ResultError
from pricebt.results import tearsheet as T
from pricebt.testing.scripted import ScriptedStrategy
from test_results_common import forward_run, make_grid, rich_run, synth_result

pytestmark = pytest.mark.core

pytest.importorskip("matplotlib")
PNG = b"\x89PNG\r\n\x1a\n"


def png_size(b):
    return struct.unpack(">II", b[16:24])


def test_png_and_pdf_are_valid_and_deterministic():
    res = rich_run(measures=("dv01",))
    a = T.render(res, formats=("png", "pdf"))
    b = T.render(res, formats=("png", "pdf"))
    assert a["png"].startswith(PNG) and a["pdf"].startswith(b"%PDF")
    assert a["png"] == b["png"] and a["pdf"] == b["pdf"]  # no timestamps / versions in the files
    assert len(a["png"]) > 20_000


def test_panels_without_data_are_omitted():
    flat = BacktestResult(make_engine(make_grid(), ScriptedStrategy())[0].run())
    avail = T._available(flat)
    assert not avail["trades"] and not avail["layers"] and not avail["positions_value"] and not avail["measures"]
    rich = rich_run(measures=("dv01",))
    assert all(T._available(rich).values())
    assert png_size(T.render(flat)["png"])[1] < png_size(T.render(rich)["png"])[1]  # fewer panels -> shorter figure
    only = T.render(rich, panels=("equity",))["png"]
    assert png_size(only)[1] < png_size(T.render(flat)["png"])[1]


def test_short_runs_skip_rolling_sharpe_and_unknown_panels_raise():
    short = synth_result([1.0, 2.0, -1.0])
    assert not T._available(short)["rolling_sharpe"]
    assert T.render(short)["png"].startswith(PNG)
    with pytest.raises(ResultError, match="unknown panels"):
        T.render(short, panels=("nope",))
    with pytest.raises(ResultError):
        T.render(short, formats=("svg",))


def test_html_is_self_contained_and_escaped():
    res = forward_run(name="<b>x</b>")
    assert T.tearsheet(res, formats=("png",)).html is None
    out = T.tearsheet(res, formats=("html",))
    page = out.html
    assert page.startswith("<!doctype html>") and "&lt;b&gt;x&lt;/b&gt;" in page and "<b>x</b>" not in page
    b64 = page.split("data:image/png;base64,")[1].split("'")[0]
    assert base64.b64decode(b64) == T.render(res)["png"]
    for needle in ("Summary statistics", "Attribution by layer", "Reconciliation", "sharpe", "carry", "abc123", "Best 10 closed trades", "By exit reason", "identity"):
        assert needle in page
    assert "http://" not in page and "https://" not in page and "<script" not in page and "<link" not in page
    assert page == T.tearsheet(res, formats=("html",)).html  # deterministic


def test_paths_suffix_and_stem(tmp_path):
    res = forward_run()
    one = T.tearsheet(res, tmp_path / "a" / "sheet.png")
    assert [p.name for p in one.paths] == ["sheet.png"] and one.paths[0].read_bytes().startswith(PNG) and one.html is None
    many = T.tearsheet(res, tmp_path / "stem", formats=("html", "png", "pdf"))
    assert sorted(p.name for p in many.paths) == ["stem.html", "stem.pdf", "stem.png"]
    assert (tmp_path / "stem.html").read_text(encoding="utf8") == many.html
    nothing = T.tearsheet(res, formats=("png",))
    assert nothing.paths == () and nothing.figures["png"].startswith(PNG)
    with pytest.raises(ResultError):
        T.tearsheet(res, tmp_path / "x", formats=("svg",))
    via_method = res.tearsheet(tmp_path / "m.html")
    assert via_method.paths[0].name == "m.html"


def test_decimate_keeps_extremes_and_endpoints():
    n = 100_000
    y = np.sin(np.arange(n) / 50.0) + np.random.default_rng(0).normal(0, 0.01, n)
    y[12_345] = 5.0
    y[77_777] = -5.0
    x = np.arange(n)
    dx, dy = T.decimate(x, y)
    assert len(dy) <= 8_002 and dx[0] == 0 and dx[-1] == n - 1
    assert dy.max() == 5.0 and dy.min() == -5.0 and np.all(np.diff(dx) > 0)
    sx, sy = T.decimate(x[:100], y[:100])
    assert len(sy) == 100


def test_missing_matplotlib_gives_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    with pytest.raises(OptionalDependencyError, match="matplotlib"):
        T.render(synth_result([1.0, 2.0]))


def test_rendering_never_imports_pyplot():
    code = (
        "import sys; sys.path[:0]=['src','tests']\n"
        "from test_results_common import forward_run\n"
        "from pricebt.results import tearsheet as T\n"
        "T.render(forward_run(), formats=('png','pdf'))\n"
        "assert 'matplotlib.pyplot' not in sys.modules\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=100)
    assert r.returncode == 0, r.stderr


def test_import_does_not_load_matplotlib_and_io_has_no_module_level_pyarrow():
    """(pandas itself imports pyarrow when installed, so only our own module namespace can be checked for it.)"""
    code = ("import sys; sys.path.insert(0,'src'); import pricebt.results, pricebt.results.io as io; "
            "assert 'matplotlib' not in sys.modules and not hasattr(io, 'pyarrow')")
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).returncode == 0
