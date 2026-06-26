-- ============================================================
-- ml_models 테이블에 실적발표 모델 라우팅 컬럼 추가
--
-- Supabase SQL Editor에서 실행하세요.
-- 실적발표 모델은 신규 테이블을 만들지 않고 ml_models를 재사용한다.
-- 섹터별 최신 모델 라우팅을 위해 domain / gics_sector 컬럼을 추가한다.
--   조회 예: ml_models?domain=eq.earnings&gics_sector=eq.Technology
--            &order=created_at.desc&limit=1
-- ============================================================

ALTER TABLE ml_models
  ADD COLUMN IF NOT EXISTS domain VARCHAR(20);          -- 'earnings' / 'sp500' / null

ALTER TABLE ml_models
  ADD COLUMN IF NOT EXISTS gics_sector VARCHAR(50);     -- 실적발표 모델의 섹터 키

ALTER TABLE ml_models
  ADD COLUMN IF NOT EXISTS model_version VARCHAR(50);   -- 정렬용 버전(YYYYMMDDHHMMSS)

ALTER TABLE ml_models
  ADD COLUMN IF NOT EXISTS rmse DOUBLE PRECISION;       -- 학습 RMSE(회귀 모델)

-- 섹터별 최신 모델 라우팅: model_version 내림차순으로 1건 조회
CREATE INDEX IF NOT EXISTS idx_ml_models_domain_sector
  ON ml_models(domain, gics_sector, model_version DESC);
