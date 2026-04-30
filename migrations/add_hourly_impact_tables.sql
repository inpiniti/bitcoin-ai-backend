-- ============================================================
-- S&P 500 시간대별 영향도 분석 테이블
--
-- 기존 일별(DATE) 구조에서 시간별(TIMESTAMPTZ) 구조로 확장
-- Supabase SQL Editor에서 실행하세요.
-- ============================================================

-- 1. 종목별 시간대별 영향도 (새 테이블)
CREATE TABLE IF NOT EXISTS sp500_impact_hourly (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  analysis_datetime TIMESTAMPTZ NOT NULL,  -- 시간까지 저장 (예: 2026-04-30 09:00:00+00:00)
  ticker VARCHAR(10) NOT NULL,
  name VARCHAR(100),
  sector VARCHAR(50),
  direction VARCHAR(10) NOT NULL CHECK (direction IN ('bullish', 'bearish', 'neutral')),
  confidence DECIMAL(3,2) CHECK (confidence >= 0 AND confidence <= 1),
  reason TEXT,
  news_count INTEGER,

  -- 모델 예측 신호 컬럼
  xgb_prob DECIMAL(4,3),
  xgb_model_id TEXT,
  rl_signal VARCHAR(4),
  rl_model_id TEXT,
  timesfm_signal VARCHAR(4),
  chronos_signal VARCHAR(4),
  moirai_signal VARCHAR(4),

  -- 소문 분석 신호 컬럼
  rumors_signal VARCHAR(4),
  rumors_confidence DECIMAL(3,2),
  rumors_post_count INTEGER DEFAULT 0,
  rumors_reason TEXT,

  created_at TIMESTAMPTZ DEFAULT NOW(),

  -- 같은 날짜의 같은 종목은 여러 시간에 저장 가능,
  -- 하지만 같은 시간에 같은 종목의 중복은 방지
  UNIQUE(analysis_datetime, ticker)
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_sp500_impact_hourly_datetime ON sp500_impact_hourly(analysis_datetime DESC);
CREATE INDEX IF NOT EXISTS idx_sp500_impact_hourly_ticker ON sp500_impact_hourly(ticker);
CREATE INDEX IF NOT EXISTS idx_sp500_impact_hourly_sector ON sp500_impact_hourly(sector);
CREATE INDEX IF NOT EXISTS idx_sp500_impact_hourly_direction ON sp500_impact_hourly(direction);

-- 2. 시간별 분석 메타 (새 테이블)
CREATE TABLE IF NOT EXISTS sp500_hourly_analysis_meta (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  analysis_datetime TIMESTAMPTZ NOT NULL UNIQUE,
  news_count INTEGER,
  news_sources JSONB,
  bullish_count INTEGER DEFAULT 0,
  bearish_count INTEGER DEFAULT 0,
  neutral_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sp500_hourly_meta_datetime ON sp500_hourly_analysis_meta(analysis_datetime DESC);

-- RLS (Row Level Security) 활성화
ALTER TABLE sp500_impact_hourly ENABLE ROW LEVEL SECURITY;
ALTER TABLE sp500_hourly_analysis_meta ENABLE ROW LEVEL SECURITY;

-- anon 키(또는 모든 환경)에서 읽기/쓰기/수정을 허용하는 정책 생성
CREATE POLICY "Allow all for anon" ON sp500_impact_hourly
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for anon" ON sp500_hourly_analysis_meta
  FOR ALL USING (true) WITH CHECK (true);
