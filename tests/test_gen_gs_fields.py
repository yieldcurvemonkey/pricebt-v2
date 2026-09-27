"""tests/test_gen_gs_fields.py (IMPLEMENTATION_PLAN.md P1.6).

Regenerates src/pricebt/instrument/_gs_fields.py in memory from the committed
tests/data/gs_instruments_1_5_4.json and asserts it is byte-identical to the committed generated
file -- so a stale or hand-edited _gs_fields.py (forbidden, IMPLEMENTATION_PLAN.md section 0.2) is
caught. Also exercises coerce_tag_for() directly, and cross-checks tools/gen_gs_fields.py's literal
PRICEBT_ENUM_NAMES against the real pricebt.common enum set once P1.1 exists (P1 gate; see that
test's docstring for why it skips rather than fails during P1.6's own parallel run).

Run with the stir env's python, PYTHONPATH=src;tests:
    C:\\Users\\chris\\anaconda3\\envs\\stir\\python.exe -m pytest tests/test_gen_gs_fields.py -o addopts=
"""
from __future__ import annotations

import enum
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OUT_PY = ROOT / "src" / "pricebt" / "instrument" / "_gs_fields.py"
TOOL_PATH = ROOT / "tools" / "gen_gs_fields.py"
API_JSON = ROOT / "tests" / "data" / "gs_api_1_5_4.json"
INSTRUMENTS_JSON = ROOT / "tests" / "data" / "gs_instruments_1_5_4.json"


def _load_gen_gs_fields():
    """tools/ is not on PYTHONPATH (only src;tests, IMPLEMENTATION_PLAN.md section 0.3), so import
    the tool directly from its file path rather than requiring `tools` to be a package."""
    spec = importlib.util.spec_from_file_location("gen_gs_fields", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshots_have_no_unstable_reprs():
    """A repr containing a live memory address (e.g. `repr(dataclasses.MISSING)` ==
    "<dataclasses._MISSING_TYPE object at 0x...>", the bug gs_api_snapshot.py's `_default_repr`
    had before it special-cased `dataclasses.MISSING`) is not a usable snapshot value -- it's
    different on every process run, so regenerating this file (the P1.6 acceptance step) would
    produce a spurious diff unrelated to any real change in gs's API. Whole-file text scan: covers
    every `default_repr` site (`signature`, `fields`, `method_signatures`) in one assertion."""
    for path in (API_JSON, INSTRUMENTS_JSON):
        text = path.read_text(encoding="utf8")
        assert not re.search(r" at 0x[0-9a-fA-F]+>", text), path.name
        assert "_MISSING_TYPE" not in text, path.name


def test_regenerates_byte_identical(tmp_path):
    gen = _load_gen_gs_fields()
    source = gen.generate_source()
    regenerated = tmp_path / "_gs_fields.py"
    regenerated.write_bytes(source.encode("utf8"))
    assert regenerated.read_bytes() == OUT_PY.read_bytes()


@pytest.mark.parametrize(
    "annotation,expected",
    [
        ("typing.Optional[gs_quant.target.common.PayReceive]", "PayReceive"),
        ("typing.Optional[gs_quant.common.PayReceive]", "PayReceive"),
        ("typing.Optional[gs_quant.target.common.Currency]", "Currency"),
        ("typing.Optional[gs_quant.target.common.ValuationTime]", None),  # not a pricebt enum
        ("typing.Optional[str]", None),
        ("typing.Optional[float]", None),
        ("typing.Optional[datetime.date]", None),
        ("typing.Union[float, str, NoneType]", None),  # not a plain Optional[X]
    ],
)
def test_coerce_tag_for(annotation, expected):
    gen = _load_gen_gs_fields()
    assert gen.coerce_tag_for(annotation) == expected


def test_enum_literal_list_matches_pricebt_common():
    """Cross-check (IMPLEMENTATION_PLAN.md P1.6, "You do NOT need pricebt.common to exist..."):
    tools/gen_gs_fields.py's literal PRICEBT_ENUM_NAMES must equal the set of enum classes
    pricebt.common actually defines -- but only once P1.1 (running in parallel) has written real
    enums. Skip rather than fail if pricebt.common is still a docstring-only stub, so this test is
    correct regardless of P1.1's finish order; the P1 gate re-runs it after every P1 task is done."""
    try:
        from pricebt import common
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - depends on P1.1's
        # parallel progress. Narrowed to import-shaped errors only (not e.g. a NameError or
        # AttributeError from a real bug inside pricebt.common) so a genuine break doesn't get
        # silently downgraded to a skip at the P1 gate, where this check is meant to be
        # authoritative (IMPLEMENTATION_PLAN.md line 351).
        pytest.skip(f"pricebt.common not importable yet (P1.1 may still be in progress): {exc}")

    real_enum_names = {
        name for name in dir(common)
        if isinstance(getattr(common, name, None), type)
        and issubclass(getattr(common, name), enum.Enum)
        and getattr(common, name).__module__ == common.__name__  # exclude e.g. `Enum` itself,
        # imported into pricebt.common with `from enum import Enum` -- issubclass(Enum, Enum) is
        # True, but it is not one of pricebt.common's own enum classes.
    }
    if not real_enum_names:
        pytest.skip("pricebt.common defines no enums yet (P1.1 may still be in progress)")

    gen = _load_gen_gs_fields()
    assert set(gen.PRICEBT_ENUM_NAMES) == real_enum_names
