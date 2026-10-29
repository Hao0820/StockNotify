"""Batch download historical & intraday OHLCV data from Yahoo Finance."""

import logging
import time
from typing import Dict
import pandas as pd
import yfinance as yf

from src.fetchers.stock_list import StockInfo

logger = logging.getLogger(__name__)


def fetch_yahoo_ohlcv_batch(
    stocks: list[StockInfo],
    period: str = "4mo",
    batch_size: int = 120,
) -> Dict[str, pd.DataFrame]:
    """
    Download daily OHLCV data for all given stocks in batches from Yahoo Finance.
    During Taiwan market hours (e.g., 13:00 TST), Yahoo Finance automatically includes
    today's latest intraday bar as the last row of the DataFrame.
    At 08:00 TST (pre-market), the last row is the previous trading day's close.

    Returns:
        dict mapping `stock_id` (e.g. '2330') -> DataFrame with columns:
        ['Open', 'High', 'Low', 'Close', 'Volume']
    """
    results: Dict[str, pd.DataFrame] = {}
    ticker_to_stock = {s.yahoo_ticker: s for s in stocks}
    all_tickers = list(ticker_to_stock.keys())

    total_batches = (len(all_tickers) + batch_size - 1) // batch_size
    for idx in range(0, len(all_tickers), batch_size):
        chunk = all_tickers[idx : idx + batch_size]
        batch_num = idx // batch_size + 1
        logger.info("Fetching Yahoo Finance batch %d/%d (%d tickers)...", batch_num, total_batches, len(chunk))

        try:
            df = yf.download(
                tickers=chunk,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
            if df is None or df.empty:
                continue

            if len(chunk) == 1:
                ticker = chunk[0]
                stock = ticker_to_stock[ticker]
                sub_df = df.dropna(subset=["Close"]).copy()
                if len(sub_df) >= 35:
                    results[stock.stock_id] = sub_df
            else:
                for ticker in chunk:
                    if ticker not in df.columns.get_level_values(0):
                        continue
                    sub_df = df[ticker].dropna(subset=["Close"]).copy()
                    if len(sub_df) >= 35:
                        stock = ticker_to_stock[ticker]
                        results[stock.stock_id] = sub_df
        except Exception as exc:
            logger.warning("Error downloading Yahoo Finance batch %d: %s", batch_num, exc)

        time.sleep(0.5)

    logger.info("Successfully fetched valid OHLCV data for %d/%d stocks.", len(results), len(stocks))
    return results
