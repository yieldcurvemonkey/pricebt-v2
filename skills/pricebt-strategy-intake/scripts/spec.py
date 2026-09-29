"""Load, default, validate and write a pricebt strategy spec (skills/pricebt-strategy-intake/templates/strategy_spec.yaml).

In-process:
    import sys; sys.path.insert(0, "skills/pricebt-strategy-intake/scripts")
    import spec as specmod
    s, assumptions = specmod.apply_defaults(specmod.load_spec("my_spec.yaml"))
    errors = specmod.validate_spec(s)

CLI (repository root):
    python skills/pricebt-strategy-intake/scripts/spec.py validate SPEC.yaml
    python skills/pricebt-strategy-intake/scripts/spec.py defaults SPEC.yaml [--out FILLED.yaml]
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Union

import yaml

import instrument_terms as terms  # this directory (on sys.path when spec.py is imported or run)

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "strategy_spec.yaml"
REPO_ROOT = Path(__file__).resolve().parents[3]

ARCHETYPES = ("periodic_roll", "mean_reversion", "momentum", "curve_trade", "delta_hedged", "risk_band", "event", "custom")
SIGNAL_TYPES = ("none", "par_rate_zscore", "rate_momentum", "custom")
COST_MODELS = ("none", "constant", "notional_bp", "dv01_bp")
SIZING_METHODS = ("notional", "dv01_target", "nav")
# the signal type each signal-driven archetype needs
ARCHETYPE_SIGNAL = {"mean_reversion": "par_rate_zscore", "momentum": "rate_momentum"}
# keys whose template value is an example, not a default to merge into a user's value
_WHOLE_KEYS = ("instruments", "assets")
_DATE_KEYS = ("start", "end", "in_sample_end")
_FREQ_RE = re.compile(r"^\d+[bdwmy]$", re.IGNORECASE)


def _to_date(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        try:
            return date.fromisoformat(v)
        except ValueError:
            return v  # validate_spec reports it
    return v


def _coerce_dates(spec: dict) -> dict:
    dates = spec.get("dates")
    if isinstance(dates, dict):
        for k in _DATE_KEYS:
            if k in dates:
                dates[k] = _to_date(dates[k])
        if isinstance(dates.get("holiday_calendar"), list):
            dates["holiday_calendar"] = [_to_date(d) for d in dates["holiday_calendar"]]
    if isinstance(spec.get("event_dates"), list):
        spec["event_dates"] = [_to_date(d) for d in spec["event_dates"]]
    return spec


def load_spec(path_or_dict: Union[str, Path, dict]) -> dict:
    """A spec from a YAML path or a dict (deep-copied); dates become datetime.date."""
    if isinstance(path_or_dict, dict):
        spec = copy.deepcopy(path_or_dict)
    else:
        spec = yaml.safe_load(Path(path_or_dict).read_text(encoding="utf-8")) or {}
    if not isinstance(spec, dict):
        raise ValueError(f"spec must be a mapping, got {type(spec).__name__}")
    return _coerce_dates(spec)


def template() -> dict:
    return load_spec(TEMPLATE)


def _merge(user: dict, default: dict, prefix: str, assumptions: list) -> None:
    for key, dval in default.items():
        path = f"{prefix}{key}"
        if key not in user or (user[key] is None and isinstance(dval, dict)):
            user[key] = copy.deepcopy(dval)
            if key != "assumptions":
                assumptions.append(f"{path} = {_fmt(dval)} (template default)")
        elif isinstance(dval, dict) and isinstance(user[key], dict) and key not in _WHOLE_KEYS:
            _merge(user[key], dval, f"{path}.", assumptions)


def _fmt(v: Any) -> str:
    return " ".join(yaml.safe_dump(v, default_flow_style=True, sort_keys=False).split()).removesuffix(" ...")


def apply_defaults(spec: Union[dict, str, Path]) -> "tuple[dict, list[str]]":
    """Fill every missing field from the template. Returns (filled spec, the defaults used, one line
    each). The same lines are appended (de-duplicated) to the spec's own `assumptions:` list.
    `instruments` and `assets` are defaulted only when the whole key is absent (the template's
    values are an example, not per-field defaults). `deliverables.out_dir` defaults to
    reports/<name>, not the template's example folder."""
    spec = load_spec(spec)
    tmpl = template()
    out_dir_given = isinstance(spec.get("deliverables"), dict) and "out_dir" in spec["deliverables"]
    assumptions: list = []
    _merge(spec, tmpl, "", assumptions)
    if not out_dir_given:
        spec["deliverables"]["out_dir"] = f"reports/{spec['name']}"
        assumptions = [a.replace(f"reports/{tmpl['name']}", f"reports/{spec['name']}") if a.startswith("deliverables")
                       else a for a in assumptions]
    existing = list(spec.get("assumptions") or [])
    spec["assumptions"] = existing + [a for a in assumptions if a not in existing]
    return _coerce_dates(spec), assumptions


def resolve_path(p: Union[str, Path]) -> Path:
    """A spec path as given if it exists (relative to the cwd), else relative to the repository root."""
    p = Path(p)
    return p if p.is_absolute() or p.exists() else REPO_ROOT / p


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_spec(spec: dict) -> "list[str]":
    """Every problem found, one message each; [] means valid. Run apply_defaults first: a missing
    section is reported as an error here."""
    errors: list = []
    err = errors.append
    for section in ("instruments", "dates", "signal", "rebalance", "sizing", "risk_limits", "costs", "financing"):
        if not isinstance(spec.get(section), dict):
            err(f"{section}: missing or not a mapping (run apply_defaults first)")
    if errors:
        return errors

    name = spec.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        err(f"name: {name!r} must be kebab-case (lower-case letters, digits, '-')")

    # --- assets / currencies
    assets = spec.get("assets")
    if not assets or not isinstance(assets, list):
        err("assets: at least one asset config path is required")
    else:
        for a in assets:
            if not resolve_path(a).exists():
                err(f"assets: {a} does not exist (paths are relative to the repository root)")
    if spec.get("fx") and not resolve_path(spec["fx"]).exists():
        err(f"fx: {spec['fx']} does not exist")
    if spec.get("result_ccy") and not spec.get("fx"):
        err("result_ccy is set but fx is null: converting to result_ccy needs an FX config")

    # --- instruments
    from pricebt import instrument as _instrument_mod

    insts = spec["instruments"]
    if "primary" not in insts:
        err("instruments: a 'primary' instrument is required")
    ccys = set()
    for key, inst in insts.items():
        if not isinstance(inst, dict) or "class" not in inst:
            err(f"instruments.{key}: needs 'class' and 'kwargs'")
            continue
        if not hasattr(_instrument_mod, str(inst["class"])):
            err(f"instruments.{key}.class: {inst['class']!r} is not a pricebt.instrument class")
        kw = inst.get("kwargs") or {}
        if not isinstance(kw, dict):
            err(f"instruments.{key}.kwargs: must be a mapping")
            continue
        if hasattr(_instrument_mod, str(inst["class"])):
            errors.extend(f"instruments.{key}.kwargs: {p}" for p in terms.kwarg_problems(inst["class"], kw))
        if kw.get("notional_currency"):
            ccys.add(str(kw["notional_currency"]).split(".")[-1].upper())
    if len(ccys) > 1 and not spec.get("result_ccy"):
        err(f"instruments span {sorted(ccys)}: set result_ccy (and fx) to report in one currency")

    # --- dates
    d = spec["dates"]
    start, end, ise = d.get("start"), d.get("end"), d.get("in_sample_end")
    if not isinstance(start, date) or not isinstance(end, date):
        err(f"dates: start ({start!r}) and end ({end!r}) must be ISO dates")
    else:
        if start >= end:
            err(f"dates: start {start} must be before end {end}")
        if ise is not None:
            if not isinstance(ise, date):
                err(f"dates.in_sample_end: {ise!r} is not an ISO date")
            elif not start <= ise <= end:
                err(f"dates.in_sample_end {ise} must lie inside [start {start}, end {end}]")
    if not _FREQ_RE.match(str(d.get("frequency", ""))):
        err(f"dates.frequency: {d.get('frequency')!r} is not a gs frequency like 1b, 1w, 1m")

    # --- archetype / signal
    arch = spec.get("archetype")
    sig = spec["signal"]
    if arch not in ARCHETYPES:
        err(f"archetype: {arch!r} is not one of {', '.join(ARCHETYPES)}")
    if sig.get("type") not in SIGNAL_TYPES:
        err(f"signal.type: {sig.get('type')!r} is not one of {', '.join(SIGNAL_TYPES)}")
    elif sig.get("type") != "none":
        if sig.get("instrument") not in insts:
            err(f"signal.instrument: {sig.get('instrument')!r} is not a named instrument ({', '.join(insts)})")
        if not sig.get("measure"):
            err("signal.measure: required when signal.type is not none")
    lag = sig.get("lag", 0)
    if not (isinstance(lag, int) and not isinstance(lag, bool) and lag >= 0):
        err(f"signal.lag: {lag!r} must be an integer >= 0")
    need = ARCHETYPE_SIGNAL.get(arch)
    if need and sig.get("type") != need:
        err(f"archetype {arch} needs signal.type {need}, got {sig.get('type')!r}")
    params = sig.get("params") or {}
    lookback = sig.get("lookback")
    if arch == "mean_reversion":
        if not (_num(params.get("z_entry")) and params["z_entry"] > 0):
            err("signal.params.z_entry: mean_reversion needs a positive z_entry")
        if not (isinstance(lookback, int) and lookback >= 2):
            err("signal.lookback: mean_reversion needs an integer lookback >= 2")
    if arch == "momentum":
        if not (isinstance(lookback, int) and lookback >= 1):
            err("signal.lookback: momentum needs an integer lookback >= 1")
        if "threshold_bp" in params and not (_num(params["threshold_bp"]) and params["threshold_bp"] >= 0):
            err("signal.params.threshold_bp: must be a number >= 0")

    reb = spec["rebalance"]
    if arch in ("periodic_roll", "momentum", "curve_trade", "delta_hedged", "risk_band"):
        if not _FREQ_RE.match(str(reb.get("frequency", ""))):
            err(f"rebalance.frequency: {reb.get('frequency')!r} is not a gs frequency like 1m")
    if arch == "event":
        ev = spec.get("event_dates") or []
        if not ev:
            err("event_dates: archetype event needs at least one date")
        for e in ev:
            if not isinstance(e, date):
                err(f"event_dates: {e!r} is not an ISO date")
            elif isinstance(start, date) and isinstance(end, date) and not start <= e <= end:
                err(f"event_dates: {e} lies outside [{start}, {end}]")
    if arch == "curve_trade":
        if "second" not in insts:
            err("curve_trade needs two instruments: 'primary' and 'second'")
        elif not terms.opposite_positions(insts["primary"], insts["second"]):
            err("curve_trade: primary and second must be opposite positions (unit IRDelta of opposite signs): swaps "
                "opposite pay_or_receive (one Pay, one Receive); swaptions or bonds opposite buy_sell; or a long bond "
                "against a pay-fixed swap")
    if arch in ("delta_hedged", "risk_band") and "hedge" not in insts:
        err(f"{arch} needs a 'hedge' instrument")

    # --- sizing
    sz = spec["sizing"]
    method = sz.get("method")
    if method not in SIZING_METHODS:
        err(f"sizing.method: {method!r} is not one of {', '.join(SIZING_METHODS)}")
    elif method == "notional":
        if sz.get("notional") is not None and not (_num(sz["notional"]) and sz["notional"] > 0):
            err("sizing.notional: must be a positive number (or null to keep each instrument's size kwarg: "
                "notional_amount, or size for a Bond)")
    elif not (_num(sz.get(method)) and sz[method] > 0):
        err(f"sizing.{method}: sizing.method {method} needs a positive sizing.{method}")
    if method == "dv01_target":
        errors.extend(terms.dv01_sizing_problems(insts))
    if arch == "mean_reversion" and method in ("dv01_target", "nav"):
        err("mean_reversion supports sizing.method notional only: the gs MeanReversionTrigger passes its +1/-1 "
            "direction through AddTradeActionInfo.scaling, which AddScaledTradeAction never reads")

    # --- limits / costs / financing
    rl = spec["risk_limits"]
    if arch == "risk_band" and not (_num(rl.get("max_abs_dv01")) and rl["max_abs_dv01"] > 0):
        err("risk_limits.max_abs_dv01: risk_band needs a positive max_abs_dv01")
    if rl.get("stop_loss_mtm") and spec.get("result_ccy"):
        err("risk_limits.stop_loss_mtm with result_ccy is not supported: run_backtest adds its own Price and "
            "rewrites it to Price(currency=...) after de-duplicating, so the trigger's Price column would appear twice")
    for k in ("max_abs_dv01", "stop_loss_mtm", "max_positions", "max_drawdown"):
        v = rl.get(k)
        if v is not None and not (_num(v) and v > 0):
            err(f"risk_limits.{k}: must be a positive number or null")
    if rl.get("hedge_measure"):  # optional (commented out in the template): the measure delta_hedged / risk_band hedge
        try:
            parse_risk(rl["hedge_measure"])
        except ValueError as e:
            err(f"risk_limits.hedge_measure: {e}")
    c = spec["costs"]
    if c.get("model") not in COST_MODELS:
        err(f"costs.model: {c.get('model')!r} is not one of {', '.join(COST_MODELS)}")
    elif c["model"] != "none" and not (_num(c.get("level")) and c["level"] >= 0):
        err(f"costs.level: must be a number >= 0 for costs.model {c['model']}")
    rate = spec["financing"].get("cash_accrual_rate")
    if rate is not None and not _num(rate):
        err("financing.cash_accrual_rate: must be a number")
    if not _num(spec.get("initial_value", 0)):
        err("initial_value: must be a number")

    # --- reporting
    risks = spec.get("risks_to_report") or []
    for r in risks:
        try:
            parse_risk(r)
        except ValueError as e:
            err(f"risks_to_report: {e}")
    bench = spec.get("benchmark", "none")
    if bench not in ("none", "buy_and_hold") and bench not in insts:
        err(f"benchmark: {bench!r} must be none, buy_and_hold or a named instrument")
    return errors


_RISK_RE = re.compile(r"^\s*(\w+)\s*(?:\((.*)\))?\s*$")


def parse_risk(text: Any):
    """'Price', 'IRDeltaParallel' or "IRDelta(aggregation_level='Type')" -> the pricebt.risk measure.
    Quoted values stay strings; unquoted ones are read as YAML scalars ("IRVanna(aggregation_level=Type,
    bump_size=0.5)" passes the float 0.5)."""
    from pricebt import risk as _risk_mod
    from pricebt.common import RiskMeasure

    m = _RISK_RE.match(str(text))
    measure = getattr(_risk_mod, m.group(1), None) if m else None
    if not isinstance(measure, RiskMeasure):
        raise ValueError(f"{text!r} is not a risk measure in pricebt.risk")
    if m.group(2):
        kwargs = {}
        for part in filter(None, (p.strip() for p in m.group(2).split(","))):
            k, _, v = part.partition("=")
            v = v.strip()
            kwargs[k.strip()] = v[1:-1] if v[:1] in ("'", '"') else yaml.safe_load(v)
        measure = measure(**kwargs)
    return measure


def dump_spec(spec: dict, path: Union[str, Path]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="apply defaults, then print every validation error (exit 1 if any)")
    v.add_argument("spec")
    dfl = sub.add_parser("defaults", help="print the defaults the spec relies on")
    dfl.add_argument("spec")
    dfl.add_argument("--out", help="also write the filled spec here")
    args = ap.parse_args(argv)

    spec, assumptions = apply_defaults(load_spec(args.spec))
    if args.cmd == "defaults":
        for a in assumptions:
            print(a)
        if args.out:
            print(f"wrote {dump_spec(spec, args.out)}")
        return 0
    errors = validate_spec(spec)
    for e in errors:
        print(f"ERROR: {e}")
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
