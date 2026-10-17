# Prompt: move a swaption config's market into market providers; curve, strike alias and risk size per instrument

A hand-off prompt for an AI coding agent that works where your pricing library is installed. It
refactors one existing swaption asset config (market loading, trade building and pricing in one
file) into market provider configs plus an asset config that holds no market-data detail, and adds
three things each instrument can then say for itself:

```python
IRSwaption("Pay", "10y", "USD", expiration_date="1y", curve="sofr:lch", strike="25d payer", vega=50_000)
```

The swap version is [`MARKET_PROVIDER_REFACTOR_PROMPT.md`](MARKET_PROVIDER_REFACTOR_PROMPT.md); the
design is [`MARKET_CONTEXT_DESIGN.md`](MARKET_CONTEXT_DESIGN.md). Everything this prompt asks for
runs on the toy library in `tests/test_curve_agnostic_swaption.py`, which the agent is told to read
as its working example. The checker it uses is [`tools/parity_check.py`](../../tools/parity_check.py).

What shapes it (all verified in this repository):

- **The currency is already per instrument.** `IRSwaption(..., "EUR", ...)` is routed to the asset
  config whose `match:` names EUR. An asset config has one fixed `currency:` and one fixed
  `market.provider`, so there is one asset file per currency with an identical body.
- **Keywords pricebt does not know pass through to `resolve`**: `curve=`, `dv01=`, `vega=`,
  `gamma=`. A `strike` string is passed through unchanged too, whatever it says.
- **gs spellings are kept.** gs writes `'ATM'`, `'ATM+50'`, `'a-100'`, `'ATMF-25'`, a number and
  `'25d'`. `'25d payer'` and `'25d receiver'` are additions that name the leg. The gs premium form
  (`'10000/pv'`) is not built.
- **`resolve` wraps the old resolve and does not edit it.** A delta strike is solved after the old
  resolve has pinned the dates and the forward; the size is solved last. So the old strike
  spellings cannot change.
- **A delta strike and a risk size are solved with the config's own functions** (the ones mapped to
  `IRDelta`, `Annuity`, `IRVega`, `IRGammaParallel`), so the prompt needs no library call.
- **A market that is one object holding curves and vols stays one provider.** Splitting it into a
  curve provider and a vol provider would mean rewriting library calls.
- **The prompt names no library.** The file is the source of truth; code is moved, not rewritten;
  identifiers of any other curve are asked for, never guessed.

Fill in section 0, then give the agent everything below the line.

---

# Task: split a pricebt swaption asset config into market providers plus a curve-agnostic asset config

## 0. Filled in by the user (ask once if any line is blank)

- Repository root: `<path>`
- How to run Python here (interpreter, PYTHONPATH, anything the pricing library needs): `<command>`
- Config to refactor: `configs/assets/<file>.yaml`
- Its currency, and the curve it prices on today as `index:clearing_house`: `<CCY>`, `<index>:<clearing house>`
- Market data location of its snapshot (`NYC`, `LDN`, `HKG`, `TKO`), or `any` to keep today's
  behaviour (the location is ignored): `<location>`
- Three or more consecutive business dates known to have market data: `<dates>`
- Delta-strike convention, if not the default of step 6: `<default>`

The examples below use USD, `sofr:<ch>` and `NYC`. Substitute the values above everywhere.

## 1. Goal

The config above does three jobs in one file: it loads the dated market, it builds the trade, and
it prices and risks the trade. Change it so that:

1. **Market data lives in market provider configs** (`configs/markets/*.yaml`). The asset config
   names a provider and contains no currency, index, curve, funding or discount name, no calendar,
   no file or cache location and no market label.
2. **The curve is chosen per instrument** with a `curve` keyword: a token `index:clearing_house`.
3. **The strike takes aliases**: every spelling the config accepts today, plus a delta strike
   (`'25d'`, `'25d payer'`, `'25d receiver'`).
4. **A trade can be sized by risk**: `dv01=`, `vega=` or `gamma=` instead of `notional_amount`.
5. **Numbers do not change.** For the existing curve and today's strike spellings the refactored
   config reproduces the current one exactly. This is checked with `tools/parity_check.py`, not by
   inspection.

Target usage:

```python
PricingContext.current = PricingContext(market_data_location="NYC")   # not needed when section 0 says `any`
PricebtSession.use(markets=["configs/markets"], assets=["configs/assets"])
IRSwaption("Straddle", "10y", "USD", expiration_date="1y", curve="sofr:<ch>", strike="ATMF+25", vega=50_000)
IRSwaption("Pay", "10y", "USD", expiration_date="3m", strike="25d", dv01=10_000)
```

The currency is the third argument. It routes the instrument to the asset file whose `match:`
names that currency; nothing new is needed for it.

## 2. Before you change anything

1. Confirm the checkout has what this task needs. All three commands must succeed; if one fails,
   stop and tell the user to check out a branch that has them:
   - `python -c "from pricebt.assets import load_market, MarketRegistry"`
   - `python tools/parity_check.py` prints `216 values compared, 0 difference(s)`
   - `python skills/pricebt-verify-asset-config/scripts/check_asset.py --help` lists `--location`
2. Read, in this order:
   - `configs/markets/README.md`
   - `docs/v2/MARKET_CONTEXT_DESIGN.md` sections 3, 4, 5 and 10
   - `docs/v2/ASSET_CONFIG_GUIDE.md`, sections "Injected variables" and "Market providers"
   - `tests/test_curve_agnostic_swaption.py`, all of it. **This is your working example**: the
     whole task done on a toy library, with tests. `_PROVIDER_CODE` is a curve provider,
     `_ASSET_CODE` is the asset code, `_asset()` shows what changes in the YAML. When this prompt
     and that file seem to disagree, stop and ask.
   - the whole config to refactor, top to bottom
3. **This repository's remote is public and the config is private.** Do not `git add`, commit or
   push the config, the new provider files, your copy of the parity script or any output. Do not
   copy library identifiers into any tracked file.

## 3. Framework facts you must design around

These are how pricebt works. Do not try to work around them and do not edit anything under
`src/pricebt`.

- An asset config has **one fixed `currency:`** and **one fixed `market: {provider: <name>}`**.
  So there is one asset file per currency. Everything below its header must be identical across
  currencies; only the header differs.
- A provider's `expr` sees only `pricebt_date`, `pricebt_timestamp`, `pricebt_datetime`,
  `pricebt_csa`, `pricebt_location` and `markets`. It never sees an instrument or an asset.
- A provider is evaluated once per (provider, date, csa, location) and its object is shared by
  every asset that names it. `imports:` and `code:` of a config run once per process, in that
  config's own namespace: **a class or function defined in a provider does not exist in the asset
  config**, and a class written in both files is two different classes.
- `requires:` is all-or-nothing: if any required provider returns `None` for a date, the provider
  that requires it has no market for that date.
- `None` from a provider means "no market on this date" (a backtest drops the date). An exception
  means a real error. Keep that distinction exactly as the current market-loading function has it.
- Keywords pricebt does not know (`curve=`, `dv01=`, `vega=`, `gamma=`) reach `resolve` unchanged
  in `kwargs`, and so does a `strike` string. `defaults:` fills what the instrument leaves out.
- `resolve` runs once, on the trade date, and its output is the trade's terms for life. It must
  return plain data only (str, int, float, bool, None, date, datetime, or a tuple of those). It
  sees `market`, `kwargs`, `pricebt_date` and the other `pricebt_*` names of a provider; it does
  **not** see the parameters of a measure (a bump size, a finite-difference method).
- A `portfolio_functions:` entry with `returns: buckets` may return `{mkt_point: value}` or a list
  of rows `{"mkt_asset": ..., "mkt_point": ..., "value": ...}`. A row's own coordinates win; the
  static `labels:` of the entry fill the ones a row leaves out.
- The clearing house is chosen by the `curve` token only. Do not use `pricebt_csa` for it.

## 4. Target layout

```
configs/markets/usd_sofr_<ch>_vol.yaml           one provider per curve token: loads that token's dated market
configs/markets/usd_rates_vol.yaml               one provider per currency: requires its tokens, returns {token: market}
configs/assets/usd_interest_rate_swaption.yaml   asset: usd_interest_rate_swaption, market: {provider: usd_rates_vol}
configs/_legacy/<original file name>             frozen copy of today's file (the parity reference)
```

Naming, lower case: token providers are `<ccy>_<index>_<clearing house>_vol`, the currency
provider is `<ccy>_rates_vol`, a token is `<index>:<clearing house>`.

**One token, one provider, one market object.** If today's market-loading function returns one
object that holds the curves and the vols together, that object is the market of one provider. Do
not split it into a curve provider and a vol provider. If `configs/markets/` already holds
providers for another asset (a swap), leave them alone and do not `require` them.

**The market object and its identifiers.** A token provider returns exactly the object today's
market-loading function returns, with two additions (section 7 shows the code):

- an attribute `pricebt_ids`: a `types.SimpleNamespace` with one attribute for **every literal in
  today's file that is specific to this currency or this curve**. Find them all yourself; the file
  is the source of truth. Places they hide:
  - arguments of library calls in `code:` (a currency code, an index, a funding or discount name);
  - **default values of function parameters** (a holiday calendar);
  - module-level constants, including ones read from an environment variable;
  - **expression strings** under `functions:` (a curve looked up by currency, a constant passed to
    a library call);
  - rows built in code (a CRIF row, a P&L-explain row) and static `labels:` of
    `portfolio_functions:` (`mkt_asset`);
  - the market loading itself: a market definition file, a cache folder, a cache file name.
  Name each attribute after what it feeds (`ccy`, `calendar`, `funding`, `mkt_asset`, ...).
- a method `derive(env)` on its class: the same class and the same `pricebt_ids` around a derived
  (bumped, rolled) environment.

Today's market object should be an instance of a small wrapper class defined in the config's own
`code:`. If instead the loading function returns a library object directly, stop: use the record
shape of `MARKET_PROVIDER_REFACTOR_PROMPT.md` section 4 and tell the user.

The currency provider returns a plain dict `{"sofr:<ch>": <market object>, ...}`.

## 5. Hard rules

1. **Do not change any library call.** Same function, same arguments, same order, same constants,
   same signs, same unit conversions. You may only: move code between files; replace a literal by
   `m.pricebt_ids.<attribute>`; make the two class rewrites of step 4; add the `curve`, strike
   alias and risk-size features exactly as steps 4, 6 and 7 describe.
2. **Do not edit the body of today's resolve function**, apart from replacing literals. The new
   features go in a new function that calls it (step 4).
3. **Do not invent library identifiers.** You know the identifiers of one curve only: the ones
   already in the file. For any other curve, stop and ask the user (step 8).
4. Keep every entry of `risk_measures:`, `functions:`, `portfolio_functions:`, `attributes:`,
   `resolve:` and `trade:` (including `build_on`). Only their expressions' arguments change.
5. Do not change tolerances or expected values to make a check pass. If a gate fails and you
   cannot find the cause in two attempts, stop and report the differing values.
6. Do not edit anything under `src/pricebt`, `tests/`, `tools/` or `skills/`.

## 6. Steps (do them in order; each ends with a gate)

### Step 1. Freeze the reference and prove the checker works

1. Copy today's config, unchanged, to `configs/_legacy/` (same file name).
2. Copy `tools/parity_check.py` to `scratch/parity_check.py` and edit the copy:
   - add `from pricebt.instrument import IRSwaption`;
   - `OLD` and `NEW` both load the legacy copy;
   - `DATES` are the user's dates;
   - `LOCATION` is the location of section 0 (`None` for `any`);
   - `CASES` cover: a straddle with every default; a bought payer and a sold receiver; the
     at-the-money spelling, at-the-money plus and minus an offset, and a number as strike; two
     different expiries and two different underlying tenors. Each case is
     `(label, factory for OLD, factory for NEW)`; both factories are the same lambda for now.
3. **Gate:** it reports 0 differences. Then prove it can fail: in a temporary copy of the config
   used as `NEW`, multiply one function's expression by `1.0001`; the script must report
   differences and exit 1. Delete the temporary copy.

If the script prints a `note ... OLD raises for [...]` line, report it: a function that already
fails in the legacy config is compared only by its error type, so it is not really covered.

### Step 2. Write the token provider (guide: section 7)

Create `configs/markets/usd_sofr_<ch>_vol.yaml`. Move into it, unchanged: the market-loading
function, the wrapper class, and every helper and constant they use.

### Step 3. Write the currency provider

```yaml
schema_version: 1
market: usd_rates_vol
description: "USD swaption markets by curve token."
requires: [usd_sofr_<ch>_vol]
expr: '{"sofr:<ch>": markets["usd_sofr_<ch>_vol"]}'
```

### Step 4. Refactor the asset config

Work on a copy named `configs/assets/usd_interest_rate_swaption.yaml`; then delete the original
from `configs/assets/` (the legacy copy stays). Header:

```yaml
asset: usd_interest_rate_swaption
instrument: IRSwaption
match: {notional_currency: USD}
currency: USD
defaults: {<today's defaults, unchanged>, curve: "sofr:<ch>"}
market: {provider: usd_rates_vol}
```

**`imports:`** stays as it is (section 7 rule 2 says why).

**`code:`**, in this order:

1. Delete what moved to the provider: the market-loading function, the wrapper class, their
   helpers and constants.
2. Replace every literal of section 4 by `m.pricebt_ids.<attribute>`, where `m` is the market
   object the function already receives. A literal that is a parameter's default value: make the
   default `None` and read the identifier inside the function (`calendar or m.pricebt_ids.calendar`).
   A module-level constant that is an identifier: delete it and read the attribute where it was
   used. A function that uses a literal but receives no market object: add `m` as a parameter and
   pass it from its callers.
3. The wrapper class now lives in the provider, so the asset code can no longer name it. Two
   rewrites, wherever they occur, and no others:
   - `<WrapperClass>(x)` becomes `m.derive(x)`, with `m` the market object that function has;
   - `isinstance(m, <WrapperClass>)` becomes `hasattr(m, "pricebt_ids")`.
   Everything else that uses the object (`m.env`, its bump and roll methods) stays as written.
4. Add the curve lookup and the new resolve. `<legacy resolve>` is today's resolve function,
   called with the arguments it has today except that its market argument is `m`:

   ```python
   def curve_of(market, terms):
       """The market of the curve a trade prices on. `market` is the currency provider's dict."""
       key = terms["curve"]
       if key not in market:
           raise ValueError(f"no curve {key!r} in this market; available: {sorted(market)}")
       return market[key]

   def resolve(market, kw, d):
       kw = dict(kw)
       key = str(kw["curve"]).strip().lower()
       m = curve_of(market, {"curve": key})
       terms = <legacy resolve>(m, kw, d)
       terms["curve"] = key
       return terms
   ```
   Steps 6 and 7 add to this function. In the YAML: `resolve: {expr: 'resolve(market, kwargs, pricebt_date)'}`.

**Expressions.** In `functions:`, `trade:` and `attributes:`, replace the argument `market` by
`curve_of(market, resolved)`. An expression that itself holds a library call or a literal: move it,
unchanged, into a function in `code:` that takes `(m, trade, resolved, d)`, replace its literals
there, and call that function from the expression.

**`portfolio_functions:`** receive `trades` and `weights`, not `resolved`, and the trades of one
call **may be on different curves**. Compute per curve and return rows. `terms_of(trade)` is the
resolved terms today's trade-building function already attaches to each trade (if it attaches
none, stop and ask):

```python
def by_curve(market, trades, weights):
    """[(the curve's market, its trades, their weights)], curves in order of first appearance."""
    groups = {}
    for trade, weight in zip(trades, weights):
        group = groups.setdefault(terms_of(trade)["curve"], ([], []))
        group[0].append(trade)
        group[1].append(weight)
    return [(curve_of(market, {"curve": key}), group, w) for key, (group, w) in groups.items()]

def ladder_rows(market, trades, weights, ladder, *args):
    """A {point: value} ladder computed per curve, as rows carrying that curve's own mkt_asset."""
    return [
        {"mkt_asset": m.pricebt_ids.mkt_asset, "mkt_point": point, "value": value}
        for m, group, w in by_curve(market, trades, weights)
        for point, value in ladder(m, group, w, *args).items()
    ]
```

- A ladder or cube expression `f(market, trades, weights, pricebt_date)` becomes
  `ladder_rows(market, trades, weights, f, pricebt_date)`. `f` itself is not edited.
- In the YAML `labels:` of each such entry, delete the coordinates that now come from the rows
  (`mkt_asset`); keep the others (`mkt_type`, `mkt_class`). If two entries need different
  `mkt_asset` values today, give `pricebt_ids` one attribute for each.
- A function that compares two markets (`market` and `market_to`): call today's function once per
  group with `m` and `curve_of(market_to, terms_of(group[0]))`, and concatenate the rows.

**Gate A (text).** Below the header, the asset file contains none of the literals you moved.
Search for each one; every search must find nothing. List the searches in your report.

**Gate B (numbers).** In `scratch/parity_check.py` set
`NEW = dict(markets=["configs/markets"], assets=["configs/assets/usd_interest_rate_swaption.yaml"])`,
keep `OLD` = the legacy copy, and set `IGNORED_RESOLVED_KEYS = {"curve"}`. It reports
0 differences. The script compares a ladder as labelled rows, so a wrong or missing `mkt_asset`
shows up as a difference.

**Gate C (checker).**
`python skills/pricebt-verify-asset-config/scripts/check_asset.py configs/assets/usd_interest_rate_swaption.yaml --markets configs/markets --location NYC --date <d1> --date <d2>`
gives every row the same status as the same command on the legacy copy (without `--markets`).
Leave out `--location` when section 0 says `any`.

### Step 5. Curve selection

Check and report: `curve="SOFR:<CH>"` (upper case) resolves to the lower-case token; an unknown
token raises the `ValueError` above listing the available tokens; with no `curve` keyword the
default is used.

### Step 6. Strike aliases

| `strike=` | Meaning |
|---|---|
| a number | that strike, exactly as today |
| every at-the-money spelling the legacy resolve accepts, with or without an offset | exactly as today: the legacy resolve handles it |
| `'25d'`, `'25d payer'`, `'25d receiver'` (any number between 0 and 100; `p`, `pay`, `r`, `rec` also name the leg; case does not matter) | the strike at which that leg has a 25% delta |

**Check first:** with the plain at-the-money spelling, the legacy resolve must set the strike term
to the forward rate of the underlying. Read the code and confirm it. If it does not, stop and tell
the user.

**Convention (the default of section 0).** The delta of a leg is
`|function mapped to the IRDelta scalar| / (|function mapped to Annuity| / 1e4)`, for one leg, at
the default bump. `IRDelta` is in currency per basis point and `Annuity` is in currency, hence the
`1e4`. Without a leg word the leg is the trade's own; a straddle must name the leg, and both its
legs take the strike found.

Add to `code:`. `_solve` is copied as it is. In the other two, `<...>` are the config's own
functions and spellings. Call each function the way its expression under `functions:` calls it,
without the measure parameters (a bump size, a method), so that its defaults apply:

```python
_DELTA = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*d\s*(payer|pay|p|receiver|receive|rec|r)?\s*$", re.I)

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

def _delta_fraction(m, terms, strike, leg, d):
    """The delta of one leg struck at `strike`, as a fraction of its annuity."""
    probe = {**terms, "strike": strike, "pay_or_receive": leg}
    trade = <today's trade-building function>(m, probe)
    return abs(<IRDelta function>(m, trade, probe, d)) / abs(<Annuity function>(m, trade, probe, d)) * 1e4

def _delta_strike(m, terms, alias, d):
    """The strike at which the named leg has the delta of '25d', '25d payer' or '25d receiver'."""
    target = float(alias.group(1)) / 100.0
    if not 0.0 < target < 1.0:
        raise ValueError(f"a delta strike is between 0d and 100d, got {alias.group(0)!r}")
    named = (alias.group(2) or "").lower()
    if named:
        leg = "<payer spelling>" if named.startswith("p") else "<receiver spelling>"
    elif <terms["pay_or_receive"] is a single leg>:
        leg = terms["pay_or_receive"]
    else:
        raise ValueError("a delta strike on a straddle must name the leg: '25d payer' or '25d receiver'")
    payer = <leg is the payer>   # a payer's delta falls as the strike rises, a receiver's rises
    return _solve(lambda k: _delta_fraction(m, terms, k, leg, d) - target, terms["strike"], falling=payer)
```

The strike is in the unit the legacy resolve stores it in; the search starts at the forward and
reaches 16 percentage points to either side if that unit is a decimal rate. If the legacy resolve
stores the strike in another unit, scale `widths` and `tol` to it and say so in your report.

In `resolve`, around the legacy call:

```python
    alias = _DELTA.match(kw["strike"]) if isinstance(kw.get("strike"), str) else None
    if alias:
        kw["strike"] = "<the plain at-the-money spelling>"   # the legacy resolve pins the dates and the forward
    terms = <legacy resolve>(m, kw, d)
    terms["curve"] = key
    if alias:
        terms["strike"] = _delta_strike(m, terms, alias, d)
```

**Gates** (under `PricingContext(<trade date>)`, on two dates; quote the numbers):

1. **Known answer first.** An at-the-money payer and an at-the-money receiver, one year into ten:
   `calc(IRDelta(aggregation_level="Type"))` divided by `calc(Annuity) / 1e4` is between 0.40 and
   0.60 in size. If it is not, the convention does not fit this config: stop and report the two
   numbers. Do not change the `1e4`.
2. For `strike="25d"` on a payer, `"25d"` on a receiver and `"10d payer"` on a payer, the same
   ratio is 0.25, 0.25 and 0.10 in size, to 1e-4. A straddle with `strike="25d payer"` resolves
   to the same strike as the payer with `strike="25d"`.
3. The receiver's strike is below the forward and the payer's is above it.
4. The resolved `strike` is a number, and it is the same when the trade is priced on a later date.
5. `strike="25d"` on a straddle raises the `ValueError` above; `strike="100d"` raises.
6. Rerun Gate B: still 0 differences.
7. Time one `resolve` of a delta-strike trade and report the seconds: it builds and prices about
   25 probe trades, once per trade.

### Step 7. Sizing by risk

`dv01=`, `vega=` and `gamma=` are amounts in the unit of the function they name, always positive.
The direction comes from `buy_sell` and `pay_or_receive`. When one is given it defines the size and
`notional_amount` is ignored (it always has a default). Add to `code:`:

```python
_SIZE_BY = {"dv01": <IRDelta scalar function>, "vega": <IRVega scalar function>, "gamma": <IRGammaParallel function>}

def _size_scale(m, terms, targets, d):
    """What to multiply the notional by so the trade has the one risk target given."""
    if len(targets) != 1:
        raise ValueError(f"size by one of {sorted(_SIZE_BY)}, got {sorted(targets)}")
    ((name, target),) = targets.items()
    if not float(target) > 0.0:
        raise ValueError(f"{name} must be positive (the direction comes from buy_sell and pay_or_receive), got {target!r}")
    if name == "dv01" and <terms["pay_or_receive"] is a straddle>:
        raise ValueError("a straddle is sized by vega or gamma, not by dv01")
    trade = <today's trade-building function>(m, terms)
    function = _SIZE_BY[name]
    risk = function(m, trade, terms, d)   # at the notional the terms already have
    if not risk:
        raise ValueError(f"cannot size by {name}: the trade has none")
    return abs(float(target) / risk)
```

and in `resolve`, the first line before the legacy call and the last two before `return`:

```python
    targets = {k: kw.pop(k) for k in _SIZE_BY if kw.get(k) is not None}
    ...
    if targets:
        terms[<signed notional key>] *= _size_scale(m, terms, targets, d)
```

The order inside `resolve` is fixed: curve, take out the size targets, legacy resolve, curve term,
delta strike, size. The size is solved on the final strike. Scale every resolved term that is
proportional to the notional, and none that is not. The trade built here is only a probe: the real
trade is still built by `trade:`. The functions are called with their default bump, because
`resolve` does not see a measure's parameters; say so in the config's `description`, with the
delta convention and the rule that a risk size replaces `notional_amount`.

**Gates** (resolve under `PricingContext(<trade date>)`, on two dates; quote the numbers):

1. A bought straddle with `vega=50_000`: `calc(IRVega(aggregation_level="Type"))` is `+50_000`
   to 1e-6 relative. Sold: `-50_000`.
2. A bought straddle with `gamma=<amount>`: `calc(IRGammaParallel)` is that amount.
3. A payer with `dv01=10_000`: `calc(IRDelta(aggregation_level="Type"))` is `+10_000`; a
   receiver: `-10_000`.
4. Combined with a delta strike (`strike="25d", dv01=10_000`): the strike is the one the same
   trade gets without `dv01`, and the delta is `10_000`.
5. Resolve on the first date and price on the second: the resolved notional is unchanged (the
   size is pinned at the trade date, not re-solved).
6. `vega=` together with `gamma=` raises; a negative amount raises; a straddle with `dv01=` raises.
7. Rerun Gate B: still 0 differences (no case there uses a delta strike or a risk size).

### Step 8. Finish

1. Search notebooks and scripts for the old file name and the old asset name; update them, add
   `markets=["configs/markets"]` to their `PricebtSession.use(...)` calls and, unless section 0
   says `any`, add `PricingContext.current = PricingContext(market_data_location="NYC")` once
   after their imports. List what you changed.
2. Do **not** add another curve or currency yourself. Report to the user the exact list of
   `pricebt_ids` attributes (with the value each has for the existing curve) and everything else
   the token provider holds that is specific to the curve (market definition, cache file name),
   and ask for those values for the next curve. Adding it is then: one new token provider file,
   one entry in the currency provider's `requires:` and `expr`. A new currency also needs a copy
   of the asset file with only the header changed.

## 7. Guide: writing the token provider

```yaml
schema_version: 1
market: usd_sofr_<ch>_vol
description: >
  <what the market is, where it comes from, snapshot time and location, which dates return None>
imports: |
  <today's imports block, unchanged>
  from types import SimpleNamespace
code: |
  <the constants the market loading uses, unchanged>
  <the wrapper class, unchanged, plus the method below>
  <the market-loading function and its helpers, unchanged>

  def record(d, location):
      if location != "NYC":
          raise ValueError(f"no snapshot rule for market data location {location!r}")
      m = <market-loading function>(d)
      if m is None:
          return None
      m.pricebt_ids = SimpleNamespace(<one attribute per identifier>)
      return m
expr: 'record(pricebt_date, pricebt_location)'
```

The one method to add to the wrapper class:

```python
    def derive(self, env):
        """The same class and identifiers on a derived (bumped, rolled) environment."""
        other = type(self)(env)
        other.pricebt_ids = self.pricebt_ids
        return other
```

Rules:

1. **Move, do not rewrite.** The market-loading function keeps its body, its checks, its `None`
   returns and its raises exactly. So does the wrapper class, apart from the added method.
2. **Imports.** Copy today's whole `imports:` block, unchanged and in the same order, into the
   provider, and leave it unchanged in the asset config. An unused import is harmless; a changed
   import order may not be. If a line of it is not safe to run twice (it registers something, logs
   in, or appends to a path without checking), put only those lines behind a process-wide flag,
   with the same flag name in both files:

   ```python
   import sys
   if not getattr(sys, "_pricebt_library_ready", False):
       <those lines, unchanged>
       sys._pricebt_library_ready = True
   ```
3. **Location.** `pricebt_location` is the market data location of the pricing context, `"LDN"`
   unless the user sets another. A snapshot belongs to one location: accept the one of section 0
   and raise for anything else, as `record` does above. When section 0 says `any`, leave out those
   two lines and say in `description` that the location is ignored.
4. **Caches.** Keep any cache of loaded markets, in memory or on disk, exactly as it is, including
   its file names, so that existing cache files stay valid. A cache key or file name must contain
   everything the result depends on: two token providers must never share one. Note in your report
   what a second provider would have to change.
5. **Identifiers that the loading does not use** (a funding or discount name that only the pricing
   passes to the library) still belong in `pricebt_ids`. If today's file reads one from an
   environment variable, the provider reads the same variable with the same default.
6. **No instrument logic.** The provider knows nothing about swaptions, strikes, notionals or
   measures.
7. **Check it alone** before touching the asset:

   ```python
   from pricebt.markets import PricingContext
   from pricebt.session import PricebtSession
   PricingContext.current = PricingContext(market_data_location="NYC")
   s = PricebtSession.use(markets=["configs/markets"])
   m = s.pricing._provider_market("usd_sofr_<ch>_vol", <business date>, None)
   print(type(m).__name__, vars(m.pricebt_ids))
   assert m.derive(m.env).pricebt_ids is m.pricebt_ids
   assert s.pricing._provider_market("usd_sofr_<ch>_vol", <a Saturday>, None) is not m   # the "no market" sentinel
   ```
   `m` must be the same kind of object today's market loading returns for that date.

## 8. Final report

State, with the command output for each: the result of every gate (A, B, C and the gates of steps
5, 6 and 7); the files created, moved and deleted; the list of `pricebt_ids` attributes and their
values for the existing curve; the two class rewrites you made and where; anything you could not
verify; and the question for the user from step 8. Do not describe a gate as passed unless you ran
it and are quoting its output.
