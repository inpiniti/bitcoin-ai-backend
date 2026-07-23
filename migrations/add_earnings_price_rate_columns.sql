-- ============================================================
-- earnings_events 확장: 보유 구간 장중 고저 + 금리변동
--
-- Supabase SQL Editor에서 실행하세요.
--   - px_max / px_min       : 보유 구간(발표일~다음발표전날) 장중 최고/최저가
--   - ret_max_up            : (px_max - px_post)/px_post  = MFE(최대 상승폭)
--   - ret_max_down          : (px_min - px_post)/px_post  = MAE(최대 하락폭)
--   - ust10y_change         : 보유 구간 10년물 국채금리 변동(%p, 종료 - 시작)
--
--   ※ ust10y(발표일 금리 수준) 컬럼은 기존 스키마에 이미 존재 → 이제 값이 채워짐
--   ※ px_max/min, ret_max_*, ust10y_change 는 '구간 종료까지의 결과'라
--     예측 입력(피처)이 아니라 타깃/사후분석용이다 (미래 정보 — 학습 누수 주의)
-- ============================================================

ALTER TABLE earnings_events
  ADD COLUMN IF NOT EXISTS px_max        DECIMAL(12,2),   -- 구간 장중 최고가
  ADD COLUMN IF NOT EXISTS px_min        DECIMAL(12,2),   -- 구간 장중 최저가
  ADD COLUMN IF NOT EXISTS ret_max_up    DECIMAL(10,4),   -- MFE: 발표후 최대 상승폭
  ADD COLUMN IF NOT EXISTS ret_max_down  DECIMAL(10,4),   -- MAE: 발표후 최대 하락폭
  ADD COLUMN IF NOT EXISTS ust10y_change DECIMAL(8,4);    -- 구간 10년물 금리변동(%p)
