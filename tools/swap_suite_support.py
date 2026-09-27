"""Support for the SWAP suite (configs/suite/s1*, s2*, s3*, s4*, s08*, s11*, s12*, s07): the stack overlays and independent raw-library re-computations.

The suite configs are library-neutral (spec T1, X1): ONE instrument spec `usd_sofr_ois`, side / maturity / notional are terms, and the pricing library is
chosen by a STACK OVERLAY (`configs/adapters/<stack>_swap.yaml`, `<stack>_bond.yaml`) that sets only the instrument's factory and bindings and the pricer role's wrap.
Cost models are plain YAML: `{type: scaled, scaling_type: "measure:dv01", scaling_level: 0.1}`.

    STACKS                     the stack names: rateslib, quantlib, refstack
    overlays(stack, *kinds)    the overlay files of a stack for the instrument kinds a config uses ("swap", "bond")
    par_panel(stamps)          spot-starting par rates (percent) of the store curve served at each stamp, computed with RAW rateslib from the snapshot's
                               node table (independent of the engine, the strategy layer and the adapter)
    shadow_ledger(result)      S11's buy-and-hold equity from raw rateslib (NPV of the same swap on the store curve + fixed/float cash paid)

The raw-library helpers live here, in `tools/` (never in `src/`): a checking tool built on the library itself is the point of an independent check.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "configs" / "adapters"
STACKS = ("rateslib", "quantlib", "refstack")
EOD_ASSET = "USD-SOFR-1D-CITIVELOEXCEL"
FIXTURES = ROOT / "data" / "fixtures"


def overlays(stack: str, *kinds: str) -> list:
    """The overlay files of `stack` for the instrument kinds (`swap`, `bond`) a config defines; a config with both (s07) takes both."""
    if stack not in STACKS:
        raise ValueError(f"unknown stack {stack!r}; known: {list(STACKS)}")
    bad = [k for k in kinds if k not in ("swap", "bond")]
    if bad:
        raise ValueError(f"unknown instrument kind {bad}; known: ['swap', 'bond']")
    return [ADAPTERS / f"{stack}_{k}.yaml" for k in kinds]


def _fixtures_root() -> Path:
    from support.common import default_fixtures_root  # tests/support: honours PRICEBT_DATA

    return default_fixtures_root()


def _raw_curve(node_dates: Sequence[dt.date], dfs: Sequence[float], interpolation: str = "log_linear") -> Any:
    import rateslib as rl

    return rl.Curve(nodes={dt.datetime(d.year, d.month, d.day): float(v) for d, v in zip(node_dates, dfs)}, convention="act360", calendar="nyc", modifier="MF",
                    interpolation=interpolation)


def par_panel(stamps: Sequence[pd.Timestamp], tenors: Sequence[str] = ("2Y", "5Y", "10Y"), asset: str = EOD_ASSET, store: Optional[Any] = None) -> pd.DataFrame:
    """Spot-starting par rates (percent) of `tenors` on the store curve served at each stamp (same asof selection as the runs), by raw rateslib: the curve is
    rebuilt from the snapshot's node dates and discount factors and each tenor is a `usd_irs` swap starting two business days after the reference date."""
    import rateslib as rl

    from support.curves import CurveStore

    st = store or CurveStore(asset)
    cal = rl.get_calendar("nyc")
    rows = {}
    for ts in stamps:
        snap = st.get_pricer(ts).snapshot
        c = next(iter(snap.curves.values()))
        curve = _raw_curve(c.node_dates, c.values)
        eff = cal.add_bus_days(dt.datetime(snap.reference_date.year, snap.reference_date.month, snap.reference_date.day), 2, True)
        rows[ts] = {t: float(rl.IRS(effective=eff, termination=t.lower(), spec="usd_irs", notional=1e6).rate(curves=curve)) for t in tenors}
    return pd.DataFrame.from_dict(rows, orient="index")


def sel_day(store: Any, sel: Any) -> str:
    return str(dt.date(1970, 1, 1) + dt.timedelta(days=int(store.index["tdays"][sel.pos])))


def shadow_ledger(r: Any, bump_bp: float = 0.0, asset: str = EOD_ASSET) -> Dict[str, Any]:
    """Independent valuation of S11's single swap: raw rateslib IRS, the curve rebuilt from the store parquet row, the fixings read from the parquet file.
    `bump_bp` shifts the shadow's fixed rate (a control that must break the match)."""
    import pyarrow.parquet as pq
    import rateslib as rl

    from support.curves import CurveStore

    root = _fixtures_root()
    pos = r.positions.iloc[0]
    entry = pd.Timestamp(pos["entry_ts"])
    store = CurveStore(asset)  # used ONLY to find which parquet row the run's asof mapping selected at each stamp
    fx = pd.read_parquet(root / "fixings" / "USD-SOFR-1D.parquet")
    fixings = pd.Series(fx["rate"].to_numpy(dtype=float) * 100.0, index=pd.DatetimeIndex(pd.to_datetime(fx["date"])).normalize())

    def curve_at(ts: pd.Timestamp) -> Any:
        sel = store.select(ts)
        part = root / "curves" / f"asset={asset}" / f"date={sel_day(store, sel)}"
        tab = pd.concat([pq.read_table(f).to_pandas() for f in sorted(part.glob("*.parquet"))], ignore_index=True)
        row = tab[pd.to_datetime(tab["timestamp_utc"], utc=True) == sel.stamp].iloc[-1]
        nodes = {pd.Timestamp(d).to_pydatetime(): float(v) for d, v in zip(row["node_dates"], row["discount_factors"])}
        return rl.Curve(nodes=nodes, convention="act360", calendar="nyc", modifier="MF", interpolation=str(row["interpolation"] or "log_linear")), min(nodes)

    c0, ref0 = curve_at(entry)
    eff = rl.get_calendar("nyc").add_bus_days(ref0, 2, True)
    probe = rl.IRS(effective=eff, termination="10Y", spec="usd_irs", notional=1e6)
    k = float(probe.rate(curves=c0)) + bump_bp / 100.0
    name = "SHADOW_SOFR"
    stamps = list(r.equity.index[::10]) + [r.equity.index[-1]]
    diffs, n = [], 0
    npv0 = None
    for ts in [entry] + stamps:
        c, ref = curve_at(ts)
        fxv = fixings[fixings.index < pd.Timestamp(ref)]
        key = f"{name}_1B"
        if key in rl.fixings.loader.loaded:
            rl.fixings.pop(key)
        rl.fixings.add(key, fxv)
        sw = rl.IRS(effective=eff, termination=probe.leg1.schedule.termination, spec="usd_irs", fixed_rate=k, notional=-1e7, leg2_rate_fixings=name)
        npv = float(sw.npv(curves=c))
        cfs = sw.cashflows(curves=c)
        paid = float(cfs.loc[(cfs["Payment"] >= ref0) & (cfs["Payment"] < ref), "Cashflow"].sum()) if ref > ref0 else 0.0
        if npv0 is None:
            npv0 = npv
            continue
        diffs.append(abs(npv - npv0 + paid - float(r.equity.loc[ts, "equity"])))
        n += 1
    return {"fixed_rate": k, "max_abs_diff": float(max(diffs)), "n": n}


def recorded_dv01(instrument: Any, ctx: Any) -> float:
    """The dv01 the RECORDED (pre-refactor) suite runs reported for a swap: the analytic fixed-leg PV01, also once the swap has started.

    The rateslib adapter's `dv01` is the parallel par-rate dollar delta: for a swap that has started it is the sum of its delta ladder, because the analytic PV01 keeps the
    accrued coupon (which no longer moves with rates) and overstates the market dv01 (a 2Y swap one month old: by several percent). The recorded runs used the analytic
    figure. Binding it back (`configs/adapters/rateslib_swap_recorded_dv01.yaml`, with `--set registry.allow=[support,tools.swap_suite_support]`) reproduces every recorded swap
    result bit for bit (tasks/suite_reproduction.md, section 4); the default binding differs in the cost of every EXIT and, for s2, in the rebalance decisions.
    Reads the adapter's own instrument internals (`_prep`, `_active`): a reproduction aid, not an API."""
    from pricebt.contrib.rateslib import _compat as C

    p = ctx.pricer
    if instrument.matured(p):
        return 0.0
    instrument._prep(p)
    return C.finite(instrument._active.analytic_delta(curves=p.curve, leg=1))
