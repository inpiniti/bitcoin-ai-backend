-- ============================================================
-- 국내주식 마켓 지원 추가
-- ============================================================
-- 0단계: DB 스키마 변경
-- - market 컬럼에 국내 시장값(KRX, KOSDAQ) 허용
-- - market_type 컬럼 추가 (GENERATED: domestic / overseas)

ALTER TABLE realtime_trading
  DROP CONSTRAINT IF EXISTS realtime_trading_market_check;

ALTER TABLE realtime_trading
  ADD CONSTRAINT realtime_trading_market_check
  CHECK (market IN ('NAS','NYS','AMS','KRX','KOSDAQ'));

ALTER TABLE realtime_trading
  ADD COLUMN IF NOT EXISTS market_type TEXT
  GENERATED ALWAYS AS (
    CASE WHEN market IN ('KRX','KOSDAQ') THEN 'domestic' ELSE 'overseas' END
  ) STORED;
