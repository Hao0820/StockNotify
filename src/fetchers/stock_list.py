"""Fetch all active Taiwan listed (TWSE) and OTC (TPEx) ordinary stocks."""

import logging
from dataclasses import dataclass
import requests

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class StockInfo:
    stock_id: str
    stock_name: str
    market: str       # 'TWSE' or 'TPEX'
    yahoo_ticker: str # e.g. '2330.TW' or '8069.TWO'
    industry: str = ""


def _fetch_from_twse_tpex_openapi() -> list[StockInfo]:
    """Fetch listed and OTC stock list from official TWSE & TPEx OpenAPI."""
    stocks: dict[str, StockInfo] = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 1. TWSE Listed Companies
    try:
        resp = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers=headers,
            timeout=20,
        )
        if resp.status_code == 200:
            for item in resp.json():
                code = str(item.get("Code", "")).strip()
                name = str(item.get("Name", "")).strip()
                # 只保留 4 碼數字普通股 (排除權證、ETF、特別股)
                if len(code) == 4 and code.isdigit() and not code.startswith("00"):
                    stocks[code] = StockInfo(
                        stock_id=code,
                        stock_name=name,
                        market="TWSE",
                        yahoo_ticker=f"{code}.TW",
                    )
    except Exception as exc:
        logger.warning("TWSE OpenAPI stock list fetch failed: %s", exc)

    # 2. TPEx OTC Companies
    try:
        resp = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            headers=headers,
            timeout=20,
        )
        if resp.status_code == 200:
            for item in resp.json():
                code = str(item.get("SecuritiesCompanyCode", "")).strip()
                name = str(item.get("CompanyName", "")).strip()
                if len(code) == 4 and code.isdigit() and not code.startswith("00"):
                    stocks[code] = StockInfo(
                        stock_id=code,
                        stock_name=name,
                        market="TPEX",
                        yahoo_ticker=f"{code}.TWO",
                    )
    except Exception as exc:
        logger.warning("TPEx OpenAPI stock list fetch failed: %s", exc)

    return list(stocks.values())


def _fetch_from_finmind() -> list[StockInfo]:
    """Fallback: Fetch TaiwanStockInfo from FinMind API."""
    stocks: dict[str, StockInfo] = {}
    params = {"dataset": "TaiwanStockInfo"}
    if settings.finmind_api_token:
        params["token"] = settings.finmind_api_token

    try:
        resp = requests.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=params,
            timeout=25,
        )
        data = resp.json().get("data", [])
        for row in data:
            code = str(row.get("stock_id", "")).strip()
            name = str(row.get("stock_name", "")).strip()
            m_type = str(row.get("type", "")).lower()
            industry = str(row.get("industry_category", ""))
            if len(code) == 4 and code.isdigit() and not code.startswith("00"):
                if m_type == "twse":
                    stocks[code] = StockInfo(
                        stock_id=code,
                        stock_name=name,
                        market="TWSE",
                        yahoo_ticker=f"{code}.TW",
                        industry=industry,
                    )
                elif m_type == "tpex":
                    stocks[code] = StockInfo(
                        stock_id=code,
                        stock_name=name,
                        market="TPEX",
                        yahoo_ticker=f"{code}.TWO",
                        industry=industry,
                    )
    except Exception as exc:
        logger.error("FinMind stock list fetch failed: %s", exc)

    return list(stocks.values())


def get_all_taiwan_stocks(limit: int = 0) -> list[StockInfo]:
    """Return all active 4-digit ordinary stocks in TWSE and TPEx."""
    stocks = _fetch_from_twse_tpex_openapi()
    if len(stocks) < 500:
        logger.info("Official OpenAPI returned %d stocks, falling back to FinMind...", len(stocks))
        finmind_stocks = _fetch_from_finmind()
        if len(finmind_stocks) > len(stocks):
            stocks = finmind_stocks

    stocks.sort(key=lambda s: s.stock_id)
    logger.info("Loaded %d Taiwan ordinary stocks (TWSE + TPEx).", len(stocks))

    if limit and limit > 0:
        return stocks[:limit]
    return stocks
