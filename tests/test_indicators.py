"""Unit tests for KD, RSI, and MACD indicator calculations and screening criteria."""

import numpy as np
import pandas as pd
from src.indicators.technical import compute_kd, compute_rsi, compute_macd, evaluate_technical_indicators


def _generate_turnaround_df(rows: int = 50) -> pd.DataFrame:
    """
    產生一段先緩跌整理、最後 3 天放量轉強反彈的模擬日 K 資料，
    用來驗證 KD 金叉附近、RSI 金叉附近、MACD 綠柱收斂/翻紅轉強的觸發邏輯。
    """
    # 前 35 天從 115 緩跌至 95，接著 12 天在 95~96 横盤整理，最後 3 天反彈至 97.5 -> 99.0 -> 101.2
    closes = (
        list(np.linspace(115.0, 95.0, 35))
        + [95.2, 95.0, 94.8, 95.1, 94.9, 95.0, 94.7, 94.6, 94.5, 94.8, 95.0, 95.2]
        + [96.0, 97.2, 98.8]
    )
    closes = np.array(closes[:rows])
    highs = closes + 1.2
    lows = closes - 1.2
    opens = closes - 0.5
    volumes = np.full(len(closes), 1_200_000)
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes,
        }
    )


def test_compute_kd_bounds():
    df = _generate_turnaround_df()
    k, d = compute_kd(df, n=9)
    assert len(k) == len(df)
    assert len(d) == len(df)
    assert (k >= 0).all() and (k <= 100).all()
    assert (d >= 0).all() and (d <= 100).all()


def test_compute_rsi_bounds():
    df = _generate_turnaround_df()
    r6 = compute_rsi(df["Close"], 6)
    r12 = compute_rsi(df["Close"], 12)
    assert (r6 >= 0).all() and (r6 <= 100).all()
    assert (r12 >= 0).all() and (r12 <= 100).all()


def test_evaluate_technical_indicators_turnaround():
    df = _generate_turnaround_df()
    res = evaluate_technical_indicators(df)
    assert res.passed is True
    assert res.kd_signal in ("GOLDEN_CROSS", "NEAR_GOLDEN_CROSS")
    assert res.rsi_signal in ("GOLDEN_CROSS", "NEAR_GOLDEN_CROSS")
    assert res.macd_signal in ("HIST_TURN_POSITIVE", "BULLISH_EXPANDING", "BEARISH_CONVERGING")
    assert len(res.tech_reasons) == 3
