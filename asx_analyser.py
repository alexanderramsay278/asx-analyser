#!/usr/bin/env python3
"""
ASX Trade Analyser
------------------
Mean-reversion swing trading signal tool for ASX stocks.

Architecture:
  - Python computes the technical features (100-day MA, 2-period RSI, ATR,
    consecutive down days, average volume) from Yahoo Finance data.
  - The Anthropic Claude API reasons over the features against a fixed
    mean-reversion framework and returns a structured BUY/WAIT/AVOID signal
    with entry, stop, target, and position size.

Setup:
  1. Install dependencies:  pip install -r requirements.txt
  2. Set your API key as an environment variable (get a key at
     https://console.anthropic.com):
       PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
       bash/zsh:    export ANTHROPIC_API_KEY="sk-ant-..."
  3. Run:  python asx_analyser.py
"""

import json
import os
import sys
import anthropic
import yfinance as yf
from datetime import datetime

# ── API KEY ────────────────────────────────────────────────────────────────────
# Loaded from the ANTHROPIC_API_KEY environment variable. Never hardcode this
# value and never commit it to source control.
API_KEY = os.environ.get("ANTHROPIC_API_KEY")
# ───────────────────────────────────────────────────────────────────────────────


def fetch_price_data(ticker: str) -> dict:
    """Fetch real price data from Yahoo Finance for the given ASX ticker."""
    symbol = f"{ticker}.AX"
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period="6mo")
        if hist.empty:
            return {}

        closes = hist["Close"].tolist()
        current = round(closes[-1], 2)

        # 100-day MA (use available data if less than 100 days)
        ma100 = round(sum(closes[-100:]) / min(len(closes), 100), 2)

        # 2-period RSI
        if len(closes) >= 3:
            gains, losses = [], []
            for i in range(-3, 0):
                change = closes[i] - closes[i - 1]
                gains.append(max(change, 0))
                losses.append(max(-change, 0))
            avg_gain = sum(gains) / 2
            avg_loss = sum(losses) / 2
            if avg_loss == 0:
                rsi2 = 100
            else:
                rs = avg_gain / avg_loss
                rsi2 = round(100 - (100 / (1 + rs)), 1)
        else:
            rsi2 = None

        # Consecutive down days
        down_days = 0
        for i in range(-1, -6, -1):
            if closes[i] < closes[i - 1]:
                down_days += 1
            else:
                break

        # ATR-based volatility (5-day)
        highs = hist["High"].tolist()
        lows = hist["Low"].tolist()
        atrs = [highs[i] - lows[i] for i in range(-5, 0)]
        atr5 = round(sum(atrs) / 5, 2)
        atr_pct = round((atr5 / current) * 100, 1)

        # 52-week range
        week52_high = round(max(hist["High"].tolist()), 2)
        week52_low = round(min(hist["Low"].tolist()), 2)

        # Average volume (20-day)
        vols = hist["Volume"].tolist()
        avg_vol = int(sum(vols[-20:]) / 20)

        return {
            "symbol": symbol,
            "current_price": current,
            "ma100": ma100,
            "above_ma100": current > ma100,
            "rsi2": rsi2,
            "consecutive_down_days": down_days,
            "atr5": atr5,
            "atr_pct": atr_pct,
            "week52_high": week52_high,
            "week52_low": week52_low,
            "avg_volume_20d": avg_vol,
            "pct_from_52w_high": round(((current - week52_high) / week52_high) * 100, 1),
        }
    except Exception as e:
        return {"error": str(e)}


def analyse(ticker: str) -> dict:
    """Run the full mean-reversion analysis using real data + Claude reasoning."""
    ticker = ticker.upper().strip()
    print(f"\n  Fetching price data for {ticker}.AX...")
    data = fetch_price_data(ticker)

    if not data or "error" in data:
        print(f"  Could not fetch data. Check the ticker is a valid ASX code.")
        return {}

    today = datetime.now().strftime("%d %B %Y")

    prompt = f"""You are a quantitative trading analyst specialising in ASX equities. Today is {today}.

Here is REAL price data fetched from Yahoo Finance for {ticker}.AX:

{json.dumps(data, indent=2)}

Using this data and the mean-reversion swing trading framework (2-5 day holds), provide a structured trading assessment.

Mean-reversion rules:
- Trend filter PASSES if current_price > ma100
- RSI condition: rsi2 < 15 = OVERSOLD, rsi2 > 85 = OVERBOUGHT, else NEUTRAL
- Consecutive down days of 3+ strengthens a mean-reversion BUY case
- Signal should be BUY only if trend filter passes AND rsi2 is oversold
- Signal should be WAIT if trend is good but not yet oversold, or trend is broken but stock is oversold
- Signal should be AVOID if trend filter fails and RSI is not in a compelling range

Respond ONLY with a valid JSON object, no markdown, no preamble:

{{
  "ticker": "{ticker}",
  "company_name": "full company name",
  "sector": "sector name",
  "current_price": {data['current_price']},
  "signal": "BUY or WAIT or AVOID",
  "signal_reason": "one sentence explaining the signal",
  "trend_filter": "PASS or FAIL",
  "trend_filter_note": "price vs 100-day MA with specific numbers",
  "rsi_condition": "OVERSOLD or NEUTRAL or OVERBOUGHT",
  "rsi_note": "rsi2 value and what it means",
  "consecutive_down_days": {data['consecutive_down_days']},
  "volatility": "LOW or MEDIUM or HIGH",
  "volatility_note": "ATR% context: {data['atr_pct']}% daily range",
  "liquidity": "GOOD or ADEQUATE or POOR",
  "liquidity_note": "avg volume context",
  "risk_score": 0,
  "risk_scores_breakdown": {{
    "trend_risk": 0,
    "volatility_risk": 0,
    "liquidity_risk": 0,
    "event_risk": 0
  }},
  "entry_price": 0.00,
  "stop_loss": 0.00,
  "target_price": 0.00,
  "risk_reward_ratio": "1:X",
  "max_position_pct": 0,
  "reasoning": "3-4 sentences using the actual data provided",
  "key_risks": ["risk 1", "risk 2", "risk 3"]
}}

All risk scores are 1-10. Entry/stop/target must be realistic prices in AUD based on the actual current price of {data['current_price']}."""

    client = anthropic.Anthropic(api_key=API_KEY)
    print(f"  Analysing with Claude...")

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    text = message.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def print_result(r: dict):
    """Print a formatted analysis result to the terminal."""
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    DIM    = "\033[2m"

    signal_color = GREEN if r["signal"] == "BUY" else RED if r["signal"] == "AVOID" else YELLOW

    print("\n" + "─" * 60)
    print(f"{BOLD}{r['ticker']}.AX  —  {r['company_name']}{RESET}  {DIM}({r['sector']}){RESET}")
    print(f"Current price: {BOLD}${r['current_price']:.2f} AUD{RESET}")
    print("─" * 60)

    print(f"\n  Signal:   {signal_color}{BOLD}{r['signal']}{RESET}")
    print(f"  {DIM}{r['signal_reason']}{RESET}")

    print(f"\n  Trend filter:   {'✅ PASS' if r['trend_filter'] == 'PASS' else '❌ FAIL'}")
    print(f"  {DIM}{r['trend_filter_note']}{RESET}")

    print(f"\n  RSI (2-period): {r['rsi_condition']}")
    print(f"  {DIM}{r['rsi_note']}{RESET}")

    print(f"\n  Consecutive down days: {r['consecutive_down_days']}")
    print(f"  Volatility:  {r['volatility']}  |  Liquidity: {r['liquidity']}")

    print(f"\n  Overall risk: {BOLD}{r['risk_score']}/10{RESET}")
    bd = r["risk_scores_breakdown"]
    print(f"  {DIM}Trend {bd['trend_risk']}/10  |  Volatility {bd['volatility_risk']}/10  |  Liquidity {bd['liquidity_risk']}/10  |  Event {bd['event_risk']}/10{RESET}")

    print(f"\n  Trade levels:")
    print(f"  {GREEN}Entry:     ${r['entry_price']:.2f}{RESET}")
    print(f"  {RED}Stop loss: ${r['stop_loss']:.2f}{RESET}")
    print(f"  {CYAN}Target:    ${r['target_price']:.2f}{RESET}")
    print(f"  Risk/reward: {r['risk_reward_ratio']}  |  Max position: {r['max_position_pct']}% of portfolio")

    print(f"\n  Reasoning:")
    words = r["reasoning"].split()
    line = "  "
    for word in words:
        if len(line) + len(word) > 62:
            print(line)
            line = "  " + word + " "
        else:
            line += word + " "
    if line.strip():
        print(line)

    print(f"\n  Key risks:")
    for risk in r["key_risks"]:
        print(f"  • {risk}")

    print("\n" + "─" * 60)
    print(f"{DIM}Educational purposes only. Not financial advice.{RESET}\n")


def main():
    print("\n╔══════════════════════════════════════╗")
    print("║      ASX Mean-Reversion Analyser     ║")
    print("╚══════════════════════════════════════╝")

    if not API_KEY:
        print("\n⚠️  ANTHROPIC_API_KEY environment variable is not set.")
        print("   PowerShell:  $env:ANTHROPIC_API_KEY = \"sk-ant-...\"")
        print("   bash/zsh:    export ANTHROPIC_API_KEY=\"sk-ant-...\"")
        print("   Get a key at: https://console.anthropic.com\n")
        sys.exit(1)

    while True:
        print("\nEnter an ASX ticker (e.g. BHP, CBA, NAB) or 'quit' to exit:")
        ticker = input("  > ").strip().upper()

        if ticker in ("QUIT", "Q", "EXIT", ""):
            print("\nGoodbye.\n")
            break

        try:
            result = analyse(ticker)
            if result:
                print_result(result)
        except json.JSONDecodeError:
            print("\n  Could not parse the analysis. Try again.")
        except Exception as e:
            print(f"\n  Error: {e}")

        print("\nAnalyse another stock? (Press Enter to continue or type 'quit')")
        again = input("  > ").strip().lower()
        if again in ("quit", "q", "exit"):
            print("\nGoodbye.\n")
            break


if __name__ == "__main__":
    main()
