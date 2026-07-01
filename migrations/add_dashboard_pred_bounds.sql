-- ============================================================
-- earnings_dashboard 뷰에 예측 최고/최저가 노출 추가
--
-- Supabase SQL Editor에서 실행하세요.
--
-- 배경:
--   대시보드(예측 탭)에서 종가 예측(predict_price)뿐 아니라
--   구간 예상 최고가(price_max_pred)·최저가(price_min_pred)도 함께 보여주기 위해
--   최신 예측 1건을 끌어오는 LATERAL 조인에 두 컬럼을 추가한다.
--   earnings_predictions.price_max_pred / price_min_pred 는
--   add_earnings_multitarget_predictions.sql 에서 이미 추가됨.
--
-- 주의: CREATE OR REPLACE VIEW 는 기존 컬럼(순서·이름·타입)을 그대로 두고
--   새 컬럼을 '맨 뒤'에만 추가할 수 있다. 그래서 price_max_pred/price_min_pred 는
--   elapsed_pct 뒤에 append 한다. (앱은 컬럼을 이름으로 읽으므로 순서 무관)
-- ============================================================

CREATE OR REPLACE VIEW earnings_dashboard AS
SELECT
  e.id                AS event_id,
  e.ticker,
  e.gics_sector,
  e.earnings_date,
  e.next_earnings_date,
  e.px_pre            AS start_price,     -- 시작가 (발표 직전 종가)
  pr.target_price     AS predict_price,   -- 예측가 (최신 예측, 종가)
  e.ret_hold,                             -- 종료값
  CASE WHEN e.ret_hold IS NULL THEN 'pending' ELSE 'labeled' END AS label_status,
  ROUND(
    (CURRENT_DATE - e.earnings_date)::numeric
      / NULLIF((e.next_earnings_date - e.earnings_date), 0) * 100,
    1
  ) AS elapsed_pct,                       -- 기간 경과%
  pr.price_max_pred,                      -- 예상 최고가 (구간 MFE)  ← append
  pr.price_min_pred                       -- 예상 최저가 (구간 MAE)  ← append
FROM earnings_events e
LEFT JOIN LATERAL (
  SELECT target_price, price_max_pred, price_min_pred
  FROM earnings_predictions
  WHERE event_id = e.id
  ORDER BY predicted_at DESC
  LIMIT 1
) pr ON TRUE;
