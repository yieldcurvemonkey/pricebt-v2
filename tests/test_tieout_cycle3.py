"""Tie-out and CLI, doubt cycle 3 (T1-T8 and the floor trade-off): the harness must be able to FAIL (X5), report a difference instead of omitting it (X2, X4), validate what it is
given, and say what each stack owns. Every test names the reviewer's finding (R<n>) it pins."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from pricebt.config import cli
from pricebt.errors import ConfigError
from pricebt.testing import refstack as R
from pricebt.tieout import DEFAULT_TOLERANCES, Tol, Tolerances, compare, run_stack, run_tieout, to_markdown
from test_tieout_selftest import NUMERIC, REF
from tieout_helpers import BASE, DIFFERENT, SAME

pytestmark = pytest.mark.core

ROOT = Path(__file__).resolve().parents[1]
LADDER = ("dv01", "delta_ladder")


# ------------------------------------------------------------------ fixtures: a swap run with a delta ladder (one column per bucket) and a toy run
def _ladder_base(notional=None):
    b = copy.deepcopy(NUMERIC)
    b["backtest"]["grid"] = {"start": "2024-06-03", "end": "2024-06-14", "freq": "1b"}
    b["backtest"]["vector_measures"] = []  # the run RECORDS no ladder: only the audit asks for it, so a broken ladder cannot abort the run
    b["strategy"]["triggers"][0]["actions"] = b["strategy"]["triggers"][0]["actions"][:1]
    if notional is not None:
        b["strategy"]["triggers"][0]["actions"][0]["priceables"]["terms"]["notional"] = notional
    return b


def _ladder_stack(**changes):
    """An overlay of the reference stack whose delta_ladder binding is changed (a hidden unit conversion, a wrong `keys` declaration ...)."""
    lad = {**R.SWAP_BIND["delta_ladder"], **changes}
    return {**REF, "instruments": {"ois": {"factory": "pricebt.testing.refstack:swap", "bind": {"delta_ladder": lad}}}}


@pytest.fixture(scope="module")
def ladder_ref():
    return run_stack(_ladder_base(), [REF], audit_measures=LADDER, baseline=False)[1]


@pytest.fixture(scope="module")
def toy_ref():
    return run_stack(BASE, [SAME])[1]


def _drop_buckets(res, tenors):
    out = copy.deepcopy(res)
    m = out.record.audit["marks"]
    out.record.audit["marks"] = m.drop(columns=[c for c in m.columns if c.startswith("measure_delta_ladder.") and c.split(".")[1] in tenors])
    return out


def _ladder_rows(rep):
    return {r.quantity: r for r in rep.rows if r.level == "L2" and r.quantity.startswith("delta_ladder.")}


# ------------------------------------------------------------------ T1: a measure column present in ONE run only is a failing structure row (r12, r18)
def test_t1_a_ladder_that_lacks_buckets_in_one_run_fails_and_names_them(ladder_ref):
    """r12: 5 of 11 buckets missing used to pass with 6 rows compared: the missing buckets must be rows of their own."""
    gone = ("7Y", "10Y", "15Y", "20Y", "30Y")
    short = _drop_buckets(ladder_ref, gone)
    for a, b in ((ladder_ref, short), (short, ladder_ref)):  # whichever side lacks them
        rep = compare(a, b, names=("a", "b"))
        rows = _ladder_rows(rep)
        assert not rep.passed and len(rows) == 11
        assert {k for k, r in rows.items() if r.status == "structure"} == {f"delta_ladder.{t}" for t in gone}
        assert all(r.status == "exact" for k, r in rows.items() if k.split(".")[1] not in gone), "the buckets both runs have still compare exactly"
        assert "one run" in rows["delta_ladder.7Y"].note or "only" in rows["delta_ladder.7Y"].note


def test_t1_identical_ladders_pass_with_one_exact_row_per_bucket(ladder_ref):
    """The known answer for the check above: the same ladder twice is 11 exact rows and a pass (X5a), so the failing case is not just 'ladders always fail'."""
    rep = compare(ladder_ref, copy.deepcopy(ladder_ref), names=("a", "b"))
    rows = _ladder_rows(rep)
    assert rep.passed and len(rows) == 11 and all(r.status == "exact" for r in rows.values())


def test_t1_a_stack_whose_ladder_is_broken_for_every_position_fails_end_to_end(ladder_ref):
    """r18: the `keys` declared by the binding are wrong, so the measure raises for every position and the audit records a NaN scalar column: no ladder row, and a pass."""
    _, broken = run_stack(_ladder_base(), [_ladder_stack(keys=["3M"])], audit_measures=LADDER, baseline=False)
    assert not [c for c in broken.record.audit["marks"].columns if c.startswith("measure_delta_ladder.")], "the broken stack has no bucket at all"
    rep = compare(ladder_ref, broken, names=("ref", "broken"))
    assert not rep.passed
    assert {r.status for r in _ladder_rows(rep).values()} == {"structure"}, "every bucket the reference has is reported as missing from the broken stack"


def test_t1_a_scalar_measure_missing_from_one_run_fails(toy_ref):
    """A whole (scalar) measure column absent from one run: reported, not skipped."""
    other = copy.deepcopy(toy_ref)
    other.record.audit["marks"] = other.record.audit["marks"].drop(columns=["measure_dv01"])
    rep = compare(toy_ref, other, names=("a", "b"))
    row = [r for r in rep.rows if r.level == "L2" and r.quantity == "dv01"][0]
    assert row.status == "structure" and not rep.passed


def test_t1_a_measure_that_is_nan_in_one_run_and_absent_from_the_other_stays_info():
    """The rule's other side: nothing to compare on either side (`vega` is bound by no traded instrument) is `info`, as before, whether the column is NaN or absent."""
    base = copy.deepcopy(BASE)
    base["instruments"]["opt"] = {"asset_class": "toy", "factory": "pricebt.testing.toys:option", "conventions": {"strike": 4.0, "expiry": "2024-06-28", "notional": 1e3}}
    a = run_stack(base, [SAME], audit_measures=("dv01", "vega"))[1]
    assert a.record.audit["marks"]["measure_vega"].isna().all()
    other = copy.deepcopy(a)
    other.record.audit["marks"] = other.record.audit["marks"].drop(columns=["measure_vega"])
    for x, y in ((a, other), (other, a)):
        rep = compare(x, y, names=("a", "b"))
        row = [r for r in rep.rows if r.level == "L2" and r.quantity == "vega"][0]
        assert row.status == "info" and rep.passed


# ------------------------------------------------------------------ T2: run_tieout validates the config's declarations and an explicit argument wins (r15)
@pytest.mark.parametrize("tie", [["x"], {"tolerances": "tight"}, {"tolerances": ["L1.pv"]}, {"tolerances": {"L1.pv": {"rel": "abc"}}}, {"tolerances": {"L9.pv": 1e-3}}])
def test_t2_run_tieout_refuses_an_invalid_tieout_block_with_a_config_error(tie):
    """r15: `tieout: ['x']` was a raw AttributeError, `{tolerances: 'tight'}` a raw ValueError (the loader's check was bypassed)."""
    cfg = copy.deepcopy(BASE)
    cfg["tieout"] = tie
    with pytest.raises(ConfigError):
        run_tieout(cfg, {"a": [SAME], "b": [SAME]}, selftest=False)


def test_t2_an_invalid_explicit_argument_is_a_config_error_too():
    """r15: the explicit argument goes through the same gate as the config's block, not a raw error from the constructor or the merge."""
    for bad in ({"L1.pv": "tight"}, {"L9.pv": 1e-3}, {"L1.pv": {"rel": float("nan")}}):
        with pytest.raises(ConfigError):
            run_tieout(BASE, {"a": [SAME], "b": [SAME]}, tolerances=bad, selftest=False)


@pytest.mark.parametrize("declared, explicit, key, expected_rel", [
    ({"L1.pv.toy": 1e-1}, {"L1.pv": 1e-9}, ("L1", "pv", "toy"), 1e-9),  # r15: `L1.pv` covers `L1.pv.<class>`
    ({"L1.pv.toy": 1e-1, "L1.cash": 1e-2}, {"L1.*": 1e-9}, ("L1", "pv", "toy"), 1e-9),  # a wildcard covers `L1.<q>.<class>` ...
    ({"L1.pv.toy": 1e-1, "L1.cash": 1e-2}, {"L1.*": 1e-9}, ("L1", "cash", "toy"), 1e-9),  # ... and `L1.<q>`
    ({"L1.pv.toy": 1e-1, "L2.dv01": 1e-2}, {"L1.*": 1e-9}, ("L2", "dv01", "toy"), 1e-2),  # but not another level
    ({"L1.pv": 1e-1}, {"L1.pv.toy": 1e-9}, ("L1", "pv", "bond"), 1e-1),  # a more specific explicit key covers only itself
    ({"L1.*": 1e-1}, {"L1.pv": 1e-9}, ("L1", "cash", "toy"), 1e-1),  # a more specific explicit key does not remove the wildcard
    ({"L1.pv.toy": 1e-1, "L1.cash": 1e-2}, {"L1.pv": 1e-9}, ("L1", "cash", "toy"), 1e-2),  # nor a sibling quantity
])
def test_t2_an_explicit_tolerance_removes_every_declared_key_it_covers(declared, explicit, key, expected_rel):
    """r15: the covering rule (`L1.pv` covers `L1.pv.<class>`, `L1.*` covers `L1.<q>[.<class>]`) and what it must NOT cover."""
    from pricebt.tieout.tolerances import merge_declarations

    assert Tolerances(merge_declarations(declared, explicit)).for_(*key).rel == expected_rel


def test_t2_an_explicit_argument_wins_in_a_real_run_against_a_more_specific_declared_key():
    """r15 end to end: config `L1.pv.toy: 1.0` (would call the strike shift noise), explicit `L1.pv: 1e-9`: the explicit value decides."""
    cfg = copy.deepcopy(BASE)
    cfg["tieout"] = {"tolerances": {"L1.pv.toy": 1.0, "L4.*": 1.0, "L3.*": 1.0}}
    loose = run_tieout(cfg, {"a": [SAME], "b": [DIFFERENT]}, selftest=False).reports["a_vs_b"]
    assert [r.status for r in loose.rows if r.level == "L1" and r.quantity == "pv"] == ["noise"], "the config alone: the difference (0.5 relative) is within 1.0"
    tight = run_tieout(cfg, {"a": [SAME], "b": [DIFFERENT]}, tolerances={"L1.pv": 1e-9}, selftest=False).reports["a_vs_b"]
    row = [r for r in tight.rows if r.level == "L1" and r.quantity == "pv"][0]
    assert row.tol_rel == 1e-9 and row.status == "exceeds"


# ------------------------------------------------------------------ T3: a tolerance declaration is validated (unknown level/quantity, malformed value) and never a raw exception
def _validate(mapping):
    from pricebt.tieout.tolerances import validate_declaration

    return validate_declaration(mapping, "tieout.tolerances")


@pytest.mark.parametrize("key", [
    "L9.pv", "L1.pvv", "L1.pv.toy.extra", "L4.equty", "L0.resolved_terms", "pv", "L1", "L1.", ".pv", "L1.*.toy", "L2.dv01.1Y", "L3.carry/unitt", "L1.pv/unit", "l1.pv",
    "L4.trade_ledger_structure",
])
def test_t3_an_unknown_level_quantity_or_shape_of_key_is_refused_with_its_path(key):
    """R9/R23: a typo'd key was a silent no-op (the tolerance was never looked up); it is a CFG-TIEOUT naming the path, in the validator and in the constructor."""
    from pricebt.tieout.tolerances import validate_declaration

    with pytest.raises(ConfigError) as e:
        validate_declaration({key: 1e-3}, "tieout.tolerances")
    assert e.value.code == "CFG-TIEOUT" and "tieout.tolerances" in str(e.value) and key in str(e.value)
    with pytest.raises(ConfigError):
        Tolerances({key: 1e-3})  # the constructor is the same gate: a typo'd key is never a silent no-op


def test_t3_l0_has_no_tolerance_and_says_why():
    """L0 rows are exact by definition, so a declaration for L0 would be a silent no-op: it says so instead of `unknown level`."""
    with pytest.raises(ConfigError, match="exact by definition"):
        _validate({"L0.resolved_terms": 1e-3})


def test_t2_the_declared_tolerances_of_a_loaded_config_are_validated_in_one_call():
    """the one call `run_tieout` uses to read `tieout.tolerances` of a loaded config (and the lead may use): absent is {}, anything malformed is a ConfigError."""
    from pricebt.tieout.tolerances import declared_tolerances

    for absent in ({}, {"tieout": None}, {"tieout": {}}, {"tieout": {"tolerances": None}}):
        assert declared_tolerances(absent) == {}
    assert declared_tolerances({"tieout": {"tolerances": {"L1.pv": 1e-3}}}) == {"L1.pv": 1e-3}
    for bad in (["x"], "x", {"tolerances": "tight"}, {"tolerances": {"L9.pv": 1e-3}}, {"tolerances": {"L1.pv": {"rel": "abc"}}}):
        with pytest.raises(ConfigError):
            declared_tolerances({"tieout": bad})


def test_t3_a_misspelt_quantity_says_what_was_meant():
    """the message suggests the nearest quantity (`L1.pvv` -> `L1.pv`)."""
    with pytest.raises(ConfigError, match="L1.pv"):
        _validate({"L1.pvv": 1e-3})


@pytest.mark.parametrize("value", [
    "tight", None, [1e-3], True, False, {}, {"floor": 1.0}, {"rel": "abc"}, {"rel": None}, {"rel": float("nan")}, {"rel": float("inf")}, {"rel": -1e-3}, {"rel": True},
    {"rel": 1e-3, "floor": "x"}, {"rel": 1e-3, "floor": float("nan")}, {"rel": 1e-3, "floor": float("inf")}, {"rel": 1e-3, "floor": -1.0}, {"rel": 1e-3, "floor": True},
    {"rel": 1e-3, "floor": 0.0}, {"rel": 1e-3, "expected": "yes"}, {"rel": 1e-3, "expected": 1}, {"rel": 1e-3, "strict": "no"}, {"rel": 1e-3, "strict": 0}, {"rel": 1e-3, "extra": 1},
    {"rel": 1e-3, "reason": ""}, {"rel": 1e-3, "reason": "   "}, {"rel": 1e-3, "reason": 5}, {"rel": 1e-3, "reason": None}, float("nan"), float("inf"), -1e-3,
])
def test_t3_a_malformed_value_is_a_config_error_never_a_raw_exception(value):
    """r10/r15: `{rel: 'abc'}` raised a raw ValueError out of float()."""
    from pricebt.tieout.tolerances import validate_declaration

    with pytest.raises(ConfigError) as e:
        validate_declaration({"L1.pv": value}, "tieout.tolerances")
    assert e.value.code == "CFG-TIEOUT" and "tieout.tolerances.L1.pv" in str(e.value)
    with pytest.raises(ConfigError):
        Tolerances({"L1.pv": value})  # the constructor is the same gate


def test_t3_a_declaration_that_is_not_a_mapping_is_refused():
    """a declaration block that is not a mapping is refused before its entries are read."""
    from pricebt.tieout.tolerances import validate_declaration

    for bad in ("tight", ["L1.pv"], 5):
        with pytest.raises(ConfigError, match="mapping"):
            validate_declaration(bad, "tieout.tolerances")


@pytest.mark.parametrize("mapping", [
    {}, {"L1.pv": 1e-3}, {"L1.pv": 1}, {"L1.pv": np.float64(1e-3)}, {"L1.pv.swap": {"rel": 1e-3, "floor": 100.0}}, {"L1.*": 1e-2}, {"L1.quantity": {"rel": 0.0, "floor": 1e-12, "strict": True}},
    {"L2.dv01": 1e-3, "L2.delta_ladder": {"rel": 1e-3, "floor": 100.0}, "L2.vega": 1e-2, "L2.gamma.swap": 1e-2, "L2.*": 1e-3},
    {"L3.carry": 1e-3, "L3.carry/unit": 1e-2, "L3.tay_delta": 1e-3, "L3.some_extension_layer": 1e-2, "L3.unexplained.swap": 1e-2, "L3.*": 1e-3},
    {"L4.equity": 1e-5, "L4.trade_cash": 1e-8, "L4.stats_ratios": 5e-2, "L4.positions_value": 1e-5, "L4.*": 1e-5},
    {"L1.pv": {"rel": 0, "expected": True, "strict": False, "reason": "carry is forwards-realised in one stack and accrual-net in the other (X6)"}},
    {"L1.pv": Tol(1e-3, 2.0, True)},
])
def test_t3_the_shipped_shapes_and_the_extension_points_are_accepted(mapping):
    """known answers for the other side: every legal shape (numbers, mappings with every field, wildcards, per-class, measure and layer names) is accepted."""
    _validate(mapping)
    Tolerances(mapping)


def test_t3_every_shipped_default_key_is_a_valid_declaration():
    """the shipped table itself is a valid declaration (keys and values), so the validator cannot be narrower than the defaults."""
    _validate({k: {"rel": t.rel, "floor": t.floor} for k, t in DEFAULT_TOLERANCES.items()})
    _validate(dict(DEFAULT_TOLERANCES))


def test_t3_every_quantity_the_harness_emits_is_a_valid_declaration_key(ladder_ref, toy_ref):
    """The known set must be what the harness ACTUALLY compares: a key built from every row of real reports (ladder buckets, per-unit layers, baseline layers, statistics) validates."""
    keys = set()
    for res in (ladder_ref, toy_ref):
        for r in compare(res, copy.deepcopy(res), names=("a", "b")).rows:
            if r.tol_rel or r.tol_floor:
                q = r.quantity.split(".")[0] if r.level == "L2" else r.quantity  # a ladder row is `<measure>.<bucket>`, looked up as `<measure>`
                keys |= {f"{r.level}.{q}", *([f"{r.level}.{q}.{r.asset_class}"] if r.asset_class else [])}
    assert {"L1.pv", "L1.pv.swap", "L2.delta_ladder", "L2.dv01", "L3.carry/unit", "L3.tay_delta", "L4.equity", "L4.stats", "L1.quantity.toy"} <= keys
    _validate({k: 1e-3 for k in keys})


def test_t3_the_shipped_precedence_rules_are_unchanged():
    """pin (passes before and after): the precedence rules of `for_` are kept by the validation change."""
    t = Tolerances({"L1.pv": 1e-9, "L1.pv.swap": {"rel": 1e-3, "floor": 100.0}, "L3.carry": {"rel": 0.5, "expected": True}, "L4.*": 1e-2})
    assert t.for_("L1", "pv", "bond").rel == 1e-9 and t.for_("L1", "pv", "swap") == Tol(1e-3, 100.0) and t.for_("L3", "carry").expected is True
    assert t.for_("L4", "trade_pv").rel == 1e-2 and t.for_("L1", "quantity", "toy").rel == 0.0 and t.for_("L1", "cash").rel == DEFAULT_TOLERANCES["L1.cash"].rel


def test_t3_a_per_unit_layer_can_be_declared_apart_from_the_layer_and_falls_back_to_it():
    """`L3.<layer>/unit` is a legal key, so it must be effective: its own declaration first, then the layer's."""
    t = Tolerances({"L3.carry": 1e-2, "L3.delta/unit": 1e-1, "L3.delta": 1e-4})
    assert t.for_("L3", "carry/unit").rel == 1e-2, "no `carry/unit` declaration: the layer's own"
    assert t.for_("L3", "delta/unit").rel == 1e-1 and t.for_("L3", "delta").rel == 1e-4


def test_t3_a_per_unit_declaration_reaches_the_per_unit_rows_of_a_real_report(toy_ref):
    """the per-unit declaration above reaches the `<layer>/unit` rows of a real comparison (not just the lookup)."""
    b = copy.deepcopy(toy_ref)
    b.record.audit["layers"]["amount"] = b.record.audit["layers"]["amount"] * 1.01
    rep = compare(toy_ref, b, names=("a", "b"), tolerances=Tolerances({"L3.delta/unit": 1e-3, "L3.*": 1e-9}))
    by = {r.quantity: r for r in rep.rows if r.level == "L3"}
    assert by["delta/unit"].tol_rel == 1e-3 and by["delta"].tol_rel == 1e-9


# ------------------------------------------------------------------ the lead's addition: a declared `reason` is kept, validated and shown
def test_a_declared_reason_is_kept_and_shown_in_the_tolerances_used_table(toy_ref):
    """lead's addition (spec 11.3: no tolerance widened without a written reason): a declared reason is kept and printed, a default's is empty."""
    t = Tolerances({"L1.pv": {"rel": 1e-9, "reason": "the two stacks fix the strike on different dates"}})
    assert t.for_("L1", "pv").reason == "the two stacks fix the strike on different dates" and t.for_("L1", "cash").reason == ""
    rep = compare(toy_ref, copy.deepcopy(toy_ref), names=("a", "b"), tolerances=t)
    assert rep.tolerances["L1.pv.toy"]["reason"] == "the two stacks fix the strike on different dates" and rep.tolerances["L1.cash.toy"]["reason"] == ""
    md = to_markdown(rep)
    assert "| key | rel | floor | floor unit | expected | reason |" in md
    line = [ln for ln in md.splitlines() if ln.startswith("| L1.pv.toy")][0]
    assert "the two stacks fix the strike on different dates" in line
    assert [ln for ln in md.splitlines() if ln.startswith("| L1.cash.toy")][0].rstrip().endswith("|  |"), "a shipped default has an empty reason"


@pytest.mark.parametrize("reason", ["", "  ", 5, None, ["because"]])
def test_a_reason_that_is_not_a_non_empty_string_is_a_config_error(reason):
    """lead's addition: a missing, blank or non-string reason is a CFG-TIEOUT (the message names `non-empty string`)."""
    with pytest.raises(ConfigError, match="non-empty string"):
        Tolerances({"L1.pv": {"rel": 1e-9, "reason": reason}})


# ------------------------------------------------------------------ T4: the CLI never lets a raw exception become the documented "tie-out FAILED" status (r10)
def test_t4_a_stack_name_is_stripped_before_the_duplicate_check():
    """r10: `['a=x', ' a=y']` silently dropped a stack because the duplicate check ran before the strip."""
    with pytest.raises(ConfigError, match="twice"):
        cli._named_stacks(["a=x.yaml", " a=y.yaml"])
    with pytest.raises(ConfigError, match="twice"):
        cli._named_stacks([" a =x.yaml", "a=y.yaml"])
    assert cli._named_stacks([" a = x.yaml , y.yaml", "b=z.yaml"]) == {"a": ["x.yaml", "y.yaml"], "b": ["z.yaml"]}


def _cli_files(tmp_path, base=BASE):
    (tmp_path / "base.yaml").write_text(yaml.safe_dump(base), encoding="utf8")
    (tmp_path / "same.yaml").write_text(yaml.safe_dump(SAME), encoding="utf8")
    return tmp_path


@pytest.mark.parametrize("argv_of", [
    lambda d: ["tieout", str(d / "base.yaml"), "--stack", f"a={d / 'same.yaml'}", "--stack", f"b={d / 'same.yaml'}"],
    lambda d: ["validate", str(d / "base.yaml")],
    lambda d: ["describe", str(d / "base.yaml")],
    lambda d: ["run", str(d / "base.yaml"), "--no-progress"],
    lambda d: ["run", str(d / "base.yaml"), "--dry-run"],
])
def test_t4_a_raw_exception_in_any_subcommand_is_exit_2_with_its_type_and_message(tmp_path, capsys, monkeypatch, argv_of):
    """r10: a raw ValueError escaped `main`, the interpreter exited 1 = 'TIE-OUT FAILED'. The documented error status is 2 for every subcommand."""
    from pricebt import api
    import pricebt.tieout

    def boom(*a, **kw):
        raise ValueError("boom from the library")

    monkeypatch.setattr(pricebt.tieout, "run_tieout", boom)
    monkeypatch.setattr(api, "build", boom)
    monkeypatch.setattr(api, "run", boom)
    d = _cli_files(tmp_path)
    assert cli.main(argv_of(d)) == 2
    err = capsys.readouterr().err
    assert "error: [ValueError] boom from the library" in err


def test_t4_a_pricebt_error_still_prints_its_own_code_and_exits_2(tmp_path, capsys):
    """the documented error status and the printed code are kept for the errors pricebt raises itself (`[TieoutError]`, `[CLI]`)."""
    d = _cli_files(tmp_path)
    assert cli.main(["tieout", str(d / "base.yaml"), "--stack", f"a={d / 'same.yaml'}", "--stack", f"b={d / 'same.yaml'}", "--reference", "zzz"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: [TieoutError]") and "zzz" in err
    assert cli.main(["tieout", str(d / "base.yaml"), "--stack", "one"]) == 2
    assert capsys.readouterr().err.startswith("error: [CLI]")


def test_t4_the_reviewers_scenario_is_exit_2_in_a_real_process(tmp_path):
    """r10 as a subprocess: `rel: abc` in the base config's tieout block (a raw ValueError before T2/T3) is a clean error and status 2, not a traceback and status 1."""
    cfg = copy.deepcopy(BASE)
    cfg["tieout"] = {"tolerances": {"L1.pv": {"rel": "abc"}}}
    d = _cli_files(tmp_path, cfg)
    import os

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT / "tests")}
    p = subprocess.run([sys.executable, "-m", "pricebt", "tieout", str(d / "base.yaml"), "--stack", f"a={d / 'same.yaml'}", "--stack", f"b={d / 'same.yaml'}"], capture_output=True, text=True, env=env, timeout=300)
    assert p.returncode == 2 and "Traceback" not in p.stderr and "CFG-TIEOUT" in p.stderr and "abc" in p.stderr


# ------------------------------------------------------------------ T5: the report says what each stack owns (r14)
SCALED = {"instruments": {"fwd": {**SAME["instruments"]["fwd"], "bind": {"dv01": {"target": {"method": "dv01"}, "kwargs": {"ctx": "@ctx"}, "scale": 2.0}}}}}


def test_t5_the_markdown_of_a_pair_discloses_factory_wrap_and_every_binding_and_flags_a_hidden_scale():
    """r14: a stack-owned `scale: 2.0` was invisible in the markdown and on the CLI."""
    res = run_tieout(BASE, {"a": [SAME], "b": [SCALED]}, selftest=False)
    md = to_markdown(res.reports["a_vs_b"])
    sec = md.split("## Stacks", 1)[1].split("\n## ", 1)[0]
    assert "pricebt.testing.toys:forward" in sec and "| a | fwd |" in sec and "| b | fwd |" in sec, "the factory of each stack"
    assert "dv01" in sec and "method:dv01" in sec and "scale" in sec
    flagged = [ln for ln in sec.splitlines() if "NOT IDENTITY" in ln]
    assert len(flagged) == 1 and flagged[0].startswith("| b |") and "scale 2.0" in flagged[0], flagged
    assert "not the identity" in sec.lower() or "not identity" in sec.lower()
    assert "pricebt.testing.toys:forward" in res.header["factories"]["a"]["fwd"] and res.header["wraps"] == {"a": {"primary": None}, "b": {"primary": None}}


def test_t5_identical_stacks_flag_nothing_and_a_wrap_is_named():
    """known answer for the flag: two identical stacks flag nothing, and the `wrap` of a pricer role is named (r14: it was not disclosed at all)."""
    res = run_tieout(_ladder_base(), {"a": [REF], "b": [REF]}, selftest=False, audit_measures=LADDER, baseline=False)
    md = to_markdown(res.reports["a_vs_b"])
    sec = md.split("## Stacks", 1)[1].split("\n## ", 1)[0]
    assert "pricebt.testing.refstack:wrap" in sec and "pricebt.testing.refstack:swap" in sec and "NOT IDENTITY" not in sec
    assert res.header["wraps"]["a"] == {"primary": "pricebt.testing.refstack:wrap"}


def test_t5_a_literal_kwarg_of_a_binding_is_shown():
    """r14: a literal library argument in a stack's `bind` (here the ladder tenors) is part of what the stack owns and is shown."""
    res = run_tieout(_ladder_base(), {"a": [REF], "b": [_ladder_stack(scale=1.0)]}, selftest=False, audit_measures=LADDER, baseline=False)
    sec = to_markdown(res.reports["a_vs_b"]).split("## Stacks", 1)[1]
    assert "tenors" in sec and "10Y" in sec, "the literal library arguments a stack passes are part of what it owns"


def test_t5_the_cli_prints_the_disclosure_and_the_flag(tmp_path, capsys):
    """r14: on the command line, the markdown of the pair carries the disclosure, one compact line per stack follows, and the json keeps it."""
    d = _cli_files(tmp_path)
    (d / "scaled.yaml").write_text(yaml.safe_dump(SCALED), encoding="utf8")
    assert cli.main(["tieout", str(d / "base.yaml"), "--stack", f"a={d / 'same.yaml'}", "--stack", f"b={d / 'scaled.yaml'}", "--no-selftest", "--out", str(d / "out")]) == 1
    out = capsys.readouterr().out
    assert "## Stacks" in out and "pricebt.testing.toys:forward" in out, "the markdown of the pair is printed with the disclosure"
    lines = [ln for ln in out.splitlines() if ln.startswith("stack ")]
    assert len(lines) == 2 and "factory fwd=pricebt.testing.toys:forward" in lines[0] and "not identity: none" in lines[0]
    assert "not identity: fwd.dv01 (scale 2.0)" in lines[1], lines
    assert "NOT IDENTITY" in (d / "out" / "a_vs_b.md").read_text(encoding="utf8")
    meta = json.loads((d / "out" / "tieout.json").read_text(encoding="utf8"))
    assert "factories" in meta["header"] and "wraps" in meta["header"] and meta["reports"]["a_vs_b"]["disclosure"]["bindings"]["b"]["fwd"]["dv01"]["scale"] == 2.0


# ------------------------------------------------------------------ T6: the ledger's position sizes use the tolerance of the position's asset class (r22)
def _resized(res, factor):
    out = copy.deepcopy(res)
    out.record.trades["quantity"] = out.record.trades["quantity"] * factor
    return out


def test_t6_a_declared_per_class_quantity_tolerance_reaches_the_trade_ledger(toy_ref):
    """r22: the ledger's size tolerance ignored the asset class of the position, so `L1.quantity.<class>` gave a false FAIL."""
    other = _resized(toy_ref, 1 + 1e-4)
    row = lambda t: [r for r in compare(toy_ref, other, names=("a", "b"), tolerances=Tolerances(t)).rows if r.quantity == "trade_ledger_structure"][0].status  # noqa: E731
    assert row({}) == "structure", "an invariant by default"
    assert row({"L1.quantity.toy": 1e-3}) == "exact", "r22: the class-specific declaration used to be ignored by the ledger (a false FAIL)"
    assert row({"L1.quantity.bond": 1e-3}) == "structure", "another class's declaration does not loosen the toy's sizes"
    assert row({"L1.quantity": 1e-3}) == "exact" and row({"L1.quantity.toy": 1e-6}) == "structure", "the unqualified declaration and a tighter class one still behave"


# ------------------------------------------------------------------ T7: the tolerances table prints the DECLARED expected flag (r25)
def test_t7_the_declared_expected_flag_is_printed_even_when_nothing_exceeds(toy_ref):
    """r25: the table printed `status == expected` (False when nothing exceeds), not the flag that was declared."""
    rep = compare(toy_ref, copy.deepcopy(toy_ref), names=("a", "b"), tolerances=Tolerances({"L1.pv": {"rel": 1e-9, "expected": True}}))
    assert rep.tolerances["L1.pv.toy"]["expected"] is True and rep.tolerances["L1.cash.toy"]["expected"] is False
    md = to_markdown(rep)
    cells = lambda key: [c.strip() for c in [ln for ln in md.splitlines() if ln.startswith(f"| {key} ")][0].split("|")[1:-1]]  # noqa: E731
    assert cells("L1.pv.toy")[4] == "True" and cells("L1.cash.toy")[4] == "False"


# ------------------------------------------------------------------ T8: an L0 snapshot difference explains a position only in a role that position reads (r26)
def _role_difference(toy_ref, role):
    a, b = copy.deepcopy(toy_ref), copy.deepcopy(toy_ref)  # copies: a test edits the manifest
    inp = b.record.audit["inputs"]
    t5 = inp["ts"].iloc[5]
    extra = inp.iloc[[5]].copy()
    extra["role"], extra["digest"] = role, "different"
    b.record.audit["inputs"] = pd.concat([inp, extra], ignore_index=True)
    m = b.record.audit["marks"]
    pid = m.loc[m["ts"] == t5, "position"].iloc[0]
    m.loc[(m["ts"] == t5) & (m["position"] == pid), "pv"] += 0.5  # a genuine model difference at that timestamp
    b.record.equity.loc[b.record.equity.index >= t5, "equity"] += 1.0  # ... which reaches the portfolio from then on
    return a, b


def _status(rep, level, quantity):
    return [r for r in rep.rows if r.level == level and r.quantity == quantity][0].status


def test_t8_a_snapshot_difference_in_a_role_no_instrument_reads_does_not_explain_a_pv_difference(toy_ref):
    """r26: a snapshot difference in a role no instrument reads used to label a genuine pv (and equity) difference `input`."""
    a, b = _role_difference(toy_ref, "some_other_role_no_instrument_uses")
    rep = compare(a, b, names=("a", "b"), roles={"fwd": ["primary"]})
    assert _status(rep, "L1", "pv") == "exceeds" and _status(rep, "L4", "equity") == "exceeds" and not rep.passed


def test_t8_a_snapshot_difference_in_a_role_the_instrument_reads_still_explains_it(toy_ref):
    """the other side of r26: a difference in a role the instrument DOES read still explains the pv difference (L0 attribution kept)."""
    a, b = _role_difference(toy_ref, "primary")
    rep = compare(a, b, names=("a", "b"), roles={"fwd": ["primary"]})
    assert _status(rep, "L1", "pv") == "input" and _status(rep, "L4", "equity") == "input"


def test_t8_the_roles_are_read_from_the_manifest_when_the_run_records_them(toy_ref):
    """a standalone `compare` gets the roles from `manifest.instruments.<name>.roles` when the engine records them; an explicit argument wins."""
    a, b = _role_difference(toy_ref, "some_other_role_no_instrument_uses")
    a.record.manifest["instruments"]["fwd"]["roles"] = ["primary"]
    assert _status(compare(a, b, names=("a", "b")), "L1", "pv") == "exceeds"
    a.record.manifest["instruments"]["fwd"]["roles"] = ["primary", "some_other_role_no_instrument_uses"]
    assert _status(compare(a, b, names=("a", "b")), "L1", "pv") == "input"
    a.record.manifest["instruments"]["fwd"]["roles"] = ["primary"]
    assert _status(compare(a, b, names=("a", "b"), roles={"fwd": ["some_other_role_no_instrument_uses"]}), "L1", "pv") == "input", "an explicit argument wins over the manifest"


def test_t8_without_any_role_information_every_role_explains_as_before(toy_ref):
    """The documented limit of a standalone `compare` of results that record no roles: the old behaviour (any snapshot difference at that timestamp explains)."""
    a, b = _role_difference(toy_ref, "some_other_role_no_instrument_uses")
    for run in (a, b):
        run.record.manifest["instruments"]["fwd"].pop("roles", None)  # the engine records them now; a run saved before it did not
    assert _status(compare(a, b, names=("a", "b")), "L1", "pv") == "input"


def test_t8_a_run_without_positions_or_roles_keeps_the_any_role_fallback_for_portfolio_quantities():
    """Guard for the fallback: with no position to say which roles are read (or a position that does not say), the first differing snapshot explains a portfolio quantity, as before T8."""
    from pricebt.tieout.compare import _Collector

    t = pd.Timestamp("2024-01-05 17:00", tz="America/New_York")
    for roles in ({}, {"P1": None}, {"P1": frozenset({"primary"}), "P2": None}):
        c = _Collector(Tolerances(), 10, roles)
        c.explained_inputs = {t: {"other"}}
        assert c._first_explaining_ts() == t
    c = _Collector(Tolerances(), 10, {"P1": frozenset({"primary"})})
    c.explained_inputs = {t: {"other"}}
    assert c._first_explaining_ts() is None, "every position's roles are known and none reads `other`"


def test_t8_run_tieout_hands_compare_the_market_roles_each_instrument_reads(monkeypatch):
    """the harness knows the roles from what it built, so the attribution is role-aware without an engine change."""
    from pricebt.tieout import runner

    seen, real = [], runner.compare
    monkeypatch.setattr(runner, "compare", lambda *a, **kw: (seen.append(kw.get("roles")), real(*a, **kw))[1])
    run_tieout(BASE, {"a": [SAME], "b": [SAME]}, selftest=False)
    assert seen == [{"fwd": ["primary"]}]


# ------------------------------------------------------------------ TRADE-OFF (r11): absolute floors do not scale with the trade size; the report says so and a run declares its own
def test_the_floor_tradeoff_is_stated_shown_with_its_unit_and_a_declared_floor_closes_it():
    """r11 (trade-off, floors not changed): the module states the limit, the report prints each floor with its unit, and a declared own floor makes the same doubled ladder fail."""
    from pricebt.tieout import tolerances

    doc = " ".join((tolerances.__doc__ or "").split())
    assert "assume templates sized like the shipped ones" in doc and "declare its own floors" in doc and "do not scale with the trade size" in doc, "the module states the limit"
    small = _ladder_base(notional=100.0)  # per-unit ladder buckets of ~0.02: far below the shipped floor of 100 per bucket
    lad = _ladder_stack(scale=2.0)
    _, a = run_stack(small, [REF], audit_measures=LADDER, baseline=False)
    _, x = run_stack(small, [lad], audit_measures=LADDER, baseline=False)
    shipped = compare(a, x, names=("ref", "doubled"))
    assert shipped.passed and {r.status for r in _ladder_rows(shipped).values()} <= {"exact", "noise"}, "the documented limit: a doubled ladder is 'noise' under the shipped floors at this size"
    own = compare(a, x, names=("ref", "doubled"), tolerances=Tolerances({"L2.delta_ladder": {"rel": 1e-3, "floor": 1e-6, "reason": "notional 100: buckets are ~0.02 per unit"}}))
    assert not own.passed and "exceeds" in {r.status for r in _ladder_rows(own).values()}, "declaring its own floor makes the same doubled ladder fail"
    md = to_markdown(shipped)
    line = [ln for ln in md.splitlines() if ln.startswith("| L2.delta_ladder.1Y.swap")][0]
    assert "1.000e+02" in line and "per bucket" in line and "per unit of position" in line, line
    assert "| key | rel | floor | floor unit | expected | reason |" in md
    unit_of = lambda key: [ln for ln in md.splitlines() if ln.startswith(f"| {key} ")][0]  # noqa: E731
    assert "position size" in unit_of("L1.quantity.swap") and "value" in unit_of("L1.pv.swap")
