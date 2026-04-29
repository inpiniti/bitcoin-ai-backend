-- ============================================================
-- 소문 분석 컬럼 정리
--
-- Supabase SQL Editor에서 실행하세요.
-- rumors_sentiment 컬럼을 제거합니다 (rumors_signal로 대체됨).
-- ============================================================

-- rumors_sentiment 컬럼 삭제 (rumors_signal로 대체됨)
ALTER TABLE sp500_daily_impact
  DROP COLUMN IF EXISTS rumors_sentiment;
