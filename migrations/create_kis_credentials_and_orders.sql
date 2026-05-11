-- ============================================================
-- 1. KIS 자격증명 테이블 (서버가 자동매매 시 사용)
-- ============================================================
CREATE TABLE IF NOT EXISTS kis_credentials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID,
  account_no VARCHAR(20) NOT NULL,   -- "12345678-01" 또는 "1234567801"
  appkey TEXT NOT NULL,
  appsecret TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 단일 사용자 환경: 최신 1건만 사용. RLS 비활성화 (websocket_keys와 동일 정책)
ALTER TABLE kis_credentials DISABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_kis_credentials_updated
  ON kis_credentials(updated_at DESC);


-- ============================================================
-- 2. 실시간 매매 주문 이력 테이블
-- ============================================================
CREATE TABLE IF NOT EXISTS realtime_orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trade_id UUID REFERENCES realtime_trading(id) ON DELETE CASCADE,
  ticker VARCHAR(10) NOT NULL,
  market VARCHAR(10) NOT NULL,
  side VARCHAR(10) NOT NULL,                 -- buy / sell / none
  action VARCHAR(30) NOT NULL,               -- buy_and_update / sell_and_update / update_base_price
  quantity INTEGER NOT NULL DEFAULT 0,
  price DECIMAL(12,2) NOT NULL,
  base_price_before DECIMAL(12,2),
  base_price_after DECIMAL(12,2),
  price_rate DECIMAL(8,4),                   -- 등락율 (%)
  success BOOLEAN NOT NULL DEFAULT FALSE,
  order_no VARCHAR(30),                      -- KIS 주문번호 (성공 시)
  error_message TEXT,                        -- 실패 사유 (실패 시)
  created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE realtime_orders DISABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_realtime_orders_trade
  ON realtime_orders(trade_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_realtime_orders_created
  ON realtime_orders(created_at DESC);
