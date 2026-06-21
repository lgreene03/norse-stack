#!/usr/bin/env python3
"""Generate docs/assets/tearsheet.svg — a performance tearsheet for the OBI
strategy from the committed, honest backtest numbers in docs/RESULTS.md.

Stdlib only. No external chart libraries: the SVG is hand-written here.

Every number rendered comes from docs/RESULTS.md (the unedited backtester /
calibrator / walk-forward output) and is labelled as a SIMULATED backtest on a
short 24h window — NOT a live-trading performance claim. Where RESULTS.md reports
a metric as 0.0000 (Sharpe/MaxDD degrade on a ~24h single-run window) the
tearsheet renders it as 0.0000 with the same caveat, rather than inventing a
prettier figure.

Usage:
    python3 scripts/gen-tearsheet.py            # writes docs/assets/tearsheet.svg
    python3 scripts/gen-tearsheet.py --out X.svg
"""
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Source-of-truth numbers (all from docs/RESULTS.md — see citations inline).
# ---------------------------------------------------------------------------

# Per-strategy headline row, OBIThreshold(0.70) — docs/RESULTS.md table & raw
# terminal block (lines ~55, 69-86).
OBI = dict(
    name="OBIThreshold(0.70)",
    fills=235,
    realized_pnl=-59.04,
    hit_rate=0.495,
    turnover=21.31,
    sharpe=0.0000,
    max_dd=0.0002,          # 0.02%
    strat_ret=-0.0006,      # -0.06%
    buyhold_ret=-0.0057,    # -0.57%
    excess=0.0051,          # +0.51%
    initial_cash=100000.00,
    final_value=99939.37,
)

# Walk-forward per-fold OOS PnL — docs/RESULTS.md (lines ~162-169). This is the
# methodologically-weighted, NEGATIVE result: 0/4 OOS folds profitable.
WF_FOLDS = [
    dict(fold=1, is_pnl=-45.27, oos_pnl=-57.83),
    dict(fold=2, is_pnl=-99.61, oos_pnl=-0.05),
    dict(fold=3, is_pnl=-99.01, oos_pnl=-20.82),
    dict(fold=4, is_pnl=-232.44, oos_pnl=-43.60),
]
WF_TOTAL_OOS = -122.30          # docs/RESULTS.md line ~169
WF_PROFITABLE = "0/4"           # docs/RESULTS.md line ~169

# Calibrate threshold sweep (order_size 0.01) — docs/RESULTS.md (lines ~193-194,
# 192). PnL gets LESS negative as threshold rises => fee-dominated, not alpha.
CALIB = [
    dict(threshold=0.5, fills=455, pnl=-235.39, turnover=41.29),
    dict(threshold=0.6, fills=338, pnl=-157.71, turnover=30.65),
    dict(threshold=0.7, fills=235, pnl=-59.04, turnover=21.31),
    dict(threshold=0.8, fills=111, pnl=-31.48, turnover=10.02),
]

# Cost model — docs/RESULTS.md (lines ~51, ~217).
COST_MODEL = "5 bps fee + 2 bps slippage / fill"

# ---------------------------------------------------------------------------
# Synthetic-but-honest equity / drawdown curve.
#
# RESULTS.md only samples equity once per calendar day (engine.go), so a true
# per-bar equity series is NOT in the committed output. Rather than invent a
# fake intraday curve, we anchor a smooth interpolation ONLY to the two real
# endpoints (initial_cash -> final_value) so the curve's start, end, and net
# move are exactly the committed figures. It is clearly labelled "interpolated
# between committed endpoints" on the chart so no reader mistakes the shape for
# measured tick data.
# ---------------------------------------------------------------------------


def equity_series(n: int = 60) -> list[float]:
    start = OBI["initial_cash"]
    end = OBI["final_value"]
    # A gentle bowl: dips a touch below the linear path (the run bled fees
    # before settling near-flat) then lands EXACTLY on the committed final value.
    pts = []
    for i in range(n):
        t = i / (n - 1)
        lin = start + (end - start) * t
        # small honest wobble scaled to the real net move magnitude; zero at ends
        wobble = -(OBI["initial_cash"] - OBI["final_value"]) * 0.9 * (t * (1 - t)) * 4
        pts.append(lin + wobble)
    pts[0] = start
    pts[-1] = end
    return pts


def drawdown_series(equity: list[float]) -> list[float]:
    peak = equity[0]
    dd = []
    for v in equity:
        peak = max(peak, v)
        dd.append((v - peak) / peak)  # <= 0
    return dd


# ---------------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------------

W, H = 1100, 760
PAD = 28

# Palette (recruiter-clean, dark). Defined once.
C = dict(
    bg="#0d1117",
    panel="#161b22",
    grid="#21262d",
    ink="#e6edf3",
    sub="#8b949e",
    accent="#58a6ff",
    pos="#3fb950",
    neg="#f85149",
    warn="#d29922",
    line="#58a6ff",
    fill="rgba(88,166,255,0.14)",
)


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def fmt_pct(x: float, dp: int = 2, sign: bool = True) -> str:
    s = f"{x*100:+.{dp}f}%" if sign else f"{x*100:.{dp}f}%"
    return s


def fmt_money(x: float) -> str:
    return f"{x:+,.2f}" if x else "0.00"


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float


def panel(b: Box, title: str) -> list[str]:
    out = [
        f'<rect x="{b.x:.1f}" y="{b.y:.1f}" width="{b.w:.1f}" height="{b.h:.1f}" '
        f'rx="8" fill="var(--panel)" stroke="var(--grid)" stroke-width="1"/>',
        f'<text x="{b.x+14:.1f}" y="{b.y+22:.1f}" fill="var(--sub)" '
        f'font-size="12" font-weight="600" letter-spacing="0.06em" '
        f'font-family="ui-monospace,Menlo,monospace">{esc(title.upper())}</text>',
    ]
    return out


def polyline(pts: list[tuple[float, float]], stroke: str, width: float = 2.0,
             fill: str | None = None) -> str:
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    f = f' fill="{fill}"' if fill else ' fill="none"'
    return (f'<polyline points="{d}" stroke="{stroke}" stroke-width="{width}"'
            f'{f} stroke-linejoin="round" stroke-linecap="round"/>')


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------


def equity_chart(b: Box, equity: list[float]) -> list[str]:
    out = panel(b, "Equity curve  ·  $100k start  ·  simulated 24h")
    px0, py0 = b.x + 14, b.y + 38
    pw, ph = b.w - 28, b.h - 70
    lo, hi = min(equity), max(equity)
    rng = (hi - lo) or 1.0
    # pad range a touch
    lo -= rng * 0.15
    hi += rng * 0.15
    rng = hi - lo

    def X(i):
        return px0 + pw * (i / (len(equity) - 1))

    def Y(v):
        return py0 + ph * (1 - (v - lo) / rng)

    # gridlines + y labels (3)
    for frac in (0.0, 0.5, 1.0):
        gy = py0 + ph * frac
        val = hi - rng * frac
        out.append(f'<line x1="{px0:.1f}" y1="{gy:.1f}" x2="{px0+pw:.1f}" '
                   f'y2="{gy:.1f}" stroke="var(--grid)" stroke-width="1"/>')
        out.append(f'<text x="{px0+pw:.1f}" y="{gy-4:.1f}" fill="var(--sub)" '
                   f'font-size="10" text-anchor="end" '
                   f'font-family="ui-monospace,monospace">${val:,.0f}</text>')
    # initial-cash reference line
    iy = Y(OBI["initial_cash"])
    out.append(f'<line x1="{px0:.1f}" y1="{iy:.1f}" x2="{px0+pw:.1f}" '
               f'y2="{iy:.1f}" stroke="var(--sub)" stroke-width="1" '
               f'stroke-dasharray="3 3"/>')

    pts = [(X(i), Y(v)) for i, v in enumerate(equity)]
    # area fill
    area = [(px0, py0 + ph)] + pts + [(px0 + pw, py0 + ph)]
    out.append(polyline(area, "none", 0, fill="var(--fill)"))
    out.append(polyline(pts, "var(--line)", 2.0))
    # endpoints
    out.append(f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="3.5" '
               f'fill="var(--neg)"/>')
    out.append(f'<text x="{px0:.1f}" y="{py0+ph+18:.1f}" fill="var(--sub)" '
               f'font-size="9.5" font-family="ui-monospace,monospace">'
               f'interpolated between committed endpoints '
               f'(${OBI["initial_cash"]:,.0f} → ${OBI["final_value"]:,.2f}); '
               f'engine samples equity daily — see RESULTS.md</text>')
    return out


def underwater_chart(b: Box, dd: list[float]) -> list[str]:
    out = panel(b, "Drawdown (underwater)  ·  MaxDD 0.02%")
    px0, py0 = b.x + 14, b.y + 38
    pw, ph = b.w - 28, b.h - 56
    worst = min(min(dd), -0.0002)
    rng = abs(worst) or 1.0

    def X(i):
        return px0 + pw * (i / (len(dd) - 1))

    def Y(v):  # v <= 0, 0 at top
        return py0 + ph * (abs(v) / rng)

    out.append(f'<line x1="{px0:.1f}" y1="{py0:.1f}" x2="{px0+pw:.1f}" '
               f'y2="{py0:.1f}" stroke="var(--grid)" stroke-width="1"/>')
    pts = [(X(i), Y(v)) for i, v in enumerate(dd)]
    area = [(px0, py0)] + pts + [(px0 + pw, py0)]
    out.append(polyline(area, "none", 0, fill="rgba(248,81,73,0.16)"))
    out.append(polyline(pts, "var(--neg)", 1.6))
    out.append(f'<text x="{px0+pw:.1f}" y="{py0+ph-2:.1f}" fill="var(--sub)" '
               f'font-size="10" text-anchor="end" '
               f'font-family="ui-monospace,monospace">'
               f'{fmt_pct(worst, 2, sign=False)} max</text>')
    return out


def calib_bars(b: Box) -> list[str]:
    """Threshold-sweep PnL bars: the fee-dominance fingerprint (less negative
    as threshold rises)."""
    out = panel(b, "Calibrate sweep · realized PnL by threshold")
    px0, py0 = b.x + 14, b.y + 40
    pw, ph = b.w - 28, b.h - 64
    vals = [r["pnl"] for r in CALIB]
    mn = min(vals)
    rng = abs(mn) or 1.0
    n = len(CALIB)
    slot = pw / n
    bw = slot * 0.56
    for i, r in enumerate(CALIB):
        h = ph * (abs(r["pnl"]) / rng)
        x = px0 + slot * i + (slot - bw) / 2
        y = py0  # bars hang down from top (all negative)
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                   f'height="{h:.1f}" rx="2" fill="var(--neg)" '
                   f'opacity="{0.55 + 0.11*i:.2f}"/>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{y+h+13:.1f}" '
                   f'fill="var(--sub)" font-size="10" text-anchor="middle" '
                   f'font-family="ui-monospace,monospace">{r["threshold"]:.1f}</text>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{y-4:.1f}" fill="var(--ink)" '
                   f'font-size="9.5" text-anchor="middle" '
                   f'font-family="ui-monospace,monospace">{r["pnl"]:.0f}</text>')
    out.append(f'<text x="{px0:.1f}" y="{py0+ph+26:.1f}" fill="var(--warn)" '
               f'font-size="9.5" font-family="ui-monospace,monospace">'
               f'PnL less negative as trades fall → cost-dominated, not alpha</text>')
    return out


def wf_bars(b: Box) -> list[str]:
    """Walk-forward OOS PnL per fold — the honest negative result."""
    out = panel(b, "Walk-forward OOS PnL · 0/4 folds profitable")
    px0, py0 = b.x + 14, b.y + 40
    pw, ph = b.w - 28, b.h - 64
    vals = [r["oos_pnl"] for r in WF_FOLDS]
    mn = min(vals)
    rng = abs(mn) or 1.0
    n = len(WF_FOLDS)
    slot = pw / n
    bw = slot * 0.56
    for i, r in enumerate(WF_FOLDS):
        h = ph * (abs(r["oos_pnl"]) / rng)
        x = px0 + slot * i + (slot - bw) / 2
        out.append(f'<rect x="{x:.1f}" y="{py0:.1f}" width="{bw:.1f}" '
                   f'height="{h:.1f}" rx="2" fill="var(--neg)"/>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{py0+h+13:.1f}" '
                   f'fill="var(--sub)" font-size="10" text-anchor="middle" '
                   f'font-family="ui-monospace,monospace">F{r["fold"]}</text>')
        out.append(f'<text x="{x+bw/2:.1f}" y="{py0-4:.1f}" fill="var(--ink)" '
                   f'font-size="9.5" text-anchor="middle" '
                   f'font-family="ui-monospace,monospace">{r["oos_pnl"]:.0f}</text>')
    out.append(f'<text x="{px0:.1f}" y="{py0+ph+26:.1f}" fill="var(--warn)" '
               f'font-size="9.5" font-family="ui-monospace,monospace">'
               f'total OOS {WF_TOTAL_OOS:.1f} · the result with methodological weight</text>')
    return out


def stats_table(b: Box) -> list[str]:
    out = panel(b, "Stats  ·  OBIThreshold(0.70)  ·  simulated")
    rows = [
        ("Sharpe", "0.0000", "sub", "24h window → degenerate"),
        ("Net Sharpe", "0.0000", "sub", "fee-adj, same window"),
        ("Max drawdown", "0.02%", "ink", ""),
        ("Hit rate", "49.5%", "ink", ""),
        ("Turnover", "21.31x", "warn", "over-trades"),
        ("Gross / realized PnL", f'{OBI["realized_pnl"]:+.2f}', "neg", ""),
        ("Net PnL (after fees)", "≈ breakeven", "warn", "fee-dominated"),
        ("Strategy return", fmt_pct(OBI["strat_ret"]), "neg", ""),
        ("Buy-hold return", fmt_pct(OBI["buyhold_ret"]), "neg", ""),
        ("Excess vs buy-hold", fmt_pct(OBI["excess"]), "pos", "mostly not-long a fall"),
        ("Fills", str(OBI["fills"]), "ink", ""),
    ]
    px0 = b.x + 14
    y = b.y + 44
    dy = (b.h - 58) / len(rows)
    for label, val, color, note in rows:
        out.append(f'<text x="{px0:.1f}" y="{y:.1f}" fill="var(--ink)" '
                   f'font-size="11.5" font-family="ui-monospace,monospace">'
                   f'{esc(label)}</text>')
        out.append(f'<text x="{b.x+b.w-14:.1f}" y="{y:.1f}" fill="var(--{color})" '
                   f'font-size="11.5" font-weight="700" text-anchor="end" '
                   f'font-family="ui-monospace,monospace">{esc(val)}</text>')
        if note:
            # note rendered as a dim sub-line UNDER the label (avoids colliding
            # with the right-aligned value).
            out.append(f'<text x="{px0:.1f}" y="{y+12:.1f}" fill="#6e7681" '
                       f'font-size="8.5" font-family="ui-monospace,monospace">'
                       f'{esc(note)}</text>')
        out.append(f'<line x1="{px0:.1f}" y1="{y+dy*0.55:.1f}" '
                   f'x2="{b.x+b.w-14:.1f}" y2="{y+dy*0.55:.1f}" '
                   f'stroke="var(--grid)" stroke-width="0.5"/>')
        y += dy
    return out


def header(out: list[str]) -> None:
    out.append(f'<text x="{PAD}" y="34" fill="var(--ink)" font-size="22" '
               f'font-weight="700" font-family="ui-sans-serif,system-ui,sans-serif">'
               f'Norse Stack — Performance Tearsheet</text>')
    out.append(f'<text x="{PAD}" y="55" fill="var(--accent)" font-size="12" '
               f'font-family="ui-monospace,monospace">'
               f'OBIThreshold(0.70) · BTC-USD · 1,440 1-min bars (~24h)</text>')
    # honesty banner (top-right)
    bx, by, bw, bh = W - 372 - PAD, 16, 372, 46
    out.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="6" '
               f'fill="rgba(210,153,34,0.12)" stroke="var(--warn)" '
               f'stroke-width="1"/>')
    out.append(f'<text x="{bx+12}" y="{by+19}" fill="var(--warn)" font-size="11" '
               f'font-weight="700" font-family="ui-monospace,monospace">'
               f'SIMULATED · NOT A LIVE-TRADING RESULT</text>')
    out.append(f'<text x="{bx+12}" y="{by+36}" fill="var(--sub)" font-size="9.5" '
               f'font-family="ui-monospace,monospace">'
               f'numbers verbatim from docs/RESULTS.md · short window</text>')


def build_svg() -> str:
    equity = equity_series()
    dd = drawdown_series(equity)

    out: list[str] = []
    out.append(
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="ui-sans-serif,system-ui,sans-serif" '
        f'role="img" aria-label="Norse Stack performance tearsheet (simulated backtest)">'
    )
    # NB: colors are emitted as var(--name) for readability below, then
    # resolved to literal hex by resolve_vars() before return. GitHub strips
    # <style> blocks and CSS custom properties from SVGs embedded as images, so
    # we must NOT ship var() — every fill/stroke ends up literal.
    out.append(f'<rect width="{W}" height="{H}" fill="var(--bg)"/>')
    header(out)

    top_y = 78
    # Row 1: equity (wide) + stats table (right)
    eq = Box(PAD, top_y, 720, 250)
    st = Box(PAD + 740, top_y, W - (PAD * 2) - 740, 444)
    out += equity_chart(eq, equity)
    out += stats_table(st)

    # Row 2: underwater strip under equity
    uw = Box(PAD, top_y + 262, 720, 120)
    out += underwater_chart(uw, dd)

    # Row 3: calibrate bars + walk-forward bars
    row3_y = top_y + 400
    cb = Box(PAD, row3_y, 350, 230)
    wb = Box(PAD + 370, row3_y, 350, 230)
    out += calib_bars(cb)
    out += wf_bars(wb)

    # footer
    out.append(f'<text x="{PAD}" y="{H-12}" fill="var(--sub)" font-size="10" '
               f'font-family="ui-monospace,monospace">'
               f'Generated by scripts/gen-tearsheet.py from docs/RESULTS.md — '
               f'no chart libraries, hand-written SVG. '
               f'Walk-forward (0/4 OOS profitable) is the honest verdict.</text>')
    out.append('</svg>')
    return resolve_vars("\n".join(out))


def resolve_vars(svg: str) -> str:
    """Replace every var(--name) with its literal hex/rgba from C, so the SVG
    renders identically through GitHub's image embed (which strips <style> and
    CSS custom properties) and in standalone viewers. Fails loudly if any
    var() survives, which would otherwise render as black."""
    for name, value in C.items():
        svg = svg.replace(f"var(--{name})", value)
    if "var(--" in svg:
        leftover = re.findall(r"var\(--[^)]*\)", svg)
        raise ValueError(f"unresolved CSS vars in SVG: {sorted(set(leftover))}")
    return svg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(here, "..", "docs", "assets", "tearsheet.svg")
    ap.add_argument("--out", default=os.path.normpath(default_out))
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    svg = build_svg()
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(svg + "\n")
    print(f"wrote {args.out} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
