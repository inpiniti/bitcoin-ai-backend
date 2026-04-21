-- ============================================================
-- 기존 채용공고 및 뉴스 관련 테이블 삭제
-- 
-- 사용하지 않는 기존 파이프라인의 데이터를 정리합니다.
-- Supabase SQL Editor에서 실행하세요.
-- ============================================================

DROP TABLE IF EXISTS job_listings CASCADE;
DROP TABLE IF EXISTS news_stock_impact CASCADE;
DROP TABLE IF EXISTS news CASCADE;
