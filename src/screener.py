"""Combine Yahoo Finance technical screening with institutional & TDCC major holder chip concentration."""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from src.config import settings
from src.fetchers.stock_list import StockInfo, get_all_taiwan_stocks
from src.fetchers.yahoo_price import fetch_yahoo_ohlcv_batch
from src.fetchers.chip_data import (
    ChipMetrics,
    fetch_tdcc_major_holders_bulk,
    fetch_twse_tpex_latest_institutional,
    enrich_candidate_chips_with_finmind,
)
from src.indicators.technical import TechnicalResult, evaluate_technical_indicators

logger = logging.getLogger(__name__)


@dataclass
class StockRecommendation:
    stock_id: str
    stock_name: str
    market: str
    close_price: float
    price_change_pct: float
    volume_lots: int

    # KD
    k_value: float
    d_value: float
    kd_signal: str

    # RSI
    rsi_6: float
    rsi_12: float
    rsi_signal: str

    # MACD
    macd_dif: float
    macd_dea: float
    macd_hist: float
    macd_prev_hist: float
    macd_signal: str

    # Chips
    foreign_net_lots: int
    trust_net_lots: int
    inst_concentration_pct: float
    major_holder_400_pct: float
    major_holder_change_pct: float

    total_score: float
    reasons: list[str] = field(default_factory=list)


def run_market_screening(limit: int = 0) -> tuple[int, list[StockRecommendation]]:
    """
    執行完整台股篩選流程：
      1. 取得上市櫃普通股清單
      2. 從 Yahoo Finance 批次下載日K與盤中最新價量
      3. 第一階段：檢查 KD 黃金交叉附近、RSI 黃金交叉附近、MACD 轉強
      4. 第二階段：針對通過技術面的候選股，檢查外資、投信、大戶集保集中度並綜合評分排序
    """
    stocks = get_all_taiwan_stocks(limit=limit)
    if not stocks:
        logger.error("No stocks fetched from stock list providers.")
        return 0, []

    ohlcv_map = fetch_yahoo_ohlcv_batch(stocks)
    total_scanned = len(ohlcv_map)

    # 第一階段：技術面篩選
    tech_candidates: list[tuple[StockInfo, TechnicalResult]] = []
    stock_lookup = {s.stock_id: s for s in stocks}

    for stock_id, df in ohlcv_map.items():
        tech_res = evaluate_technical_indicators(df)
        if tech_res.passed:
            tech_candidates.append((stock_lookup[stock_id], tech_res))

    logger.info(
        "Technical screening completed: %d/%d stocks matched (KD near golden cross + RSI near golden cross + MACD strengthening).",
        len(tech_candidates),
        total_scanned,
    )

    if not tech_candidates:
        return total_scanned, []

    # 第二階段：載入全市場法人與集保大戶基準資料，並對候選股進行籌碼集中度檢查
    bulk_inst_map = fetch_twse_tpex_latest_institutional()
    bulk_tdcc_map = fetch_tdcc_major_holders_bulk()

    # 若技術面候選股較多，先依技術面分數取前 60 檔進行深度籌碼檢查，避免 API 延遲
    tech_candidates.sort(key=lambda x: x[1].tech_score, reverse=True)
    shortlist = tech_candidates[:60]

    recommendations: list[StockRecommendation] = []
    for stock, tech in shortlist:
        chip: ChipMetrics = enrich_candidate_chips_with_finmind(
            stock_id=stock.stock_id,
            avg_5d_volume_lots=tech.avg_5d_volume_lots,
            base_inst=bulk_inst_map.get(stock.stock_id),
            base_tdcc=bulk_tdcc_map.get(stock.stock_id),
        )

        # 過濾條件：三大法人（外資或投信）買超 > 0，或大戶持股比例 >= 40% / 大戶持股增加
        has_positive_chips = (
            chip.trust_net_lots_5d > 0
            or chip.foreign_net_lots_5d > 0
            or chip.major_holder_change_pct > 0
            or chip.major_holder_400_pct >= 45.0
        )
        if not has_positive_chips:
            continue

        total_score = round(tech.tech_score + chip.chip_score, 2)
        all_reasons = tech.tech_reasons + chip.chip_reasons

        recommendations.append(
            StockRecommendation(
                stock_id=stock.stock_id,
                stock_name=stock.stock_name,
                market=stock.market,
                close_price=tech.close_price,
                price_change_pct=tech.price_change_pct,
                volume_lots=tech.volume_lots,
                k_value=tech.k_val,
                d_value=tech.d_val,
                kd_signal=tech.kd_signal,
                rsi_6=tech.rsi_6,
                rsi_12=tech.rsi_12,
                rsi_signal=tech.rsi_signal,
                macd_dif=tech.macd_dif,
                macd_dea=tech.macd_dea,
                macd_hist=tech.macd_hist,
                macd_prev_hist=tech.macd_prev_hist,
                macd_signal=tech.macd_signal,
                foreign_net_lots=chip.foreign_net_lots_5d,
                trust_net_lots=chip.trust_net_lots_5d,
                inst_concentration_pct=chip.inst_concentration_pct,
                major_holder_400_pct=chip.major_holder_400_pct,
                major_holder_change_pct=chip.major_holder_change_pct,
                total_score=total_score,
                reasons=all_reasons,
            )
        )

    recommendations.sort(key=lambda r: r.total_score, reverse=True)
    final_list = recommendations[: settings.top_recommendations_limit]
    logger.info("Final recommendations after chip concentration filter: %d stocks.", len(final_list))
    return total_scanned, final_list
