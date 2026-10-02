"""pricebt.risk.contracts and the asset-config schema around it (docs/v2/IR_RISK_DESIGN.md section 2,
section 00 R2-9..R2-12; docs/v2/IR_STRICT_CONTRACT.md R3-0/R3-1; pricebt DEV-I11, DEV-I19):
per-instrument measure contracts checked at load time, `unsupported_measures:` declarations (Bond
only: IRSwap/IRSwaption must map every contract measure), `returns: frame` / `scale_columns`,
`instrument:` validation, `UnsupportedMeasureError`, the paste-ready declaration block and the
mapping skeleton.

Configs are self-contained dicts with stdlib expressions (nothing is evaluated: load_asset only
compiles), in test_asset_config.py's `_minimal()` style.
"""
from __future__ import annotations

import copy
import pickle
import warnings
from pathlib import Path

import pandas as pd
import pytest
import yaml

import pricebt.risk as risk
from pricebt.assets import load_asset
from pricebt.common import AssetClass
from pricebt.errors import AssetEvaluationError, ConfigError, MarketDataUnavailable, NotSupportedError, UnsupportedMeasureError
from pricebt.risk import contracts
from pricebt.risk.contracts import MappedFunction

pytestmark = pytest.mark.core

REPO = Path(__file__).resolve().parents[1]
IR_CONFIGS = [
    "tests/assets/toy_usd_irs.yaml",
    "tests/assets/toy_eur_irs.yaml",
    "tests/assets/toy_usd_swaption.yaml",
    "tests/assets/toy_usd_irs_full.yaml",
    "tests/assets/toy_usd_bond.yaml",
    "configs/assets/usd_sofr_ois_interest_rate_swap.yaml",
    "tests/skills/fixtures/check_asset/bad_dv01_sign.yaml",
    "tests/skills/fixtures/check_asset/bad_par_rate_decimal.yaml",
    "tests/skills/fixtures/check_asset/bad_unpinned_maturity.yaml",
    "tests/skills/fixtures/check_asset/bad_weekend_raises.yaml",
    "skills/pricebt-connect-pricing-library/example/meridian_usd_irs.yaml",
    "skills/pricebt-connect-pricing-library/example/mistakes/dv01_sign_not_flipped.yaml",
    "skills/pricebt-connect-pricing-library/example/mistakes/maturity_not_pinned.yaml",
    "skills/pricebt-connect-pricing-library/example/mistakes/par_rate_in_percent.yaml",
    "skills/pricebt-connect-pricing-library/references/config-template.yaml",
    "skills/pricebt-connect-pricing-library/references/config-template-swaption.yaml",
    "skills/pricebt-connect-pricing-library/references/config-template-bond.yaml",
]
STRICT = ("IRSwap", "IRSwaption")
STRICT_MSG = "IRSwap/IRSwaption configs must map every contract measure; unsupported_measures cannot satisfy them"
NOT_MAPPED = "not mapped (IRSwap/IRSwaption require a mapping for every contract measure)"


def _declare_all(instrument, except_=("Price",), reason="test: not computed"):
    return {r.measure: reason for r in contracts.contract_for(instrument) if r.measure not in except_}


def _all_forms(instrument, except_=("Price",)):
    return [(r.measure, f) for r in contracts.contract_for(instrument) for f in r.forms if r.measure not in except_]


def _bond(**overrides):
    """A minimal Bond config that satisfies its (map-or-declare) contract: Price mapped, everything
    else declared."""
    cfg = {
        "schema_version": 1,
        "asset": "bond_asset",
        "instrument": "Bond",
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"npv": {"expr": "0.0", "unit": "ccy"}},
        "risk_measures": {"Price": "npv"},
        "unsupported_measures": _declare_all("Bond"),
    }
    cfg.update(overrides)
    return cfg


def _filled_skeleton(instrument, missing):
    """The mapping skeleton with every stub expression replaced by a literal: what an author gets
    after writing each expression."""
    return yaml.safe_load(contracts.mapping_skeleton(instrument, missing).replace(contracts.SKELETON_EXPR, "0.0"))


def _strict(instrument="IRSwap", **overrides):
    """A minimal IRSwap/IRSwaption config that maps the whole strict contract (from the filled
    skeleton) and declares nothing."""
    filled = _filled_skeleton(instrument, _all_forms(instrument))
    cfg = {
        "schema_version": 1,
        "asset": "swap_asset",
        "instrument": instrument,
        "currency": "USD",
        "market": {"expr": "1"},
        "functions": {"npv": {"expr": "0.0", "unit": "ccy"}, **filled["functions"]},
        "portfolio_functions": filled["portfolio_functions"],
        "risk_measures": {"Price": "npv", **filled["risk_measures"]},
    }
    cfg.update(overrides)
    return cfg


def _load_error(cfg) -> ConfigError:
    with pytest.raises(ConfigError) as exc_info:
        load_asset(cfg)
    return exc_info.value


def _fn(name, unit, *, swq=None, returns="scalar", cols=()):
    extensive = unit in {"ccy", "ccy_per_bp", "ccy_per_bp2", "number"}
    return MappedFunction(name, unit, extensive if swq is None else swq, returns, tuple(cols))


def _check(instrument, mapped, unsupported):
    """`mapped` as {key: (scalar, bucketed)}; `unsupported` in YAML shape (a bare reason means "*",
    which check() accepts as well as the loader's normalised {"*": reason})."""
    return contracts.check(instrument, {k: {"scalar": s, "bucketed": b} for k, (s, b) in mapped.items()}, unsupported)


def _good(req, form):
    """A function that satisfies `req`'s `form`."""
    units, intensive = contracts.KINDS[req.kind]
    if units is None:
        return _fn(req.measure, "ccy", returns="frame", cols=contracts.FRAME_SCALE_COLUMNS[req.measure])
    unit = "decimal" if "decimal" in units else min(units)
    return _fn(req.measure, unit, swq=False if intensive else None, returns="buckets" if form == "bucketed" else "scalar")


def _all_mapped(instrument, **override):
    """{key: (scalar, bucketed)} mapping every contract form of `instrument`, then `override`."""
    out = {r.measure: (next((_good(r, f) for f in r.forms if f != "bucketed"), None), _good(r, "bucketed") if "bucketed" in r.forms else None)
           for r in contracts.contract_for(instrument)}
    out.update(override)
    return out


# ------------------------------------------------------------------------------------ the table itself


def test_contract_table_shape():
    assert set(contracts.CONTRACTS) == {"IRSwap", "IRSwaption", "Bond"}
    for cls, reqs in contracts.CONTRACTS.items():
        names = [r.measure for r in reqs]
        assert len(names) == len(set(names)), cls
        for r in reqs:
            assert r.kind in contracts.KINDS, r
            assert r.forms and set(r.forms) <= set(contracts.FORMS), r
            assert r.doc and "\n" not in r.doc, r
            assert (r.kind == "table") == (r.forms == ("frame",)), r
    swap = {r.measure for r in contracts.contract_for("IRSwap")}
    bond = {r.measure for r in contracts.contract_for("Bond")}
    assert {r.measure for r in contracts.contract_for("IRSwaption")} - swap == {"ProbabilityOfExercise"}
    assert bond - swap == {"LightningDV01", "LightningOAS"}
    # R3-1: the seven strict-only rows (ParSpread is in both, with each class's own text)
    assert swap - bond == {"FairPremium", "ForwardPrice", "PremiumCents", "LocalAnnuityInCents", "CompoundedFixedRate", "CRIFIRCurve", "PnlExplain"}
    assert len(swap) == 27 and [r.measure for r in contracts.contract_for("IRSwap")][-8:] == [
        "ParSpread", "FairPremium", "ForwardPrice", "PremiumCents", "LocalAnnuityInCents", "CompoundedFixedRate", "CRIFIRCurve", "PnlExplain"]
    assert set(contracts.FRAME_COLUMNS) == {r.measure for reqs in contracts.CONTRACTS.values() for r in reqs if r.kind == "table"}
    assert set(contracts.FRAME_SCALE_COLUMNS) == set(contracts.FRAME_COLUMNS)
    assert all(set(contracts.FRAME_SCALE_COLUMNS[m]) <= set(cols) for m, cols in contracts.FRAME_COLUMNS.items())


def test_every_contract_measure_is_a_catalogue_name():
    # measure names are strings (the catalogue may land separately); a typo here would make a
    # config declare or map a name nothing can ever request. PnlExplain is a class (relative measure).
    for reqs in contracts.CONTRACTS.values():
        for r in reqs:
            obj = getattr(risk, r.measure, None)
            assert isinstance(obj, risk.RiskMeasure) or obj is risk.PnlExplain, r.measure


def test_kinds_intensive_flag_semantics():
    assert contracts.KINDS["rate"] == (frozenset({"bp", "pct", "decimal"}), True)
    assert contracts.KINDS["notional_level"] == (frozenset({"bp", "pct", "decimal", "number"}), True)
    assert contracts.KINDS["value"][1] is False  # R2-11: False = no constraint, not "must be extensive"


def test_classes_without_a_contract_are_unaffected():
    for cls in ("FXOption", "EqOption", "InflationSwap", "Cash", "FXForward", "ConfigInstrument"):
        assert contracts.contract_for(cls) == ()
        assert not contracts.is_strict(cls)
        assert _check(cls, {"Price": (_fn("f", "bp"), None)}, {}) == ([], [], [])
    cfg = load_asset(_bond(instrument="FXOption", unsupported_measures={}))  # only Price, as before
    assert cfg.instrument == "FXOption" and cfg.unsupported_measures == {}


# ------------------------------------------------------------------------------------ R3-0 shared data


def test_strict_classes_and_shared_constants():
    assert contracts.STRICT_CLASSES == frozenset({"IRSwap", "IRSwaption"})
    assert contracts.is_strict("IRSwap") and contracts.is_strict("IRSwaption") and not contracts.is_strict("Bond")
    assert set(contracts.ZERO_BY_CONVENTION) == contracts.STRICT_CLASSES
    for cls, names in contracts.ZERO_BY_CONVENTION.items():
        assert names <= {r.measure for r in contracts.contract_for(cls)}, cls
    # pinned exactly: a literal 0 is honest only where the contract text defines the value as 0
    assert contracts.ZERO_BY_CONVENTION["IRSwap"] == {
        "IRVega", "IRVanna", "IRVolga", "IRAnnualImpliedVol", "IRAnnualATMImpliedVol", "IRDailyImpliedVol", "IRBasis", "IRXccyDelta"}
    assert contracts.ZERO_BY_CONVENTION["IRSwaption"] == {"IRBasis", "IRXccyDelta"}
    assert contracts.SIMM_IR_TENORS == ("2w", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "10y", "15y", "20y", "30y")
    crif = next(r for r in contracts.contract_for("IRSwap") if r.measure == "CRIFIRCurve")
    assert " ".join(contracts.SIMM_IR_TENORS) in crif.doc  # the contract text and the constant agree


def _ir_relevant_names():
    """Every pricebt.risk measure with asset_class Rates or None, plus the relative-measure classes."""
    out = []
    for name in risk.__all__:
        obj = getattr(risk, name)
        if isinstance(obj, risk.RiskMeasure):
            if obj.asset_class in (None, AssetClass.Rates):
                out.append(name)
        elif isinstance(obj, type) and issubclass(obj, (risk.PnlExplain, risk.PnlPredictLive)):
            out.append(name)
    return out


def test_excluded_is_exactly_what_the_strict_contract_leaves_out():
    """R3-1: "all the IRSwap- and IRSwaption-related measures" made checkable. Every IR-relevant
    catalogue name is a contract measure, a preset/fallback of one, or EXCLUDED with a reason; and
    EXCLUDED lists nothing else (no stale entries)."""
    contract = {r.measure for r in contracts.contract_for("IRSwaption")}
    names = _ir_relevant_names()
    assert {"PnlExplain", "PnlExplainClose", "PnlExplainLive", "PnlPredictLive", "IRDeltaParallel", "BaseCPI"} <= set(names)  # the filter sees them
    uncovered = {n for n in names if n not in contract and contracts.base_measure(n)[0] not in contract}
    assert uncovered == set(contracts.EXCLUDED)
    assert all(reason.strip() for reason in contracts.EXCLUDED.values())


# ------------------------------------------------------------------------------------ check(): error paths (Bond: map or declare)


def test_missing_measure_is_a_problem_and_listed_as_missing():
    problems, _, missing = _check("Bond", {"Price": (_fn("npv", "ccy"), None)}, {})
    assert ("IRGammaParallel", "scalar") in missing and ("Cashflows", "frame") in missing
    assert ("Price", "scalar") not in missing
    assert any(p.startswith("IRGammaParallel (scalar): neither mapped nor declared") for p in problems)
    assert len(problems) == len(contracts.contract_for("Bond")) - 1  # one line per measure (IRDelta: both forms on one line)


def test_missing_form_is_a_problem():
    unsupported = _declare_all("Bond", except_=("Price", "IRDelta"))
    problems, _, missing = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRDelta": (_fn("dv01", "ccy_per_bp"), None)}, unsupported)
    assert missing == [("IRDelta", "bucketed")]
    assert len(problems) == 1 and problems[0].startswith("IRDelta (bucketed):")
    # declaring just that form satisfies it
    assert _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRDelta": (_fn("dv01", "ccy_per_bp"), None)}, {**unsupported, "IRDelta": {"bucketed": "no ladder"}}).problems == []


def test_wrong_unit_is_a_problem():
    problems, _, missing = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRDelta": (_fn("dv01", "ccy"), _fn("ladder", "ccy_per_bp", returns="buckets"))}, _declare_all("Bond", except_=("Price", "IRDelta")))
    assert missing == []
    assert problems == ["IRDelta (scalar): function 'dv01' has unit 'ccy'; allowed ['ccy_per_bp']"]


def test_rate_level_must_be_intensive():
    unsupported = _declare_all("Bond", except_=("Price", "IRFwdRate"))
    ok = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRFwdRate": (_fn("par", "bp"), None)}, unsupported)
    assert ok.problems == []
    bad = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRFwdRate": (_fn("par", "bp", swq=True), None)}, unsupported)
    assert len(bad.problems) == 1 and "must be intensive" in bad.problems[0]
    # `number` is extensive by default: a time level in `number` must say scale_with_quantity false
    unsupported = _declare_all("Bond", except_=("Price", "ExpiryInYears"))
    assert "must be intensive" in _check("Bond", {"Price": (_fn("npv", "ccy"), None), "ExpiryInYears": (_fn("t", "number"), None)}, unsupported).problems[0]
    assert _check("Bond", {"Price": (_fn("npv", "ccy"), None), "ExpiryInYears": (_fn("t", "number", swq=False), None)}, unsupported).problems == []


def test_extensive_price_marked_non_scaling_still_loads():
    # R2-11: `value` has no intensivity constraint, so a checker fixture with a non-scaling npv
    # loads and fails at its checker row instead
    cfg = _bond(functions={"npv": {"expr": "0.0", "unit": "ccy", "scale_with_quantity": False}})
    assert load_asset(cfg).functions["npv"].scale_with_quantity is False


def test_frame_required_for_a_table_measure():
    unsupported = _declare_all("Bond", except_=("Price", "Cashflows"))
    base = {"Price": (_fn("npv", "ccy"), None)}
    problems, _, missing = _check("Bond", {**base, "Cashflows": (_fn("cf", "ccy"), None)}, unsupported)
    assert missing == []  # a shape mismatch is one problem, not also a "missing" one
    assert len(problems) == 1 and "returns: frame" in problems[0]
    good = _fn("cf", "ccy", returns="frame", cols=("payment_amount", "notional"))
    assert _check("Bond", {**base, "Cashflows": (good, None)}, unsupported).problems == []
    no_amount = _fn("cf", "ccy", returns="frame", cols=("notional",))
    assert "must include ['payment_amount']" in _check("Bond", {**base, "Cashflows": (no_amount, None)}, unsupported).problems[0]
    not_scaling = _fn("cf", "ccy", swq=False, returns="frame", cols=("payment_amount",))
    assert "must scale with the position" in _check("Bond", {**base, "Cashflows": (not_scaling, None)}, unsupported).problems[0]


def test_frame_for_a_number_measure_is_a_problem():
    problems = _check("Bond", {"Price": (_fn("npv", "ccy", returns="frame", cols=("x",)), None)}, _declare_all("Bond")).problems
    assert problems == ["Price (scalar): function 'npv' returns a frame; this measure needs a number"]


def test_mapped_and_declared_is_a_warning_not_a_problem():
    unsupported = _declare_all("Bond", except_=("Price",))  # IRDelta declared as a whole...
    res = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRDelta": (_fn("dv01", "ccy_per_bp"), None)}, unsupported)  # ...but its scalar is mapped
    assert res.problems == []
    assert res.warnings == ["IRDelta is declared unsupported (every form) but mapped (scalar); the mapping is used -- remove or narrow the stale declaration"]
    # and a non-contract measure behaves the same way (R2-9 is generic)
    res = _check("ConfigInstrument", {"Price": (_fn("f", "ccy"), None), "IRTheta": (_fn("th", "ccy"), None)}, {"IRTheta": {"scalar": "old"}})
    assert len(res.warnings) == 1 and res.warnings[0].startswith("IRTheta is declared unsupported (scalar)")


def test_mapped_and_declared_warns_at_load():
    cfg = _bond(functions={"npv": {"expr": "0.0", "unit": "ccy"}, "dv01": {"expr": "1.0", "unit": "ccy_per_bp"}}, risk_measures={"Price": "npv", "IRDelta": "dv01"})
    with pytest.warns(UserWarning, match=r"asset bond_asset: IRDelta is declared unsupported \(every form\) but mapped \(scalar\)"):
        loaded = load_asset(cfg)
    assert loaded.risk_measures["IRDelta"].scalar == "dv01"  # the mapping wins
    assert loaded.unsupported_measures["IRDelta"] == {"*": "test: not computed"}


def test_preset_key_counts_toward_its_base():
    unsupported = {**_declare_all("Bond", except_=("Price", "IRDelta")), "IRDelta": {"bucketed": "no ladder"}}
    res = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRDeltaParallel": (_fn("dv01", "ccy_per_bp"), None)}, unsupported)
    assert res.problems == [] and res.missing == []
    # its unit is checked against the base's kind, and the message names the key used
    bad = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRDeltaParallel": (_fn("dv01", "bp"), None)}, unsupported)
    assert bad.problems == ["IRDelta (scalar, via IRDeltaParallel): function 'dv01' has unit 'bp'; allowed ['ccy_per_bp']"]
    # a stale declaration of the base is flagged through the preset too
    stale = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRDeltaParallel": (_fn("dv01", "ccy_per_bp"), None)}, {**unsupported, "IRDelta": "no delta"})
    assert stale.warnings and "scalar via IRDeltaParallel" in stale.warnings[0]


def test_preset_bucketed_slot_does_not_provide_the_base_bucketed_form():
    # pricing never routes a bare IRDelta (bucketed) request to the IRDeltaParallel key
    unsupported = {**_declare_all("Bond", except_=("Price", "IRDelta")), "IRDelta": {"scalar": "no scalar"}}
    res = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "IRDeltaParallel": (None, _fn("ladder", "ccy_per_bp", returns="buckets"))}, unsupported)
    assert res.missing == [("IRDelta", "bucketed")]


def test_provided_forms_is_what_the_contract_counts():
    mapped = {"Price": {"scalar": _fn("npv", "ccy")}, "IRDeltaParallel": {"scalar": _fn("dv01", "ccy_per_bp"), "bucketed": _fn("ladder", "ccy_per_bp", returns="buckets")},
              "IRGammaParallelLocalCcy": {"scalar": _fn("g", "ccy_per_bp2")}, "IRDeltaLocalCcy": {"bucketed": _fn("ladder", "ccy_per_bp", returns="buckets")},
              "PnlExplainClose": {"bucketed": _fn("pnl", "ccy", returns="buckets")}}
    assert contracts.provided_forms("IRSwap", mapped) == {
        ("Price", "scalar"): "Price", ("IRDelta", "scalar"): "IRDeltaParallel",  # the preset's bucketed slot does not count
        ("IRGammaParallel", "scalar"): "IRGammaParallelLocalCcy", ("IRDelta", "bucketed"): "IRDeltaLocalCcy",
        ("PnlExplain", "bucketed"): "PnlExplainClose",  # a class preset (R3-1)
    }


# ------------------------------------------------------------------------------------ declarations the contract cannot count (warnings)


def _declaration_warnings(**extra):
    return _check("Bond", {"Price": (_fn("npv", "ccy"), None)}, {**_declare_all("Bond"), **extra}).warnings


def test_declaring_a_preset_name_warns_to_declare_the_base():
    assert _declaration_warnings(IRDeltaParallel="old") == [
        "IRDeltaParallel is a preset or fallback of IRDelta; declare IRDelta instead (the contract counts only IRDelta, and a request for IRDeltaParallel falls back to IRDelta's mapping or declaration)"
    ]


def test_declaring_a_form_outside_the_contract_row_warns():
    assert _declaration_warnings(IRFwdRate={"scalar": "x", "bucketed": "y"}) == [
        "IRFwdRate declares ['bucketed'] unsupported but its Bond contract row has only ['scalar']; remove the extra form(s)"
    ]


def test_declaring_an_unknown_name_warns_with_a_suggestion():
    (w,) = _declaration_warnings(IRVanaa="typo")
    assert w.startswith("IRVanaa is neither in the Bond contract nor a pricebt.risk measure (did you mean 'IRVanna'?)")
    # a catalogue measure outside the contract is a working declaration (pricing honours it by name)
    assert _declaration_warnings(FXDelta="no fx") == []
    assert _declaration_warnings(PnlPredictLive="live only") == []  # a relative-measure class is a catalogue name


def test_undeclarable_names_warn_at_load():
    with pytest.warns(UserWarning, match="asset bond_asset: IRVegaParallel is a preset or fallback of IRVega"):
        load_asset(_bond(unsupported_measures={**_declare_all("Bond"), "IRVegaParallel": "old"}))


def test_base_measure():
    assert contracts.base_measure("IRDeltaParallel") == ("IRDelta", "scalar")
    assert contracts.base_measure("IRVegaParallel") == ("IRVega", "scalar")
    assert contracts.base_measure("IRDeltaLocalCcy") == ("IRDelta", None)  # no aggregation level: each slot is its own form
    assert contracts.base_measure("IRDelta") == ("IRDelta", None)
    assert contracts.base_measure("IRTheta") == ("IRTheta", None)  # not in the catalogue: itself
    assert contracts.base_measure("FXDelta") == ("FXDelta", None)
    assert contracts.base_measure("PnlExplainClose") == ("PnlExplain", None)
    assert contracts.base_measure("PnlExplainLive") == ("PnlExplainLive", None)  # EXCLUDED, not a preset
    if getattr(risk, "IRGammaParallelLocalCcy", None) is not None:  # DEV-I16, landing with the catalogue
        assert contracts.base_measure("IRGammaParallelLocalCcy") == ("IRGammaParallel", None)


def test_bond_and_swaption_extras():
    assert ("ProbabilityOfExercise", "scalar") in _check("IRSwaption", {"Price": (_fn("npv", "ccy"), None)}, {}).missing
    res = _check("Bond", {"Price": (_fn("npv", "ccy"), None), "LightningOAS": (_fn("oas", "bp", swq=True), None)}, _declare_all("Bond", except_=("Price", "LightningDV01", "LightningOAS", "ParSpread")))
    assert res.missing == [("LightningDV01", "scalar"), ("ParSpread", "scalar")]
    assert any("LightningOAS (scalar)" in p and "must be intensive" in p for p in res.problems)


# ------------------------------------------------------------------------------------ R3-0: strict classes (IRSwap, IRSwaption)


@pytest.mark.parametrize("instrument", STRICT)
def test_fully_mapped_strict_config_loads_without_warnings(instrument):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_asset(_strict(instrument))
    assert cfg.unsupported_measures == {}
    assert {m for m, _f in cfg.provided_forms} >= {r.measure for r in contracts.contract_for(instrument)}
    assert _check(instrument, _all_mapped(instrument), {}) == ([], [], [])


@pytest.mark.parametrize("instrument", STRICT)
@pytest.mark.parametrize("declaration, shown", [
    ({"IRVega": "no vol model"}, "IRVega,"),  # the whole measure
    ({"IRDelta": {"bucketed": "no ladder"}}, "IRDelta,"),  # one form
    ({"CRIFIRCurve": {"frame": "no SIMM"}}, "CRIFIRCurve,"),  # a new R3-1 row
    ({"IRDeltaParallel": "old"}, "IRDeltaParallel (a preset or fallback of IRDelta)"),  # presets resolving to a contract measure
    ({"IRGammaParallelLocalCcy": "old"}, "IRGammaParallelLocalCcy (a preset or fallback of IRGammaParallel)"),
    ({"PnlExplainClose": "old"}, "PnlExplainClose (a preset or fallback of PnlExplain)"),
], ids=["measure", "form", "crif", "IRDeltaParallel", "IRGammaParallelLocalCcy", "PnlExplainClose"])
def test_strict_class_rejects_every_declaration_of_a_contract_measure(instrument, declaration, shown):
    """Fully mapped, so for Bond each of these would be a stale-declaration warning (R2-9); for a
    strict class it is a load error, and no warning is issued instead."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        err = _load_error(_strict(instrument, unsupported_measures=declaration))
    msg = str(err)
    assert err.key == "risk_measures"
    assert f"unsupported_measures declares {shown}" in msg and STRICT_MSG in msg
    assert f"a {instrument} contract measure" in msg


@pytest.mark.parametrize("instrument", STRICT)
def test_a_declaration_never_satisfies_a_strict_row(instrument):
    """IRVega's bucketed form is not mapped and is declared: for Bond the declaration satisfies it;
    for a strict class the form stays missing (in the skeleton) and the declaration is a second problem."""
    mapped = _all_mapped(instrument, IRVega=(_fn("vega", "ccy_per_bp"), None))
    res = _check(instrument, mapped, {"IRVega": {"bucketed": "no cube"}})
    assert res.missing == [("IRVega", "bucketed")]
    assert res.problems[0].startswith(f"IRVega (bucketed): {NOT_MAPPED} -- ")
    assert len(res.problems) == 2 and STRICT_MSG in res.problems[1]
    assert res.warnings == []
    # the same declaration satisfies Bond's row
    bond = _check("Bond", _all_mapped("Bond", IRVega=(_fn("vega", "ccy_per_bp"), None)), {"IRVega": {"bucketed": "no cube"}})
    assert bond == ([], [], [])


def test_strict_class_keeps_the_warnings_for_names_outside_the_contract():
    with pytest.warns(UserWarning, match=r"IRVanaa is neither in the IRSwap contract nor a pricebt\.risk measure \(did you mean 'IRVanna'\?\)"):
        load_asset(_strict(unsupported_measures={"IRVanaa": "typo"}))
    with pytest.warns(UserWarning, match="InflationDeltaParallel is a preset or fallback of InflationDelta; declare InflationDelta instead"):
        load_asset(_strict(unsupported_measures={"InflationDeltaParallel": "no inflation"}))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_asset(_strict(unsupported_measures={"FXDelta": "no fx"}))  # a catalogue measure outside the contract
    assert cfg.unsupported_measures == {"FXDelta": {"*": "no fx"}}


@pytest.mark.parametrize("instrument", STRICT)
def test_strict_error_lists_every_gap_at_once_and_ends_with_the_skeleton(instrument):
    cfg = _strict(instrument, functions={"npv": {"expr": "0.0", "unit": "ccy"}, "par": {"expr": "1.0", "unit": "ccy"}},
                  portfolio_functions={}, risk_measures={"Price": "npv", "IRFwdRate": "par"}, unsupported_measures={"Theta": "no carry"})
    msg = str(_load_error(cfg))
    reqs = contracts.contract_for(instrument)
    gaps = [r.measure for r in reqs if r.measure not in ("Price", "IRFwdRate")]
    # every gap, plus IRFwdRate's two (unit; a ccy function scales, a rate must not) and the declaration
    assert msg.startswith(f"[swap_asset:risk_measures] {len(gaps) + 3} measure-contract problem(s) for instrument {instrument}")
    for m in gaps:
        assert f"\n  - {m} (" in msg and NOT_MAPPED in msg.split(f"\n  - {m} (", 1)[1].splitlines()[0], m
    assert "IRFwdRate (scalar): function 'par' has unit 'ccy'" in msg
    assert "unsupported_measures declares Theta," in msg
    assert "unsupported_measures:" not in msg.split("\nMap each missing measure", 1)[1]  # no declaration block for a strict class
    skeleton = msg[msg.index("\nfunctions:\n") + 1:]
    assert msg.rstrip().splitlines()[-1].startswith("  ")  # the skeleton is last
    assert skeleton == contracts.mapping_skeleton(instrument, _check(instrument, {"Price": (_fn("npv", "ccy"), None), "IRFwdRate": (_fn("par", "ccy"), None)}, {}).missing)


@pytest.mark.parametrize("instrument", STRICT)
def test_mapping_skeleton_names_every_missing_form_with_its_unit_and_does_not_load(instrument, tmp_path):
    missing = _all_forms(instrument)
    text = contracts.mapping_skeleton(instrument, missing)
    sk = yaml.safe_load(text)
    reqs = {r.measure: r for r in contracts.contract_for(instrument)}
    served = set()
    for measure, slots in sk["risk_measures"].items():
        for slot, fname in slots.items():
            form = "bucketed" if slot == "bucketed" else "frame" if reqs[measure].kind == "table" else "scalar"
            served.add((measure, form))
            units, intensive = contracts.KINDS[reqs[measure].kind]
            spec = (sk["portfolio_functions"] if form == "bucketed" else sk["functions"])[fname]
            assert spec["expr"] == contracts.SKELETON_EXPR
            assert f"# {measure} ({form}):" in text.split(f"  {fname}:", 1)[1].splitlines()[0]
            if units is None:
                assert spec["returns"] == "frame" and set(contracts.FRAME_SCALE_COLUMNS[measure]) <= set(spec["scale_columns"])
            else:
                assert spec["unit"] in units, (measure, spec)
                assert spec.get("scale_with_quantity", True) is (not intensive), measure
                assert spec.get("returns", "scalar") == ("buckets" if form == "bucketed" else "scalar")
    assert served == set(missing)
    # pasted as-is it cannot load: every stub is a syntax error
    cfg = _strict(instrument, functions={"npv": {"expr": "0.0", "unit": "ccy"}, **sk["functions"]}, portfolio_functions=sk["portfolio_functions"],
                  risk_measures={"Price": "npv", **sk["risk_measures"]})
    err = _load_error(cfg)
    assert "syntax error" in str(err) and contracts.SKELETON_EXPR in str(err)
    # once each stub computes something, it loads (the _strict fixture is exactly that)
    load_asset(_strict(instrument))


def test_mapping_skeleton_merges_with_a_partly_mapped_measure():
    assert contracts.mapping_skeleton("IRSwap", [("IRDelta", "bucketed")]) == (
        "portfolio_functions:\n"
        "  ir_delta_buckets:   # IRDelta (bucketed): kind sens1, unit one of ccy_per_bp\n"
        f"    expr: '{contracts.SKELETON_EXPR}'\n"
        "    unit: ccy_per_bp\n"
        "    returns: buckets\n"
        "risk_measures:\n"
        "  IRDelta: {bucketed: ir_delta_buckets}\n"
    )
    with pytest.raises(SyntaxError):
        compile(contracts.SKELETON_EXPR, "<stub>", "eval")


# ------------------------------------------------------------------------------------ R3-1: one test per new row's rule


def _new_row(instrument, key, scalar=None, bucketed=None):
    return _check(instrument, _all_mapped(instrument, **{key: (scalar, bucketed)}), {})


@pytest.mark.parametrize("instrument", STRICT)
def test_par_spread_and_compounded_fixed_rate_are_intensive_rates(instrument):
    for m in ("ParSpread", "CompoundedFixedRate"):
        for unit in ("bp", "pct", "decimal"):
            assert _new_row(instrument, m, _fn("r", unit, swq=False)).problems == [], (m, unit)
        assert "must be intensive" in _new_row(instrument, m, _fn("r", "bp", swq=True)).problems[0]
        assert "allowed ['bp', 'decimal', 'pct']" in _new_row(instrument, m, _fn("r", "ccy")).problems[0]
        assert "allowed" in _new_row(instrument, m, _fn("r", "number", swq=False)).problems[0]  # number is not a rate unit


@pytest.mark.parametrize("instrument", STRICT)
def test_fair_premium_and_forward_price_are_ccy_values(instrument):
    for m in ("FairPremium", "ForwardPrice"):
        assert _new_row(instrument, m, _fn("v", "ccy")).problems == []
        assert _new_row(instrument, m, _fn("v", "ccy", swq=False)).problems == []  # value: no intensivity rule (R2-11)
        # DEV-I19: gs declares ForwardPrice in BPS; pricebt follows its docstring (ccy)
        assert _new_row(instrument, m, _fn("v", "bp", swq=False)).problems == [f"{m} (scalar): function 'v' has unit 'bp'; allowed ['ccy']"]


@pytest.mark.parametrize("instrument", STRICT)
def test_premium_cents_and_local_annuity_in_cents_are_intensive_notional_levels(instrument):
    for m in ("PremiumCents", "LocalAnnuityInCents"):
        for unit in ("bp", "pct", "decimal"):
            assert _new_row(instrument, m, _fn("n", unit)).problems == [], (m, unit)
        assert _new_row(instrument, m, _fn("n", "number", swq=False)).problems == []
        assert "must be intensive" in _new_row(instrument, m, _fn("n", "number")).problems[0]  # number defaults to extensive
        assert "allowed ['bp', 'decimal', 'number', 'pct']" in _new_row(instrument, m, _fn("n", "ccy")).problems[0]


@pytest.mark.parametrize("instrument", STRICT)
def test_crif_ir_curve_is_a_frame_whose_amount_scales(instrument):
    assert _new_row(instrument, "CRIFIRCurve", _fn("crif", "ccy", returns="frame", cols=("Amount",))).problems == []
    (p,) = _new_row(instrument, "CRIFIRCurve", _fn("crif", "ccy")).problems
    assert "returns: frame" in p
    (p,) = _new_row(instrument, "CRIFIRCurve", _fn("crif", "ccy", returns="frame", cols=("Other",))).problems
    assert "must include ['Amount']" in p
    (p,) = _new_row(instrument, "CRIFIRCurve", _fn("crif", "ccy", swq=False, returns="frame", cols=("Amount",))).problems
    assert "must scale with the position" in p


def test_validate_frame_crif_ir_curve():
    cols = list(contracts.FRAME_COLUMNS["CRIFIRCurve"])
    assert cols == ["RiskType", "Qualifier", "Bucket", "Label1", "Label2", "Amount", "AmountCurrency"]
    contracts.validate_frame("CRIFIRCurve", pd.DataFrame(columns=cols))  # dead instrument: no rows, the columns
    with pytest.raises(ConfigError, match=r"missing required column\(s\) \['Label2'\]"):
        contracts.validate_frame("CRIFIRCurve", pd.DataFrame(columns=[c for c in cols if c != "Label2"]))


@pytest.mark.parametrize("instrument", STRICT)
def test_pnl_explain_is_a_bucketed_ccy_value(instrument):
    """A `returns: buckets` portfolio function lands in the bucketed slot even from a bare string
    (assets.config `_risk_target_kind`), so the row's form is bucketed; a PnlExplainClose key serves it."""
    buckets = _fn("pnl", "ccy", returns="buckets")
    assert _new_row(instrument, "PnlExplain", None, buckets).problems == []
    mapped = {k: v for k, v in _all_mapped(instrument).items() if k != "PnlExplain"}
    assert _check(instrument, {**mapped, "PnlExplainClose": (None, buckets)}, {}).problems == []
    assert _new_row(instrument, "PnlExplain", None, _fn("pnl", "bp", swq=False, returns="buckets")).problems == ["PnlExplain (bucketed): function 'pnl' has unit 'bp'; allowed ['ccy']"]
    res = _new_row(instrument, "PnlExplain", _fn("pnl", "ccy"))  # a scalar function does not provide the bucketed form
    assert res.missing == [("PnlExplain", "bucketed")]
    # and at load: the shipped shape `PnlExplain: pnl_explain` with a buckets portfolio function
    cfg = _strict(instrument)
    cfg["portfolio_functions"] = {**cfg["portfolio_functions"], "pnl_explain": {"expr": "[]", "unit": "ccy", "returns": "buckets"}}
    cfg["risk_measures"] = {**cfg["risk_measures"], "PnlExplain": "pnl_explain"}
    assert load_asset(cfg).risk_measures["PnlExplain"].bucketed == "pnl_explain"


# ------------------------------------------------------------------------------------ load_asset: contract errors (Bond)


def test_one_config_error_lists_every_problem_and_ends_with_the_block():
    cfg = _bond(unsupported_measures={}, functions={"npv": {"expr": "0.0", "unit": "ccy"}, "par": {"expr": "1.0", "unit": "ccy"}}, risk_measures={"Price": "npv", "IRFwdRate": "par"})
    err = _load_error(cfg)
    msg = str(err)
    assert err.asset == "bond_asset" and err.key == "risk_measures"
    assert "IRFwdRate (scalar): function 'par' has unit 'ccy'" in msg
    assert "IRGammaParallel (scalar): neither mapped nor declared" in msg
    assert msg.rstrip().splitlines()[-1].startswith("  ParSpread: ")  # the paste-ready block is last
    assert "\nunsupported_measures:\n" in msg


def test_paste_ready_block_makes_the_config_load(tmp_path):
    cfg = _bond(unsupported_measures={}, functions={"npv": {"expr": "0.0", "unit": "ccy"}, "dv01": {"expr": "1.0", "unit": "ccy_per_bp"}}, risk_measures={"Price": "npv", "IRDelta": "dv01"})
    msg = str(_load_error(copy.deepcopy(cfg)))
    lines = msg.splitlines()
    block = "\n".join(lines[lines.index("unsupported_measures:"):]) + "\n"
    assert '  IRDelta: {bucketed: "TODO: why your library cannot compute this"}' in block
    del cfg["unsupported_measures"]
    path = tmp_path / "pasted.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False) + block, encoding="utf8")  # through pricebt's strict loader
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        loaded = load_asset(path)
    assert loaded.unsupported_measures["IRDelta"] == {"bucketed": "TODO: why your library cannot compute this"}  # R2-12: TODO reasons load
    assert loaded.unsupported_measures["Theta"] == {"*": "TODO: why your library cannot compute this"}


def test_unsupported_block_quotes_reasons_and_orders_by_contract():
    block = contracts.unsupported_block("IRSwaption", [("ProbabilityOfExercise", "scalar"), ("IRVega", "bucketed"), ("IRDelta", "scalar"), ("IRDelta", "bucketed")], reason='a: "b"')
    assert block == 'unsupported_measures:\n  IRDelta: "a: \\"b\\""\n  IRVega: {bucketed: "a: \\"b\\""}\n  ProbabilityOfExercise: "a: \\"b\\""\n'
    assert yaml.safe_load(block)["unsupported_measures"]["IRVega"] == {"bucketed": 'a: "b"'}


@pytest.mark.parametrize("value, key", [
    ("", "unsupported_measures.Theta"),
    ("   ", "unsupported_measures.Theta"),
    (3, "unsupported_measures.Theta"),
    (None, "unsupported_measures.Theta"),
    ({}, "unsupported_measures.Theta"),
    ({"scalar": ""}, "unsupported_measures.Theta.scalar"),
    ({"scalar": "  \t"}, "unsupported_measures.Theta.scalar"),
])
def test_empty_or_blank_reasons_are_rejected(value, key):
    err = _load_error(_bond(unsupported_measures={**_declare_all("Bond"), "Theta": value}))
    assert err.key == key


def test_unknown_declared_form_is_rejected_with_a_suggestion():
    err = _load_error(_bond(unsupported_measures={**_declare_all("Bond"), "IRDelta": {"scalr": "x", "bucketed": "y"}}))
    assert err.key == "unsupported_measures.IRDelta.scalr" and "did you mean 'scalar'" in str(err)


def test_unsupported_measures_must_be_a_mapping():
    assert _load_error(_bond(unsupported_measures=["IRDelta"])).key == "unsupported_measures"


def test_custom_measure_names_load_freely_with_any_unit():
    # the in-flight v2-pnl-explain names (R14 section 8 items 1 and 4): no unit checks, no rejection,
    # on a strict class too
    cfg = _strict()
    cfg["functions"].update({"th": {"expr": "1.0", "unit": "ccy"}, "yf": {"expr": "1.0", "unit": "number"}, "cash": {"expr": "1.0", "unit": "bp"}})
    cfg["risk_measures"].update({"IRTheta": "th", "YearFraction": "yf", "CashPaidToDate": "cash"})
    loaded = load_asset(cfg)
    assert {"Price", "IRTheta", "YearFraction", "CashPaidToDate"} <= set(loaded.risk_measures)


def test_config_instrument_is_exempt():
    cfg = {
        "schema_version": 1, "asset": "free", "instrument": "ConfigInstrument", "currency": "USD", "market": {"expr": "1"},
        "functions": {"f": {"expr": "1.0", "unit": "ccy"}}, "risk_measures": {"Price": "f", "IRDelta": "f", "IRFwdRate": "f"},
    }
    loaded = load_asset(cfg)  # IRDelta/IRFwdRate in ccy: no contract, no unit rule
    assert loaded.unsupported_measures == {}


@pytest.mark.parametrize("path", IR_CONFIGS)
def test_every_shipped_ir_config_is_contract_valid_without_warnings(path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_asset(REPO / path)
    assert cfg.instrument in contracts.CONTRACTS
    assert all(r and r.strip() for d in cfg.unsupported_measures.values() for r in d.values())
    if contracts.is_strict(cfg.instrument):
        assert not any(contracts.base_measure(m)[0] in {r.measure for r in contracts.contract_for(cfg.instrument)} for m in cfg.unsupported_measures)


# ------------------------------------------------------------------------------------ instrument: validation


def test_unknown_instrument_is_rejected_with_a_suggestion():
    err = _load_error({**_strict(), "instrument": "IRSwapp"})
    assert err.key == "instrument"
    assert "did you mean 'IRSwap'" in str(err) and "ConfigInstrument" in str(err)


def test_instrument_base_class_is_not_a_config_instrument():
    assert _load_error(_bond(instrument="Instrument", unsupported_measures={})).key == "instrument"


# ------------------------------------------------------------------------------------ returns: frame / scale_columns


def test_frame_function_with_scale_columns_loads():
    fns = {"npv": {"expr": "0.0", "unit": "ccy"}, "cf": {"expr": "[]", "unit": "ccy", "returns": "frame", "scale_columns": ["payment_amount", "notional"]}}
    unsupported = _declare_all("Bond", except_=("Price", "Cashflows"))
    cfg = load_asset(_bond(functions=fns, risk_measures={"Price": "npv", "Cashflows": "cf"}, unsupported_measures=unsupported))
    spec = cfg.functions["cf"]
    assert spec.returns == "frame" and spec.scale_columns == ("payment_amount", "notional")
    assert cfg.functions["npv"].returns == "scalar" and cfg.functions["npv"].scale_columns == ()
    assert cfg.risk_measures["Cashflows"].scalar == "cf"


@pytest.mark.parametrize("fn, key", [
    ({"expr": "0.0", "unit": "ccy", "scale_columns": ["a"]}, "functions.g.scale_columns"),  # only with returns: frame
    ({"expr": "[]", "unit": "ccy", "returns": "frame"}, "functions.g.scale_columns"),  # extensive frame needs them
    ({"expr": "[]", "unit": "ccy", "returns": "frame", "scale_columns": []}, "functions.g.scale_columns"),
    ({"expr": "[]", "unit": "ccy", "returns": "frame", "scale_columns": "payment_amount"}, "functions.g.scale_columns"),
    ({"expr": "[]", "unit": "ccy", "returns": "frame", "scale_columns": ["ok", ""]}, "functions.g.scale_columns"),
    ({"expr": "0.0", "unit": "ccy", "returns": "buckets"}, "functions.g.returns"),  # buckets are portfolio functions only
])
def test_frame_schema_errors(fn, key):
    assert _load_error(_bond(functions={"npv": {"expr": "0.0", "unit": "ccy"}, "g": fn})).key == key


def test_non_scaling_frame_needs_no_scale_columns():
    fns = {"npv": {"expr": "0.0", "unit": "ccy"}, "g": {"expr": "[]", "unit": "ccy", "returns": "frame", "scale_with_quantity": False}}
    assert load_asset(_bond(functions=fns)).functions["g"].scale_columns == ()


@pytest.mark.parametrize("pf, key", [
    ({"expr": "[]", "unit": "ccy", "returns": "frame"}, "portfolio_functions.pg.returns"),
    ({"expr": "1.0", "unit": "ccy", "scale_columns": ["a"]}, "portfolio_functions.pg.scale_columns"),
])
def test_portfolio_functions_keep_scalar_and_buckets_only(pf, key):
    assert _load_error(_bond(portfolio_functions={"pg": pf})).key == key


# ------------------------------------------------------------------------------------ validate_frame


def test_validate_frame():
    cols = list(contracts.FRAME_COLUMNS["Cashflows"])
    contracts.validate_frame("Cashflows", pd.DataFrame(columns=cols))  # no rows, but the schema: fine
    contracts.validate_frame("Cashflows", pd.DataFrame([dict.fromkeys(cols, 1)]).assign(notional=1.0))  # extra columns are fine
    with pytest.raises(ConfigError, match=r"missing required column\(s\) \['payment_amount'\]"):
        contracts.validate_frame("Cashflows", pd.DataFrame(columns=[c for c in cols if c != "payment_amount"]))
    with pytest.raises(ConfigError, match="missing required"):
        contracts.validate_frame("Cashflows", pd.DataFrame())
    with pytest.raises(ConfigError, match="must produce a DataFrame, got list"):
        contracts.validate_frame("Cashflows", [])
    contracts.validate_frame("SomeCustomTable", pd.DataFrame())  # no required columns


# ------------------------------------------------------------------------------------ UnsupportedMeasureError


def test_unsupported_measure_error():
    err = UnsupportedMeasureError("toy_eur_irs", "IRGammaParallel", "*", "no gamma function")
    assert isinstance(err, ConfigError) and isinstance(err, NotSupportedError)
    assert (err.asset, err.measure, err.form, err.reason) == ("toy_eur_irs", "IRGammaParallel", "*", "no gamma function")
    assert err.key == "unsupported_measures.IRGammaParallel"
    # the generic "no mapping for risk measure X" text stays in it: the v2-pnl-explain branch's
    # T-MISSING test matches that phrase on toy_eur_irs (R14 section 8 #3, decision 0.12)
    assert "asset toy_eur_irs has no mapping for risk measure IRGammaParallel: 'IRGammaParallel' is declared unsupported (every form): no gamma function" in str(err)
    one_form = UnsupportedMeasureError("a", "IRDelta", "bucketed", "no ladder")
    assert one_form.key == "unsupported_measures.IRDelta.bucketed" and "(bucketed): no ladder" in str(one_form)
    with pytest.raises(NotSupportedError):
        raise one_form


@pytest.mark.parametrize("err", [
    UnsupportedMeasureError("a", "IRDelta", "bucketed", "no ladder"),
    ConfigError("bad", asset="a", key="k"),
    AssetEvaluationError("a", "k", "f(x)", "2024-01-02", None),
    MarketDataUnavailable("a", "2024-01-02", None, "closed"),
], ids=lambda e: type(e).__name__)
def test_errors_survive_deepcopy_and_pickle(err):
    """Stored per leaf by calc(fn=) (Phase B), so they must copy: custom constructors need __reduce__."""
    for clone in (copy.deepcopy(err), pickle.loads(pickle.dumps(err))):
        assert type(clone) is type(err) and str(clone) == str(err) and vars(clone) == vars(err)
