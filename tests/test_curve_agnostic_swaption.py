"""docs/v2/SWAPTION_PROVIDER_REFACTOR_PROMPT.md at toy scale: the toy swaption config split into one
market provider per curve, one per currency returning {token: market}, and an asset config with no
market detail in it. Each instrument picks its curve (`curve='sofr:lch'`), may give its strike as a
delta (`strike='25d payer'`) and may be sized by risk (`dv01=`, `vega=`, `gamma=`), all in `resolve`,
which wraps the legacy resolve without editing it. `tools/parity_check.py` proves the split config
prices like the legacy one, labels included.
"""
from __future__ import annotations

import importlib.util
import math
from datetime import date
from pathlib import Path
from statistics import NormalDist

import pytest

import toylib.swaption as ts
from pricebt.assets import yamlio
from pricebt.errors import AssetEvaluationError
from pricebt.instrument import IRSwaption
from pricebt.markets import PricingContext
from pricebt.risk import Annuity, IRDelta, IRFwdRate, IRGammaParallel, IRVanna, IRVega
from pricebt.session import PricebtSession

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests" / "assets" / "toy_usd_swaption.yaml"
D = date(2024, 3, 4)
DELTA, VEGA, VANNA = IRDelta(aggregation_level="Type"), IRVega(aggregation_level="Type"), IRVanna(aggregation_level="Type")

# One provider per curve. `Market` stands for the wrapper class a real config defines around its
# library's environment: the provider owns the class, so asset code never names it. It reaches a
# derived market with `m.derive(env)` and the curve's identifiers with `m.pricebt_ids`.
_PROVIDER_CODE = '''
class Market:
    def __init__(self, env):
        self.env = env

    def derive(self, env):
        """The same class and identifiers on a derived (bumped, rolled) environment."""
        other = type(self)(env)
        other.pricebt_ids = self.pricebt_ids
        return other


def record(env, location, **ids):
    if location != "NYC":
        raise ValueError(f"no snapshot rule for market data location {location!r}")
    if env is None:
        return None
    m = Market(env)
    m.pricebt_ids = SimpleNamespace(**ids)
    return m


def sloped(d, csa):
    curve = tr.market_sloped(d, "USD", csa, 0.0005)
    return None if curve is None else ts.market_on(curve)
'''
_PROVIDER_IMPORTS = "from types import SimpleNamespace\nimport toylib.rates as tr\nimport toylib.swaption as ts"

_ASSET_CODE = r'''
_DELTA = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*d\s*(payer|pay|p|receiver|receive|rec|r)?\s*$", re.I)
_SIZE_BY = {"dv01": ts.delta, "vega": ts.vega, "gamma": ts.gamma}  # keyword -> the function mapped to IRDelta / IRVega / IRGammaParallel


def curve_of(market, terms):
    """The market of the curve a trade prices on. `market` is the currency provider's dict."""
    key = terms["curve"]
    if key not in market:
        raise ValueError(f"no curve {key!r} in this market; available: {sorted(market)}")
    return market[key]


def _solve(f, x0, falling, widths=(0.0025, 0.005, 0.01, 0.02, 0.04, 0.08, 0.16), tol=1e-8):
    """The root of the monotone function f nearest x0. `falling`: f falls as x rises. The bracket
    grows from x0 on the side the root is on, then bisection."""
    lo, flo = x0, f(x0)
    if flo == 0.0:
        return x0
    step = 1.0 if (flo > 0.0) == falling else -1.0
    for width in widths:
        hi = x0 + step * width
        fhi = f(hi)
        if fhi == 0.0:
            return hi
        if (fhi > 0.0) != (flo > 0.0):
            break
        lo, flo = hi, fhi
    else:
        raise ValueError(f"no root within {widths[-1]} of {x0}")
    while abs(hi - lo) > tol:
        mid = 0.5 * (lo + hi)
        fmid = f(mid)
        if (fmid > 0.0) == (flo > 0.0):
            lo, flo = mid, fmid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _delta_fraction(m, terms, strike, leg):
    """|IRDelta| / (|Annuity| / 1e4) of one leg struck at `strike`: the annuity-normalised forward delta."""
    probe = {**terms, "strike": strike, "pay_or_receive": leg}
    return abs(ts.delta(m.env, probe)) / abs(ts.annuity(m.env, probe)) * 1e4


def _delta_strike(m, terms, alias):
    """The strike at which the named leg has the delta of '25d', '25d payer' or '25d receiver'."""
    target = float(alias.group(1)) / 100.0
    if not 0.0 < target < 1.0:
        raise ValueError(f"a delta strike is between 0d and 100d, got {alias.group(0)!r}")
    named = (alias.group(2) or "").lower()
    if named:
        leg = "Pay" if named.startswith("p") else "Receive"
    elif len(ts._legs(terms["pay_or_receive"])) == 1:
        leg = terms["pay_or_receive"]
    else:
        raise ValueError("a delta strike on a straddle must name the leg: '25d payer' or '25d receiver'")
    payer = ts._legs(leg) == (True,)  # a payer's delta falls as the strike rises, a receiver's rises
    return _solve(lambda k: _delta_fraction(m, terms, k, leg) - target, terms["strike"], falling=payer)


def _size_scale(m, terms, targets):
    """What to multiply the notional by so the trade has the one risk target given."""
    if len(targets) != 1:
        raise ValueError(f"size by one of {sorted(_SIZE_BY)}, got {sorted(targets)}")
    ((name, target),) = targets.items()
    if not float(target) > 0.0:
        raise ValueError(f"{name} must be positive (the direction comes from buy_sell and pay_or_receive), got {target!r}")
    if name == "dv01" and len(ts._legs(terms["pay_or_receive"])) == 2:
        raise ValueError("a straddle is sized by vega or gamma, not by dv01")
    risk = _SIZE_BY[name](m.env, terms)  # at the notional the terms already have
    if not risk:
        raise ValueError(f"cannot size by {name}: the trade has none")
    return abs(float(target) / risk)


def resolve(market, kw):
    kw = dict(kw)
    key = str(kw["curve"]).strip().lower()
    m = curve_of(market, {"curve": key})
    targets = {k: kw.pop(k) for k in _SIZE_BY if kw.get(k) is not None}
    alias = _DELTA.match(kw["strike"]) if isinstance(kw.get("strike"), str) else None
    if alias:
        kw["strike"] = "ATM"  # the legacy resolve pins the dates and the forward; the strike is solved below
    terms = ts.resolve_swaption(m.env, kw)  # the legacy resolve, not edited
    terms["curve"] = key
    if alias:
        terms["strike"] = _delta_strike(m, terms, alias)
    if targets:
        terms["notional"] *= _size_scale(m, terms, targets)
    return terms


def vanna(m, trade):
    """The legacy central difference, on two derived markets."""
    up, dn = (m.derive(SimpleNamespace(curve=m.env.curve, sigma=m.env.sigma + h)) for h in (ts.H_VOL, -ts.H_VOL))
    return (ts.delta(up.env, trade) - ts.delta(dn.env, trade)) / 2.0 if up.pricebt_ids is m.pricebt_ids else float("nan")


def by_curve(market, trades, weights):
    """[(the curve's market, its trades, their weights)], curves in order of first appearance."""
    groups = {}
    for trade, weight in zip(trades, weights):
        group = groups.setdefault(trade["curve"], ([], []))
        group[0].append(trade)
        group[1].append(weight)
    return [(curve_of(market, {"curve": key}), group, w) for key, (group, w) in groups.items()]


def ladder_rows(market, trades, weights, ladder, *args):
    """A {point: value} ladder computed per curve, as rows carrying that curve's own mkt_asset."""
    return [
        {"mkt_asset": m.pricebt_ids.mkt_asset, "mkt_point": point, "value": value}
        for m, group, w in by_curve(market, trades, weights)
        for point, value in ladder(m.env, group, w, *args).items()
    ]


def explain_rows(market, market_to, trades, weights, d):
    return [
        row
        for m, group, w in by_curve(market, trades, weights)
        for row in ts.pnl_explain(m.env, curve_of(market_to, group[0]).env, group, w, d)
    ]
'''


def _providers(cme_asset="TOY-USD"):
    extra = {"imports": _PROVIDER_IMPORTS, "code": _PROVIDER_CODE}
    return [
        {"schema_version": 1, "market": "usd_sofr_cme_vol", "expr": f'record(ts.market(pricebt_date, "USD", pricebt_csa), pricebt_location, mkt_asset="{cme_asset}")', **extra},
        {"schema_version": 1, "market": "usd_sofr_lch_vol", "expr": 'record(sloped(pricebt_date, pricebt_csa), pricebt_location, mkt_asset="TOY-USD-LCH")', **extra},
        {
            "schema_version": 1,
            "market": "usd_rates_vol",
            "requires": ["usd_sofr_cme_vol", "usd_sofr_lch_vol"],
            "expr": '{"sofr:cme": markets["usd_sofr_cme_vol"], "sofr:lch": markets["usd_sofr_lch_vol"]}',
        },
    ]


def _asset() -> dict:
    """The legacy toy config with every `market` argument replaced by the curve's market, the three
    ladders returning rows, and no curve-specific label left in the YAML."""
    raw = dict(yamlio.load_file(LEGACY))
    here = "curve_of(market, resolved)"
    raw["asset"] = "usd_interest_rate_swaption"
    raw["defaults"] = {**raw["defaults"], "curve": "sofr:cme"}
    raw["imports"] = "import re\nfrom types import SimpleNamespace\nimport toylib.swaption as ts"
    raw["code"] = _ASSET_CODE
    raw["market"] = {"provider": "usd_rates_vol"}
    raw["resolve"] = {"expr": "resolve(market, kwargs)"}
    functions = {k: {**v, "expr": v["expr"].replace("(market, trade", f"({here}.env, trade")} for k, v in raw["functions"].items()}
    functions["vanna"] = {**functions["vanna"], "expr": f"vanna({here}, trade)"}
    raw["functions"] = functions
    portfolio = {k: dict(v) for k, v in raw["portfolio_functions"].items()}
    for name in ("delta_ladder", "gamma_ladder", "vega_cube"):
        legacy = portfolio[name]["expr"]
        assert legacy.startswith(f"ts.{name}(market, trades, weights, ")
        portfolio[name]["expr"] = legacy.replace(f"ts.{name}(market, trades, weights, ", f"ladder_rows(market, trades, weights, ts.{name}, ")
        portfolio[name]["labels"] = {k: v for k, v in portfolio[name]["labels"].items() if k != "mkt_asset"}
    portfolio["pnl_explain"]["expr"] = "explain_rows(market, market_to, trades, weights, pricebt_date)"
    raw["portfolio_functions"] = portfolio
    return raw


@pytest.fixture
def session():
    PricingContext.current = PricingContext(market_data_location="NYC")  # the providers answer to NYC only; conftest restores the default
    return PricebtSession.use(markets=_providers(), assets=[_asset()])


def _option(pay_or_receive="Pay", **kwargs):
    return IRSwaption(pay_or_receive, "10y", "USD", **{"expiration_date": "1y", **kwargs})


def _terms(option, d=D):
    with PricingContext(d):
        resolved = option.resolve(in_place=False)
    return resolved.result().resolved_terms


def _calc(option, *measures, d=D):
    with PricingContext(d):
        futures = [option.calc(m) for m in measures]
    return [float(f.result()) for f in futures]


# ------------------------------------------------------------------------------------ parity with the legacy config


@pytest.fixture
def parity():
    spec = importlib.util.spec_from_file_location("parity_check", ROOT / "tools" / "parity_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OLD = dict(assets=[LEGACY])
    module.NEW = dict(markets=_providers(), assets=[_asset()])
    module.IGNORED_RESOLVED_KEYS = {"curve"}
    module.LOCATION = "NYC"
    module.CASES = [
        ("payer ATM", lambda: _option(), lambda: _option()),
        ("sold receiver ATM+25", lambda: _option("Receive", strike="ATM+25", buy_sell="Sell"), lambda: _option("Receive", strike="ATM+25", buy_sell="Sell")),
        ("straddle at 4%", lambda: _option("Straddle", strike=0.04, notional_amount=5e6), lambda: _option("Straddle", strike=0.04, notional_amount=5e6)),
    ]
    return module


def test_the_split_config_prices_like_the_legacy_one(parity, capsys):
    assert parity.main() == 0
    out = capsys.readouterr().out
    assert "0 difference(s)" in out and "note" not in out  # nothing compared only as an error


def test_parity_reads_a_ladder_as_labelled_rows_and_reports_a_wrong_label(parity, capsys):
    """The legacy ladder is {point: value} under a static mkt_asset; the split one returns rows
    carrying the provider's. Equal when the label is the same, a difference when it is not."""
    parity.NEW = dict(markets=_providers(cme_asset="TOY-EUR"), assets=[_asset()])
    assert parity.main() == 1
    out = capsys.readouterr().out
    assert "/portfolio_functions.delta_ladder[0]/mkt_asset: OLD 'TOY-USD' != NEW 'TOY-EUR'" in out
    assert "/functions." not in out


def test_parity_needs_the_location_the_providers_answer_to(parity):
    parity.LOCATION = None
    with pytest.raises(AssetEvaluationError, match="no snapshot rule for market data location 'LDN'"):
        parity.main()


# ------------------------------------------------------------------------------------ curve and location


def test_the_curve_is_chosen_per_instrument_and_pinned(session):
    cme, lch = _option(), _option(curve="SOFR:LCH")
    assert _calc(cme, IRFwdRate) != _calc(lch, IRFwdRate)
    assert _terms(cme)["curve"] == "sofr:cme" and _terms(lch)["curve"] == "sofr:lch"
    with pytest.raises(AssetEvaluationError) as exc_info:
        _terms(_option(curve="sofr:eurex"))
    assert "no curve 'sofr:eurex' in this market; available: ['sofr:cme', 'sofr:lch']" in str(exc_info.value.__cause__)


def test_a_provider_raises_for_a_location_it_has_no_snapshot_rule_for(session):
    with PricingContext(D, market_data_location="LDN"), pytest.raises(AssetEvaluationError) as exc_info:
        _option().price()
    assert "no snapshot rule for market data location 'LDN'" in str(exc_info.value.__cause__)


def test_a_ladder_keeps_trades_on_different_curves_apart(session):
    with PricingContext(D):
        rows = session.pricing.portfolio_value(session.registry["usd_interest_rate_swaption"], D, "delta_ladder", [_option(), _option(curve="sofr:lch")], None)
    live = [(r["mkt_asset"], r["mkt_point"]) for r in rows if r["value"]]
    assert live == [("TOY-USD", "10Y"), ("TOY-USD-LCH", "10Y")] and len(rows) == 8  # four pillars per curve, never summed


def test_a_derived_market_keeps_the_providers_class_and_identifiers(session):
    """The asset's vanna bumps the vol through `m.derive`; equal to the library's own means the
    derived markets priced, and carried the identifiers (the asset returns NaN otherwise)."""
    option = _option()
    market = ts.market(D, "USD")
    assert _calc(option, VANNA) == [ts.vanna(market, _terms(option))]


# ------------------------------------------------------------------------------------ strike aliases


def _delta_fraction(option):
    delta, annuity = _calc(option, DELTA, Annuity)
    return delta / (annuity / 1e4)


@pytest.mark.parametrize("curve", ["sofr:cme", "sofr:lch"])
def test_a_delta_strike_gives_the_named_leg_that_delta(session, curve):
    payer, receiver = _option(strike="25d", curve=curve), _option("Receive", strike="25D", curve=curve)
    assert _delta_fraction(payer) == pytest.approx(0.25, abs=1e-5)
    assert _delta_fraction(receiver) == pytest.approx(-0.25, abs=1e-5)
    forward = _terms(_option(curve=curve))["strike"]
    assert _terms(receiver)["strike"] < forward < _terms(payer)["strike"]
    assert isinstance(_terms(payer)["strike"], float)  # pinned as a number, not the alias


def test_a_delta_strike_is_near_the_bachelier_closed_form(session):
    """K = F + sigma sqrt(T) inv_cdf(1 - delta) for a payer. The toy's IRDelta is a total
    derivative (the annuity moves with the curve), so it is close, not equal."""
    atm, otm = _terms(_option()), _terms(_option(strike="25d payer"))
    width = ts._sigma(D, "USD") * math.sqrt((atm["expiration_date"] - D).days / 365.0)
    assert otm["strike"] == pytest.approx(atm["strike"] + width * NormalDist().inv_cdf(0.75), abs=5e-4)
    assert _delta_fraction(_option()) == pytest.approx(0.5, abs=0.05)  # the known answer the prompt gates on


def test_a_straddle_takes_the_strike_of_the_leg_it_names(session):
    straddle, payer = _terms(_option("Straddle", strike="30d receiver")), _terms(_option("Receive", strike="30d"))
    assert straddle["strike"] == payer["strike"]
    with pytest.raises(AssetEvaluationError) as exc_info:
        _terms(_option("Straddle", strike="30d"))
    assert "must name the leg" in str(exc_info.value.__cause__)
    with pytest.raises(AssetEvaluationError) as exc_info:
        _terms(_option(strike="100d"))
    assert "between 0d and 100d" in str(exc_info.value.__cause__)


def test_the_legacy_strike_spellings_are_untouched(session):
    atm, shifted, fixed = (_terms(_option(strike=s))["strike"] for s in ("ATM", "a+25", 0.035))
    assert shifted == pytest.approx(atm + 0.0025, abs=1e-15) and fixed == 0.035
    with pytest.raises(AssetEvaluationError) as exc_info:
        _terms(_option(strike="cheap"))
    assert "unsupported strike 'cheap'" in str(exc_info.value.__cause__)


# ------------------------------------------------------------------------------------ sizing by risk


@pytest.mark.parametrize(
    "keyword, measure, option, sign",
    [
        ("vega", VEGA, dict(pay_or_receive="Straddle"), 1.0),
        ("vega", VEGA, dict(pay_or_receive="Straddle", buy_sell="Sell"), -1.0),
        ("gamma", IRGammaParallel, dict(pay_or_receive="Straddle", strike="25d payer"), 1.0),
        ("dv01", DELTA, dict(pay_or_receive="Pay", strike="25d"), 1.0),
        ("dv01", DELTA, dict(pay_or_receive="Receive", curve="sofr:lch"), -1.0),
        ("dv01", DELTA, dict(pay_or_receive="Receive", buy_sell="Sell"), 1.0),
    ],
)
def test_a_trade_sized_by_risk_has_that_risk_on_its_trade_date_and_keeps_its_notional(session, keyword, measure, option, sign):
    sized = _option(**option, **{keyword: 50_000})
    assert _calc(sized, measure) == pytest.approx([sign * 50_000.0], rel=1e-9)
    with PricingContext(D):
        resolved = sized.resolve(in_place=False).result()
    notional = resolved.resolved_terms["notional"]
    assert notional != pytest.approx(sign * 1e6) and resolved.resolved_terms["strike"] == _terms(_option(**option))["strike"]
    with PricingContext(date(2024, 6, 3)):
        later = float(resolved.calc(measure).result())
        assert resolved.resolve(in_place=False).result().resolved_terms["notional"] == notional  # pinned, not re-solved
    assert later != pytest.approx(sign * 50_000.0, rel=1e-6)  # the risk drifts; the size does not


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(vega=1e4, gamma=1e2), "size by one of ['dv01', 'gamma', 'vega'], got ['gamma', 'vega']"),
        (dict(vega=-1e4), "vega must be positive"),
        (dict(pay_or_receive="Straddle", dv01=1e4), "a straddle is sized by vega or gamma, not by dv01"),
    ],
)
def test_a_size_that_cannot_be_met_is_refused(session, kwargs, message):
    with pytest.raises(AssetEvaluationError) as exc_info:
        _terms(_option(**kwargs))
    assert message in str(exc_info.value.__cause__)


# ------------------------------------------------------------------------------------ the config checker


def test_check_asset_prices_under_the_location_it_is_given(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "skills" / "pricebt-verify-asset-config" / "scripts"))
    import check_asset

    run = lambda **kw: check_asset.run_checks(_asset(), markets=_providers(), dates=[D, date(2024, 3, 5)], backtest=False, **kw)  # noqa: E731
    assert not [r for r in run(location="NYC") if r.status == check_asset.FAIL]
    assert [r.name for r in run() if r.status == check_asset.FAIL] == ["market_available"]
