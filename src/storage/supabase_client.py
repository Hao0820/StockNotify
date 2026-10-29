"""Supabase client wrapper for saving screening runs and stock recommendations."""

import logging
from datetime import date
from supabase import create_client, Client

from src.config import settings
from src.screener import StockRecommendation

logger = logging.getLogger(__name__)


def get_supabase_client() -> Client | None:
    if not settings.supabase_url or not settings.supabase_key:
        logger.warning("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not configured. Skipping Supabase storage.")
        return None
    try:
        return create_client(settings.supabase_url, settings.supabase_key)
    except Exception as exc:
        logger.error("Failed to initialize Supabase client: %s", exc)
        return None


def save_results_to_supabase(
    run_date: date,
    session_type: str,
    total_scanned: int,
    recommendations: list[StockRecommendation],
    execution_time_sec: float,
) -> str | None:
    """
    Save the screening run metadata to `screening_runs` and upsert recommendations
    into `stock_recommendations`.
    Returns the `run_id` UUID string if successful, or None.
    """
    client = get_supabase_client()
    if client is None:
        return None

    date_str = run_date.isoformat()

    try:
        # 1. Insert screening_runs record
        run_payload = {
            "run_date": date_str,
            "session_type": session_type,
            "total_scanned": total_scanned,
            "total_matched": len(recommendations),
            "execution_time_sec": round(execution_time_sec, 2),
            "status": "completed",
        }
        run_resp = client.table("screening_runs").insert(run_payload).execute()
        run_id = run_resp.data[0]["id"] if run_resp.data else None

        if not recommendations:
            logger.info("Saved empty screening run (run_id=%s) to Supabase.", run_id)
            return run_id

        # 2. Upsert stock_recommendations records
        rows = []
        for rec in recommendations:
            rows.append(
                {
                    "run_id": run_id,
                    "run_date": date_str,
                    "session_type": session_type,
                    "stock_id": rec.stock_id,
                    "stock_name": rec.stock_name,
                    "market": rec.market,
                    "close_price": rec.close_price,
                    "price_change_pct": rec.price_change_pct,
                    "volume_lots": rec.volume_lots,
                    "k_value": rec.k_value,
                    "d_value": rec.d_value,
                    "kd_signal": rec.kd_signal,
                    "rsi_6": rec.rsi_6,
                    "rsi_12": rec.rsi_12,
                    "rsi_signal": rec.rsi_signal,
                    "macd_dif": rec.macd_dif,
                    "macd_dea": rec.macd_dea,
                    "macd_hist": rec.macd_hist,
                    "macd_prev_hist": rec.macd_prev_hist,
                    "macd_signal": rec.macd_signal,
                    "foreign_net_lots": rec.foreign_net_lots,
                    "trust_net_lots": rec.trust_net_lots,
                    "inst_concentration_pct": rec.inst_concentration_pct,
                    "major_holder_400_pct": rec.major_holder_400_pct,
                    "major_holder_change_pct": rec.major_holder_change_pct,
                    "total_score": rec.total_score,
                    "reasons": rec.reasons,
                }
            )

        client.table("stock_recommendations").upsert(
            rows, on_conflict="run_date,session_type,stock_id"
        ).execute()

        logger.info(
            "Successfully saved %d recommendations to Supabase (run_id=%s).",
            len(rows),
            run_id,
        )
        return run_id
    except Exception as exc:
        logger.error("Error saving results to Supabase: %s", exc)
        return None
