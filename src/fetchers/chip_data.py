"""Fetch institutional investors (外資/投信) and TDCC major shareholder (集保大戶) concentration data."""

import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict
import pandas as pd
import requests

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ChipMetrics:
    stock_id: str
    foreign_net_lots_5d: int = 0          # 外資近 N 日累計買賣超 (張)
    trust_net_lots_5d: int = 0            # 投信近 N 日累計買賣超 (張)
    inst_concentration_pct: float = 0.0   # 外資+投信買超佔期間總成交量集中度 (%)
    major_holder_400_pct: float = 0.0     # 400張以上大戶持股比例 (%)
    major_holder_change_pct: float = 0.0  # 400張以上大戶持股比例較前週增減 (%)
    chip_score: float = 0.0
    chip_reasons: list[str] = None

    def __post_init__(self):
        if self.chip_reasons is None:
            self.chip_reasons = []


def _parse_int(val) -> int:
    if val is None:
        return 0
    s = str(val).replace(",", "").strip()
    try:
        return int(float(s))
    except ValueError:
        return 0


def fetch_tdcc_major_holders_bulk() -> Dict[str, tuple[float, float]]:
    """
    從 TDCC 集保中心 Open Data 下載最新全市場「集保戶股權分散表」CSV，
    計算每檔股票 400 張以上 (持股分級 12, 13, 14, 15) 的大戶持股比例 (%)。
    回傳: dict[stock_id, (major_400_pct, change_pct)]
    """
    result: Dict[str, tuple[float, float]] = {}
    url = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200 or not resp.content:
            return result

        df = pd.read_csv(io.BytesIO(resp.content), dtype=str)
        # 欄位通常為: 資料日期, 證券代號, 持股分級, 人數, 股數, 占集保庫存數比例%
        col_code = next((c for c in df.columns if "代號" in c), None)
        col_level = next((c for c in df.columns if "分級" in c), None)
        col_pct = next((c for c in df.columns if "比例" in c), None)

        if not (col_code and col_level and col_pct):
            return result

        df[col_code] = df[col_code].str.strip()
        df[col_level] = pd.to_numeric(df[col_level], errors="coerce")
        df[col_pct] = pd.to_numeric(df[col_pct], errors="coerce").fillna(0.0)

        # 持股分級 12~15 代表 400張~1000張以上大戶：
        # 12: 400~600張, 13: 600~800張, 14: 800~1000張, 15: 1000張以上
        major_df = df[df[col_level].isin([12, 13, 14, 15])]
        grouped = major_df.groupby(col_code)[col_pct].sum()
        for code, pct in grouped.items():
            result[str(code)] = (round(float(pct), 2), 0.0)

        logger.info("Loaded TDCC major shareholder (400+ lots) data for %d stocks.", len(result))
    except Exception as exc:
        logger.warning("TDCC OpenData CSV fetch failed: %s", exc)

    return result


def fetch_twse_tpex_latest_institutional() -> Dict[str, tuple[int, int]]:
    """
    從證交所 (TWSE T86) 與櫃買中心 (TPEx) OpenAPI 抓取最新交易日全市場外資與投信買賣超（張數）。
    回傳: dict[stock_id, (foreign_net_lots, trust_net_lots)]
    """
    inst_map: Dict[str, tuple[int, int]] = {}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 1. TWSE 證交所三大法人買賣超日報 (最近 5 個日曆天內往前找最近一個交易日)
    for offset in range(0, 6):
        dt_str = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={dt_str}&selectType=ALLBUT0999&response=json"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            data = resp.json()
            if data.get("stat") == "OK" and data.get("data"):
                for row in data["data"]:
                    code = str(row[0]).strip()
                    if len(code) == 4 and code.isdigit():
                        # row[4]: 外陸資買賣超股數(不含外資自營商), row[10]: 投信買賣超股數
                        foreign_shares = _parse_int(row[4])
                        trust_shares = _parse_int(row[10])
                        inst_map[code] = (round(foreign_shares / 1000), round(trust_shares / 1000))
                logger.info("Loaded TWSE T86 institutional data for %s (%d stocks).", dt_str, len(inst_map))
                break
        except Exception as exc:
            logger.debug("TWSE T86 fetch for %s skipped: %s", dt_str, exc)

    # 2. TPEx 櫃買中心三大法人買賣超日報
    for offset in range(0, 6):
        dt = datetime.now() - timedelta(days=offset)
        roc_year = dt.year - 1911
        dt_str = f"{roc_year}/{dt.strftime('%m/%d')}"
        url = (
            f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
            f"3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={dt_str}&o=json"
        )
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            data = resp.json()
            rows = data.get("aaData") or data.get("tables", [{}])[0].get("data", [])
            if rows:
                count_tpex = 0
                for row in rows:
                    code = str(row[0]).strip()
                    if len(code) == 4 and code.isdigit():
                        # 櫃買中心欄位：外資買賣超股數通常在 index 10 (或 4)，投信買賣超股數在 index 13
                        foreign_shares = _parse_int(row[10]) if len(row) > 13 else _parse_int(row[4])
                        trust_shares = _parse_int(row[13]) if len(row) > 13 else _parse_int(row[7])
                        inst_map[code] = (round(foreign_shares / 1000), round(trust_shares / 1000))
                        count_tpex += 1
                logger.info("Loaded TPEx institutional data for %s (%d stocks).", dt_str, count_tpex)
                break
        except Exception as exc:
            logger.debug("TPEx institutional fetch for %s skipped: %s", dt_str, exc)

    return inst_map


def enrich_candidate_chips_with_finmind(
    stock_id: str,
    avg_5d_volume_lots: float,
    base_inst: tuple[int, int] | None,
    base_tdcc: tuple[float, float] | None,
) -> ChipMetrics:
    """
    針對通過技術面初篩的候選股，進一步透過 FinMind API 查詢近 5 日三大法人買賣超累計與大戶持股增減，
    若無 FinMind 則使用 TWSE/TPEx 與 TDCC 全市場批次資料計算集中度。
    """
    foreign_lots, trust_lots = base_inst if base_inst else (0, 0)
    major_400_pct, major_change_pct = base_tdcc if base_tdcc else (0.0, 0.0)

    start_date_inst = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    start_date_tdcc = (datetime.now() - timedelta(days=25)).strftime("%Y-%m-%d")

    # 1. 嘗試透過 FinMind 取得近 5 個交易日外資與投信累計買超
    try:
        params = {
            "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
            "data_id": stock_id,
            "start_date": start_date_inst,
        }
        if settings.finmind_api_token:
            params["token"] = settings.finmind_api_token
        resp = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=10)
        rows = resp.json().get("data", [])
        if rows:
            df_inst = pd.DataFrame(rows)
            recent_dates = sorted(df_inst["date"].unique())[-5:]
            df_recent = df_inst[df_inst["date"].isin(recent_dates)].copy()
            df_recent["net"] = pd.to_numeric(df_recent["buy"], errors="coerce").fillna(0) - pd.to_numeric(
                df_recent["sell"], errors="coerce"
            ).fillna(0)

            f_df = df_recent[df_recent["name"].str.contains("Foreign_Investor", na=False)]
            t_df = df_recent[df_recent["name"].str.contains("Investment_Trust", na=False)]
            foreign_lots = int(round(f_df["net"].sum() / 1000.0))
            trust_lots = int(round(t_df["net"].sum() / 1000.0))
    except Exception as exc:
        logger.debug("FinMind institutional lookup fallback for %s: %s", stock_id, exc)

    # 2. 嘗試透過 FinMind 取得近兩週 TDCC 大戶持股比例變化 (400張以上 level 12~15)
    try:
        params = {
            "dataset": "TaiwanStockHoldingSharesPer",
            "data_id": stock_id,
            "start_date": start_date_tdcc,
        }
        if settings.finmind_api_token:
            params["token"] = settings.finmind_api_token
        resp = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=10)
        rows = resp.json().get("data", [])
        if rows:
            df_hold = pd.DataFrame(rows)
            major_levels = [
                "400,001-600,000",
                "600,001-800,000",
                "800,001-1,000,000",
                "more than 1,000,001",
            ]
            df_major = df_hold[df_hold["HoldingSharesLevel"].isin(major_levels)].copy()
            if not df_major.empty:
                df_major["percent"] = pd.to_numeric(df_major["percent"], errors="coerce").fillna(0.0)
                by_date = df_major.groupby("date")["percent"].sum().sort_index()
                if len(by_date) >= 1:
                    major_400_pct = round(float(by_date.iloc[-1]), 2)
                if len(by_date) >= 2:
                    major_change_pct = round(float(by_date.iloc[-1] - by_date.iloc[-2]), 2)
    except Exception as exc:
        logger.debug("FinMind TDCC lookup fallback for %s: %s", stock_id, exc)

    # 計算外資+投信集中度 (%) = (外資+投信近5日淨買超張數) / (5日總成交張數) * 100
    total_5d_vol = max(avg_5d_volume_lots * 5.0, 1.0)
    inst_conc_pct = round(((foreign_lots + trust_lots) / total_5d_vol) * 100.0, 2)

    score = 0.0
    reasons: list[str] = []

    if trust_lots > 50:
        score += 20.0
        reasons.append(f"投信買超+{trust_lots}張")
    elif trust_lots > 0:
        score += 10.0
        reasons.append(f"投信小買+{trust_lots}張")

    if foreign_lots > 200:
        score += 18.0
        reasons.append(f"外資買超+{foreign_lots}張")
    elif foreign_lots > 0:
        score += 8.0
        reasons.append(f"外資偏多+{foreign_lots}張")

    if inst_conc_pct >= 5.0:
        score += 15.0
        reasons.append(f"法人集中度高({inst_conc_pct}%)")
    elif inst_conc_pct >= 1.5:
        score += 8.0
        reasons.append(f"法人集中度正向({inst_conc_pct}%)")

    if major_change_pct > 0.3:
        score += 15.0
        reasons.append(f"400張大戶增加+{major_change_pct}% (達{major_400_pct}%)")
    elif major_400_pct >= 60.0:
        score += 10.0
        reasons.append(f"400張大戶高集中({major_400_pct}%)")
    elif major_400_pct >= 40.0:
        score += 5.0
        reasons.append(f"大戶持股穩定({major_400_pct}%)")

    return ChipMetrics(
        stock_id=stock_id,
        foreign_net_lots_5d=foreign_lots,
        trust_net_lots_5d=trust_lots,
        inst_concentration_pct=inst_conc_pct,
        major_holder_400_pct=major_400_pct,
        major_holder_change_pct=major_change_pct,
        chip_score=score,
        chip_reasons=reasons,
    )
