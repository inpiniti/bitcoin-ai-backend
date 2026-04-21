-- ============================================================
-- S&P 500 일별 영향도 분석 테이블
-- 
-- Supabase SQL Editor에서 실행하세요.
-- ============================================================

-- 1. 종목별 일별 영향도
CREATE TABLE IF NOT EXISTS sp500_daily_impact (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  analysis_date DATE NOT NULL,
  ticker VARCHAR(10) NOT NULL,
  name VARCHAR(100),
  sector VARCHAR(50),
  direction VARCHAR(10) NOT NULL CHECK (direction IN ('bullish', 'bearish', 'neutral')),
  confidence DECIMAL(3,2) CHECK (confidence >= 0 AND confidence <= 1),
  reason TEXT,
  news_count INTEGER,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  
  UNIQUE(analysis_date, ticker)
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_sp500_impact_date ON sp500_daily_impact(analysis_date);
CREATE INDEX IF NOT EXISTS idx_sp500_impact_sector ON sp500_daily_impact(sector);
CREATE INDEX IF NOT EXISTS idx_sp500_impact_direction ON sp500_daily_impact(direction);

-- 2. 일별 분석 메타 (요약 정보)
CREATE TABLE IF NOT EXISTS sp500_daily_analysis_meta (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  analysis_date DATE NOT NULL UNIQUE,
  news_count INTEGER,
  news_sources JSONB,
  summary TEXT,
  bullish_count INTEGER DEFAULT 0,
  bearish_count INTEGER DEFAULT 0,
  neutral_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- RLS (Row Level Security) - 필요 시 활성화
-- ALTER TABLE sp500_daily_impact ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE sp500_daily_analysis_meta ENABLE ROW LEVEL SECURITY;

-- anon 키로 읽기/쓰기 허용 (서버 사이드 사용)
-- CREATE POLICY "Allow all for anon" ON sp500_daily_impact FOR ALL USING (true);
-- CREATE POLICY "Allow all for anon" ON sp500_daily_analysis_meta FOR ALL USING (true);
