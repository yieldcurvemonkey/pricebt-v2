"""skills/pricebt-risk-measures: measures.py (contract table, capability matrix, paste-ready mapping
skeleton for IRSwap/IRSwaption, declaration block for Bond) on the toy configs, in-process and via the
CLI; the catalogue reference lists every exported measure; the contract table of the SKILL.md lists
every row and the EXCLUDED list; and the runnable blocks of the references execute as written."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
import yaml

import pricebt.risk as risk
from pricebt.assets import load_asset, yamlio
from pricebt.errors import ConfigError
from pricebt.risk import contracts

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "pricebt-risk-measures"
SCRIPT = SKILL / "scripts" / "measures.py"
ASSETS = REPO / "tests" / "assets"
FULL = {"IRSwap": "toy_usd_irs_full.yaml", "IRSwaption": "toy_usd_swaption.yaml", "Bond": "toy_usd_bond.yaml"}
sys.path.insert(0, str(SCRIPT.parent))

import measures  # noqa: E402


def _cli(*args):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(["src", "tests"])}
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)


def _statuses(matrix):
    return {(r["measure"], r["form"]): r["status"] for r in matrix["rows"]}


def _toy_irs() -> dict:
    raw = dict(yamlio.load_file(ASSETS / "toy_usd_irs.yaml"))
    assert "unsupported_measures" not in raw  # a strict class: it maps the whole contract
    return raw


def _bond_declaring(*measures) -> dict:
    """toy_usd_bond with `measures` unmapped and declared (only a Bond may still declare)."""
    raw = dict(yamlio.load_file(ASSETS / "toy_usd_bond.yaml"))
    raw["risk_measures"] = {k: v for k, v in raw["risk_measures"].items() if k not in measures}
    raw["unsupported_measures"] = {m: f"the bond library has no {m} call" for m in measures}
    return raw


def _write(tmp_path, name, raw) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


# ------------------------------------------------------------------------------------ contract


@pytest.mark.parametrize("instrument", sorted(contracts.CONTRACTS))
def test_contract_rows_are_the_contract(instrument):
    rows = measures.contract_rows(instrument)
    assert [r["measure"] for r in rows] == [q.measure for q in contracts.contract_for(instrument)]
    by = {r["measure"]: r for r in rows}
    assert by["IRFwdRate"]["intensive"] and not by["IRDelta"]["intensive"]
    assert by["IRGammaParallel"]["units"] == ["ccy_per_bp2"] and by["Cashflows"]["forms"] == ["frame"]


def test_contract_cli():
    proc = _cli("contract", "IRSwaption")
    assert proc.returncode == 0, proc.stderr
    assert "| ProbabilityOfExercise | prob | scalar |" in proc.stdout and "'<tail>;<expiry>'" in proc.stdout
    assert "every row must be MAPPED" in proc.stdout and "| PremiumCents | notional_level | scalar |" in proc.stdout
    assert "or declare it" in _cli("contract", "Bond").stdout
    bad = _cli("contract", "FXOption")
    assert bad.returncode == 2 and "no measure contract" in bad.stderr


# ------------------------------------------------------------------------------------ matrix


@pytest.mark.parametrize("instrument", sorted(FULL))
def test_full_toy_configs_map_every_row(instrument):
    path = ASSETS / FULL[instrument]
    m = measures.capability_matrix(path)
    assert m["instrument"] == instrument and measures.matrix_ok(m)
    assert set(_statuses(m).values()) == {measures.MAPPED}
    in_contract = "PnlExplain" in {r.measure for r in contracts.contract_for(instrument)}  # IRSwap, IRSwaption (R3-1)
    assert m["outside"] == ([] if in_contract else ["PnlExplain"]) and not m["warnings"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        load_asset(path)  # the matrix agrees with the loader: loads, and without warnings


def test_bond_declarations_show_reasons_and_hints(tmp_path):
    """Bond keeps map-or-declare: declared rows are DECLARED with their reason, and a hint only where
    every library can supply the measure."""
    raw = _bond_declaring("Theta", "IRVega", "ExpiryInYears")
    m = measures.capability_matrix(raw)
    st = _statuses(m)
    assert st[("IRDelta", "scalar")] == st[("IRDelta", "bucketed")] == st[("IRFwdRate", "scalar")] == measures.MAPPED
    assert st[("Theta", "scalar")] == measures.DECLARED and measures.MISSING not in st.values() and measures.matrix_ok(m)
    assert not measures.matrix_ok(m, strict=True)  # IRVega / ExpiryInYears: declarations any library can avoid
    rows = {(r["measure"], r["form"]): r for r in m["rows"]}
    assert rows[("Theta", "scalar")]["reason"] == raw["unsupported_measures"]["Theta"]
    assert "R2-8" in rows[("IRVega", "scalar")]["hint"] and "days / 365" in rows[("ExpiryInYears", "scalar")]["hint"]
    assert rows[("Theta", "scalar")]["hint"] == ""  # hints only where every library can supply the measure
    proc = _cli("matrix", str(_write(tmp_path, "bond.yaml", raw)))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "| Theta | scalar | theta | ccy | DECLARED |" in proc.stdout and "TODO=0 MISSING=0" in proc.stdout


def test_a_strict_class_declaration_stays_missing_and_is_a_problem():
    """IRSwap (R3-0): declaring Theta does not satisfy its row -- it stays MISSING with the reason
    shown, the declaration is a load problem, and the next step is the mapping skeleton."""
    raw = _toy_irs()
    raw["risk_measures"] = {k: v for k, v in raw["risk_measures"].items() if k != "Theta"}
    raw["unsupported_measures"] = {"Theta": "no carry call"}
    m = measures.capability_matrix(raw)
    row = next(r for r in m["rows"] if r["measure"] == "Theta")
    assert row["status"] == measures.MISSING and "no carry call" in row["reason"] and not measures.matrix_ok(m)
    assert any(p.startswith("unsupported_measures declares Theta") for p in m["problems"])
    assert not any("require a mapping for every contract measure" in p for p in m["problems"])  # the MISSING row is a row
    assert "mapping skeleton" in measures.matrix_markdown(m)
    assert measures.missing_block(raw) == contracts.mapping_skeleton("IRSwap", [("Theta", "scalar")])


def test_preset_key_counts_toward_its_base_measure():
    raw = _toy_irs()
    raw["risk_measures"] = {"Price": "npv", "IRDeltaParallel": "dv01", "IRFwdRate": "par_rate"}
    m = measures.capability_matrix(raw)
    rows = {(r["measure"], r["form"]): r for r in m["rows"]}
    assert rows[("IRDelta", "scalar")]["status"] == measures.MAPPED and rows[("IRDelta", "scalar")]["via"] == "IRDeltaParallel"
    assert rows[("IRDelta", "bucketed")]["status"] == measures.MISSING  # a preset's slot never serves the ladder
    assert ("IRDelta", "bucketed") in m["missing"] and "IRDeltaParallel" not in m["outside"]


def test_matrix_reports_load_problems_without_duplicating_missing_rows():
    raw = dict(yamlio.load_file(ASSETS / "toy_usd_irs_full.yaml"))
    raw["functions"] = {**raw["functions"], "par_rate": {**raw["functions"]["par_rate"], "unit": "ccy"}}
    raw["risk_measures"] = {**raw["risk_measures"], "Theta": "no_such_function"}
    m = measures.capability_matrix(raw)
    assert not measures.matrix_ok(m)
    assert any(p.startswith("IRFwdRate (scalar)") and "'ccy'" in p for p in m["problems"])
    assert any("no_such_function" in p for p in m["problems"])
    assert _statuses(m)[("Theta", "scalar")] == measures.MISSING
    assert not any("neither mapped nor declared" in p or "require a mapping" in p for p in m["problems"])  # MISSING rows are rows, not problems
    with pytest.raises(ConfigError):
        load_asset(raw)


# ------------------------------------------------------------------------------------ block


def test_strict_block_is_the_mapping_skeleton_and_loads_once_written(tmp_path):
    """IRSwap: `block` prints contracts.mapping_skeleton for the gaps (never a declaration block);
    merged as pasted it does not load (the stubs do not compile); with each expression and unit
    written it loads."""
    raw = _toy_irs()
    gone = {"ParSpread": "par_spread", "CompoundedFixedRate": "compounded_rate"}
    raw["risk_measures"] = {k: v for k, v in raw["risk_measures"].items() if k not in gone and k != "IRGamma"}
    raw["functions"] = {k: v for k, v in raw["functions"].items() if k not in gone.values()}
    with pytest.raises(ConfigError, match="measure-contract problem"):
        load_asset(raw)
    cfg = _write(tmp_path, "incomplete_swap.yaml", raw)
    proc = _cli("block", str(cfg), "--reason", "ignored for a swap")
    missing = [("IRGamma", "bucketed"), ("ParSpread", "scalar"), ("CompoundedFixedRate", "scalar")]
    assert proc.returncode == 0 and proc.stdout == contracts.mapping_skeleton("IRSwap", missing) == measures.missing_block(cfg), proc.stderr
    assert "unsupported_measures" not in proc.stdout and "merge each section" in proc.stderr
    pasted = yaml.safe_load(proc.stdout)
    for section in ("functions", "portfolio_functions", "risk_measures"):
        raw[section] = {**raw[section], **pasted[section]}
    with pytest.raises(ConfigError):
        load_asset(raw)  # contracts.SKELETON_EXPR is a syntax error on purpose
    written = {"ParSpread": ("tri.par_spread(market, trade)", "bp"), "CompoundedFixedRate": ("tri.compounded_fixed_rate(market, trade)", "bp"),
               "IRGamma": ('tri.gamma_ladder(market, trades, weights, ("2Y", "5Y", "10Y", "30Y"))', None)}
    for measure, (expr, unit) in written.items():
        fname = next(iter(pasted["risk_measures"][measure].values()))
        section = "functions" if fname in pasted["functions"] else "portfolio_functions"
        raw[section][fname] = {**raw[section][fname], "expr": expr, **({"unit": unit} if unit else {})}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg_ok = load_asset(raw)
    assert {("ParSpread", "scalar"), ("CompoundedFixedRate", "scalar"), ("IRGamma", "bucketed")} <= set(cfg_ok.provided_forms)


def test_block_makes_an_incomplete_bond_load_and_the_matrix_flags_its_todos(tmp_path):
    raw = dict(yamlio.load_file(ASSETS / "toy_usd_bond.yaml"))
    raw["risk_measures"] = {k: v for k, v in raw["risk_measures"].items() if k not in ("Theta", "IRVega")}
    with pytest.raises(ConfigError, match="measure-contract problem"):
        load_asset(raw)
    cfg = _write(tmp_path, "incomplete.yaml", raw)

    proc = _cli("block", str(cfg))
    assert proc.returncode == 0 and proc.stdout.startswith("unsupported_measures:\n"), proc.stderr
    assert proc.stdout == measures.missing_block(cfg)
    cfg.write_text(cfg.read_text(encoding="utf-8") + proc.stdout, encoding="utf-8")
    assert load_asset(cfg).unsupported_measures["Theta"] == {"*": "TODO: why your library cannot compute this"}

    flagged = _cli("matrix", str(cfg))
    assert flagged.returncode == 1 and "| Theta | scalar | theta | ccy | TODO |" in flagged.stdout
    assert {r["status"] for r in measures.capability_matrix(cfg)["rows"] if r["measure"] == "Theta"} == {measures.TODO}

    # honest reasons clear the flag; nothing is missing any more
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("TODO: why your library", "toylib.rates: no function; your library"), encoding="utf-8")
    assert _cli("matrix", str(cfg)).returncode == 0
    again = _cli("block", str(cfg))
    assert again.returncode == 0 and again.stdout.startswith("# nothing missing") and "merge" not in again.stderr


def test_block_warns_to_merge_into_an_existing_declaration_key(tmp_path):
    raw = _bond_declaring("IRVega", "Theta")
    del raw["unsupported_measures"]["Theta"]
    cfg = _write(tmp_path, "partial.yaml", raw)
    proc = _cli("block", str(cfg), "--reason", "no carry function")
    assert proc.stdout == 'unsupported_measures:\n  Theta: "no carry function"\n' and "merge" in proc.stderr


# ------------------------------------------------------------------------------------ references


def test_catalogue_reference_lists_every_exported_measure():
    text = (SKILL / "references" / "measure-catalogue.md").read_text(encoding="utf-8")
    names = [n for n in risk.__all__ if isinstance(getattr(risk, n), risk.RiskMeasure)]
    names += ["PnlExplain", "PnlExplainClose", "PnlExplainLive", "PnlPredictLive"]
    assert len(names) > 120
    assert not [n for n in names if not re.search(rf"`{n}[`(]", text)]


def test_skill_md_lists_every_strict_contract_row_and_the_excluded_list_exactly():
    """SKILL.md's contract table names every IRSwaption contract measure, and its "Excluded, with
    reasons" table names exactly contracts.EXCLUDED (IR_STRICT_CONTRACT R3-1)."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert not [r.measure for r in contracts.contract_for("IRSwaption") if f"`{r.measure}`" not in text]
    excluded = text.split("**Excluded, with reasons**", 1)[1].split("\n\n", 2)[1]  # the table after the heading line
    names = set(re.findall(r"`([A-Za-z_0-9]+)`", excluded))
    assert names == set(contracts.EXCLUDED), (names ^ set(contracts.EXCLUDED))


def _runnable_blocks(md: Path):
    return re.findall(r"```python\n(# runnable:.*?)```", md.read_text(encoding="utf-8"), flags=re.S)


@pytest.mark.parametrize("reference", ["implementing-measures.md", "portfolio-and-results.md"])
def test_runnable_reference_blocks_execute(reference, monkeypatch):
    blocks = _runnable_blocks(SKILL / "references" / reference)
    assert blocks, f"{reference} has no '# runnable:' python block"
    monkeypatch.chdir(REPO)  # the blocks use repository-relative paths, as documented
    for block in blocks:
        exec(compile(block, f"<{reference}>", "exec"), {"__name__": "__runnable__"})
