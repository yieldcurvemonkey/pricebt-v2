"""Config validation, doubt cycle 2 (C1 at load time, M1, M6) and the `pricebt tieout` command: a typo or a wrong type fails at LOAD with the path, never as a silent no-op, a zero
column or a run that dies at its first point."""
import copy

import pytest
import yaml

from pricebt.config import cli
from pricebt.config.loader import build
from pricebt.errors import ConfigError
from pricebt.tieout import run_tieout
from tieout_helpers import BASE, DIFFERENT, SAME

pytestmark = pytest.mark.core


def cfg(**bt):
    c = copy.deepcopy(BASE)
    c["backtest"].update(bt)
    return c


def refused(c, code, *needles):
    with pytest.raises(ConfigError) as e:
        build(c)
    assert e.value.code == code, (e.value.code, str(e.value))
    for n in needles:
        assert n in str(e.value), (n, str(e.value))


# ------------------------------------------------------------------ C1: the baseline needs its three measures, at load
def test_c1_the_baseline_names_the_instrument_and_the_measures_it_lacks():
    c = cfg(attribution={"layers": ["carry", "delta"], "cadence": "eod", "baseline": True})
    build(c)  # the toy forward binds dv01, gamma and rate
    c["instruments"]["fut"] = {"asset_class": "toy", "factory": "pricebt.testing.toys:future", "conventions": {"multiplier": 1.0}}  # the toy future binds no `rate`
    refused(c, "CFG-BASELINE", "fut", "rate")
    c["backtest"]["attribution"]["baseline"] = False
    build(c)  # without the baseline the same config is fine


# ------------------------------------------------------------------ M6: known keys, real booleans, real lists, known names
@pytest.mark.parametrize("att,needle", [({"baseine": True}, "baseine"), ({"layers": ["carry"], "cadence": "eod", "tol": 1e-6, "strictt": True}, "strictt")])
def test_m6_an_unknown_attribution_key_is_refused_with_its_path(att, needle):
    refused(cfg(attribution=att), "CFG-UNKNOWN-KEY", needle)


@pytest.mark.parametrize("key", ["baseline", "strict"])
def test_m6_a_string_where_a_boolean_belongs_is_refused_not_read_as_true(key):
    refused(cfg(attribution={"layers": ["carry"], key: "false"}), "CFG-TYPE", key, "true or false")


@pytest.mark.parametrize("key,value", [("measures", "dv01"), ("vector_measures", "delta_ladder"), ("record_signals", "x")])
def test_m6_a_bare_string_where_a_list_belongs_is_refused_not_read_letter_by_letter(key, value):
    refused(cfg(**{key: value}), "CFG-TYPE", key, "list")


def test_m6_audit_measures_must_be_a_list_of_known_measures():
    refused(cfg(audit={"measures": "dv01"}), "CFG-TYPE", "audit.measures")
    refused(cfg(audit={"measures": ["dv0l"]}), "CFG-REF", "dv0l", "did you mean 'dv01'")
    build(cfg(audit={"measures": ["dv01", "gamma", "rate"]}))
    build(cfg(audit={}))  # `audit: {}` is on with the default measures (an empty mapping is not falsy config)


@pytest.mark.parametrize("cadence", ["daily", "every_n:0", "every_n:x", "EOD", "every_n"])
def test_m6_a_bad_cadence_fails_at_load(cadence):
    refused(cfg(attribution={"layers": ["carry"], "cadence": cadence}), "CFG-TYPE", "cadence")


def test_m6_unknown_layer_and_measure_names_fail_at_load_with_a_suggestion():
    refused(cfg(attribution={"layers": ["carry", "delat"]}), "CFG-REF", "delat", "did you mean 'delta'")
    refused(cfg(measures=["dv01", "gama"]), "CFG-REF", "gama", "did you mean 'gamma'")
    refused(cfg(vector_measures=["nope"]), "CFG-REF", "nope")
    build(cfg(attribution={"layers": ["carry", "delta"]}, measures=["dv01", "gamma", "pv"]))


def test_m6_a_signal_is_recorded_and_resolved_under_one_name():
    c = cfg(record_signals=["book_dv01"])
    c["signals"] = {"book_dv01": {"type": "book_measure", "measure": "dv01", "name": "other_name"}}
    refused(c, "CFG-SIGNAL-NAME", "book_dv01", "other_name")
    c["signals"]["book_dv01"].pop("name")
    build(c)


# ------------------------------------------------------------------ M1: `tieout.tolerances`
def test_m1_the_tieout_key_is_accepted_validated_and_used():
    c = copy.deepcopy(BASE)
    c["tieout"] = {"tolerances": {"L1.pv": 1.0, "L2.*": 1.0, "L3.*": 1.0, "L4.*": 1.0, "L4.stats": 1.0}}
    build(c)  # a base that only says what it tolerates still builds and runs
    strict = run_tieout(BASE, {"a": [SAME], "b": [DIFFERENT]}, selftest=False)
    loose = run_tieout(c, {"a": [SAME], "b": [DIFFERENT]}, selftest=False)
    assert not strict.passed and loose.passed, "the tolerances declared in the base config decide the verdict"
    forced = run_tieout(c, {"a": [SAME], "b": [DIFFERENT]}, selftest=False, tolerances={"L1.pv": 1e-12, "L4.*": 1.0, "L3.*": 1.0, "L4.stats": 1.0})
    assert not forced.passed, "and an explicit argument beats the config"


@pytest.mark.parametrize("tie,needle", [({"tolerance": {}}, "tolerance"), ({"tolerances": "tight"}, "mapping"), ({"tolerances": {"L1.pv": {"rel": 1e-9, "expected": "false"}}}, "expected"),
                                        ("x", "mapping"), ({"tolerances": {"L1.pv": {"floor": 1.0}}}, "rel")])
def test_m1_an_invalid_tieout_block_fails_at_load(tie, needle):
    c = copy.deepcopy(BASE)
    c["tieout"] = tie
    with pytest.raises(ConfigError) as e:
        build(c)
    assert needle in str(e.value)


# ------------------------------------------------------------------ the command
def _write(tmp_path):
    (tmp_path / "base.yaml").write_text(yaml.safe_dump(BASE), encoding="utf8")
    (tmp_path / "same.yaml").write_text(yaml.safe_dump(SAME), encoding="utf8")
    (tmp_path / "different.yaml").write_text(yaml.safe_dump(DIFFERENT), encoding="utf8")
    return tmp_path


def test_the_tieout_command_passes_on_identical_stacks_and_fails_on_a_different_one(tmp_path, capsys):
    d = _write(tmp_path)
    base = str(d / "base.yaml")
    assert cli.main(["tieout", base, "--stack", f"one={d / 'same.yaml'}", "--stack", f"two={d / 'same.yaml'}", "--out", str(d / "out")]) == 0
    out = capsys.readouterr().out
    assert "TIE-OUT PASSED" in out and "one_vs_two" in out and (d / "out").exists() and "harness self-test: passed" in out
    assert cli.main(["tieout", base, "--stack", f"one={d / 'same.yaml'}", "--stack", f"two={d / 'different.yaml'}", "--no-selftest"]) == 1
    out = capsys.readouterr().out
    assert "TIE-OUT FAILED" in out and "L1.pv" in out and "harness self-test: skipped" in out


@pytest.mark.parametrize("stacks,needle", [(["one"], "NAME=OVERLAY"), (["=x.yaml", "b=y.yaml"], "NAME=OVERLAY"), (["a=x.yaml", "a=y.yaml"], "twice")])
def test_the_tieout_command_refuses_a_malformed_stack_argument(tmp_path, capsys, stacks, needle):
    args = ["tieout", str(_write(tmp_path) / "base.yaml")]
    for s in stacks:
        args += ["--stack", s]
    assert cli.main(args) == 2
    assert needle in capsys.readouterr().err


def test_the_tieout_command_needs_two_stacks_and_names_a_reference_that_does_not_exist(tmp_path, capsys):
    d = _write(tmp_path)
    assert cli.main(["tieout", str(d / "base.yaml"), "--stack", f"one={d / 'same.yaml'}"]) == 2
    assert "two stacks" in capsys.readouterr().err
    assert cli.main(["tieout", str(d / "base.yaml"), "--stack", f"a={d / 'same.yaml'}", "--stack", f"b={d / 'same.yaml'}", "--reference", "zzz"]) == 2
    assert "zzz" in capsys.readouterr().err


# ================================================================== doubt cycle 3 (loader): R8 / R19 kit extension names, R9 values, R23 wrong types
from pricebt.contracts.spec import Kit  # noqa: E402
from pricebt.testing.toys import FORWARD_BIND, TOY_SCHEMA, ToyForward, _ToyFactory  # noqa: E402

EXT = Kit(factory=_ToyFactory(ToyForward), asset_class="toy", default_bind={**FORWARD_BIND, "x.carry2": FORWARD_BIND["carry"], "x.dv01b": FORWARD_BIND["dv01"]}, cls=ToyForward, schema=TOY_SCHEMA,
          extra={"x.carry2": "layer", "x.dv01b": "measure"}, doc="toy forward with an extension layer and an extension measure")


def ext_cfg(**bt):
    c = cfg(**bt)
    c["registry"]["allow"] = ["pricebt", "tieout_helpers", "test_config_review"]
    c["instruments"]["fwd"]["factory"] = "test_config_review:EXT"
    return c


def test_r8_a_kit_extension_measure_is_a_known_name_for_measures_and_the_audit():
    """the shipped swap kits bind `dv01_zero` / `gamma_zero` as extension measures: valid at run time, and the loader used to refuse them (it looked only at the schema's names)."""
    assert build(ext_cfg(measures=["dv01", "x.dv01b"])).settings.measures == ("dv01", "x.dv01b")
    assert build(ext_cfg(audit={"measures": ["x.dv01b"]})).settings.audit_measures == ("x.dv01b",)
    refused(ext_cfg(measures=["x.dv01c"]), "CFG-REF", "x.dv01c", "did you mean 'x.dv01b'")


def test_r8_the_shipped_swap_kits_extension_measures_load():
    from test_tieout_selftest import NUMERIC  # a reference-stack swap book

    from pricebt.testing import refstack as R
    assert R.swap.extra == {"dv01_zero": "measure", "gamma_zero": "measure"}
    c = copy.deepcopy(NUMERIC)
    c["backtest"].update(measures=["dv01", "dv01_zero"], audit={"measures": ["gamma_zero"]})
    s = build(c).settings
    assert s.measures == ("dv01", "dv01_zero") and s.audit_measures == ("gamma_zero",)


def test_r19_an_extension_layer_is_a_known_layer_engine_wide_and_a_measure_is_not_a_layer():
    b = build(ext_cfg(attribution={"layers": ["carry", "delta", "x.carry2"], "cadence": "eod"}))
    assert b.settings.layers == ("carry", "delta", "x.carry2")
    refused(ext_cfg(attribution={"layers": ["carry", "x.dv01b"], "cadence": "eod"}), "CFG-REF", "x.dv01b")  # an extension MEASURE is not a layer
    refused(ext_cfg(vector_measures=["x.dv01b"]), "CFG-REF", "x.dv01b")  # and a scalar one is not a vector


@pytest.mark.parametrize("key,value", [("on_error", "Raise"), ("on_error", "recrod"), ("on_error", "skip "), ("fill_price", "nxt"), ("exit_policy", "next-grid"), ("fill_lag", 1.9), ("fill_lag", -3),
                                       ("fill_lag", True), ("fill_lag", "x"), ("initial_capital", "abc"), ("initial_capital", True), ("initial_capital", float("nan"))])
def test_r9_a_typo_or_a_wrong_type_in_a_backtest_scalar_fails_at_load_with_its_path(key, value):
    refused(cfg(**{key: value}), "CFG-TYPE", f"backtest.{key}")


@pytest.mark.parametrize("key,value", [("on_error", "raise"), ("on_error", "record"), ("on_error", "skip"), ("fill_price", "decision"), ("fill_price", "next"), ("exit_policy", "own_time"),
                                       ("exit_policy", "next_grid"), ("fill_lag", 0), ("fill_lag", 2), ("initial_capital", 1e6), ("initial_capital", 0)])
def test_r9_the_documented_values_still_load(key, value):
    assert getattr(build(cfg(**{key: value})).settings, key) == value


def test_r9_progress_show_is_a_real_boolean_and_progress_a_mapping():
    refused(cfg(progress={"show": "false"}), "CFG-TYPE", "backtest.progress.show", "true or false")
    refused(cfg(progress="yes"), "CFG-TYPE", "backtest.progress")
    assert build(cfg(progress={"show": False})).settings.show_progress is False


def test_r9_attribution_tol_is_a_non_negative_finite_number_and_the_block_a_mapping():
    for bad in ("x", -1.0, float("inf"), True):
        refused(cfg(attribution={"layers": ["carry"], "tol": bad}), "CFG-TYPE", "backtest.attribution.tol")
    refused(cfg(attribution=["layers"]), "CFG-TYPE", "backtest.attribution")


def test_r9_an_empty_audit_mapping_is_on_and_false_or_absent_is_off():
    """`audit: {}` was silently OFF (`bool({})`): the existing test said 'on' in a comment and asserted nothing."""
    assert build(cfg(audit={})).settings.audit is True
    assert build(cfg(audit=True)).settings.audit is True
    assert build(cfg(audit={"measures": ["dv01"]})).settings.audit is True
    assert build(cfg(audit=False)).settings.audit is False
    assert build(cfg()).settings.audit is False


@pytest.mark.parametrize("mut,path", [(lambda c: c.update(instruments=["fwd"]), "instruments"), (lambda c: c.update(backtest="x"), "backtest"), (lambda c: c.update(trades=["t"]), "trades"),
                                      (lambda c: c.update(signals="s"), "signals"), (lambda c: c.update(strategy="s"), "strategy"), (lambda c: c.update(market="m"), "market")])
def test_r23_a_top_level_block_of_the_wrong_type_fails_at_load_with_its_path(mut, path):
    c = copy.deepcopy(BASE)
    mut(c)
    with pytest.raises(ConfigError) as e:
        build(c)
    assert e.value.code == "CFG-TYPE" and path in str(e.value), (e.value.code, str(e.value))


def test_r23_the_cli_reports_a_raw_failure_as_an_error_not_as_a_failed_tieout(tmp_path, capsys):
    """exit 1 means 'TIE-OUT FAILED', 2 means 'error': an exception that is not a PricebtError must not borrow the first."""
    d = _write(tmp_path)
    bad = yaml.safe_load((d / "base.yaml").read_text())
    bad["backtest"]["initial_capital"] = "abc"
    (d / "bad.yaml").write_text(yaml.safe_dump(bad))
    assert cli.main(["tieout", str(d / "bad.yaml"), "--stack", f"a={d / 'same.yaml'}", "--stack", f"b={d / 'same.yaml'}"]) == 2
    assert "initial_capital" in capsys.readouterr().err
