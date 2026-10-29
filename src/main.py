"""Main entrypoint for StockNotify scheduled & manual screening runs."""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone, timedelta

from src.screener import run_market_screening
from src.storage.supabase_client import save_results_to_supabase
from src.notifiers.discord_notifier import send_discord_notification
from src.notifiers.email_notifier import send_email_notification

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("StockNotify")

TW_TZ = timezone(timedelta(hours=8))


def normalize_session(session_arg: str) -> str:
    if session_arg in ("morning", "morning_0800", "0800"):
        return "morning_0800"
    if session_arg in ("intraday", "afternoon", "intraday_1300", "1300"):
        return "intraday_1300"
    if session_arg == "auto":
        now_tw = datetime.now(TW_TZ)
        return "morning_0800" if now_tw.hour < 11 else "intraday_1300"
    return "manual"


def print_summary_table(recommendations):
    if not recommendations:
        print("\n=== 本次掃描無符合所有篩選條件之標的 ===")
        return

    print("\n" + "=" * 105)
    print(
        f"{'排名':<4} {'代號':<6} {'名稱':<10} {'市場':<5} {'現價':>8} {'漲跌%':>7} "
        f"{'K/D':<13} {'RSI6/12':<13} {'MACD柱':>8} {'外資5日':>8} {'投信5日':>8} {'400張大戶':>9} {'總分':>6}"
    )
    print("-" * 105)
    for idx, r in enumerate(recommendations, start=1):
        kd_str = f"{r.k_value:.1f}/{r.d_value:.1f}"
        rsi_str = f"{r.rsi_6:.1f}/{r.rsi_12:.1f}"
        print(
            f"#{idx:<3} {r.stock_id:<6} {r.stock_name:<10} {r.market:<5} "
            f"{r.close_price:>8.2f} {r.price_change_pct:>+6.2f}% "
            f"{kd_str:<13} {rsi_str:<13} {r.macd_hist:>+8.3f} "
            f"{r.foreign_net_lots:>+7}張 {r.trust_net_lots:>+7}張 "
            f"{r.major_holder_400_pct:>7.1f}% {r.total_score:>6.1f}"
        )
    print("=" * 105 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Taiwan Stock Screener (Yahoo Finance + Chips + Technicals)")
    parser.add_argument(
        "--session",
        type=str,
        default="auto",
        help="Execution session: morning_0800 | intraday_1300 | manual | auto",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of stocks to scan (0 = scan all listed/OTC stocks)",
    )
    args = parser.parse_args()

    session_type = normalize_session(args.session)
    now_tw = datetime.now(TW_TZ)
    run_date = now_tw.date()

    logger.info("Starting StockNotify run | Date=%s | Session=%s | Limit=%d", run_date, session_type, args.limit)
    t0 = time.time()

    total_scanned, recommendations = run_market_screening(limit=args.limit)
    elapsed = time.time() - t0

    print_summary_table(recommendations)

    # 1. 儲存至 Supabase
    save_results_to_supabase(
        run_date=run_date,
        session_type=session_type,
        total_scanned=total_scanned,
        recommendations=recommendations,
        execution_time_sec=elapsed,
    )

    # 2. 發送 Discord Webhook 通知
    send_discord_notification(
        run_date=run_date,
        session_type=session_type,
        total_scanned=total_scanned,
        recommendations=recommendations,
    )

    # 3. 發送 Email 通知
    send_email_notification(
        run_date=run_date,
        session_type=session_type,
        total_scanned=total_scanned,
        recommendations=recommendations,
    )

    logger.info("StockNotify run finished in %.2f seconds.", elapsed)


if __name__ == "__main__":
    main()
