-- ============================================================
-- 소문(Rumors) 분석 신호 컬럼 추가 마이그레이션
--
-- Supabase SQL Editor에서 실행하세요.
-- sp500_daily_impact 테이블에 소문 분석 결과 컬럼을 추가합니다.
-- ============================================================

-- Reddit, StockTwits, Twitter 종합 감정 (positive / negative / neutral)
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS rumors_sentiment VARCHAR(10);

-- 소문 감정 분석 신뢰도 (0.0~1.0)
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS rumors_confidence DECIMAL(4,3);

-- 소문 분석 대상 플랫폼 개수 (Reddit + StockTwits + Twitter 합계)
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS rumors_post_count INT DEFAULT 0;

-- 업데이트 로그용 인덱스
CREATE INDEX IF NOT EXISTS idx_sp500_daily_rumors_sentiment
  ON sp500_daily_impact(rumors_sentiment)
  WHERE rumors_sentiment IS NOT NULL;
