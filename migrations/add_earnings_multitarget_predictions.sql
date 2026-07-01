-- ============================================================
-- earnings_predictions 확장: 다중 타깃 + 금리 시나리오
--
-- Supabase SQL Editor에서 실행하세요.
--
-- 배경:
--   예측 타깃을 종가(ret_hold) 1개 → 3개로 확장
--     ret_hold_pred      : 종가 수익률 예측 (기존)
--     ret_max_up_pred    : 최대 상승폭 예측 (MFE)
--     ret_max_down_pred  : 최대 하락폭 예측 (MAE)
--   각 예측을 px_post 기준 예측가로도 저장
--     target_price       : 종가 예측가 (기존)
--     price_max_pred     : 최대가 예측
--     price_min_pred     : 최저가 예측
--   금리 시나리오(what-if): ust10y_change 를 가정값으로 주입해 조건부 예측
--     rate_scenario      : 'actual' | 'up' | 'down' | 'flat' | 커스텀
-- ============================================================

ALTER TABLE earnings_predictions
  ADD COLUMN IF NOT EXISTS ret_max_up_pred    DECIMAL(10,4),
  ADD COLUMN IF NOT EXISTS ret_max_down_pred  DECIMAL(10,4),
  ADD COLUMN IF NOT EXISTS price_max_pred     DECIMAL(12,2),
  ADD COLUMN IF NOT EXISTS price_min_pred     DECIMAL(12,2),
  ADD COLUMN IF NOT EXISTS rate_scenario      VARCHAR(20) NOT NULL DEFAULT 'actual';

-- 유니크 키 교체: (event_id, model_version) → (event_id, rate_scenario)
--   같은 이벤트라도 금리 시나리오(up/down/actual)별 예측을 공존시킨다.
ALTER TABLE earnings_predictions
  DROP CONSTRAINT IF EXISTS earnings_predictions_event_id_model_version_key;

ALTER TABLE earnings_predictions
  DROP CONSTRAINT IF EXISTS earnings_predictions_event_scenario_key;
ALTER TABLE earnings_predictions
  ADD CONSTRAINT earnings_predictions_event_scenario_key UNIQUE(event_id, rate_scenario);
