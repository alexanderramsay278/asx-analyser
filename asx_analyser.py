#!/usr/bin/env python3
"""
ASX Mean-Reversion Analyser
---------------------------
Screens ASX-listed equities for mean-reversion swing setups and returns a
BUY / WAIT / AVOID signal with stop, target and risk-based position sizing.

Architecture
  - strategy.py computes every indicator and generates the signal. It is
    deterministic, reproducible, and is the exact code path the backtest runs.
  - The Anthropic Claude API is OPTIONAL (--commentary) and produces qualitative
    context only. It cannot change the signal, the levels or the sizing.

That separation is deliberate. A signal must be reproducible and testable; a
language model is neither, and its training data overlaps any period you would
backtest it on. Keeping it outside the signal path is what makes the published
backtest meaningful. See README.md, "Design".

Setup
  1. pip install -r requirements.txt
  2. Optional, only for --commentary:
       PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
       bash/zsh:    export ANTHROPIC_API_KEY="sk-ant-..."
  3. python asx_analyser.py CBA
     python asx_analyser.py CBA BHP NAB --portfolio 50000 --commentary
"""

from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
import yfinance as yf

import strategy

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
GREEN, RED, YELLOW, CYAN = "\033[92m", "\033[91m", "\033[93m", "\033[96m"

RISK_PER_TRADE = 0.01          # 1% of portfolio risked per trade
HISTORY_PERIOD = "2y"          # enough to warm a real 100-day MA and 52-week range


def fetch(ticker: str) -> pd.DataFrame | None:
    """Fetch daily OHLCV for an ASX ticker. Returns None if unusable."""
    symbol = ticker if ticker.upper().endswith(".AX") else f"{ticker.upper()}.AX"
    try:
        hist = yf.Ticker(symbol).history(period=HISTORY_PERIOD, auto_adjust=True)
    except Exception as exc:
        print(f"  {RED}Could not fetch {symbol}: {exc}{RESET}")
        return None

    if hist.empty:
        print(f"  {RED}No data for {symbol}. Check the ticker is a valid ASX code.{RESET}")
        return None

    # The trend filter is a 100-day mean. Anything shorter is a different
    # indicator wearing the same name, so refuse rather than silently degrade.
    if len(hist) < strategy.TREND_MA + 20:
        print(f"  {RED}{symbol}: only {len(hist)} sessions available; "
              f"need {strategy.TREND_MA + 20} for a valid 100-day trend filter.{RESET}")
        return None

    return hist


def position_sizing(sig: strategy.Signal, portfolio: float) -> dict:
    """Risk-based sizing, then an honest check against the measured cost floor."""
    risk_per_share = sig.reference_price - sig.stop
    if risk_per_share <= 0:
        return {"viable": False}

    risk_budget = portfolio * RISK_PER_TRADE
    shares = int(risk_budget // risk_per_share)
    position_value = shares * sig.reference_price

    return {
        "viable": shares > 0,
        "shares": shares,
        "position_value": position_value,
        "pct_of_portfolio": 100.0 * position_value / portfolio if portfolio else 0.0,
        "risk_budget": risk_budget,
        "expected_net": strategy.expected_net_per_trade(position_value),
        "breakeven_size": strategy.breakeven_position_size(),
    }


def commentary(sig: strategy.Signal) -> str | None:
    """Optional qualitative context from Claude. Never touches the signal."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"  {YELLOW}--commentary needs ANTHROPIC_API_KEY; skipping.{RESET}")
        return None

    try:
        import anthropic
    except ImportError:
        print(f"  {YELLOW}anthropic package not installed; skipping commentary.{RESET}")
        return None

    prompt = f"""You are reviewing the output of a deterministic mean-reversion screen for {sig.ticker}.AX.

The signal has already been decided by a rules engine. Do not restate it, argue
with it, or suggest different price levels. Your job is qualitative context only.

  Signal: {sig.signal} ({'; '.join(sig.reasons)})
  Close: {sig.close:.2f}   100-day MA: {sig.ma_trend:.2f}
  RSI(2): {sig.rsi:.1f}    ATR(5): {sig.atr:.2f} ({sig.atr_pct:.1f}% of price)
  20-day average volume: {sig.avg_volume:,.0f}
  Consecutive down days: {sig.down_days}

In 3-4 sentences, cover what a swing trader should be aware of that price data
alone does not capture: sector conditions, the kind of company-specific event
risk that would invalidate a mean-reversion assumption, and any reason this
particular name might be a poor fit for a 2-5 day hold.

Begin with this caveat verbatim: "Model commentary, not a signal input."
"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as exc:
        print(f"  {YELLOW}Commentary unavailable: {exc}{RESET}")
        return None


def wrap(text: str, width: int = 66, indent: str = "  ") -> str:
    words, lines, line = text.split(), [], indent
    for word in words:
        if len(line) + len(word) + 1 > width and line.strip():
            lines.append(line)
            line = indent + word
        else:
            line = f"{line} {word}" if line.strip() else indent + word
    if line.strip():
        lines.append(line)
    return "\n".join(lines)


def render(sig: strategy.Signal, sizing: dict, note: str | None) -> None:
    colour = {"BUY": GREEN, "AVOID": RED}.get(sig.signal, YELLOW)
    tick = lambda ok: f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"

    print("\n" + "-" * 68)
    print(f"{BOLD}{sig.ticker}.AX{RESET}   {DIM}as of {sig.as_of:%d %b %Y}{RESET}")
    print("-" * 68)
    print(f"\n  Signal:  {colour}{BOLD}{sig.signal}{RESET}")
    print(f"  {DIM}{'; '.join(sig.reasons)}{RESET}")

    print(f"\n  {BOLD}Entry conditions{RESET}")
    print(f"    Trend      {tick(sig.checks['trend'])}   close {sig.close:.2f} vs 100-day MA {sig.ma_trend:.2f}")
    print(f"    Oversold   {tick(sig.checks['oversold'])}   RSI(2) {sig.rsi:.1f} (need < {strategy.RSI_ENTRY:g})")
    print(f"    Liquidity  {tick(sig.checks['liquidity'])}   20-day avg volume {sig.avg_volume:,.0f}")
    print(f"    Volatility {tick(sig.checks['volatility'])}   ATR {sig.atr_pct:.1f}% "
          f"(band {strategy.ATR_MIN_PCT}-{strategy.ATR_MAX_PCT}%)")
    print(f"    {DIM}Consecutive down days: {sig.down_days}{RESET}")

    if sig.signal == "BUY" and sizing.get("viable"):
        print(f"\n  {BOLD}Levels{RESET}  {DIM}(reference = last close; a real fill is the next open){RESET}")
        print(f"    Reference  ${sig.reference_price:.2f}")
        print(f"    {RED}Stop       ${sig.stop:.2f}{RESET}  ({strategy.STOP_ATR_MULT}x ATR, "
              f"{100 * (sig.stop / sig.reference_price - 1):+.1f}%)")
        print(f"    {CYAN}Target     ${sig.target:.2f}{RESET}  ({strategy.EXIT_MA}-day mean, "
              f"{100 * (sig.target / sig.reference_price - 1):+.1f}%)")
        print(f"    Risk/reward  {sig.risk_reward:.2f} : 1")

        print(f"\n  {BOLD}Sizing{RESET}  {DIM}({RISK_PER_TRADE:.0%} portfolio risk){RESET}")
        print(f"    {sizing['shares']:,} shares = ${sizing['position_value']:,.0f} "
              f"({sizing['pct_of_portfolio']:.1f}% of portfolio)")

        net = sizing["expected_net"]
        if net < 0:
            print(f"\n  {RED}{BOLD}COST WARNING{RESET}")
            print(wrap(
                f"At this position size the measured edge does not cover costs. "
                f"Expected value is {RED}${net:,.2f} per trade{RESET}. Backtesting over "
                f"~2,200 trades put the gross edge at {strategy.MEASURED_EDGE:.2%} of position "
                f"value, against ${2 * strategy.BROKERAGE_PER_SIDE:.0f} brokerage plus slippage. "
                f"Positions need to exceed ${sizing['breakeven_size']:,.0f} before this "
                f"strategy is profitable at all."))
        else:
            print(f"\n  {DIM}Expected value ${net:,.2f}/trade at this size "
                  f"(breakeven ${sizing['breakeven_size']:,.0f}).{RESET}")

    if note:
        print(f"\n  {BOLD}Context{RESET}")
        print(wrap(note))

    print("\n" + "-" * 68)
    print(f"{DIM}Educational use only. Not financial advice. Signals are unprofitable")
    print(f"at retail position sizes: see README.md, Backtest Results.{RESET}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="ASX mean-reversion screen.")
    parser.add_argument("tickers", nargs="*", help="ASX codes, e.g. CBA BHP NAB")
    parser.add_argument("--portfolio", type=float, default=20_000.0,
                        help="portfolio value in AUD for position sizing (default 20000)")
    parser.add_argument("--commentary", action="store_true",
                        help="add optional Claude qualitative context (needs ANTHROPIC_API_KEY)")
    args = parser.parse_args()

    tickers = args.tickers
    if not tickers:
        raw = input("Enter ASX ticker(s), space separated (e.g. CBA BHP): ").strip()
        tickers = raw.split()
    if not tickers:
        print("No tickers given.")
        return 1

    print(f"\n{BOLD}ASX Mean-Reversion Analyser{RESET}")
    print(f"{DIM}Deterministic signal from strategy.py"
          f"{' + optional model commentary' if args.commentary else ''}{RESET}")

    for ticker in tickers:
        print(f"\n  Fetching {ticker.upper()}...")
        hist = fetch(ticker)
        if hist is None:
            continue

        features = strategy.build_features(hist)
        sig = strategy.evaluate(ticker.upper(), features)
        sizing = position_sizing(sig, args.portfolio)
        note = commentary(sig) if args.commentary else None
        render(sig, sizing, note)

    return 0


if __name__ == "__main__":
    sys.exit(main())
