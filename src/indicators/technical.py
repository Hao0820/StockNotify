"""
Calculate KD (9,3,3), RSI (6,12), and MACD (12,26,9) indicators
and evaluate:
  1. KD near golden cross (黃金交叉或即將黃金交叉)
  2. RSI near golden cross (RSI6 與 RSI12 黃金交叉或即將黃金交叉，或 RSI 從低檔轉折向上突破 50)
  3. MACD strengthening (MACD 柱狀體翻紅、紅柱放大、或綠柱連續收斂轉強)
"""

from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from src.config import settings


@dataclass
class TechnicalResult:
    passed: bool
    close_price: float = 0.0
    price_change_pct: float = 0.0
    volume_lots: int = 0
    avg_5d_volume_lots: float = 0.0

    # KD (9, 3, 3)
    k_val: float = 0.0
    d_val: float = 0.0
    prev_k: float = 0.0
    prev_d: float = 0.0
    kd_signal: str = ""

    # RSI (6, 12)
    rsi_6: float = 0.0
    rsi_12: float = 0.0
    prev_rsi_6: float = 0.0
    prev_rsi_12: float = 0.0
    rsi_signal: str = ""

    # MACD (12, 26, 9)
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_hist: float = 0.0
    macd_prev_hist: float = 0.0
    macd_signal: str = ""

    tech_score: float = 0.0
    tech_reasons: list[str] = field(default_factory=list)


def compute_kd(df: pd.DataFrame, n: int = 9) -> tuple[pd.Series, pd.Series]:
    """
    計算台股標準日 KD (9, 3, 3)，採用遞迴平滑權重 1/3 與 2/3：
      RSV = (Close - Lowest_Low_9) / (Highest_High_9 - Lowest_Low_9) * 100
      K_t = 2/3 * K_{t-1} + 1/3 * RSV_t
      D_t = 2/3 * D_{t-1} + 1/3 * K_t
    """
    low_min = df["Low"].rolling(window=n, min_periods=n).min()
    high_max = df["High"].rolling(window=n, min_periods=n).max()
    denom = (high_max - low_min).replace(0, np.nan)
    rsv = ((df["Close"] - low_min) / denom * 100.0).fillna(50.0).clip(0.0, 100.0)

    k_list = []
    d_list = []
    k_prev = 50.0
    d_prev = 50.0
    for val in rsv:
        k_curr = (2.0 / 3.0) * k_prev + (1.0 / 3.0) * float(val)
        d_curr = (2.0 / 3.0) * d_prev + (1.0 / 3.0) * k_curr
        k_list.append(k_curr)
        d_list.append(d_curr)
        k_prev = k_curr
        d_prev = d_curr

    return pd.Series(k_list, index=df.index), pd.Series(d_list, index=df.index)


def compute_rsi(close: pd.Series, period: int = 6) -> pd.Series:
    """Wilder's RSI calculation."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate DIF, DEA (Signal), and Histogram (DIF - DEA)."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - dea
    return dif, dea, hist


def evaluate_technical_indicators(df: pd.DataFrame) -> TechnicalResult:
    """
    評估單一股票的歷史/盤中 OHLCV DataFrame 是否符合：
      1. 5日均量 >= 設定門檻
      2. KD 黃金交叉附近
      3. RSI 黃金交叉附近
      4. MACD 轉強
    """
    if df is None or len(df) < 35:
        return TechnicalResult(passed=False)

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)

    avg_5d_vol_shares = float(volume.iloc[-5:].mean())
    if avg_5d_vol_shares < settings.min_avg_volume_shares:
        return TechnicalResult(passed=False)

    curr_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    price_change_pct = round(((curr_close - prev_close) / prev_close) * 100.0, 2) if prev_close > 0 else 0.0
    curr_vol_lots = int(round(float(volume.iloc[-1]) / 1000.0))
    avg_5d_vol_lots = round(avg_5d_vol_shares / 1000.0, 1)

    # 1. 計算 KD (9, 3, 3)
    k_series, d_series = compute_kd(df, n=9)
    k_curr, k_prev = float(k_series.iloc[-1]), float(k_series.iloc[-2])
    d_curr, d_prev = float(d_series.iloc[-1]), float(d_series.iloc[-2])

    kd_signal = ""
    kd_score = 0.0
    kd_reason = ""
    kd_diff = k_curr - d_curr
    prev_kd_diff = k_prev - d_prev

    # 條件 A: 剛發生黃金交叉 (前一日 K <= D，今日 K > D，或前二日內剛金叉且 K 持續往上，且 K < 80 未過熱)
    if (prev_kd_diff <= 0 and kd_diff > 0 and k_curr <= 80.0) or (
        0 < kd_diff <= settings.kd_near_cross_diff and k_curr > k_prev and k_curr <= 75.0
    ):
        kd_signal = "GOLDEN_CROSS"
        kd_score = 25.0 if k_curr < 55.0 else 18.0
        kd_reason = f"KD黃金交叉(K={k_curr:.1f}, D={d_curr:.1f})"
    # 條件 B: 即將黃金交叉 (K 略小於 D 但差距在容許範圍內，且 K 勾頭向上、差距收斂)
    elif (
        -settings.kd_near_cross_diff <= kd_diff <= 0
        and k_curr > k_prev
        and kd_diff > prev_kd_diff
        and k_curr <= 75.0
    ):
        kd_signal = "NEAR_GOLDEN_CROSS"
        kd_score = 20.0 if k_curr < 50.0 else 15.0
        kd_reason = f"KD準黃金交叉(K={k_curr:.1f}↑逼近D={d_curr:.1f})"

    if not kd_signal:
        return TechnicalResult(passed=False)

    # 2. 計算 RSI (6, 12)
    rsi6_series = compute_rsi(close, 6)
    rsi12_series = compute_rsi(close, 12)
    r6_curr, r6_prev = float(rsi6_series.iloc[-1]), float(rsi6_series.iloc[-2])
    r12_curr, r12_prev = float(rsi12_series.iloc[-1]), float(rsi12_series.iloc[-2])

    rsi_signal = ""
    rsi_score = 0.0
    rsi_reason = ""
    rsi_diff = r6_curr - r12_curr
    prev_rsi_diff = r6_prev - r12_prev

    # 條件 A: RSI6 向上突破 RSI12 黃金交叉 (或剛金叉差距極小)，且未嚴重超買 (RSI6 < 78)
    if (prev_rsi_diff <= 0 and rsi_diff > 0 and r6_curr < 78.0) or (
        0 < rsi_diff <= settings.rsi_near_cross_diff and r6_curr > r6_prev and r6_curr < 75.0
    ):
        rsi_signal = "GOLDEN_CROSS"
        rsi_score = 20.0
        rsi_reason = f"RSI黃金交叉(RSI6={r6_curr:.1f}>RSI12={r12_curr:.1f})"
    # 條件 B: RSI6 向上逼近 RSI12 (準金叉)
    elif (
        -settings.rsi_near_cross_diff <= rsi_diff <= 0
        and r6_curr > r6_prev
        and rsi_diff > prev_rsi_diff
        and 30.0 <= r6_curr <= 70.0
    ):
        rsi_signal = "NEAR_GOLDEN_CROSS"
        rsi_score = 15.0
        rsi_reason = f"RSI準金叉(RSI6={r6_curr:.1f}↑逼近RSI12={r12_curr:.1f})"

    if not rsi_signal:
        return TechnicalResult(passed=False)

    # 3. 計算 MACD (12, 26, 9) 並檢查是否「轉強」
    dif_s, dea_s, hist_s = compute_macd(close, 12, 26, 9)
    dif_curr = float(dif_s.iloc[-1])
    dea_curr = float(dea_s.iloc[-1])
    h_curr = float(hist_s.iloc[-1])
    h_prev1 = float(hist_s.iloc[-2])
    h_prev2 = float(hist_s.iloc[-3])

    macd_signal = ""
    macd_score = 0.0
    macd_reason = ""

    # 情況 1: 柱狀體 (OSC) 由負翻正 (剛金叉)
    if h_prev1 <= 0 and h_curr > 0:
        macd_signal = "HIST_TURN_POSITIVE"
        macd_score = 25.0
        macd_reason = f"MACD翻紅轉強(OSC={h_curr:.3f})"
    # 情況 2: 紅柱持續放大 (多頭加速)
    elif h_curr > 0 and h_curr > h_prev1:
        macd_signal = "BULLISH_EXPANDING"
        macd_score = 20.0
        macd_reason = f"MACD紅柱放大(OSC={h_curr:.3f}↑)"
    # 情況 3: 綠柱明顯收斂 (空翻多轉強，連續收斂且接近 0 軸)
    elif h_curr < 0 and h_curr > h_prev1 and h_prev1 >= h_prev2:
        macd_signal = "BEARISH_CONVERGING"
        macd_score = 16.0
        macd_reason = f"MACD綠柱收斂轉強(OSC={h_curr:.3f}↑)"

    if not macd_signal:
        return TechnicalResult(passed=False)

    total_tech_score = kd_score + rsi_score + macd_score
    reasons = [kd_reason, rsi_reason, macd_reason]

    return TechnicalResult(
        passed=True,
        close_price=round(curr_close, 2),
        price_change_pct=price_change_pct,
        volume_lots=curr_vol_lots,
        avg_5d_volume_lots=avg_5d_vol_lots,
        k_val=round(k_curr, 2),
        d_val=round(d_curr, 2),
        prev_k=round(k_prev, 2),
        prev_d=round(d_prev, 2),
        kd_signal=kd_signal,
        rsi_6=round(r6_curr, 2),
        rsi_12=round(r12_curr, 2),
        prev_rsi_6=round(r6_prev, 2),
        prev_rsi_12=round(r12_prev, 2),
        rsi_signal=rsi_signal,
        macd_dif=round(dif_curr, 4),
        macd_dea=round(dea_curr, 4),
        macd_hist=round(h_curr, 4),
        macd_prev_hist=round(h_prev1, 4),
        macd_signal=macd_signal,
        tech_score=total_tech_score,
        tech_reasons=reasons,
    )
