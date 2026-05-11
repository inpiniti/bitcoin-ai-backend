-- WebSocket 접속키 관리 테이블
CREATE TABLE IF NOT EXISTS websocket_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  approval_key TEXT NOT NULL,
  issued_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 사용자별 최신 키를 빠르게 조회하기 위한 인덱스
CREATE INDEX IF NOT EXISTS idx_websocket_keys_user_id
ON websocket_keys(user_id, expires_at DESC);

-- RLS 정책: 사용자는 자신의 키만 조회/수정 가능
ALTER TABLE websocket_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own websocket keys"
  ON websocket_keys FOR SELECT
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own websocket keys"
  ON websocket_keys FOR INSERT
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own websocket keys"
  ON websocket_keys FOR UPDATE
  USING (auth.uid() = user_id);
