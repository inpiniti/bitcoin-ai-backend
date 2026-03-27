-- #61 뉴스 서비스 테이블
-- Supabase SQL Editor 또는 CLI로 실행

-- ── news 테이블 ──────────────────────────────────────────────────────────────
create table if not exists news (
    id           uuid primary key default gen_random_uuid(),
    title        text not null,
    summary      text,
    url          text unique not null,    -- 중복 방지 기준
    source       text,                    -- 출처 (naver_finance, hankyung 등)
    published_at timestamptz,            -- 기사 발행 시각
    news_date    date not null,           -- KST 기준 날짜 (날짜별 조회용)
    market_impact text,                  -- 시장 전체 영향 분석 (Gemini 생성)
    impact_level  text,                  -- high | medium | low
    analyzed_at  timestamptz,            -- Gemini 분석 완료 시각
    created_at   timestamptz default now()
);

-- 날짜별 조회 인덱스 (주요 쿼리 패턴)
create index if not exists idx_news_date on news(news_date desc);
-- 미분석 뉴스 조회용 인덱스
create index if not exists idx_news_unanalyzed on news(analyzed_at) where analyzed_at is null;

-- ── news_stock_impact 테이블 ─────────────────────────────────────────────────
create table if not exists news_stock_impact (
    id         uuid primary key default gen_random_uuid(),
    news_id    uuid not null references news(id) on delete cascade,
    ticker     text,                      -- 종목코드 또는 심볼 (예: 005930, BTC)
    name       text not null,             -- 종목명 (예: 삼성전자, 비트코인)
    market     text,                      -- KOSPI | KOSDAQ | CRYPTO | US
    direction  text,                      -- bullish | bearish | neutral
    reason     text,                      -- 영향 이유 (Gemini 생성)
    confidence numeric(3,2)               -- 신뢰도 0.00 ~ 1.00
);

-- news_id 기준 조회 인덱스
create index if not exists idx_news_stock_impact_news_id on news_stock_impact(news_id);
