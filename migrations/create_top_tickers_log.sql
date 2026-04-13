-- 상위 종목 로그 테이블 (카카오 리포트 TOP10을 DB에도 저장)
-- Supabase SQL Editor 에서 실행

-- ── top_tickers_log 테이블 ──────────────────────────────────────────────────
create table if not exists top_tickers_log (
    id             uuid primary key default gen_random_uuid(),
    trade_date     date not null,                      -- 실행 날짜 (YYYY-MM-DD)
    setting_name   text,                               -- automation_settings.name
    target_group   text,                               -- sp500, usall, qqq 등
    tickers        jsonb not null default '[]',        -- [{rank, ticker, name, buy_prob}]
    buy_threshold  double precision,                   -- 매수 기준 확률
    total_scanned  integer,                            -- 전체 스캔 종목 수
    created_at     timestamptz default now()
);

-- 날짜별 조회 인덱스
create index if not exists idx_top_tickers_log_date
    on top_tickers_log (trade_date desc);

-- 설정명 + 날짜 복합 인덱스 (앱에서 필터링용)
create index if not exists idx_top_tickers_log_setting_date
    on top_tickers_log (setting_name, trade_date desc);


-- ── auto_trade_dl_logs 에 setting_name 컬럼 추가 ─────────────────────────────
-- 기존 테이블이 있을 경우 안전하게 컬럼 추가 (없으면 무시)
do $$
begin
    if not exists (
        select 1 from information_schema.columns
        where table_name = 'auto_trade_dl_logs'
          and column_name = 'setting_name'
    ) then
        alter table auto_trade_dl_logs add column setting_name text;
    end if;
end $$;
