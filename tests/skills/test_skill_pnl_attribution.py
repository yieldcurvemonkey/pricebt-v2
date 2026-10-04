"""skills/pricebt-pnl-attribution/scripts/attribution.py on real GenericEngine runs of the toy
swaption and toy bond (tests/assets): the definition it picks and the books it refuses, units read
from the configs, the pnl_explain_table identities, and the residual signatures the skill's
references/diagnosing-residuals.md teaches (flipped vega sign, unit mismatch, NaN), and the financed
bond's coupons and repo interest as cashflow_pnl and financing_pnl (pricebt DEV-E22). Every run is a few months daily, well under a second."""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import yaml

from pricebt.backtests.backtest_objects import swaption_pnl_definition
from pricebt.session import PricebtSession

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "tests" / "assets"
sys.path.insert(0, str(ROOT / "skills" / "pricebt-pnl-attribution" / "scripts"))
import attribution  # noqa: E402

SWAPTION, BOND = ASSETS / "toy_usd_swaption.yaml", ASSETS / "toy_usd_bond.yaml"
SWAP_FULL = ASSETS / "toy_usd_irs_full.yaml"
SIX = ["PNL_delta", "PNL_gamma", "VegaPnL", "PNL_vanna", "PNL_volga", "PNL_theta"]
THREE = ["PNL_delta", "PNL_gamma", "PNL_theta"]
SHORT = date(2024, 2, 29)
PCT_LEVELS = {
    "fwd_rate": {"expr": "ts.fwd_rate(market, trade) / 100", "unit": "pct"},
    "annual_vol": {"expr": "ts.annual_vol(market, trade) / 100", "unit": "pct"},
}


def _names(definition):
    return [a.attribute_name for a in definition.attributes]


def _bond_declaring_theta() -> dict:
    """A ConfigInstrument copy of toy_usd_bond with Theta declared instead of mapped: only a class
    without a contract may declare (a Bond, IRSwap or IRSwaption config with a gap does not load,
    IR_STRICT_CONTRACT R3-0, BOND_DESIGN 4.1)."""
    from pricebt.assets import yamlio

    raw = yamlio.load_file(BOND)
    raw.update(asset="toy_usd_bond_declared", instrument="ConfigInstrument")
    raw.pop("match")
    del raw["risk_measures"]["Theta"]
    raw["unsupported_measures"] = {"Theta": "this bond library has no carry call"}
    return raw


def test_definition_for_picks_the_kind_and_reads_units_from_the_configs():
    assert _names(attribution.definition_for([SWAPTION])) == SIX
    assert _names(attribution.definition_for([BOND])) == THREE
    assert _names(attribution.definition_for([SWAP_FULL, BOND])) == THREE  # swaps: no vol attributes
    mixed = attribution.definition_for([SWAPTION, SWAP_FULL, BOND])  # swap and bond map vol to 0.0 (R2-8)
    assert _names(mixed) == SIX and {a.market_data_unit for a in mixed.attributes} == {"bp", None}
    assert _names(attribution.definition_for([SWAPTION], kind="swaption", volga=False)) == [n for n in SIX if n != "PNL_volga"]
    preset = yaml.safe_load(SWAP_FULL.read_text(encoding="utf-8"))  # the scalar mapped under the preset key (R2-10)
    preset["risk_measures"].update(IRDelta={"bucketed": "delta_ladder"}, IRDeltaParallel="delta")
    assert _names(attribution.definition_for([preset])) == THREE
    session = PricebtSession.use(assets=[SWAPTION, BOND])
    assert _names(attribution.definition_for(session, assets=["toy_usd_bond"])) == THREE
    assert _names(attribution.definition_for()) == SIX  # the current session
    with pytest.raises(ValueError, match="kind must be one of"):
        attribution.definition_for([BOND], kind="fx")
    with pytest.raises(ValueError, match="are not among"):
        attribution.definition_for(session, assets=["toy_eur_irs"])


def test_definition_for_refuses_a_book_it_cannot_attribute():
    # a (ConfigInstrument) bond declaring Theta unsupported: every held asset must serve every measure
    with pytest.raises(ValueError, match=r"toy_usd_bond_declared: Theta declared unsupported") as err:
        attribution.definition_for([_bond_declaring_theta(), SWAP_FULL])
    assert "this bond library has no carry call" in str(err.value) and "toy_usd_irs_full" not in str(err.value)
    # one book, two level units: one PnlDefinition cannot scale both
    pct = yaml.safe_load(SWAPTION.read_text(encoding="utf-8"))
    pct["asset"] = "toy_usd_swaption_pct"
    pct["functions"].update(PCT_LEVELS)
    with pytest.raises(ValueError, match=r"IRFwdRate is declared in different units across the book"):
        attribution.definition_for([pct, BOND])


def test_table_identities_frames_and_stats_on_the_toy_swaption():
    bt = attribution.demo_backtest("swaption", end=SHORT)
    table, cumulative = attribution.attribution_frames(bt)
    assert list(table.columns) == ["actual_pnl", "cashflow_pnl", "financing_pnl", "economic_pnl", *SIX, "explained_pnl", "residual_pnl"]
    assert (table["financing_pnl"] == 0.0).all()  # a swaption maps no FinancingToDate: nothing booked (gs parity)
    np.testing.assert_allclose(table[SIX].sum(axis=1), table["explained_pnl"], rtol=0, atol=1e-9)
    np.testing.assert_allclose(table["economic_pnl"] - table["explained_pnl"], table["residual_pnl"], rtol=0, atol=1e-9)
    assert list(cumulative.columns) == [*SIX, "residual_pnl", "economic_pnl"]
    explain = bt.pnl_explain()
    for name in SIX:  # the cumulative columns ARE pnl_explain()'s cumulative values
        assert cumulative[name].iloc[-1] == pytest.approx(float(list(explain[name].values())[-1]), rel=1e-12, abs=1e-9)
    # bought at t0 with no costs and no coupons: the economic P&L is the backtest's Total P&L
    total = bt.result_summary[bt.TOTAL_COLUMN].astype(float)
    stats = attribution.explain_stats(table)
    assert stats["totals"]["economic_pnl"] == pytest.approx(total.iloc[-1] - total.iloc[0], rel=1e-9)
    assert stats["finite"] and stats["r2"] > 0.9999 and stats["residual_share"] < 1e-4
    assert attribution.grade(stats) == "PASS"
    with pytest.raises(ValueError, match="no PnlDefinition"):
        bt.pnl_explain_def = None
        attribution.attribution_frames(bt)


def test_pct_and_bp_configs_attribute_identically():
    """The scaling factors come from the declared units (bp 1, pct 100, decimal 1e4; squared for
    second order), so the same book quoted in percent attributes exactly as in bp."""
    bp = attribution.demo_backtest("swaption", end=SHORT)
    pct = attribution.demo_backtest("swaption", end=SHORT, functions=PCT_LEVELS)
    factors = {a.attribute_name: a.scaling_factor for a in pct.pnl_explain_def.attributes}
    assert factors == {"PNL_delta": 100.0, "PNL_gamma": 1e4, "VegaPnL": 100.0, "PNL_vanna": 1e4, "PNL_volga": 1e4, "PNL_theta": -365.0}
    np.testing.assert_allclose(pct.pnl_explain_table().to_numpy(), bp.pnl_explain_table().to_numpy(), rtol=0, atol=1e-6)
    # a definition written for bp on the pct book is refused, not silently scaled by 100 (DEV-E21)
    pct.pnl_explain_def = swaption_pnl_definition()
    with pytest.raises(ValueError, match=r"PNL_delta: IRFwdRate on .* has unit \{'pct': 1\}; the definition expects bp"):
        pct.pnl_explain_table()


def test_financed_bond_books_its_coupon_and_repo_as_cash_and_explains_them():
    """pricebt DEV-E22 on the toy bond (its config maps FinancingToDate): the engine books the coupon on
    the date Price drops it (2024-05-14, the trade date settling on the 05-15 coupon) and the repo
    interest on every mark, with no Cashflows among the risks; the table shows them as cashflow_pnl and
    financing_pnl, financing is explained, and economic_pnl sums to the change in Total."""
    from pricebt.risk import FinancingToDate

    drop_day, coupon = date(2024, 5, 14), 1e6 * 0.0425 / 2
    bt = attribution.demo_backtest("bond")
    table = bt.pnl_explain_table()
    stats = attribution.explain_stats(table)
    assert list(table.loc[table["cashflow_pnl"] != 0].index) == [drop_day] and table.at[drop_day, "cashflow_pnl"] == pytest.approx(coupon)
    assert table.at[drop_day, "actual_pnl"] < 0 < table.at[drop_day, "economic_pnl"]  # Price dropped the coupon, the cash came in
    assert (table["financing_pnl"] < 0).all()  # a long pays the repo every step
    (inst,) = bt.portfolio_dict[table.index[-1]]
    with_risk = attribution.demo_backtest("bond", risks=[FinancingToDate])
    first, last = table.index[0], table.index[-1]
    fin = {d: float(with_risk.results[d][FinancingToDate][inst.name]) for d in (date(2024, 5, 6), last)}
    assert stats["totals"]["financing_pnl"] == pytest.approx(fin[last] - fin[date(2024, 5, 6)], rel=1e-12)
    np.testing.assert_allclose(table["explained_pnl"], table[THREE].sum(axis=1) + table["financing_pnl"], rtol=0, atol=1e-9)
    total = bt.result_summary[bt.TOTAL_COLUMN].astype(float)
    assert stats["totals"]["economic_pnl"] == pytest.approx(total.loc[last] - total.loc[date(2024, 5, 6)], rel=1e-9) and first > date(2024, 5, 6)
    assert attribution.grade(stats) == "PASS" and "financed book" in attribution.grade_reason(stats)
    assert set(stats["residual_corr"]) == set(THREE)  # financing is a fixed column (known cash), never an attribute


def test_a_flipped_vega_sign_is_named_by_the_residual_correlation():
    flipped = {"vega": {"expr": "-ts.vega(market, trade)", "unit": "ccy_per_bp"}}
    stats = attribution.explain_stats(attribution.demo_backtest("swaption", end=SHORT, functions=flipped).pnl_explain_table())
    assert stats["residual_corr"]["VegaPnL"] < -0.999
    # the residual is minus twice the (wrong-signed) attribute: the "-2" signature of a sign flip
    assert stats["totals"]["residual_pnl"] / stats["totals"]["VegaPnL"] == pytest.approx(-2.0, rel=1e-2)
    assert stats["signatures"]["VegaPnL"] == pytest.approx(-1.0, abs=0.02) and attribution.grade(stats) == "FAIL"


def test_a_steady_bias_fails_and_names_the_attribute():
    """A sign-flipped Theta leaves the residual *variance* share near 1.7% (the residual is a steady
    drift, not noise), so the grade also reads |sum residual|/sum|economic| and 1 - r2, and FAILs when
    the residual's signature names an attribute: here Theta x -1 would absorb it."""
    flipped = {"theta_1d": {"expr": "-ts.theta_1d(market, trade)", "unit": "ccy"}}
    stats = attribution.explain_stats(attribution.demo_backtest("swaption", functions=flipped).pnl_explain_table())
    assert stats["residual_share"] < attribution.RESIDUAL_SHARE_WARN  # the variance share alone would PASS
    assert stats["unexplained"] == pytest.approx(stats["total_residual_ratio"]) and stats["unexplained"] > 0.2
    assert stats["signatures"]["PNL_theta"] == pytest.approx(-1.0, abs=0.05)
    assert attribution.grade(stats) == "FAIL" and "PNL_theta" in attribution.grade_reason(stats)
    assert "sign is flipped" in attribution.grade_reason(stats)
    bond = attribution.explain_stats(attribution.demo_backtest("bond", functions={"theta_1d": {"expr": "-tb.theta_1d(market, trade)", "unit": "ccy"}}).pnl_explain_table())
    assert attribution.grade(bond) == "FAIL" and bond["signatures"]["PNL_theta"] == pytest.approx(-1.0, abs=0.05)


def test_a_vol_level_in_pct_declared_bp_fails_by_its_signature():
    """IRAnnualImpliedVol returned in percent under `unit: bp`: every vol move is 100x too small, so
    VegaPnL is ~1% of itself and the residual carries the vega P&L (implied scale ~ +100). That residual
    follows dsigma (noise-like, ~2% of the P&L net), so only the signature, material by
    sum|residual|/sum|economic| (~12%), FAILs it; check_asset's swaption_vol_unit WARNs it earlier."""
    pct_as_bp = {"annual_vol": {"expr": "ts.annual_vol(market, trade) / 100", "unit": "bp"}}
    stats = attribution.explain_stats(attribution.demo_backtest("swaption", functions=pct_as_bp).pnl_explain_table())
    assert stats["unexplained"] < attribution.RESIDUAL_SHARE_WARN < stats["abs_residual_ratio"]
    assert stats["signatures"]["VegaPnL"] > 50 and attribution.grade(stats) == "FAIL"
    assert "far too small" in attribution.grade_reason(stats)


def test_healthy_demos_have_no_signature_even_where_the_residual_tracks_theta():
    """The healthy bond's tiny residual is the time cross term: corr(residual, PNL_theta) ~ -0.99, but
    Theta x ~0.99 absorbs it, which is no error signature."""
    for kind in ("swaption", "bond"):
        stats = attribution.explain_stats(attribution.demo_backtest(kind).pnl_explain_table())
        assert attribution.grade(stats) == "PASS" and stats["signatures"] == {} and stats["unexplained"] < 1e-3


def test_an_exercised_swaption_held_past_expiry_leaves_the_swap_carry_in_the_residual():
    """references/diagnosing-residuals.md section 11: after expiry the leg is the underlying swap, but
    ExpiryInYears is 0, so PNL_theta is 0 and the swap's carry is residual (per calendar day)."""
    from pricebt.backtests.actions import AddTradeAction
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements
    from pricebt.instrument import IRSwaption

    session = PricebtSession.use(assets=[SWAPTION])
    start = date(2024, 1, 2)
    itm = IRSwaption("Pay", "10y", "USD", notional_amount=1e6, expiration_date="1m", strike="ATM-100", buy_sell="Buy", name="p")
    trigger = DateTrigger(DateTriggerRequirements(dates=[start]), [AddTradeAction(itm, name="Add")])
    bt = GenericEngine().run_backtest(Strategy(None, trigger), start=start, end=date(2024, 2, 16), frequency="1b",
                                      pnl_explain=attribution.definition_for(session), show_progress=False)
    after = bt.pnl_explain_table().loc[date(2024, 2, 5):]
    assert (after["PNL_theta"] == 0.0).all() and after["economic_pnl"].abs().max() > 50   # still a live swap
    per_day = after["residual_pnl"] / np.diff([date(2024, 2, 2), *after.index]).astype("timedelta64[D]").astype(float)
    assert per_day.min() > 10.0 and per_day.max() < 11.5                           # the swap's carry, ~10.9 a day


def test_a_nan_anywhere_fails_and_voids_the_ratios():
    table = attribution.demo_backtest("bond", end=date(2024, 5, 31)).pnl_explain_table()
    table.iloc[3, table.columns.get_loc("PNL_delta")] = math.nan
    stats = attribution.explain_stats(table)
    assert not stats["finite"] and stats["r2"] is None and stats["residual_share"] is None
    assert attribution.grade(stats) == "FAIL"


def test_cli_definition_and_demo(capsys, tmp_path):
    frame = attribution.main(["--definition", str(SWAP_FULL), str(BOND)])
    assert list(frame["attribute"]) == THREE and frame.set_index("attribute").at["PNL_theta", "k (scaling_factor)"] == -365.0
    capsys.readouterr()
    with pytest.raises(SystemExit) as stop:  # a book it cannot attribute: the gap list, no traceback
        declared = tmp_path / "bond_declared.yaml"
        declared.write_text(yaml.safe_dump(_bond_declaring_theta(), sort_keys=False), encoding="utf-8")
        attribution.main(["--definition", str(declared)])
    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("cannot attribute this book: every held asset must map") and "Theta declared unsupported" in err
    assert "a declaration does not count" in err
    stats = attribution.main(["--demo", "bond"])
    out = capsys.readouterr().out
    assert stats["finite"] and '"grade": "PASS"' in out
    assert json.loads(out[out.index("{"):])["totals"]["cashflow_pnl"] == pytest.approx(21250.0)


def test_instrument_level_snippet_of_the_reference_runs():
    """references/instrument-level.md's usage block, on the toy swaption and bond: the rows sum to
    the full revaluation with the pricing date held, and the portfolio aggregates by factor."""
    from pricebt.errors import NotSupportedError
    from pricebt.instrument import Bond, IRSwaption
    from pricebt.markets import CloseMarket, PricingContext
    from pricebt.markets.portfolio import Portfolio
    from pricebt.risk import PnlExplain, PnlExplainClose, Price

    PricebtSession.use(assets=[SWAPTION, BOND])
    swaption = IRSwaption("Pay", "10y", "USD", notional_amount=1e6, expiration_date="1y", strike="ATM", buy_sell="Buy")
    bond = Bond(identifier="TOY 4.25 2034-11-15", size=1e6, buy_sell="Buy", settlement_currency="USD")
    F, T = date(2024, 1, 2), date(2024, 1, 9)
    with PricingContext(pricing_date=F):
        rows = swaption.calc(PnlExplain(CloseMarket(date=T))).result()
        book = Portfolio((swaption, bond)).calc(PnlExplain(CloseMarket(date=T))).aggregate()
        base = float(swaption.calc(Price).result())
    with PricingContext(pricing_date=F, market=CloseMarket(date=T)):
        moved = float(swaption.calc(Price).result())
    assert list(rows["mkt_type"]) == ["IR", "IR VOL", "CROSSES"]
    assert rows["value"].sum() == pytest.approx(moved - base, rel=1e-12)
    assert set(book["mkt_type"]) == {"IR", "IR VOL", "CREDIT", "CROSSES"}
    with pytest.raises(NotSupportedError, match="also the market it explains from"):
        with PricingContext(pricing_date=F):
            swaption.calc(PnlExplainClose()).result()
