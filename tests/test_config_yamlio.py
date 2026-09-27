import datetime as dt
import json

import pytest

from pricebt.config import yamlio
from pricebt.errors import ConfigError

pytestmark = pytest.mark.core


def test_spine_12_10_scalar_literals():
    doc = yamlio.loads("a: 1e7\nb: 1.0e7\nc: 1e-4\nd: 1_000\ne: no\nf: NO\ng: on\nh: off\ni: true\nj: False\nk: 1:30\nl: 09:30\nm: 2025-01-02\nn: .5\no: -2.5e3\np: 0\nq: 012")
    assert doc["a"] == 1e7 and isinstance(doc["a"], float)
    assert doc["b"] == 1e7 and doc["c"] == 1e-4
    assert doc["d"] == 1000 and isinstance(doc["d"], int)
    assert doc["e"] == "no" and doc["f"] == "NO" and doc["g"] == "on" and doc["h"] == "off"  # YAML 1.1 bools stay strings
    assert doc["i"] is True and doc["j"] is False
    assert doc["k"] == "1:30" and doc["l"] == "09:30"  # no sexagesimal
    assert doc["m"] == dt.date(2025, 1, 2)
    assert doc["n"] == 0.5 and doc["o"] == -2500.0 and doc["p"] == 0
    assert doc["q"] == "012"  # leading zero is not a decimal int


def test_control_stock_pyyaml_gets_these_wrong():
    import yaml

    stock = yaml.safe_load("a: 1e7\ne: no\nk: 1:30")
    assert isinstance(stock["a"], str) and stock["e"] is False and stock["k"] == 90  # the defects the hardened loader fixes


def test_json_literals_and_autodetect():
    assert yamlio.loads(json.dumps({"x": 1e7, "y": [1, 2]})) == {"x": 1e7, "y": [1, 2]}
    assert yamlio.loads("[1, 2]") == [1, 2]


def test_duplicate_keys_error_with_position():
    with pytest.raises(ConfigError) as e:
        yamlio.loads("a: 1\nb: 2\na: 3\n", name="cfg.yaml")
    assert "duplicate mapping key 'a'" in str(e.value) and "cfg.yaml:3" in str(e.value)


def test_env_interpolation():
    assert yamlio.interpolate({"a": "x-${V}-y", "b": ["${W:-dflt}"]}, {"V": "1"}) == {"a": "x-1-y", "b": ["dflt"]}
    with pytest.raises(ConfigError):
        yamlio.interpolate("${MISSING}", {})


def test_deep_merge_and_overrides():
    base = {"a": {"x": 1, "y": 2}, "l": [1, 2]}
    assert yamlio.deep_merge(base, {"a": {"y": 3}, "l": [9]}) == {"a": {"x": 1, "y": 3}, "l": [9]}
    out = yamlio.apply_overrides({"a": {"b": [1, {"c": 2}]}}, ["a.b.1.c=5", "a.new=1e3", "z.q=true"])
    assert out["a"]["b"][1]["c"] == 5 and out["a"]["new"] == 1000.0 and out["z"]["q"] is True


def test_extends_and_cycle(tmp_path):
    (tmp_path / "base.yaml").write_text("backtest: {fill_lag: 0, name: base}\nk: 1\n")
    (tmp_path / "child.yaml").write_text("extends: base.yaml\nbacktest: {name: child}\n")
    got = yamlio.load_file(tmp_path / "child.yaml")
    assert got == {"backtest": {"fill_lag": 0, "name": "child"}, "k": 1}
    (tmp_path / "a.yaml").write_text("extends: b.yaml\n")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n")
    with pytest.raises(ConfigError):
        yamlio.load_file(tmp_path / "a.yaml")
