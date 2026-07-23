-- ============================================================
-- earnings_dashboard 뷰: 예측가를 '저장된 변화율 × 시작가(px_pre)'로 재계산
--
-- Supabase SQL Editor에서 실행하세요.
--
-- 배경:
--   모델은 '변화율(ret_*_pred)'만 예측한다. 가격은 파생값일 뿐이므로
--   예측을 다시 돌릴 필요 없이 저장된 변화율에 시작가(px_pre)를 곱해
--   화면 가격을 만든다. → 기존 예측도 즉시 px_pre 기준으로 교정됨(재예측 불필요).
--
--   predict_price   = px_pre * (1 + ret_hold_pred)
--   price_max_pred  = px_pre * (1 + ret_max_up_pred)
--   price_min_pred  = px_pre * (1 + ret_max_down_pred)
--
-- DROP + CREATE 로 재생성 (컬럼 순서/타입 제약 회피). 앱은 컬럼을 이름으로 읽음.
-- ============================================================

DROP VIEW IF EXISTS earnings_dashboard;

CREATE VIEW earnings_dashboard AS
SELECT
  e.id                AS event_id,
  e.ticker,
  e.gics_sector,
  e.earnings_date,
  e.next_earnings_date,
  e.px_pre            AS start_price,                                  -- 시작가 (발표 직전 종가)
  ROUND(e.px_pre * (1 + pr.ret_hold_pred), 2)     AS predict_price,    -- 예측가 = 변화율 × 시작가
  ROUND(e.px_pre * (1 + pr.ret_max_up_pred), 2)   AS price_max_pred,   -- 예상 최고가
  ROUND(e.px_pre * (1 + pr.ret_max_down_pred), 2) AS price_min_pred,   -- 예상 최저가
  e.ret_hold,                                                          -- 종료값(실제)
  CASE WHEN e.ret_hold IS NULL THEN 'pending' ELSE 'labeled' END AS label_status,
  ROUND(
    (CURRENT_DATE - e.earnings_date)::numeric
      / NULLIF((e.next_earnings_date - e.earnings_date), 0) * 100,
    1
  ) AS elapsed_pct                                                     -- 기간 경과%
FROM earnings_events e
LEFT JOIN LATERAL (
  SELECT ret_hold_pred, ret_max_up_pred, ret_max_down_pred
  FROM earnings_predictions
  WHERE event_id = e.id
  ORDER BY predicted_at DESC
  LIMIT 1
) pr ON TRUE;
