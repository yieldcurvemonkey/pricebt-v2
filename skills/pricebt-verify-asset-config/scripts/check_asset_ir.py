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

CONTRACT (docs/v2/IR_RISK_DESIGN.md section 2, docs/v2/IR_STRICT_CONTRACT.md R3-0)
  contract[M]         IRSwap / IRSwaption (strict): PASS mapped; FAIL not mapped, or declared under
                      unsupported_measures (the measure, a form, or a preset of it), mapped or not:
                      only a mapping satisfies a strict row. Bond: PASS mapped; WARN declared (the
                      reason is the explanation; P&L-critical measures say what stops working); WARN
                      "declaration reason is a TODO" (the pasted block was never edited); FAIL
                      neither mapped nor declared. Any class: FAIL a mapped slot with the wrong
                      unit/shape. The loader already refuses every FAIL here, so they show on --pack
                      only (a ConfigInstrument).
  contract_declarations  strict: FAIL per declared contract measure or preset of one. Any class:
                      WARN per stale declaration (Bond: mapped AND declared, the mapping wins), a
                      declared preset name, a declared form outside the row, an unknown name.
  ir_fake_constant    (strict) a contract measure mapped to a literal constant ('0.0', '{}',
                      float('nan'), math.nan, math.inf) outside contracts.ZERO_BY_CONVENTION (vol
                      measures on a swap, IRBasis, IRXccyDelta): a placeholder standing in for a
                      measure the library was never asked for (FAIL); a zero-by-convention measure
                      mapped to a constant other than 0 / {} (IRVega '5.0') (FAIL).

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
                      from d1 as d(IRDelta) = slope*dr + drift*days (vanna*dsigma removed), so a
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
WARN on a tolerance miss; each SKIPs when its measures are not mapped)
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

BOND PACK (Bond; direction buy_sell, size `size`)
  bond_buy_sell_fold  Sell, negative size, or Sell with negative size not folded into one signed
                      face (FAIL).
  bond_size_linearity Price/IRDelta not doubling with size, or the yield moving with size (FAIL).
  bond_dv01_sign      long bond IRDelta >= 0: risk reported per -1bp or receiver-positive (FAIL).
  bond_gamma_sign     long bond IRGammaParallel <= 0 (FAIL; bullet bonds are convex).
  bond_lightning_dv01 LightningDV01 != the IRDelta scalar (WARN > 1%, FAIL > 5%).
  bond_yield_unit     IRFwdRate (the yield) outside the band of its declared unit (FAIL), or
                      under 20 when declared bp (WARN: percent?).
  bond_price_yield    Price net of carry rising with the yield across d1..d2 (FAIL).
  bond_cashflows_bound  Price outside (0, sum of future flows) for a long bond at a positive
                      yield (FAIL): Cashflows missing flows, or Price per 100 vs per face.
  bond_expiry         ExpiryInYears not pointing at the maturity (WARN; INFO if no date to compare).
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
# a declared measure here (a Bond: the strict classes cannot declare) stops ir_pnl_definition /
# bond_pnl_definition / pnl_explain_table (UnsupportedMeasureError) -- still a WARN, see pnl_note()
PNL_CRITICAL = ("IRDelta", "IRGammaParallel", "IRFwdRate", "Theta", "ExpiryInYears", "Cashflows")
PNL_CRITICAL_VOL = ("IRVega", "IRAnnualImpliedVol", "IRVanna", "IRVolga")
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
_LEVEL_KINDS = frozenset({"rate", "vol", "time", "prob"})


def _is_amount(key: str, spec, inst: Optional[str] = None) -> bool:
    """True for an amount (negates with direction, scales with size), False for a level. A
    contract measure goes by its kind (a `number` probability is a level); anything else by unit."""
    from pricebt.risk import contracts

    base = contracts.base_measure(key)[0]
    kind = next((r.kind for r in contracts.contract_for(inst or "") if r.measure == base), None)
    if kind is not None and kind != "table":
        return kind not in _LEVEL_KINDS
    return spec.unit in _AMOUNT_UNITS


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


def pnl_note(measure: str, inst: str) -> str:
    """What a declaration of `measure` stops (appended to its WARN)."""
    if measure == "Cashflows":
        return " -- P&L-critical when Price drops paid flows: BackTest.pnl_explain_table cannot book the coupon cash (cashflow_pnl 0), so the residual jumps on payment dates."
    if measure in PNL_CRITICAL or (inst == "IRSwaption" and measure in PNL_CRITICAL_VOL):
        return " -- P&L-critical: a backtest using ir_pnl_definition / swaption_pnl_definition / bond_pnl_definition raises UnsupportedMeasureError on this asset."
    return ""


def check_contract(ctx, inst: str) -> List[CheckResult]:
    """One row per contract measure of `inst` (map / declare / missing), plus declaration warnings.
    Bond: declared is a WARN, never a FAIL -- an honest declaration is the contract working
    (decision 0.2), and the P&L code already raises loudly on a declared measure. A strict class
    (IRSwap, IRSwaption; IR_STRICT_CONTRACT R3-0): only a mapping satisfies a row, so a missing or
    a declared contract measure (or a declared preset of one) is a FAIL, mapped or not."""
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
    strict = contracts.is_strict(inst)
    strict_names = "/".join(sorted(contracts.STRICT_CLASSES))
    out = []
    for req in reqs:
        status, parts = PASS, []
        declared = cfg.unsupported_measures.get(req.measure, {})
        if strict:
            names = sorted(m for m in cfg.unsupported_measures if contracts.base_measure(m)[0] == req.measure)
            if names:
                status = FAIL
                parts.append(f"declared under unsupported_measures ({', '.join(names)}): {strict_names} configs must map every contract measure; a declaration cannot satisfy it -- map it and delete the declaration")
        for form in req.forms:
            key = provided.get((req.measure, form))
            reason = declared.get(form, declared.get("*"))
            if key is not None:
                mp = cfg.risk_measures[key]
                fname = mp.bucketed if form == "bucketed" else mp.scalar
                parts.append(f"{form}: {fname} ({_spec(cfg, fname).unit}{'' if key == req.measure else f', via {key}'})")
            elif strict:
                status = FAIL
                parts.append(f"{form}: not mapped -- {strict_names} require a mapping for every contract measure (`measures.py block <config>` prints the paste-ready mapping skeleton) ({req.doc})")
            elif reason is not None:
                status = WARN
                if reason.strip().upper().startswith("TODO"):
                    parts.append(f"{form}: declared unsupported, but the declaration reason is a TODO ({reason!r}): replace it with the honest reason your library cannot compute it")
                else:
                    parts.append(f"{form}: declared unsupported: {reason}")
            else:
                status = FAIL
                parts.append(f"{form}: neither mapped nor declared -- map it, or declare `{req.measure}: \"<why your library cannot compute it>\"` under unsupported_measures ({req.doc})")
        slot = [p for p in res.problems if p.startswith(f"{req.measure} (") and "neither mapped nor declared" not in p and "require a mapping for every contract measure" not in p]
        if slot:
            status = FAIL
            parts += slot
        detail = "; ".join(parts) + (pnl_note(req.measure, inst) if status == WARN else "")
        out.append(CheckResult(f"contract[{req.measure}]", status, detail))
    forbidden = [p for p in res.problems if p.startswith("unsupported_measures declares")]  # strict classes only
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
    days = np.array([(b - a).days for a, b in zip(dates, dates[1:])], dtype=float)
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


def _notional(env: _Env) -> Optional[float]:
    n = env.ctx.kwargs.get(ca.SIZE_KWARG)
    return abs(float(n)) if isinstance(n, (int, float)) and n else None


def _strike(inst) -> Optional[float]:
    """The resolved fixed rate (swap) or strike (swaption), DECIMAL, or None."""
    terms = inst.resolved_terms
    k = terms.get("fixed_rate", terms.get("strike"))
    return float(k) if isinstance(k, (int, float)) and not isinstance(k, bool) else None


def _priced_date(env: _Env) -> date:
    """d2 when it has a market (usually off-market, so Price is not ~0), else d1."""
    return env.d2 if env.d2 != env.d1 and env.market_on(env.d2) else env.d1


def _per_notional_row(env: _Env, name: str, measure: str, source: str, why: str) -> List[CheckResult]:
    if not (env.has(measure) and env.has(source)):
        return [CheckResult(name, SKIP, f"needs {measure} and {source}")]
    n = _notional(env)
    if n is None:
        return [CheckResult(name, SKIP, f"no numeric {ca.SIZE_KWARG} kwarg")]
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
        parts.append(f"{side}{measure} {_fmt(got)} {unit} vs {source} {_fmt(src)} / |{ca.SIZE_KWARG}| {_fmt(n)} x {factor:g} = {_fmt(want)}")
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
    kwarg or resolved term, else the median spacing of the fixed leg's Cashflows payment dates."""
    for where, src in (("kwarg", env.ctx.kwargs), ("resolved term", env.r1.resolved_terms)):
        v = str(src.get("fixed_rate_frequency") or "").strip().lower()
        if v in _FREQ:
            return float(_FREQ[v]), f"{where} fixed_rate_frequency {v!r}"
    if env.has("Cashflows", "frame"):
        f = env.frame(env.r1, env.d1)
        if len(f) and "payment_type" in f:
            dates = sorted({_as_date(p) for p, t in zip(f["payment_date"], f["payment_type"]) if "fixed" in str(t).lower()})
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


def check_fake_constants(ctx, inst: str) -> List[CheckResult]:
    """R3-0: a contract measure mapped to a literal constant ('0.0', '{}', float('nan'), math.nan) is
    honest only where the contract defines the value as 0 (contracts.ZERO_BY_CONVENTION), and there only
    when the constant IS 0 (or {}); anything else is a placeholder."""

    from pricebt.risk import contracts

    name = "ir_fake_constant"
    if not contracts.is_strict(inst):
        return [CheckResult(name, SKIP, f"{inst} is not a strict class (a Bond may declare instead)")]
    allowed = contracts.ZERO_BY_CONVENTION.get(inst, frozenset())
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
            (fine if base in allowed and zero else bad).append(f"{key} -> {fname} = {expr!r}")
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
      + (STRICT_ROWS if contracts.is_strict(inst) else ()))


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


def _fold_mismatches(env: _Env, a, b, sign: float, d: date) -> List[str]:
    """Measures where b != sign * a (extensive) or b != a (intensive levels)."""
    bad = []
    for key, spec in env.scalar_measures():
        va, vb = env.val(a, d, key), env.val(b, d, key)
        want = sign * va if _is_amount(key, spec, env.inst) else va
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
    bad = [f"{label}: {m}" for label, (inst, sign) in cases.items() for m in _fold_mismatches(env, buy, inst, sign, env.d1)[:2]]
    if bad:
        return [CheckResult("bond_buy_sell_fold", FAIL, "; ".join(bad[:6]) + " -- fold buy_sell x sign(size) into one signed face amount in resolve")]
    return [CheckResult("bond_buy_sell_fold", PASS, "Sell = -Buy, -size = -Buy, Sell with -size = Buy for every extensive measure; levels equal")]


def row_bond_size(env: _Env) -> List[CheckResult]:
    size = env.ctx.kwargs.get("size")
    if not isinstance(size, (int, float)):
        return [CheckResult("bond_size_linearity", SKIP, "no numeric size kwarg")]
    one, two = env.resolve(size=size), env.resolve(size=2 * size)
    bad = _fold_mismatches(env, one, two, 2.0, env.d1)
    if bad:
        return [CheckResult("bond_size_linearity", FAIL, f"size x2: {bad[:5]} -- extensive measures must double, the yield and other levels must not move")]
    return [CheckResult("bond_size_linearity", PASS, f"size {_fmt(size)} -> {_fmt(2 * size)}: extensive measures double, levels unchanged")]


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


def bond_pack(ctx) -> List[CheckResult]:
    """Bond pack: gs Bond fields buy_sell, identifier, size; pricebt conventions: holder-signed, per
    +1bp of the yield (IRFwdRate), long bond IRDelta < 0."""
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
    ))
