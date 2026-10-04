"""check_asset_ir: the interest-rate rows of check_asset.py -- the measure-contract rows, the contract
semantics every IR pack shares, the swaption and bond packs, and the finite-difference-parameter
probe. `check_asset.run_checks` adds them through `with_ir_checks`. Like check_asset, this module
never imports a pricing library: every number is a pricebt evaluation of the config under test, so
the rows work for any library behind it.

Pack selection (`check_asset.py CONFIG --pack NAME`, `run_checks(..., pack=NAME)`):
  auto (default)  the config's `instrument:` if it is IRSwap, IRSwaption or Bond, else no pack
  none            no contract rows, no IR rows, no pack
  IRSwap | IRSwaption | Bond   force that pack and that class's contract (use it for a
                  ConfigInstrument asset that is really a swap, swaption or bond)
IRSwap keeps check_asset.swap_pack as its pack; the contract and ir_* rows run for all three.

Each row, and the realistic mistake it catches (status on the mistake):

SHAPES (every config; the generic check_asset rows skip frame and relative functions)
  functions_finite[f] / risk_measures[M] for a `returns: frame` function or a relative measure
                      (PnlExplain): evaluated in its own shape, never through float().
  quantity_scaling[M bucketed|frame]  a ladder or a frame's scale_columns that ignore quantity_, or
                      a frame scaling a column it must not (FAIL). Scalar forms keep the generic row.

CONTRACT (docs/v2/IR_RISK_DESIGN.md section 2, docs/v2/IR_STRICT_CONTRACT.md R3-0, docs/v2/BOND_DESIGN.md 4.1)
  contract[M]         every contract class is strict (Bond, IRSwap, IRSwaption): PASS mapped; FAIL
                      not mapped, or declared under unsupported_measures (the measure, a form, or a
                      preset of it), mapped or not: only a mapping satisfies a row; FAIL a mapped
                      slot with the wrong unit/shape. The loader already refuses every FAIL here, so
                      they show on --pack only (a ConfigInstrument).
  contract_declarations  FAIL per declared contract measure or preset of one; WARN per declared
                      preset of a non-contract measure or unknown name.
  ir_fake_constant    a contract measure mapped to a literal constant ('0.0', '{}', float('nan'),
                      math.nan, math.inf) outside contracts.ZERO_BY_CONVENTION[class] (vol measures
                      on a swap or a bond, IRBasis, IRXccyDelta; the PASS lists each with its
                      reason): a placeholder standing in for a measure the library was never asked
                      for (FAIL); a zero-by-convention measure mapped to a constant other than 0 /
                      {} (IRVega '5.0') (FAIL).

IR SEMANTICS (every IR pack; library-agnostic, from evaluated values only)
  ir_expiry_in_years  ExpiryInYears not falling by exactly (d2-d1).days/365 (business-day or
                      ACT/365.25 year fractions; a date that never moves) (FAIL).
  ir_taylor           Price(t1)+cash-Price(t0) on two consecutive business days after d2 versus
                      delta*dr + gamma*dr^2/2 + vega*ds + vanna*dr*ds + volga*ds^2/2 + theta*days:
                      a wrong unit, sign or scale in any mapped greek (WARN > 5%, FAIL > 20% of
                      the explained size).
  ir_theta            Theta against the implied one-day carry of the same step (the step's Price
                      change with every other term removed): per-year theta (ratio ~365, FAIL
                      "looks per year"), a rolled instead of translated curve, a missing cash term
                      (WARN). The implied carry absorbs every other term's error too: a WARN here
                      with nothing else flagged can be an IRDelta that is not the total own-rate
                      derivative (annuity pv01 off-market) or a wrong gamma/vega.
  ir_gamma_ratio      IRGammaParallel against d(IRDelta)/dr, fitted over 20 business-day steps
                      from d1 as d(IRDelta) = slope*dr + drift*days (vanna*dsigma removed; a bond's
                      days are settlement days, its Price being a settlement-date value), so a
                      delta drifting with time (a bond's charm) does not bias it: the half-gamma
                      trap (ratio ~0.5, FAIL); ratio ~2 is a WARN (an ATM-exact fixed-annuity
                      IRDelta with a true gamma, or a doubled gamma). When |IRDelta| equals
                      |Annuity| x 1e-4 off-market (a fixed-annuity pv01), the reference is
                      2 x d(IRDelta)/dr. IRSwap with no Annuity mapped (or no off-market d2): a
                      PASS becomes WARN "unverifiable", because a fixed-annuity IRDelta with a
                      half gamma also reads ~1.
  swap_annuity_sign   (IRSwap) Annuity not payer-positive (pay-fixed > 0, receive-fixed < 0, the
                      sign of IRDelta): a QuantLib-style fixedLegBPS mapped as is (FAIL).
  ir_dead_levels      an exception or NaN on/after the final or expiry date (R2-7): levels must
                      stay finite, sensitivities finite, ExpiryInYears 0 (FAIL).
  ir_ladder_sum[M]    bucketed sum versus scalar (INFO within 5%, WARN beyond: an own-rate scalar
                      and a curve ladder may differ by dr/ds, R2-2).
  ir_vega_cube_keys   vega cube mkt_point not '<tail>;<expiry>' (FAIL), or apparently
                      '<expiry>;<tail>' / mkt_type not 'IR VOL' (WARN).
  ir_cashflows        a flow already paid still listed, or payment_amount not holder-signed
                      (the opposite direction must negate every amount) (FAIL).
  ir_cashflow_drop    Price not dropping the flow Cashflows lists on its payment date (or
                      dropping a different amount): the Taylor residual across the first payment
                      date against that flow (FAIL > 20%). IRSwap, when that is not a PASS: a
                      seasoned swap's own par rate jumps when the paid period rolls off the remaining
                      schedule, so the delta term is re-taken on the MARKET part of the IRFwdRate
                      move (IRFwdRate on the payment step's end date, minus the same priced on the
                      start date's market via a CloseMarket override), the roll part at the
                      remaining Annuity x 1e-4 (Theta holds the own par fixed across it, DEV-I15),
                      and the bands apply to that.
STRICT IDENTITIES (IRSwap, IRSwaption; IR_STRICT_CONTRACT R3-1; FAIL on a sign or identity break,
WARN on a tolerance miss; each SKIPs when its measures are not mapped). A Bond runs ir_local_annuity
(per |size|), ir_compounded_fixed_rate (the resolved coupon and frequency) and ir_crif; its
ForwardPrice, FairPremium, PremiumCents and ParSpread have Bond texts (the bond pack below).
  ir_premium_cents    PremiumCents != Price / |notional_amount| x the unit factor (bp 1e4, pct 100,
                      decimal/number 1), checked on BOTH directions: a percent-of-notional declared
                      bp, a signed notional, an unsigned abs(Price) (FAIL).
  ir_local_annuity    LocalAnnuityInCents != Annuity / |notional_amount| (same factors), on both
                      directions: an annuity per bp (x 1e-4), a signed notional, abs(Annuity) (FAIL).
  ir_forward_price    ForwardPrice / Price = 1 / DF(expiry): opposite sign (FAIL); the implied rate
                      z = ln(ratio) / ExpiryInYears of the opposite sign to IRFwdRate r once |r| >=
                      10bp (FAIL: Price x DF, at any rate level); z far from r (WARN beyond
                      max(r/2, 25bp), FAIL beyond max(r, 50bp): the wrong date); == Price from expiry on.
  ir_fair_premium     FairPremium / Price = 1 / DF(settlement): opposite sign (FAIL); ln(ratio) of the
                      opposite sign to IRFwdRate once |r| >= 10bp (FAIL: Price x DF(spot)); more than
                      ~10 days of discounting (WARN to 1%: a far premium date; FAIL beyond: the
                      expiry or final date used, which is ForwardPrice).
  ir_par_spread       ParSpread vs K - IRFwdRate in bp: changes with direction, reversed sign
                      (F - K), or off by a scale factor x2 (FAIL); more than max(0.5bp, 1%) apart
                      (WARN: legs on different schedules or curves).
  ir_compounded_fixed_rate  outside [K, e^K - 1] (a de-compounded or continuous restatement) or
                      moving with the pricing date (FAIL); when the fixed-leg frequency f is knowable
                      (a fixed_rate_frequency kwarg or resolved term, else the spacing of the fixed
                      leg's Cashflows dates), != (1 + K/f)^f - 1: a semiannual leg returned
                      uncompounded as K (FAIL). f unknowable: the bounds only, and the row says so.
  ir_crif             CRIFIRCurve RiskType not 'Risk_IRCurve', Qualifier not the config currency,
                      Bucket not a SIMM volatility group string ('1'/'2'/'3'; an int 1 FAILs),
                      Label1 not a SIMM tenor ('10Y' upper case), Label2 not a SIMM sub-curve
                      (OIS, Libor1m/3m/6m/12m, Prime, Municipal; 'SOFR' FAILs), AmountCurrency not
                      the Qualifier's currency (FAIL); sum(Amount) != sum of the IRDelta ladder
                      (FAIL; WARN within 1%).

  fd_params / fd_params[M]  DEV-I10: a function not naming pricebt_bump_size must make
                      IRDelta(aggregation_level='Type', bump_size=1) raise NotSupportedError (PASS),
                      else FAIL "silently ignored"; a function naming a parameter is evaluated with
                      two values (INFO: the difference; WARN if identical).

SWAPTION PACK (IRSwaption; direction buy_sell, option type pay_or_receive, strike `strike`)
  swaption_buy_sell_fold  a Sell priced like a Buy: every extensive scalar must negate, every
                      level must stay equal (FAIL).
  swaption_straddle   Straddle != Payer + Receiver (FAIL), or accepted at resolve then failing
                      to price (FAIL); a clean error at resolve is a PASS.
  swaption_strike_pinned  'ATM' / 'A-50' / 'ATM+25' left as strings, or offsets not 50bp/25bp
                      on a decimal strike (FAIL); a grammar the resolve rejects cleanly (WARN).
  swaption_parity     Payer(K) - Receiver(K) != Annuity * (F - K) with F = IRFwdRate (needs
                      Annuity and IRFwdRate): strike or annuity in another unit, annuity not
                      holder-signed (WARN > 0.01bp running, FAIL > 1bp).
  swaption_fwd_unit   an ATM-struck clone's IRFwdRate on its trade date != its resolved strike x 1e4
                      in bp: a forward in pct or decimal under a bp declaration (FAIL).
  swaption_vol_unit   IRAnnualImpliedVol in bp (by its declared unit) under 5 (WARN: percent?),
                      under 0.2 or above 1000 (FAIL: decimal, or a lognormal vol).
  swaption_vega_sign / swaption_delta_sign / swaption_gamma_sign  bought vega > 0, payer delta
                      > 0 and receiver delta < 0, bought gamma > 0 (FAIL).
  swaption_prob_exercise  ProbabilityOfExercise outside [0, 1] (FAIL); payer + receiver != 1
                      before expiry (WARN).
  swaption_expiry     a 1m-expiry clone raising or going NaN on/after its expiry date, or
                      ExpiryInYears != 0 after it (FAIL); vega != 0 after it (WARN).

BOND PACK (Bond; direction buy_sell, size `size`; docs/v2/BOND_DESIGN.md section 3)
  bond_buy_sell_fold  Sell, negative size, or Sell with negative size not folded into one signed
                      face, on d1 and on a later date (FinancingToDate is 0 on the trade date):
                      amounts negate, PremiumCents-style per-|face| levels negate, other levels
                      (yield, Clean/DirtyPrice per signed face, durations, repo terms, days) stay
                      equal (FAIL). A RepoRate that differs by direction (a short reverse-repos the
                      bond in) is allowed: RepoRate, RepoHaircut, FinancingToDate, ForwardPrice and
                      Carry are then not folded, and the PASS says so.
  bond_size_linearity amounts not doubling with size, or a level (yield, price per 100, repo rate,
                      DaysToSettlement, ...) moving with size (FAIL).
  bond_dv01_sign      long bond IRDelta >= 0: risk reported per -1bp or receiver-positive (FAIL).
  bond_gamma_sign     long bond IRGammaParallel <= 0 (FAIL; bullet bonds are convex).
  bond_lightning_dv01 LightningDV01 != the IRDelta scalar (WARN > 1%, FAIL > 5%).
  bond_yield_unit     IRFwdRate (the yield) outside the band of its declared unit (FAIL), or
                      under 20 when declared bp (WARN: percent?).
  bond_price_yield    Price net of carry rising with the yield across d1..d2 (FAIL).
  bond_cashflows_bound  Price outside (0, sum of future flows) for a long bond at a positive
                      yield (FAIL): Cashflows missing flows, or Price per 100 vs per face.
  bond_expiry         ExpiryInYears not pointing at the maturity (WARN; INFO if no date to compare).
  bond_clean_dirty    CleanPrice + 100 AccruedInterest / face != DirtyPrice, or DirtyPrice x face /
                      100 != Price, on both directions (signed face): an accrued added, an unsigned
                      face, a price per face (FAIL); a day of accrual off (WARN).
  bond_fair_premium   FairPremium != Price (a bond's Price is already the settlement value): the
                      swap text Price / DF(settlement) (WARN), a sign flip or the clean value (FAIL).
  bond_premium_cents  PremiumCents != Price / |face| (and sign(face) x DirtyPrice) in its unit,
                      both directions: a price per face declared pct, a signed face (FAIL).
  bond_duration       ModifiedDuration vs -1e4 IRDelta / Price: opposite sign or > 5% apart (FAIL),
                      > 0.5% (WARN); and vs the near step's own price change in the yield (WARN
                      when > 20% apart).
  bond_convexity      Convexity <= 0 for a long bullet bond, or > 10% from 1e8 IRGammaParallel /
                      Price (half of it, per 100bp^2) (FAIL); > 1% (WARN).
  bond_settlement     DaysToSettlement not a whole number of calendar days in [0, 7], 1 on a Friday
                      when the weekdays read 1 (business days), or the settlement_date attribute !=
                      trade date + DaysToSettlement (FAIL); outside 1..4, not T+1 (WARN).
  bond_accrued_over_coupon  AccruedInterest not resetting across the first coupon's drop date
                      (accrued to the pricing date, not settlement) or not holder-signed (FAIL);
                      not ~100% before / ~0% after (WARN).
  bond_repo           RepoRate non-finite or outside its unit's band, RepoHaircut outside [0, 1)
                      (a percent declared decimal) (FAIL); a bp rate under 0.2 (WARN).
  bond_financing      FinancingToDate != 0 on the trade date, a long receiving / a short paying at a
                      positive repo rate (FAIL); a step's change != -(1 - h) Price(trade) x RepoRate x
                      SETTLEMENT days / basis (360 or 365, reported) on the first step and on a
                      Thursday -> Friday-like step: trade-date days, the haircut ignored (FAIL).
  bond_forward_parity ForwardPrice != Price (1 + r tau(s, H)) - coupons in (s, H] (1 + r tau(c, H)),
                      H = settlement + 1 month on the following business day of the bond's calendar
                      (a weekday with a market), c a coupon's payment date (its drop date's
                      settlement: a weekend coupon is paid on Monday), on the priced date and on a
                      date whose horizon holds a coupon: a coupon kept, a sign flip, Price / DF (FAIL
                      beyond 1%, WARN within; PASS within one day of repo interest on |Price|).
  bond_carry_roll     Carry != (Price - AI) - (parity forward - AI at H) (the accrued at H the
                      config's AccruedInterest on the trade date settling on H; ACT/ACT from the
                      Cashflows accrual columns when none has a market; INFO without either), or a
                      short's Carry / RollDown not the long's negated, or Carry + RollDown
                      non-finite (FAIL); the same one-day floor.
  bond_holding_cash   GenericEngine on a short across the first coupon drop date (pricebt DEV-E22):
                      backtest.holding_cash != the config's dropped Cashflows + FinancingToDate
                      change on a mark, Total change != Price change + coupons + financing over the
                      run, or a step whose Price change + booked coupon, net of its Theta and yield
                      move, is over half a coupon (a Cashflows payment_date that is not the drop
                      date) (FAIL).
"""
from __future__ import annotations

import dataclasses
import math
import re
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pandas as pd

import check_asset as ca
from check_asset import FAIL, INFO, PASS, SKIP, WARN, CheckResult, _bday, _fmt

PACKS = ("IRSwap", "IRSwaption", "Bond")
# the direction kwarg of each class and its two values (first = the long / holder-positive side)
DIRECTION = {"IRSwap": ("pay_or_receive", "Pay", "Receive"), "IRSwaption": ("buy_sell", "Buy", "Sell"), "Bond": ("buy_sell", "Buy", "Sell")}
TO_BP = {"bp": 1.0, "pct": 100.0, "decimal": 1e4}
RELATIVE_NAMES = frozenset({"market_to", "pricebt_to_date"})
FD_PROBE = {"bump_size": (1.0, 10.0), "scale_factor": (1.0, 2.0), "finite_difference_method": ("Centered", "Up"), "local_curve": (False, True)}
_POINT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([DWMY])\s*;\s*(\d+(?:\.\d+)?)\s*([DWMY])\s*$", re.I)
_YEARS = {"D": 1 / 365.0, "W": 7 / 365.0, "M": 1 / 12.0, "Y": 1.0}

# bands (see the module docstring and SKILL.md "How to read the IR rows")
TAYLOR_WARN, TAYLOR_FAIL = 0.05, 0.20
THETA_PASS = (0.8, 1.25)
THETA_PER_YEAR = (150.0, 1000.0)
GAMMA_PASS, GAMMA_HALF, GAMMA_DOUBLE = (0.7, 1.4), (0.4, 0.6), (1.6, 2.5)
GAMMA_MIN_MOVE_BP = 0.5
GAMMA_STEPS = 20  # business-day steps the delta slope is fitted over
VOL_BP_BAND = (5.0, 1000.0)  # normal vol in bp: WARN under 5 (pct?), FAIL above 1000 or under 0.2 (decimal?)


# ------------------------------------------------------------------------------------ plumbing


def select_pack(cfg, pack: Optional[str]) -> Optional[str]:
    """The contract/pack class for this run: auto -> the config's own class if it has a pack."""
    if pack in (None, "auto"):
        return cfg.instrument if cfg.instrument in PACKS else None
    if pack == "none":
        return None
    if pack not in PACKS:
        raise ValueError(f"pack {pack!r}: use auto, none or one of {list(PACKS)}")
    return pack


def _risk_obj(key: str):
    """A `pricebt.risk` measure INSTANCE for this key, else None (custom names; classes such as
    PnlExplain, which need a target)."""
    from pricebt.risk import RiskMeasure

    m = ca._risk(key)
    return m if isinstance(m, RiskMeasure) else None


def _spec(cfg, fname: str):
    return cfg.functions.get(fname) or cfg.portfolio_functions[fname]


_AMOUNT_UNITS = frozenset({"ccy", "ccy_per_bp", "ccy_per_bp2"})
# gs Cashflows columns (contracts.FRAME_COLUMNS + the optional ones): amounts vs levels/labels
_CASHFLOW_AMOUNT_COLS = frozenset({"payment_amount", "notional"})
_CASHFLOW_LEVEL_COLS = frozenset({
    "payment_date", "currency", "payment_type", "set_date", "accrual_start_date", "accrual_end_date", "floating_rate_option",
    "floating_rate_designated_maturity", "day_count_fraction", "spread", "rate", "discount_factor",
})
_LEVEL_KINDS = frozenset({"rate", "vol", "time", "prob", "days"})
# notional_level measures per SIGNED face (BOND_DESIGN section 3): the same for a long and a short
_PER_SIGNED_FACE = frozenset({"CleanPrice", "DirtyPrice"})


def _scaling(key: str, spec, inst: Optional[str] = None) -> str:
    """How a scalar measure moves with the position: 'amount' (negates with direction, scales with
    size), 'per_face' (a notional_level per |notional|, e.g. PremiumCents: negates with direction,
    never scales) or 'level' (neither: rates, vols, times, days, probabilities, a bond's clean and
    dirty price per signed face). A contract measure goes by its kind; anything else by unit."""
    from pricebt.risk import contracts

    base = contracts.base_measure(key)[0]
    kind = next((r.kind for r in contracts.contract_for(inst or "") if r.measure == base), None)
    if kind is None or kind == "table":
        return "amount" if spec.unit in _AMOUNT_UNITS else "level"
    if kind == "notional_level":
        return "level" if base in _PER_SIGNED_FACE else "per_face"
    return "level" if kind in _LEVEL_KINDS else "amount"


def _names(code) -> set:
    """Every name an expression reads, nested scopes included (pricebt's own discovery does the same)."""
    return set(code.co_names).union(*(_names(c) for c in code.co_consts if hasattr(c, "co_names")))


def _is_relative_fn(cfg, fname: str) -> bool:
    return bool(RELATIVE_NAMES & _names(cfg.code(fname)))


def generic_view(ctx):
    """`ctx` with frame functions, relative (two-market) functions and the measures mapped to them
    or to a measure class (PnlExplain) removed, for the generic check_asset rows, which evaluate
    one number per function. Identical to `ctx` for a config without either."""
    cfg = ctx.cfg
    drop = {f for f, s in cfg.functions.items() if s.returns == "frame"} | {f for f in cfg.portfolio_functions if _is_relative_fn(cfg, f)}
    rms = {k: m for k, m in cfg.risk_measures.items() if not isinstance(ca._risk(k), type) and not ({m.scalar, m.bucketed} & drop)}
    if not drop and len(rms) == len(cfg.risk_measures):
        return ctx
    view = dataclasses.replace(
        cfg,
        functions={f: s for f, s in cfg.functions.items() if f not in drop},
        portfolio_functions={f: s for f, s in cfg.portfolio_functions.items() if f not in drop},
        risk_measures=rms,
    )
    return dataclasses.replace(ctx, cfg=view)


def with_ir_checks(checks: Sequence[tuple], ctx, pack: Optional[str], swap_pack: Callable) -> List[tuple]:
    """check_asset's generic `(name, fn)` list, run on `generic_view(ctx)`, plus the shape rows,
    and -- when a pack applies -- the contract rows, the IR semantics rows, the FD-parameter probe
    and the pack itself (IRSwap: check_asset.swap_pack)."""
    view = generic_view(ctx)
    out = [(name, (lambda f: (lambda _ctx: f(view)))(fn)) for name, fn in checks]
    out.append(("shapes", check_shapes))
    inst = select_pack(ctx.cfg, pack)
    if inst is None:
        return out
    out += [
        ("contract", lambda c: check_contract(c, inst)),
        ("ir_fake_constant", lambda c: check_fake_constants(c, inst)),
        ("ir_semantics", lambda c: check_ir_semantics(c, inst)),
        ("fd_params", check_fd_params),
        {"IRSwap": ("swap_pack", swap_pack), "IRSwaption": ("swaption_pack", swaption_pack), "Bond": ("bond_pack", bond_pack)}[inst],
    ]
    return out


def _result(v):
    from pricebt.risk.results import LazyFuture

    return v.result() if isinstance(v, LazyFuture) else v


def _as_date(x) -> date:
    return pd.Timestamp(x).date()


def _dir_name(v) -> str:
    s = str(getattr(v, "value", v)).strip().lower()
    return {"rec": "receive", "receiver": "receive", "payer": "pay"}.get(s, s)


class _Env:
    """What every IR row needs: the resolved probe trade, clones of it, and measure values."""

    def __init__(self, ctx, inst: str):
        self.ctx, self.inst, self.svc, self.cfg = ctx, inst, ctx.service, ctx.cfg
        self.r1, self.d1, self.d2 = ctx.r1, ctx.d1, ctx.d2
        self._markets: Dict[date, bool] = {}

    def has(self, m: str, form: str = "scalar") -> bool:
        return (m, form) in self.cfg.provided_forms

    def spec(self, m: str, form: str = "scalar"):
        mapping = self.cfg.risk_measures[self.cfg.provided_forms[(m, form)]]
        return _spec(self.cfg, mapping.bucketed if form == "bucketed" else mapping.scalar)

    def val(self, inst, d: date, m: str) -> float:
        return float(self.svc.value(inst, d, ca._scalar_form(ca._risk(m)), None))

    def get(self, inst, d: date, m: str) -> Optional[float]:
        return self.val(inst, d, m) if self.has(m) else None

    def bp(self, inst, d: date, m: str) -> Optional[float]:
        """A rate/vol level converted to bp by its declared unit (None: unmapped or not a rate unit)."""
        if not self.has(m) or self.spec(m).unit not in TO_BP:
            return None
        return self.val(inst, d, m) * TO_BP[self.spec(m).unit]

    def bucketed(self, inst, d: date, m: str) -> pd.DataFrame:
        return _result(self.svc.value(inst, d, ca._risk(m), None))

    def frame(self, inst, d: date, m: str = "Cashflows") -> pd.DataFrame:
        return self.svc.value(inst, d, ca._risk(m), None)

    def resolve(self, d: Optional[date] = None, **kw):
        return self.svc.resolve(self.ctx.inst.clone(**kw), d or self.d1, None)

    def market_on(self, d: date) -> bool:
        if d not in self._markets:
            try:
                self._markets[d] = bool(self.svc.has_market(self.ctx.asset, d, None))
            except Exception:
                self._markets[d] = False
        return self._markets[d]

    def next_market(self, d: date, tries: int = 10) -> Optional[date]:
        """The first business day after `d` with a market."""
        for _ in range(tries):
            d = _bday(d + timedelta(days=1))
            if self.market_on(d):
                return d
        return None

    def scalar_measures(self) -> List[Tuple[str, Any]]:
        """(key, function spec) of every mapped scalar pricebt measure that evaluates to a number."""
        out = []
        for key, mapping in self.cfg.risk_measures.items():
            if mapping.scalar is None or _risk_obj(key) is None:
                continue
            spec = _spec(self.cfg, mapping.scalar)
            if spec.returns == "frame" or _is_relative_fn(self.cfg, mapping.scalar):
                continue
            out.append((key, spec))
        return out

    def direction(self) -> Tuple[str, str, str]:
        """(kwarg, this trade's side, the other side) for the pack class."""
        kwarg, a, b = DIRECTION[self.inst]
        cur = _dir_name(self.ctx.kwargs.get(kwarg, a))
        return (kwarg, a, b) if cur == a.lower() else (kwarg, b, a)


def _run(env: _Env, rows: Sequence[Tuple[str, Callable]]) -> List[CheckResult]:
    out: List[CheckResult] = []
    for name, fn in rows:
        try:
            out.extend(fn(env))
        except Exception as exc:  # a config error becomes a row, never a traceback
            out.append(CheckResult(name, FAIL, f"{type(exc).__name__}: {exc}"))
    return out


def _close(a: float, b: float, rel: float = 1e-6) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=rel * 1e-3 * max(1.0, abs(a), abs(b)))


# ------------------------------------------------------------------------------------ shapes


def check_shapes(ctx) -> List[CheckResult]:
    """Frame functions, relative measures and bucketed/frame quantity scaling: what the generic
    rows skip (they read one number per function)."""
    from pricebt.markets import CloseMarket
    from pricebt.risk import PnlExplain

    cfg, svc, out = ctx.cfg, ctx.service, []
    scaled = ctx.r1.clone(quantity_=-3.0)
    for key, mapping in cfg.risk_measures.items():
        m = ca._risk(key)
        fns = [f for f in (mapping.scalar, mapping.bucketed) if f is not None]
        if isinstance(m, type):
            if not issubclass(m, PnlExplain):
                out.append(CheckResult(f"risk_measures[{key}]", SKIP, f"{key} needs parameters the checker cannot choose"))
                continue
            try:
                df = _result(svc.value(ctx.r1, ctx.d1, PnlExplain(CloseMarket(date=ctx.d2)), None))
                out.append(CheckResult(f"risk_measures[{key}]", PASS, f"PnlExplain({ctx.d1} -> {ctx.d2} close): {len(df)} rows {sorted(set(map(str, df.get('mkt_type', []))))} summing to {_fmt(float(df['value'].sum()))}"))
            except Exception as exc:
                out.append(CheckResult(f"risk_measures[{key}]", FAIL, f"{type(exc).__name__}: {exc}"))
            continue
        if m is None:
            continue
        spec = _spec(cfg, fns[0]) if fns else None
        if mapping.scalar is not None and spec.returns == "frame":
            f1 = svc.value(ctx.r1, ctx.d1, m, None)
            out.append(CheckResult(f"functions_finite[{mapping.scalar}]", PASS, f"frame: {len(f1)} rows, columns {list(f1.columns)}"))
            f1, f3 = svc.value(ctx.r1, ctx.d2, m, None), svc.value(scaled, ctx.d2, m, None)
            bad = []
            for col in f1.columns:
                if not pd.api.types.is_numeric_dtype(f1[col]) or len(f1) != len(f3):
                    continue
                want = -3.0 * f1[col] if (col in spec.scale_columns and spec.unit in _AMOUNT_UNITS) else f1[col]
                if not all(_close(float(a), float(b), 1e-9) for a, b in zip(want, f3[col])):
                    bad.append(col)
            detail = f"{key} ({mapping.scalar}): {len(f1)} rows at q=1 and q=-3 on {ctx.d2}; scale_columns {list(spec.scale_columns)}"
            if key == "Cashflows":  # gs column semantics: amounts scale with the position, levels/dates never
                levels = sorted(set(spec.scale_columns) & _CASHFLOW_LEVEL_COLS)
                unscaled = sorted((_CASHFLOW_AMOUNT_COLS & set(f1.columns)) - set(spec.scale_columns))
                if levels or unscaled:
                    out.append(CheckResult(f"quantity_scaling[{key} frame]", FAIL, detail + (f": {levels} are levels/labels, never scaled" if levels else "") + (f": amount column(s) {unscaled} missing from scale_columns" if unscaled else "")))
                    continue
            if len(f1) != len(f3):
                out.append(CheckResult(f"quantity_scaling[{key} frame]", FAIL, detail + f": row count changed with quantity ({len(f1)} vs {len(f3)})"))
            else:
                note = " (empty: inconclusive)" if not len(f1) else ""
                out.append(CheckResult(f"quantity_scaling[{key} frame]", FAIL if bad else PASS, detail + (f": columns {bad} do not scale as declared (scale_columns x quantity, others unchanged)" if bad else note)))
        from pricebt.risk import RiskMeasureWithFiniteDifferenceParameter as FD

        # a bare FD measure selects the bucketed form; a plain one only when no scalar is mapped
        if mapping.bucketed is not None and (isinstance(m, FD) or mapping.scalar is None):
            bspec = _spec(cfg, mapping.bucketed)
            if _is_relative_fn(cfg, mapping.bucketed):
                continue
            b1, b3 = _result(svc.value(ctx.r1, ctx.d2, m, None)), _result(svc.value(scaled, ctx.d2, m, None))
            factor = -3.0 if bspec.unit in _AMOUNT_UNITS else 1.0  # by unit, as quantity_scaling
            v1, v3 = b1["value"].to_numpy(float), b3["value"].to_numpy(float)
            ok = len(v1) == len(v3) and all(_close(factor * a, b, 1e-9) for a, b in zip(v1, v3))
            detail = f"{key} ({mapping.bucketed}, {bspec.unit}): {len(v1)} buckets summing to {_fmt(v1.sum()) if len(v1) else 0} at q=1, {_fmt(v3.sum()) if len(v3) else 0} at q=-3 on {ctx.d2}, expected x{factor:g}"
            out.append(CheckResult(f"quantity_scaling[{key} bucketed]", PASS if ok else FAIL, detail + ("" if ok else "; a ladder in ccy per bp must be the per-unit ladder times `weights` (fix scale_with_quantity or the unit)") + (" (empty: inconclusive)" if ok and not len(v1) else "")))
    mapped = {f for mp in cfg.risk_measures.values() for f in (mp.scalar, mp.bucketed)}
    for fname in cfg.portfolio_functions:
        if _is_relative_fn(cfg, fname) and fname not in mapped:
            out.append(CheckResult(f"functions_finite[{fname}]", SKIP, "compares two markets (market_to) and no measure maps it"))
    for fname, spec in cfg.functions.items():
        if spec.returns == "frame" and fname not in mapped:
            out.append(CheckResult(f"functions_finite[{fname}]", SKIP, "returns: frame and no measure maps it"))
    return out


# ------------------------------------------------------------------------------------ contract


def check_contract(ctx, inst: str) -> List[CheckResult]:
    """One row per contract measure of `inst`, plus declaration rows. Every class with a contract is
    strict (Bond, IRSwap, IRSwaption; IR_STRICT_CONTRACT R3-0, BOND_DESIGN 4.1): only a mapping
    satisfies a row, so a missing or a declared contract measure (or a declared preset of one) is a
    FAIL, mapped or not."""
    from pricebt.risk import contracts

    reqs = contracts.contract_for(inst)
    if not reqs:
        return [CheckResult("contract", SKIP, f"{inst} has no measure contract")]
    cfg = ctx.cfg

    def summary(fname):
        if fname is None:
            return None
        f = _spec(cfg, fname)
        return contracts.MappedFunction(fname, f.unit, f.scale_with_quantity, f.returns, f.scale_columns)

    mapped = {k: {"scalar": summary(m.scalar), "bucketed": summary(m.bucketed)} for k, m in cfg.risk_measures.items()}
    res = contracts.check(inst, mapped, cfg.unsupported_measures)
    provided = contracts.provided_forms(inst, mapped)
    strict_names = "/".join(sorted(contracts.STRICT_CLASSES))
    out = []
    for req in reqs:
        status, parts = PASS, []
        names = sorted(m for m in cfg.unsupported_measures if contracts.base_measure(m)[0] == req.measure)
        if names:
            status = FAIL
            parts.append(f"declared under unsupported_measures ({', '.join(names)}): {strict_names} configs must map every contract measure; a declaration cannot satisfy it -- map it and delete the declaration")
        for form in req.forms:
            key = provided.get((req.measure, form))
            if key is not None:
                mp = cfg.risk_measures[key]
                fname = mp.bucketed if form == "bucketed" else mp.scalar
                parts.append(f"{form}: {fname} ({_spec(cfg, fname).unit}{'' if key == req.measure else f', via {key}'})")
            else:
                status = FAIL
                parts.append(f"{form}: not mapped -- {strict_names} require a mapping for every contract measure (`measures.py block <config>` prints the paste-ready mapping skeleton) ({req.doc})")
        slot = [p for p in res.problems if p.startswith(f"{req.measure} (") and "require a mapping for every contract measure" not in p]
        if slot:
            status = FAIL
            parts += slot
        out.append(CheckResult(f"contract[{req.measure}]", status, "; ".join(parts)))
    forbidden = [p for p in res.problems if p.startswith("unsupported_measures declares")]
    out += [CheckResult("contract_declarations", FAIL, p) for p in forbidden]
    out += [CheckResult("contract_declarations", WARN, w) for w in res.warnings]
    if not res.warnings and not forbidden:
        out.append(CheckResult("contract_declarations", PASS, "no stale or misplaced declarations"))
    return out


# ------------------------------------------------------------------------------------ IR semantics


def _cash_between(env: _Env, inst, t0: date, t1: date) -> float:
    """Holder-signed flows Cashflows(t0) lists with t0 < payment_date <= t1 (0 without Cashflows)."""
    if not env.has("Cashflows", "frame"):
        return 0.0
    f = env.frame(inst, t0)
    if not len(f):
        return 0.0
    paid = [float(a) for p, a in zip(f["payment_date"], f["payment_amount"]) if t0 < _as_date(p) <= t1]
    return sum(paid)


def _step(env: _Env, inst, t0: date, t1: date) -> Optional[dict]:
    """The Taylor terms of one step, greeks at t0 (None without IRDelta and a rate-unit IRFwdRate)."""
    r0, D = env.bp(inst, t0, "IRFwdRate"), env.get(inst, t0, "IRDelta")
    if r0 is None or D is None:
        return None
    dr = env.bp(inst, t1, "IRFwdRate") - r0
    s0 = env.bp(inst, t0, "IRAnnualImpliedVol")
    ds = None if s0 is None else env.bp(inst, t1, "IRAnnualImpliedVol") - s0
    terms, missing = {"delta": D * dr}, []
    G = env.get(inst, t0, "IRGammaParallel")
    if G is None:
        missing.append("IRGammaParallel")
    else:
        terms["gamma"] = 0.5 * G * dr * dr
    if ds is not None and ds != 0.0:
        for m, name, f in (("IRVega", "vega", lambda x: x * ds), ("IRVanna", "vanna", lambda x: x * dr * ds), ("IRVolga", "volga", lambda x: 0.5 * x * ds * ds)):
            v = env.get(inst, t0, m)
            if v is None:
                missing.append(m)
            else:
                terms[name] = f(v)
    cash = _cash_between(env, inst, t0, t1)
    economic = env.val(inst, t1, "Price") - env.val(inst, t0, "Price") + cash
    return {"t0": t0, "t1": t1, "days": (t1 - t0).days, "dr": dr, "ds": ds, "delta": D, "vega": env.get(inst, t0, "IRVega"),
            "terms": terms, "theta": env.get(inst, t0, "Theta"), "economic": economic, "cash": cash, "missing": missing}


def _near_step(env: _Env) -> Optional[dict]:
    t1 = env.next_market(env.d2)
    return None if t1 is None or not env.market_on(env.d2) else _step(env, env.r1, env.d2, t1)


def _terms_text(terms: Dict[str, float]) -> str:
    return " + ".join(f"{k} {_fmt(v)}" for k, v in terms.items())


def row_taylor(env: _Env) -> List[CheckResult]:
    s = _near_step(env)
    if s is None:
        return [CheckResult("ir_taylor", SKIP, "needs IRDelta (scalar), IRFwdRate in bp/pct/decimal and a market on d2 and the next business day")]
    terms, missing = dict(s["terms"]), list(s["missing"])
    if s["theta"] is None:
        missing.append("Theta")
    else:
        terms["theta"] = s["theta"] * s["days"]
    resid = s["economic"] - sum(terms.values())
    scale = max(sum(abs(v) for v in terms.values()), abs(s["economic"]), 1e-12)
    ratio = abs(resid) / scale
    status = PASS if ratio <= TAYLOR_WARN else WARN if ratio <= TAYLOR_FAIL else FAIL
    detail = (f"{s['t0']} -> {s['t1']} ({s['days']}d, dr {s['dr']:.3f}bp): Price change + cash {_fmt(s['economic'])} vs {_terms_text(terms)}: "
              f"residual {_fmt(resid)} = {ratio:.1%} of the explained size")
    if missing:
        detail += f"; not in the expansion (declared or unmapped): {missing}"
    if status != PASS:
        detail += "; check the unit, sign and per-bp scale of the largest term, and that Theta is per calendar day"
    return [CheckResult("ir_taylor", status, detail)]


def row_theta(env: _Env) -> List[CheckResult]:
    if not env.has("Theta"):
        return [CheckResult("ir_theta", SKIP, "Theta not mapped")]
    s = _near_step(env)
    if s is None:
        return [CheckResult("ir_theta", SKIP, "needs IRDelta (scalar), IRFwdRate and a market on d2 and the next business day")]
    th, days = s["theta"], s["days"]
    carry = (s["economic"] - sum(s["terms"].values())) / days
    floor = 1e-3 * max(sum(abs(v) for v in s["terms"].values()), abs(s["economic"]), 1e-12)
    head = f"Theta {_fmt(th)}/day vs implied carry {_fmt(carry)}/day ({s['t0']} -> {s['t1']}, {days}d: Price change + cash minus the {sorted(s['terms'])} terms)"
    if abs(th) * days < floor and abs(carry) * days < floor:
        return [CheckResult("ir_theta", PASS, head + ": both ~0 (inconclusive: e.g. an at-the-money swap on a translated curve)")]
    ratio = th / carry if carry else math.inf
    bound = 20.0 * (abs(s["delta"]) + abs(s["vega"] or 0.0)) + 1e-9
    if THETA_PER_YEAR[0] <= abs(ratio) <= THETA_PER_YEAR[1]:
        return [CheckResult("ir_theta", FAIL, head + f": ratio {ratio:.1f} looks per year (a per-year IRTheta is 365 x Theta; Theta is ccy per calendar day, DEV-I15)")]
    if THETA_PASS[0] <= ratio <= THETA_PASS[1]:
        status, note = PASS, f": ratio {ratio:.3f}"
    else:
        status, note = WARN, (f": ratio {ratio:.3g}; check Theta per calendar day, on the curve translated (forwards fixed) not rolled, with the paid-flow cash"
                              " term -- OR another term is wrong: the implied carry is the Price change minus every other term, so an IRDelta that"
                              " is not the total own-rate derivative (an annuity pv01 off-market), a wrong gamma or a wrong vega lands here too")
    if abs(th) > bound:
        status, note = WARN, note + f"; |Theta| > 20 x (|IRDelta| + |IRVega|) = {_fmt(bound)} per day"
    return [CheckResult("ir_theta", status, head + note)]


def row_gamma_ratio(env: _Env) -> List[CheckResult]:
    """IRGammaParallel vs the delta slope fitted over up to GAMMA_STEPS consecutive business days
    from d1: d(IRDelta)_i - vanna*dsigma_i = slope*dr_i + charm*days_i (least squares, no
    intercept), so a delta that drifts with time (a bond's charm) does not bias the slope. Costs
    GAMMA_STEPS + 1 evaluations of IRDelta, IRGammaParallel and IRFwdRate (a bump recipe's IRDelta
    is 2-3 PVs each)."""
    import numpy as np

    name = "ir_gamma_ratio"
    if not (env.has("IRGammaParallel") and env.has("IRDelta") and env.bp(env.r1, env.d1, "IRFwdRate") is not None):
        return [CheckResult(name, SKIP, "needs IRGammaParallel, IRDelta (scalar) and IRFwdRate in bp/pct/decimal")]
    dates = [env.d1] if env.market_on(env.d1) else []
    while dates and len(dates) <= GAMMA_STEPS:
        nxt = env.next_market(dates[-1])
        if nxt is None:
            break
        dates.append(nxt)
    if len(dates) < 4:
        return [CheckResult(name, SKIP, f"fewer than 3 consecutive market steps from {env.d1}")]
    rates = np.array([env.bp(env.r1, d, "IRFwdRate") for d in dates])
    dr = np.diff(rates)
    if np.abs(dr).max() < GAMMA_MIN_MOVE_BP:
        return [CheckResult(name, SKIP, f"IRFwdRate moved at most {np.abs(dr).max():.2f}bp in a day over {dates[0]}..{dates[-1]}: inconclusive (pass a --date on a day rates moved)")]
    d_delta = np.diff([env.val(env.r1, d, "IRDelta") for d in dates])
    corr = ""
    if env.has("IRVanna") and env.bp(env.r1, env.d1, "IRAnnualImpliedVol") is not None:
        vanna = np.array([env.val(env.r1, d, "IRVanna") for d in dates])
        d_delta = d_delta - 0.5 * (vanna[:-1] + vanna[1:]) * np.diff([env.bp(env.r1, d, "IRAnnualImpliedVol") for d in dates])
        corr = ", vanna x dsigma removed"
    # a bond's Price is a settlement-date value: its delta drifts per SETTLEMENT day (a Friday step moves
    # settlement by one business day, not three calendar days), so the drift regressor counts those
    clock = (lambda d: _settle(env, env.r1, d)) if env.inst == "Bond" and env.has("DaysToSettlement") else (lambda d: d)
    days = np.array([(clock(b) - clock(a)).days for a, b in zip(dates, dates[1:])], dtype=float)
    (slope, charm), *_ = np.linalg.lstsq(np.column_stack([dr, days]), d_delta, rcond=None)
    ref_name, annuity_note = "d(IRDelta)/dr", ""
    off_market = env.d2 != env.d1 and abs(env.bp(env.r1, env.d2, "IRFwdRate") - rates[0]) >= 1.0
    if env.has("Annuity") and off_market:
        a2 = abs(env.val(env.r1, env.d2, "Annuity")) * 1e-4  # abs: the fixed-annuity test must not hang on Annuity's sign
        if a2 and math.isclose(abs(env.val(env.r1, env.d2, "IRDelta")), a2, rel_tol=1e-6):
            slope, ref_name = 2.0 * slope, "2 x d(IRDelta)/dr (|IRDelta| = |Annuity| x 1e-4 off-market: a fixed-annuity pv01, ATM-exact, R2-1)"
    elif env.inst == "IRSwap":
        annuity_note = ("unverifiable: " + ("map Annuity" if not env.has("Annuity") else "the par rate moved < 1bp between d1 and d2, pass --date d2 further out")
                        + " -- if IRDelta is a fixed-annuity pv01, d(pv01)/dr is half the gamma, so a half gamma also reads ~1 and only"
                        " |IRDelta| = |Annuity| x 1e-4 off-market tells the two apart (Annuity = -dPV/dK: two PVs with the fixed rate moved +-1bp)")
    gamma = float(np.mean([env.val(env.r1, d, "IRGammaParallel") for d in dates]))
    q = gamma / slope if slope else math.inf
    detail = (f"IRGammaParallel {_fmt(gamma)} (mean) vs {ref_name} {_fmt(slope)} fitted over {len(dr)} steps {dates[0]} -> {dates[-1]}"
              f" (largest move {np.abs(dr).max():.2f}bp, delta drift {_fmt(charm)}/day removed{corr}): ratio {q:.3f}")
    if GAMMA_HALF[0] <= q <= GAMMA_HALF[1]:
        return [CheckResult(name, FAIL, detail + ": the half-gamma trap -- IRGammaParallel is half the second derivative (d(pv01)/dr of an annuity pv01, a Taylor coefficient gamma/2, or a missing chain-rule term); map the full chain-rule d2 Price / dr2 per bp^2")]
    if GAMMA_PASS[0] <= q <= GAMMA_PASS[1]:
        return [CheckResult(name, WARN, detail + "; " + annuity_note)] if annuity_note else [CheckResult(name, PASS, detail)]
    if GAMMA_DOUBLE[0] <= q <= GAMMA_DOUBLE[1]:
        return [CheckResult(name, WARN, detail + ": ~2 -- either IRDelta is a fixed-annuity pv01 (ATM-exact, R2-1) and gamma is right, or gamma is doubled; map Annuity so the checker can tell, or make IRDelta the total own-rate derivative")]
    return [CheckResult(name, WARN, detail + ": outside the bands; noise on small moves -- rerun with --date on a volatile stretch, then compare gamma with a bump in your library")]


def row_expiry(env: _Env) -> List[CheckResult]:
    if not env.has("ExpiryInYears"):
        return [CheckResult("ir_expiry_in_years", SKIP, "ExpiryInYears not mapped")]
    e1, e2 = env.val(env.r1, env.d1, "ExpiryInYears"), env.val(env.r1, env.d2, "ExpiryInYears")
    days = (env.d2 - env.d1).days
    want = max(e1 - days / 365.0, 0.0)
    detail = f"{_fmt(e1)} on {env.d1}, {_fmt(e2)} on {env.d2} ({days} calendar days): expected {_fmt(want)} = max(e1 - {days}/365, 0)"
    if e1 < 0 or not math.isclose(e2, want, rel_tol=0, abs_tol=1e-6):
        return [CheckResult("ir_expiry_in_years", FAIL, detail + "; ExpiryInYears is max(final_or_expiry - t, 0).days / 365 (calendar days, ACT/365F, DEV-I17): not business days, not ACT/365.25, and it must move with the pricing date")]
    return [CheckResult("ir_expiry_in_years", PASS, detail)]


def row_dead_levels(env: _Env) -> List[CheckResult]:
    name = "ir_dead_levels"
    if not env.has("ExpiryInYears"):
        return [CheckResult(name, SKIP, "needs ExpiryInYears to find the final/expiry date")]
    final = env.d1 + timedelta(days=round(env.val(env.r1, env.d1, "ExpiryInYears") * 365.0))
    f0 = final if env.market_on(final) else env.next_market(final)
    f1 = env.next_market(f0 + timedelta(days=6)) if f0 else None
    if f0 is None or f1 is None:
        return [CheckResult(name, SKIP, f"no market on/after the final date {final}: rerun with --kwargs for a trade that ends inside your data")]
    bad = []
    for d in (f0, f1):
        for key, _spec_ in env.scalar_measures():
            try:
                v = env.val(env.r1, d, key)
                if not math.isfinite(v):
                    bad.append(f"{key}={v} on {d}")
            except Exception as exc:
                bad.append(f"{key} on {d}: {type(exc).__name__}: {exc}"[:200])
    if env.has("ExpiryInYears") and not bad and env.val(env.r1, f1, "ExpiryInYears") != 0.0:
        bad.append(f"ExpiryInYears on {f1} is {env.val(env.r1, f1, 'ExpiryInYears')}, expected 0 after the final date")
    if bad:
        return [CheckResult(name, FAIL, f"final/expiry date {final}: " + "; ".join(bad[:6]) + " -- levels must stay finite (last live value), sensitivities 0 or finite, never raise (R2-7)")]
    return [CheckResult(name, PASS, f"final/expiry date {final}: {len(env.scalar_measures())} scalar measures finite on {f0} and {f1}")]


def row_ladder_sums(env: _Env) -> List[CheckResult]:
    out = []
    for bkey, skey in (("IRDelta", "IRDelta"), ("IRGamma", "IRGammaParallel"), ("IRVega", "IRVega")):
        if not (env.has(bkey, "bucketed") and env.has(skey)):
            continue
        df = env.bucketed(env.r1, env.d2, bkey)
        total, s = float(df["value"].sum()) if len(df) else 0.0, env.val(env.r1, env.d2, skey)
        rel = abs(total - s) / max(abs(s), abs(total), 1e-12)
        ok = rel <= 0.05 or max(abs(s), abs(total)) < 1e-9
        detail = f"sum of {len(df)} {bkey} buckets {_fmt(total)} vs {skey} scalar {_fmt(s)} on {env.d2} ({rel:.2%})"
        out.append(CheckResult(f"ir_ladder_sum[{bkey}]", INFO if ok else WARN, detail + ("" if ok else ": an own-rate scalar and a curve ladder may differ by dr/ds (R2-2); a larger gap means missing pillars or another unit/sign")))
    return out


def _years(n: str, u: str) -> float:
    return float(n) * _YEARS[u.upper()]


def row_vega_cube(env: _Env) -> List[CheckResult]:
    name = "ir_vega_cube_keys"
    if not env.has("IRVega", "bucketed"):
        return [CheckResult(name, SKIP, "no bucketed IRVega")]
    df = env.bucketed(env.r1, env.d1, "IRVega")
    if not len(df):
        return [CheckResult(name, PASS, "empty cube (no vol exposure)")]
    points = [str(p) for p in df["mkt_point"]]
    bad = [p for p in points if not _POINT_RE.match(p)]
    if bad:
        return [CheckResult(name, FAIL, f"mkt_point {bad[:5]} is not '<tail>;<expiry>' (gs order, e.g. '10Y;1Y')")]
    notes = []
    types = sorted(set(map(str, df.get("mkt_type", []))))
    if types != ["IR VOL"]:
        notes.append(f"mkt_type {types}, expected ['IR VOL'] (labels.mkt_type)")
    if env.has("ExpiryInYears"):
        e = env.val(env.r1, env.d1, "ExpiryInYears")
        top = points[int(df["value"].abs().to_numpy().argmax())]
        n1, u1, n2, u2 = _POINT_RE.match(top).groups()
        tail, expiry = _years(n1, u1), _years(n2, u2)
        if e > 0 and abs(expiry - e) > abs(tail - e):
            notes.append(f"largest bucket {top!r}: its tail part is nearer ExpiryInYears {e:.2f} than its expiry part -- keys look '<expiry>;<tail>'")
    if notes:
        return [CheckResult(name, WARN, "; ".join(notes))]
    return [CheckResult(name, PASS, f"{len(points)} points, e.g. {points[:3]}")]


def row_cashflows(env: _Env) -> List[CheckResult]:
    name = "ir_cashflows"
    if not env.has("Cashflows", "frame"):
        return [CheckResult(name, SKIP, "Cashflows not mapped")]
    kwarg, cur, other = env.direction()
    base, opp = env.resolve(**{kwarg: cur}), env.resolve(**{kwarg: other})
    fb, fo = env.frame(base, env.d1), env.frame(opp, env.d1)
    if not len(fb) and not len(fo):
        return [CheckResult(name, PASS, "empty for both directions: a total-return Price that never drops a paid flow (R2-6)")]
    paid = [str(_as_date(p)) for p in fb["payment_date"] if _as_date(p) <= env.d1]
    if paid:
        return [CheckResult(name, FAIL, f"lists flows already paid on {env.d1}: {paid[:3]} (Cashflows = the flows Price still includes: payment_date > pricing date)")]
    a = fb.sort_values("payment_date")["payment_amount"].to_numpy(float)
    b = fo.sort_values("payment_date")["payment_amount"].to_numpy(float)
    if len(a) != len(b) or not all(_close(x, -y, 1e-9) for x, y in zip(a, b)):
        return [CheckResult(name, FAIL, f"{kwarg}={cur} amounts {list(a[:3])} vs {kwarg}={other} {list(b[:3])}: payment_amount must be holder-signed (the other direction negates every flow)")]
    return [CheckResult(name, PASS, f"{len(a)} flows after {env.d1}, holder-signed ({kwarg} {cur} vs {other} negates them)")]


def row_cashflow_drop(env: _Env) -> List[CheckResult]:
    name = "ir_cashflow_drop"
    if not env.has("Cashflows", "frame"):
        return [CheckResult(name, SKIP, "Cashflows not mapped")]
    f = env.frame(env.r1, env.d1)
    if not len(f):
        return [CheckResult(name, SKIP, "Cashflows empty on d1: a total-return Price (nothing to drop), or no flow left")]
    p = min(_as_date(x) for x in f["payment_date"])
    ta = _bday(p - timedelta(days=1), -1)
    for _ in range(10):
        if env.market_on(ta):
            break
        ta = _bday(ta - timedelta(days=1), -1)
    tb = p if env.market_on(p) else env.next_market(p)
    if not env.market_on(ta) or tb is None:
        return [CheckResult(name, SKIP, f"no market around the first payment date {p}")]
    got = _drop_residual(env, env.r1, ta, tb)
    if got is None:
        return [CheckResult(name, SKIP, "needs IRDelta, IRFwdRate and a flow in the step")]
    s, terms, resid = got
    flow = s["cash"]
    ratio = abs(resid) / abs(flow)
    status = PASS if ratio <= TAYLOR_WARN else WARN if ratio <= TAYLOR_FAIL else FAIL
    detail = f"{ta} -> {tb} across payment date {p}: Price change {_fmt(s['economic'] - flow)} with flow {_fmt(flow)}; residual after {_terms_text(terms)} = {_fmt(resid)} ({ratio:.1%} of the flow)"
    if status != PASS and env.inst == "IRSwap":
        # A seasoned swap's own par rate (IRFwdRate) jumps on a payment date when the paid period
        # leaves the remaining schedule (a roll, ~N x acc x DF x (par - that period's float rate) in
        # IRDelta x dr, the same for any fixed rate, while the net flow shrinks near par). The market
        # part of the IRFwdRate move is IRFwdRate on tb minus IRFwdRate on tb priced on ta's market (a
        # CloseMarket override: the trade as of tb, no market move); a config that values an override
        # on its own date gives the whole move back, i.e. the single-trade residual above.
        unit = TO_BP[env.spec("IRFwdRate").unit]
        r_b_on_a = float(env.svc.value(env.r1, tb, ca._scalar_form(ca._risk("IRFwdRate")), None, ta)) * unit
        dr_mkt = env.bp(env.r1, tb, "IRFwdRate") - r_b_on_a
        # Theta holds the own par fixed (DEV-I15), taking the roll out at the remaining swap's annuity pv01, so
        # the roll part of the move is explained at that pv01 (Annuity on tb x 1e-4), the market part at IRDelta.
        ann_b = env.get(env.r1, tb, "Annuity")
        if abs(dr_mkt - s["dr"]) > 1e-9 and ann_b is not None:
            roll = s["dr"] - dr_mkt
            terms2 = dict(terms, delta=s["delta"] * dr_mkt, roll=ann_b * 1e-4 * roll)
            if "gamma" in terms2:
                terms2["gamma"] = 0.5 * env.val(env.r1, ta, "IRGammaParallel") * dr_mkt * dr_mkt
            resid2 = s["economic"] - sum(terms2.values())
            ratio = abs(resid2) / abs(flow)
            status = PASS if ratio <= TAYLOR_WARN else WARN if ratio <= TAYLOR_FAIL else FAIL
            detail += (f"; IRFwdRate moved {s['dr']:.3f}bp, {dr_mkt:.3f}bp of it with the market (IRFwdRate on {tb} on the {ta} market: the rest is the paid"
                       f" period rolling off the remaining schedule, not P&L; Theta holds the own par fixed across it): residual after delta"
                       f" {_fmt(terms2['delta'])} on the market move + the roll {roll:.3f}bp x the remaining annuity pv01 {_fmt(ann_b * 1e-4)} = {_fmt(resid2)} ({ratio:.1%} of the flow)")
    if status != PASS:
        detail += ": Price must drop exactly the flows Cashflows lists, on their payment date (R2-6); a total-return Price lists none"
    return [CheckResult(name, status, detail)]



def _drop_residual(env: _Env, inst, ta: date, tb: date):
    """(step, terms, residual) of the Taylor step ta -> tb with theta, or None without a flow in it."""
    s = _step(env, inst, ta, tb)
    if s is None or not s["cash"]:
        return None
    terms = dict(s["terms"])
    if s["theta"] is not None:
        terms["theta"] = s["theta"] * s["days"]
    return s, terms, s["economic"] - sum(terms.values())


def row_swap_annuity_sign(env: _Env) -> List[CheckResult]:
    """IRSwap: Annuity carries the payer-positive signed notional, the sign of IRDelta."""
    name = "swap_annuity_sign"
    if not (env.has("Annuity") and env.has("IRDelta")):
        return [CheckResult(name, SKIP, "needs Annuity and IRDelta (scalar)")]
    vals = {por: (env.val(t, env.d1, "Annuity"), env.val(t, env.d1, "IRDelta"))
            for por in ("Pay", "Receive") for t in [env.resolve(pay_or_receive=por)]}
    shown = ", ".join(f"{por.lower()} Annuity {_fmt(a)} / IRDelta {_fmt(d)}" for por, (a, d) in vals.items())
    if vals["Pay"][0] > 0 > vals["Receive"][0] and all(a * d > 0 for a, d in vals.values()):
        return [CheckResult(name, PASS, shown)]
    return [CheckResult(name, FAIL, shown + ": Annuity is N x A with the payer-positive signed notional (pay-fixed > 0, receive-fixed < 0,"
                                          " the sign of IRDelta); a QuantLib-style fixedLegBPS is the fixed leg's own sign: Annuity = -1e4 x fixedLegBPS")]



# ------------------------------------------------------------------------------------ strict-contract identities (R3-1)
# docs/v2/IR_STRICT_CONTRACT.md R3-1: each row is a known-answer relation between the new measure and
# measures the config already maps; FAIL on a sign or identity break, WARN on a tolerance miss.

NOTIONAL_UNIT = {"bp": 1e4, "pct": 100.0, "decimal": 1.0, "number": 1.0}  # a per-unit-of-notional ratio in each unit
IDENTITY_TOL, IDENTITY_WARN = 1e-6, 0.01
SIGN_FLOOR = 0.001  # |own rate| (decimal) from which a discounting ratio must imply a rate of the same sign


def _identity_status(got: float, want: float, floor: float) -> str:
    """PASS within 1e-6 relative (+ floor), WARN same sign within 1%, else FAIL."""
    gap, scale = abs(got - want), max(abs(got), abs(want))
    if gap <= IDENTITY_TOL * scale + floor:
        return PASS
    return WARN if got * want > 0 and gap <= IDENTITY_WARN * scale else FAIL


SIZE_KWARG = {"Bond": "size"}  # the gs size field per class (else check_asset.SIZE_KWARG, notional_amount)


def _size_kwarg(env: _Env) -> str:
    return SIZE_KWARG.get(env.inst, ca.SIZE_KWARG)


def _notional(env: _Env) -> Optional[float]:
    n = env.ctx.kwargs.get(_size_kwarg(env))
    return abs(float(n)) if isinstance(n, (int, float)) and n else None


def _strike(inst) -> Optional[float]:
    """The resolved fixed rate (swap), strike (swaption) or coupon (bond), DECIMAL, or None."""
    terms = inst.resolved_terms
    k = terms.get("fixed_rate", terms.get("strike", terms.get("coupon")))
    return float(k) if isinstance(k, (int, float)) and not isinstance(k, bool) else None


def _priced_date(env: _Env) -> date:
    """d2 when it has a market (usually off-market, so Price is not ~0), else d1."""
    return env.d2 if env.d2 != env.d1 and env.market_on(env.d2) else env.d1


def _per_notional_row(env: _Env, name: str, measure: str, source: str, why: str) -> List[CheckResult]:
    if not (env.has(measure) and env.has(source)):
        return [CheckResult(name, SKIP, f"needs {measure} and {source}")]
    n = _notional(env)
    if n is None:
        return [CheckResult(name, SKIP, f"no numeric {_size_kwarg(env)} kwarg")]
    unit = env.spec(measure).unit
    factor = NOTIONAL_UNIT.get(unit)
    if factor is None:
        return [CheckResult(name, FAIL, f"{measure} declared {unit!r}; a per-notional level is bp/pct/decimal/number")]
    d = _priced_date(env)
    # both directions, as ir_par_spread does: an unsigned abs(Price) / |N| matches on whichever side has
    # Price > 0 and only the other side shows it
    kwarg, _cur, other = env.direction()
    parts, statuses = [], []
    for side, inst in (("", env.r1), (f"{kwarg}={other}: ", env.resolve(**{kwarg: other}))):
        got, src = env.val(inst, d, measure), env.val(inst, d, source)
        want = src / n * factor
        statuses.append(_identity_status(got, want, 1e-9 * factor))
        parts.append(f"{side}{measure} {_fmt(got)} {unit} vs {source} {_fmt(src)} / |{_size_kwarg(env)}| {_fmt(n)} x {factor:g} = {_fmt(want)}")
    status = FAIL if FAIL in statuses else WARN if WARN in statuses else PASS
    detail = f"on {d}: " + "; ".join(parts)
    return [CheckResult(name, status, detail + ("" if status == PASS else why))]


def row_premium_cents(env: _Env) -> List[CheckResult]:
    return _per_notional_row(env, "ir_premium_cents", "PremiumCents", "Price",
                             ": PremiumCents is Price / |notional_amount| in the declared unit (bp: 1e4 x the ratio, gs's premium in cents);"
                             " a percent-of-notional declared bp, the signed notional, an unsigned abs(Price), or another notional breaks it")


def row_local_annuity(env: _Env) -> List[CheckResult]:
    return _per_notional_row(env, "ir_local_annuity", "LocalAnnuityInCents", "Annuity",
                             ": LocalAnnuityInCents is Annuity / |notional_amount| (decimal: a 10y payer is about +8.5), holder-signed like Annuity;"
                             " an annuity per bp (Annuity x 1e-4), the signed notional, or an unsigned abs(Annuity) breaks it")


def _ratio_head(env: _Env, measure: str, d: date):
    """((value, Price, own rate decimal or None), None) on d, or (None, why it is inconclusive)."""
    pv = env.val(env.r1, d, "Price")
    n = _notional(env) or 1.0
    if abs(pv) < 1e-9 * n:
        return None, f"Price {_fmt(pv)} on {d} is ~0: the ratio is inconclusive (pass a --date when the trade is off-market)"
    r = env.bp(env.r1, d, "IRFwdRate")
    return (env.val(env.r1, d, measure), pv, None if r is None else r / 1e4), None


def row_forward_price(env: _Env) -> List[CheckResult]:
    """ForwardPrice / Price = 1 / DF(expiry): the implied rate ln(ratio) / ExpiryInYears must be near
    the own rate (ForwardPrice = Price x DF is the classic slip)."""
    name = "ir_forward_price"
    if not (env.has("ForwardPrice") and env.has("ExpiryInYears")):
        return [CheckResult(name, SKIP, "needs ForwardPrice and ExpiryInYears")]
    d = _priced_date(env)
    head, why = _ratio_head(env, "ForwardPrice", d)
    if head is None:
        return [CheckResult(name, SKIP, why)]
    fp, pv, r = head
    T = env.val(env.r1, d, "ExpiryInYears")
    detail = f"on {d}: ForwardPrice {_fmt(fp)} vs Price {_fmt(pv)} (ratio {fp / pv:.6g}), ExpiryInYears {T:.4f}"
    if T <= 0:
        ok = _close(fp, pv)
        return [CheckResult(name, PASS if ok else FAIL, detail + ("" if ok else ": on or after expiry ForwardPrice is Price"))]
    if fp / pv <= 0:
        return [CheckResult(name, FAIL, detail + ": opposite sign to Price -- ForwardPrice is Price / DF(expiry), holder-signed like Price")]
    z = math.log(fp / pv) / T
    if r is None:
        return [CheckResult(name, INFO, detail + f": implied rate {z:.4%} (no rate-unit IRFwdRate to compare)")]
    detail += f": implied rate ln(ratio)/T {z:.4%} vs own rate {r:.4%}"
    if abs(r) >= SIGN_FLOOR and z * r < 0:
        # Price x DF implies -r: at low rates it sits inside any relative band, so the sign decides
        return [CheckResult(name, FAIL, detail + ": the implied rate has the opposite sign to the own rate -- ForwardPrice is Price / DF(expiry), never Price x DF")]
    gap = abs(z - r)
    if gap <= max(0.5 * abs(r), 0.0025):
        return [CheckResult(name, PASS, detail)]
    if gap <= max(abs(r), 0.005):
        return [CheckResult(name, WARN, detail + ": the discounting implies a rate far from the own rate -- the right date (a swaption's expiry, a swap's final date)?")]
    return [CheckResult(name, FAIL, detail + ": ForwardPrice is Price / DF(expiry) -- Price x DF, a price in bp, or the wrong date gives this")]


def row_fair_premium(env: _Env) -> List[CheckResult]:
    """FairPremium / Price = 1 / DF(settlement), settlement a few days away (spot lag, or the
    premium payment date): within 10 days of discounting at the own rate."""
    name = "ir_fair_premium"
    if not env.has("FairPremium"):
        return [CheckResult(name, SKIP, "FairPremium not mapped")]
    d = _priced_date(env)
    head, why = _ratio_head(env, "FairPremium", d)
    if head is None:
        return [CheckResult(name, SKIP, why)]
    fp, pv, r = head
    ratio = fp / pv
    detail = f"on {d}: FairPremium {_fmt(fp)} vs Price {_fmt(pv)} (ratio {ratio:.8g})"
    if ratio <= 0:
        return [CheckResult(name, FAIL, detail + ": opposite sign to Price -- FairPremium is Price / DF(settlement), holder-signed")]
    if r is not None and abs(r) >= SIGN_FLOOR and math.log(ratio) * r < -1e-15:
        return [CheckResult(name, FAIL, detail + f": the ratio discounts the wrong way for an own rate of {r:.4%} -- FairPremium is Price / DF(settlement), never Price x DF")]
    bound = max(abs(r or 0.0), 0.01) * 10.0 / 365.0
    if abs(math.log(ratio)) <= bound + 1e-12:
        return [CheckResult(name, PASS, detail + f" (|ln ratio| within {bound:.2e}: at most ~10 days of discounting)")]
    if abs(math.log(ratio)) <= 0.01:
        return [CheckResult(name, WARN, detail + ": more than ~10 days of discounting -- a far premium_payment_date is legitimate; otherwise check the settlement date")]
    return [CheckResult(name, FAIL, detail + ": FairPremium is Price / DF(premium settlement), settlement = spot or the premium payment date -- not the expiry/final date (that is ForwardPrice)")]


def row_par_spread(env: _Env) -> List[CheckResult]:
    """ParSpread = K - IRFwdRate (single curve, matching schedules), the same for both directions."""
    name = "ir_par_spread"
    k = _strike(env.r1)
    d = _priced_date(env)
    ps, f = env.bp(env.r1, d, "ParSpread"), env.bp(env.r1, d, "IRFwdRate")
    if ps is None or f is None or k is None:
        return [CheckResult(name, SKIP, "needs ParSpread and IRFwdRate in bp/pct/decimal and a numeric resolved fixed_rate/strike")]
    kwarg, _cur, other = env.direction()
    want = k * 1e4 - f
    flipped = env.bp(env.resolve(**{kwarg: other}), d, "ParSpread")
    detail = f"on {d}: ParSpread {ps:.4f}bp vs K - IRFwdRate = {k * 1e4:.4f} - {f:.4f} = {want:.4f}bp; {kwarg}={other}: {flipped:.4f}bp"
    if abs(flipped - ps) > 1e-6 * max(1.0, abs(ps)):
        return [CheckResult(name, FAIL, detail + ": ParSpread does not depend on direction (payer and receiver of the same terms share it) -- a holder-signed spread")]
    if abs(want) > 1.0 and ps * want < 0:
        return [CheckResult(name, FAIL, detail + ": the sign is reversed -- ParSpread = K - forward (the spread on the floating leg that makes Price 0), not forward - K")]
    if abs(want) > 1.0 and not 0.5 <= ps / want <= 2.0:
        return [CheckResult(name, FAIL, detail + ": off by a scale factor -- the declared unit (bp/pct/decimal) of ParSpread or IRFwdRate?")]
    gap = ps - want
    if abs(gap) <= max(0.5, 0.01 * abs(want)):
        return [CheckResult(name, PASS, detail)]
    return [CheckResult(name, WARN, detail + f": {gap:+.3f}bp apart -- fine if the legs' schedules, day counts or curves differ (then ParSpread is the floating-leg spread itself), else check the definition")]


def row_compounded_fixed_rate(env: _Env) -> List[CheckResult]:
    """(1 + K/f)^f - 1 lies in [K, e^K - 1] for every f >= 1, is K for an annual leg, and never moves."""
    name = "ir_compounded_fixed_rate"
    k = _strike(env.r1)
    if not env.has("CompoundedFixedRate") or env.spec("CompoundedFixedRate").unit not in TO_BP or k is None:
        return [CheckResult(name, SKIP, "needs CompoundedFixedRate in bp/pct/decimal and a numeric resolved fixed_rate/strike")]
    d2 = _priced_date(env)
    c1 = env.bp(env.r1, env.d1, "CompoundedFixedRate") / 1e4
    c2 = env.bp(env.r1, d2, "CompoundedFixedRate") / 1e4
    hi = math.expm1(k)
    detail = f"CompoundedFixedRate {c1:.8%} on {env.d1}, {c2:.8%} on {d2}; K {k:.8%}, bounds [K, e^K - 1 = {hi:.8%}]"
    if abs(c1 - c2) > 1e-12:
        return [CheckResult(name, FAIL, detail + ": it moved with the pricing date -- a trade term (the fixed rate restated), not a market level")]
    if c1 < k - 1e-12 or c1 > hi + 1e-12:
        return [CheckResult(name, FAIL, detail + ": outside the bounds -- (1 + K/f)^f - 1 for a leg paying f times a year (annual: K); a de-compounded or continuous restatement is below K")]
    f, source = _fixed_frequency(env)
    if f is None:
        return [CheckResult(name, PASS, detail + (": = K, right for an annual fixed leg; the leg frequency is not knowable here (no fixed_rate_frequency, no fixed-leg"
                                                  " Cashflows) -- pass a fixed_rate_frequency kwarg (--kwargs) to check it" if abs(c1 - k) <= 1e-12 else ""))]
    want = (1.0 + k / f) ** f - 1.0
    detail += f"; fixed leg f = {f:g} a year ({source}): (1 + K/f)^f - 1 = {want:.8%}"
    if abs(c1 - want) > 1e-10:
        return [CheckResult(name, FAIL, detail + ": CompoundedFixedRate is the fixed rate compounded at the leg's own frequency -- K as-is is right only for an annual leg")]
    return [CheckResult(name, PASS, detail)]


_FREQ = {"1y": 1, "12m": 1, "annual": 1, "6m": 2, "semiannual": 2, "3m": 4, "quarterly": 4, "1m": 12, "monthly": 12}


def _fixed_frequency(env: _Env) -> Tuple[Optional[float], str]:
    """(fixed-leg payments a year, where it came from), or (None, why not): a fixed_rate_frequency
    kwarg or resolved term (a bond: a numeric resolved `frequency`), else the median spacing of the
    fixed leg's (a bond's coupon) Cashflows payment dates."""
    for where, src in (("kwarg", env.ctx.kwargs), ("resolved term", env.r1.resolved_terms)):
        v = str(src.get("fixed_rate_frequency") or "").strip().lower()
        if v in _FREQ:
            return float(_FREQ[v]), f"{where} fixed_rate_frequency {v!r}"
    f = env.r1.resolved_terms.get("frequency")
    if isinstance(f, (int, float)) and not isinstance(f, bool) and f in (1, 2, 4, 12):
        return float(f), f"resolved term frequency {f!r}"
    if env.has("Cashflows", "frame"):
        f = env.frame(env.r1, env.d1)
        if len(f) and "payment_type" in f:
            dates = sorted({_as_date(p) for p, t in zip(f["payment_date"], f["payment_type"]) if any(w in str(t).lower() for w in ("fixed", "coupon"))})
            gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
            if len(gaps) >= 2:
                n = 365.25 / gaps[len(gaps) // 2]
                for k in (1, 2, 4, 12):
                    if abs(n - k) <= 0.15 * k:
                        return float(k), f"fixed-leg Cashflows every {gaps[len(gaps) // 2]} days"
    return None, "unknown"


def row_crif(env: _Env) -> List[CheckResult]:
    """CRIFIRCurve labels (RiskType, Qualifier, SIMM Label1) and sum(Amount) = sum of the IRDelta ladder."""
    from pricebt.risk import contracts

    name = "ir_crif"
    if not env.has("CRIFIRCurve", "frame"):
        return [CheckResult(name, SKIP, "CRIFIRCurve not mapped")]
    df = env.frame(env.r1, env.d1, "CRIFIRCurve")
    bad = []
    rt = sorted(set(map(str, df["RiskType"])) - {"Risk_IRCurve"})
    if rt:
        bad.append(f"RiskType {rt} (always 'Risk_IRCurve')")
    q = sorted(set(map(str, df["Qualifier"])) - {env.cfg.currency})
    if q:
        bad.append(f"Qualifier {q} (the currency ISO code, {env.cfg.currency!r})")
    labels = sorted({str(x) for x in df["Label1"]} - set(contracts.SIMM_IR_TENORS))
    if labels:
        bad.append(f"Label1 {labels} not SIMM tenors {list(contracts.SIMM_IR_TENORS)} (lower case: '10y', not '10Y')")
    buckets = sorted({repr(x) for x in df["Bucket"] if not (isinstance(x, str) and x in contracts.SIMM_IR_BUCKETS)})
    if buckets:
        bad.append(f"Bucket {buckets} not a SIMM volatility group string {list(contracts.SIMM_IR_BUCKETS)} ('1' for USD, EUR)")
    sub = sorted({repr(x) for x in df["Label2"] if x not in contracts.SIMM_IR_SUBCURVES})
    if sub:
        bad.append(f"Label2 {sub} not a SIMM sub-curve {list(contracts.SIMM_IR_SUBCURVES)} (a SOFR or ESTR curve is 'OIS')")
    ccy = sorted({f"{a!r} for {q!r}" for a, q in zip(df["AmountCurrency"], df["Qualifier"]) if str(a) != str(q)})
    if ccy:
        bad.append(f"AmountCurrency {ccy} not the Qualifier's currency")
    total = float(pd.to_numeric(df["Amount"]).sum()) if len(df) else 0.0
    head = f"{len(df)} rows on {env.d1}, sum(Amount) {_fmt(total)}"
    if bad:
        return [CheckResult(name, FAIL, head + ": " + "; ".join(bad))]
    if not env.has("IRDelta", "bucketed"):
        return [CheckResult(name, PASS, head + "; labels valid (no IRDelta ladder to compare the sum)")]
    lad = env.bucketed(env.r1, env.d1, "IRDelta")
    ladder = float(lad["value"].sum()) if len(lad) else 0.0
    status = _identity_status(total, ladder, 1e-9 * max(1.0, abs(ladder)))
    detail = head + f" vs sum of the IRDelta ladder {_fmt(ladder)}"
    return [CheckResult(name, status, detail + ("" if status == PASS else ": sum(Amount) must equal the IRDelta bucketed ladder (the same ccy per +1bp, holder-signed, per unit trade) -- a sign, a unit or a missing pillar"))]


STRICT_ROWS = (
    ("ir_premium_cents", row_premium_cents),
    ("ir_local_annuity", row_local_annuity),
    ("ir_forward_price", row_forward_price),
    ("ir_fair_premium", row_fair_premium),
    ("ir_par_spread", row_par_spread),
    ("ir_compounded_fixed_rate", row_compounded_fixed_rate),
    ("ir_crif", row_crif),
)
# a Bond's ForwardPrice, FairPremium, PremiumCents and ParSpread have Bond texts (BOND_DESIGN section 3):
# the bond pack checks them (bond_forward_parity, bond_fair_premium, bond_premium_cents); these share the swap identity
BOND_IDENTITY_ROWS = (
    ("ir_local_annuity", row_local_annuity),
    ("ir_compounded_fixed_rate", row_compounded_fixed_rate),
    ("ir_crif", row_crif),
)


def check_fake_constants(ctx, inst: str) -> List[CheckResult]:
    """R3-0: a contract measure mapped to a literal constant ('0.0', '{}', float('nan'), math.nan) is
    honest only where the contract defines the value as 0 (contracts.ZERO_BY_CONVENTION), and there only
    when the constant IS 0 (or {}); anything else is a placeholder."""

    from pricebt.risk import contracts

    name = "ir_fake_constant"
    if not contracts.is_strict(inst):
        return [CheckResult(name, SKIP, f"{inst} has no measure contract")]
    allowed = contracts.ZERO_BY_CONVENTION.get(inst, {})  # {measure: why the contract defines it as 0}
    names = {r.measure for r in contracts.contract_for(inst)}
    bad, fine = [], []
    for key, mapping in ctx.cfg.risk_measures.items():
        base = contracts.base_measure(key)[0]
        if base not in names:
            continue
        for fname in filter(None, (mapping.scalar, mapping.bucketed)):
            expr = _spec(ctx.cfg, fname).expr.strip()
            found, value = _constant(expr)
            if not found:
                continue
            zero = value == {} or (isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0)
            if base in allowed and zero:
                fine.append(f"{key} -> {fname} ({allowed[base]})")
            else:
                bad.append(f"{key} -> {fname} = {expr!r}")
    if bad:
        return [CheckResult(name, FAIL, f"literal constant for {bad}: only {sorted(allowed)} may be a constant for {inst}, and only 0 (or {{}}: the contract defines"
                                        " them as 0); every other contract measure must be computed -- a placeholder silently zeroes (or NaNs) P&L and risk")]
    return [CheckResult(name, PASS, f"no placeholder constants; zero by convention: {fine or 'none'}")]


def _constant(expr: str) -> Tuple[bool, Any]:
    """(True, value) when expr is a constant: a Python literal, float('nan'/'inf'/...), math/np/numpy
    .nan/.inf/.pi/.e, optionally negated; else (False, None)."""
    import ast

    try:
        return True, ast.literal_eval(expr)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        pass
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return False, None
    sign = 1.0
    while isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign, node = (-sign if isinstance(node.op, ast.USub) else sign), node.operand
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float" and len(node.args) == 1
            and not node.keywords and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, (str, int, float))):
        try:
            return True, sign * float(node.args[0].value)
        except ValueError:
            return False, None
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in ("math", "np", "numpy")
            and node.attr.lower() in ("nan", "inf", "pi", "e")):
        return True, sign * getattr(math, node.attr.lower())
    return False, None


def check_ir_semantics(ctx, inst: str) -> List[CheckResult]:
    from pricebt.risk import contracts

    env = _Env(ctx, inst)
    return _run(env, (
        ("ir_expiry_in_years", row_expiry),
        ("ir_taylor", row_taylor),
        ("ir_theta", row_theta),
        ("ir_gamma_ratio", row_gamma_ratio),
        ("ir_dead_levels", row_dead_levels),
        ("ir_ladder_sum", row_ladder_sums),
        ("ir_vega_cube_keys", row_vega_cube),
        ("ir_cashflows", row_cashflows),
        ("ir_cashflow_drop", row_cashflow_drop),
    ) + ((("swap_annuity_sign", row_swap_annuity_sign),) if inst == "IRSwap" else ())
      + (BOND_IDENTITY_ROWS if inst == "Bond" else STRICT_ROWS if contracts.is_strict(inst) else ()))


# ------------------------------------------------------------------------------------ FD parameters


def check_fd_params(ctx) -> List[CheckResult]:
    """DEV-I10 probe on every mapped finite-difference measure's scalar function."""
    from pricebt.errors import NotSupportedError
    from pricebt.risk import RiskMeasureWithFiniteDifferenceParameter as FD

    cfg, svc, out, refused = ctx.cfg, ctx.service, [], []
    for key, mapping in cfg.risk_measures.items():
        m = _risk_obj(key)
        if not isinstance(m, FD) or mapping.scalar is None:
            continue
        names = _names(cfg.code(mapping.scalar))
        used = [p for p in FD_PROBE if f"pricebt_{p}" in names]
        if not used:
            try:
                svc.value(ctx.r1, ctx.d1, m(aggregation_level="Type", bump_size=1.0), None)
            except NotSupportedError:
                refused.append(key)
                continue
            out.append(CheckResult(f"fd_params[{key}]", FAIL, f"{mapping.scalar} does not name pricebt_bump_size, yet bump_size=1 did not raise NotSupportedError: silently ignored"))
            continue
        for p in used:
            a, b = FD_PROBE[p]
            va = float(svc.value(ctx.r1, ctx.d1, m(aggregation_level="Type", **{p: a}), None))
            vb = float(svc.value(ctx.r1, ctx.d1, m(aggregation_level="Type", **{p: b}), None))
            detail = f"{mapping.scalar} names pricebt_{p}: {p}={a!r} -> {_fmt(va)}, {p}={b!r} -> {_fmt(vb)} (difference {_fmt(vb - va)})"
            out.append(CheckResult(f"fd_params[{key}]", WARN if va == vb else INFO, detail + (": identical -- is the parameter reaching your library's bump?" if va == vb else "")))
    if refused:
        out.insert(0, CheckResult("fd_params", PASS, f"{refused}: bump_size refused with NotSupportedError, as documented (their functions name no pricebt_<parameter>, DEV-I10)"))
    return out or [CheckResult("fd_params", SKIP, "no finite-difference measure mapped")]


# ------------------------------------------------------------------------------------ swaption pack


# a Bond's measures that a direction-dependent repo rate moves: not folded when RepoRate differs by side
DIRECTION_REPO = frozenset({"RepoRate", "RepoHaircut", "FinancingToDate", "ForwardPrice", "Carry"})


def _fold_mismatches(env: _Env, a, b, sign: float, d: date, size: float = 1.0, skip=frozenset()) -> List[str]:
    """Measures (but `skip`) where b is not a with the direction flipped by `sign` and the size
    scaled by `size` (see _scaling: amounts x size x sign, per-face levels x sign, levels unchanged)."""
    bad = []
    for key, spec in env.scalar_measures():
        if key in skip:
            continue
        va, vb = env.val(a, d, key), env.val(b, d, key)
        want = {"amount": size * sign, "per_face": sign, "level": 1.0}[_scaling(key, spec, env.inst)] * va
        if not _close(vb, want):
            bad.append(f"{key} {_fmt(va)} vs {_fmt(vb)}")
    return bad


def row_swaption_fold(env: _Env) -> List[CheckResult]:
    buy, sell = env.resolve(buy_sell="Buy"), env.resolve(buy_sell="Sell")
    bad = _fold_mismatches(env, buy, sell, -1.0, env.d1)
    if bad:
        return [CheckResult("swaption_buy_sell_fold", FAIL, f"Buy vs Sell on {env.d1}: {bad[:5]} -- fold buy_sell (x sign of notional_amount) into one signed notional in resolve; pricebt never reads buy_sell")]
    return [CheckResult("swaption_buy_sell_fold", PASS, f"Sell = -Buy for every extensive measure, levels equal ({len(env.scalar_measures())} measures)")]


def row_swaption_straddle(env: _Env) -> List[CheckResult]:
    name = "swaption_straddle"
    try:
        st = env.resolve(pay_or_receive="Straddle")
    except Exception as exc:
        return [CheckResult(name, PASS, f"Straddle rejected at resolve: {type(exc).__name__}: {str(exc)[:150]}")]
    payer, recv = env.resolve(pay_or_receive="Pay"), env.resolve(pay_or_receive="Receive")
    bad = []
    for key in [k for k in ("Price", "IRDelta", "IRVega", "IRGammaParallel") if env.has(k)]:
        try:
            vs = env.val(st, env.d1, key)
        except Exception as exc:
            return [CheckResult(name, FAIL, f"Straddle resolves but {key} fails: {type(exc).__name__}: {exc} -- reject Straddle in resolve if the library cannot price it")]
        vp, vr = env.val(payer, env.d1, key), env.val(recv, env.d1, key)
        if not _close(vs, vp + vr):
            bad.append(f"{key}: straddle {_fmt(vs)} vs payer + receiver {_fmt(vp + vr)}")
    if bad:
        return [CheckResult(name, FAIL, "; ".join(bad))]
    return [CheckResult(name, PASS, "Straddle = Payer + Receiver (Price and mapped greeks)")]


def row_swaption_strike(env: _Env) -> List[CheckResult]:
    name = "swaption_strike_pinned"
    ks, warns = {}, []
    for s in ("ATM", "A-50", "ATM+25"):
        try:
            k = env.resolve(strike=s).resolved_terms.get("strike")
        except Exception as exc:
            if s == "ATM":
                return [CheckResult(name, FAIL, f"strike='ATM' fails at resolve: {type(exc).__name__}: {exc}")]
            warns.append(f"{s!r} rejected at resolve ({type(exc).__name__})")
            continue
        if k is None:
            return [CheckResult(name, SKIP, "no resolved 'strike' term")]
        if not isinstance(k, (int, float)):
            return [CheckResult(name, FAIL, f"strike={s!r} resolves to {k!r}: pin every strike to a number (decimal) in resolve, or it is re-read on every pricing date")]
        ks[s] = float(k)
    offs = {s: (ks[s] - ks["ATM"]) * 1e4 for s in ks if s != "ATM"}
    want = {"A-50": -50.0, "ATM+25": 25.0}
    off_bad = {s: v for s, v in offs.items() if abs(v - want[s]) > 0.01}
    if off_bad:
        return [CheckResult(name, FAIL, f"resolved strikes {ks}: offsets from ATM {offs} bp, expected {want}: the strike must be a decimal and 'A-50'/'ATM+25' offsets are bp")]
    detail = f"resolved strikes {({s: round(v, 8) for s, v in ks.items()})}"
    return [CheckResult(name, WARN if warns else PASS, detail + ("; " + "; ".join(warns) if warns else ""))]


def row_swaption_parity(env: _Env) -> List[CheckResult]:
    name = "swaption_parity"
    if not (env.has("Annuity") and env.bp(env.r1, env.d1, "IRFwdRate") is not None):
        return [CheckResult(name, SKIP, "needs Annuity and IRFwdRate")]
    payer, recv = env.resolve(pay_or_receive="Pay"), env.resolve(pay_or_receive="Receive")
    k = payer.resolved_terms.get("strike")
    if not isinstance(k, (int, float)):
        return [CheckResult(name, SKIP, "no numeric resolved 'strike'")]
    d = env.d2 if env.market_on(env.d2) else env.d1
    if env.has("ExpiryInYears") and env.val(payer, d, "ExpiryInYears") <= 0:
        return [CheckResult(name, SKIP, f"expired on {d}")]
    lhs = env.val(payer, d, "Price") - env.val(recv, d, "Price")
    ann = env.val(payer, d, "Annuity")
    f_dec = env.bp(payer, d, "IRFwdRate") / 1e4
    rhs = ann * (f_dec - float(k))
    err_bp = abs(lhs - rhs) / max(abs(ann) * 1e-4, 1e-12)
    status = PASS if err_bp <= 0.01 else WARN if err_bp <= 1.0 else FAIL
    detail = f"on {d}: payer - receiver {_fmt(lhs)} vs Annuity x (F - K) = {_fmt(ann)} x ({f_dec:.6f} - {float(k):.6f}) = {_fmt(rhs)} (gap {err_bp:.3g}bp running)"
    return [CheckResult(name, status, detail + ("" if status == PASS else ": strike not decimal, Annuity not holder-signed N x A, or IRFwdRate not the underlying forward"))]


def _sign_row(env: _Env, name: str, m: str, want: Dict[str, int]) -> List[CheckResult]:
    if not env.has(m):
        return [CheckResult(name, SKIP, f"{m} not mapped")]
    vals = {por: env.val(env.resolve(buy_sell="Buy", pay_or_receive=por), env.d1, m) for por in want}
    bad = {por: v for por, v in vals.items() if not v * want[por] > 0}
    shown = ", ".join(f"bought {por.lower()} {_fmt(v)}" for por, v in vals.items())
    if bad:
        return [CheckResult(name, FAIL, f"{m}: {shown}; expected " + ", ".join(f"{p.lower()} {'>' if s > 0 else '<'} 0" for p, s in want.items()) + " (holder-signed, per +1bp)")]
    return [CheckResult(name, PASS, f"{m}: {shown}")]


def row_swaption_prob(env: _Env) -> List[CheckResult]:
    name = "swaption_prob_exercise"
    if not env.has("ProbabilityOfExercise"):
        return [CheckResult(name, SKIP, "ProbabilityOfExercise not mapped")]
    p = env.val(env.resolve(pay_or_receive="Pay"), env.d1, "ProbabilityOfExercise")
    r = env.val(env.resolve(pay_or_receive="Receive"), env.d1, "ProbabilityOfExercise")
    if not (0.0 <= p <= 1.0 and 0.0 <= r <= 1.0):
        return [CheckResult(name, FAIL, f"payer {p}, receiver {r}: a probability is in [0, 1] (unit decimal, not percent)")]
    ok = abs(p + r - 1.0) <= 1e-6
    return [CheckResult(name, PASS if ok else WARN, f"payer {p:.6f} + receiver {r:.6f} = {p + r:.6f}" + ("" if ok else ": not 1 at the same strike before expiry"))]


def row_swaption_expiry(env: _Env) -> List[CheckResult]:
    name = "swaption_expiry"
    short = env.resolve(expiration_date="1m")
    ex = short.resolved_terms.get("expiration_date")
    if not isinstance(ex, date):
        return [CheckResult(name, SKIP, f"resolved expiration_date {ex!r} is not a date")]
    f0 = ex if env.market_on(ex) else env.next_market(ex)
    f1 = env.next_market(f0 + timedelta(days=6)) if f0 else None
    if f0 is None or f1 is None:
        return [CheckResult(name, SKIP, f"no market on/after the 1m expiry {ex}")]
    bad = []
    for d in (f0, f1):
        for key, _s in env.scalar_measures():
            try:
                v = env.val(short, d, key)
                if not math.isfinite(v):
                    bad.append(f"{key}={v} on {d}")
            except Exception as exc:
                bad.append(f"{key} on {d}: {type(exc).__name__}: {exc}"[:200])
    if not bad and env.has("ExpiryInYears") and env.val(short, f1, "ExpiryInYears") != 0.0:
        bad.append(f"ExpiryInYears {env.val(short, f1, 'ExpiryInYears')} on {f1}, expected 0")
    if bad:
        return [CheckResult(name, FAIL, f"1m-expiry clone (expiry {ex}): " + "; ".join(bad[:6]) + " -- a held swaption is priced on and after its expiry date: no exception, no NaN (R2-7)")]
    vega = env.get(short, f1, "IRVega")
    if vega:
        return [CheckResult(name, WARN, f"expiry {ex}: IRVega {_fmt(vega)} on {f1} after expiry; an expired (exercised or not) swaption has no vol exposure (R2-7, R2-8)")]
    return [CheckResult(name, PASS, f"1m-expiry clone (expiry {ex}): every scalar measure finite on {f0} and {f1}, ExpiryInYears 0 after")]


def row_swaption_fwd_unit(env: _Env) -> List[CheckResult]:
    """IRFwdRate of an ATM-struck clone on its trade date is its resolved (decimal) strike: exact,
    whatever the declared unit, so a forward in pct or decimal declared bp cannot hide."""
    name = "swaption_fwd_unit"
    if env.bp(env.r1, env.d1, "IRFwdRate") is None:
        return [CheckResult(name, SKIP, "IRFwdRate not mapped in bp/pct/decimal")]
    atm = env.resolve(strike="ATM")
    k = atm.resolved_terms.get("strike")
    unit, f_bp = env.spec("IRFwdRate").unit, env.bp(atm, env.d1, "IRFwdRate")
    if not isinstance(k, (int, float)):
        return [CheckResult(name, SKIP, "no numeric resolved 'strike' for strike='ATM'")]
    detail = f"ATM clone on {env.d1}: IRFwdRate {_fmt(f_bp)}bp (declared {unit}) vs resolved strike x 1e4 = {_fmt(float(k) * 1e4)}bp"
    if abs(f_bp - float(k) * 1e4) > 0.5:
        return [CheckResult(name, FAIL, detail + ": an ATM strike IS the forward on its trade date -- the forward is in another unit than declared (pct or decimal under bp?), or the strike is not decimal")]
    return [CheckResult(name, PASS, detail)]


def row_swaption_vol_unit(env: _Env) -> List[CheckResult]:
    """IRAnnualImpliedVol before expiry, converted to bp by its declared unit, in a normal-vol band."""
    name = "swaption_vol_unit"
    v_bp = env.bp(env.r1, env.d1, "IRAnnualImpliedVol")
    if v_bp is None:
        return [CheckResult(name, SKIP, "IRAnnualImpliedVol not mapped in bp/pct/decimal")]
    unit = env.spec("IRAnnualImpliedVol").unit
    detail = f"IRAnnualImpliedVol {_fmt(v_bp)}bp of normal vol (declared {unit}) on {env.d1}"
    if not 0.2 <= v_bp <= VOL_BP_BAND[1]:
        return [CheckResult(name, FAIL, detail + f": outside ({0.2}, {VOL_BP_BAND[1]})bp -- a decimal vol under bp, a bp vol under pct/decimal, or a lognormal (Black) vol, not the normal vol")]
    if v_bp < VOL_BP_BAND[0]:
        return [CheckResult(name, WARN, detail + f": under {VOL_BP_BAND[0]}bp -- percent (or decimal) returned under a bp declaration? VegaPnL and vanna/volga would be ~100x too small, with the whole vega P&L in the residual")]
    return [CheckResult(name, PASS, detail)]


def swaption_pack(ctx) -> List[CheckResult]:
    """Swaption pack: gs IRSwaption fields buy_sell, pay_or_receive (Pay/Receive/Straddle), strike,
    expiration_date; pricebt conventions: holder-signed, per +1bp of the forward / normal vol."""
    env = _Env(ctx, "IRSwaption")
    return _run(env, (
        ("swaption_buy_sell_fold", row_swaption_fold),
        ("swaption_straddle", row_swaption_straddle),
        ("swaption_strike_pinned", row_swaption_strike),
        ("swaption_parity", row_swaption_parity),
        ("swaption_fwd_unit", row_swaption_fwd_unit),
        ("swaption_vol_unit", row_swaption_vol_unit),
        ("swaption_vega_sign", lambda e: _sign_row(e, "swaption_vega_sign", "IRVega", {"Pay": 1, "Receive": 1})),
        ("swaption_delta_sign", lambda e: _sign_row(e, "swaption_delta_sign", "IRDelta", {"Pay": 1, "Receive": -1})),
        ("swaption_gamma_sign", lambda e: _sign_row(e, "swaption_gamma_sign", "IRGammaParallel", {"Pay": 1, "Receive": 1})),
        ("swaption_prob_exercise", row_swaption_prob),
        ("swaption_expiry", row_swaption_expiry),
    ))


# ------------------------------------------------------------------------------------ bond pack


def row_bond_fold(env: _Env) -> List[CheckResult]:
    size = env.ctx.kwargs.get("size")
    if not isinstance(size, (int, float)):
        return [CheckResult("bond_buy_sell_fold", SKIP, "no numeric size kwarg")]
    buy = env.resolve(buy_sell="Buy", size=abs(size))
    cases = {"Sell": (env.resolve(buy_sell="Sell", size=abs(size)), -1.0), "Buy with -size": (env.resolve(buy_sell="Buy", size=-abs(size)), -1.0),
             "Sell with -size": (env.resolve(buy_sell="Sell", size=-abs(size)), 1.0)}
    days = sorted({env.d1, _priced_date(env)})  # a later date too: FinancingToDate is 0 on the trade date
    sell = cases["Sell"][0]
    # a repo rate (and haircut) may depend on the direction (a short reverse-repos the bond in, at the special
    # rate): when it does, the financing measures built on it cannot negate exactly, so they are not folded
    by_side = env.has("RepoRate") and any(not _close(env.val(buy, d, "RepoRate"), env.val(sell, d, "RepoRate")) for d in days)
    skip = DIRECTION_REPO if by_side else frozenset()
    bad = [f"{label} on {d}: {m}" for d in days for label, (inst, sign) in cases.items() for m in _fold_mismatches(env, buy, inst, sign, d, skip=skip if sign < 0 else frozenset())[:2]]
    if bad:
        return [CheckResult("bond_buy_sell_fold", FAIL, "; ".join(bad[:6]) + " -- fold buy_sell x sign(size) into one signed face amount in resolve")]
    note = f"; RepoRate depends on the direction, so {sorted(DIRECTION_REPO)} were not folded" if by_side else ""
    return [CheckResult("bond_buy_sell_fold", PASS, f"on {', '.join(map(str, days))}: Sell = -Buy, -size = -Buy, Sell with -size = Buy for every amount (and PremiumCents-style per-|face| level); other levels equal{note}")]


def row_bond_size(env: _Env) -> List[CheckResult]:
    size = env.ctx.kwargs.get("size")
    if not isinstance(size, (int, float)):
        return [CheckResult("bond_size_linearity", SKIP, "no numeric size kwarg")]
    one, two = env.resolve(size=size), env.resolve(size=2 * size)
    days = sorted({env.d1, _priced_date(env)})
    bad = [f"on {d}: {m}" for d in days for m in _fold_mismatches(env, one, two, 1.0, d, size=2.0)]
    if bad:
        return [CheckResult("bond_size_linearity", FAIL, f"size x2: {bad[:5]} -- extensive measures must double, the yield, prices per 100, repo terms and other levels must not move")]
    return [CheckResult("bond_size_linearity", PASS, f"size {_fmt(size)} -> {_fmt(2 * size)} on {', '.join(map(str, days))}: extensive measures double, levels unchanged")]


def row_bond_signs(env: _Env) -> List[CheckResult]:
    out = []
    long_ = env.resolve(buy_sell="Buy")
    for name, m, want, why in (("bond_dv01_sign", "IRDelta", -1, "a long bond loses when its yield rises: IRDelta < 0 per +1bp (negate a per -1bp or receiver-positive risk)"),
                               ("bond_gamma_sign", "IRGammaParallel", 1, "a bullet bond is convex: IRGammaParallel > 0 per bp^2 (chain-rule second derivative)")):
        if not env.has(m):
            out.append(CheckResult(name, SKIP, f"{m} not mapped"))
            continue
        v = env.val(long_, env.d1, m)
        out.append(CheckResult(name, PASS if v * want > 0 else FAIL, f"long bond {m} {_fmt(v)}" + ("" if v * want > 0 else f": {why}")))
    return out


def row_bond_lightning(env: _Env) -> List[CheckResult]:
    name = "bond_lightning_dv01"
    if not (env.has("LightningDV01") and env.has("IRDelta")):
        return [CheckResult(name, SKIP, "needs LightningDV01 and IRDelta")]
    a, b = env.val(env.r1, env.d1, "LightningDV01"), env.val(env.r1, env.d1, "IRDelta")
    rel = abs(a - b) / max(abs(a), abs(b), 1e-12)
    status = PASS if rel <= 0.01 else WARN if rel <= 0.05 else FAIL
    return [CheckResult(name, status, f"LightningDV01 {_fmt(a)} vs IRDelta {_fmt(b)} ({rel:.2%})" + ("" if status == PASS else ": both are the Price change per +1bp of yield (per unit trade, holder-signed)"))]


def row_bond_yield_unit(env: _Env) -> List[CheckResult]:
    name = "bond_yield_unit"
    if not env.has("IRFwdRate"):
        return [CheckResult(name, SKIP, "IRFwdRate (the yield) not mapped")]
    unit, v = env.spec("IRFwdRate").unit, env.val(env.r1, env.d1, "IRFwdRate")
    bands = {"bp": (-500, 3000), "pct": (-5, 30), "decimal": (-0.05, 0.30)}
    if unit not in bands:
        return [CheckResult(name, FAIL, f"yield declared {unit!r}; a rate is bp/pct/decimal")]
    lo, hi = bands[unit]
    if not lo < v < hi:
        return [CheckResult(name, FAIL, f"yield {_fmt(v)} {unit} outside ({lo}, {hi})")]
    if unit == "bp" and abs(v) < 0.2:
        return [CheckResult(name, FAIL, f"yield {_fmt(v)} declared bp looks decimal: multiply by 1e4 or declare decimal")]
    if unit == "bp" and abs(v) < 20:
        return [CheckResult(name, WARN, f"yield {_fmt(v)} declared bp could be percent")]
    return [CheckResult(name, PASS, f"yield {_fmt(v)} {unit}")]


def row_bond_price_yield(env: _Env) -> List[CheckResult]:
    name = "bond_price_yield"
    if not env.has("IRFwdRate") or not env.market_on(env.d2):
        return [CheckResult(name, SKIP, "needs IRFwdRate and a market on d2")]
    long_ = env.resolve(buy_sell="Buy")
    dy = env.bp(long_, env.d2, "IRFwdRate") - env.bp(long_, env.d1, "IRFwdRate")
    if abs(dy) < 2.0:
        return [CheckResult(name, SKIP, f"yield moved {dy:.2f}bp between d1 and d2 (< 2bp)")]
    days = (env.d2 - env.d1).days
    carry = (env.get(long_, env.d1, "Theta") or 0.0) * days
    move = env.val(long_, env.d2, "Price") - env.val(long_, env.d1, "Price") + _cash_between(env, long_, env.d1, env.d2) - carry
    ok = move * dy < 0
    return [CheckResult(name, PASS if ok else FAIL, f"yield {dy:+.2f}bp, Price change net of cash and Theta x {days}d {_fmt(move)}" + ("" if ok else ": Price must fall when the yield rises (yield sign or unit?)"))]


def row_bond_cashflows_bound(env: _Env) -> List[CheckResult]:
    name = "bond_cashflows_bound"
    if not env.has("Cashflows", "frame"):
        return [CheckResult(name, SKIP, "Cashflows not mapped")]
    long_ = env.resolve(buy_sell="Buy")
    f = env.frame(long_, env.d1)
    y = env.bp(long_, env.d1, "IRFwdRate")
    if not len(f) or y is None or y <= 0:
        return [CheckResult(name, SKIP, "needs a non-empty Cashflows and a positive yield")]
    total, price = float(f["payment_amount"].sum()), env.val(long_, env.d1, "Price")
    ok = 0 < price < total
    return [CheckResult(name, PASS if ok else FAIL, f"long bond Price {_fmt(price)} vs sum of {len(f)} future flows {_fmt(total)} at yield {y:.1f}bp" + ("" if ok else ": at a positive yield 0 < Price < the undiscounted flows (Price per face, not per 100? flows missing or not holder-signed?)"))]


def row_bond_expiry(env: _Env) -> List[CheckResult]:
    name = "bond_expiry"
    if not env.has("ExpiryInYears"):
        return [CheckResult(name, SKIP, "ExpiryInYears not mapped")]
    implied = env.d1 + timedelta(days=round(env.val(env.r1, env.d1, "ExpiryInYears") * 365.0))
    dates = [v for v in env.r1.resolved_terms.values() if isinstance(v, date)]
    if "termination_date" in env.cfg.attributes:
        dates.append(env.svc.attribute(env.r1, "termination_date"))
    dates = [d for d in dates if isinstance(d, date)]
    if not dates:
        return [CheckResult(name, INFO, f"ExpiryInYears points at {implied}; no resolved date term to compare")]
    final = max(dates)
    ok = abs((final - implied).days) <= 1
    return [CheckResult(name, PASS if ok else WARN, f"ExpiryInYears points at {implied}, latest resolved/attribute date {final}" + ("" if ok else ": ExpiryInYears for a bond is the years to maturity (DEV-I17)"))]


# ---- BOND_DESIGN section 3: the bond identities and the financing contract (pricebt DEV-I20, DEV-I21)
REPO_BASES = (360.0, 365.0)  # the repo day counts a row tries; it reports the one that matches
DURATION_BANDS = (0.005, 0.05)  # |ModifiedDuration / (-1e4 IRDelta / Price) - 1|: PASS, WARN; beyond: FAIL
CONVEXITY_BANDS = (0.01, 0.10)  # |Convexity / (1e8 IRGammaParallel / Price) - 1|
YIELD_BANDS = {"bp": (-500, 3000), "pct": (-5, 30), "decimal": (-0.05, 0.30)}


def _bond_side(env: _Env, side: str):
    """(resolved clone on d1, signed face) for buy_sell=side, the probe's |size|."""
    size = abs(float(env.ctx.kwargs.get("size") or 1.0))
    return env.resolve(buy_sell=side, size=size), size * (1.0 if side == "Buy" else -1.0)


def _per_face(env: _Env, inst, d: date, m: str) -> float:
    """A notional_level measure as a plain ratio per unit of face (pct 100 -> 1.0)."""
    return env.val(inst, d, m) / NOTIONAL_UNIT[env.spec(m).unit]


def _decimal(env: _Env, inst, d: date, m: str) -> float:
    """A rate measure in decimal, by its declared unit."""
    return env.bp(inst, d, m) / 1e4


def _business_day(env: _Env, d: date) -> date:
    """d, or the first date after it, that is a business day of the bond's calendar: a weekday on
    which the config has a market (a US holiday has none). Past the config's data (no market on any
    of the next ten weekdays), the first weekday."""
    first = x = _bday(d)
    for _ in range(10):
        if env.market_on(x):
            return x
        x = _bday(x + timedelta(days=1))
    return first


def _settle(env: _Env, inst, d: date) -> date:
    """Standard settlement of trade date d: d + DaysToSettlement, else the next business day (T+1)."""
    return d + timedelta(days=round(env.val(inst, d, "DaysToSettlement"))) if env.has("DaysToSettlement") else _business_day(env, d + timedelta(days=1))


def _horizon(env: _Env, s: date) -> date:
    """H = settlement + 1 calendar month on the following business day of the bond's calendar
    (BOND_DESIGN: ForwardPrice, Carry): 2026-06-03 + 1 month = 2026-07-03, a US holiday, is 07-06."""
    return _business_day(env, s + ca.relativedelta(months=1))


def _flow_dates(env: _Env, inst, f: pd.DataFrame, until: Optional[date] = None) -> List[Tuple[date, date, float, str]]:
    """(drop date, actual payment date, holder-signed amount, type) per Cashflows row. The actual
    payment date is the settlement date of the drop date (the first settlement on or after the
    coupon date: a weekend or holiday coupon is paid on the next business day, as a Treasury's is),
    for rows dropping on or before `until` (default: the first drop date); later rows get the next
    business day after the drop date (they are past every horizon the rows look at)."""
    kinds = f["payment_type"] if "payment_type" in f else [""] * len(f)
    drops = [_as_date(p) for p in f["payment_date"]]
    until = until if until is not None else min(drops, default=None)
    out = []
    for drop, a, k in zip(drops, f["payment_amount"], kinds):
        exact = until is not None and drop <= until and env.market_on(drop)
        paid = _settle(env, inst, drop) if exact else _business_day(env, drop + timedelta(days=1))
        out.append((drop, paid, float(a), str(k)))
    return sorted(out)


def _coupons(env: _Env, inst, d: date):
    """Coupon rows (payment_type 'Coupon', else every row but the largest) of Cashflows(inst, d)."""
    rows = _flow_dates(env, inst, env.frame(inst, d))
    tagged = [r for r in rows if "coupon" in r[3].lower()]
    if tagged or not rows:
        return tagged
    big = max(abs(r[2]) for r in rows)
    return [r for r in rows if abs(r[2]) < big]


def _market_before(env: _Env, d: date) -> Optional[date]:
    """The last business day before d with a market."""
    for _ in range(10):
        d = _bday(d - timedelta(days=1), -1)
        if env.market_on(d):
            return d
    return None


def _band(rel: float, bands: Tuple[float, float]) -> str:
    return PASS if rel <= bands[0] else WARN if rel <= bands[1] else FAIL


def _worst(statuses) -> str:
    return next((s for s in (FAIL, WARN, SKIP, INFO) if s in statuses), PASS)


def row_bond_clean_dirty(env: _Env) -> List[CheckResult]:
    """CleanPrice + 100 x AccruedInterest / face = DirtyPrice and DirtyPrice x face / 100 = Price, on
    both directions (signed face: clean and dirty are the same for a long and a short)."""
    name = "bond_clean_dirty"
    need = ("CleanPrice", "DirtyPrice", "AccruedInterest", "Price")
    if not all(env.has(m) for m in need) or env.spec("CleanPrice").unit not in NOTIONAL_UNIT or env.spec("DirtyPrice").unit not in NOTIONAL_UNIT:
        return [CheckResult(name, SKIP, f"needs {list(need)} (prices per face in bp/pct/decimal/number)")]
    d = _priced_date(env)
    parts, statuses = [], []
    for side in ("Buy", "Sell"):
        inst, face = _bond_side(env, side)
        clean, dirty, ai, price = _per_face(env, inst, d, "CleanPrice"), _per_face(env, inst, d, "DirtyPrice"), env.val(inst, d, "AccruedInterest"), env.val(inst, d, "Price")
        statuses += [_identity_status(clean + ai / face, dirty, 1e-12), _identity_status(dirty * face, price, 1e-9 * abs(face))]
        parts.append(f"{side} (face {_fmt(face)}): clean {clean * 100:.6f} + 100 x accrued {_fmt(ai)} / face = {(clean + ai / face) * 100:.6f} vs dirty {dirty * 100:.6f}; "
                     f"dirty x face / 100 = {_fmt(dirty * face)} vs Price {_fmt(price)}")
    status = _worst(statuses)
    detail = f"on {d} (per 100 face): " + "; ".join(parts)
    return [CheckResult(name, status, detail + ("" if status == PASS else
                        ": CleanPrice = DirtyPrice - 100 x AccruedInterest / face and DirtyPrice = 100 x Price / face, per SIGNED face -- an accrued added instead of"
                        " subtracted, the accrued of the pricing date instead of the settlement date, an unsigned face, or a price per face instead of per 100"))]


def row_bond_fair_premium(env: _Env) -> List[CheckResult]:
    """A bond's FairPremium is Price itself (Price is already the settlement-date value, DEV-I20)."""
    name = "bond_fair_premium"
    if not env.has("FairPremium"):
        return [CheckResult(name, SKIP, "FairPremium not mapped")]
    d = _priced_date(env)
    got, price = env.val(env.r1, d, "FairPremium"), env.val(env.r1, d, "Price")
    status = _identity_status(got, price, 1e-9 * (_notional(env) or 1.0))
    detail = f"on {d}: FairPremium {_fmt(got)} vs Price {_fmt(price)}"
    return [CheckResult(name, status, detail + ("" if status == PASS else
                        ": a bond's FairPremium is Price (the amount the holder pays at standard settlement, holder-signed) -- not Price / DF(settlement)"
                        " (the swap text), not the clean value, not the cash flow's sign"))]


def row_bond_premium_cents(env: _Env) -> List[CheckResult]:
    """PremiumCents = Price / |face| in its unit (pct: the dirty price per 100 for a long), and so
    sign(face) x DirtyPrice in the same unit; both directions."""
    name = "bond_premium_cents"
    if not (env.has("PremiumCents") and env.has("DirtyPrice")) or env.spec("PremiumCents").unit not in NOTIONAL_UNIT or env.spec("DirtyPrice").unit not in NOTIONAL_UNIT:
        return [CheckResult(name, SKIP, "needs PremiumCents and DirtyPrice in bp/pct/decimal/number")]
    d, unit = _priced_date(env), env.spec("PremiumCents").unit
    parts, statuses = [], []
    for side in ("Buy", "Sell"):
        inst, face = _bond_side(env, side)
        got, price, dirty = _per_face(env, inst, d, "PremiumCents"), env.val(inst, d, "Price"), _per_face(env, inst, d, "DirtyPrice")
        sign = 1.0 if face > 0 else -1.0
        statuses += [_identity_status(got, price / abs(face), 1e-12), _identity_status(got, sign * dirty, 1e-12)]
        k = NOTIONAL_UNIT[unit]
        parts.append(f"{side}: PremiumCents {_fmt(got * k)} {unit} vs Price / |face| = {_fmt(price / abs(face) * k)}, sign(face) x DirtyPrice = {_fmt(sign * dirty * k)}")
    status = _worst(statuses)
    return [CheckResult(name, status, f"on {d}: " + "; ".join(parts) + ("" if status == PASS else
                        ": PremiumCents is Price / |face| in the declared unit (pct: the dirty price per 100, negative for a short) -- a price per face declared pct,"
                        " a signed face (a short's premium positive), or the clean price"))]


def row_bond_duration(env: _Env) -> List[CheckResult]:
    """ModifiedDuration = -(1/P) dP/dy = -1e4 x IRDelta / Price (IRDelta per +1bp of the yield); and,
    when the near step moved the yield, against the step's own price change (INFO, or WARN off)."""
    name = "bond_duration"
    if not all(env.has(m) for m in ("ModifiedDuration", "IRDelta", "Price")):
        return [CheckResult(name, SKIP, "needs ModifiedDuration, IRDelta (scalar) and Price")]
    d = _priced_date(env)
    md, delta, price = env.val(env.r1, d, "ModifiedDuration"), env.val(env.r1, d, "IRDelta"), env.val(env.r1, d, "Price")
    if abs(price) < 1e-12:
        return [CheckResult(name, SKIP, f"Price ~0 on {d}")]
    want = -1e4 * delta / price
    detail = f"on {d}: ModifiedDuration {md:.6f}y vs -1e4 x IRDelta {_fmt(delta)} / Price {_fmt(price)} = {want:.6f}y"
    if md * want <= 0:
        return [CheckResult(name, FAIL, detail + ": opposite signs -- ModifiedDuration = -(1/P) dP/dy is positive for a bullet bond (long or short); dP/dy has the opposite sign")]
    rel = abs(md / want - 1.0)
    status = _band(rel, DURATION_BANDS)
    s = _near_step(env)
    if s is not None and abs(s["dr"]) >= GAMMA_MIN_MOVE_BP and env.has("Convexity") and s["theta"] is not None:
        p0, dy = env.val(env.r1, s["t0"], "Price"), s["dr"] / 1e4
        move = s["economic"] - s["theta"] * s["days"] - 0.5 * env.val(env.r1, s["t0"], "Convexity") * p0 * dy * dy
        fd = -move / (p0 * dy)
        detail += f"; the {s['t0']} -> {s['t1']} step (yield {s['dr']:+.2f}bp, carry and convexity removed) implies {fd:.4f}y"
        if status == PASS and abs(fd / md - 1.0) > 0.2:
            status, detail = WARN, detail + " (more than 20% apart: a duration on another yield than IRFwdRate's?)"
    return [CheckResult(name, status, detail + ("" if status == PASS else
                        f" ({rel:.2%} apart): ModifiedDuration is -(1/P) dP/dy in years per unit of DECIMAL yield in the IRFwdRate convention, P the dirty price"
                        " -- a Macaulay duration under another compounding, a duration per 100bp or in months, or a clean-price denominator"))]


def row_bond_convexity(env: _Env) -> List[CheckResult]:
    """Convexity > 0 for a bullet bond (long and short alike: intensive) and = 1e8 x IRGammaParallel /
    Price (IRGammaParallel per bp^2 of the yield: a finite difference of Price in the yield)."""
    name = "bond_convexity"
    if not all(env.has(m) for m in ("Convexity", "IRGammaParallel", "Price")):
        return [CheckResult(name, SKIP, "needs Convexity, IRGammaParallel and Price")]
    d = _priced_date(env)
    long_, _face = _bond_side(env, "Buy")
    c, gamma, price = env.val(long_, d, "Convexity"), env.val(long_, d, "IRGammaParallel"), env.val(long_, d, "Price")
    if abs(price) < 1e-12:
        return [CheckResult(name, SKIP, f"Price ~0 on {d}")]
    want = 1e8 * gamma / price
    detail = f"on {d}: long Convexity {c:.6f}y^2 vs 1e8 x IRGammaParallel {_fmt(gamma)} / Price {_fmt(price)} = {want:.6f}y^2"
    if c <= 0:
        return [CheckResult(name, FAIL, detail + ": a bullet bond is convex -- Convexity = (1/P) d2P/dy2 > 0 in years^2")]
    rel = abs(c / want - 1.0) if want else math.inf
    status = _band(rel, CONVEXITY_BANDS)
    return [CheckResult(name, status, detail + ("" if status == PASS else
                        f" ({rel:.2%} apart): Convexity is (1/P) d2P/dy2 per unit of decimal yield squared -- half of it (the Taylor coefficient), a convexity"
                        " per 100bp^2 or in percent, or a gamma that is not the chain-rule second derivative"))]


def row_bond_settlement(env: _Env) -> List[CheckResult]:
    """DaysToSettlement: calendar days to standard settlement, 1..4 for a T+1 market, a whole number
    (3 on a Friday when the other weekdays read 1); the settlement_date attribute = trade date +
    DaysToSettlement of the trade date."""
    name = "bond_settlement"
    if not env.has("DaysToSettlement"):
        return [CheckResult(name, SKIP, "DaysToSettlement not mapped")]
    days, d = [], env.d1
    while len(days) < 6 and d is not None:
        if env.market_on(d):
            days.append(d)
        d = env.next_market(d)
    vals = {d: env.val(env.r1, d, "DaysToSettlement") for d in days}
    shown = ", ".join(f"{d:%a} {d}: {v:g}" for d, v in vals.items())
    bad = [f"{d}: {v!r}" for d, v in vals.items() if not (math.isfinite(v) and v == round(v) and 0 <= v <= 7)]
    if bad:
        return [CheckResult(name, FAIL, f"{shown}: {bad} -- DaysToSettlement is a whole number of CALENDAR days from the pricing date to standard settlement (T+1: 1, 3 over a weekend)")]
    fridays = [v for d, v in vals.items() if d.weekday() == 4]
    others = {v for d, v in vals.items() if d.weekday() < 3}
    if fridays and others == {1.0} and any(v == 1.0 for v in fridays):
        return [CheckResult(name, FAIL, f"{shown}: 1 on a Friday -- business days, not calendar days (a T+1 Friday trade settles on Monday: 3)")]
    parts, status = [shown], PASS
    if "settlement_date" in env.cfg.attributes:
        for d in [env.d1] + [d for d in days if d.weekday() == 4][:1]:
            inst = env.r1 if d == env.d1 else env.resolve(d)
            sd = env.svc.attribute(inst, "settlement_date")
            sd = _as_date(sd) if sd is not None else None
            dts = env.val(inst, d, "DaysToSettlement")
            if sd is None or (sd - d).days != round(dts):
                return [CheckResult(name, FAIL, f"{shown}; trade date {d}: settlement_date attribute {sd} vs trade date + DaysToSettlement {dts:g} = {d + timedelta(days=round(dts))}"
                                                " -- resolve pins the standard settlement of the trade date (BOND_DESIGN 4.11); the two must agree")]
            parts.append(f"settlement_date attribute of a trade on {d}: {sd} = trade date + {dts:g}d")
    if any(v not in (1.0, 2.0, 3.0, 4.0) for v in vals.values()):
        status = WARN
        parts.append("outside 1..4: not a T+1 market (fine for T+0/T+2/T+3 if that is your standard settlement)")
    return [CheckResult(name, status, "; ".join(parts))]


def row_bond_accrued_over_coupon(env: _Env) -> List[CheckResult]:
    """Across the first coupon Cashflows lists: on the business day before its drop date the
    accrued is ~ the whole coupon, on the drop date (settlement on or after the coupon date) ~0."""
    name = "bond_accrued_over_coupon"
    if not (env.has("AccruedInterest") and env.has("Cashflows", "frame")):
        return [CheckResult(name, SKIP, "needs AccruedInterest and Cashflows")]
    long_, _face = _bond_side(env, "Buy")
    coupons = _coupons(env, long_, env.d1)
    if not coupons:
        return [CheckResult(name, SKIP, f"no coupon in Cashflows on {env.d1}")]
    drop, paid, amount, _k = coupons[0]
    tb = drop if env.market_on(drop) else env.next_market(drop)
    ta = _market_before(env, drop)
    if ta is None or tb is None:
        return [CheckResult(name, SKIP, f"no market around the coupon drop date {drop}")]
    before, after = env.val(long_, ta, "AccruedInterest"), env.val(long_, tb, "AccruedInterest")
    detail = f"coupon {_fmt(amount)} paid {paid} (dropped from Price on {drop}): long AccruedInterest {_fmt(before)} on {ta}, {_fmt(after)} on {tb}"
    if before * amount <= 0 or after >= before:
        return [CheckResult(name, FAIL, detail + ": the accrued must be holder-signed and reset when the coupon leaves Price -- accrued to the PRICING date (not settlement),"
                                                  " or not holder-signed, keeps it from resetting on the drop date")]
    if not (0.8 <= before / amount <= 1.0 + 1e-9 and -1e-9 <= after / amount <= 0.1):
        return [CheckResult(name, WARN, detail + f": {before / amount:.1%} of the coupon before, {after / amount:.1%} after (expected ~100% and ~0%): the accrual day count or the settlement lag?")]
    return [CheckResult(name, PASS, detail + f" ({before / amount:.1%} -> {after / amount:.1%} of the coupon)")]


def row_bond_repo(env: _Env) -> List[CheckResult]:
    """RepoRate finite and in a plausible band for its unit on every probed date; RepoHaircut a
    fraction in [0, 1)."""
    name = "bond_repo"
    if not (env.has("RepoRate") and env.has("RepoHaircut")):
        return [CheckResult(name, SKIP, "needs RepoRate and RepoHaircut")]
    ru, hu = env.spec("RepoRate").unit, env.spec("RepoHaircut").unit
    if ru not in YIELD_BANDS or hu not in TO_BP:
        return [CheckResult(name, FAIL, f"RepoRate declared {ru!r}, RepoHaircut {hu!r}: both are rates (bp/pct/decimal)")]
    days = sorted({env.d1, _priced_date(env)})
    rates = {d: env.val(env.r1, d, "RepoRate") for d in days}
    cuts = {d: _decimal(env, env.r1, d, "RepoHaircut") for d in days}
    lo, hi = YIELD_BANDS[ru]
    detail = "; ".join(f"{d}: RepoRate {_fmt(r)} {ru}, RepoHaircut {cuts[d]:.4%}" for d, r in rates.items())
    bad = [d for d, r in rates.items() if not (math.isfinite(r) and lo < r < hi)]
    if bad:
        return [CheckResult(name, FAIL, detail + f": RepoRate outside ({lo}, {hi}) {ru} on {bad} -- a rate in another unit than declared, or a NaN on a held date")]
    bad = [d for d, h in cuts.items() if not (math.isfinite(h) and 0.0 <= h < 1.0)]
    if bad:
        return [CheckResult(name, FAIL, detail + f": RepoHaircut outside [0, 1) on {bad} -- the fraction NOT financed, in its declared unit (decimal 0.02 = 2%; a percent declared decimal reads 2.0)")]
    if ru == "bp" and any(0 < abs(r) < 0.2 for r in rates.values()):
        return [CheckResult(name, WARN, detail + ": under 0.2bp declared bp -- a decimal rate under a bp declaration?")]
    return [CheckResult(name, PASS, detail)]


def _financing_step(env: _Env, inst, t0: date, t1: date, principal: float, haircut: float) -> Tuple[str, str]:
    """(status, text) for one step's FinancingToDate change against -(1 - h) Price(trade) x RepoRate x
    settlement days / basis, trying the repo rate of either end of the step and both bases."""
    got = env.val(inst, t1, "FinancingToDate") - env.val(inst, t0, "FinancingToDate")
    s0, s1 = _settle(env, inst, t0), _settle(env, inst, t1)
    days, trade_days = (s1 - s0).days, (t1 - t0).days
    best = None
    for rd in (t0, t1):
        r = _decimal(env, inst, rd, "RepoRate")
        for basis in REPO_BASES:
            want = -principal * r * days / basis
            if best is None or abs(got - want) < abs(got - best[0]):
                best = (want, rd, basis, r)
    want, rd, basis, r = best
    status = _identity_status(got, want, 1e-9 * abs(principal))
    text = (f"{t0} -> {t1} (settlement {s0} -> {s1}: {days} calendar days): change {_fmt(got)} vs -(1 - h) x Price(trade) {_fmt(principal)} x RepoRate {r:.4%}"
            f" (of {rd}) x {days} / {basis:g} = {_fmt(want)}")
    if status != PASS and days != trade_days:
        alt = -principal * r * trade_days / basis
        if _identity_status(got, alt, 1e-9 * abs(principal)) == PASS:
            status, text = FAIL, text + f"; it matches {trade_days} TRADE-date days ({_fmt(alt)}): repo accrues between settlement dates"
    if status != PASS and haircut:
        alt = want / (1.0 - haircut)
        if _identity_status(got, alt, 1e-9 * abs(principal)) == PASS:
            status, text = FAIL, text + f"; it matches a principal of the whole Price ({_fmt(alt)}): the principal is (1 - RepoHaircut) x Price(trade date)"
    return status, text


def row_bond_financing(env: _Env) -> List[CheckResult]:
    """FinancingToDate: 0 on the trade date; a long pays (<= 0) and a short receives (>= 0) at a
    positive repo rate; its change over a step is -(1 - h) x Price(trade date) x RepoRate x
    settlement days / basis (basis 360 or 365, reported), on the first step and on the first step
    whose settlement days differ from its trade days (a Thursday -> Friday step: 3 vs 1)."""
    name = "bond_financing"
    need = ("FinancingToDate", "RepoRate", "RepoHaircut", "Price")
    if not all(env.has(m) for m in need) or env.spec("RepoRate").unit not in TO_BP:
        return [CheckResult(name, SKIP, f"needs {list(need)} (RepoRate in bp/pct/decimal)")]
    t1 = env.next_market(env.d1)
    if t1 is None or not env.market_on(env.d1):
        return [CheckResult(name, SKIP, f"needs markets on {env.d1} and the next business day")]
    parts, statuses = [], []
    for side in ("Buy", "Sell"):
        inst, face = _bond_side(env, side)
        f0, f1 = env.val(inst, env.d1, "FinancingToDate"), env.val(inst, t1, "FinancingToDate")
        price0, r = env.val(inst, env.d1, "Price"), _decimal(env, inst, t1, "RepoRate")
        if abs(f0) > 1e-9 * abs(price0):
            return [CheckResult(name, FAIL, f"{side}: FinancingToDate {_fmt(f0)} on its trade date {env.d1}, expected 0 -- the funding leg starts at the trade's settlement (resolve pins the trade date)")]
        if r > 0 and f1 * face > 0:
            return [CheckResult(name, FAIL, f"{side}: FinancingToDate {_fmt(f1)} on {t1} at RepoRate {r:.4%} -- holder-signed: a long pays the repo interest (<= 0), a short lends the cash and receives it (>= 0)")]
        if side == "Sell":
            parts.append(f"Sell: {_fmt(f1)} on {t1} (>= 0)")
            continue
        haircut = _decimal(env, inst, env.d1, "RepoHaircut")
        principal = (1.0 - haircut) * price0
        steps, d = [(env.d1, t1)], t1
        for _ in range(10):
            nxt = env.next_market(d)
            if nxt is None:
                break
            if (_settle(env, inst, nxt) - _settle(env, inst, d)).days != (nxt - d).days:
                steps.append((d, nxt))
                break
            d = nxt
        for t0, t1_ in steps:
            st, text = _financing_step(env, inst, t0, t1_, principal, haircut)
            statuses.append(st)
            parts.append(text)
    status = _worst(statuses)
    detail = f"0 on the trade date {env.d1}; " + "; ".join(parts)
    return [CheckResult(name, status, detail + ("" if status == PASS else
                        ": FinancingToDate accrues simple interest at RepoRate in the repo day count on (1 - RepoHaircut) x Price(trade date), between SETTLEMENT dates (DEV-I21)"))]


def _parity_forward(env: _Env, inst, d: date):
    """(Price, [(actual payment date, holder-signed amount)], RepoRate decimal, settlement s, horizon H)
    on d: the inputs of the parity forward (_forward_at)."""
    s = _settle(env, inst, d)
    h, r = _horizon(env, s), _decimal(env, inst, d, "RepoRate")
    flows = [(paid, a) for _drop, paid, a, _k in _flow_dates(env, inst, env.frame(inst, d), until=h)] if env.has("Cashflows", "frame") else []
    return env.val(inst, d, "Price"), flows, r, s, h


def _day_floor(price: float, r: float) -> float:
    """The forward and carry rows' absolute floor: one day of repo interest on |Price| (a payment or
    horizon date one day apart, e.g. an unadjusted weekend coupon date, is not a mistake)."""
    return abs(price) * max(abs(r), 1e-4) / 360.0


def _forward_at(price: float, flows, r: float, s: date, h: date, basis: float) -> Tuple[float, float]:
    """(forward value at h, the coupons paid in (s, h])."""
    due = [(c, a) for c, a in flows if s < c <= h]
    return price * (1.0 + r * (h - s).days / basis) - sum(a * (1.0 + r * (h - c).days / basis) for c, a in due), sum(a for _c, a in due)


def _coupon_horizon_date(env: _Env, inst) -> Optional[date]:
    """A market date whose carry horizon (settlement + 1 month) contains the first coupon."""
    if not env.has("Cashflows", "frame"):
        return None
    coupons = _coupons(env, inst, env.d1)
    if not coupons:
        return None
    d = _bday(coupons[0][1] - timedelta(days=15), -1)
    for _ in range(10):
        if d > env.d1 and env.market_on(d):
            return d
        d = _bday(d - timedelta(days=1), -1)
    return None


def row_bond_forward_parity(env: _Env) -> List[CheckResult]:
    """ForwardPrice = Price (1 + RepoRate tau(s, H)) - sum over flows c in (s, H] of C (1 + RepoRate
    tau(c, H)), H = settlement + 1 calendar month (following business day), c the coupon's actual
    payment date, tau in the repo day count (360 or 365, reported); on the priced date and on a date
    whose horizon holds a coupon; within one day of repo interest on |Price| is a PASS."""
    name = "bond_forward_parity"
    if not all(env.has(m) for m in ("ForwardPrice", "RepoRate", "Price")) or env.spec("RepoRate").unit not in TO_BP:
        return [CheckResult(name, SKIP, "needs ForwardPrice, RepoRate (bp/pct/decimal) and Price")]
    long_, _face = _bond_side(env, "Buy")
    dates = [_priced_date(env)] + [d for d in [_coupon_horizon_date(env, long_)] if d is not None]
    parts, statuses = [], []
    for d in dates:
        got = env.val(long_, d, "ForwardPrice")
        price, flows, r, s, h = _parity_forward(env, long_, d)
        fits = {b: _forward_at(price, flows, r, s, h, b) for b in REPO_BASES}
        basis = min(fits, key=lambda b: abs(fits[b][0] - got))
        want, cpn = fits[basis]
        statuses.append(_identity_status(got, want, _day_floor(price, r)))
        paid = ", ".join(str(c) for c, _a in flows if s < c <= h)
        parts.append(f"{d}: ForwardPrice {_fmt(got)} vs Price {_fmt(price)} financed at {r:.4%} from {s} to H {h} (/{basis:g}) less coupons {_fmt(cpn)}"
                     + (f" paid {paid}" if paid else "") + f" = {_fmt(want)}")
    status = _worst(statuses)
    return [CheckResult(name, status, "long: " + "; ".join(parts) + ("" if status == PASS else
                        ": ForwardPrice is the financed forward value at H = settlement + 1 month: a coupon paid before H must be subtracted (with its"
                        " reinvestment at the repo rate), and the swap text Price / DF(expiry) does not apply to a bond (DEV-I21)"))]


def _accrued_at(f: pd.DataFrame, x: date) -> Optional[float]:
    """Holder-signed accrued at settlement date x from the Cashflows accrual columns (ACT/ACT in the
    period), 0 on a coupon date and after the last flow; None without the columns."""
    if not {"accrual_start_date", "accrual_end_date"} <= set(f.columns):
        return None
    for st, en, a, k in zip(f["accrual_start_date"], f["accrual_end_date"], f["payment_amount"], f.get("payment_type", [""] * len(f))):
        if "principal" in str(k).lower():
            continue
        st, en = _as_date(st), _as_date(en)
        if st <= x < en:
            return float(a) * (x - st).days / (en - st).days
    return 0.0


def _accrued_at_horizon(env: _Env, inst, d: date, h: date) -> Tuple[Optional[float], str]:
    """(the holder-signed accrued for settlement H, where it came from): the config's own
    AccruedInterest on the trade date that settles on H (its own day count), else the Cashflows
    accrual columns read ACT/ACT in the period (the US Treasury convention), else (None, why)."""
    x = _market_before(env, h)
    if x is not None and x >= d and _settle(env, inst, x) == h:
        return env.val(inst, x, "AccruedInterest"), f"the config's AccruedInterest on {x}, settling on H"
    ai = _accrued_at(env.frame(inst, d), h) if env.has("Cashflows", "frame") else None
    return ai, "ACT/ACT in the Cashflows accrual period: no trade date with a market settles on H"


def row_bond_carry_roll(env: _Env) -> List[CheckResult]:
    """Carry = clean value now - clean forward value at H: (Price - AccruedInterest) - (forward - the
    accrued at H), the forward from the parity formula (bond_forward_parity), the accrued at H the
    config's own (AccruedInterest on the trade date settling on H; ACT/ACT from the Cashflows accrual
    columns only when no such date has a market); Carry and RollDown negate with the direction;
    Carry + RollDown finite; within one day of repo interest on |Price| is a PASS."""
    name = "bond_carry_roll"
    need = ("Carry", "RollDown", "Price", "AccruedInterest", "RepoRate")
    if not all(env.has(m) for m in need) or env.spec("RepoRate").unit not in TO_BP:
        return [CheckResult(name, SKIP, f"needs {list(need)}")]
    long_, _f = _bond_side(env, "Buy")
    short, _f = _bond_side(env, "Sell")
    dates = [_priced_date(env)] + [d for d in [_coupon_horizon_date(env, long_)] if d is not None]
    parts, statuses = [], []
    for d in dates:
        carry, roll = env.val(long_, d, "Carry"), env.val(long_, d, "RollDown")
        sc, sr = env.val(short, d, "Carry"), env.val(short, d, "RollDown")
        if not (math.isfinite(carry + roll) and _close(sc, -carry) and _close(sr, -roll)):
            return [CheckResult(name, FAIL, f"{d}: long Carry {_fmt(carry)}, RollDown {_fmt(roll)}; short {_fmt(sc)}, {_fmt(sr)} -- finite, holder-signed (a short's"
                                            " carry and roll-down are the long's negated)")]
        price, flows, r, s, h = _parity_forward(env, long_, d)
        ai_h, source = _accrued_at_horizon(env, long_, d, h)
        if ai_h is None:
            statuses.append(INFO)
            parts.append(f"{d}: Carry {_fmt(carry)}, RollDown {_fmt(roll)} (the accrued at H {h} is not readable: no trade date settling on it, no accrual columns in Cashflows)")
            continue
        clean_now = price - env.val(long_, d, "AccruedInterest")
        fits = {b: clean_now - (_forward_at(price, flows, r, s, h, b)[0] - ai_h) for b in REPO_BASES}
        basis = min(fits, key=lambda b: abs(fits[b] - carry))
        statuses.append(_identity_status(carry, fits[basis], _day_floor(price, r)))
        parts.append(f"{d}: Carry {_fmt(carry)} vs clean now {_fmt(clean_now)} - (forward at H {h} - accrued at H {_fmt(ai_h)} ({source})) = {_fmt(fits[basis])} (/{basis:g});"
                     f" RollDown {_fmt(roll)}, Carry + RollDown {_fmt(carry + roll)}")
    status = _worst([s for s in statuses if s != INFO] or [INFO])
    return [CheckResult(name, status, "long: " + "; ".join(parts) + ("" if status in (PASS, INFO) else
                        ": Carry is the clean value now minus the clean financed forward at H = coupon income over (s, H] minus the repo interest -- a dirty"
                        " forward, the wrong horizon, or financing left out"))]


def _step_explained(env: _Env, inst, t0: date, t1: date) -> float:
    """Theta x days + IRDelta x dy + IRGammaParallel x dy^2 / 2 over t0 -> t1, greeks on t0, dy the
    yield (IRFwdRate) move in bp: the Price change plus dropped flows a step should show (a bond's
    Theta counts the flows it drops), so a large yield move is not taken for a missing coupon."""
    out = (env.get(inst, t0, "Theta") or 0.0) * (t1 - t0).days
    y0 = env.bp(inst, t0, "IRFwdRate")
    if y0 is not None:
        dy = env.bp(inst, t1, "IRFwdRate") - y0
        out += (env.get(inst, t0, "IRDelta") or 0.0) * dy + 0.5 * (env.get(inst, t0, "IRGammaParallel") or 0.0) * dy * dy
    return out


def row_bond_holding_cash(env: _Env) -> List[CheckResult]:
    """A short position held across the first coupon drop date by GenericEngine: on every mark the
    engine books (backtest.holding_cash) the Cashflows dropped since the previous mark plus the
    change of FinancingToDate, both recomputed here from the config; the Total change over the run is
    the Price change + coupons + financing (once, over the run); and on no step does the Price change
    plus the booked coupon, net of the step's Theta and yield move (_step_explained), leave a
    coupon-sized jump (a Cashflows payment_date that is not the drop date)."""
    from pricebt.backtests.actions import AddTradeAction
    from pricebt.backtests.generic_engine import GenericEngine
    from pricebt.backtests.strategy import Strategy
    from pricebt.backtests.triggers import DateTrigger, DateTriggerRequirements

    name = "bond_holding_cash"
    if not (env.has("FinancingToDate") and env.has("Cashflows", "frame")):
        return [CheckResult(name, SKIP, "needs FinancingToDate and Cashflows (the engine books holding cash only for an asset mapping FinancingToDate)")]
    long_, _f = _bond_side(env, "Buy")
    coupons = _coupons(env, long_, env.d1)
    if not coupons:
        return [CheckResult(name, SKIP, f"no coupon in Cashflows on {env.d1}")]
    drop = coupons[0][0]
    start = _market_before(env, _market_before(env, drop) or drop)
    end = env.next_market(drop if env.market_on(drop) else (env.next_market(drop) or drop))
    if start is None or end is None:
        return [CheckResult(name, SKIP, f"no market around the coupon drop date {drop}")]
    size = abs(float(env.ctx.kwargs.get("size") or 1.0))
    trade = env.ctx.inst.clone(buy_sell="Sell", size=size, name="holding_cash_probe")
    trigger = DateTrigger(DateTriggerRequirements(dates=[start]), actions=AddTradeAction(trade, name="hc"))
    bt = GenericEngine().run_backtest(Strategy(None, [trigger]), start=start, end=end, frequency="1b", show_progress=False)
    booked = {d: day for d, day in bt.holding_cash.items() if day}
    if not booked:
        return [CheckResult(name, FAIL, f"{start} -> {end}: the engine booked no holding cash for a position whose asset maps FinancingToDate (DEV-E22)")]
    ((inst, _),) = next(iter(booked.values())).items()  # the engine keys holding cash by the instrument
    marks = sorted(d for d in bt.portfolio_dict if any(i.name == inst.name for i in bt.portfolio_dict[d]))
    bad, prev, total_cash, total_fin = [], marks[0], 0.0, 0.0
    jump = 0.5 * abs(coupons[0][2])
    for d in marks[1:]:
        ccy, cash, fin = bt.holding_cash.get(d, {}).get(inst, (None, 0.0, 0.0))
        want_cash = _cash_between(env, inst, prev, d)
        want_fin = env.val(inst, d, "FinancingToDate") - env.val(inst, prev, "FinancingToDate")
        if not (_close(cash, want_cash, 1e-9) and _close(fin, want_fin, 1e-9)):
            bad.append(f"{d}: booked coupons {_fmt(cash)}, financing {_fmt(fin)} vs the config's {_fmt(want_cash)}, {_fmt(want_fin)}")
        dp = env.val(inst, d, "Price") - env.val(inst, prev, "Price")
        explained = _step_explained(env, inst, prev, d)
        if abs(dp + cash - explained) > jump:
            bad.append(f"{d}: Price change {_fmt(dp)} + booked coupons {_fmt(cash)} - the step's carry and yield move {_fmt(explained)} leaves a coupon-sized"
                       " jump: Cashflows.payment_date must be the date Price drops the flow")
        total_cash, total_fin, prev = total_cash + cash, total_fin + fin, d
    total = bt.result_summary[bt.TOTAL_COLUMN].astype(float)
    dprice = env.val(inst, marks[-1], "Price") - env.val(inst, marks[0], "Price")
    change = float(total.loc[marks[-1]] - total.loc[marks[0]])
    if not _close(change, dprice + total_cash + total_fin, 1e-9):
        bad.append(f"Total change {_fmt(change)} != Price change {_fmt(dprice)} + coupons {_fmt(total_cash)} + financing {_fmt(total_fin)}")
    detail = (f"short {size:g} face held {marks[0]} -> {marks[-1]} across the drop date {drop}: Total change {_fmt(change)} = Price change {_fmt(dprice)}"
              f" + coupons {_fmt(total_cash)} + financing {_fmt(total_fin)}; {len(marks) - 1} marks booked")
    if bad:
        return [CheckResult(name, FAIL, detail + "; " + "; ".join(bad[:4]))]
    return [CheckResult(name, PASS, detail + ", each equal to the config's dropped Cashflows + FinancingToDate change (DEV-E22)")]


def bond_pack(ctx) -> List[CheckResult]:
    """Bond pack: gs Bond fields buy_sell, identifier, size; pricebt conventions: holder-signed, per
    +1bp of the yield (IRFwdRate), long bond IRDelta < 0; the bond identities and the financing
    contract (BOND_DESIGN section 3)."""
    env = _Env(ctx, "Bond")
    return _run(env, (
        ("bond_buy_sell_fold", row_bond_fold),
        ("bond_size_linearity", row_bond_size),
        ("bond_signs", row_bond_signs),
        ("bond_lightning_dv01", row_bond_lightning),
        ("bond_yield_unit", row_bond_yield_unit),
        ("bond_price_yield", row_bond_price_yield),
        ("bond_cashflows_bound", row_bond_cashflows_bound),
        ("bond_expiry", row_bond_expiry),
        ("bond_clean_dirty", row_bond_clean_dirty),
        ("bond_fair_premium", row_bond_fair_premium),
        ("bond_premium_cents", row_bond_premium_cents),
        ("bond_duration", row_bond_duration),
        ("bond_convexity", row_bond_convexity),
        ("bond_settlement", row_bond_settlement),
        ("bond_accrued_over_coupon", row_bond_accrued_over_coupon),
        ("bond_repo", row_bond_repo),
        ("bond_financing", row_bond_financing),
        ("bond_forward_parity", row_bond_forward_parity),
        ("bond_carry_roll", row_bond_carry_roll),
        ("bond_holding_cash", row_bond_holding_cash),
    ))
