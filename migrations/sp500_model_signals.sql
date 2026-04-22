-- ============================================================
-- S&P 500 모델 예측 신호 컬럼 추가 마이그레이션
--
-- Supabase SQL Editor에서 실행하세요.
-- 기존 sp500_daily_impact 테이블에 모델 예측 컬럼을 추가합니다.
-- ============================================================

-- XGBoost 상승 확률 (0.0~1.0, ex: 0.72 = 72%)
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS xgb_prob DECIMAL(4,3);

-- 강화학습(RL) 신호 (BUY / SELL / HOLD / null)
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS rl_signal VARCHAR(4);

-- TimesFM 방향 예측 (up / down / null)
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS timesfm_signal VARCHAR(4);

-- Amazon Chronos-2 방향 예측 (up / down / null)
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS chronos_signal VARCHAR(4);

-- Salesforce Moirai 방향 예측 (up / down / null)
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS moirai_signal VARCHAR(4);

-- 모델 예측에 사용된 XGBoost 모델 ID
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS xgb_model_id TEXT;

-- 모델 예측에 사용된 RL 모델 ID
ALTER TABLE sp500_daily_impact
  ADD COLUMN IF NOT EXISTS rl_model_id TEXT;
