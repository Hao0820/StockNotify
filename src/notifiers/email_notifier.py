"""Send HTML Email notifications via SMTP for recommended Taiwan stocks."""

import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.config import settings
from src.screener import StockRecommendation

logger = logging.getLogger(__name__)

SESSION_TITLES = {
    "morning_0800": "盤前 08:00 選股報告",
    "intraday_1300": "盤中 13:00 即時選股報告",
    "manual": "手動執行選股報告",
}


def send_email_notification(
    run_date: date,
    session_type: str,
    total_scanned: int,
    recommendations: list[StockRecommendation],
) -> bool:
    """Send an HTML formatted table of stock recommendations via SMTP."""
    if not (settings.smtp_user and settings.smtp_password and settings.notify_email_to):
        logger.info("SMTP Email settings not fully configured. Skipping Email notification.")
        return False

    session_title = SESSION_TITLES.get(session_type, session_type)
    subject = f"【StockNotify】{run_date.isoformat()} {session_title} (共 {len(recommendations)} 檔符合條件)"

    rows_html = ""
    for idx, rec in enumerate(recommendations, start=1):
        color = "#d93025" if rec.price_change_pct >= 0 else "#188038"
        yahoo_link = f"https://tw.stock.yahoo.com/quote/{rec.stock_id}.{'TW' if rec.market == 'TWSE' else 'TWO'}"
        reasons_str = "<br>".join(rec.reasons)
        rows_html += f"""
        <tr>
            <td style="padding:8px;border:1px solid #ddd;text-align:center;">{idx}</td>
            <td style="padding:8px;border:1px solid #ddd;">
                <a href="{yahoo_link}" target="_blank"><b>{rec.stock_id} {rec.stock_name}</b></a>
                <br><small style="color:#666;">{rec.market}</small>
            </td>
            <td style="padding:8px;border:1px solid #ddd;text-align:right;color:{color};">
                <b>{rec.close_price:.2f}</b><br><small>{rec.price_change_pct:+.2f}%</small>
            </td>
            <td style="padding:8px;border:1px solid #ddd;text-align:right;">{rec.volume_lots:,}</td>
            <td style="padding:8px;border:1px solid #ddd;">
                K: {rec.k_value:.1f} / D: {rec.d_value:.1f}<br>
                RSI6: {rec.rsi_6:.1f} / RSI12: {rec.rsi_12:.1f}<br>
                MACD柱: {rec.macd_hist:+.3f}
            </td>
            <td style="padding:8px;border:1px solid #ddd;">
                外資5日: <b>{rec.foreign_net_lots:+,}</b> 張<br>
                投信5日: <b>{rec.trust_net_lots:+,}</b> 張<br>
                法人集中: {rec.inst_concentration_pct:+.2f}%
            </td>
            <td style="padding:8px;border:1px solid #ddd;text-align:right;">
                <b>{rec.major_holder_400_pct:.1f}%</b><br>
                <small>({rec.major_holder_change_pct:+.2f}%)</small>
            </td>
            <td style="padding:8px;border:1px solid #ddd;text-align:center;"><b>{rec.total_score:.1f}</b></td>
            <td style="padding:8px;border:1px solid #ddd;font-size:12px;">{reasons_str}</td>
        </tr>
        """

    if not rows_html:
        rows_html = '<tr><td colspan="9" style="padding:16px;text-align:center;">今日無符合所有條件之股票</td></tr>'

    html_body = f"""
    <html>
    <body style="font-family: Arial, 'Microsoft JhengHei', sans-serif; color: #222;">
        <h2>📈 StockNotify 台股籌碼與技術面選股報告</h2>
        <p>
            <b>執行日期：</b>{run_date.isoformat()} &nbsp;|&nbsp;
            <b>時段：</b>{session_title} &nbsp;|&nbsp;
            <b>掃描檔數：</b>{total_scanned} 檔 &nbsp;|&nbsp;
            <b>入選檔數：</b>{len(recommendations)} 檔
        </p>
        <p style="font-size:13px;color:#555;">
            篩選條件：KD (9,3,3) 黃金交叉附近 ＋ RSI (6,12) 黃金交叉附近 ＋ MACD (12,26,9) 轉強 ＋ 外資/投信/400張大戶籌碼集中度正向
        </p>
        <table style="border-collapse:collapse;width:100%;font-size:13px;">
            <thead>
                <tr style="background-color:#f4f6f8;">
                    <th style="padding:8px;border:1px solid #ddd;">排名</th>
                    <th style="padding:8px;border:1px solid #ddd;">股票代號/名稱</th>
                    <th style="padding:8px;border:1px solid #ddd;">價格(漲跌)</th>
                    <th style="padding:8px;border:1px solid #ddd;">成交張數</th>
                    <th style="padding:8px;border:1px solid #ddd;">技術指標</th>
                    <th style="padding:8px;border:1px solid #ddd;">外資/投信籌碼</th>
                    <th style="padding:8px;border:1px solid #ddd;">400張大戶比例</th>
                    <th style="padding:8px;border:1px solid #ddd;">總分</th>
                    <th style="padding:8px;border:1px solid #ddd;">觸發訊號</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_user
    msg["To"] = ", ".join(settings.notify_email_to)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, settings.notify_email_to, msg.as_string())
        logger.info("Email notification sent successfully to %s.", settings.notify_email_to)
        return True
    except Exception as exc:
        logger.error("Failed to send Email notification: %s", exc)
        return False
