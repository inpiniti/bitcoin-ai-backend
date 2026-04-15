-- ml_models 테이블에 stage 컬럼 추가
-- stage: 피처 엔지니어링 단계 (1~11), 기존 모델은 AS-IS 기준 stage 6 으로 마이그레이션
ALTER TABLE ml_models
  ADD COLUMN IF NOT EXISTS stage INTEGER NOT NULL DEFAULT 6;
