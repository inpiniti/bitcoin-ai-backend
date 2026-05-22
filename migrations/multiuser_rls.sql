-- ============================================================
-- 멀티유저 RLS 전환
-- ============================================================
--
-- ⚠️ 실행 시점 (무중단 전환 — 순서 엄수):
--   [1] 스키마 준비: 언제든 안전하게 먼저 실행 가능.
--   [2] 데이터 백필 + [3] RLS 정책: 앱·백엔드의 멀티유저 코드가
--       모두 배포된 뒤 "맨 마지막"에 실행한다. 그 전에 실행하면
--       기존 사용자의 종목이 안 보이고 백엔드 감지가 막힌다.
--
-- ⚠️ 선행 조건:
--   백엔드 HF Secrets에 SUPABASE_SERVICE_ROLE_KEY가 설정되어 있어야 한다.
--   (백엔드 감지·매매는 service_role로 RLS를 우회한다)


-- ─────────────────────────────────────────────
-- [1] 스키마 준비 (안전 — 먼저 실행해도 무방)
-- ─────────────────────────────────────────────
-- 소유자 추적 기준은 realtime_trading.user_id.
-- realtime_orders는 trade_id로 소유자를 조인 판정한다 (별도 컬럼 없음).

CREATE INDEX IF NOT EXISTS idx_realtime_trading_user
  ON realtime_trading(user_id);


-- ─────────────────────────────────────────────
-- [2] 기존 데이터 백필 (RLS 켜기 "직전"에 실행)
-- ─────────────────────────────────────────────
-- 기존 realtime_trading 행은 user_id가 NULL이라 RLS 적용 시 접근 불가가 된다.
-- 본인 계좌로 한 번 로그인하면 kis_credentials에 본인 user_id가 생기므로,
-- 그 값으로 기존 종목을 귀속시킨다.
--
--   1) 본인 user_id 확인:
--        SELECT user_id, account_no FROM kis_credentials
--          ORDER BY updated_at DESC;
--   2) 아래 <OWNER_USER_ID>를 위에서 확인한 본인 user_id로 치환해 실행:
--
-- UPDATE realtime_trading SET user_id = '<OWNER_USER_ID>' WHERE user_id IS NULL;

-- user_id 없는 자격증명/키 정리 (재로그인 시 백엔드가 자동 재생성)
DELETE FROM kis_credentials WHERE user_id IS NULL;
DELETE FROM websocket_keys  WHERE user_id IS NULL;


-- ─────────────────────────────────────────────
-- [3] RLS 정책 (맨 마지막 실행)
-- ─────────────────────────────────────────────

-- realtime_trading: 본인 행만. anon(게스트)은 정책이 없어 전면 차단된다.
DROP POLICY IF EXISTS "Allow all access" ON realtime_trading;
ALTER TABLE realtime_trading ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "rt_own_rows" ON realtime_trading;
CREATE POLICY "rt_own_rows" ON realtime_trading
  FOR ALL TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

-- realtime_orders: 본인 trade의 주문만 조회 (insert는 백엔드 service_role이 우회).
ALTER TABLE realtime_orders ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "ro_own_select" ON realtime_orders;
CREATE POLICY "ro_own_select" ON realtime_orders
  FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM realtime_trading t
    WHERE t.id = realtime_orders.trade_id
      AND t.user_id = auth.uid()
  ));

-- kis_credentials / websocket_keys: 앱은 직접 접근하지 않는다(백엔드 전용).
-- RLS만 켜면 anon/authenticated는 차단되고 service_role만 통과한다.
ALTER TABLE kis_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE websocket_keys  ENABLE ROW LEVEL SECURITY;
