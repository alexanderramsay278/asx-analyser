# ASX Mean-Reversion Analyser

A Python tool that screens ASX-listed equities for mean-reversion swing trade setups and outputs a structured `BUY / WAIT / AVOID` signal with entry, stop, target, and position-sizing levels.

Built independently as a first-year Commerce / Economics student exploring an honest question: **can a large language model, constrained by a fixed quantitative framework, produce more transparent trade reasoning than a hardcoded rules engine — and where does it fail?**

---

## Architecture

The tool deliberately separates **feature computation** from **decision reasoning**:

| Layer | Responsibility |
|---|---|
| **Python** | Pulls 6-month OHLCV data from Yahoo Finance (`yfinance`) and computes the technical features: 100-day moving average, 2-period RSI, 5-day ATR, consecutive down days, 20-day average volume, 52-week range. |
| **Anthropic Claude API** | Receives the computed features plus a fixed mean-reversion framework, returns a structured JSON signal with entry / stop / target / risk-reward / max position size / key risks. |

The deterministic part (indicators) is in code; the reasoning part (signal interpretation) is delegated to the LLM under a strict prompt. This is the actual interesting question the project explores — not whether mean reversion works, but whether constrained LLM reasoning over real features can rival rules.

---

## The Strategy

**Mean reversion within an uptrend.**

| Component | Rule |
|---|---|
| Trend filter | Current price > 100-day moving average |
| Momentum trigger | 2-period RSI below ~15 (oversold pullback) |
| Confirmation | Consecutive down days ≥ 3 strengthens the case |
| Volatility | 5-day ATR as % of price (classified LOW / MEDIUM / HIGH) |
| Liquidity | 20-day average volume |
| Signal | `BUY` if trend passes AND RSI oversold; `WAIT` if partial; `AVOID` if trend filter fails |

The thesis: liquid, trending stocks experiencing short-term oversold pullbacks have a statistical tendency to revert toward their trend. Gating every trade behind a long-term trend filter avoids the classic mean-reversion failure mode — catching a falling knife on a name in structural decline.

---

## Why ASX Specifically

Most retail mean-reversion content targets US equities. The ASX is structurally different:

- Higher brokerage cost per trade (~$5–$20 AUD) kills scalping → swing horizon (2–5 day holds) is the natural fit.
- No Pattern Day Trader rule constraint (a US-only regulation).
- Liquidity drops off steeply outside the ASX 200 → the liquidity check is non-optional, not nice-to-have.

The framework is calibrated for these conditions.

---

## Setup

```bash
git clone https://github.com/<your-username>/asx-analyser.git
cd asx-analyser
pip install -r requirements.txt
```

Set your Anthropic API key as an environment variable (get one at https://console.anthropic.com):

```powershell
# PowerShell (Windows)
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

```bash
# bash / zsh (macOS / Linux)
export ANTHROPIC_API_KEY="sk-ant-..."
```

Run:

```bash
python asx_analyser.py
```

The tool runs as an interactive REPL — enter an ASX ticker (e.g. `BHP`, `CBA`, `WES`) and it returns the analysis.

---

## Example Output

```
────────────────────────────────────────────────────────────
CBA.AX  —  Commonwealth Bank of Australia  (Financials)
Current price: $112.40 AUD
────────────────────────────────────────────────────────────

  Signal:   BUY
  Trend in place, RSI deeply oversold on a 3-day pullback.

  Trend filter:   PASS
  Price 4.2% above 100-day MA ($107.86).

  RSI (2-period): OVERSOLD
  RSI at 12.4 — short-term washout against a healthy trend.

  Consecutive down days: 3
  Volatility:  MEDIUM  |  Liquidity: GOOD

  Overall risk: 4/10
  Trend 2/10  |  Volatility 4/10  |  Liquidity 2/10  |  Event 5/10

  Trade levels:
  Entry:     $112.40
  Stop loss: $108.90
  Target:    $118.20
  Risk/reward: 1:1.65  |  Max position: 2.0% of portfolio
```

*(Example only — actual values are generated live against real market data.)*

---

## Honest Limitations

A working tool is more credible when its limits are stated up front. This is **not** production trading infrastructure.

- **No backtest harness yet.** Signal generation is implemented; vectorised backtesting with transaction costs, slippage, and out-of-sample testing is the next iteration. Until that exists, expected returns cannot be claimed.
- **Single-name analysis, not a portfolio engine.** Position sizing assumes the trade is taken in isolation; no correlation or sector-exposure awareness.
- **The LLM is the decision layer.** Strengths: nuanced reasoning, explainability. Risks: non-determinism, prompt-bound behaviour, knowledge-cutoff staleness on event risk. The structured JSON output is intentionally narrow to limit this.
- **No live execution.** Signals only — no broker integration.
- **Paper trading only.** Threshold for real money: 30+ logged trades with positive expectancy and a stable process.
- **Educational use only — not financial advice.**

---

## Roadmap

- [ ] Vectorised backtest with realistic ASX brokerage and slippage
- [ ] Walk-forward parameter validation (avoid in-sample overfit)
- [ ] Multi-ticker batch screening across the ASX 200
- [ ] CSV / journal output that plugs into a personal trade log
- [ ] Side-by-side comparison: LLM-generated signal vs. pure rules engine on the same features
- [ ] Read-only broker API integration for position-aware sizing

---

## About

Built by Alexander Ramsay, first-year Bachelor of Commerce / Economics, Macquarie University (transferring to UNSW Term 3 2026). Part of an ongoing personal effort to build the quantitative and engineering foundation for a career in proprietary trading.

Contact: alexanderramsay278@gmail.com
