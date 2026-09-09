"""
backtest.py - portfolio backtest for the mean-reversion strategy.

Indicators and signal rules are imported from strategy.py, which is the same
code path asx_analyser.py uses live. This is the whole point: results here are
evidence about the shipped tool, not about a separate reimplementation.

Design decisions, all of them the conservative choice:
  - Signals computed on bar t's CLOSE are filled at bar t+1's OPEN.
  - Stops are the only intraday event.
  - A bar that gaps below the stop fills at the OPEN, not the stop price.
  - A bar touching both stop and target is assumed to have hit the STOP first;
    daily bars do not reveal intraday sequence.
  - Brokerage on both sides, slippage on every fill.
  - Positions compete for a fixed number of slots, so the equity curve reflects
    capital constraints rather than assuming unlimited parallel trades.

Run:  python backtest.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import yfinance as yf

import strategy

# -- Backtest configuration ---------------------------------------------------
# Strategy parameters live in strategy.py. Only harness settings belong here.

UNIVERSE = [
    "BHP.AX", "CBA.AX", "CSL.AX", "NAB.AX", "WBC.AX", "ANZ.AX", "WES.AX",
    "MQG.AX", "TLS.AX", "WOW.AX", "RIO.AX", "GMG.AX", "FMG.AX", "TCL.AX",
    "STO.AX", "QBE.AX", "REA.AX", "COL.AX", "ALL.AX", "SUN.AX",
]

BENCHMARK = "STW.AX"                        # SPDR ASX 200 ETF: the do-nothing option
IN_SAMPLE = ("2012-01-01", "2020-12-31")    # parameters tuned here
OUT_SAMPLE = ("2021-01-01", "2026-08-01")   # tested once, reported as-is

STARTING_EQUITY = 20_000.0
MAX_POSITIONS = 4


def load_prices(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Download with a buffer so indicators are warm on day one of the test."""
    buffered = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    raw = yf.download(tickers, start=buffered, end=end, auto_adjust=True,
                      progress=False, group_by="ticker", threads=True)

    data: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            df = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < strategy.TREND_MA + 50:
            print(f"  skipped {ticker}: insufficient history")
            continue
        if getattr(df.index, "tz", None) is not None:
            df = df.tz_localize(None)
        data[ticker] = strategy.build_features(df)
    return data


def run_backtest(data: dict[str, pd.DataFrame], start: str, end: str):
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    all_dates = sorted({d for df in data.values() for d in df.index})
    dates = [d for d in all_dates if start_ts <= d <= end_ts]

    cash = STARTING_EQUITY
    open_positions: dict[str, dict] = {}
    pending_entries: list[str] = []
    pending_exits: set[str] = set()
    trades: list[dict] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    slip = strategy.SLIPPAGE_BPS / 10_000.0
    fee = strategy.BROKERAGE_PER_SIDE

    for date in dates:
        # 1. Manage existing positions.
        for ticker in list(open_positions):
            df = data[ticker]
            if date not in df.index:
                continue
            bar = df.loc[date]
            pos = open_positions[ticker]

            exit_price = reason = None
            if ticker in pending_exits:
                exit_price = bar["Open"] * (1 - slip)      # signalled yesterday
                reason = pos["pending_reason"]
                pending_exits.discard(ticker)
            elif bar["Low"] <= pos["stop"]:
                fill = min(bar["Open"], pos["stop"])       # gap fills at the open
                exit_price = fill * (1 - slip)
                reason = "stop"

            if exit_price is not None:
                proceeds = pos["shares"] * exit_price - fee
                cash += proceeds
                trades.append({
                    "ticker": ticker,
                    "entry_date": pos["entry_date"], "exit_date": date,
                    "entry_price": pos["entry_price"], "exit_price": exit_price,
                    "shares": pos["shares"], "reason": reason,
                    "held_days": (date - pos["entry_date"]).days,
                    "pnl": proceeds - pos["cost_basis"],
                    "return_pct": 100.0 * (proceeds - pos["cost_basis"]) / pos["cost_basis"],
                })
                del open_positions[ticker]
                continue

            held = (date - pos["entry_date"]).days
            if bool(bar["exit_signal"]):
                pending_exits.add(ticker)
                pos["pending_reason"] = "target"
            elif held >= strategy.MAX_HOLD_DAYS:
                pending_exits.add(ticker)
                pos["pending_reason"] = "timeout"

        # 2. Fill yesterday's entry signals at today's open.
        for ticker in pending_entries:
            if len(open_positions) >= MAX_POSITIONS or ticker in open_positions:
                continue
            df = data[ticker]
            if date not in df.index:
                continue
            bar = df.loc[date]

            allocation = cash / (MAX_POSITIONS - len(open_positions))
            entry_price = bar["Open"] * (1 + slip)
            shares = int((allocation - fee) // entry_price)
            if shares <= 0:
                continue
            cost_basis = shares * entry_price + fee
            if cost_basis > cash:
                continue

            cash -= cost_basis
            open_positions[ticker] = {
                "entry_date": date, "entry_price": entry_price, "shares": shares,
                "cost_basis": cost_basis,
                "stop": entry_price - strategy.STOP_ATR_MULT * df["atr"].shift(1).loc[date],
                "pending_reason": None,
            }
        pending_entries = []

        # 3. Scan today's close for tomorrow's entries.
        if len(open_positions) < MAX_POSITIONS:
            for ticker, df in data.items():
                if ticker in open_positions or date not in df.index:
                    continue
                bar = df.loc[date]
                if bool(bar["entry_signal"]) and np.isfinite(bar["atr"]):
                    pending_entries.append(ticker)

        # 4. Mark to market.
        holdings = 0.0
        for ticker, pos in open_positions.items():
            df = data[ticker]
            idx = df.index[df.index <= date]
            if len(idx):
                holdings += pos["shares"] * df.loc[idx[-1], "Close"]
        equity_curve.append((date, cash + holdings))

    return pd.DataFrame(trades), pd.Series(dict(equity_curve)).sort_index()


def report(label: str, trades: pd.DataFrame, equity: pd.Series, benchmark: pd.Series | None) -> dict:
    print(f"\n{'=' * 62}\n{label}\n{'=' * 62}")
    if trades.empty or equity.empty:
        print("No trades generated.")
        return

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    cagr = 100.0 * ((equity.iloc[-1] / STARTING_EQUITY) ** (1 / years) - 1)
    max_dd = 100.0 * (equity / equity.cummax() - 1).min()
    daily = equity.pct_change().dropna()
    sharpe = (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0
    costs = len(trades) * strategy.BROKERAGE_PER_SIDE * 2

    print(f"Period              {equity.index[0]:%Y-%m-%d} to {equity.index[-1]:%Y-%m-%d}  ({years:.1f}y)")
    print(f"Trades              {len(trades)}")
    print(f"Win rate            {len(wins) / len(trades):.1%}")
    print(f"Average win         ${wins['pnl'].mean() if len(wins) else 0:,.2f}")
    print(f"Average loss        ${losses['pnl'].mean() if len(losses) else 0:,.2f}")
    print(f"EXPECTANCY/TRADE    ${trades['pnl'].mean():,.2f}   <- the number that matters")
    print(f"Avg hold            {trades['held_days'].mean():.1f} days")
    print(f"Brokerage paid      ${costs:,.2f}")
    print("-" * 62)
    print(f"Total return        {100.0 * (equity.iloc[-1] / STARTING_EQUITY - 1):+.2f}%")
    print(f"CAGR                {cagr:+.2f}%")
    print(f"Max drawdown        {max_dd:.2f}%")
    print(f"Sharpe              {sharpe:.2f}")

    if benchmark is not None and len(benchmark) > 1:
        bench = benchmark.reindex(equity.index).ffill().dropna()
        if len(bench) > 1:
            bh_cagr = 100.0 * ((bench.iloc[-1] / bench.iloc[0]) ** (1 / years) - 1)
            print("-" * 62)
            print(f"Buy & hold {BENCHMARK}   {100.0 * (bench.iloc[-1] / bench.iloc[0] - 1):+.2f}%  "
                  f"(CAGR {bh_cagr:+.2f}%, maxDD {100.0 * (bench / bench.cummax() - 1).min():.2f}%)")
            print(f"VERDICT: strategy {'BEAT' if cagr > bh_cagr else 'LOST TO'} buy-and-hold on CAGR.")

    print("\nExit breakdown:")
    for reason, group in trades.groupby("reason"):
        print(f"  {reason:<8} {len(group):>4} trades   avg ${group['pnl'].mean():>8,.2f}")

    stats = {
        "trades": len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_win": float(wins["pnl"].mean()) if len(wins) else 0.0,
        "avg_loss": float(losses["pnl"].mean()) if len(losses) else 0.0,
        "expectancy": float(trades["pnl"].mean()),
        "avg_hold": float(trades["held_days"].mean()),
        "cagr": float(cagr),
        "max_dd": float(max_dd),
        "sharpe": float(sharpe),
        "start": f"{equity.index[0]:%Y}",
        "end": f"{equity.index[-1]:%Y}",
    }
    if benchmark is not None and len(benchmark) > 1:
        bench = benchmark.reindex(equity.index).ffill().dropna()
        if len(bench) > 1:
            stats["bench_cagr"] = float(100.0 * ((bench.iloc[-1] / bench.iloc[0]) ** (1 / years) - 1))
    return stats


def decompose(trades: pd.DataFrame) -> None:
    """Split net expectancy into signal edge versus transaction costs."""
    position = trades["shares"] * trades["entry_price"]
    slippage = position * (2 * strategy.SLIPPAGE_BPS / 10_000.0)
    brokerage = 2 * strategy.BROKERAGE_PER_SIDE
    raw_edge = trades["pnl"] + brokerage + slippage

    print(f"\n{'=' * 62}\nWHERE THE MONEY WENT  (all {len(trades)} trades)\n{'=' * 62}")
    print(f"Average position size    ${position.mean():,.0f}")
    print(f"Raw signal edge          ${raw_edge.mean():+,.2f}  ({100 * raw_edge.mean() / position.mean():+.3f}% of position)")
    print(f"  minus slippage         ${-slippage.mean():,.2f}")
    print(f"  minus brokerage        ${-brokerage:,.2f}")
    print(f"NET EXPECTANCY           ${trades['pnl'].mean():+,.2f}")
    print(f"\nBreakeven position size  ${strategy.breakeven_position_size():,.0f}")
    print(f"  -> at {MAX_POSITIONS} concurrent slots, a "
          f"${strategy.breakeven_position_size() * MAX_POSITIONS:,.0f} portfolio floor.")


def main() -> None:
    print("Downloading price data...")
    data = load_prices(UNIVERSE, IN_SAMPLE[0], OUT_SAMPLE[1])
    print(f"Loaded {len(data)} tickers.")

    bench = yf.download(BENCHMARK, start=IN_SAMPLE[0], end=OUT_SAMPLE[1],
                        auto_adjust=True, progress=False)["Close"]
    if isinstance(bench, pd.DataFrame):
        bench = bench.iloc[:, 0]
    if getattr(bench.index, "tz", None) is not None:
        bench.index = bench.index.tz_localize(None)

    is_trades, is_equity = run_backtest(data, *IN_SAMPLE)
    is_stats = report("IN-SAMPLE  (parameters tuned here - treat with suspicion)", is_trades, is_equity, bench)

    oos_trades, oos_equity = run_backtest(data, *OUT_SAMPLE)
    oos_stats = report("OUT-OF-SAMPLE  (the honest number - reported as-is)", oos_trades, oos_equity, bench)

    all_trades = pd.concat([is_trades, oos_trades], ignore_index=True)
    decompose(all_trades)
    all_trades.to_csv("backtest_trades.csv", index=False)

    position = all_trades["shares"] * all_trades["entry_price"]
    slippage = position * (2 * strategy.SLIPPAGE_BPS / 10_000.0)
    raw_edge = all_trades["pnl"] + 2 * strategy.BROKERAGE_PER_SIDE + slippage
    summary = {
        "in_sample": is_stats,
        "out_of_sample": oos_stats,
        "total_trades": len(all_trades),
        "avg_position": float(position.mean()),
        "raw_edge": float(raw_edge.mean()),
        "edge_pct": float(raw_edge.mean() / position.mean()),
        "slippage": float(slippage.mean()),
        "brokerage": 2 * strategy.BROKERAGE_PER_SIDE,
        "net": float(all_trades["pnl"].mean()),
        "breakeven": float(strategy.breakeven_position_size()),
        "portfolio_floor": float(strategy.breakeven_position_size() * MAX_POSITIONS),
        "slots": MAX_POSITIONS,
        "universe": len(UNIVERSE),
        "benchmark": BENCHMARK,
    }
    with open("backtest_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print("\nTrade log written to backtest_trades.csv")
    print("Summary written to backtest_summary.json")

    print("\nKNOWN BIASES (stated in README.md):")
    print("  - Survivorship: universe is today's large caps; delisted failures absent.")
    print("  - Universe selection: 20 hand-picked names, not a rules-based screen.")
    print("  - Same-bar stop/target ambiguity resolved pessimistically.")
    print("  - No dividend timing, franking credits, or tax modelled.")


if __name__ == "__main__":
    main()
