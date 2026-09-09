#!/usr/bin/env python3
"""
make_chart.py - render the equity curve from backtest_equity.csv to an SVG.

Charts the HELD-OUT period only. The in-sample and held-out runs each start
from the same opening equity, so splicing them would draw a discontinuity that
is an artefact of the harness rather than anything the strategy did. The
held-out period is the honest number in any case.

Log scale, because a curve that loses most of its value cannot be read on a
linear axis beside a benchmark that rises. Both series are indexed to 100 at the
start of the period so they are directly comparable.

Run:  python make_chart.py      (after python backtest.py)
"""

from __future__ import annotations

import pandas as pd

W, H = 900, 380
PAD_L, PAD_R, PAD_T, PAD_B = 58, 132, 26, 40

STRATEGY = "#2a78d6"      # categorical slot 1
BENCHMARK = "#eb6834"     # categorical slot 2
GRID, AXIS, MUTED, INK = "#e1e0d9", "#c3c2b7", "#898781", "#0b0b0b"

OOS_START = pd.Timestamp("2021-01-01")


def build_svg(df: pd.DataFrame) -> str:
    import math

    df = df.dropna()
    base = df.iloc[0]
    idx = pd.DataFrame({"strategy": 100 * df["strategy"] / base["strategy"],
                        "benchmark": 100 * df["benchmark"] / base["benchmark"]})

    lo, hi = float(idx.min().min()), float(idx.max().max())
    lo, hi = max(lo * 0.85, 1.0), hi * 1.12
    log_lo, log_hi = math.log10(lo), math.log10(hi)
    x0, x1 = df.index[0].value, df.index[-1].value

    def px(ts) -> float:
        return PAD_L + (ts.value - x0) / (x1 - x0) * (W - PAD_L - PAD_R)

    def py(v: float) -> float:
        return PAD_T + (log_hi - math.log10(max(v, lo))) / (log_hi - log_lo) * (H - PAD_T - PAD_B)

    def path(series: pd.Series) -> str:
        step = max(len(series) // 1400, 1)
        pts = [f"{px(t):.1f},{py(v):.1f}" for t, v in series.iloc[::step].items()]
        return "M" + " L".join(pts)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'font-family="ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#fcfcfb"/>',
    ]

    # Horizontal gridlines at decade-ish ticks.
    for tick in (10, 25, 50, 100, 200, 400):
        if lo <= tick <= hi:
            y = py(tick)
            parts.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" '
                         f'stroke="{GRID}" stroke-width="1"/>')
            parts.append(f'<text x="{PAD_L - 10}" y="{y + 4:.1f}" text-anchor="end" '
                         f'font-size="11" fill="{MUTED}">{tick}</text>')

    # Year ticks.
    for year in range(df.index[0].year, df.index[-1].year + 1):
        ts = pd.Timestamp(f"{year}-01-01")
        if df.index[0] <= ts <= df.index[-1]:
            parts.append(f'<text x="{px(ts):.1f}" y="{H - 14}" text-anchor="middle" '
                         f'font-size="11" fill="{MUTED}">{year}</text>')

    parts.append(f'<line x1="{PAD_L}" y1="{H - PAD_B}" x2="{W - PAD_R}" y2="{H - PAD_B}" '
                 f'stroke="{AXIS}" stroke-width="1"/>')

    for name, colour in (("benchmark", BENCHMARK), ("strategy", STRATEGY)):
        parts.append(f'<path d="{path(idx[name])}" fill="none" stroke="{colour}" '
                     f'stroke-width="2" stroke-linejoin="round"/>')

    # Direct labels beat a legend box when the series end apart.
    for name, colour, label in (("benchmark", BENCHMARK, "Buy &amp; hold STW.AX"),
                                ("strategy", STRATEGY, "Strategy")):
        end = idx[name].iloc[-1]
        parts.append(f'<circle cx="{px(df.index[-1]):.1f}" cy="{py(end):.1f}" r="3.5" fill="{colour}"/>')
        parts.append(f'<text x="{W - PAD_R + 10}" y="{py(end) - 2:.1f}" font-size="12" '
                     f'font-weight="600" fill="{colour}">{label}</text>')
        parts.append(f'<text x="{W - PAD_R + 10}" y="{py(end) + 13:.1f}" font-size="11" '
                     f'fill="{MUTED}">{end:.0f} (from 100)</text>')

    parts.append(f'<text x="{PAD_L}" y="16" font-size="12" fill="{INK}" font-weight="600">'
                 f'Held-out period · equity indexed to 100 · log scale</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    df = pd.read_csv("backtest_equity.csv", index_col=0, parse_dates=True)
    df = df.loc[df.index >= OOS_START]
    svg = build_svg(df)
    with open("equity_curve.svg", "w", encoding="utf-8") as fh:
        fh.write(svg)
    print(f"wrote equity_curve.svg ({len(svg):,} bytes)")


if __name__ == "__main__":
    main()
