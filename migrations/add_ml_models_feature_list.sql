-- ============================================================
-- ml_models 에 feature_list(JSONB) 추가
--
-- Supabase SQL Editor에서 실행하세요.
--
-- 배경:
--   실적 모델이 '피처 선택(forward selection)'으로 고른 부분 피처셋으로 학습되면서,
--   각 모델이 '자신이 어떤 피처를 어떤 순서로 썼는지'를 알아야 예측 시 동일하게
--   벡터를 구성할 수 있다. 모델별로 feature_list 를 저장한다.
--   (없으면 예측은 전체 FEATURE_COLUMNS 로 폴백)
-- ============================================================

ALTER TABLE ml_models
  ADD COLUMN IF NOT EXISTS feature_list JSONB;
