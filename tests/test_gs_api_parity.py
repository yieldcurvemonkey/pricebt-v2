"""gs API parity snapshot test (IMPLEMENTATION_PLAN.md P4.2; DESIGN.md section 12.3, MUST-2).

Compares every in-scope pricebt symbol against tests/data/gs_api_1_5_4.json /
tests/data/gs_instruments_1_5_4.json, the P1.6 snapshots of real gs_quant 1.5.4 taken by
tools/gs_api_snapshot.py. This test **never imports gs_quant**: the descriptor functions below are
copied (in spirit -- kept import-free of gs_quant so this module can be collected and run without
it) from that tool's own introspection helpers, so a pricebt object and a gs_quant snapshot entry
are described identically and a diff is a real API difference, never a describer inconsistency.

Allowed differences are declared in tests/data/gs_api_exceptions.yaml (schema documented in that
file's header comment). Any other difference -- an in-scope symbol missing from pricebt entirely, an
unlisted signature/field/method/property mismatch, or a listed dev_id with no DESIGN.md section 11
row -- fails a test below.
"""
from __future__ import annotations

import dataclasses
import enum
import fnmatch
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
import yaml

import pricebt.backtests.actions as bt_actions
import pricebt.backtests.backtest_objects as bt_backtest_objects
import pricebt.backtests.backtest_utils as bt_backtest_utils
import pricebt.backtests.core as bt_core
import pricebt.backtests.data_sources as bt_data_sources
import pricebt.backtests.generic_engine as bt_generic_engine
import pricebt.backtests.generic_engine_action_impls as bt_impls
import pricebt.backtests.strategy as bt_strategy
import pricebt.backtests.triggers as bt_triggers
import pricebt.common as pb_common
import pricebt.datetime as pb_datetime
import pricebt.instrument as pb_instrument
import pricebt.markets as pb_markets
import pricebt.markets.portfolio as pb_portfolio
import pricebt.risk as pb_risk
import pricebt.session as pb_session

pytestmark = pytest.mark.core

DATA = Path(__file__).parent / "data"
DESIGN = Path(__file__).parent.parent / "docs" / "v2" / "DESIGN.md"

API = json.loads((DATA / "gs_api_1_5_4.json").read_text(encoding="utf8"))
SYMBOLS: Dict[str, dict] = API["symbols"]
REQUESTED_NOT_FOUND: List[str] = API["requested_not_found"]

EXCEPTIONS: List[dict] = yaml.safe_load((DATA / "gs_api_exceptions.yaml").read_text(encoding="utf8")) or []
_USED: set = set()  # ids(exception dict) actually consulted by some comparison, for the hygiene test

_VALID_ASPECTS = {"signature", "fields", "methods", "method_signature", "properties", "members", "all", "risk_measure"}
_RISK_MEASURE_ASPECTS = ("class", "name", "measure_type", "asset_class", "unit")
_DEV_ID_RE = re.compile(r"\bDEV-[A-Za-z0-9]+\b")


# ============================================================================ descriptor engine
# Copied (in spirit) from tools/gs_api_snapshot.py's own `_default_repr`/`_field_default_repr`/
# `_sig_list`/`_public_methods_and_properties`/`_describe`, so pricebt objects are introspected with
# exactly the same rules as the snapshot was built with (see that file's module docstring for the
# format). Deliberately gs_quant-import-free.


def _default_repr(default: Any) -> str:
    if default is inspect.Signature.empty or default is dataclasses.MISSING:
        return "NODEFAULT"
    return repr(default)


def _field_default_repr(field: "dataclasses.Field") -> str:
    if field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return "FACTORY"
    return _default_repr(field.default)


def _sig_list(obj: Any, strip_self: bool) -> list:
    sig = inspect.signature(obj)
    params = list(sig.parameters.values())
    if strip_self and params and params[0].name in ("self", "cls"):
        params = params[1:]
    return [[p.name, str(p.kind), _default_repr(p.default)] for p in params]


def _public_methods_and_properties(cls: type) -> Tuple[list, list, dict]:
    methods: list = []
    properties: list = []
    method_signatures: dict = {}
    for name in dir(cls):
        if name.startswith("_"):
            continue
        try:
            member = inspect.getattr_static(cls, name)
        except AttributeError:
            continue
        if isinstance(member, property):
            properties.append(name)
        elif isinstance(member, (classmethod, staticmethod)):
            methods.append(name)
            method_signatures[name] = _sig_list(member.__func__, strip_self=isinstance(member, classmethod))
        elif inspect.isfunction(member):
            methods.append(name)
            method_signatures[name] = _sig_list(member, strip_self=True)
    return sorted(methods), sorted(properties), method_signatures


def describe(obj: Any) -> dict:
    if isinstance(obj, type):
        if issubclass(obj, enum.Enum):
            return {"kind": "enum", "members": [[m.name, _default_repr(m.value)] for m in obj]}
        entry: Dict[str, Any] = {
            "kind": "dataclass" if dataclasses.is_dataclass(obj) else "class",
            "signature": _sig_list(obj, strip_self=False),
        }
        if dataclasses.is_dataclass(obj):
            entry["fields"] = [[f.name, f.init, _field_default_repr(f)] for f in dataclasses.fields(obj)]
        entry["methods"], entry["properties"], entry["method_signatures"] = _public_methods_and_properties(obj)
        return entry
    if dataclasses.is_dataclass(obj):  # a module-level RiskMeasure instance
        cls = type(obj)
        return {
            "kind": "risk_measure",
            "class": cls.__name__,
            "name": getattr(obj, "name", None),
            "measure_type": str(getattr(obj, "measure_type", None)),
            "asset_class": str(getattr(obj, "asset_class", None)),
            "unit": str(getattr(obj, "unit", None)),
        }
    return {"kind": "function", "signature": _sig_list(obj, strip_self=True)}


# ============================================================================ exception matching


def _matches(pattern: str, gs_key: str) -> bool:
    return pattern == gs_key or ("*" in pattern and fnmatch.fnmatchcase(gs_key, pattern))


def _applicable(gs_key: str, aspect: str) -> List[dict]:
    hits = [e for e in EXCEPTIONS if e["aspect"] == aspect and _matches(e["symbol"], gs_key)]
    for e in hits:
        _USED.add(id(e))
    return hits


def _skip_all(gs_key: str) -> bool:
    hits = _applicable(gs_key, "all")
    return any(e.get("skip") for e in hits)


def _fields_skipped(gs_key: str) -> bool:
    hits = [e for e in EXCEPTIONS if e["aspect"] == "fields" and _matches(e["symbol"], gs_key)]
    skip = any(e.get("skip") for e in hits)
    if skip:
        for e in hits:
            if e.get("skip"):
                _USED.add(id(e))
    return skip


# ============================================================================ list/set/member comparisons


def _compare_ordered_list(
    gs_key: str, aspect: str, gs_list: list, pb_list: Optional[list], exc: Optional[List[dict]] = None
) -> Optional[str]:
    if pb_list is None:
        return f"{aspect}: pricebt has none (gs={gs_list!r})"
    if exc is None:
        exc = _applicable(gs_key, aspect)
    insert = {n for e in exc for n in e.get("insert", [])}
    # `missing`: names gs has that pricebt legitimately omits (the mirror image of `insert`); same
    # word/meaning as the methods/properties aspects' `missing`, just applied to an ordered list.
    missing = {n for e in exc for n in e.get("missing", [])}
    ignore_default = {n for e in exc for n in e.get("ignore_default", [])}

    def norm(rows):
        out = []
        for row in rows:
            name, kind_or_init, default = row
            if name in ignore_default:
                default = "*"
            out.append([name, kind_or_init, default])
        return out

    gs_filtered = [row for row in gs_list if row[0] not in missing]
    pb_filtered = [row for row in pb_list if row[0] not in insert]
    if norm(gs_filtered) == norm(pb_filtered):
        return None
    return (
        f"{aspect}: gs={gs_list!r} pb={pb_list!r} (after removing insert={sorted(insert)!r} from pb: "
        f"{pb_filtered!r}; missing={sorted(missing)!r} from gs: {gs_filtered!r})"
    )


def _method_signature_exceptions(gs_key: str, method_name: str) -> List[dict]:
    hits = [
        e
        for e in EXCEPTIONS
        if e["aspect"] == "method_signature" and _matches(e["symbol"], gs_key) and e.get("method") == method_name
    ]
    for e in hits:
        _USED.add(id(e))
    return hits


def _compare_name_set(gs_key: str, aspect: str, gs_names: list, pb_names: list) -> Optional[str]:
    exc = _applicable(gs_key, aspect)
    missing_ok = {n for e in exc for n in e.get("missing", [])}
    extra_ok = {n for e in exc for n in e.get("extra", [])}
    gs_set, pb_set = set(gs_names), set(pb_names)
    bad_missing = (gs_set - pb_set) - missing_ok
    bad_extra = (pb_set - gs_set) - extra_ok
    if bad_missing or bad_extra:
        return f"{aspect}: gs-only(unexplained)={sorted(bad_missing)!r} pb-only(unexplained)={sorted(bad_extra)!r}"
    return None


def _compare_members(gs_key: str, gs_members: list, pb_members: list) -> Optional[str]:
    exc = _applicable(gs_key, "members")
    gs_t = [tuple(m) for m in gs_members]
    pb_t = [tuple(m) for m in pb_members]
    if any(e.get("subset") for e in exc):
        if set(pb_t) <= set(gs_t):
            return None
        return f"members: pb has members gs does not: {sorted(set(pb_t) - set(gs_t))!r}"
    if gs_t == pb_t:
        return None
    return f"members: gs={gs_members!r} pb={pb_members!r}"


# ============================================================================ symbol resolution

_BT_MODULES = {
    "strategy": bt_strategy,
    "triggers": bt_triggers,
    "actions": bt_actions,
    "data_sources": bt_data_sources,
    "backtest_objects": bt_backtest_objects,
    "backtest_utils": bt_backtest_utils,
    "generic_engine": bt_generic_engine,
    "core": bt_core,
}

_INSTRUMENT_SIG_TAIL = [
    ["pricebt_asset", "KEYWORD_ONLY", "None"],
    ["quantity_", "KEYWORD_ONLY", "1.0"],
    ["kwargs", "VAR_KEYWORD", "NODEFAULT"],
]


def resolve(gs_key: str) -> Any:
    parts = gs_key.split(".")
    root = parts[1]
    if root == "backtests":
        mod, name = parts[2], parts[3]
        obj = getattr(_BT_MODULES[mod], name, None)
        if obj is None and mod == "generic_engine":
            # DESIGN.md section 9.3: the *Impl classes live in generic_engine_action_impls.py in
            # pricebt (split out of gs 1.5.4's single generic_engine.py) -- not a MUST-2 module-path
            # name, just the file split that section documents.
            obj = getattr(bt_impls, name, None)
        return obj
    if root == "instrument":
        return getattr(pb_instrument, parts[2], None)
    if root == "risk":
        return getattr(pb_risk, parts[2], None)
    if root == "markets":
        if parts[2] == "portfolio":
            return getattr(pb_portfolio, parts[3], None)
        return getattr(pb_markets, parts[2], None)
    if root == "session":
        if gs_key == "gs_quant.session.GsSession.use":
            return pb_session.GsSession.use
        if gs_key == "gs_quant.session.Environment":
            return pb_session.Environment
    raise AssertionError(f"no resolver for {gs_key!r}")


# ============================================================================ the generic comparator


def diff_symbol(gs_key: str) -> List[str]:
    """Returns a list of human-readable failure strings for `gs_key`; empty means it matches."""
    gs_desc = SYMBOLS[gs_key]
    pb_obj = resolve(gs_key)
    # an `all` row with `pending: <phase>` excuses a symbol a later phase adds -- and only while it
    # is absent, so the row cannot outlive the implementation (IR_RISK_DESIGN.md R2-23)
    pending = [e["pending"] for e in _applicable(gs_key, "all") if e.get("pending")]
    if pb_obj is None:
        return [] if pending else ["missing from pricebt entirely"]
    if pending:
        return [f"pricebt now has it, but a `pending: {pending[0]}` row still excuses it: delete that row so it is compared"]
    if _skip_all(gs_key):
        return []

    pb_desc = describe(pb_obj)
    failures: List[str] = []
    fields_skipped = _fields_skipped(gs_key)

    if not fields_skipped and gs_desc["kind"] != pb_desc["kind"]:
        failures.append(f"kind: gs={gs_desc['kind']!r} pb={pb_desc['kind']!r}")

    if "fields" in gs_desc and not fields_skipped:
        d = _compare_ordered_list(gs_key, "fields", gs_desc["fields"], pb_desc.get("fields"))
        if d:
            failures.append(d)

    if "signature" in gs_desc:
        d = _compare_ordered_list(gs_key, "signature", gs_desc["signature"], pb_desc.get("signature"))
        if d:
            failures.append(d)
        if gs_key.startswith("gs_quant.instrument."):
            # DESIGN.md section 5.1's stronger rule: the trailing triple must be in this EXACT
            # position (prefix identical to gs, then exactly this tail), not merely present
            # somewhere. This is what actually catches a reordered/misplaced GS_FIELDS entry.
            pb_sig = pb_desc.get("signature") or []
            gs_sig = gs_desc["signature"]
            ok = pb_sig[: len(gs_sig)] == gs_sig and pb_sig[len(gs_sig) :] == _INSTRUMENT_SIG_TAIL
            if not ok:
                failures.append(f"signature (section 5.1 exact-tail rule): gs={gs_sig!r} pb={pb_sig!r}")

    if "members" in gs_desc:
        d = _compare_members(gs_key, gs_desc["members"], pb_desc.get("members", []))
        if d:
            failures.append(d)

    if gs_desc["kind"] == "risk_measure":
        # a `risk_measure` exception's `expect` replaces gs's value for the fields it names: pricebt
        # must then equal the expected value exactly (IR_RISK_DESIGN.md R2-24; e.g. DEV-I9's class)
        expect = {k: v for e in _applicable(gs_key, "risk_measure") for k, v in e.get("expect", {}).items()}
        for f in _RISK_MEASURE_ASPECTS:
            want = expect.get(f, gs_desc[f])
            if want != pb_desc.get(f):
                failures.append(f"{f}: gs={gs_desc[f]!r} expected={want!r} pb={pb_desc.get(f)!r}")

    if "methods" in gs_desc:
        d = _compare_name_set(gs_key, "methods", gs_desc["methods"], pb_desc.get("methods", []))
        if d:
            failures.append(d)

    if "method_signatures" in gs_desc:
        pb_sigs = pb_desc.get("method_signatures", {})
        for name, gs_sig in gs_desc["method_signatures"].items():
            if name not in pb_sigs:
                continue  # a missing method is already reported by the "methods" name-set check above
            exc = _method_signature_exceptions(gs_key, name)
            d = _compare_ordered_list(gs_key, "method_signature", gs_sig, pb_sigs[name], exc=exc)
            if d:
                failures.append(f"{name}(): {d}")

    if "properties" in gs_desc:
        d = _compare_name_set(gs_key, "properties", gs_desc["properties"], pb_desc.get("properties", []))
        if d:
            failures.append(d)

    return failures


# ============================================================================ parametrized symbol lists

_SCOPE_PREFIXES = ("gs_quant.backtests.", "gs_quant.instrument.", "gs_quant.risk.", "gs_quant.markets.", "gs_quant.session.")
GS_DRIVEN_KEYS = sorted(k for k in SYMBOLS if k.startswith(_SCOPE_PREFIXES))

# The 2.1.17-only risk measures: absent from the 1.5.4 snapshot by construction (gs_api_snapshot.py
# puts them in requested_not_found, not symbols). DESIGN.md section 12.3: these are EXTRA pricebt
# symbols, not mismatches -- existence-only, never compared against a snapshot descriptor, and never
# treated as "extra symbol not in a gs listing" failures.
RISK_2_1_17_ONLY = sorted(k.rsplit(".", 1)[-1] for k in REQUESTED_NOT_FOUND if k.startswith("gs_quant.risk."))

COMMON_ENUM_NAMES = sorted(
    name
    for name, v in vars(pb_common).items()
    if isinstance(v, type) and issubclass(v, enum.Enum) and v.__module__ == pb_common.__name__
)

# DESIGN.md section 9.2's import map (line ~776) scopes pricebt.datetime to a curated subset of gs's
# 11 gs_quant.datetime functions, not full parity (day_count_fraction/has_feb_29/relative_date_add/
# time_difference_as_string/to_zulu_string are not ported; relative_date_add's job is instead covered
# by the richer RelativeDate/RelativeDateSchedule API). Iteration here is pricebt-driven, like the
# common-enum subset above, for the same reason.
DATETIME_NAMES = list(pb_datetime.__all__)


@pytest.mark.parametrize("gs_key", GS_DRIVEN_KEYS, ids=GS_DRIVEN_KEYS)
def test_gs_driven_symbol_parity(gs_key):
    failures = diff_symbol(gs_key)
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("name", RISK_2_1_17_ONLY, ids=RISK_2_1_17_ONLY)
def test_2_1_17_only_risk_measures_exist_in_pricebt(name):
    obj = getattr(pb_risk, name, None)
    assert obj is not None, f"pricebt.risk.{name} missing (2.1.17-only measure, DESIGN.md section 8.1)"
    desc = describe(obj)
    assert desc["kind"] == "risk_measure", f"pricebt.risk.{name} is not a RiskMeasure instance: {desc!r}"


def test_no_unexpected_extra_risk_measures():
    """pricebt.risk's RiskMeasure instances are exactly the ported gs catalogue (IR_RISK_DESIGN.md
    section 1): every 1.5.4-present name plus the 2.1.17-only ones the snapshot tool requested but
    did not find (RISK_2_1_17_ONLY, six today). No other extras (DESIGN.md section 12.3)."""
    scope = {k.rsplit(".", 1)[-1] for k in SYMBOLS if k.startswith("gs_quant.risk.")} | set(RISK_2_1_17_ONLY)
    actual = {n for n in pb_risk.__all__ if dataclasses.is_dataclass(getattr(pb_risk, n)) and not isinstance(getattr(pb_risk, n), type)}
    assert actual - scope == set(), f"unexpected extra pricebt risk measures: {sorted(actual - scope)}"


def test_risk_measure_exception_expect_is_enforced(monkeypatch):
    """R2-24's `risk_measure` aspect is a real check, not a skip: an `expect` that does not match
    pricebt fails, and dropping the row brings back the plain gs comparison's failure."""
    key = "gs_quant.risk.IRVanna"
    real = list(EXCEPTIONS)
    rows = [e for e in real if e["symbol"] == key and e["aspect"] == "risk_measure"]
    assert rows, f"no risk_measure exception row for {key}"
    assert diff_symbol(key) == []
    wrong = [dict(e, expect={"class": "RiskMeasureWithCurrencyParameter"}) if e in rows else e for e in real]
    monkeypatch.setitem(globals(), "EXCEPTIONS", wrong)
    assert any(f.startswith("class:") for f in diff_symbol(key)), diff_symbol(key)
    monkeypatch.setitem(globals(), "EXCEPTIONS", [e for e in real if e not in rows])
    assert diff_symbol(key) == ["class: gs='RiskMeasure' expected='RiskMeasure' pb='RiskMeasureWithFiniteDifferenceParameter'"]


def test_pending_row_excuses_only_an_absent_symbol(monkeypatch):
    """R2-23's later-phase symbols: without the `pending` row the absence fails; once pricebt has
    the symbol, the row itself fails (so Phase B/E must delete it and get a real comparison)."""
    key = "gs_quant.risk.PnlExplain"
    assert resolve(key) is None and diff_symbol(key) == []
    real = list(EXCEPTIONS)
    monkeypatch.setitem(globals(), "EXCEPTIONS", [e for e in real if not (e["symbol"] == key and e.get("pending"))])
    assert diff_symbol(key) == ["missing from pricebt entirely"]
    monkeypatch.setitem(globals(), "EXCEPTIONS", real)
    monkeypatch.setattr(pb_risk, "PnlExplain", type("PnlExplain", (), {}), raising=False)
    assert diff_symbol(key) == ["pricebt now has it, but a `pending: Phase E` row still excuses it: delete that row so it is compared"]


@pytest.mark.parametrize("name", COMMON_ENUM_NAMES, ids=COMMON_ENUM_NAMES)
def test_common_enum_parity(name):
    gs_key = f"gs_quant.common.{name}"
    assert gs_key in SYMBOLS, f"pricebt.common.{name} does not name a real gs_quant.common enum"
    pb_cls = getattr(pb_common, name)
    d = _compare_members(gs_key, SYMBOLS[gs_key]["members"], describe(pb_cls)["members"])
    assert d is None, d


@pytest.mark.parametrize("name", DATETIME_NAMES, ids=DATETIME_NAMES)
def test_datetime_function_parity(name):
    gs_key = f"gs_quant.datetime.{name}"
    assert gs_key in SYMBOLS, f"pricebt.datetime.{name} does not name a real gs_quant.datetime function"
    d = _compare_ordered_list(gs_key, "signature", SYMBOLS[gs_key]["signature"], describe(getattr(pb_datetime, name))["signature"])
    assert d is None, d


# ============================================================================ yaml hygiene


def _dev_ids_in_design() -> set:
    text = DESIGN.read_text(encoding="utf8")
    start = text.index("## 11. Deviations from gs")
    end = text.index("## 12. Verification strategy")
    return set(_DEV_ID_RE.findall(text[start:end]))


DESIGN_DEV_IDS = _dev_ids_in_design()


def test_every_exception_has_a_reason_and_a_valid_aspect():
    for e in EXCEPTIONS:
        assert e.get("reason", "").strip(), f"empty reason: {e}"
        assert e["aspect"] in _VALID_ASPECTS, f"unknown aspect {e['aspect']!r}: {e}"


def test_every_exception_dev_id_is_a_real_design_row():
    for e in EXCEPTIONS:
        dev_id = e.get("dev_id")
        if dev_id:
            assert dev_id in DESIGN_DEV_IDS, f"{e['symbol']} aspect={e['aspect']}: dev_id {dev_id!r} has no DESIGN.md section 11 row"


def test_every_dev_id_cited_in_src_is_a_real_design_row():
    """A DEV id in shipped code or text (markers, docstrings, error/contract text) names a DESIGN.md
    section 11 row, so a reader can look it up."""
    src = Path(__file__).parent.parent / "src" / "pricebt"
    cited = {(m, str(p.relative_to(src))) for p in src.rglob("*.py") for m in _DEV_ID_RE.findall(p.read_text(encoding="utf8"))}
    missing = sorted((m, p) for m, p in cited if m not in DESIGN_DEV_IDS)
    assert not missing, f"DEV ids with no DESIGN.md section 11 row: {missing}"


def test_every_exception_symbol_matches_at_least_one_real_symbol():
    known_prefixes = _SCOPE_PREFIXES + ("gs_quant.common.", "gs_quant.datetime.")
    for e in EXCEPTIONS:
        pattern = e["symbol"]
        assert pattern.startswith(known_prefixes), f"{pattern}: not an in-scope symbol prefix"
        if "*" in pattern:
            hits = [k for k in SYMBOLS if _matches(pattern, k)]
            assert hits, f"glob {pattern!r} matches no symbol in the snapshot"


def test_every_exception_was_consulted():
    """Runs the full sweep again (independent of test order/selection) so this test alone proves
    every yaml row is real, not a stale, unreachable permission."""
    for gs_key in GS_DRIVEN_KEYS:
        diff_symbol(gs_key)
    for name in COMMON_ENUM_NAMES:
        gs_key = f"gs_quant.common.{name}"
        _compare_members(gs_key, SYMBOLS[gs_key]["members"], describe(getattr(pb_common, name))["members"])
    unused = [e for e in EXCEPTIONS if id(e) not in _USED]
    assert not unused, "unused exception entries:\n" + "\n".join(f"{e['symbol']} / {e['aspect']}" for e in unused)


def test_gs_quant_is_never_imported():
    assert "gs_quant" not in sys.modules
