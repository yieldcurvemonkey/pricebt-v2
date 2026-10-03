"""Swap P&L explain (delta / gamma / carry): definitions, reconciliation and diagnostics.

docs/v2/PNL_EXPLAIN_PLAN.md is the spec; section numbers below refer to it. Everything here is a
pricebt CALLER, not engine code -- `src/pricebt/**` is untouched and never imported for its own
sake beyond the public `pricebt.risk`/`pricebt.backtests` surface every other skill script uses.

Usage (plan section 3.3's wiring note):

    import sys; sys.path.insert(0, "skills/pricebt-strategy-recipes/scripts")
    import swap_pnl
    bt = GenericEngine().run_backtest(strategy, ..., risks=[CashPaidToDate], pnl_explain=swap_pnl.swap_pnl_definition())
    table = swap_pnl.explain_table(bt)          # actual_dpv, cash, economic, PNL_*, explained, residual
    stats = swap_pnl.explain_stats(table)        # totals, r2, residual_share, worst-residual date

`run_backtest` takes `pnl_explain=` directly (checked against `GenericEngine.run_backtest`'s real
signature in `src/pricebt/backtests/generic_engine.py`) -- no need to set `bt.pnl_explain_def` by
hand. Add `CashPaidToDate` to `run_backtest(risks=[...])` yourself to get a non-zero `cash` column;
without it, `explain_table` reports `cash=0.0` (correct on the toy regardless, plan section 2.4).

Section 2.6: this definition cannot be combined with `result_ccy=...` -- every measure here except
the rate measure itself is a plain (non-parameterised) `RiskMeasure`, so `run_backtest`'s
`result_ccy` rewrite raises `RuntimeError("Unparameterised risk: ...")` before pricing starts. That
is gs's own clean failure mode (`generic_engine.py`'s `raiser`), not something this module works
around.
"""
from __future__ import annotations

import pandas as pd

from pricebt.backtests.backtest_objects import PnlAttribute, PnlDefinition
from pricebt.common import AssetClass
from pricebt.errors import ConfigError
from pricebt.risk import IRDeltaParallel, IRFwdRate, IRGammaParallel, RiskMeasure

__all__ = [
    "IRTheta",
    "YearFraction",
    "CashPaidToDate",
    "RS_TARGET",
    "rate_unit_for",
    "swap_pnl_definition",
    "explain_table",
    "explain_stats",
    "exact_split",
]

# plan section 5.6's own literal number: the default residual-share ceiling for a near-ATM,
# monthly-roll book. This is the one general-purpose default `check_pnl_attribution`
# (skills/pricebt-spot-checks/scripts/spot_check.py) and the tearsheet's "P&L attribution" section
# fall back to when a caller does not supply its own target -- a SINGLE canonical number, so the
# two don't drift apart (plan section 7). A calibrated run's own target (e.g.
# tests/skills/test_skill_swap_pnl.py's RS_TARGET_TOY_ROLL, tests/test_live_arbs_pnl.py's
# RS_TARGET_ARBS) is tighter and specific to that run's book; this constant is deliberately looser
# because an arbitrary/off-market book carries a real, larger residual that is not a bug (section
# 2.7) -- do not tighten it to a calibrated value.
RS_TARGET = 1e-3

# --------------------------------------------------------------------------------- custom measures
# plan section 3.3: plain RiskMeasure singletons, matching IRGammaParallel/IRFwdRate's style in
# src/pricebt/risk/__init__.py:112 (not the parameterised-preset style of IRDeltaParallel). These
# live HERE, in a skill script, never in src/pricebt -- the engine has no notion of theta, year
# fraction or cash-paid-to-date; an asset config that maps them (tests/assets/toy_usd_irs.yaml,
# PNL_EXPLAIN_PLAN.md section 3.2) is what makes them real numbers.
IRTheta = RiskMeasure(name="IRTheta", asset_class=AssetClass.Rates, measure_type="Theta")
YearFraction = RiskMeasure(name="YearFraction", asset_class=AssetClass.Rates, measure_type="YearFraction")
CashPaidToDate = RiskMeasure(name="CashPaidToDate", asset_class=AssetClass.Rates, measure_type="CashPaidToDate")

# plan section 2.1/3.3: converts a market-rate delta into bp, to match dv01/gamma's fixed ccy-per-bp(^2)
# convention regardless of what unit the config's rate function declares.
_RATE_SCALE = {"bp": 1.0, "pct": 100.0, "decimal": 1e4}
_FALLBACK_RATE_UNIT = "bp"  # ASSET_CONFIG_GUIDE.md's own default for a bare rate function


def rate_unit_for(asset_config, rate_measure: RiskMeasure = IRFwdRate) -> str:
    """The `unit:` of the `functions:` entry `asset_config.risk_measures[...]` maps `rate_measure`
    (default `IRFwdRate`) to, read from the config instead of assumed. Falls back to `'bp'`
    (ASSET_CONFIG_GUIDE.md's convention for a plain rate function) when the config has no mapping
    for `rate_measure` (or for its pre-rename `base_name`, e.g. a caller who passed
    `IRFwdRate(name=...)`), or maps it only to a bucketed function -- `swap_pnl_definition`'s rate
    measure is always used in scalar form."""
    mapping = asset_config.risk_measures.get(rate_measure.name)
    if mapping is None and rate_measure.base_name:
        mapping = asset_config.risk_measures.get(rate_measure.base_name)
    if mapping is None or mapping.scalar is None:
        return _FALLBACK_RATE_UNIT
    spec = asset_config.functions.get(mapping.scalar)
    if spec is None:
        return _FALLBACK_RATE_UNIT
    return spec.unit


def swap_pnl_definition(
    rate_unit: str = "bp",
    gamma: bool = True,
    carry: bool = True,
    rate_measure: RiskMeasure = IRFwdRate,
) -> PnlDefinition:
    """PNL_EXPLAIN_PLAN.md section 3.3's swap `PnlDefinition`: delta always, gamma/carry optional.

    - `PNL_delta = IRDeltaParallel x rate_measure`, `second_order=False`. `IRDeltaParallel` is
      already the SCALAR form by construction (`src/pricebt/risk/__init__.py`:
      `IRDelta(aggregation_level=AggregationLevel.Asset, name="IRDeltaParallel")`) -- never call it
      again here, and never substitute bare `IRDelta` (bucketed/ambiguous; T-DEF's mutation).
    - `PNL_gamma = IRGammaParallel x rate_measure`, `second_order=True` (only when `gamma=True`).
    - `PNL_carry = IRTheta x YearFraction`, `second_order=False` (only when `carry=True`).
      `scaling_factor=1.0` always: theta is already ccy per YEAR (plan section 2.2) and
      `YearFraction` is already years (plan section 2.3), so no unit conversion applies -- carry's
      scaling does not move with `rate_unit` (that parameter governs only how `rate_measure`'s own
      unit is converted to bp for the delta/gamma terms below).
    - `scaling_factor` for delta: `bp -> 1, pct -> 100, decimal -> 1e4` (whatever unit
      `rate_measure` reports, converted to bp to match dv01's ccy-per-bp convention). Gamma uses
      the SQUARE of that factor: `pnl_explain`'s `second_order` branch squares
      `(cur_mkt - prev_mkt)` itself, so gamma's `scaling_factor` must already carry the squared
      unit conversion (plan section 2.1's denominator convention) for `0.5 * scaling * risk *
      delta^2` to land in ccy.

    :param rate_unit: 'bp' | 'pct' | 'decimal' -- the unit `rate_measure` is declared in. Read it
        from a config with `rate_unit_for(asset_config, rate_measure)` instead of hardcoding it.
    :param gamma: include `PNL_gamma`.
    :param carry: include `PNL_carry`.
    :param rate_measure: the market rate driving delta/gamma (default `IRFwdRate`, the trade's own
        par rate -- plan section 2, "par is the trade's own par rate").
    """
    if rate_unit not in _RATE_SCALE:
        raise ConfigError(f"swap_pnl_definition: rate_unit must be one of {sorted(_RATE_SCALE)}, got {rate_unit!r}")
    scale = _RATE_SCALE[rate_unit]

    attributes = [
        PnlAttribute(
            attribute_name="PNL_delta",
            attribute_metric=IRDeltaParallel,
            market_data_metric=rate_measure,
            scaling_factor=scale,
            second_order=False,
        )
    ]
    if gamma:
        attributes.append(
            PnlAttribute(
                attribute_name="PNL_gamma",
                attribute_metric=IRGammaParallel,
                market_data_metric=rate_measure,
                scaling_factor=scale * scale,
                second_order=True,
            )
        )
    if carry:
        attributes.append(
            PnlAttribute(
                attribute_name="PNL_carry",
                attribute_metric=IRTheta,
                market_data_metric=YearFraction,
                scaling_factor=1.0,
                second_order=False,
            )
        )
    return PnlDefinition(attributes=attributes)


# --------------------------------------------------------------------------------- reconciliation


def _call_pnl_explain(bt) -> dict:
    """`bt.pnl_explain()`, with a clear `ConfigError` in place of pandas' own opaque failure (T-DEF's
    mutation: an `attribute_metric` that resolves to a bucketed/DataFrame result -- e.g. bare
    `IRDelta` instead of the scalar-form `IRDeltaParallel` -- makes `pnl_explain`'s own
    `if prev_date_risk == 0` raise `ValueError: The truth value of a DataFrame is ambiguous`, deep
    inside `src/pricebt/backtests/backtest_objects.py`, which is frozen and not something this
    module can fix from here)."""
    try:
        return bt.pnl_explain() or {}
    except ValueError as exc:
        # Only re-diagnose the ONE known failure mode (a bucketed/DataFrame attribute_metric
        # tripping `if prev_date_risk == 0`) -- pandas' exact wording for that ambiguous-truth-
        # value comparison. Any other ValueError (or any other exception type) is a different,
        # real problem and must propagate as itself, not get mislabelled as a scalar-form issue.
        if "ambiguous" not in str(exc):
            raise
        raise ConfigError(
            "bt.pnl_explain() failed -- every PnlAttribute.attribute_metric/market_data_metric must "
            "be a SCALAR-form risk measure (e.g. IRDeltaParallel, never bare IRDelta): "
            f"{type(exc).__name__}: {exc}"
        ) from exc


def explain_table(bt, cash: bool = True) -> pd.DataFrame:
    """Reconcile `bt.pnl_explain()`'s attribution against the held book's ACTUAL P&L, computed
    INDEPENDENTLY (plan section 3.3): this function walks `bt.results`/`bt.trade_exit_risk_results`
    itself and differences `PV(t) - PV(t-1)` (plus `cash_paid_to_date` if requested), mirroring
    `pnl_explain`'s own lookup pattern (`src/pricebt/backtests/backtest_objects.py:404`) -- it never
    goes through the attribution mechanism for the "actual" side, so it is fit to be the ground
    truth `pnl_explain` is checked against.

    Indexed by date (one row per step `t-1 -> t`, i.e. `dates[1:]` of
    `sorted(bt.results.keys() | bt.trade_exit_risk_results.keys())`, exactly the index
    `bt.pnl_explain()`'s own per-attribute dicts use).

    Columns: `actual_dpv, cash, economic, PNL_delta, PNL_gamma, PNL_carry, explained, residual`.
    `economic = actual_dpv + cash`. The attribution columns are the PER-STEP differences of
    `bt.pnl_explain()`'s cumulative series (a component not in `bt.pnl_explain_def` -- e.g. gamma
    when `swap_pnl_definition(gamma=False)` was used -- is 0 for every row, plan section 3.3).
    `explained = PNL_delta + PNL_gamma + PNL_carry`; `residual = economic - explained`.

    :param cash: include `cash_paid_to_date` deltas in `economic`. Needs `CashPaidToDate` to have
        actually been requested (`run_backtest(risks=[CashPaidToDate, ...])`) -- if it was not,
        `cash` is reported as 0.0 for every row (correct on the toy regardless, section 2.4) rather
        than raising, since the column is meaningful either way.
    """
    price_risk = bt.price_measure
    results = bt.results
    exit_results = bt.trade_exit_risk_results
    dates = sorted(set(results.keys()) | set(exit_results.keys()))
    has_cash = cash and CashPaidToDate in bt.risks

    actual_dpv: dict = {}
    cash_col: dict = {}
    for idx in range(1, len(dates)):
        cur_date, prev_date = dates[idx], dates[idx - 1]
        if prev_date not in results:
            actual_dpv[cur_date] = 0.0
            cash_col[cur_date] = 0.0
            continue
        dpv = 0.0
        dcash = 0.0
        for inst in results[prev_date].portfolio.all_instruments:
            prev_pv = results[prev_date][inst][price_risk]
            still_held = cur_date in results and inst in results[cur_date].portfolio
            cur_pv = results[cur_date][inst][price_risk] if still_held else exit_results[cur_date][inst][price_risk]
            dpv += float(cur_pv) - float(prev_pv)
            if has_cash:
                prev_cash = results[prev_date][inst][CashPaidToDate]
                cur_cash = results[cur_date][inst][CashPaidToDate] if still_held else exit_results[cur_date][inst][CashPaidToDate]
                dcash += float(cur_cash) - float(prev_cash)
        actual_dpv[cur_date] = dpv
        cash_col[cur_date] = dcash

    idx = dates[1:]
    table = pd.DataFrame(index=pd.Index(idx, name="date"))
    table["actual_dpv"] = pd.Series(actual_dpv, dtype=float).reindex(idx)
    table["cash"] = pd.Series(cash_col, dtype=float).reindex(idx) if has_cash else 0.0
    table["economic"] = table["actual_dpv"] + table["cash"]

    raw = _call_pnl_explain(bt)
    for name in ("PNL_delta", "PNL_gamma", "PNL_carry"):
        if name in raw:
            cumulative = pd.Series(raw[name], dtype=float).reindex(idx)
            step = cumulative.diff()
            if len(idx):
                step.iloc[0] = cumulative.iloc[0]
            table[name] = step
        else:
            table[name] = 0.0

    table["explained"] = table["PNL_delta"] + table["PNL_gamma"] + table["PNL_carry"]
    table["residual"] = table["economic"] - table["explained"]
    return table


def explain_stats(table: pd.DataFrame) -> dict:
    """Summary statistics of an `explain_table` result.

    - `totals`: sum of every numeric column.
    - `r2`: the standard `1 - SS_res/SS_tot` of `explained` vs `economic`, DAILY (one point per
      row of `table`), with `SS_tot` taken around `economic`'s own mean (the ordinary
      coefficient-of-determination convention -- not around zero: `explained` need not average to
      `economic`'s mean for a perfect fit if `economic` itself has no fixed baseline, but in
      practice here both are P&L series centred near 0, so the two conventions rarely disagree
      much; recorded explicitly since the plan asks to "decide and document which").
    - `residual_share = var(residual) / var(economic)` (pandas default `ddof=1`).
    - `abs_residual_total`, `abs_economic_total`, and their ratio `abs_residual_ratio`.
    - `worst_residual_date` / `worst_residual`: the row with the largest `|residual|`.
    """
    cols = [c for c in ("actual_dpv", "cash", "economic", "PNL_delta", "PNL_gamma", "PNL_carry", "explained", "residual") if c in table.columns]
    totals = {c: float(table[c].sum()) for c in cols}

    if len(table) == 0:
        return {
            "totals": totals,
            "r2": float("nan"),
            "residual_share": float("nan"),
            "abs_residual_total": 0.0,
            "abs_economic_total": 0.0,
            "abs_residual_ratio": float("nan"),
            "worst_residual_date": None,
            "worst_residual": float("nan"),
        }

    economic, explained, residual = table["economic"], table["explained"], table["residual"]
    ss_res = float((residual**2).sum())
    ss_tot = float(((economic - economic.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot != 0 else float("nan")

    var_resid = float(residual.var(ddof=1)) if len(residual) > 1 else 0.0
    var_econ = float(economic.var(ddof=1)) if len(economic) > 1 else 0.0
    residual_share = var_resid / var_econ if var_econ != 0 else float("nan")

    abs_residual_total = float(residual.abs().sum())
    abs_economic_total = float(economic.abs().sum())
    abs_residual_ratio = abs_residual_total / abs_economic_total if abs_economic_total != 0 else float("nan")

    worst_date = residual.abs().idxmax()
    return {
        "totals": totals,
        "r2": r2,
        "residual_share": residual_share,
        "abs_residual_total": abs_residual_total,
        "abs_economic_total": abs_economic_total,
        "abs_residual_ratio": abs_residual_ratio,
        "worst_residual_date": worst_date,
        "worst_residual": float(residual.loc[worst_date]),
    }


def exact_split(bt) -> pd.DataFrame:
    """The exact algebraic split of plan section 2.7, the diagnostic T-RECON/A-DAILY reconcile
    against: for a vanilla swap, `PV = pv01 * (par - K)` on every date (K in bp), so

        dPV = pv01(t-1) * dpar + (par(t-1) - K) * dpv01 + dpv01 * dpar

    per held trade per step, computed from PER-TRADE `npv`/`dv01`/`par` read straight off the asset
    config's own `risk_measures` mapping (`Price`/`IRFwdRate`/`IRDelta`) via `PricingService`
    DIRECTLY (`service.unit_value`, quantity 1 -- i.e. never through the engine's own risk results,
    and never scaled by a held position's actual `quantity_`; this is a per-unit-trade diagnostic).
    The resolved strike K is `inst.resolved_terms['fixed_rate'] * 1e4` (gs's `fixed_rate` is always
    a decimal, independent of what unit the config's own par-rate function reports -- section 2.7).

    Indexed exactly like `explain_table` (`dates[1:]` of the same date union). Columns:
    `delta_term, moneyness_term, convexity_term, total` (`total` is the exact `dPV`, algebraically
    identical to `actual_dpv` from `explain_table` to floating-point precision for every trade this
    identity holds for).
    """
    from pricebt.session import PricebtSession

    session = PricebtSession.current
    if session is None:
        raise RuntimeError("exact_split needs an active PricebtSession (call PricebtSession.use(...) first)")
    service = session.pricing

    results = bt.results
    exit_results = bt.trade_exit_risk_results
    dates = sorted(set(results.keys()) | set(exit_results.keys()))

    delta_term: dict = {}
    moneyness_term: dict = {}
    convexity_term: dict = {}
    for idx in range(1, len(dates)):
        cur_date, prev_date = dates[idx], dates[idx - 1]
        if prev_date not in results:
            delta_term[cur_date] = moneyness_term[cur_date] = convexity_term[cur_date] = 0.0
            continue
        d_sum = m_sum = c_sum = 0.0
        for inst in results[prev_date].portfolio.all_instruments:
            asset = service.asset_for(inst)
            par_mapping = asset.risk_measures.get("IRFwdRate")
            dv01_mapping = asset.risk_measures.get("IRDelta")
            if par_mapping is None or par_mapping.scalar is None or dv01_mapping is None or dv01_mapping.scalar is None:
                raise ConfigError(f"exact_split needs scalar IRFwdRate and IRDelta mappings on asset {asset.name}", asset=asset.name)
            par_fn, dv01_fn = par_mapping.scalar, dv01_mapping.scalar
            par_unit = asset.functions[par_fn].unit
            to_bp = _RATE_SCALE.get(par_unit)
            if to_bp is None:
                raise ConfigError(f"exact_split: {par_fn} unit {par_unit!r} is not bp/pct/decimal", asset=asset.name)
            csa = inst.resolution_csa
            par0 = service.unit_value(inst, prev_date, par_fn, csa) * to_bp
            par1 = service.unit_value(inst, cur_date, par_fn, csa) * to_bp
            pv01_0 = service.unit_value(inst, prev_date, dv01_fn, csa)
            pv01_1 = service.unit_value(inst, cur_date, dv01_fn, csa)
            k_bp = float(inst.resolved_terms["fixed_rate"]) * 1e4
            dpar = par1 - par0
            dpv01 = pv01_1 - pv01_0
            d_sum += pv01_0 * dpar
            m_sum += (par0 - k_bp) * dpv01
            c_sum += dpv01 * dpar
        delta_term[cur_date] = d_sum
        moneyness_term[cur_date] = m_sum
        convexity_term[cur_date] = c_sum

    idx = dates[1:]
    table = pd.DataFrame(index=pd.Index(idx, name="date"))
    table["delta_term"] = pd.Series(delta_term, dtype=float).reindex(idx)
    table["moneyness_term"] = pd.Series(moneyness_term, dtype=float).reindex(idx)
    table["convexity_term"] = pd.Series(convexity_term, dtype=float).reindex(idx)
    table["total"] = table["delta_term"] + table["moneyness_term"] + table["convexity_term"]
    return table
