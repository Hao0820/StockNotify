-- ============================================================================
-- StockNotify Supabase Schema + pg_cron 自動排程 (與現有 GoldNotify 共存不衝突)
-- 儲存週一至週五 08:00 (盤前) 與 13:00 (盤中) 選股結果與執行紀錄
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;

-- 1. 執行批次紀錄表 (每次排程執行產生一筆紀錄)
CREATE TABLE IF NOT EXISTS public.screening_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_date DATE NOT NULL,
    session_type VARCHAR(20) NOT NULL CHECK (session_type IN ('morning_0800', 'intraday_1300', 'manual')),
    total_scanned INT NOT NULL DEFAULT 0,
    total_matched INT NOT NULL DEFAULT 0,
    execution_time_sec NUMERIC(8, 2),
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'Asia/Taipei')
);

CREATE INDEX IF NOT EXISTS idx_screening_runs_date_session 
    ON public.screening_runs (run_date DESC, session_type);

-- 2. 股票推薦明細表 (儲存符合技術面 + 籌碼面條件之個股指標)
CREATE TABLE IF NOT EXISTS public.stock_recommendations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id UUID REFERENCES public.screening_runs(id) ON DELETE CASCADE,
    run_date DATE NOT NULL,
    session_type VARCHAR(20) NOT NULL,
    stock_id VARCHAR(16) NOT NULL,
    stock_name VARCHAR(64) NOT NULL,
    market VARCHAR(16) NOT NULL, -- 'TWSE' (上市) 或 'TPEX' (上櫃)
    
    -- 價格與成交量
    close_price NUMERIC(10, 2) NOT NULL,
    price_change_pct NUMERIC(6, 2),
    volume_lots INT, -- 成交張數 (1張=1000股)
    
    -- 技術指標：KD (9, 3, 3)
    k_value NUMERIC(6, 2),
    d_value NUMERIC(6, 2),
    kd_signal VARCHAR(32), -- 'GOLDEN_CROSS' (剛黃金交叉) 或 'NEAR_GOLDEN_CROSS' (黃金交叉附近)
    
    -- 技術指標：RSI (6, 12)
    rsi_6 NUMERIC(6, 2),
    rsi_12 NUMERIC(6, 2),
    rsi_signal VARCHAR(32), -- 'GOLDEN_CROSS' 或 'NEAR_GOLDEN_CROSS'
    
    -- 技術指標：MACD (12, 26, 9)
    macd_dif NUMERIC(8, 4),
    macd_dea NUMERIC(8, 4),
    macd_hist NUMERIC(8, 4),
    macd_prev_hist NUMERIC(8, 4),
    macd_signal VARCHAR(32), -- 'HIST_TURN_POSITIVE' / 'BULLISH_EXPANDING' / 'BEARISH_CONVERGING'
    
    -- 籌碼指標：外資、投信、大戶集中度
    foreign_net_lots INT DEFAULT 0,          -- 外資買賣超(張)
    trust_net_lots INT DEFAULT 0,            -- 投信買賣超(張)
    inst_concentration_pct NUMERIC(6, 2),    -- 法人買超佔成交量集中度(%)
    major_holder_400_pct NUMERIC(6, 2),      -- 400張以上大戶持股比例(%)
    major_holder_change_pct NUMERIC(6, 2),   -- 400張以上大戶持股比例增減(%)
    
    -- 綜合評分與觸發理由
    total_score NUMERIC(6, 2) NOT NULL DEFAULT 0,
    reasons TEXT[] NOT NULL DEFAULT '{}',
    raw_metrics JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'Asia/Taipei'),

    UNIQUE (run_date, session_type, stock_id)
);

CREATE INDEX IF NOT EXISTS idx_stock_recommendations_date_score 
    ON public.stock_recommendations (run_date DESC, session_type, total_score DESC);

CREATE INDEX IF NOT EXISTS idx_stock_recommendations_stock_id 
    ON public.stock_recommendations (stock_id, run_date DESC);

-- 3. 建立最新推薦檢視表 (方便直接查詢最新一批推薦名單)
CREATE OR REPLACE VIEW public.v_latest_recommendations AS
SELECT r.*
FROM public.stock_recommendations r
INNER JOIN (
    SELECT id
    FROM public.screening_runs
    WHERE status = 'completed'
    ORDER BY created_at DESC
    LIMIT 1
) latest_run ON r.run_id = latest_run.id
ORDER BY r.total_score DESC;

-- 啟用 RLS (Row Level Security)
ALTER TABLE public.screening_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.stock_recommendations ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'screening_runs' AND policyname = 'Allow public read on screening_runs'
    ) THEN
        CREATE POLICY "Allow public read on screening_runs"
            ON public.screening_runs FOR SELECT
            USING (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'stock_recommendations' AND policyname = 'Allow public read on stock_recommendations'
    ) THEN
        CREATE POLICY "Allow public read on stock_recommendations"
            ON public.stock_recommendations FOR SELECT
            USING (true);
    END IF;
END $$;

-- ============================================================================
-- 4. 建立 Supabase 資料庫端排程觸發函式 (供 pg_cron 於週一至週五 08:00 與 13:00 呼叫)
--    已自動帶入您的專案 https://mmvwmhopreouirgvlynm.supabase.co
-- ============================================================================
CREATE OR REPLACE FUNCTION public.invoke_stock_screener(p_session TEXT DEFAULT 'auto')
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_project_url TEXT := 'https://mmvwmhopreouirgvlynm.supabase.co';
  v_anon_key TEXT := 'sb_publishable_tqhgtF9dUYLnDcd02v3PAw_8Td_NpO9';
BEGIN
  PERFORM net.http_post(
    url := v_project_url || '/functions/v1/stock-screener?session=' || p_session,
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'apikey', v_anon_key,
      'Authorization', 'Bearer ' || v_anon_key
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 120000
  );
END;
$$;

-- 5. 設定 pg_cron 台灣時間 週一至週五 早上 08:00 (UTC 00:00)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'stock-notify-morning-0800') THEN
    PERFORM cron.unschedule('stock-notify-morning-0800');
  END IF;
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'stock-notify-intraday-1300') THEN
    PERFORM cron.unschedule('stock-notify-intraday-1300');
  END IF;
END
$$;

-- 台灣時間週一至週五 08:00 -> UTC 00:00 (1-5)
SELECT cron.schedule(
  'stock-notify-morning-0800',
  '0 0 * * 1-5',
  $$SELECT public.invoke_stock_screener('morning_0800');$$
);
