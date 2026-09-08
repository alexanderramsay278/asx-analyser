#!/usr/bin/env python3
"""
report.py - render signals to a standalone HTML file.

Same data and the same code path as asx_analyser.py: signals come from
strategy.py, prices come from Yahoo Finance. No API key, no network calls beyond
the price fetch, no server. Writes a single self-contained HTML file with inline
CSS that opens straight in a browser.

Run:
  python report.py CPU XRO --portfolio 20000
  python report.py CPU BHP ANZ --portfolio 50000 --out signals.html
"""

from __future__ import annotations

import argparse
import html
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import strategy
from asx_analyser import RISK_PER_TRADE, fetch, position_sizing

# Palette roles. Light/dark are both selected, not an automatic flip.
CSS = """
:root {
  color-scheme: light;
  --page:        #f9f9f7;
  --surface:     #fcfcfb;
  --ink:         #0b0b0b;
  --ink-2:       #52514e;
  --muted:       #898781;
  --hairline:    #e1e0d9;
  --border:      rgba(11,11,11,0.10);
  --good:        #0ca30c;
  --warning:     #fab219;
  --critical:    #d03b3b;
  --up:          #2a78d6;
  --down:        #d03b3b;
  --neutral:     #f0efec;
  --warn-bg:     rgba(250,178,25,0.10);
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page:      #0d0d0d;
    --surface:   #1a1a19;
    --ink:       #ffffff;
    --ink-2:     #c3c2b7;
    --muted:     #898781;
    --hairline:  #2c2c2a;
    --border:    rgba(255,255,255,0.10);
    --up:        #3987e5;
    --neutral:   #383835;
    --warn-bg:   rgba(250,178,25,0.14);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
  --hairline: #2c2c2a; --border: rgba(255,255,255,0.10);
  --up: #3987e5; --neutral: #383835; --warn-bg: rgba(250,178,25,0.14);
}

* { box-sizing: border-box; }
body {
  margin: 0; padding: 40px 24px 56px;
  background: var(--page); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Inter, Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 860px; margin: 0 auto; }

header.page { margin-bottom: 28px; }
header.page h1 { margin: 0 0 4px; font-size: 21px; font-weight: 650; letter-spacing: -0.01em; }
header.page p { margin: 0; color: var(--ink-2); font-size: 13.5px; }

.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 14px; padding: 26px 28px 24px; margin-bottom: 20px;
}
.card-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 4px; }
.ticker { font-size: 19px; font-weight: 650; letter-spacing: -0.01em; }
.asof { color: var(--muted); font-size: 12.5px; margin-left: auto; }
.price { color: var(--ink-2); font-size: 14px; }

.badge {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 5px 12px 5px 10px; border-radius: 999px;
  font-size: 13px; font-weight: 620; letter-spacing: 0.01em;
  border: 1px solid; margin: 12px 0 6px;
}
.badge .dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.badge.buy    { color: var(--good);     border-color: color-mix(in srgb, var(--good) 40%, transparent); }
.badge.wait   { color: var(--warning);  border-color: color-mix(in srgb, var(--warning) 45%, transparent); }
.badge.avoid  { color: var(--critical); border-color: color-mix(in srgb, var(--critical) 40%, transparent); }
.reason { color: var(--ink-2); font-size: 13.5px; margin: 0 0 20px; }

.section-label {
  font-size: 11px; font-weight: 640; letter-spacing: 0.07em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 10px;
}

.checks { display: grid; grid-template-columns: repeat(4, 1fr); gap: 2px; margin-bottom: 22px; }
.check { background: var(--surface); border: 1px solid var(--hairline); padding: 11px 13px; }
.checks .check:first-child { border-radius: 9px 0 0 9px; }
.checks .check:last-child  { border-radius: 0 9px 9px 0; }
.check .name { font-size: 12px; color: var(--ink-2); margin-bottom: 5px; }
.check .state { display: flex; align-items: center; gap: 5px; font-size: 12.5px; font-weight: 640; margin-bottom: 3px; }
.check .state.pass { color: var(--good); }
.check .state.fail { color: var(--critical); }
.check .detail { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }

.levels { margin-bottom: 8px; }
.bar { position: relative; height: 34px; margin: 26px 0 58px; }
.track { position: absolute; top: 15px; left: 0; right: 0; height: 4px; background: var(--neutral); border-radius: 2px; }
.arm { position: absolute; top: 15px; height: 4px; }
.arm.risk   { background: var(--down); border-radius: 2px 0 0 2px; }
.arm.reward { background: var(--up);   border-radius: 0 2px 2px 0; }
.pin { position: absolute; top: 9px; width: 3px; height: 16px; border-radius: 2px; background: var(--ink);
       box-shadow: 0 0 0 2px var(--surface); }
.pin-label { position: absolute; top: 32px; font-size: 11.5px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.pin-label .v { font-weight: 640; }
.pin-label .k { color: var(--muted); display: block; font-size: 10.5px; letter-spacing: 0.04em; text-transform: uppercase; }
.pin-label.mid { transform: translateX(-50%); text-align: center; }
.pin-label.right { right: 0; text-align: right; }

.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-bottom: 20px; }
.tile .label { font-size: 12px; color: var(--ink-2); margin-bottom: 3px; }
.tile .value { font-size: 20px; font-weight: 620; letter-spacing: -0.01em; }
.tile .sub { font-size: 11.5px; color: var(--muted); margin-top: 1px; }

.callout {
  border: 1px solid color-mix(in srgb, var(--warning) 45%, transparent);
  background: var(--warn-bg); border-radius: 10px; padding: 14px 16px;
}
.callout .head { display: flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 650;
                 letter-spacing: 0.03em; text-transform: uppercase; margin-bottom: 6px; }
.callout .head .icon { font-size: 13px; }
.callout p { margin: 0; font-size: 13.5px; color: var(--ink-2); }
.callout b { color: var(--ink); font-variant-numeric: tabular-nums; }

footer.page { color: var(--muted); font-size: 12px; margin-top: 26px; line-height: 1.6; }
footer.page code { font-size: 11.5px; }
@media (max-width: 620px) {
  .checks, .grid { grid-template-columns: 1fr 1fr; }
  .checks .check { border-radius: 0 !important; }
}
"""


def esc(text: object) -> str:
    return html.escape(str(text))


def price(value: float) -> str:
    """Share prices keep cents."""
    sign = "−" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def money(value: float) -> str:
    """Dollar amounts: cents are noise above $1,000."""
    sign = "−" if value < 0 else ""
    magnitude = abs(value)
    body = f"{magnitude:,.0f}" if magnitude >= 1000 or magnitude == int(magnitude) else f"{magnitude:,.2f}"
    return f"{sign}${body}"


def level_bar(sig: strategy.Signal) -> str:
    """A one-dimensional range: stop - reference - target, to scale.

    Downside and upside arms are the documented diverging pair, so the
    asymmetry between risk and reward is visible rather than inferred.
    """
    low, high = sig.stop, max(sig.target, sig.reference_price)
    span = high - low
    if span <= 0:
        return ""
    ref_pct = 100.0 * (sig.reference_price - low) / span
    tgt_pct = 100.0 * (sig.target - low) / span

    stop_pct = 100.0 * (sig.stop / sig.reference_price - 1)
    target_pct = 100.0 * (sig.target / sig.reference_price - 1)

    return f"""
    <div class="bar">
      <div class="track"></div>
      <div class="arm risk" style="left:0; width:{ref_pct:.2f}%"></div>
      <div class="arm reward" style="left:{ref_pct:.2f}%; width:{max(tgt_pct - ref_pct, 0):.2f}%"></div>
      <div class="pin" style="left:0"></div>
      <div class="pin" style="left:{ref_pct:.2f}%"></div>
      <div class="pin" style="left:calc({tgt_pct:.2f}% - 3px)"></div>
      <div class="pin-label" style="left:0">
        <span class="k">Stop</span><span class="v">{price(sig.stop)}</span> <span class="k" style="display:inline">{stop_pct:+.1f}%</span>
      </div>
      <div class="pin-label mid" style="left:{ref_pct:.2f}%">
        <span class="k">Reference</span><span class="v">{price(sig.reference_price)}</span>
      </div>
      <div class="pin-label right">
        <span class="k">Target</span><span class="v">{price(sig.target)}</span> <span class="k" style="display:inline">{target_pct:+.1f}%</span>
      </div>
    </div>"""


def check_tiles(sig: strategy.Signal) -> str:
    rows = [
        ("Trend", sig.checks["trend"], f"{sig.close:,.2f} vs MA100 {sig.ma_trend:,.2f}"),
        ("Oversold", sig.checks["oversold"], f"RSI(2) {sig.rsi:.1f} — need &lt; {strategy.RSI_ENTRY:g}"),
        ("Liquidity", sig.checks["liquidity"], f"{sig.avg_volume:,.0f} avg vol"),
        ("Volatility", sig.checks["volatility"], f"ATR {sig.atr_pct:.1f}% of price"),
    ]
    out = []
    for name, ok, detail in rows:
        state = "pass" if ok else "fail"
        mark = "✓" if ok else "✕"
        word = "PASS" if ok else "FAIL"
        out.append(
            f'<div class="check"><div class="name">{esc(name)}</div>'
            f'<div class="state {state}"><span>{mark}</span><span>{word}</span></div>'
            f'<div class="detail">{detail}</div></div>'
        )
    return f'<div class="checks">{"".join(out)}</div>'


def render_card(sig: strategy.Signal, sizing: dict, portfolio: float) -> str:
    cls = sig.signal.lower()
    parts = [
        '<section class="card">',
        '<div class="card-head">',
        f'<span class="ticker">{esc(sig.ticker)}.AX</span>',
        f'<span class="price">{price(sig.close)}</span>',
        f'<span class="asof">as of {sig.as_of:%d %b %Y}</span>',
        "</div>",
        f'<div class="badge {cls}"><span class="dot"></span>{esc(sig.signal)}</div>',
        f'<p class="reason">{esc("; ".join(sig.reasons))}</p>',
        '<p class="section-label">Entry conditions</p>',
        check_tiles(sig),
    ]

    if sig.signal == "BUY" and sizing.get("viable"):
        parts += [
            '<p class="section-label">Levels — reference is the last close; a real fill is the next open</p>',
            '<div class="levels">', level_bar(sig), "</div>",
            '<div class="grid">',
            f'<div class="tile"><div class="label">Risk / reward</div>'
            f'<div class="value">{sig.risk_reward:.2f} : 1</div>'
            f'<div class="sub">stop {strategy.STOP_ATR_MULT:g}× ATR, target {strategy.EXIT_MA}-day mean</div></div>',
            f'<div class="tile"><div class="label">Position</div>'
            f'<div class="value">{sizing["shares"]:,} sh</div>'
            f'<div class="sub">{money(sizing["position_value"])} · {sizing["pct_of_portfolio"]:.1f}% of portfolio</div></div>',
            f'<div class="tile"><div class="label">Risk budget</div>'
            f'<div class="value">{money(sizing["risk_budget"])}</div>'
            f'<div class="sub">{RISK_PER_TRADE:.0%} of {money(portfolio)}</div></div>',
            "</div>",
        ]

        net = sizing["expected_net"]
        if net < 0:
            parts.append(
                '<div class="callout"><div class="head"><span class="icon">⚠</span>'
                "<span>Cost warning</span></div><p>"
                f"At this position size the measured edge does not cover costs — expected value "
                f"<b>{money(net)} per trade</b>. Backtesting over 1,418 trades put the gross edge at "
                f"<b>{strategy.MEASURED_EDGE:.3%}</b> of position value, against "
                f"<b>{money(2 * strategy.BROKERAGE_PER_SIDE)}</b> brokerage plus slippage. "
                f"Positions must exceed <b>{money(sizing['breakeven_size'])}</b> before this strategy "
                f"is profitable at all.</p></div>"
            )
        else:
            parts.append(
                '<div class="callout" style="border-color:var(--border);background:transparent">'
                '<p>Expected value <b>' + money(net) + " per trade</b> at this size; "
                f"breakeven is <b>{money(sizing['breakeven_size'])}</b> per position.</p></div>"
            )

    parts.append("</section>")
    return "".join(parts)


def build(signals: list[tuple[strategy.Signal, dict]], portfolio: float) -> str:
    cards = "".join(render_card(sig, sizing, portfolio) for sig, sizing in signals)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASX Mean-Reversion Screen</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<header class="page">
  <h1>ASX Mean-Reversion Screen</h1>
  <p>Deterministic signals from <code>strategy.py</code> · position sizing at
     {RISK_PER_TRADE:.0%} portfolio risk on {money(portfolio)} ·
     generated {datetime.now():%d %b %Y}</p>
</header>
{cards}
<footer class="page">
  Signals are generated by rules, not by a model, and are reproducible from price data alone.
  Backtested over 1,418 trades (2012–2026) with 2021 onward held out: the strategy carries a
  +{strategy.MEASURED_EDGE:.3%} per-trade gross edge that is fully consumed by ASX retail
  transaction costs. Educational use only — not financial advice.
</footer>
</div></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Render ASX signals to a standalone HTML report.")
    ap.add_argument("tickers", nargs="+", help="ASX codes, e.g. CPU XRO BHP")
    ap.add_argument("--portfolio", type=float, default=20_000.0)
    ap.add_argument("--out", default="report.html")
    ap.add_argument("--no-open", action="store_true", help="do not open the file in a browser")
    args = ap.parse_args()

    results = []
    for ticker in args.tickers:
        print(f"  fetching {ticker.upper()}...")
        hist = fetch(ticker)
        if hist is None:
            continue
        sig = strategy.evaluate(ticker.upper(), strategy.build_features(hist))
        results.append((sig, position_sizing(sig, args.portfolio)))

    if not results:
        print("No usable tickers.")
        return 1

    path = Path(args.out).resolve()
    path.write_text(build(results, args.portfolio), encoding="utf-8")
    print(f"\n  wrote {path}")
    for sig, _ in results:
        print(f"    {sig.ticker:<5} {sig.signal}")

    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
