"""Long-end USD convexity RV screener: forward curves vs straddles.

Run:   python -I screener.py <path to fwd_curves_and_vol.xlsx>
Out:   screener.html next to this file, plus the summary table on stdout.

Units: forwards in %, vols are normal bp/yr. Every package number is bp/yr per 1 unit of curve DV01.

Curve  = DV01-neutral, receive the later-start forward, pay the earlier one (same tenor). Long gamma.
Option = short ATM straddle, gamma matched to the curve.

  leg gamma per DV01      = 2 * D_A * 1e-4            (D_A = modified annuity duration)
  carry                   = roll_L - roll_S           (roll = d fwd / d start, spot curve unchanged)
  sigma_BE (curve implied)= sqrt(-carry * 1e4 / dD)   (signed: negative = you are paid to own gamma)
  edge                    = carry + 1e-4 * dD * sigma_imp^2        (= 1e-4*dD*(sigma_imp^2 - sigma_BE^2))
  exp                     = edge + 1e-4 * dD * (sigma_c^2 - sigma_o^2)   (realised-vol basis, 3m realised as forecast)
  risk                    = sqrt(slope_vol^2 + (vega * vol_of_vol)^2)
"""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

W = 63      # realised-vol / corr window (3m)
ZW = 252    # z-score window (1y)
PHI0 = 1 / math.sqrt(2 * math.pi)
STRIPS = {5: (10, 15, 20, 25, 30), 10: (10, 12, 15, 20)}   # tradeable starts per tenor


# ---------------------------------------------------------------- data
def load(path):
    """xlsx layout: label row 2, data from row 5, (date, value) column pairs."""
    out = {}
    for name, raw in pd.read_excel(path, sheet_name=None, header=None).items():
        cols = {}
        for j in range(0, raw.shape[1] - 1, 2):
            label = raw.iat[1, j]
            if pd.isna(label):
                continue
            s = raw.iloc[4:, [j, j + 1]].dropna()
            ser = pd.Series(pd.to_numeric(s.iloc[:, 1]).values, index=pd.to_datetime(s.iloc[:, 0]))
            cols[str(label)] = ser.groupby(level=0).last()
        out[name] = pd.DataFrame(cols).sort_index()
    return out["fwds"], out["vol"]


# ---------------------------------------------------------------- curve math
def ann_dur(T, n, y_pct):
    """Annuity and modified annuity duration of a T-start n-tenor swap, flat semi-annual yield (per date)."""
    y = np.asarray(y_pct, float)[..., None] / 100
    t = np.arange(T + 0.5, T + n + 1e-9, 0.5)
    df = (1 + y / 2) ** (-2 * t)
    return df.sum(-1) / 2, (t * df).sum(-1) / df.sum(-1) / (1 + y[..., 0] / 2)


def synth(f1, T1, n1, f2, T2, n2):
    """Join two adjacent forwards into one longer forward (annuity-weighted)."""
    a1, a2 = ann_dur(T1, n1, f1)[0], ann_dur(T2, n2, f2)[0]
    return (f1 * a1 + f2 * a2) / (a1 + a2)


def dquad(x, f, at):
    """Derivative at `at` of the quadratic through (x[i], f[i]), i=0..2."""
    x0, x1, x2 = x
    f0, f1, f2 = f
    return (f0 * (2 * at - x1 - x2) / ((x0 - x1) * (x0 - x2))
            + f1 * (2 * at - x0 - x2) / ((x1 - x0) * (x1 - x2))
            + f2 * (2 * at - x0 - x1) / ((x2 - x0) * (x2 - x1)))


def strip_rolls(strip):
    """Receiver roll (bp/yr) at each start of a forward strip (columns = start years)."""
    xs = sorted(strip.columns)
    out = {}
    for i, T in enumerate(xs):
        k = min(max(i - 1, 0), len(xs) - 3)          # centred where possible, one-sided at the ends
        pts = xs[k:k + 3]
        out[T] = 100 * dquad(pts, [strip[p] for p in pts], T)
    return pd.DataFrame(out)


def build_strips(F):
    s5 = pd.DataFrame({T: F[f"{T}Y.5Y"] for T in (5, 10, 15, 20, 25, 30)})
    s10 = pd.DataFrame({T: F[f"{T}Y.10Y"] for T in (10, 12, 15, 20)})
    # anchors for the roll derivative only, never traded
    s10[5] = synth(F["5Y.5Y"], 5, 5, F["10Y.5Y"], 10, 5)
    s10[25] = synth(F["25Y.5Y"], 25, 5, F["30Y.5Y"], 30, 5)
    return {5: s5, 10: s10[sorted(s10.columns)]}


def rv(x):
    """Annualised realised normal vol in bp of a % series."""
    return x.diff().mul(100).rolling(W).std() * math.sqrt(252)


def curve_frame(strip, rolls, n, s, l):
    d = pd.concat({"fS": strip[s], "fL": strip[l], "rS": rolls[s], "rL": rolls[l]}, axis=1).dropna()
    d["DS"] = ann_dur(s, n, d.fS)[1]
    d["DL"] = ann_dur(l, n, d.fL)[1]
    d["dD"] = d.DL - d.DS
    d["carry"] = d.rL - d.rS
    v = -d.carry * 1e4 / d.dD
    d["sbe"] = np.sign(v) * np.sqrt(v.abs())
    d["spread"] = (d.fL - d.fS) * 100
    d["spread_z"] = (d.spread - d.spread.rolling(ZW).mean()) / d.spread.rolling(ZW).std()
    d["sL"], d["sS"] = rv(d.fL), rv(d.fS)
    d["sc2"] = (d.DL * d.sL**2 - d.DS * d.sS**2) / d.dD      # curve's effective realised variance
    d["slope_vol"] = rv(d.fL - d.fS)
    d["thresh"] = np.sqrt(d.DS / d.DL)                          # long gamma iff sL/sS > thresh
    d["lvl"] = (d.fL + d.fS) / 2
    return d


# ---------------------------------------------------------------- package math
def expiry_years(code):
    return int(code[:-1]) / (12 if code.endswith("M") else 1)


def package(c, vol, spot, T):
    """Curve frame c + one straddle (implied vol series, underlying proxy series, expiry yrs)."""
    d = c.join(pd.concat({"simp": vol, "spot": spot}, axis=1), how="inner").dropna(subset=["simp", "spot"])
    d["so"] = rv(d.spot)
    d["volvol"] = d.simp.diff().rolling(W).std() * math.sqrt(252)
    d["edge"] = d.carry + 1e-4 * d.dD * d.simp**2
    d["basis"] = 1e-4 * d.dD * (d.sc2 - d.so**2)
    d["exp"] = d.edge + d.basis
    # ponytail: vega uses expiry at entry; short expiries are rolled, so true vega path is lower. Model the roll if it matters.
    d["vega_risk"] = 2 * d.dD * 1e-4 * d.simp * T * d.volvol
    # daily gamma-basis P&L: curve legs' squared moves vs the straddle underlying's, per curve DV01
    dL, dS, do = (d[k].diff() * 100 for k in ("fL", "fS", "spot"))
    d["basis_risk"] = (1e-4 * (d.DL * dL**2 - d.DS * dS**2 - d.dD * do**2)).rolling(W).std() * math.sqrt(252)
    d["risk"] = np.sqrt(d.slope_vol**2 + d.vega_risk**2 + d.basis_risk**2)
    d["sharpe"] = d.exp / d.risk
    d["edge_z"] = (d.edge - d.edge.rolling(ZW, min_periods=ZW // 2).mean()) / d.edge.rolling(ZW, min_periods=ZW // 2).std()
    d["ratio"] = d.dD * 1e-4 * d.simp * math.sqrt(T) / PHI0      # straddle DV01 per curve DV01
    d["corr"] = d.lvl.diff().rolling(W).corr(d.spot.diff())
    return d


def screen(F, V):
    strips = build_strips(F)
    rolls = {n: strip_rolls(s) for n, s in strips.items()}
    curves = {}
    for n, starts in STRIPS.items():
        for i, s in enumerate(starts):
            for l in starts[i + 1:]:
                curves[f"{s}y{n}y/{l}y{n}y"] = curve_frame(strips[n], rolls[n], n, s, l)
    opts = sorted(V.columns, key=lambda c: (int(c.split(".")[1][:-1]), expiry_years(c.split(".")[0])))
    cells = {}
    for cn, c in curves.items():
        for o in opts:
            exp_code, tail = o.split(".")
            # ponytail: spot swap rate stands in for the option's 1y-forward underlying; use forward swap if available
            cells[(cn, o)] = package(c, V[o], F[tail], expiry_years(exp_code))
    return curves, opts, cells


# ---------------------------------------------------------------- self-checks
def bachelier_straddle(F, K, s, T):
    sd = s * math.sqrt(T)
    d = (F - K) / sd
    Phi = 0.5 * (1 + math.erf(d / math.sqrt(2)))
    return (F - K) * (2 * Phi - 1) + 2 * sd * math.exp(-d * d / 2) * PHI0


def selfcheck():
    # 1. derivative exact on a known quadratic: 3x^2 + 2x + 1 -> 6x + 2
    x = (1.0, 2.0, 4.0)
    assert abs(dquad(x, [3 * v * v + 2 * v + 1 for v in x], 1.7) - 12.2) < 1e-9
    # 2. flat strip -> zero roll everywhere -> zero carry
    flat = pd.DataFrame({T: [5.0, 5.0] for T in (5, 10, 15, 20, 25, 30)})
    assert strip_rolls(flat).abs().to_numpy().max() < 1e-9
    # 3. gamma-matched straddle: theta == 1e-4*dD*sigma^2 and gamma == 2*dD*1e-4 per curve DV01
    dD, s, T, h, dt = 4.88, 90.0, 1.0, 0.5, 1e-5
    ratio = dD * 1e-4 * s * math.sqrt(T) / PHI0
    theta = ratio * (bachelier_straddle(0, 0, s, T) - bachelier_straddle(0, 0, s, T - dt)) / dt
    gamma = ratio * (bachelier_straddle(h, 0, s, T) + bachelier_straddle(-h, 0, s, T) - 2 * bachelier_straddle(0, 0, s, T)) / h**2
    assert abs(theta / (1e-4 * dD * s**2) - 1) < 1e-3, theta
    assert abs(gamma / (2 * dD * 1e-4) - 1) < 1e-3, gamma
    # 4. known value: 30y spot receiver convexity ~9-10bp/yr at 90 vol
    assert 8.5 < ann_dur(0, 30, 4.8)[1][()] * 90**2 * 1e-4 < 10.5


# ---------------------------------------------------------------- output
def fmt(x, p=1):
    return "" if pd.isna(x) else f"{x:,.{p}f}"


def report(curves, opts, cells, out):
    asof = max(c.index.max() for c in cells.values())
    snap = pd.DataFrame({k: d.loc[asof] for k, d in cells.items() if asof in d.index}).T
    snap.index.names = ["curve", "option"]
    best = snap.loc[snap.groupby(level="curve")["sharpe"].idxmax()].sort_values("sharpe", ascending=False)
    # matched view: restrict each curve to the tail its legs co-move with most, then best expiry on that tail
    s2 = snap.assign(tail=[o.split(".")[1] for _, o in snap.index])
    mtail = s2.groupby(["curve", "tail"])["corr"].mean().groupby(level="curve").idxmax().str[1]
    s2 = s2[s2["tail"].values == mtail.reindex(s2.index.get_level_values("curve")).values]
    matched = s2.loc[s2.groupby(level="curve")["sharpe"].idxmax()].sort_values("sharpe", ascending=False)

    cols = [("Curve", lambda r, k: k[0]), ("Spread bp", lambda r, k: fmt(r.spread)), ("Spread z", lambda r, k: fmt(r.spread_z, 2)),
            ("Carry", lambda r, k: fmt(r.carry)), ("ΔD", lambda r, k: fmt(r.dD)), ("σ_BE", lambda r, k: fmt(r.sbe)),
            ("σL/σS", lambda r, k: fmt(r.sL / r.sS, 2)), ("Thresh", lambda r, k: fmt(r.thresh, 2)),
            ("Straddle", lambda r, k: k[1]), ("σ_imp", lambda r, k: fmt(r.simp)), ("Edge", lambda r, k: fmt(r.edge)),
            ("Edge z", lambda r, k: fmt(r.edge_z, 2)), ("Basis", lambda r, k: fmt(r.basis)), ("Exp", lambda r, k: fmt(r.exp)),
            ("Risk", lambda r, k: fmt(r.risk)), ("Sharpe", lambda r, k: fmt(r.sharpe, 2)),
            ("Strad DV01/curve", lambda r, k: fmt(r.ratio, 3)), ("Corr", lambda r, k: fmt(r["corr"], 2))]
    cols.insert(-3, ("Basis risk", lambda r, k: fmt(r.basis_risk)))
    tab = lambda df: pd.DataFrame({h: [f(r, k) for k, r in df.iterrows()] for h, f in cols})
    print(f"as of {asof.date()}  (bp/yr per curve DV01; vols normal bp/yr)\n")
    figs = []
    for df, title in ((best, "Best straddle per curve (any tail), ranked by Sharpe"),
                      (matched, "Best straddle on the matched tail (highest co-movement with the curve legs)")):
        table = tab(df)
        print(title + "\n" + table.to_string(index=False) + "\n")
        figs.append(go.Figure(go.Table(
            header=dict(values=list(table.columns), fill_color="#1f3b5c", font=dict(color="white", size=11), align="center"),
            cells=dict(values=[table[c] for c in table.columns], align="right", font=dict(size=11),
                       fill_color=[["#f4f6f9", "white"] * len(table)]))).update_layout(
            title=title, height=90 + 26 * len(table), margin=dict(l=10, r=10, t=40, b=10)))

    hm = snap["sharpe"].unstack("option").reindex(columns=opts).loc[best.index.get_level_values("curve")]
    figs.append(go.Figure(go.Heatmap(z=hm.values, x=hm.columns, y=hm.index, colorscale="RdBu", zmid=0,
                                     text=np.round(hm.values, 2), texttemplate="%{text}", colorbar=dict(title="Sharpe")))
                .update_layout(title="Sharpe: curve × straddle (columns: tail, then expiry)", height=520,
                               yaxis=dict(autorange="reversed"), margin=dict(l=10, r=10, t=40, b=10)))

    fr = go.Figure()
    fr.add_trace(go.Scatter(x=snap.risk, y=snap.exp, mode="markers", marker=dict(color="#c8cdd4", size=6), name="all packages",
                            text=[f"{c} vs {o}" for c, o in snap.index], hovertemplate="%{text}<br>risk %{x:.1f}<br>exp %{y:.1f}"))
    fr.add_trace(go.Scatter(x=best.risk, y=best.exp, mode="markers+text", name="best per curve",
                            marker=dict(color=best.sharpe, colorscale="Viridis", size=11, showscale=False),
                            text=[f"{c} | {o}" for c, o in best.index], textposition="top center", textfont=dict(size=9)))
    fr.update_layout(title="Frontier: expected P&L vs risk (bp/yr per curve DV01)", xaxis_title="risk", yaxis_title="expected",
                     height=560, margin=dict(l=10, r=10, t=40, b=10))
    figs.append(fr)

    top = best.index[0]
    h = cells[top]
    hs = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                       subplot_titles=("Curve implied vs straddle implied vol (bp/yr)", "Edge (bp/yr per DV01)"))
    hs.add_trace(go.Scatter(x=h.index, y=h.sbe, name="σ_BE curve", line=dict(width=1)), 1, 1)
    hs.add_trace(go.Scatter(x=h.index, y=h.simp, name=f"σ_imp {top[1]}", line=dict(width=1)), 1, 1)
    hs.add_trace(go.Scatter(x=h.index, y=h.edge, name="edge", line=dict(width=1, color="#444")), 2, 1)
    hs.update_layout(title=f"History: {top[0]} vs {top[1]}", height=560, margin=dict(l=10, r=10, t=60, b=10))
    figs.append(hs)

    for f in figs:
        f.update_layout(template="plotly_white", font=dict(family="Arial"))
    body = "".join(f.to_html(full_html=False, include_plotlyjs="cdn" if i == 0 else False) for i, f in enumerate(figs))
    out.write_text(
        f"<!doctype html><html><head><meta charset='utf-8'><title>Convexity RV Screener</title></head>"
        f"<body style='font-family:Arial;max-width:1400px;margin:auto;padding:16px'>"
        f"<h2>USD long-end convexity RV: forward curves vs straddles</h2>"
        f"<p>As of {asof.date()}. Receive later-start forward / pay earlier (DV01-neutral) vs short gamma-matched ATM straddle. "
        f"bp/yr per 1 unit of curve DV01. Forecast realised vol = 3m realised.</p>{body}</body></html>", encoding="utf-8")
    return snap


def main():
    sys.stdout.reconfigure(encoding="utf-8")   # Windows console default (cp1252) can't print Greek
    selfcheck()
    F, V = load(sys.argv[1])
    curves, opts, cells = screen(F, V)
    # regression anchors vs the one-date hand calc (17-Oct-2026); tolerance, not equality
    a = pd.Timestamp("2026-10-17")
    if a in curves["10y10y/20y10y"].index:
        assert 87 < curves["10y10y/20y10y"].loc[a, "sbe"] < 97, curves["10y10y/20y10y"].loc[a, "sbe"]
        assert curves["20y5y/25y5y"].loc[a, "sbe"] < 25, curves["20y5y/25y5y"].loc[a, "sbe"]
    out = Path(__file__).with_name("screener.html")
    report(curves, opts, cells, out)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
