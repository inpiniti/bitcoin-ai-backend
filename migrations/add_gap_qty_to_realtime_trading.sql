-- Add gap_qty (갭 수량) column to realtime_trading
-- Run in Supabase SQL Editor
--
-- gap_qty: 한 번의 갭 도달 시 매수/매도할 수량 (기존 quantity는 현재 보유 수량 — 매매 시 자동 갱신)
-- 매수는 gap_qty만큼 그대로 실행, 매도는 보유수량(quantity)이 gap_qty보다 적으면 보유수량만큼만 매도.

alter table if exists realtime_trading
add column if not exists gap_qty integer not null default 1;

-- 기존 row는 기본값 1로 설정됨 (안전한 값)
