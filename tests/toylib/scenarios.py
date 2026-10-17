"""Toy scenario transforms: what a user's library implements to back an asset config's
`scenarios:` section (docs/v2/SCENARIOS.md). Test-only fixture, never shipped; it never imports
pricebt (it stands in for an external library). Import as `toylib.scenarios`.

Every transform takes the market an asset config built (a `ToyCurve` for the swap toys, a
`SimpleNamespace(curve, sigma)` for the swaption toy) and the scenario as the plain dict pricebt
injects, and returns a NEW market; the input is never mutated. The toy's semantics (all decimal
unless a gs field says bp):

- `MarketDataPattern` matching: `mkt_type` 'IR' is the zero curve, 'IR Vol' the normal vol;
  `mkt_asset` None, or starting with the curve's currency, matches. Any other type is data this
  market does not hold, so it is left alone (correct: a shock to other data changes nothing here).
- `MarketDataShock`: Absolute adds `value`, Proportional multiplies by `1 + value`, Override
  sets it. Point shocks, caps and floors raise NotImplementedError (the toy cannot).
- `CurveScenario`: `parallel_shift` and `curve_shift` in bp; the curve shift is linear in tenor
  between `tenor_start` and `tenor_end` (years), zero at `pivot_point` (default the midpoint),
  flat outside.
- `IndexCurveShift`: the toy has one curve, so it is shifted: `annualised_parallel_shift` bp plus
  `annualised_slope_shift` bp per year of tenor, up to `cutoff` years.
- `CurveOverlay`: the curve is replaced by the zero rates the discount factors imply, linear in
  tenor between their dates.
- `RollFwd`/`CarryScenario`: the curve moves to the new valuation date with its forwards
  realised (`realise_fwd`/`roll_to_fwds`; exact for a sloped toy curve: ToyCurve(t1, r + 2*s*D,
  slope=s)) or its spot zero curve held; the vol is held. The rolled curve's `path_start` marks
  the scenario path, so a swaption expiring on it exercises in the scenario world.
- `MeasureScenario` (scope: trade): `IRFwdRate` moves the curve in parallel until the trade's own
  rate reads `value` (bp, the toy configs' unit); `IRAnnualImpliedVol`/`IRAnnualATMImpliedVol`
  set the normal vol to `value` bp.
- `LiborFallbackScenario`: the toy curves are overnight-index curves; there is nothing to fall
  back from, so the market is returned unchanged.
"""
from __future__ import annotations

import dataclasses
import math
from types import SimpleNamespace

from toylib import rates as tr

# ------------------------------------------------------------------------------ market pieces


def _curve(market):
    return market.curve if isinstance(market, SimpleNamespace) else market


def _with_curve(market, curve):
    return SimpleNamespace(**{**vars(market), "curve": curve}) if isinstance(market, SimpleNamespace) else curve


def _with_sigma(market, sigma: float):
    if not isinstance(market, SimpleNamespace):
        raise NotImplementedError("toy: this market holds no vol")
    return SimpleNamespace(**{**vars(market), "sigma": sigma})


def _kind(pattern, ccy: str):
    """'IR', 'IR VOL' or None (data this toy market does not hold)."""
    kind = str(pattern.get("mkt_type") or "").replace("_", " ").strip().upper()
    asset = pattern.get("mkt_asset")
    if asset is not None and not str(asset).upper().startswith(ccy.upper()):
        return None
    if kind in ("IR", "IR VOL"):
        if pattern.get("mkt_point"):
            raise NotImplementedError(f"toy: a point shock ({pattern['mkt_point']}) needs a curve with pillars")
        return kind
    return None


def _shocked(level: float, shock) -> float:
    if any(shock.get(k) is not None for k in ("precision", "cap", "floor", "coordinate_cap", "coordinate_floor")):
        raise NotImplementedError("toy: shock precision/caps/floors")
    kind, value = shock["shock_type"], shock["value"]
    if kind == "Absolute":
        return level + value
    if kind == "Proportional":
        return level * (1.0 + value)
    if kind == "Override":
        return value
    raise NotImplementedError(f"toy: shock type {kind}")


def _shock_curve(curve, shock):
    kind, value = shock["shock_type"], shock["value"]
    if kind == "Absolute":
        return dataclasses.replace(curve, zero_rate=curve.zero_rate + value)
    if kind == "Proportional":  # every zero rate scaled
        f = 1.0 + value
        return dataclasses.replace(curve, zero_rate=curve.zero_rate * f, slope=curve.slope * f, shape=tuple((t, v * f) for t, v in curve.shape))
    if kind == "Override":  # one flat level
        return dataclasses.replace(curve, zero_rate=value, slope=0.0, shape=())
    raise NotImplementedError(f"toy: shock type {kind}")


def add_shape(curve, pillars):
    """`curve` plus a piecewise-linear zero add-on given as ((years, dz), ...): the two shapes are
    summed exactly on the union of their pillars (both flat outside)."""
    new = tuple(sorted(pillars))
    xs = sorted({t for t, _ in curve.shape} | {t for t, _ in new})
    return dataclasses.replace(curve, shape=tuple((t, tr.shape_value(curve.shape, t) + tr.shape_value(new, t)) for t in xs))


# ------------------------------------------------------------------------------ transforms


def apply_shocks(market, scenario):
    """MarketDataShockBasedScenario: each (pattern, shock) in order."""
    for item in scenario["shocks"]:
        pattern, shock = item["pattern"], item["shock"]
        kind = _kind(pattern, _curve(market).ccy)
        if kind == "IR":
            _shocked(0.0, shock)  # validates the shock fields the toy supports
            market = _with_curve(market, _shock_curve(_curve(market), shock))
        elif kind == "IR VOL" and isinstance(market, SimpleNamespace):  # a swap market holds no vol: nothing to shock
            market = _with_sigma(market, _shocked(market.sigma, shock))
    return market


def curve_scenario(market, scenario):
    """CurveScenario (see the module docstring)."""
    curve = _curve(market)
    if _kind(scenario.get("market_data_pattern") or {"mkt_type": "IR"}, curve.ccy) != "IR":
        return market
    if scenario.get("shock_type") not in (None, "Absolute"):
        raise NotImplementedError(f"toy: CurveScenario shock_type {scenario['shock_type']}")
    par = (scenario.get("parallel_shift") or 0.0) / 1e4
    curve = dataclasses.replace(curve, zero_rate=curve.zero_rate + par)
    shift = scenario.get("curve_shift")
    if shift:
        start, end = scenario.get("tenor_start"), scenario.get("tenor_end")
        if start is None or end is None or end <= start:
            raise ValueError("CurveScenario: curve_shift needs tenor_start < tenor_end")
        pivot = scenario.get("pivot_point")
        pivot = (start + end) / 2.0 if pivot is None else pivot
        curve = add_shape(curve, tuple((t, shift / 1e4 * (t - pivot) / (end - start)) for t in (start, end)))
    return _with_curve(market, curve)


def index_curve_shift(market, scenario):
    """IndexCurveShift (see the module docstring)."""
    curve = _curve(market)
    if _kind(scenario.get("market_data_pattern") or {"mkt_type": "IR"}, curve.ccy) != "IR":
        return market
    unsupported = [k for k in ("freeze_pattern", "floor", "bucket_shift", "bucket_start", "bucket_end") if scenario.get(k) is not None]
    if unsupported:
        raise NotImplementedError(f"toy: IndexCurveShift {unsupported}")
    par = (scenario.get("annualised_parallel_shift") or 0.0) / 1e4
    slope = (scenario.get("annualised_slope_shift") or 0.0) / 1e4
    cutoff = scenario.get("cutoff")
    curve = dataclasses.replace(curve, zero_rate=curve.zero_rate + par)
    if slope:
        if cutoff is None:
            curve = dataclasses.replace(curve, slope=curve.slope + slope)
        else:
            curve = add_shape(curve, ((0.0, 0.0), (float(cutoff), slope * cutoff)))
    return _with_curve(market, curve)


def curve_overlay(market, scenario):
    """CurveOverlay: the curve becomes the zero rates its discount factors imply."""
    curve = _curve(market)
    if scenario.get("denominated") not in (None, curve.ccy):
        return market
    pillars = []
    for d, df in zip(scenario["dates"], scenario["discount_factors"]):
        t = (d - curve.ref_date).days / 365.0
        if t > 0:
            pillars.append((t, -math.log(df) / t))
    if not pillars:
        raise ValueError("CurveOverlay: no discount factor after the valuation date")
    return _with_curve(market, dataclasses.replace(curve, zero_rate=0.0, slope=0.0, shape=tuple(sorted(pillars))))


def roll(market, scenario, from_date, to_date):
    """RollFwd / CarryScenario: move the market from `from_date` to `to_date` (pricebt's
    `pricebt_from_date`/`pricebt_date`)."""
    realise = scenario.get("realise_fwd", scenario.get("roll_to_fwds", True))
    curve = _curve(market)
    if curve.ref_date != from_date:  # a CloseMarket override's market, seen from the valuation date first
        curve = dataclasses.replace(curve, ref_date=from_date)
    if to_date != from_date:
        if realise:
            if curve.shape:
                raise NotImplementedError("toy: rolling a reshaped curve along its forwards")
            years = (to_date - from_date).days / 365.0
            curve = dataclasses.replace(curve, ref_date=to_date, zero_rate=curve.zero_rate + 2.0 * curve.slope * years)
        else:
            curve = dataclasses.replace(curve, ref_date=to_date)
    return _with_curve(market, dataclasses.replace(curve, path_start=curve.path_start or from_date))


def override_measure(market, scenario, own_rate):
    """MeasureScenario (scope: trade). `own_rate(market)` is the trade's own rate in bp."""
    name, value = scenario["risk_measure"], scenario["value"]
    if name in ("IRAnnualImpliedVol", "IRAnnualATMImpliedVol"):
        return _with_sigma(market, value / 1e4)
    if name == "IRFwdRate":
        for _ in range(8):  # secant on a parallel shift: the own rate is near-linear in it
            curve = _curve(market)
            r0 = own_rate(market)
            if abs(r0 - value) < 1e-10:
                break
            r1 = own_rate(_with_curve(market, dataclasses.replace(curve, zero_rate=curve.zero_rate + 1e-4)))
            market = _with_curve(market, dataclasses.replace(curve, zero_rate=curve.zero_rate + 1e-4 * (value - r0) / (r1 - r0)))
        return market
    raise NotImplementedError(f"toy: MeasureScenario of {name}")


def libor_fallback(market, scenario):
    return market
