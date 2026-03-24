-- #44 job_listings 테이블
-- Supabase SQL Editor 또는 CLI로 실행

create table if not exists job_listings (
    id          uuid primary key default gen_random_uuid(),
    site        text not null,           -- 'saramin' | 'wanted'
    company     text not null,
    title       text not null,
    url         text unique not null,    -- 중복 방지 기준
    deadline    date,
    career      text default '',
    location    text default '',
    notified_at timestamptz,             -- 카카오 발송 시각 (null = 미발송)
    created_at  timestamptz default now()
);

-- 최신순 조회용 인덱스
create index if not exists idx_job_listings_created_at on job_listings(created_at desc);
-- 미발송 공고 조회용 인덱스
create index if not exists idx_job_listings_notified on job_listings(notified_at) where notified_at is null;
