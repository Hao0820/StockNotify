// Supabase Edge Function: stock-screener (雙軌制隱藏飆股引擎 • 100分滿分制 • 《籌碼K線》同款 Top15 主力 5日/10日集中度版)
//
// 核心篩選與評分架構（滿分 100 分）：
// 1. 【籌碼集中度維度】(最高 35 分)：
//    - 串接《籌碼K線》同款 Top 15 買賣方主力分點差額 (`chip5`, `chip10`) 與對齊當期 5日/10日成交量：
//      * 5日主力籌碼集中(張) 與 【5日籌碼集中度(%)】 (與《籌碼K線》App 數值一致！)
//      * 10日主力籌碼集中(張) 與 【10日籌碼集中度(%)】 (與《籌碼K線》App 數值一致！)
//    - 證交所 T86 與 櫃買中心三大法人日報：
//      * 外資、投信 5日/10日累計買賣超(張) 與 連續買超天數 (streak)
//    - TDCC 集保中心全市場股權分散表：
//      * 400張以上大戶持股比例(Level 12~15) vs 100張以下散戶持股比例(Level 1~9)
// 2. 【關鍵支撐與隱藏飆股型態維度】(最高 35 分)：
//    - 多週期 (日線 1D / 週線 1W / 月線 1M) 12 EMA 與 布林通道 (21, 2.1) 中軌、下軌距離檢查：
//      * 日線容忍範圍 <= 1.0%
//      * 週線容忍範圍 <= 3.0%
//      * 月線容忍範圍 <= 5.0%
//    - 布林帶寬壓縮 (Bollinger Squeeze，帶寬 <= 15%) 與 量能異動 (帶量點火 / 量縮回踩)
// 3. 【技術動能轉強維度】(最高 30 分)：
//    - KD (9, 3, 3) 黃金交叉或準黃金交叉
//    - RSI (6, 12) 黃金交叉或準黃金交叉
//    - MACD (12, 26, 9) 柱狀體翻紅、紅柱放大或綠柱收斂轉強

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

interface StockBasic {
  stockId: string;
  stockName: string;
  market: "TWSE" | "TPEX";
  yahooSymbol: string;
  closePrice: number;
  volumeShares: number;
}

interface DailyInstRecord {
  dateStr: string;
  foreignLots: number;
  trustLots: number;
}

interface MultiDayInstChip {
  foreign5dLots: number;
  trust5dLots: number;
  foreign10dLots: number;
  trust10dLots: number;
  foreignStreak: number;
  trustStreak: number;
}

interface TdccHolding {
  major400Pct: number;   // 400張以上大戶比例 (Level 12~15)
  retail100Pct: number;  // 100張以下散戶比例 (Level 1~9)
}

interface ChipCandidate {
  stock: StockBasic;
  inst: MultiDayInstChip;
  major400Pct: number;
  retail100Pct: number;
  estConc5dPct: number;
  prelimScore: number;
}

interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface BrokerChipConcentration {
  chip5dLots: number;
  conc5dPct: number;
  vol5dLots: number;
  chip10dLots: number;
  conc10dPct: number;
  vol10dLots: number;
  source: "top15_broker" | "institutional_fallback";
}

interface SupportCheckResult {
  matched: boolean;
  matchedLabels: string[];
  supportScore: number; // 0 ~ 35
  dailyBandwidthPct: number;
  isSqueeze: boolean;
  volumeRatio5d: number;
  rawSupportDetails: Record<string, any>;
}

interface FullStockEvaluation {
  passed: boolean;
  trackType: string;
  closePrice: number;
  priceChangePct: number;
  volumeLots: number;
  kVal: number;
  dVal: number;
  kdSignal: string;
  rsi6: number;
  rsi12: number;
  rsiSignal: string;
  macdDif: number;
  macdDea: number;
  macdHist: number;
  macdPrevHist: number;
  macdSignal: string;
  chip5dLots: number;
  conc5dPct: number;
  chip10dLots: number;
  conc10dPct: number;
  chipScore: number;    // 0 ~ 35
  supportScore: number; // 0 ~ 35
  techScore: number;    // 0 ~ 30
  totalScore: number;   // 0 ~ 100
  reasons: string[];
  rawMetrics: Record<string, any>;
}

const BB_PERIOD = 21;
const BB_MULT = 2.1;
const EMA_PERIOD = 12;

function cleanEnv(val: string | undefined): string {
  if (!val) return "";
  return val.trim().replace(/^["']|["']$/g, "").trim();
}

function getTaipeiDateStr(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function getTaipeiHour(): number {
  const hrStr = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    hour: "2-digit",
    hour12: false,
  }).format(new Date());
  return parseInt(hrStr, 10);
}

function parseNum(val: unknown): number {
  if (val === null || val === undefined) return 0;
  const cleaned = String(val).replace(/,/g, "").trim();
  const n = parseFloat(cleaned);
  return isNaN(n) ? 0 : n;
}

function getRecentWeekdayDates(count = 15): Array<{ yyyymmdd: string; rocDate: string }> {
  const list: Array<{ yyyymmdd: string; rocDate: string }> = [];
  let offset = 0;
  while (list.length < count && offset < 30) {
    const dt = new Date(Date.now() + 8 * 3600_000 - offset * 86400_000);
    const dayOfWeek = dt.getUTCDay();
    offset++;
    if (dayOfWeek === 0 || dayOfWeek === 6) continue;

    const yyyy = dt.getUTCFullYear();
    const rocYear = yyyy - 1911;
    const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
    const dd = String(dt.getUTCDate()).padStart(2, "0");
    list.push({
      yyyymmdd: `${yyyy}${mm}${dd}`,
      rocDate: `${rocYear}/${mm}/${dd}`,
    });
  }
  return list;
}

/**
 * 取得 HiStock 通用 Chart Token (只需請求 1 次，即可用於查詢所有股票的 Top 15 主力籌碼集中度)
 */
async function fetchHiStockPageToken(): Promise<string> {
  try {
    const res = await fetch("https://histock.tw/stock/main.aspx?no=2330", {
      headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" },
    });
    if (!res.ok) return "";
    const html = await res.text();
    const m = html.match(/HiChartPageToken\s*=\s*"([^"]+)"/);
    return m ? m[1] : "";
  } catch (_) {
    return "";
  }
}

/**
 * 查詢個股《籌碼K線》同款 Top 15 買賣超主力分點 5日 與 10日 籌碼集中張數與集中度 (%)
 * 公式：
 *   5日籌碼集中度(%) = chip5[last] / (對齊 chip5 日期之近5日總成交張數) * 100
 *   10日籌碼集中度(%) = chip10[last] / (對齊 chip10 日期之近10日總成交張數) * 100
 */
async function fetchTop15BrokerConcentration(
  stockId: string,
  pageToken: string
): Promise<BrokerChipConcentration | null> {
  if (!pageToken) return null;
  try {
    const url = `https://histock.tw/stock/chip/chartdata.aspx?no=${encodeURIComponent(
      stockId
    )}&m=volume,chip5,chip10&p=${encodeURIComponent(pageToken)}`;
    const res = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        Referer: `https://histock.tw/stock/main.aspx?no=${stockId}`,
      },
    });
    if (!res.ok) return null;
    const data = await res.json();
    if (!data || data.Error || !data.chip5 || !data.chip10 || !data.Volume) {
      return null;
    }

    const chip5Arr: Array<[number, number]> = JSON.parse(data.chip5);
    const chip10Arr: Array<[number, number]> = JSON.parse(data.chip10);
    const volArr: Array<[number, number]> = JSON.parse(data.Volume);

    if (chip5Arr.length === 0 || chip10Arr.length === 0 || volArr.length < 10) {
      return null;
    }

    const lastChip5 = chip5Arr[chip5Arr.length - 1];
    const lastChip10 = chip10Arr[chip10Arr.length - 1];
    const targetTs = lastChip5[0];

    const chip5dLots = Math.round(Number(lastChip5[1]));
    const chip10dLots = Math.round(Number(lastChip10[1]));

    // 找到 Volume 陣列中對齊 chip5 最新結算日期的索引 (若盤中尚未結算當日分點，自動對齊至前一個已結算交易日)
    let alignIdx = volArr.findIndex((v) => Math.abs(v[0] - targetTs) < 43200_000);
    if (alignIdx < 0) {
      alignIdx = volArr.length - 1;
    }

    const vol5Slice = volArr.slice(Math.max(0, alignIdx - 4), alignIdx + 1);
    const vol10Slice = volArr.slice(Math.max(0, alignIdx - 9), alignIdx + 1);

    const vol5dLots = Math.max(
      1,
      Math.round(vol5Slice.reduce((sum, item) => sum + Number(item[1] || 0), 0))
    );
    const vol10dLots = Math.max(
      1,
      Math.round(vol10Slice.reduce((sum, item) => sum + Number(item[1] || 0), 0))
    );

    const conc5dPct = Number(((chip5dLots / vol5dLots) * 100).toFixed(2));
    const conc10dPct = Number(((chip10dLots / vol10dLots) * 100).toFixed(2));

    return {
      chip5dLots,
      conc5dPct,
      vol5dLots,
      chip10dLots,
      conc10dPct,
      vol10dLots,
      source: "top15_broker",
    };
  } catch (_) {
    return null;
  }
}

/**
 * 1. 從證交所 (TWSE) 與櫃買中心 (TPEx) 取得全市場所有 4 碼上市櫃普通股
 */
async function fetchAllTaiwanOrdinaryStocks(): Promise<StockBasic[]> {
  const stocks: StockBasic[] = [];
  const headers = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" };

  const [twseRes, tpexRes] = await Promise.allSettled([
    fetch("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", { headers }),
    fetch("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", { headers }),
  ]);

  if (twseRes.status === "fulfilled" && twseRes.value.ok) {
    try {
      const data = await twseRes.value.json();
      for (const row of data) {
        const code = String(row.Code || "").trim();
        const name = String(row.Name || "").trim();
        if (/^[1-9]\d{3}$/.test(code)) {
          const volShares = parseNum(row.TradeVolume);
          const closePrice = parseNum(row.ClosingPrice);
          if (closePrice > 0) {
            stocks.push({
              stockId: code,
              stockName: name,
              market: "TWSE",
              yahooSymbol: `${code}.TW`,
              closePrice,
              volumeShares: volShares,
            });
          }
        }
      }
    } catch (e) {
      console.warn("TWSE OpenAPI parse error:", e);
    }
  }

  if (tpexRes.status === "fulfilled" && tpexRes.value.ok) {
    try {
      const data = await tpexRes.value.json();
      for (const row of data) {
        const code = String(row.SecuritiesCompanyCode || "").trim();
        const name = String(row.CompanyName || "").trim();
        if (/^[1-9]\d{3}$/.test(code)) {
          const volShares = parseNum(row.TradingShares);
          const closePrice = parseNum(row.Close);
          if (closePrice > 0) {
            stocks.push({
              stockId: code,
              stockName: name,
              market: "TPEX",
              yahooSymbol: `${code}.TWO`,
              closePrice,
              volumeShares: volShares,
            });
          }
        }
      }
    } catch (e) {
      console.warn("TPEx OpenAPI parse error:", e);
    }
  }

  return stocks;
}

/**
 * 2. 平行抓取最近 10 個交易日「證交所 T86」與「櫃買中心」三大法人買賣超
 */
async function fetch10DayInstitutionalChips(): Promise<Map<string, MultiDayInstChip>> {
  const candidateDates = getRecentWeekdayDates(15);
  const headers = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" };

  const twsePromises = candidateDates.map(async (d) => {
    try {
      const url = `https://www.twse.com.tw/rwd/zh/fund/T86?date=${d.yyyymmdd}&selectType=ALLBUT0999&response=json`;
      const res = await fetch(url, { headers });
      if (!res.ok) return null;
      const json = await res.json();
      if (json.stat === "OK" && Array.isArray(json.data) && json.data.length > 100) {
        const dayMap = new Map<string, { f: number; t: number }>();
        for (const row of json.data) {
          const code = String(row[0] || "").trim();
          if (/^[1-9]\d{3}$/.test(code)) {
            const fShares = parseNum(row[4]);
            const tShares = parseNum(row[10]);
            dayMap.set(code, {
              f: Math.round(fShares / 1000),
              t: Math.round(tShares / 1000),
            });
          }
        }
        return { dateStr: d.yyyymmdd, dayMap };
      }
    } catch (_) {
      // ignore
    }
    return null;
  });

  const tpexPromises = candidateDates.map(async (d) => {
    try {
      const url = `https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d=${d.rocDate}&o=json`;
      const res = await fetch(url, { headers });
      if (!res.ok) return null;
      const json = await res.json();
      const rows = json.aaData || json.tables?.[0]?.data || [];
      if (Array.isArray(rows) && rows.length > 50) {
        const dayMap = new Map<string, { f: number; t: number }>();
        for (const row of rows) {
          const code = String(row[0] || "").trim();
          if (/^[1-9]\d{3}$/.test(code)) {
            const fShares = row.length > 13 ? parseNum(row[10]) : parseNum(row[4]);
            const tShares = row.length > 13 ? parseNum(row[13]) : parseNum(row[7]);
            dayMap.set(code, {
              f: Math.round(fShares / 1000),
              t: Math.round(tShares / 1000),
            });
          }
        }
        return { dateStr: d.yyyymmdd, dayMap };
      }
    } catch (_) {
      // ignore
    }
    return null;
  });

  const [twseResults, tpexResults] = await Promise.all([
    Promise.all(twsePromises),
    Promise.all(tpexPromises),
  ]);

  const validTwseDays = twseResults
    .filter((r): r is NonNullable<typeof r> => r !== null)
    .sort((a, b) => b.dateStr.localeCompare(a.dateStr))
    .slice(0, 10);

  const validTpexDays = tpexResults
    .filter((r): r is NonNullable<typeof r> => r !== null)
    .sort((a, b) => b.dateStr.localeCompare(a.dateStr))
    .slice(0, 10);

  const historyByStock = new Map<string, DailyInstRecord[]>();

  function mergeDays(days: Array<{ dateStr: string; dayMap: Map<string, { f: number; t: number }> }>) {
    for (const d of days) {
      for (const [code, val] of d.dayMap.entries()) {
        let arr = historyByStock.get(code);
        if (!arr) {
          arr = [];
          historyByStock.set(code, arr);
        }
        arr.push({ dateStr: d.dateStr, foreignLots: val.f, trustLots: val.t });
      }
    }
  }

  mergeDays(validTwseDays);
  mergeDays(validTpexDays);

  const finalMap = new Map<string, MultiDayInstChip>();

  for (const [code, records] of historyByStock.entries()) {
    records.sort((a, b) => b.dateStr.localeCompare(a.dateStr));
    const rec5 = records.slice(0, 5);
    const rec10 = records.slice(0, 10);

    const foreign5dLots = rec5.reduce((sum, r) => sum + r.foreignLots, 0);
    const trust5dLots = rec5.reduce((sum, r) => sum + r.trustLots, 0);
    const foreign10dLots = rec10.reduce((sum, r) => sum + r.foreignLots, 0);
    const trust10dLots = rec10.reduce((sum, r) => sum + r.trustLots, 0);

    let foreignStreak = 0;
    for (const r of rec10) {
      if (r.foreignLots > 0) foreignStreak++;
      else break;
    }

    let trustStreak = 0;
    for (const r of rec10) {
      if (r.trustLots > 0) trustStreak++;
      else break;
    }

    finalMap.set(code, {
      foreign5dLots,
      trust5dLots,
      foreign10dLots,
      trust10dLots,
      foreignStreak,
      trustStreak,
    });
  }

  return finalMap;
}

/**
 * 3. 從 TDCC 集保中心 Open Data 下載最新全市場「400張以上大戶比例」與「100張以下散戶比例」
 */
async function fetchTdccHoldings(): Promise<Map<string, TdccHolding>> {
  const tdccMap = new Map<string, TdccHolding>();
  try {
    const res = await fetch("https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5", {
      headers: { "User-Agent": "Mozilla/5.0" },
    });
    if (!res.ok) return tdccMap;
    const text = await res.text();
    const lines = text.split(/\r?\n/);
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i];
      if (!line) continue;
      const cols = line.split(",");
      if (cols.length < 6) continue;
      const stockId = cols[1].trim();
      const level = parseInt(cols[2].trim(), 10);
      const pct = parseFloat(cols[5].trim()) || 0;
      const cur = tdccMap.get(stockId) || { major400Pct: 0, retail100Pct: 0 };
      if (level >= 12 && level <= 15) {
        cur.major400Pct = Number((cur.major400Pct + pct).toFixed(2));
      } else if (level >= 1 && level <= 9) {
        cur.retail100Pct = Number((cur.retail100Pct + pct).toFixed(2));
      }
      tdccMap.set(stockId, cur);
    }
  } catch (e) {
    console.warn("TDCC fetch warning:", e);
  }
  return tdccMap;
}

/**
 * 4. 第一階段：對全市場 1,900+ 檔股票以「5日/10日法人買超、連買天數、大戶持股」進行初篩
 */
function filterCandidatesByMultiDayChips(
  allStocks: StockBasic[],
  instMap: Map<string, MultiDayInstChip>,
  tdccMap: Map<string, TdccHolding>,
  minVolumeLots = 300
): ChipCandidate[] {
  const candidates: ChipCandidate[] = [];

  for (const st of allStocks) {
    const dailyVolLots = Math.round(st.volumeShares / 1000);
    if (dailyVolLots < minVolumeLots) continue;

    const inst = instMap.get(st.stockId) || {
      foreign5dLots: 0,
      trust5dLots: 0,
      foreign10dLots: 0,
      trust10dLots: 0,
      foreignStreak: 0,
      trustStreak: 0,
    };
    const tdcc = tdccMap.get(st.stockId) || { major400Pct: 0, retail100Pct: 0 };

    const net5d = inst.foreign5dLots + inst.trust5dLots;
    const net10d = inst.foreign10dLots + inst.trust10dLots;
    const est5dVol = Math.max(dailyVolLots * 5, 1);
    const estConc5dPct = Number(((net5d / est5dVol) * 100).toFixed(2));

    const hasPositive5dOr10d =
      inst.trust5dLots >= 15 ||
      inst.trustStreak >= 2 ||
      (inst.foreign5dLots >= 100 && net5d > 0) ||
      (net5d > 0 && net10d > 0 && tdcc.major400Pct >= 45.0) ||
      (estConc5dPct >= 3.0 && tdcc.major400Pct >= 40.0);

    if (!hasPositive5dOr10d) continue;

    let prelimScore = 0;
    if (inst.trust5dLots > 0) prelimScore += 12;
    if (inst.trustStreak >= 2) prelimScore += 8;
    if (inst.foreign5dLots > 0) prelimScore += 8;
    if (inst.foreignStreak >= 3) prelimScore += 6;
    if (net5d > 0 && net10d > 0) prelimScore += 8;
    if (tdcc.major400Pct >= 55) prelimScore += 8;

    candidates.push({
      stock: st,
      inst,
      major400Pct: tdcc.major400Pct,
      retail100Pct: tdcc.retail100Pct,
      estConc5dPct,
      prelimScore,
    });
  }

  candidates.sort((a, b) => b.prelimScore - a.prelimScore || b.estConc5dPct - a.estConc5dPct);
  return candidates.slice(0, 350);
}

function resampleBars(dailyBars: Bar[], mode: "W" | "M"): Bar[] {
  const grouped = new Map<string, Bar>();
  for (const b of dailyBars) {
    const dt = new Date(b.time * 1000);
    let key = "";
    if (mode === "M") {
      key = `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, "0")}`;
    } else {
      const day = dt.getUTCDay() || 7;
      const monday = new Date(dt.getTime() - (day - 1) * 86400_000);
      key = `${monday.getUTCFullYear()}-${String(monday.getUTCMonth() + 1).padStart(2, "0")}-${String(
        monday.getUTCDate()
      ).padStart(2, "0")}`;
    }

    const existing = grouped.get(key);
    if (!existing) {
      grouped.set(key, { ...b });
    } else {
      existing.high = Math.max(existing.high, b.high);
      existing.low = Math.min(existing.low, b.low);
      existing.close = b.close;
      existing.volume += b.volume;
    }
  }
  return Array.from(grouped.values());
}

function calcEmaAndBollinger(closes: number[]): {
  ema12: number | null;
  bbMiddle: number | null;
  bbUpper: number | null;
  bbLower: number | null;
  bandwidthPct: number | null;
} {
  const n = closes.length;
  if (n === 0) {
    return { ema12: null, bbMiddle: null, bbUpper: null, bbLower: null, bandwidthPct: null };
  }

  let ema12: number | null = null;
  if (n >= EMA_PERIOD) {
    const k = 2 / (EMA_PERIOD + 1);
    let ema = closes[0];
    for (let i = 1; i < n; i++) {
      ema = closes[i] * k + ema * (1 - k);
    }
    ema12 = ema;
  }

  let bbMiddle: number | null = null;
  let bbUpper: number | null = null;
  let bbLower: number | null = null;
  let bandwidthPct: number | null = null;

  if (n >= BB_PERIOD) {
    const slice = closes.slice(n - BB_PERIOD);
    const mean = slice.reduce((acc, v) => acc + v, 0) / BB_PERIOD;
    const variance = slice.reduce((acc, v) => acc + Math.pow(v - mean, 2), 0) / BB_PERIOD;
    const std = Math.sqrt(variance);
    bbMiddle = mean;
    bbUpper = mean + BB_MULT * std;
    bbLower = mean - BB_MULT * std;
    if (mean > 0) {
      bandwidthPct = ((bbUpper - bbLower) / mean) * 100;
    }
  }

  return { ema12, bbMiddle, bbUpper, bbLower, bandwidthPct };
}

function evaluateMultiTimeframeSupport(dailyBars: Bar[]): SupportCheckResult {
  const currPrice = dailyBars[dailyBars.length - 1].close;
  const weeklyBars = resampleBars(dailyBars, "W");
  const monthlyBars = resampleBars(dailyBars, "M");

  const dStats = calcEmaAndBollinger(dailyBars.map((b) => b.close));
  const wStats = calcEmaAndBollinger(weeklyBars.map((b) => b.close));
  const mStats = calcEmaAndBollinger(monthlyBars.map((b) => b.close));

  const matchedLabels: string[] = [];
  let rawScore = 0;

  function checkLevel(
    tfLabel: string,
    indicatorLabel: string,
    targetVal: number | null,
    tolerancePct: number,
    points: number
  ) {
    if (targetVal === null || targetVal <= 0) return;
    const diffPct = Math.abs((currPrice - targetVal) / targetVal) * 100;
    if (diffPct <= tolerancePct) {
      matchedLabels.push(`${tfLabel}${indicatorLabel}(差${diffPct.toFixed(1)}%)`);
      rawScore += points;
    }
  }

  checkLevel("日", "12EMA", dStats.ema12, 1.0, 14);
  checkLevel("日", "布林中軌", dStats.bbMiddle, 1.0, 14);
  checkLevel("日", "布林下軌", dStats.bbLower, 1.0, 12);

  checkLevel("週", "12EMA", wStats.ema12, 3.0, 15);
  checkLevel("週", "布林中軌", wStats.bbMiddle, 3.0, 15);
  checkLevel("週", "布林下軌", wStats.bbLower, 3.0, 13);

  checkLevel("月", "12EMA", mStats.ema12, 5.0, 15);
  checkLevel("月", "布林中軌", mStats.bbMiddle, 5.0, 15);
  checkLevel("月", "布林下軌", mStats.bbLower, 5.0, 13);

  const hasLineSupport = matchedLabels.length > 0;

  const dailyBandwidthPct = dStats.bandwidthPct !== null ? Number(dStats.bandwidthPct.toFixed(1)) : 99;
  const isSqueeze = dailyBandwidthPct <= 15.0;
  if (isSqueeze) {
    rawScore += 7;
    matchedLabels.push(`布林壓縮Squeeze(帶寬${dailyBandwidthPct}%)`);
  }

  const n = dailyBars.length;
  const currVol = dailyBars[n - 1].volume;
  const avg5dVol = dailyBars.slice(n - 5).reduce((a, b) => a + b.volume, 0) / 5;
  const volumeRatio5d = avg5dVol > 0 ? Number((currVol / avg5dVol).toFixed(2)) : 1.0;

  if (volumeRatio5d >= 1.5 && dailyBars[n - 1].close > dailyBars[n - 2].close) {
    rawScore += 6;
    matchedLabels.push(`帶量點火(${volumeRatio5d}倍量)`);
  } else if (volumeRatio5d <= 0.85 && hasLineSupport) {
    rawScore += 4;
    matchedLabels.push(`量縮回測支撐(${volumeRatio5d}倍量)`);
  }

  return {
    matched: hasLineSupport,
    matchedLabels,
    supportScore: Math.min(35, rawScore),
    dailyBandwidthPct,
    isSqueeze,
    volumeRatio5d,
    rawSupportDetails: {
      daily: {
        ema12: dStats.ema12 ? Number(dStats.ema12.toFixed(2)) : null,
        bbMiddle: dStats.bbMiddle ? Number(dStats.bbMiddle.toFixed(2)) : null,
        bbLower: dStats.bbLower ? Number(dStats.bbLower.toFixed(2)) : null,
        bbUpper: dStats.bbUpper ? Number(dStats.bbUpper.toFixed(2)) : null,
        bandwidthPct: dailyBandwidthPct,
      },
      weekly: {
        ema12: wStats.ema12 ? Number(wStats.ema12.toFixed(2)) : null,
        bbMiddle: wStats.bbMiddle ? Number(wStats.bbMiddle.toFixed(2)) : null,
        bbLower: wStats.bbLower ? Number(wStats.bbLower.toFixed(2)) : null,
      },
      monthly: {
        ema12: mStats.ema12 ? Number(mStats.ema12.toFixed(2)) : null,
        bbMiddle: mStats.bbMiddle ? Number(mStats.bbMiddle.toFixed(2)) : null,
        bbLower: mStats.bbLower ? Number(mStats.bbLower.toFixed(2)) : null,
      },
    },
  };
}

/**
 * 5. 第二階段：向 Yahoo Finance 抓取 2 年日 K，並結合《籌碼K線》同款 Top 15 主力 5日/10日集中度進行完整評分
 */
async function fetchAndEvaluateYahooStock(
  symbol: string,
  cand: ChipCandidate,
  hiStockToken: string
): Promise<FullStockEvaluation> {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(
    symbol
  )}?range=2y&interval=1d`;

  const res = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" },
  });
  if (!res.ok) return { passed: false } as FullStockEvaluation;

  const json = await res.json();
  const result = json?.chart?.result?.[0];
  const timestamps: number[] = result?.timestamp || [];
  const quote = result?.indicators?.quote?.[0];
  if (!quote || !Array.isArray(quote.close) || timestamps.length === 0) {
    return { passed: false } as FullStockEvaluation;
  }

  const dailyBarsByDate = new Map<string, Bar>();
  for (let i = 0; i < quote.close.length; i++) {
    const c = quote.close[i];
    const o = quote.open[i];
    const h = quote.high[i];
    const l = quote.low[i];
    const v = quote.volume[i];
    if (
      c !== null &&
      c !== undefined &&
      Number(c) > 0 &&
      h !== null &&
      l !== null &&
      v !== null &&
      v !== undefined &&
      Number(v) > 0 &&
      timestamps[i]
    ) {
      const ts = Number(timestamps[i]);
      const dt = new Date((ts + 8 * 3600) * 1000);
      const dayKey = `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, "0")}-${String(
        dt.getUTCDate()
      ).padStart(2, "0")}`;
      dailyBarsByDate.set(dayKey, {
        time: ts,
        open: Number(o ?? c),
        high: Number(h),
        low: Number(l),
        close: Number(c),
        volume: Number(v),
      });
    }
  }
  const dailyBars: Bar[] = Array.from(dailyBarsByDate.values());

  const n = dailyBars.length;
  if (n < 35) return { passed: false } as FullStockEvaluation;

  const closes = dailyBars.map((b) => b.close);
  const highs = dailyBars.map((b) => b.high);
  const lows = dailyBars.map((b) => b.low);
  const volumes = dailyBars.map((b) => b.volume);

  const currClose = closes[n - 1];
  const prevClose = closes[n - 2];
  const priceChangePct =
    prevClose > 0 ? Number((((currClose - prevClose) / prevClose) * 100).toFixed(2)) : 0;
  const currVolLots = Math.round(volumes[n - 1] / 1000);

  // --- (A) 計算台股日 KD (9, 3, 3) (最高 10 分) ---
  let kPrev = 50.0;
  let dPrev = 50.0;
  let kCurr = 50.0;
  let dCurr = 50.0;
  let kBeforeLast = 50.0;
  let dBeforeLast = 50.0;

  const startIdx = Math.max(8, n - 60);
  for (let i = startIdx; i < n; i++) {
    let lowest = Infinity;
    let highest = -Infinity;
    for (let j = i - 8; j <= i; j++) {
      if (lows[j] < lowest) lowest = lows[j];
      if (highs[j] > highest) highest = highs[j];
    }
    const rsv =
      highest > lowest
        ? Math.max(0, Math.min(100, ((closes[i] - lowest) / (highest - lowest)) * 100))
        : 50.0;
    kCurr = (2 / 3) * kPrev + (1 / 3) * rsv;
    dCurr = (2 / 3) * dPrev + (1 / 3) * kCurr;
    if (i === n - 2) {
      kBeforeLast = kCurr;
      dBeforeLast = dCurr;
    }
    kPrev = kCurr;
    dPrev = dCurr;
  }

  const kdDiff = kCurr - dCurr;
  const prevKdDiff = kBeforeLast - dBeforeLast;
  let kdSignal = "";
  let kdScore = 0;
  let kdReason = "";

  if (
    (prevKdDiff <= 0 && kdDiff > 0 && kCurr <= 80) ||
    (kdDiff > 0 && kdDiff <= 4.0 && kCurr > kBeforeLast && kCurr <= 75)
  ) {
    kdSignal = "GOLDEN_CROSS";
    kdScore = kCurr < 55 ? 10 : 8;
    kdReason = `KD金叉(K=${kCurr.toFixed(1)}/D=${dCurr.toFixed(1)})`;
  } else if (
    kdDiff >= -4.5 &&
    kdDiff <= 0 &&
    kCurr > kBeforeLast &&
    kdDiff > prevKdDiff &&
    kCurr <= 75
  ) {
    kdSignal = "NEAR_GOLDEN_CROSS";
    kdScore = 8;
    kdReason = `KD準金叉(K=${kCurr.toFixed(1)}↑逼近D=${dCurr.toFixed(1)})`;
  }

  // --- (B) 計算 Wilder RSI (6, 12) (最高 10 分) ---
  function calcRsi(period: number): [number, number] {
    let avgGain = 0;
    let avgLoss = 0;
    const rsiStart = Math.max(1, n - 80);
    for (let i = rsiStart; i < n; i++) {
      const diff = closes[i] - closes[i - 1];
      const gain = diff > 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;
      if (i - rsiStart < period) {
        avgGain += gain / period;
        avgLoss += loss / period;
      } else {
        avgGain = (avgGain * (period - 1) + gain) / period;
        avgLoss = (avgLoss * (period - 1) + loss) / period;
      }
    }
    const currRsi = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

    let pGain = 0;
    let pLoss = 0;
    for (let i = rsiStart; i < n - 1; i++) {
      const diff = closes[i] - closes[i - 1];
      const gain = diff > 0 ? diff : 0;
      const loss = diff < 0 ? -diff : 0;
      if (i - rsiStart < period) {
        pGain += gain / period;
        pLoss += loss / period;
      } else {
        pGain = (pGain * (period - 1) + gain) / period;
        pLoss = (pLoss * (period - 1) + loss) / period;
      }
    }
    const prevRsi = pLoss === 0 ? 100 : 100 - 100 / (1 + pGain / pLoss);
    return [currRsi, prevRsi];
  }

  const [r6Curr, r6Prev] = calcRsi(6);
  const [r12Curr, r12Prev] = calcRsi(12);
  const rsiDiff = r6Curr - r12Curr;
  const prevRsiDiff = r6Prev - r12Prev;

  let rsiSignal = "";
  let rsiScore = 0;
  let rsiReason = "";

  if (
    (prevRsiDiff <= 0 && rsiDiff > 0 && r6Curr < 78) ||
    (rsiDiff > 0 && rsiDiff <= 3.5 && r6Curr > r6Prev && r6Curr < 75)
  ) {
    rsiSignal = "GOLDEN_CROSS";
    rsiScore = 10;
    rsiReason = `RSI金叉(6日${r6Curr.toFixed(1)}>12日${r12Curr.toFixed(1)})`;
  } else if (
    rsiDiff >= -4.0 &&
    rsiDiff <= 0 &&
    r6Curr > r6Prev &&
    rsiDiff > prevRsiDiff &&
    r6Curr >= 28 &&
    r6Curr <= 70
  ) {
    rsiSignal = "NEAR_GOLDEN_CROSS";
    rsiScore = 8;
    rsiReason = `RSI準金叉(6日${r6Curr.toFixed(1)}↑逼近12日${r12Curr.toFixed(1)})`;
  }

  // --- (C) 計算 MACD (12, 26, 9) (最高 10 分) ---
  const k12 = 2 / 13;
  const k26 = 2 / 27;
  const k9 = 2 / 10;
  const macdStart = Math.max(0, n - 120);
  let ema12 = closes[macdStart];
  let ema26 = closes[macdStart];
  let dea = 0;
  let difCurr = 0;
  let hCurr = 0;
  let hPrev1 = 0;
  let hPrev2 = 0;

  for (let i = macdStart; i < n; i++) {
    ema12 = closes[i] * k12 + ema12 * (1 - k12);
    ema26 = closes[i] * k26 + ema26 * (1 - k26);
    const dif = ema12 - ema26;
    dea = i === macdStart ? dif : dif * k9 + dea * (1 - k9);
    const h = dif - dea;
    if (i === n - 3) hPrev2 = h;
    if (i === n - 2) hPrev1 = h;
    if (i === n - 1) {
      difCurr = dif;
      hCurr = h;
    }
  }

  let macdSignal = "";
  let macdScore = 0;
  let macdReason = "";

  if (hPrev1 <= 0 && hCurr > 0) {
    macdSignal = "HIST_TURN_POSITIVE";
    macdScore = 10;
    macdReason = `MACD翻紅(OSC=${hCurr.toFixed(2)})`;
  } else if (hCurr > 0 && hCurr > hPrev1) {
    macdSignal = "BULLISH_EXPANDING";
    macdScore = 9;
    macdReason = `MACD紅柱放大(OSC=${hCurr.toFixed(2)}↑)`;
  } else if (hCurr < 0 && hCurr > hPrev1 && hPrev1 >= hPrev2) {
    macdSignal = "BEARISH_CONVERGING";
    macdScore = 8;
    macdReason = `MACD綠柱收斂(OSC=${hCurr.toFixed(2)}↑)`;
  }

  // --- (D) 多週期 (日/週/月) 12 EMA 與 布林(21, 2.1) 支撐檢查 ---
  const supportRes = evaluateMultiTimeframeSupport(dailyBars);

  const activeTechCount =
    (kdSignal ? 1 : 0) + (rsiSignal ? 1 : 0) + (macdSignal ? 1 : 0);

  // 先檢查技術與支撐軌道是否初步吻合，才呼叫 HiStock 取得精確 Top 15 主力籌碼集中度 (節省請求)
  if (!(activeTechCount === 3 || (supportRes.matched && activeTechCount >= 2))) {
    return { passed: false } as FullStockEvaluation;
  }

  // --- (E) 查詢《籌碼K線》同款 Top 15 主力 5日/10日籌碼集中張數與集中度 (%) ---
  const brokerConc = await fetchTop15BrokerConcentration(cand.stock.stockId, hiStockToken);

  const sum5dVolLots = Math.max(
    1,
    Math.round(volumes.slice(n - 5).reduce((a, b) => a + b, 0) / 1000)
  );
  const sum10dVolLots = Math.max(
    1,
    Math.round(volumes.slice(n - 10).reduce((a, b) => a + b, 0) / 1000)
  );

  const inst = cand.inst;
  const chip5dLots = brokerConc ? brokerConc.chip5dLots : inst.foreign5dLots + inst.trust5dLots;
  const chip10dLots = brokerConc ? brokerConc.chip10dLots : inst.foreign10dLots + inst.trust10dLots;
  const conc5dPct = brokerConc
    ? brokerConc.conc5dPct
    : Number(((chip5dLots / sum5dVolLots) * 100).toFixed(2));
  const conc10dPct = brokerConc
    ? brokerConc.conc10dPct
    : Number(((chip10dLots / sum10dVolLots) * 100).toFixed(2));

  // 若主力 5日集中度與 10日集中度皆為負數 (主力站在賣方出貨)，且投信也沒買，則刷掉不推薦！
  if (conc5dPct < 0 && conc10dPct < 0 && inst.trust5dLots <= 0) {
    return { passed: false } as FullStockEvaluation;
  }

  // 計算正式籌碼分數 (滿分 35 分)
  let rawChipScore = 0;
  const chipReasons: string[] = [];

  // 1) 主力 5日集中度 & 10日集中度 (最高 13 分)
  if (conc5dPct >= 8.0 && conc10dPct >= 8.0) {
    rawChipScore += 13;
    chipReasons.push(`主力5日集中${conc5dPct}%/10日${conc10dPct}%(主力大買)`);
  } else if (conc5dPct > conc10dPct && conc10dPct > 0 && conc5dPct >= 5.0) {
    rawChipScore += 11;
    chipReasons.push(`主力5日集中加速(${conc10dPct}%→${conc5dPct}%)`);
  } else if (conc5dPct >= 5.0 || conc10dPct >= 8.0) {
    rawChipScore += 9;
    chipReasons.push(`主力集中(5日${conc5dPct}%/10日${conc10dPct}%)`);
  } else if (conc5dPct > 0 || conc10dPct > 0) {
    rawChipScore += 5;
  }

  // 2) 投信 5日買超與連買 (最高 9 分)
  if (inst.trustStreak >= 3) {
    rawChipScore += 9;
    chipReasons.push(`投信連買${inst.trustStreak}日(5日+${inst.trust5dLots}張)`);
  } else if (inst.trust5dLots >= 50) {
    rawChipScore += 8;
    chipReasons.push(`投信5日+${inst.trust5dLots}張`);
  } else if (inst.trust5dLots > 0) {
    rawChipScore += 5;
    chipReasons.push(`投信5日+${inst.trust5dLots}張`);
  }

  // 3) 外資 5日買超與連買 (最高 7 分)
  if (inst.foreignStreak >= 3) {
    rawChipScore += 7;
    chipReasons.push(`外資連買${inst.foreignStreak}日(5日+${inst.foreign5dLots}張)`);
  } else if (inst.foreign5dLots >= 300) {
    rawChipScore += 6;
    chipReasons.push(`外資5日+${inst.foreign5dLots}張`);
  } else if (inst.foreign5dLots > 0) {
    rawChipScore += 4;
  }

  // 4) TDCC 400張大戶 (最高 6 分)
  if (cand.major400Pct >= 65.0 && cand.retail100Pct <= 25.0) {
    rawChipScore += 6;
    chipReasons.push(`400張大戶鎖碼(${cand.major400Pct}%)`);
  } else if (cand.major400Pct >= 50.0) {
    rawChipScore += 4;
    chipReasons.push(`400張大戶(${cand.major400Pct}%)`);
  }

  const chipScore = Math.min(35, rawChipScore);

  let trackType = "";
  if (activeTechCount === 3 && supportRes.matched) {
    trackType = "🔥支撐起漲共振";
  } else if (activeTechCount === 3) {
    trackType = "🚀動能突破型";
  } else if (supportRes.matched && activeTechCount >= 2 && chipScore >= 15) {
    trackType = "💎隱藏潛伏回測";
  } else {
    return { passed: false } as FullStockEvaluation;
  }

  const techScore = Math.min(30, kdScore + rsiScore + macdScore);
  const totalScore = Math.min(100, Math.round(chipScore + supportRes.supportScore + techScore));
  const techReasons = [kdReason, rsiReason, macdReason].filter(Boolean);
  const allReasons = [
    `【${trackType}】`,
    ...supportRes.matchedLabels,
    ...techReasons,
    ...chipReasons,
  ];

  return {
    passed: true,
    trackType,
    closePrice: Number(currClose.toFixed(2)),
    priceChangePct,
    volumeLots: currVolLots,
    kVal: Number(kCurr.toFixed(2)),
    dVal: Number(dCurr.toFixed(2)),
    kdSignal: kdSignal || "HOLD",
    rsi6: Number(r6Curr.toFixed(2)),
    rsi12: Number(r12Curr.toFixed(2)),
    rsiSignal: rsiSignal || "HOLD",
    macdDif: Number(difCurr.toFixed(4)),
    macdDea: Number(dea.toFixed(4)),
    macdHist: Number(hCurr.toFixed(4)),
    macdPrevHist: Number(hPrev1.toFixed(4)),
    macdSignal: macdSignal || "HOLD",
    chip5dLots,
    conc5dPct,
    chip10dLots,
    conc10dPct,
    chipScore,
    supportScore: supportRes.supportScore,
    techScore,
    totalScore,
    reasons: allReasons,
    rawMetrics: {
      trackType,
      chip5dLots,
      conc5dPct,
      vol5dLots: brokerConc?.vol5dLots ?? sum5dVolLots,
      chip10dLots,
      conc10dPct,
      vol10dLots: brokerConc?.vol10dLots ?? sum10dVolLots,
      concSource: brokerConc?.source ?? "institutional_fallback",
      foreign5dLots: inst.foreign5dLots,
      trust5dLots: inst.trust5dLots,
      foreign10dLots: inst.foreign10dLots,
      trust10dLots: inst.trust10dLots,
      foreignStreak: inst.foreignStreak,
      trustStreak: inst.trustStreak,
      retail100Pct: cand.retail100Pct,
      supportMatched: supportRes.matchedLabels,
      supportLevels: supportRes.rawSupportDetails,
      scoreBreakdown: {
        chipScore,
        supportScore: supportRes.supportScore,
        techScore,
      },
    },
  };
}

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }
  const t0 = Date.now();
  try {
    const supabaseUrl = cleanEnv(Deno.env.get("SUPABASE_URL"));
    const supabaseServiceKey = cleanEnv(Deno.env.get("SUPABASE_SERVICE_ROLE_KEY"));
    const discordWebhookUrl =
      cleanEnv(Deno.env.get("STOCK_DISCORD_WEBHOOK_URL")) ||
      cleanEnv(Deno.env.get("DISCORD_WEBHOOK_URL"));

    if (!supabaseUrl || !supabaseServiceKey) {
      throw new Error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY");
    }

    const url = new URL(req.url);
    const sessionParam = url.searchParams.get("session") || "auto";

    const taipeiDate = getTaipeiDateStr();
    const taipeiHour = getTaipeiHour();
    const sessionType =
      sessionParam === "morning_0800" || sessionParam === "intraday_1300" || sessionParam === "manual"
        ? sessionParam
        : taipeiHour < 11
        ? "morning_0800"
        : "intraday_1300";

    // 1. 平行下載全市場普通股清單 + 近 10 個交易日三大法人買賣超 + TDCC 集保大戶與散戶比例 + HiStock 主力籌碼 Token
    const [allStocks, inst10dMap, tdccMap, hiStockToken] = await Promise.all([
      fetchAllTaiwanOrdinaryStocks(),
      fetch10DayInstitutionalChips(),
      fetchTdccHoldings(),
      fetchHiStockPageToken(),
    ]);

    const totalMarketStocks = allStocks.length;

    // 2. 第一階段：對全市場進行 5日/10日法人與大戶籌碼初篩
    const chipCandidates = filterCandidatesByMultiDayChips(allStocks, inst10dMap, tdccMap, 300);

    // 3. 第二階段：並行抓取 Yahoo Finance 2 年日 K +《籌碼K線》同款 Top 15 主力 5日/10日集中度
    const concurrency = 25;
    const recommendations: Array<Record<string, any>> = [];

    for (let i = 0; i < chipCandidates.length; i += concurrency) {
      const batch = chipCandidates.slice(i, i + concurrency);
      const batchResults = await Promise.allSettled(
        batch.map(async (cand) => {
          const evalRes = await fetchAndEvaluateYahooStock(
            cand.stock.yahooSymbol,
            cand,
            hiStockToken
          );
          return { cand, evalRes };
        })
      );

      for (const item of batchResults) {
        if (item.status !== "fulfilled" || !item.value.evalRes.passed) continue;
        const { cand, evalRes } = item.value;
        const st = cand.stock;

        recommendations.push({
          run_date: taipeiDate,
          session_type: sessionType,
          stock_id: st.stockId,
          stock_name: st.stockName,
          market: st.market,
          close_price: evalRes.closePrice,
          price_change_pct: evalRes.priceChangePct,
          volume_lots: evalRes.volumeLots,
          k_value: evalRes.kVal,
          d_value: evalRes.dVal,
          kd_signal: evalRes.kdSignal,
          rsi_6: evalRes.rsi6,
          rsi_12: evalRes.rsi12,
          rsi_signal: evalRes.rsiSignal,
          macd_dif: evalRes.macdDif,
          macd_dea: evalRes.macdDea,
          macd_hist: evalRes.macdHist,
          macd_prev_hist: evalRes.macdPrevHist,
          macd_signal: evalRes.macdSignal,
          foreign_net_lots: cand.inst.foreign5dLots,
          trust_net_lots: cand.inst.trust5dLots,
          inst_concentration_pct: evalRes.conc5dPct,
          major_holder_400_pct: cand.major400Pct,
          major_holder_change_pct: 0,
          total_score: evalRes.totalScore,
          reasons: evalRes.reasons,
          raw_metrics: evalRes.rawMetrics,
        });
      }
    }

    recommendations.sort((a, b) => b.total_score - a.total_score);
    const topRecs = recommendations.slice(0, 10);
    const elapsedSec = Number(((Date.now() - t0) / 1000).toFixed(2));

    // 4. 寫入 Supabase 資料表 (先移除同日同場次的舊批次，避免殘留孤兒紀錄)
    const supabase = createClient(supabaseUrl, supabaseServiceKey);
    let runId: string | null = null;

    await supabase
      .from("stock_recommendations")
      .delete()
      .eq("run_date", taipeiDate)
      .eq("session_type", sessionType);

    await supabase
      .from("screening_runs")
      .delete()
      .eq("run_date", taipeiDate)
      .eq("session_type", sessionType);

    const { data: runData, error: runErr } = await supabase
      .from("screening_runs")
      .insert({
        run_date: taipeiDate,
        session_type: sessionType,
        total_scanned: totalMarketStocks,
        total_matched: topRecs.length,
        execution_time_sec: elapsedSec,
        status: "completed",
      })
      .select("id")
      .maybeSingle();

    if (runErr) {
      console.warn("Supabase screening_runs insert warning:", runErr.message);
    } else if (runData) {
      runId = runData.id;
    }

    if (topRecs.length > 0) {
      const rowsWithRunId = topRecs.map((r) => ({ ...r, run_id: runId }));
      const { error: recErr } = await supabase
        .from("stock_recommendations")
        .upsert(rowsWithRunId, { onConflict: "run_date,session_type,stock_id" });
      if (recErr) {
        console.warn("Supabase stock_recommendations upsert warning:", recErr.message);
      }
    }

    // 5. 發送 Discord Webhook 通知 (精簡版：Top 10，每檔卡片精簡扼要，單則訊息直接送達)
    if (discordWebhookUrl) {
      const sessionTitle =
        sessionType === "morning_0800"
          ? "08:00 盤前"
          : sessionType === "intraday_1300"
          ? "13:00 盤中"
          : "即時掃描";

      const embeds = topRecs.slice(0, 10).map((r, idx) => {
        const rm = r.raw_metrics || {};
        const sb = rm.scoreBreakdown || {};
        const supportList: string[] = rm.supportMatched || [];
        const trackBadge = rm.trackType || "";
        const conc5d = rm.conc5dPct ?? r.inst_concentration_pct;
        const conc10d = rm.conc10dPct ?? 0;
        const f5d = rm.foreign5dLots ?? r.foreign_net_lots;
        const t5d = rm.trust5dLots ?? r.trust_net_lots;
        const fStreak = rm.foreignStreak || 0;
        const tStreak = rm.trustStreak || 0;

        const fStreakStr = fStreak > 0 ? `(連${fStreak})` : "";
        const tStreakStr = tStreak > 0 ? `(連${tStreak})` : "";
        const supportStr = supportList.length > 0 ? supportList.join("、") : "動能突破";

        return {
          title: `#${idx + 1} ${r.stock_id} ${r.stock_name} — ${r.total_score}分 (${trackBadge})`,
          url: `https://tw.stock.yahoo.com/quote/${r.stock_id}.${r.market === "TWSE" ? "TW" : "TWO"}`,
          color:
            trackBadge === "🔥支撐起漲共振"
              ? 0xe74c3c
              : trackBadge === "💎隱藏潛伏回測"
              ? 0xf1c40f
              : 0x3498db,
          description:
            `💰 **${r.close_price}** (${r.price_change_pct >= 0 ? "+" : ""}${r.price_change_pct}%) ｜ 量 **${r.volume_lots.toLocaleString()}** 張\n` +
            `🎯 **支撐**：${supportStr}\n` +
            `📈 **指標**：K \`${r.k_value.toFixed(1)}\` D \`${r.d_value.toFixed(1)}\` ｜ RSI \`${r.rsi_6.toFixed(1)}/${r.rsi_12.toFixed(1)}\` ｜ MACD \`${r.macd_hist.toFixed(2)}\`\n` +
            `🏦 **集中度**：5日 \`${conc5d > 0 ? "+" : ""}${conc5d}%\` / 10日 \`${conc10d > 0 ? "+" : ""}${conc10d}%\` ｜ 400張大戶 \`${r.major_holder_400_pct}%\`\n` +
            `🤝 **法人5日**：外資 \`${f5d > 0 ? "+" : ""}${f5d}張\`${fStreakStr} ｜ 投信 \`${t5d > 0 ? "+" : ""}${t5d}張\`${tStreakStr}\n` +
            `🏆 **配分**：籌碼 \`${sb.chipScore || 0}\` + 支撐 \`${sb.supportScore || 0}\` + 動能 \`${sb.techScore || 0}\``,
        };
      });

      const headerMsg = `📊 **【台股選股 Top ${topRecs.length}】** \`${taipeiDate}\` (${sessionTitle})`;

      const dRes = await fetch(discordWebhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: "StockNotify 選股雷達",
          content: embeds.length > 0 ? headerMsg : headerMsg + " — 今日無符合條件標的",
          embeds,
        }),
      });
      if (!dRes.ok) {
        console.warn("Discord webhook error:", dRes.status, await dRes.text());
      }
    }

    return new Response(
      JSON.stringify({
        status: "ok",
        run_date: taipeiDate,
        session_type: sessionType,
        total_market_stocks: totalMarketStocks,
        chip_candidates_count: chipCandidates.length,
        total_matched: topRecs.length,
        execution_time_sec: elapsedSec,
        recommendations: topRecs,
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 200 }
    );
  } catch (err) {
    console.error("[StockNotify Edge Error]", err);
    return new Response(
      JSON.stringify({
        status: "error",
        message: err instanceof Error ? err.message : String(err),
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" }, status: 500 }
    );
  }
});
