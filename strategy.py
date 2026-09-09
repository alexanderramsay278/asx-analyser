"""
strategy.py - the single source of truth for the mean-reversion strategy.

Both the live analyser (asx_analyser.py) and the backtest (backtest.py) import
their indicators and signal logic from this module. That is deliberate. If the
tool and the test harness each defined the rules themselves they could drift
apart silently, and the backtest would be evidence about the harness rather
than about the tool. One definition makes that impossible.

No part of this module calls a language model. The signal is fully
deterministic and reproducible from price data alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# -- Strategy parameters ------------------------------------------------------
# These are the values used in the backtest documented in README.md. Changing
# them invalidates the published results: change them and re-run, or not at all.

TREND_MA = 100          # long-term trend filter
RSI_PERIOD = 2          # Connors-style short RSI
RSI_ENTRY = 10.0        # enter when RSI(2) closes below this
RSI_EXIT = 70.0         # exit when RSI(2) closes above this
EXIT_MA = 5             # or when price closes above this short MA
ATR_PERIOD = 5
ATR_MIN_PCT = 0.8       # too quiet to cover costs
ATR_MAX_PCT = 6.0       # too violent for swing sizing
MIN_AVG_VOLUME = 500_000
STOP_ATR_MULT = 2.0
MAX_HOLD_DAYS = 10

# -- Cost model (ASX retail) --------------------------------------------------
BROKERAGE_PER_SIDE = 10.0
SLIPPAGE_BPS = 5.0

# Measured gross edge per trade from the backtest, as a fraction of position
# value. See README.md, section "Backtest Results".
#
# Quoted to two significant figures on purpose. Price data is fetched live and
# Yahoo revises adjusted closes, so repeated runs land between 0.207% and 0.215%
# with a trade count of 1,417-1,419. The finding is robust at this precision;
# a third decimal place would not be.
MEASURED_EDGE = 0.0021


# -- Indicators ---------------------------------------------------------------

def wilder_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder-smoothed RSI, per the standard definition.

    Smoothing matters at short periods: an unsmoothed two-period RSI computed
    from a handful of raw price changes pins at exactly 0 or 100 whenever the
    window happens to be all-down or all-up, which on ASX daily data is roughly
    a quarter of all bars.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 means an unbroken run of gains, which is RSI 100 by definition.
    return rsi.mask(avg_loss == 0.0, 100.0).where(avg_loss.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range, which accounts for overnight gaps unlike a plain high-low range."""
    prev_close = df["Close"].shift(1)
    return pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def average_true_range(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def consecutive_down_days(close: pd.Series, limit: int = 5) -> int:
    """Consecutive sessions that closed lower, counting back from the last bar."""
    count = 0
    values = close.to_numpy()
    for i in range(len(values) - 1, 0, -1):
        if values[i] < values[i - 1]:
            count += 1
            if count >= limit:
                break
        else:
            break
    return count


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Attach every indicator the strategy uses. Never looks forward."""
    out = df.copy()
    out["ma_trend"] = out["Close"].rolling(TREND_MA).mean()
    out["ma_exit"] = out["Close"].rolling(EXIT_MA).mean()
    out["rsi"] = wilder_rsi(out["Close"])
    out["atr"] = average_true_range(out)
    out["atr_pct"] = 100.0 * out["atr"] / out["Close"]
    out["avg_volume"] = out["Volume"].rolling(20).mean()

    out["entry_signal"] = (
        (out["Close"] > out["ma_trend"])
        & (out["rsi"] < RSI_ENTRY)
        & (out["avg_volume"] > MIN_AVG_VOLUME)
        & (out["atr_pct"].between(ATR_MIN_PCT, ATR_MAX_PCT))
    )
    out["exit_signal"] = (out["rsi"] > RSI_EXIT) | (out["Close"] > out["ma_exit"])
    return out


# -- Signal evaluation --------------------------------------------------------

@dataclass
class Signal:
    """A fully deterministic assessment of the most recent bar."""

    ticker: str
    as_of: pd.Timestamp
    signal: str                       # BUY | WAIT | AVOID
    reasons: list[str] = field(default_factory=list)

    close: float = 0.0
    ma_trend: float = 0.0
    rsi: float = 0.0
    atr: float = 0.0
    atr_pct: float = 0.0
    avg_volume: float = 0.0
    down_days: int = 0

    checks: dict[str, bool] = field(default_factory=dict)

    reference_price: float = 0.0      # last close; a real fill is the next open
    stop: float = 0.0
    target: float = 0.0
    risk_reward: float = 0.0

    def summary(self) -> str:
        return f"{self.ticker} {self.signal} - " + "; ".join(self.reasons)


def evaluate(ticker: str, features: pd.DataFrame) -> Signal:
    """Apply the strategy rules to the latest bar. Deterministic; no model calls."""
    bar = features.iloc[-1]

    checks = {
        "trend": bool(bar["Close"] > bar["ma_trend"]),
        "oversold": bool(bar["rsi"] < RSI_ENTRY),
        "liquidity": bool(bar["avg_volume"] > MIN_AVG_VOLUME),
        "volatility": bool(ATR_MIN_PCT <= bar["atr_pct"] <= ATR_MAX_PCT),
    }

    tradeable = checks["trend"] and checks["liquidity"] and checks["volatility"]
    if tradeable and checks["oversold"]:
        signal = "BUY"
        reasons = ["all entry conditions met"]
    elif tradeable:
        signal = "WAIT"
        reasons = [f"tradeable setup, but RSI(2) is {bar['rsi']:.1f}, not below {RSI_ENTRY:g}"]
    else:
        signal = "AVOID"
        failed = [name for name, ok in checks.items() if not ok and name != "oversold"]
        reasons = [f"failed: {', '.join(failed)}"]

    reference = float(bar["Close"])
    stop = reference - STOP_ATR_MULT * float(bar["atr"])
    target = float(bar["ma_exit"])
    risk = reference - stop
    reward = target - reference
    rr = (reward / risk) if risk > 0 else 0.0

    return Signal(
        ticker=ticker,
        as_of=features.index[-1],
        signal=signal,
        reasons=reasons,
        close=reference,
        ma_trend=float(bar["ma_trend"]),
        rsi=float(bar["rsi"]),
        atr=float(bar["atr"]),
        atr_pct=float(bar["atr_pct"]),
        avg_volume=float(bar["avg_volume"]),
        down_days=consecutive_down_days(features["Close"]),
        checks=checks,
        reference_price=reference,
        stop=stop,
        target=target,
        risk_reward=rr,
    )


# -- Cost reality check -------------------------------------------------------

def breakeven_position_size(
    brokerage_per_side: float = BROKERAGE_PER_SIDE,
    slippage_bps: float = SLIPPAGE_BPS,
    edge: float = MEASURED_EDGE,
) -> float:
    """Smallest position at which the measured edge covers round-trip costs.

    Slippage scales with position size but brokerage does not, so there is a
    hard floor below which the strategy cannot be profitable at any win rate.
    """
    slippage_fraction = 2 * slippage_bps / 10_000.0
    net_edge = edge - slippage_fraction
    if net_edge <= 0:
        return float("inf")
    return (2 * brokerage_per_side) / net_edge


def expected_net_per_trade(position_size: float) -> float:
    """Expected P&L per trade at a given position size, using the measured edge."""
    gross = position_size * MEASURED_EDGE
    slippage = position_size * (2 * SLIPPAGE_BPS / 10_000.0)
    return gross - slippage - 2 * BROKERAGE_PER_SIDE
