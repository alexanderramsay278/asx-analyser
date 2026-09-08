"""
Tests for strategy.py.

The most important test here is test_no_lookahead. Every claim the backtest
makes depends on indicators at bar t being computable from bars 0..t only. That
is a property worth asserting, not assuming.

Run:  python -m pytest test_strategy.py -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import strategy


def make_frame(closes, highs=None, lows=None, volume=1_000_000) -> pd.DataFrame:
    """Build an OHLCV frame from a list of closes."""
    closes = np.asarray(closes, dtype=float)
    highs = closes * 1.01 if highs is None else np.asarray(highs, dtype=float)
    lows = closes * 0.99 if lows is None else np.asarray(lows, dtype=float)
    return pd.DataFrame(
        {"Open": closes, "High": highs, "Low": lows, "Close": closes,
         "Volume": np.full(len(closes), volume, dtype=float)},
        index=pd.bdate_range("2020-01-01", periods=len(closes)),
    )


# -- Indicators ---------------------------------------------------------------

def test_rsi_all_gains_is_100():
    """An unbroken run of up days has no average loss, which is RSI 100."""
    rsi = strategy.wilder_rsi(pd.Series(np.arange(1.0, 20.0)))
    assert rsi.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_zero():
    rsi = strategy.wilder_rsi(pd.Series(np.arange(20.0, 1.0, -1.0)))
    assert rsi.iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_stays_in_bounds():
    rng = np.random.default_rng(0)
    closes = pd.Series(100 + np.cumsum(rng.normal(0, 1, 500)))
    rsi = strategy.wilder_rsi(closes).dropna()
    assert len(rsi) > 400
    assert rsi.between(0, 100).all()


def test_true_range_accounts_for_gaps():
    """A gap down makes true range exceed the bar's own high-low range.

    A plain high-low range understates volatility on exactly the gapping days a
    stop-loss model cares about most, which is why true range is the right
    input for ATR-based stop placement.
    """
    df = pd.DataFrame({
        "Open": [100.0, 80.0], "High": [101.0, 82.0],
        "Low": [99.0, 79.0], "Close": [100.0, 80.0], "Volume": [1e6, 1e6],
    }, index=pd.bdate_range("2020-01-01", periods=2))

    tr = strategy.true_range(df)
    naive_range = df["High"].iloc[1] - df["Low"].iloc[1]      # 3.0
    assert tr.iloc[1] == pytest.approx(21.0)                   # 100 - 79
    assert tr.iloc[1] > naive_range


def test_consecutive_down_days():
    assert strategy.consecutive_down_days(pd.Series([10, 9, 8, 7.0])) == 3
    assert strategy.consecutive_down_days(pd.Series([10, 11, 12.0])) == 0
    # Counting stops at the first up day, walking backwards.
    assert strategy.consecutive_down_days(pd.Series([10, 8, 9, 8, 7.0])) == 2


# -- The property the backtest depends on -------------------------------------

def test_no_lookahead():
    """Indicators at bar t must not change when later bars are appended.

    If this fails, every backtest result in the README is invalid.
    """
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0, 1, 400))
    full = make_frame(closes)
    truncated = full.iloc[:300]

    features_full = strategy.build_features(full).iloc[:300]
    features_truncated = strategy.build_features(truncated)

    for column in ["ma_trend", "ma_exit", "rsi", "atr", "atr_pct", "avg_volume"]:
        pd.testing.assert_series_equal(
            features_full[column], features_truncated[column],
            check_names=False, rtol=1e-12,
        )


# -- Signal generation --------------------------------------------------------

def test_evaluate_is_deterministic():
    """Same input, same output. Reproducibility is why the signal layer is deterministic."""
    rng = np.random.default_rng(7)
    df = make_frame(100 + np.cumsum(rng.normal(0, 1, 300)))
    features = strategy.build_features(df)
    first = strategy.evaluate("TEST", features)
    for _ in range(5):
        assert strategy.evaluate("TEST", features).signal == first.signal


def test_buy_requires_uptrend_and_oversold():
    """Construct a rising series with a sharp two-day drop at the end."""
    closes = list(np.linspace(100, 160, 200)) + [158.0, 150.0]
    sig = strategy.evaluate("TEST", strategy.build_features(make_frame(closes)))

    assert sig.checks["trend"] is True
    assert sig.checks["oversold"] is True
    assert sig.signal == "BUY"
    assert sig.stop < sig.reference_price
    assert sig.down_days == 2


def test_downtrend_is_avoided_even_when_oversold():
    closes = list(np.linspace(160, 100, 200)) + [98.0, 92.0]
    sig = strategy.evaluate("TEST", strategy.build_features(make_frame(closes)))

    assert sig.checks["trend"] is False
    assert sig.signal == "AVOID"


def test_uptrend_not_oversold_waits():
    closes = list(np.linspace(100, 160, 202))
    sig = strategy.evaluate("TEST", strategy.build_features(make_frame(closes)))

    assert sig.checks["trend"] is True
    assert sig.checks["oversold"] is False
    assert sig.signal == "WAIT"


def test_illiquid_name_is_avoided():
    closes = list(np.linspace(100, 160, 200)) + [158.0, 150.0]
    df = make_frame(closes, volume=1_000)
    sig = strategy.evaluate("TEST", strategy.build_features(df))

    assert sig.checks["liquidity"] is False
    assert sig.signal == "AVOID"


# -- Cost model ---------------------------------------------------------------

def test_breakeven_matches_published_figure():
    """The ~$17.7k floor quoted in the README."""
    assert strategy.breakeven_position_size() == pytest.approx(17_699, rel=0.01)


def test_expected_value_is_negative_at_small_size():
    """The headline backtest finding: -$16.04/trade at the average position size."""
    assert strategy.expected_net_per_trade(3_517) == pytest.approx(-16.04, abs=0.05)


def test_expected_value_crosses_zero_at_breakeven():
    breakeven = strategy.breakeven_position_size()
    assert strategy.expected_net_per_trade(breakeven) == pytest.approx(0.0, abs=0.01)
    assert strategy.expected_net_per_trade(breakeven * 0.5) < 0
    assert strategy.expected_net_per_trade(breakeven * 2.0) > 0


def test_impossible_edge_returns_infinite_floor():
    """If slippage alone exceeds the edge, no position size is large enough."""
    assert strategy.breakeven_position_size(edge=0.00005) == float("inf")
