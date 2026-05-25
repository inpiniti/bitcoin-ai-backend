-- realtime_trading 테이블에 grid_step 칼럼 추가 (마틴게일 등비수열 매매용)
ALTER TABLE realtime_trading ADD COLUMN IF NOT EXISTS grid_step INT NOT NULL DEFAULT 0;
COMMENT ON COLUMN realtime_trading.grid_step IS '등비수열 매수 단계 (수량 조절용)';
