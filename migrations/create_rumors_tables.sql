-- ============================================================
-- 소문(Rumors) 분석 테이블 생성 마이그레이션
--
-- Supabase SQL Editor에서 실행하세요.
-- 뉴스의 news_stock_impact처럼 소문도 종목별 영향도를 저장합니다.
-- ============================================================

-- ── rumors 테이블 (뉴스 테이블과 유사) ──────────────────────────────────
create table if not exists rumors (
    id           uuid primary key default gen_random_uuid(),
    ticker       text not null,                 -- 종목코드 (AAPL 등)
    platform     text not null,                 -- reddit | stocktwits | twitter
    content      text,                          -- 원본 게시물/댓글 내용
    source_url   text,                          -- 원본 링크
    author       text,                          -- 작성자 (있으면)
    published_at timestamptz,                  -- 게시 시각
    rumor_date   date not null,                -- KST 기준 날짜 (날짜별 조회용)
    analyzed_at  timestamptz,                  -- 분석 완료 시각
    created_at   timestamptz default now()
);

-- 날짜별 조회 인덱스
create index if not exists idx_rumors_date on rumors(rumor_date desc);
-- 종목별 조회 인덱스
create index if not exists idx_rumors_ticker on rumors(ticker);
-- 플랫폼별 조회 인덱스
create index if not exists idx_rumors_platform on rumors(platform);
-- 미분석 소문 조회용 인덱스
create index if not exists idx_rumors_unanalyzed on rumors(analyzed_at) where analyzed_at is null;

-- ── rumors_stock_impact 테이블 (news_stock_impact와 동일 패턴) ──────────
create table if not exists rumors_stock_impact (
    id             uuid primary key default gen_random_uuid(),
    rumor_id       uuid not null references rumors(id) on delete cascade,
    ticker         text not null,                -- 영향받는 종목코드
    name           text,                        -- 종목명
    sentiment      text,                        -- positive | negative | neutral
    reason         text,                        -- 영향 이유 (분석 결과)
    confidence     numeric(3,2),                -- 신뢰도 0.00 ~ 1.00
    post_count     int default 0,               -- 관련 게시물 수
    created_at     timestamptz default now()
);

-- rumor_id 기준 조회 인덱스
create index if not exists idx_rumors_stock_impact_rumor_id on rumors_stock_impact(rumor_id);
-- 종목별 조회 인덱스
create index if not exists idx_rumors_stock_impact_ticker on rumors_stock_impact(ticker);
-- 감정별 조회 인덱스
create index if not exists idx_rumors_stock_impact_sentiment on rumors_stock_impact(sentiment);

-- ── 종목별 일일 소문 요약 (집계용) ─────────────────────────────────────
create table if not exists rumors_daily_summary (
    id                uuid primary key default gen_random_uuid(),
    ticker            text not null,
    summary_date      date not null,
    positive_count    int default 0,
    negative_count    int default 0,
    neutral_count     int default 0,
    avg_confidence    numeric(3,2),
    dominant_sentiment text,                   -- 가장 많은 감정
    total_posts       int default 0,
    platforms         text[],                  -- ["reddit", "stocktwits", "twitter"]
    created_at        timestamptz default now(),

    unique(ticker, summary_date)
);

-- 날짜별·종목별 조회 인덱스
create index if not exists idx_rumors_daily_summary_date_ticker
  on rumors_daily_summary(summary_date desc, ticker);
