"""Per-instrument measure contracts (docs/v2/IR_RISK_DESIGN.md section 2; DEV-I11).

gs_quant answers every IR measure on every IR instrument (a swap's vega is 0) and silently returns
`UnsupportedValue` for what it cannot compute. pricebt has no server behind it, so an asset config
whose `instrument:` has a contract here must, for every measure and form of that contract, map it
to a function with an allowed unit. Every class with a contract is strict (`STRICT_CLASSES`:
Bond, IRSwap, IRSwaption; docs/v2/IR_STRICT_CONTRACT.md R3-0, docs/v2/BOND_DESIGN.md decision
4.1): only a mapping satisfies a row, and declaring a contract measure under
`unsupported_measures:` is itself an error. `unsupported_measures:` remains for classes without a
contract (e.g. ConfigInstrument), where the pricing layer raises `UnsupportedMeasureError` on
request. `assets.config` calls `check` at load time; the pricing layer calls `validate_frame` on
frame results.

This module holds data and pure functions only. It never imports `pricebt.assets` or
`pricebt.instrument`: `assets.config` hands it a plain summary of the mappings (`MappedFunction`).
Measures are referred to by NAME; `base_measure` looks the objects up in `pricebt.risk` at call
time, so a name the catalogue does not (yet) export simply has no fallback.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, NamedTuple, Optional, Tuple

import pandas as pd

import pricebt.risk as _risk  # the parent package: importing this module has already imported it
from pricebt.common import AggregationLevel
from pricebt.errors import ConfigError

FORMS = ("scalar", "bucketed", "frame")


@dataclass(frozen=True)
class MeasureRequirement:
    """One row of a contract: a gs measure name, its kind (a key of `KINDS`), the forms an asset
    must provide or declare, and the one-line contract semantics shown in errors and docs."""

    measure: str
    kind: str
    forms: Tuple[str, ...]
    doc: str


# kind -> (allowed units, must_be_intensive). R2-11: True = the function must be intensive
# (`scale_with_quantity: false`); False = no constraint either way. `table` kinds are frames: the
# unit is not checked, the columns are (FRAME_COLUMNS, FRAME_SCALE_COLUMNS).
KINDS: Dict[str, Tuple[Optional[frozenset], Optional[bool]]] = {
    "value": (frozenset({"ccy"}), False),
    "sens1": (frozenset({"ccy_per_bp"}), False),
    "sens2": (frozenset({"ccy_per_bp2"}), False),
    "theta": (frozenset({"ccy"}), False),
    "annuity": (frozenset({"ccy"}), False),
    "rate": (frozenset({"bp", "pct", "decimal"}), True),
    "vol": (frozenset({"bp", "pct", "decimal"}), True),
    "time": (frozenset({"decimal", "number"}), True),
    "prob": (frozenset({"decimal", "number"}), True),
    "notional_level": (frozenset({"bp", "pct", "decimal", "number"}), True),  # a value per unit of notional (R3-1)
    "days": (frozenset({"number"}), True),  # a count of calendar days (BOND_DESIGN DaysToSettlement)
    "table": (None, None),
}

FRAME_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "Cashflows": ("payment_date", "payment_amount", "currency", "payment_type"),
    "CRIFIRCurve": ("RiskType", "Qualifier", "Bucket", "Label1", "Label2", "Amount", "AmountCurrency"),
}
FRAME_SCALE_COLUMNS: Dict[str, Tuple[str, ...]] = {"Cashflows": ("payment_amount",), "CRIFIRCurve": ("Amount",)}  # must scale with quantity

# R3-0: contract measures whose contract text defines them as 0 for the class, so a literal 0.0
# function is the correct computation; each with the reason. The checker skill imports this.
_NO_VOL = "no optionality: the contract defines vol exposure and vol levels as 0 (R2-8)"
_ONE_CURVE = "single curve: the contract defines the basis delta as 0 for a single-curve library"
_ONE_CCY = "single currency: the contract defines the cross-currency delta as 0"
ZERO_BY_CONVENTION: Dict[str, Dict[str, str]] = {
    "IRSwap": {**dict.fromkeys(("IRVega", "IRVanna", "IRVolga", "IRAnnualImpliedVol", "IRAnnualATMImpliedVol", "IRDailyImpliedVol"), _NO_VOL),
               "IRBasis": _ONE_CURVE, "IRXccyDelta": _ONE_CCY},
    "IRSwaption": {"IRBasis": _ONE_CURVE, "IRXccyDelta": _ONE_CCY},
    # BOND_DESIGN decision 4.8: a bullet (non-callable) bond discounted on one curve in its own currency
    "Bond": {**dict.fromkeys(("IRVega", "IRVanna", "IRVolga", "IRAnnualImpliedVol", "IRAnnualATMImpliedVol", "IRDailyImpliedVol"),
                             "a bullet bond has no optionality: vol exposure and vol levels are 0 (R2-8)"),
             "IRBasis": "a bullet bond is discounted on one curve: no projection-vs-discount basis",
             "IRXccyDelta": "a bond pays in one currency: no cross-currency basis"},
}

# ISDA SIMM IR tenors: the allowed CRIFIRCurve Label1 values (lower case)
SIMM_IR_TENORS = ("2w", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "10y", "15y", "20y", "30y")
SIMM_IR_BUCKETS = ("1", "2", "3")  # SIMM Risk_IRCurve currency volatility groups (CRIF Bucket): regular, low, high
SIMM_IR_SUBCURVES = ("OIS", "Libor1m", "Libor3m", "Libor6m", "Libor12m", "Prime", "Municipal")  # ISDA SIMM IR sub-curves (CRIF Label2)


def _req(measure: str, kind: str, forms: str, doc: str) -> MeasureRequirement:
    return MeasureRequirement(measure, kind, tuple(forms.split()), doc)


# IR_RISK_DESIGN section 2.2 as amended by section 00 (R2-1..R2-8). Holder-signed, per unit trade
# (pricebt applies quantity). "s" = scalar form, "b" = bucketed form.
# pricebt DEV-I12 (own-rate IRDelta/IRGammaParallel/IRFwdRate), DEV-I13 (diagonal IRGamma
# ladder), DEV-I15 (Theta per day, total return, own rate/vol fixed), DEV-I17 (ExpiryInYears for
# swaps/bonds): the semantics below.
_IR_BASE = (
    _req("Price", "value", "scalar",
         "PV in the function currency, holder-signed; it either drops each flow on its payment date (Cashflows then lists the flows still to drop) or never drops paid flows (total return: Cashflows is empty)."),
    _req("IRDelta", "sens1", "scalar bucketed",
         "s: TOTAL derivative of Price w.r.t. the own rate r (IRFwdRate) along the library's parallel curve shift, own-strike vol fixed: [PV(+h)-PV(-h)]/[r(+h)-r(-h)] per bp of r (a fixed-annuity pv01 is exact only at the money); "
         "b: curve ladder, ccy per +1bp at each pillar, labels.mkt_type IR. Pay-fixed swap > 0, payer swaption > 0, long bond < 0. IRDeltaParallel/IRDeltaLocalCcy resolve here (DEV-I12)."),
    _req("IRDiscountDeltaParallel", "sens1", "scalar",
         "PV change for a +1bp parallel shift of the discount curve only: forwards (projection) held fixed, only discount factors bumped. Not in general equal to the IRDelta scalar; for a single-curve library this is NOT the parallel dv01 (near zero for an at-the-money swap). IRDiscountDeltaParallelLocalCcy falls back here."),
    _req("IRGammaParallel", "sens2", "scalar",
         "chain-rule second derivative of Price w.r.t. the own rate r on the IRDelta bumps, per bp^2 of r: "
         "[n+ + n- - 2n0 - ((n+ - n-)/(r+ - r-))(r+ + r- - 2r0)] / ((r+ - r-)/2)^2; never d(pv01)/dr (half the gamma at the money). IRGammaParallelLocalCcy falls back here (DEV-I16)."),
    _req("IRGamma", "sens2", "bucketed",
         "diagonal gamma ladder, ccy per bp^2 at each pillar, a 6-column bucketed frame (DEV-I13: gs returns a 12-column cross-gamma frame). The diagonal may be a true Hessian diagonal or the parallel gamma placed at its nearest pillar (implementations differ, DEV-I13)."),
    _req("IRVega", "sens1", "scalar bucketed",
         "s: PV change for +1bp of normal implied vol (IRAnnualImpliedVol); b: vol cube, mkt_point '<tail>;<expiry>' (e.g. '5Y;1Y'), labels.mkt_type IR VOL. "
         "Swaps/bonds: 0.0 / empty by convention (R2-8). IRVegaParallel/IRVegaLocalCcy resolve here."),
    _req("IRVanna", "sens2", "scalar",
         "d(IRDelta scalar)/d(sigma) per bp x bp (rate bp x normal-vol bp); swaps/bonds 0.0. Request as IRVanna(aggregation_level='Type') (FD measure, DEV-I9)."),
    _req("IRVolga", "sens2", "scalar",
         "second derivative of Price w.r.t. normal vol, per bp^2 of vol; swaps/bonds 0.0. Request as IRVolga(aggregation_level='Type') (DEV-I9)."),
    _req("IRBasis", "sens1", "scalar",
         "PV change for +1bp of the basis (projection-vs-discount) spread; single-curve libraries: 0.0. IRBasisParallel resolves here; or request IRBasis(aggregation_level='Type')."),
    _req("IRXccyDelta", "sens1", "scalar",
         "cross-currency basis delta; single-currency instruments: 0.0. IRXccyDeltaParallel resolves here; or request IRXccyDelta(aggregation_level='Type')."),
    _req("IRFwdRate", "rate", "scalar",
         "the own quoted rate: swap par rate, swaption forward rate of the underlying, bond yield to maturity (DEV-I12). Intensive; finite on every held date including the exit date (R2-7)."),
    _req("IRSpotRate", "rate", "scalar",
         "the par rate of the spot-starting equivalent (swap / swaption underlying with the same final date); bond: its yield. Intensive."),
    _req("IRAnnualImpliedVol", "vol", "scalar",
         "annualised NORMAL implied vol at the instrument's strike; swaps/bonds: 0.0 by convention (R2-8). Intensive."),
    _req("IRAnnualATMImpliedVol", "vol", "scalar",
         "ATM-forward normal vol for the same expiry and tail; swaps/bonds: 0.0 (R2-8). Intensive."),
    _req("IRDailyImpliedVol", "vol", "scalar",
         "IRAnnualImpliedVol / sqrt(252); swaps/bonds: 0.0 (R2-8). Intensive."),
    _req("Theta", "theta", "scalar",
         "one calendar day of carry holding the own IRFwdRate and IRAnnualImpliedVol fixed: Price(t+1d) + cashflows Price drops in (t, t+1d] - Price(t), ccy PER DAY "
         "(DEV-I15; curve translated DF(x)/DF(t+1d), never rolled). A per-year IRTheta = 365 x Theta: never map Theta to a per-year function. A discrete own-rate jump when a paid period leaves the remaining schedule is a schedule-roll term; a config that removes it from the own-rate move (holding the own rate fixed) spreads it over the calendar days to the next business day, so Theta x step days counts it once on a business-day grid; on coarser grids the excess lands in the residual."),
    _req("ExpiryInYears", "time", "scalar",
         "max(final_or_expiry - t, 0).days / 365 (calendar days, ACT/365F): a swaption's expiry, a swap's or bond's final date (DEV-I17). Intensive. "
         "It stays 0 from expiry on, so PNL_theta (Theta x change in ExpiryInYears x -365) attributes no carry after it: an exercised swaption's Theta (the underlying swap's, R2-7) lands in the residual."),
    _req("Annuity", "annuity", "scalar",
         "PV of the fixed leg paying 1.0 per annum (1e4 x the fixed-leg pv01), ccy; bond: PV of 1.0 per annum on its schedule. "
         "Holder-signed like Price: pay-fixed swap > 0, receive-fixed < 0; bought swaption > 0, payer or receiver (the underlying's annuity on the swaption's signed notional); long bond > 0. "
         "A library whose fixed-leg bp value carries the fixed leg's own sign (negative for a payer) needs Annuity = -1e4 x that value."),
    _req("Cashflows", "table", "frame",
         "the flows still included in Price that Price will drop on their payment date (payment_date > pricing date), holder-signed, one row each; empty for a total-return Price (R2-6). "
         "Required columns payment_date, payment_amount, currency, payment_type; returns: frame with scale_columns including payment_amount."),
)

# docs/v2/IR_STRICT_CONTRACT.md R3-1: the rest of the gs measures that apply to a swap or a
# swaption (pricebt DEV-I19 for their definitions). Bond does not get them.
_IR_STRICT_EXTRA = (
    _req("ParSpread", "rate", "scalar",
         "the spread, in the declared rate unit, added to the floating leg's rate that makes Price zero; independent of direction (payer and receiver of the same terms share it). "
         "Single curve with matching leg schedules: fixed_rate - IRFwdRate; swaption: the underlying swap's (strike - forward). Dead instruments: continuous with the last live value (R2-7). Intensive."),
    _req("FairPremium", "value", "scalar",
         "the premium, paid by the holder on the premium settlement date, that makes the instrument plus premium worth zero: Price / DF(settlement), ccy. "
         "Settlement: the swaption's premium_payment_date if the library supports it and it is set, else the library's spot date for the currency; a library with no spot lag uses the pricing date, so FairPremium == Price (DEV-I19)."),
    _req("ForwardPrice", "value", "scalar",
         "Price forward-valued to the expiry date ExpiryInYears counts to (a swaption's expiration_date, a swap's termination date, DEV-I17): Price / DF(expiry), ccy; on or after expiry: Price. "
         "DEV-I19: gs declares the unit BPS but documents the price at expiry in the local currency; pricebt follows the docstring."),
    _req("PremiumCents", "notional_level", "scalar",
         "Price / |notional| in the declared unit, |notional| the unit trade's absolute notional_amount; in bp this is gs's premium in cents (1 cent per 100 of notional = 1bp of notional). Intensive (DEV-I19)."),
    _req("LocalAnnuityInCents", "notional_level", "scalar",
         "Annuity / |notional|: the PV of 1.0 per annum per unit of notional, holder-signed like Annuity (a 10y pay-fixed swap is about +8.5); declare unit decimal, or number with scale_with_quantity: false. "
         "It equals the PV in cents, per 100 of notional, of 1bp per annum. Intensive (DEV-I19)."),
    _req("CompoundedFixedRate", "rate", "scalar",
         "the fixed rate (swaption: the strike) restated as an annually compounded rate: (1 + K/f)^f - 1 for a fixed leg paying f times a year (an annual fixed leg: K itself). A trade term, finite on every date. Intensive (DEV-I19)."),
    _req("CRIFIRCurve", "table", "frame",
         "ISDA SIMM CRIF rows for IR curve delta, one per ladder pillar. Required columns RiskType ('Risk_IRCurve'), Qualifier (the currency ISO code), Bucket (the SIMM currency volatility group as a string; '1' for regular-volatility currencies such as USD and EUR), "
         "Label1 (SIMM tenor, lower case, one of 2w 1m 3m 6m 1y 2y 3y 5y 10y 15y 20y 30y), Label2 (the ISDA SIMM sub-curve name, e.g. 'OIS'; a SOFR curve is 'OIS'), Amount (PV change for +1bp at that pillar, in AmountCurrency, holder-signed, on the basis of the config's own IRDelta bucketed ladder), AmountCurrency; "
         "returns: frame with scale_columns including Amount. Identity: sum of Amount = sum of the IRDelta bucketed ladder. Dead instrument: an empty frame with these columns. DEV-I19: gs returns the full CRIF schema; pricebt requires this subset."),
    _req("PnlExplain", "value", "bucketed",
         "the change in value from market to market_to by risk factor, no time component (IR_RISK_DESIGN section 8): a returns: buckets portfolio function (hence the bucketed form) that also receives market_to and pricebt_to_date, "
         "rows labelled by mkt_type (IR, IR VOL, ...), ccy. Swaps: one IR row = Price(market_to) - Price(market), plus an optional IR VOL row of 0. Allowed caveat: a library whose market objects carry their own valuation date (it cannot value a later market from the pricing date) includes the carry between the two dates. PnlExplainClose resolves here."),
)

# docs/v2/BOND_DESIGN.md (revision 4): the Bond contract. A bond's Price is its settlement-date
# market value, so Price, Theta and Cashflows get Bond text; the gs measures a cash bond can answer
# get Bond text; pricebt DEV-I20 (bond analytics) and DEV-I21 (financing) name the pricebt-only
# measures (pricebt.risk.PRICEBT_MEASURES). H, the carry horizon, is the settlement date plus one
# calendar month, rolled to the following business day of the bond's calendar.
_BOND_BASE_OVERRIDES = {
    "Price": _req("Price", "value", "scalar",
                  "the position's settlement-date market value: (clean price + accrued interest at standard settlement) per 100 face x face / 100, holder-signed (a long is positive), "
                  "not discounted from the settlement date to the pricing date (DEV-I20). It drops a flow on the first trade date whose standard settlement is on or after the flow's payment date; Cashflows lists the flows still to drop."),
    "Theta": _req("Theta", "theta", "scalar",
                  "carry per calendar day with the yield (IRFwdRate) held fixed, over the step to the next business day nb: [Price(nb, same yield) + flows Price drops in (t, nb] - Price(t)] / (nb - t).days, ccy per day (DEV-I15). "
                  "Price is a settlement-date value, so one calendar day can move settlement by 0 or 3 days; spreading the next-business-day step makes Theta x step days exact on a business-day grid. Financing is not in Theta (FinancingToDate)."),
    "Cashflows": _req("Cashflows", "table", "frame",
                      "the flows still included in Price, one row each, holder-signed; payment_date is the trade date on which Price drops the flow: the first date whose standard settlement is on or after the flow's payment date (T+1: the business day before a business-day coupon date). "
                      "Required columns payment_date, payment_amount, currency, payment_type; returns: frame with scale_columns including payment_amount. A financed position's engine books these rows as cash on payment_date (DEV-E22)."),
}
_SWAP_ROWS = {r.measure: r for r in _IR_STRICT_EXTRA}
_BOND_EXTRA = (
    _req("LightningDV01", "sens1", "scalar", "yield DV01: Price change for +1bp of yield (= the IRDelta scalar for a bond)."),
    _req("LightningOAS", "rate", "scalar", "option-adjusted spread over the library's reference curve (a bullet bond: its Z-spread). Intensive."),
    _req("ParSpread", "rate", "scalar", "par asset-swap spread (or the library's par spread) in the declared unit. Intensive."),
    _req("FairPremium", "value", "scalar",
         "the amount the holder pays at standard settlement for the position: Price itself, since Price is already the settlement-date value (DEV-I20). ccy."),
    _req("ForwardPrice", "value", "scalar",
         "the forward (financed) value at the horizon H = settlement + 1 calendar month (following business day): Price x (1 + RepoRate x tau(s, H)) - sum over flows c paid in (s, H] of C x (1 + RepoRate x tau(c, H)), "
         "tau in the repo day count, RepoRate held flat to H, the whole Price financed (a forward price does not depend on the haircut); ccy, holder-signed. Dead (nothing left to pay): 0 (DEV-I21; the swap and swaption rows forward to expiry instead)."),
    _req("PremiumCents", "notional_level", "scalar", "Price / |face| in the declared unit (pct: the dirty price per 100). Intensive (DEV-I19)."),
    _req("LocalAnnuityInCents", "notional_level", "scalar", "Annuity / |face|: the PV of 1.0 per annum per unit of face. Intensive (DEV-I19)."),
    _req("CompoundedFixedRate", "rate", "scalar", "the coupon restated as an annually compounded rate: (1 + c/f)^f - 1 for f coupons a year. A trade term, finite on every date. Intensive (DEV-I19)."),
    _SWAP_ROWS["CRIFIRCurve"],
    _req("PnlExplain", "value", "bucketed",
         "the change in value from market to market_to by risk factor, no time component (IR_RISK_DESIGN section 8): a returns: buckets portfolio function receiving market_to and pricebt_to_date, rows labelled by mkt_type "
         "(IR for the curve, e.g. CREDIT for the bond's spread to it), summing to Price(market_to) - Price(market), ccy. PnlExplainClose resolves here."),
    # pricebt DEV-I20: bond analytics gs has no measure for
    _req("CleanPrice", "notional_level", "scalar",
         "the quoted clean price per 100 face for standard settlement: DirtyPrice - 100 x AccruedInterest / face (signed face). Dead (Price 0): 0. Intensive (DEV-I20)."),
    _req("DirtyPrice", "notional_level", "scalar", "100 x Price / face (signed face): the invoice price per 100, the same for a long and a short. Dead (Price 0): 0. Intensive (DEV-I20)."),
    _req("AccruedInterest", "value", "scalar",
         "the coupon accrued from the last coupon date to the standard settlement date in the bond's accrual convention (US Treasuries: ACT/ACT ICMA), holder-signed, ccy; 0 when settlement is a coupon date and after maturity (DEV-I20)."),
    _req("ModifiedDuration", "time", "scalar",
         "-(1/P) dP/dy in years per unit of decimal yield, y in the IRFwdRate convention, P the dirty price; 0 when dead. Intensive (DEV-I20)."),
    _req("Convexity", "time", "scalar", "(1/P) d2P/dy2 in years^2, y and P as ModifiedDuration; 0 when dead. Intensive (DEV-I20)."),
    _req("DaysToSettlement", "days", "scalar",
         "calendar days from the pricing date to the standard settlement date (US Treasuries T+1: 1, or 3 over a weekend or a holiday). Intensive (DEV-I20)."),
    # pricebt DEV-I21: the financing contract
    _req("RepoRate", "rate", "scalar",
         "the funding rate in force on the pricing date for this position: overnight general collateral or special, or a term rate locked at the trade date; the library or the data decides. "
         "Simple interest in the config's repo day count (USD: ACT/360). Finite on every held date. Intensive (DEV-I21)."),
    _req("RepoHaircut", "rate", "scalar", "the fraction of the settlement value not financed, in the declared unit (decimal 0.02 = 2%). Intensive (DEV-I21)."),
    _req("FinancingToDate", "value", "scalar",
         "cumulative repo interest on the position's funding leg from the settlement of its trade date to the settlement of the pricing date (or maturity, if earlier), holder-signed: a long pays (<= 0), a short lends the cash and receives (>= 0). "
         "Principal (1 - RepoHaircut) x Price on the trade date (pinned by resolve); simple interest at each calendar day's RepoRate (the last business day's fixing over weekends and holidays); 0 on the trade date. "
         "The engine books its change over each step as cash (DEV-E22). ccy (DEV-I21)."),
    _req("Carry", "value", "scalar",
         "clean value now minus clean forward value at H: (Price - AccruedInterest) - (ForwardPrice - accrued at H) = coupon income over (s, H] minus financing at RepoRate; ccy, holder-signed; dead: 0 (DEV-I21)."),
    _req("RollDown", "value", "scalar",
         "clean value at H on the library's reference curve rolled down (unchanged in time to maturity, spread held) minus clean value now; on a flat curve, the pull to par at constant yield. "
         "Carry + RollDown is the P&L to H, the whole Price financed at RepoRate and coupons paid in (s, H] reinvested at it, if the curve does not move (a backtest finances (1 - h) of Price, FinancingToDate, and holds coupons as cash). ccy, holder-signed; dead: 0 (DEV-I21)."),
)

CONTRACTS: Dict[str, Tuple[MeasureRequirement, ...]] = {
    "IRSwap": _IR_BASE + _IR_STRICT_EXTRA,
    "IRSwaption": _IR_BASE + _IR_STRICT_EXTRA + (
        _req("ProbabilityOfExercise", "prob", "scalar", "probability (0..1) of finishing in the money under the annuity measure. Intensive."),
    ),
    "Bond": tuple(_BOND_BASE_OVERRIDES.get(r.measure, r) for r in _IR_BASE) + _BOND_EXTRA,
}

# every class with a contract (BOND_DESIGN decision 4.1; R3-0): only a mapping satisfies a row, and
# declaring a contract measure (or a preset/fallback of one) under unsupported_measures: is a load error
STRICT_CLASSES = frozenset(CONTRACTS)


class MappedFunction(NamedTuple):
    """What `check` needs to know about the function in one `risk_measures:` slot (built by
    `assets.config`, so this module never imports `pricebt.assets`)."""

    function: str
    unit: str
    scale_with_quantity: bool
    returns: str  # "scalar" | "frame" (functions:) or "scalar" | "buckets" (portfolio_functions:)
    scale_columns: Tuple[str, ...] = ()


class ContractCheck(NamedTuple):
    """`check`'s result: `problems` make the config invalid; `warnings` are declarations of names
    outside the contract that cannot mean what they say (a preset name, an unknown name); `missing`
    are the `(measure, form)` pairs no mapping provides, in contract order (feed them to
    `mapping_skeleton`)."""

    problems: List[str]
    warnings: List[str]
    missing: List[Tuple[str, str]]


# docs/v2/IR_STRICT_CONTRACT.md R3-1 (BOND_DESIGN decision 4.2): the IR-relevant pricebt.risk names
# (asset_class Rates or None, plus the relative-measure classes) deliberately outside every
# contract, with the reason. tests/test_contracts.py checks this list is exactly what is neither a
# measure of some contract nor a preset/fallback of one.
_INFLATION = "inflation instruments"
EXCLUDED: Dict[str, str] = {
    "DollarPrice": "the engine maps it to Price(currency='USD')",
    "ResolvedInstrumentValues": "the engine answers it (resolution), not a config function",
    "PricePips": "FX quoting (pips)",
    "Description": "non-numeric gs server metadata",
    "Market": "non-numeric gs server metadata",
    "MarketData": "non-numeric gs server metadata",
    "MarketDataAssets": "non-numeric gs server metadata",
    "BaseCPI": _INFLATION,
    "InflMaturityCPI": _INFLATION,
    "Infl_CompPeriod": _INFLATION,
    "InflationDelta": _INFLATION,
    "InflationDeltaParallel": _INFLATION,
    "InflDeltaParallelLocalCcyInBps": _INFLATION,
    "PnlExplainLive": "the live market: NotSupportedError (DEV-M1)",
    "PnlPredictLive": "the live market: NotSupportedError (DEV-M1)",
    "FairVarStrike": "variance swaps",
    "FairVolStrike": "variance swaps",
    "CrossMultiplier": "FX",
}


def contract_for(instrument: str) -> Tuple[MeasureRequirement, ...]:
    """The contract of an instrument class name; `()` for classes without one (FXOption, EqOption,
    InflationSwap, Cash, FXForward, ConfigInstrument, ...), which keep the plain "Price is
    required" rule."""
    return CONTRACTS.get(instrument, ())


def is_strict(instrument: str) -> bool:
    """True for a class whose contract rows only a mapping satisfies: every class with a contract
    (R3-0; BOND_DESIGN decision 4.1)."""
    return instrument in STRICT_CLASSES


# PnlExplainClose is a class (PnlExplainClose() is a PnlExplain named "PnlExplain"), so it has no
# base_name to read; a key or declaration of it counts toward PnlExplain (R3-1)
_CLASS_PRESETS = {"PnlExplainClose": "PnlExplain"}


def _is_catalogue_name(name: str) -> bool:
    """True if `name` is a pricebt.risk measure: an instance, or a relative-measure class
    (PnlExplain and its family, PnlPredictLive)."""
    obj = getattr(_risk, name, None)
    return isinstance(obj, _risk.RiskMeasure) or (isinstance(obj, type) and issubclass(obj, (_risk.PnlExplain, _risk.PnlPredictLive)))


def base_measure(key: str) -> Tuple[str, Optional[str]]:
    """`(base, form)` for a `risk_measures:` key (R2-10). A preset or fallback name in
    `pricebt.risk` (one with a `base_name`, e.g. IRDeltaParallel, or PnlExplainClose) counts
    toward its base measure: `form` is "scalar" when the preset always asks for the scalar form (a
    finite-difference measure with aggregation_level other than Point), else None (each mapping
    slot counts as its own form, e.g. IRDeltaLocalCcy, IRGammaParallelLocalCcy). Any other key
    maps to itself."""
    if key in _CLASS_PRESETS:
        return _CLASS_PRESETS[key], None
    obj = getattr(_risk, key, None)
    base = getattr(obj, "base_name", None) if isinstance(obj, _risk.RiskMeasure) else None
    if not base:
        return key, None
    agg = getattr(obj.parameters, "aggregation_level", None)
    return base, ("scalar" if agg is not None and agg != AggregationLevel.Point else None)


def _slot_problems(req: MeasureRequirement, key: str, form: str, fn: MappedFunction) -> List[str]:
    where = f"{req.measure} ({form}{'' if key == req.measure else f', via {key}'})"
    units, intensive = KINDS[req.kind]
    if req.kind == "table":
        if fn.returns != "frame":
            return [f"{where}: function {fn.function!r} returns {fn.returns!r}; this measure is a table: map a functions: entry with `returns: frame`"]
        out = []
        missing_cols = [c for c in FRAME_SCALE_COLUMNS.get(req.measure, ()) if c not in fn.scale_columns]
        if missing_cols:
            out.append(f"{where}: function {fn.function!r} scale_columns {list(fn.scale_columns)} must include {missing_cols}")
        if FRAME_SCALE_COLUMNS.get(req.measure) and not fn.scale_with_quantity:
            out.append(f"{where}: function {fn.function!r} has scale_with_quantity false; its amounts must scale with the position")
        return out
    if fn.returns == "frame":
        return [f"{where}: function {fn.function!r} returns a frame; this measure needs a number"]
    out = []
    if fn.unit not in units:
        out.append(f"{where}: function {fn.function!r} has unit {fn.unit!r}; allowed {sorted(units)}")
    if intensive and fn.scale_with_quantity:
        out.append(f"{where}: function {fn.function!r} scales with quantity; a {req.kind} level must be intensive (set scale_with_quantity: false)")
    return out


def _mapped_slots(reqs: Mapping[str, MeasureRequirement], mapped: Mapping[str, Mapping[str, Optional[MappedFunction]]]):
    """Every mapped slot as `(key, base measure, requirement or None, form, function, counts)`;
    `counts` is False for a preset's bucketed slot, which never serves the base's bucketed form."""
    for key, slots in mapped.items():
        base, preset_form = base_measure(key)
        req = reqs.get(base)
        for slot in ("scalar", "bucketed"):
            fn = slots.get(slot)
            if fn is None:
                continue
            if slot == "bucketed":
                form = "bucketed"
            elif req is not None:
                # the scalar slot is an attempt at the contract's own shape (a frame for a table),
                # so a shape mismatch is one problem, not also a "missing" one
                form = "frame" if req.kind == "table" else "scalar"
            else:
                form = "frame" if fn.returns == "frame" else "scalar"
            yield key, base, req, form, fn, not (slot == "bucketed" and preset_form == "scalar")


def provided_forms(instrument: str, mapped: Mapping[str, Mapping[str, Optional[MappedFunction]]]) -> Dict[Tuple[str, str], str]:
    """`{(base measure, form): the risk_measures key whose slot provides it}` (the first such key in
    mapping order). What `check` counts toward a contract row, and what the pricing layer serves a
    request by when neither the measure's name nor its `base_name` is a key (R2-10: e.g. an
    `IRDeltaParallel` key serves `IRDelta(aggregation_level='Type')`, an `IRGammaParallelLocalCcy`
    key serves `IRGammaParallel`), so a contract row that loads is a request that prices."""
    out: Dict[Tuple[str, str], str] = {}
    for key, base, _req, form, _fn, counts in _mapped_slots({r.measure: r for r in contract_for(instrument)}, mapped):
        if counts:
            out.setdefault((base, form), key)
    return out


def check(instrument: str, mapped: Mapping[str, Mapping[str, Optional[MappedFunction]]], unsupported: Mapping[str, Mapping[str, str]]) -> ContractCheck:
    """Check an asset's mappings against its instrument's contract (IR_RISK_DESIGN section 2.1,
    R3-0, BOND_DESIGN decision 4.1).

    `mapped` is `{risk_measures key: {"scalar": MappedFunction | None, "bucketed": ... | None}}`;
    `unsupported` is `{measure: {form or "*": reason}}` (`AssetConfig.unsupported_measures`; a bare
    reason string, the YAML shape, also means "*"). A form is satisfied iff a mapping slot provides
    it (`provided_forms`: preset keys count toward their base, `base_measure`). A frame is a
    scalar-slot function with `returns: frame`. Every mapped slot of a contract measure is also
    checked for unit, intensivity and shape; measure names outside the contract are unrestricted.

    Every class with a contract is strict: declaring a contract measure, one of its forms, or a
    preset/fallback resolving to one is a problem, mapped or not. Any other declaration that is also
    mapped loads with a warning (R2-9: the mapping wins; this is how a class without a contract,
    e.g. ConfigInstrument, meets it). On a contract class, a declaration of a name outside the
    contract that cannot mean what it says (a preset of a non-contract measure, an unknown name) is
    a warning too. A class without a contract has no rows: its declarations raise
    `UnsupportedMeasureError` when requested.
    """
    reqs = {r.measure: r for r in contract_for(instrument)}
    unsupported = {m: ({"*": v} if isinstance(v, str) else v) for m, v in unsupported.items()}
    problems: List[str] = []
    for key, _base, req, form, fn, _counts in _mapped_slots(reqs, mapped):
        if req is not None:
            problems += _slot_problems(req, key, form, fn)
    provided = provided_forms(instrument, mapped)

    strict_names = "/".join(sorted(STRICT_CLASSES))
    missing: List[Tuple[str, str]] = []
    for req in reqs.values():
        gaps = [f for f in req.forms if (req.measure, f) not in provided]
        if gaps:
            missing += [(req.measure, f) for f in gaps]
            problems.append(f"{req.measure} ({', '.join(gaps)}): not mapped ({strict_names} require a mapping for every contract measure) -- {req.doc}")
    warnings: List[str] = []
    for measure, declared in unsupported.items():
        base = base_measure(measure)[0]
        if base in reqs:
            what = measure if base == measure else f"{measure} (a preset or fallback of {base})"
            problems.append(f"unsupported_measures declares {what}, a {instrument} contract measure: {strict_names} configs must map every contract measure; unsupported_measures cannot satisfy them -- map it and remove the declaration")
            continue
        # pricebt DEV-I11 (R2-9): a mapping wins over a stale declaration, with a warning
        stale = False
        for form in declared:
            hits = sorted(f"{f} via {k}" if k != measure else f for (m, f), k in provided.items() if m == measure and form in ("*", f))
            if hits:
                stale = True
                warnings.append(f"{measure} is declared unsupported ({'every form' if form == '*' else form}) but mapped ({', '.join(hits)}); the mapping is used -- remove or narrow the stale declaration")
        if stale or not reqs:
            continue
        if base != measure:
            warnings.append(f"{measure} is a preset or fallback of {base}; declare {base} instead (a request for {measure} falls back to {base}'s mapping or declaration)")
        elif not _is_catalogue_name(measure):
            close = difflib.get_close_matches(measure, list(reqs), n=1)
            hint = f" (did you mean {close[0]!r}?)" if close else ""
            warnings.append(f"{measure} is neither in the {instrument} contract nor a pricebt.risk measure{hint}; if misspelt it declares nothing (it only affects a request named exactly {measure})")
    return ContractCheck(problems, warnings, missing)


# the stub expression of mapping_skeleton: deliberately a syntax error, so a pasted skeleton cannot
# load until every expression is written (`...` alone would compile: it is Ellipsis)
SKELETON_EXPR = "... TODO"


def _snake(measure: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", measure).lower()


def mapping_skeleton(instrument: str, missing: Iterable[Tuple[str, str]]) -> str:
    """Paste-ready YAML mapping every contract `(measure, form)` in `missing` (R3-0): a
    `functions:` stub per scalar or frame form and a `portfolio_functions:` stub (`returns:
    buckets`) per bucketed form, each with an allowed unit of the measure's kind, `scale_with_quantity:
    false` for an intensive kind and `returns: frame` + `scale_columns` for a table, then the
    `risk_measures:` lines. Every `expr` is `SKELETON_EXPR`, which does not compile: the skeleton
    loads only once each stub computes its measure. Merge it into the config (a measure with one
    form already mapped gets a `{form: name}` entry to merge with the existing one)."""
    reqs = {r.measure: r for r in contract_for(instrument)}
    wanted: Dict[str, set] = {}
    for measure, form in missing:
        wanted.setdefault(measure, set()).add(form)
    functions, portfolio, risk_measures = ["functions:"], ["portfolio_functions:"], ["risk_measures:"]
    for req in (r for r in reqs.values() if r.measure in wanted):
        units, intensive = KINDS[req.kind]
        slots = []
        for form in (f for f in req.forms if f in wanted[req.measure]):
            name = _snake(req.measure) + ("_buckets" if form == "bucketed" else "")
            out = portfolio if form == "bucketed" else functions
            if units is None:
                out += [f"  {name}:   # {req.measure} ({form}): columns {', '.join(FRAME_COLUMNS.get(req.measure, ()))}", f"    expr: '{SKELETON_EXPR}'", "    unit: ccy", "    returns: frame",
                        f"    scale_columns: [{', '.join(FRAME_SCALE_COLUMNS.get(req.measure, ()))}]"]
            else:
                unit = "decimal" if "decimal" in units else min(units)
                out += [f"  {name}:   # {req.measure} ({form}): kind {req.kind}, unit one of {', '.join(sorted(units))}", f"    expr: '{SKELETON_EXPR}'", f"    unit: {unit}"]
                if intensive:
                    out.append("    scale_with_quantity: false")
                if form == "bucketed":
                    out.append("    returns: buckets")
            slots.append(f"{'bucketed' if form == 'bucketed' else 'scalar'}: {name}")
        risk_measures.append(f"  {req.measure}: {{{', '.join(slots)}}}")
    return "\n".join(line for part in (functions, portfolio, risk_measures) if len(part) > 1 for line in part) + "\n"


def validate_frame(measure_name: str, frame: pd.DataFrame) -> None:
    """Raise `ConfigError` unless `frame` is a DataFrame with every required column of
    `measure_name` (FRAME_COLUMNS), even when it has no rows: consumers index those columns. A
    measure with no required columns only has to be a DataFrame."""
    if not isinstance(frame, pd.DataFrame):
        raise ConfigError(f"{measure_name}: a frame measure must produce a DataFrame, got {type(frame).__name__}")
    required = FRAME_COLUMNS.get(measure_name, ())
    absent = [c for c in required if c not in frame.columns]
    if absent:
        raise ConfigError(f"{measure_name}: frame is missing required column(s) {absent}; required {list(required)} (return pd.DataFrame(columns=[...]) when there are no rows)")
