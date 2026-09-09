# ASX Mean-Reversion Analyser

A Python tool that screens ASX-listed equities for mean-reversion swing setups, returns a `BUY / WAIT / AVOID` signal with stop, target and risk-based sizing, and — because the strategy was backtested — tells you when your account is too small for the signal to be worth trading.

Built independently as a first-year Commerce and Economics student.

**The headline result is negative, and it is stated up front on purpose:** across ~1,400 backtested trades the signal carries a real, out-of-sample-consistent edge of **+0.21% per trade**, and ASX retail transaction costs are roughly three times larger than that edge. The strategy is capital-gated, not idea-gated.

---

## Install and run

```bash
git clone https://github.com/alexanderramsay278/asx-analyser.git
cd asx-analyser
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

```bash
python asx_analyser.py CBA
python asx_analyser.py CBA BHP NAB --portfolio 50000
python report.py CPU XRO --portfolio 20000   # writes a standalone HTML report
python -m pytest test_strategy.py -v
python backtest.py               # reproduces every figure below;
                                 # writes backtest_trades.csv
```

`report.py` writes a self-contained HTML file — inline CSS, no scripts, no external requests, light and dark — from the same signals the terminal produces.

The optional `--commentary` flag adds qualitative context from the Anthropic API and requires `ANTHROPIC_API_KEY`. **It cannot change the signal, the levels, or the sizing.** Everything else runs offline apart from Yahoo Finance price data.

---

## Architecture

```
strategy.py        indicators + signal rules. Deterministic. No model calls.
  ├── asx_analyser.py    live screen (terminal); optional LLM commentary layer
  ├── report.py          same signals rendered to a standalone HTML file
  ├── backtest.py        portfolio simulation over historical data
  └── test_strategy.py   15 tests, including a no-lookahead property test
```

`strategy.py` is the single source of truth. The analyser and the backtest import from it rather than each defining the rules themselves, so **the backtest is evidence about the tool that actually ships**. That property is the whole reason the module exists — see below.

---

## Design: why the model is not in the signal path

A trading signal has to be two things: **reproducible** and **testable**. A language model is neither.

It is non-deterministic, so the same inputs need not produce the same call twice. And its training data overlaps any historical period you would test it on, so it may already know how the next bar resolved — lookahead contamination that cannot be measured or removed. A signal you cannot backtest is a signal you cannot evaluate.

So the architecture puts the model outside the signal path entirely:

- **`strategy.py` decides everything that matters.** Indicators, entry and exit rules, stop and target placement. Deterministic, unit-tested, reproducible from price data alone.
- **The model is an optional commentary layer.** With `--commentary` it receives the finished signal and adds qualitative context — sector conditions, event risk, reasons a name might suit a 2-5 day hold poorly. It is explicitly instructed not to contradict the signal, and it cannot alter the call, the levels, or the sizing.

The second design rule is that **the analyser and the backtest import the same module**. If the live tool and the test harness each defined the rules themselves, they could drift apart silently, and the backtest would be evidence about the harness rather than about the tool. Sharing `strategy.py` makes that structurally impossible.

This is also why the backtest covers the deterministic layer only. The commentary layer is excluded by construction, not by oversight.

---

## The strategy

Mean reversion inside an established uptrend. Liquid trending stocks that pull back sharply have a statistical tendency to revert; gating every entry behind a long-term trend filter avoids the classic failure mode of catching a falling knife in a structurally declining name.

| Component | Rule |
|---|---|
| Trend filter | Close > 100-day MA |
| Entry trigger | Wilder RSI(2) < 10 |
| Liquidity | 20-day average volume > 500,000 |
| Volatility | ATR(5) between 0.8% and 6.0% of price |
| Stop | Entry − 2 × ATR |
| Exit | RSI(2) > 70, or close above the 5-day MA, else timeout at 10 days |
| Sizing | 1% portfolio risk per trade, 4 concurrent positions |

### Why the ASX specifically

Most retail mean-reversion material targets US equities. Australia differs in ways that turn out to be decisive: flat brokerage of roughly $5–20 per side makes trade frequency expensive, liquidity falls away steeply outside the ASX 200, and the US Pattern Day Trader rule does not apply. The first of those is what the backtest ended up measuring.

---

## Backtest results

~1,400 trades, 2012–2026, with **2021 onward held out** and never used for parameter selection. Figures below are the run of **9 September 2026** — see *Reproducibility* below.

**Method.** Signals computed on bar *t*'s close are filled at bar *t+1*'s **open**. Stops are the only intraday event, and a bar gapping below the stop fills at the open rather than the stop price. A bar touching both stop and target is assumed to have hit the **stop** first, since daily bars do not reveal intraday sequence. Brokerage is charged on both sides and slippage on every fill. Positions compete for four portfolio slots.

| | In-sample (2012–2020) | Out-of-sample (2021–2026) |
|---|---|---|
| Trades | 880 | 538 |
| Win rate | 46.5% | 48.3% |
| Average win | +$53.80 | +$54.76 |
| Average loss | −$73.85 | −$87.16 |
| **Expectancy per trade** | **−$14.52** | **−$18.58** |
| Average hold | 4.7 days | 4.7 days |
| CAGR | −10.71% | −11.72% |
| Max drawdown | −66.48% | −51.19% |
| Sharpe | −1.31 | −1.43 |
| **Buy & hold STW.AX** | **CAGR +9.27%** | **CAGR +9.59%** |

The strategy lost money outright and lost to buy-and-hold in both periods.

### Where the money went

| Layer | Per trade (average position $3,524) |
|---|---|
| **Raw signal edge** | **+$7.46**  (+0.21% of position) |
| − Slippage (10bps round trip) | −$3.52 |
| − Brokerage ($10 × 2 sides) | −$20.00 |
| **Net expectancy** | **−$16.06** |

**The signal works. The cost structure kills it.**

Breakeven brokerage is **under $2 per side**, which no Australian retail broker offers. Solving for position size instead — the edge is 0.21%, slippage takes 0.10%, leaving 0.11% to cover $20 of fixed brokerage:

```
minimum viable position = $20 / 0.0011 ≈ $18,000
at 4 concurrent slots   ≈ $72,000 portfolio floor
```

`asx_analyser.py` computes this live and prints a cost warning whenever your position sizing falls below it.

### Why a negative result is worth publishing

The edge held out of sample. Win rate moved 46.4% → 48.3% and the per-trade edge was unchanged across 5.6 years the parameters were never fitted to. An overfit strategy collapses on unseen data; this one did not. That makes the diagnosis trustworthy: the edge is real, it is simply smaller than the cost of harvesting it.

It also makes the failure **structural rather than parametric**. Cost arithmetic does not move much when you nudge an RSI threshold, so the conclusion is far more robust than a return figure would have been.

No parameters were adjusted after seeing the out-of-sample result.

### Reproducibility

Price data is fetched live, and Yahoo revises adjusted closes as dividends and corporate actions settle. Repeated runs therefore land between **0.207% and 0.215%** per-trade edge, on **1,417–1,419** trades, for a net of **−$15.95 to −$16.26**.

Headline figures are quoted to two significant figures for that reason: the finding is robust at that precision, and a third decimal place would not be. `strategy.MEASURED_EDGE` — which drives the runtime cost warning — is set to `0.0021` on the same basis. Pinning a price-data snapshot so runs are bit-identical is on the roadmap.

---

## Open question

The exit breakdown shows a badly asymmetric payoff: roughly 80% of trades are small wins, 11% are large losses. In-sample, 157 stop-outs cost −$22,939 against +$11,018 from 709 target exits. Gross P&L is dominated by stops.

A mean-reversion system enters *because* price has fallen. A tight volatility stop may therefore exit precisely the trades that were about to revert — which is why Connors' original research runs these systems without stops. That is a documented design question rather than a tuning knob, but it must be tested on the in-sample period only and reported alongside these results, not instead of them.

---

## Honest limitations

- **Survivorship bias.** The universe is today's large caps; companies that delisted or collapsed are absent, which biases results upward.
- **Universe is hand-picked** — 20 names, not a rules-based liquidity screen.
- **One parameter set.** No robustness surface has been mapped.
- **Same-bar stop/target ambiguity** is resolved pessimistically. Conservative, but still a modelling assumption.
- **No dividend timing, franking credits, or tax.**
- **No live execution.** Signals only; no broker connection.
- **Not production trading infrastructure**, and not a recommendation to trade this strategy — the results above are the argument against doing so at retail size.

---

## Roadmap

- [x] Portfolio backtest with realistic ASX brokerage and slippage
- [x] Held-out out-of-sample period
- [x] Deterministic signal layer, shared by the tool and the backtest
- [x] Test suite including a no-lookahead property test
- [ ] Test the no-stop variant against the asymmetric payoff above
- [ ] Walk-forward parameter validation
- [ ] Rules-based universe screen (top 100 by liquidity) to remove hand-picking bias
- [ ] Multi-ticker batch screening across the ASX 200

---

## About

Built by Alexander Ramsay, Bachelor of Commerce / Economics, Macquarie University, as part of building the quantitative and engineering foundation for a career in trading.

Educational use only. Not financial advice.
