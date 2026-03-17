"""
딥러닝(XGBoost) 모델 로드 & 추론 서비스

Supabase ml_models 테이블의 model_json 컬럼에서 XGBoost 모델을 로드합니다.
(파일 스토리지 없이 JSON 직렬화 방식 사용 — xgb_service 와 동일한 방식)

테이블 구조 (ml_models):
    id             uuid  PK
    name           text
    accuracy       float
    feature_count  int
    sample_count   int
    model_json     jsonb  XGBoost Booster JSON
    created_at     timestamptz
"""
import json
import logging
import os
import tempfile

import numpy as np

logger = logging.getLogger("dl_model_service")

# 메모리 캐시 (model_id → booster)
_model_cache: dict = {}


async def get_model(model_id: str):
    """
    ml_models 테이블에서 모델 메타데이터와 XGBoost Booster를 반환합니다.
    캐시된 모델이 있으면 재사용합니다.

    Returns:
        (meta: dict, booster: xgb.Booster)
    """
    from services.supabase_service import _headers, SUPABASE_URL

    # 메타데이터 전체 로드
    import httpx
    url = f"{SUPABASE_URL}/rest/v1/ml_models?id=eq.{model_id}&select=*"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())

    if resp.status_code >= 400:
        raise RuntimeError(f"모델 로드 실패 ({resp.status_code}): {resp.text}")

    rows = resp.json()
    if not rows:
        raise RuntimeError(f"모델을 찾을 수 없습니다: {model_id}")

    meta = rows[0]
    model_json = meta.get("model_json")
    if not model_json:
        raise RuntimeError(f"model_json 이 비어 있습니다: {model_id}")

    if model_id in _model_cache:
        logger.info(f"[DL] 캐시된 모델 사용: {model_id}")
        return meta, _model_cache[model_id]

    # XGBoost Booster 로드
    import xgboost as xgb
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(model_json, f)
        tmp_path = f.name

    try:
        booster = xgb.Booster()
        booster.load_model(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    _model_cache[model_id] = booster
    logger.info(f"[DL] 모델 로드 완료: {model_id}")
    return meta, booster


def predict(model, meta: dict, feature_matrix: list[list[float]]) -> tuple[float, float]:
    """
    XGBoost Booster로 매수/매도 확률을 추론합니다.

    Args:
        model: xgb.Booster 객체
        meta: 모델 메타데이터 (feature_count 등)
        feature_matrix: [[f1, f2, ...], ...] 형태의 피처 행렬 (시간 순)

    Returns:
        (buy_prob, sell_prob) — 0~1 사이 확률
    """
    import xgboost as xgb

    # 가장 최근 행 1개만 사용
    X = np.array(feature_matrix[-1:], dtype=np.float32)
    dmatrix = xgb.DMatrix(X)
    probs = model.predict(dmatrix)  # shape: (1,) — binary:logistic

    buy_prob = float(probs[0])
    sell_prob = 1.0 - buy_prob
    return buy_prob, sell_prob
