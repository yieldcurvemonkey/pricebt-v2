"""`tools/mutcheck.py` runs the named tests in a child pytest. It used to REPLACE the caller's PYTHONPATH with 'src', so a library or an adapter package that is importable only through the
caller's PYTHONPATH (every adapter outside `src/pricebt`) was invisible to the child: its `importorskip` tests skipped and its mutants "survived" or the control meant nothing."""
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.core

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import mutcheck  # noqa: E402


def test_the_child_keeps_the_callers_pythonpath_after_src(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["C:/somewhere/adapter_parent", "C:/elsewhere"]))
    entries = mutcheck.child_env()["PYTHONPATH"].split(os.pathsep)
    assert entries[0] == "src" and entries[1:] == ["C:/somewhere/adapter_parent", "C:/elsewhere"]


def test_without_a_callers_pythonpath_it_is_just_src(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert mutcheck.child_env()["PYTHONPATH"] == "src"


def test_the_other_variables_of_the_caller_are_kept_and_bytecode_is_not_written(monkeypatch):
    monkeypatch.setenv("PRICEBT_SOME_SWITCH", "on")
    env = mutcheck.child_env()
    assert env["PRICEBT_SOME_SWITCH"] == "on" and env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_a_child_pytest_really_sees_a_module_only_the_callers_path_provides(tmp_path, monkeypatch):
    """End to end, on a known answer: the module exists only under the caller's PYTHONPATH; the test that imports it passes in the child (and fails when the path is not passed)."""
    lib, tdir = tmp_path / "lib", tmp_path / "tests"  # separate directories: pytest puts the test file's own directory on sys.path
    lib.mkdir()
    tdir.mkdir()
    (lib / "only_on_the_callers_path.py").write_text("VALUE = 41\n", encoding="utf8")
    t = tdir / "test_child_sees_it.py"
    t.write_text("def test_it():\n    import only_on_the_callers_path as m\n    assert m.VALUE == 41\n", encoding="utf8")
    monkeypatch.setenv("PYTHONPATH", str(lib))
    rc, _ = mutcheck.run_tests([str(t)], ["-o", "addopts=", "--rootdir", str(tdir)])
    assert rc == 0
    monkeypatch.delenv("PYTHONPATH")
    rc, _ = mutcheck.run_tests([str(t)], ["-o", "addopts=", "--rootdir", str(tdir)])
    assert rc != 0, "control: without the path the same test fails, so the check above is not vacuous"


def _module_and_two_test_files(tmp_path):
    lib, tdir = tmp_path / "lib", tmp_path / "tests"
    lib.mkdir()
    tdir.mkdir()
    mod = lib / "mutant_target.py"
    mod.write_text("VALUE = 41\nOTHER = 7\n", encoding="utf8")
    (tdir / "test_a.py").write_text("def test_value():\n    import mutant_target as m\n    assert m.VALUE == 41\n\n\ndef test_unrelated():\n    assert True\n", encoding="utf8")
    (tdir / "test_b.py").write_text("def test_other():\n    import mutant_target as m\n    assert m.OTHER == 7\n", encoding="utf8")
    return mod, tdir


def _spec(tmp_path, mod, runs, mutations):
    import json
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"file": str(mod), "runs": runs, "mutations": mutations}), encoding="utf8")
    return str(spec)


def test_several_runs_a_filter_on_one_file_does_not_weaken_the_other(tmp_path, monkeypatch, capsys):
    """Known answer: OTHER is checked only by test_b.py. Run 1 filters test_a.py down to `unrelated` (which cannot see VALUE); run 2 is test_b.py unfiltered. The OTHER mutant must be KILLED by
    run 2; the VALUE mutant is not covered by any selected test and must SURVIVE: exit code 1, exactly that name."""
    mod, tdir = _module_and_two_test_files(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(mod.parent))
    common = ["-o", "addopts=", "--rootdir", str(tdir)]
    spec = _spec(tmp_path, mod, [[str(tdir / "test_a.py"), "-k", "unrelated", *common], [str(tdir / "test_b.py"), *common]],
                 [{"name": "other", "find": "OTHER = 7", "replace": "OTHER = 8"}, {"name": "value", "find": "VALUE = 41", "replace": "VALUE = 42"}])
    rc = mutcheck.main(["mutcheck", spec])
    out = capsys.readouterr().out
    assert "[other] KILLED" in out and "[value] SURVIVED" in out, out
    assert rc == 1 and "survivors: ['value']" in out
    assert mod.read_text(encoding="utf8") == "VALUE = 41\nOTHER = 7\n", "the file is restored"


def test_several_runs_all_killed_gives_exit_zero_and_the_control_must_be_green(tmp_path, monkeypatch, capsys):
    mod, tdir = _module_and_two_test_files(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(mod.parent))
    common = ["-o", "addopts=", "--rootdir", str(tdir)]
    runs = [[str(tdir / "test_a.py"), *common], [str(tdir / "test_b.py"), *common]]
    ok = _spec(tmp_path, mod, runs, [{"name": "value", "find": "VALUE = 41", "replace": "VALUE = 42"}, {"name": "other", "find": "OTHER = 7", "replace": "OTHER = 8"}])
    assert mutcheck.main(["mutcheck", ok]) == 0
    capsys.readouterr()
    mod.write_text("VALUE = 40\nOTHER = 7\n", encoding="utf8")  # control: the unmutated file is red -> abort with 2, nothing mutated
    assert mutcheck.main(["mutcheck", ok]) == 2
    assert "ABORT" in capsys.readouterr().out
