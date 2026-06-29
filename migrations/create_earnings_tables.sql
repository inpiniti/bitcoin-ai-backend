-- ============================================================
-- 실적 발표 기반 자동매매 — 데이터 테이블
--
-- Supabase SQL Editor에서 실행하세요.
-- 설계 문서: trends/실적발표_자동매매_시퀀스.md (§8.1, §12.2)
--            trends/실적발표_자동매매_DB.md
--
-- 구성:
--   1) earnings_events       이벤트 1행(발표 1건) — 피처 + 타깃
--   2) earnings_predictions  이벤트별 예측 결과
--   3) earnings_positions    현재 보유 포지션
--   4) earnings_dashboard    대시보드용 VIEW (시작가/예측가/경과%)
--   5) earnings_api_logs     실적발표 API 로깅 테이블
--   * 모델 아티팩트는 기존 ml_models 테이블을 재사용한다 (신규 생성 안 함).
-- ============================================================


-- ============================================================
-- 1. earnings_events — 실적 발표 1건 = 1행 (학습/예측 기본 단위)
-- ============================================================
CREATE TABLE IF NOT EXISTS earnings_events (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,

  -- A. 이벤트 키 & 가격
  ticker             VARCHAR(10) NOT NULL,
  gics_sector        VARCHAR(50),                       -- GICS 11섹터 (섹터별 학습 키)
  earnings_date      DATE NOT NULL,                     -- 이번 발표일
  timing             VARCHAR(3) CHECK (timing IN ('bmo','amc')),  -- 장전/장후
  px_pre             DECIMAL(12,2),                     -- 발표 직전 종가 (시작가, T-1)
  px_post            DECIMAL(12,2),                     -- 발표 직후 종가 (T+1)
  next_earnings_date DATE,                              -- 다음 발표일
  px_next_pre        DECIMAL(12,2),                     -- 다음 발표 직전 종가

  -- B. 컨센서스 & 서프라이즈 (★ 핵심 피처)
  eps_est            DECIMAL(12,4),
  eps_act            DECIMAL(12,4),
  eps_surprise_pct   DECIMAL(10,4),                     -- (실제-컨센서스)/|컨센서스|
  rev_est            DECIMAL(18,2),
  rev_act            DECIMAL(18,2),
  rev_surprise_pct   DECIMAL(10,4),
  guidance_change    VARCHAR(10) CHECK (guidance_change IN ('up','hold','down')),

  -- C. 재무 피처 — 거장 기반 (버핏/그린블라트/다모다란/린치)
  gross_margin       DECIMAL(8,4),                      -- 매출총이익률 [버핏 40%↑]
  net_margin         DECIMAL(8,4),                      -- 순이익률 [버핏 20%↑]
  sga_to_gross       DECIMAL(8,4),                      -- 판관비/매출총이익 [버핏 30%↓]
  roe                DECIMAL(8,4),                      -- ROE [버핏/다모다란 15%↑]
  roc                DECIMAL(8,4),                      -- EBIT/투하자본 [그린블라트]
  earnings_yield     DECIMAL(8,4),                      -- EBIT/EV [그린블라트]
  fcf                DECIMAL(18,2),                     -- 잉여현금흐름 [다모다란/린치]
  capex_to_ni        DECIMAL(8,4),                      -- CapEx/순이익 [버핏 25%↓]
  debt_to_ni         DECIMAL(8,4),                      -- 장기부채/순이익 [버핏 3~4배↓]
  retained_earnings  DECIMAL(18,2),                     -- 이익잉여금 [버핏 증가추세]
  cash_sti           DECIMAL(18,2),                     -- 현금+단기투자 [버핏]
  inventory_vs_sales DECIMAL(8,4),                      -- 재고증가율-매출증가율 [버핏/린치]
  eps_yoy            DECIMAL(10,4),                     -- EPS 변화율(YoY) [버핏]
  per                DECIMAL(10,2),                     -- [다모다란]
  pbr                DECIMAL(10,2),                     -- [다모다란]
  ev_ebitda          DECIMAL(10,2),                     -- [다모다란]
  peg                DECIMAL(8,2),                      -- PER/이익성장률 [린치]
  dividend_yield     DECIMAL(6,4),                      -- 배당수익률 [§6]
  ex_dividend_date   DATE,                              -- 배당락일 [§6]
  features           JSONB,                             -- 확장/원천 피처(자유 스키마)

  -- D. 매크로 스냅샷 (발표일 기준)
  fed_funds          DECIMAL(6,3),                      -- 기준금리
  ust10y             DECIMAL(6,3),                      -- 10년물 국채금리
  cpi_yoy            DECIMAL(6,3),                      -- CPI 전년比

  -- E. 타깃 (변화율)
  ret_event          DECIMAL(10,4),                     -- (px_post-px_pre)/px_pre, 발표 직후 반응
  ret_hold           DECIMAL(10,4),                     -- (px_next_pre-px_post)/px_post, 다음 발표까지
                                                        --   NULL = 라벨 미완성(학습 제외, 예측만)

  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(ticker, earnings_date)
);

CREATE INDEX IF NOT EXISTS idx_earnings_events_date    ON earnings_events(earnings_date DESC);
CREATE INDEX IF NOT EXISTS idx_earnings_events_ticker  ON earnings_events(ticker);
CREATE INDEX IF NOT EXISTS idx_earnings_events_sector  ON earnings_events(gics_sector);
-- 학습 대상(라벨 완성) 행만 빠르게 조회
CREATE INDEX IF NOT EXISTS idx_earnings_events_labeled ON earnings_events(gics_sector)
  WHERE ret_hold IS NOT NULL;


-- ============================================================
-- 2. earnings_predictions — 이벤트별 예측 결과 (종료값 없는 행에 예측 주입)
-- ============================================================
CREATE TABLE IF NOT EXISTS earnings_predictions (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  event_id        UUID REFERENCES earnings_events(id) ON DELETE CASCADE,
  ticker          VARCHAR(10) NOT NULL,                 -- 비정규화(조회 편의)
  target_price    DECIMAL(12,2),                        -- 예측가
  ret_event_pred  DECIMAL(10,4),                        -- 발표 직후 반응 예측
  ret_hold_pred   DECIMAL(10,4),                        -- 보유기간 수익률 예측
  model_id        TEXT,                                 -- ml_models 참조(텍스트, xgb_model_id 패턴)
  model_version   VARCHAR(50),
  predicted_at    TIMESTAMPTZ DEFAULT NOW(),

  UNIQUE(event_id, model_version)
);

CREATE INDEX IF NOT EXISTS idx_earnings_pred_event  ON earnings_predictions(event_id, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_earnings_pred_ticker ON earnings_predictions(ticker);


-- ============================================================
-- 3. earnings_positions — 현재 보유 포지션 (리밸런싱 기준)
-- ============================================================
CREATE TABLE IF NOT EXISTS earnings_positions (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  ticker          VARCHAR(10) NOT NULL,
  qty             INTEGER NOT NULL DEFAULT 0,
  avg_price       DECIMAL(12,2),                        -- 시작가(평단)
  opened_at       TIMESTAMPTZ DEFAULT NOW(),
  source_event_id UUID REFERENCES earnings_events(id) ON DELETE SET NULL,
  status          VARCHAR(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_earnings_positions_ticker ON earnings_positions(ticker);
CREATE INDEX IF NOT EXISTS idx_earnings_positions_open   ON earnings_positions(status)
  WHERE status = 'open';


-- ============================================================
-- 4. earnings_dashboard — 대시보드용 VIEW
--    시작가/예측가/종료값/라벨상태/기간경과%를 한 번에.
--    (현재가·가격위치%는 실시간 시세가 필요 → API 단계에서 계산)
-- ============================================================
CREATE OR REPLACE VIEW earnings_dashboard AS
SELECT
  e.id                AS event_id,
  e.ticker,
  e.gics_sector,
  e.earnings_date,
  e.next_earnings_date,
  e.px_pre            AS start_price,     -- 시작가 (발표 직전 종가)
  pr.target_price     AS predict_price,   -- 예측가 (최신 예측)
  e.ret_hold,                             -- 종료값
  CASE WHEN e.ret_hold IS NULL THEN 'pending' ELSE 'labeled' END AS label_status,
  ROUND(
    (CURRENT_DATE - e.earnings_date)::numeric
      / NULLIF((e.next_earnings_date - e.earnings_date), 0) * 100,
    1
  ) AS elapsed_pct                        -- 기간 경과%
FROM earnings_events e
LEFT JOIN LATERAL (
  SELECT target_price
  FROM earnings_predictions
  WHERE event_id = e.id
  ORDER BY predicted_at DESC
  LIMIT 1
) pr ON TRUE;


-- ============================================================
-- 5. earnings_api_logs — API 통신 이력 로깅 테이블
-- ============================================================
CREATE TABLE IF NOT EXISTS earnings_api_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    api VARCHAR(255) NOT NULL,
    payload JSONB,
    response JSONB,
    inout VARCHAR(5) NOT NULL CHECK (inout IN ('in', 'out')), -- in (외부 요청 들어옴), out (서버가 외부 호출함)
    status VARCHAR(10) NOT NULL, -- success, error, empty (빈배열 리턴)
    error_message TEXT -- 에러/빈배열 사유 기록
);

CREATE INDEX IF NOT EXISTS idx_earnings_api_logs_created ON earnings_api_logs(created_at DESC);


-- ============================================================
-- RLS — sp500_daily_impact와 동일 정책 (ENABLE + anon 전체 허용)
-- ============================================================
ALTER TABLE earnings_events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE earnings_predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE earnings_positions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE earnings_api_logs    ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all for anon" ON earnings_events
  FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for anon" ON earnings_predictions
  FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for anon" ON earnings_positions
  FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all for anon" ON earnings_api_logs
  FOR ALL USING (true) WITH CHECK (true);
