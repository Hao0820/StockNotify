"""Configuration settings loaded from environment variables."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # Supabase
    supabase_url: str = os.getenv("SUPABASE_URL", "")
    supabase_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", ""))

    # FinMind API
    finmind_api_token: str = os.getenv("FINMIND_API_TOKEN", "")

    # Discord Webhook
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")

    # Email SMTP
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    notify_email_to: list[str] = None

    # Screening thresholds
    # 最小 5 日均量（股數），預設 500,000 股（即 500 張），過濾流動性過低的冷門股
    min_avg_volume_shares: int = int(os.getenv("MIN_AVG_VOLUME_SHARES", "500000"))
    # KD 黃金交叉附近容許差距：|K - D| <= 4.0 且 K 向上
    kd_near_cross_diff: float = float(os.getenv("KD_NEAR_CROSS_DIFF", "4.0"))
    # RSI 黃金交叉附近容許差距：|RSI6 - RSI12| <= 3.5 且 RSI6 向上
    rsi_near_cross_diff: float = float(os.getenv("RSI_NEAR_CROSS_DIFF", "3.5"))
    # 推薦股票最大數量
    top_recommendations_limit: int = int(os.getenv("TOP_RECOMMENDATIONS_LIMIT", "25"))

    def __post_init__(self):
        raw_emails = os.getenv("NOTIFY_EMAIL_TO", "")
        if raw_emails:
            self.notify_email_to = [e.strip() for e in raw_emails.split(",") if e.strip()]
        else:
            self.notify_email_to = []


settings = Settings()
