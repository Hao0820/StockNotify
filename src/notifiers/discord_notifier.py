"""Send rich Discord Webhook notifications for recommended Taiwan stocks."""

import logging
from datetime import date
import requests

from src.config import settings
from src.screener import StockRecommendation

logger = logging.getLogger(__name__)

SESSION_LABELS = {
    "morning_0800": "🌅 盤前 08:00 選股報告",
    "intraday_1300": "⚡ 盤中 13:00 即時選股報告",
    "manual": "🔍 手動執行選股報告",
}


def send_discord_notification(
    run_date: date,
    session_type: str,
    total_scanned: int,
    recommendations: list[StockRecommendation],
) -> bool:
    """Send stock recommendations to Discord via Webhook."""
    if not settings.discord_webhook_url:
        logger.info("DISCORD_WEBHOOK_URL not set. Skipping Discord notification.")
        return False

    title_label = SESSION_LABELS.get(session_type, session_type)
    header_content = (
        f"**【StockNotify 台股籌碼＋技術面選股】** `{run_date.isoformat()}` {title_label}\n"
        f"掃描全市場 `{total_scanned}` 檔股票，篩選出 `{len(recommendations)}` 檔符合 "
        f"**KD 金叉附近 + RSI 金叉附近 + MACD 轉強 + 法人/大戶籌碼集中** 之標的："
    )

    if not recommendations:
        payload = {
            "content": header_content + "\n*(今日無符合所有嚴格條件之標的)*",
        }
        try:
            resp = requests.post(settings.discord_webhook_url, json=payload, timeout=15)
            return resp.status_code in (200, 204)
        except Exception as exc:
            logger.error("Failed to send Discord notification: %s", exc)
            return False

    embeds = []
    # Discord 單一訊息最多支援 10 個 Embeds，顯示前 10 名高分個股
    for rank, rec in enumerate(recommendations[:10], start=1):
        arrow = "🔺" if rec.price_change_pct > 0 else ("🔻" if rec.price_change_pct < 0 else "➖")
        yahoo_url = f"https://tw.stock.yahoo.com/quote/{rec.stock_id}.{'TW' if rec.market == 'TWSE' else 'TWO'}"
        fields = [
            {
                "name": "💰 收盤/現價 (漲跌)",
                "value": f"**{rec.close_price}** ({arrow} {rec.price_change_pct:+.2f}%)\n成交量: {rec.volume_lots:,} 張",
                "inline": True,
            },
            {
                "name": "📈 技術指標 (KD / RSI / MACD)",
                "value": (
                    f"K={rec.k_value} / D={rec.d_value}\n"
                    f"RSI6={rec.rsi_6} / RSI12={rec.rsi_12}\n"
                    f"MACD柱={rec.macd_hist:+.3f} (前值 {rec.macd_prev_hist:+.3f})"
                ),
                "inline": True,
            },
            {
                "name": "🏦 籌碼集中度 (外資 / 投信 / 400張大戶)",
                "value": (
                    f"外資近5日: `{rec.foreign_net_lots:+,}張` | 投信近5日: `{rec.trust_net_lots:+,}張`\n"
                    f"法人集中度: `{rec.inst_concentration_pct:+.2f}%` | "
                    f"400張大戶: `{rec.major_holder_400_pct:.1f}%` ({rec.major_holder_change_pct:+.2f}%)"
                ),
                "inline": False,
            },
            {
                "name": "✅ 觸發理由",
                "value": "、".join(rec.reasons),
                "inline": False,
            },
        ]

        embeds.append(
            {
                "title": f"#{rank} {rec.stock_id} {rec.stock_name} ({rec.market}) — 總分 {rec.total_score}",
                "url": yahoo_url,
                "color": 0xE74C3C if rec.price_change_pct >= 0 else 0x2ECC71,
                "fields": fields,
            }
        )

    payload = {
        "content": header_content,
        "embeds": embeds,
    }

    try:
        resp = requests.post(settings.discord_webhook_url, json=payload, timeout=15)
        if resp.status_code in (200, 204):
            logger.info("Discord notification sent successfully.")
            return True
        logger.warning("Discord webhook returned status %d: %s", resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.error("Failed to send Discord notification: %s", exc)
        return False
