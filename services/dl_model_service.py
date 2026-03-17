"""
딥러닝 모델 로드 & 추론 서비스

Supabase 의 dl_models 테이블에서 모델 메타데이터를 로드하고,
Supabase Storage 의 dl-models 버킷에서 Keras HDF5 모델 파일을 로드합니다.

테이블 구조 (dl_models):
    id              uuid  PK
    name            text
    model_type      text  ('keras' | 'xgboost')
    features        jsonb  ["close", "rsi14", "bb_pct_b", ...]
    lookback_period int    시퀀스 길이 (LSTM 등)
    buy_threshold   float  BUY 확률 임계값
    sell_threshold  float  SELL 확률 임계값
    storage_path    text   Supabase Storage 경로 (예: models/my_model.h5)
    created_at      timestamptz

Storage 버킷: dl-models
    파일 형식: Keras HDF5 (.h5) 또는 SavedModel (.zip)
"""
import os
import io
import logging
import tempfile
import httpx
import numpy as np

logger = logging.getLogger("dl_model_service")

SUPABASE_URL = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("VITE_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY", "")
STORAGE_BUCKET = "ml-models"

# 메모리 모델 캐시 (model_id → model 객체)
_model_cache: dict = {}


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


async def load_model_meta(model_id: str) -> dict:
    """ml_models 테이블에서 모델 메타데이터 로드"""
    url = f"{SUPABASE_URL}/rest/v1/ml_models?id=eq.{model_id}&select=*"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())

    if resp.status_code >= 400:
        raise RuntimeError(f"모델 메타데이터 로드 실패 ({resp.status_code}): {resp.text}")

    rows = resp.json()
    if not rows:
        raise RuntimeError(f"모델을 찾을 수 없습니다: {model_id}")
    return rows[0]


async def _download_model_bytes(storage_path: str) -> bytes:
    """Supabase Storage 에서 모델 파일 다운로드"""
    url = f"{SUPABASE_URL}/storage/v1/object/{STORAGE_BUCKET}/{storage_path}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        })

    if resp.status_code >= 400:
        raise RuntimeError(f"모델 파일 다운로드 실패 ({resp.status_code}): {storage_path}")

    return resp.content


async def get_model(model_id: str):
    """
    모델 메타데이터와 모델 객체를 반환합니다.
    캐시된 모델이 있으면 재사용합니다.

    Returns:
        (meta: dict, model: keras.Model or sklearn estimator)
    """
    meta = await load_model_meta(model_id)
    model_type = meta.get("model_type", "keras")

    if model_id in _model_cache:
        logger.info(f"[DL] 캐시된 모델 사용: {model_id}")
        return meta, _model_cache[model_id]

    storage_path = meta.get("storage_path")
    if not storage_path:
        raise RuntimeError(f"모델 storage_path 가 설정되지 않았습니다: {model_id}")

    logger.info(f"[DL] 모델 다운로드 중: {storage_path}")
    model_bytes = await _download_model_bytes(storage_path)

    if model_type == "keras":
        import tensorflow as tf
        with tempfile.NamedTemporaryFile(suffix=".h5", delete=False) as f:
            f.write(model_bytes)
            tmp_path = f.name
        model = tf.keras.models.load_model(tmp_path)
        os.unlink(tmp_path)

    elif model_type == "xgboost":
        import xgboost as xgb
        import joblib
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            f.write(model_bytes)
            tmp_path = f.name
        model = joblib.load(tmp_path)
        os.unlink(tmp_path)

    else:
        raise RuntimeError(f"지원하지 않는 모델 타입: {model_type}")

    _model_cache[model_id] = model
    logger.info(f"[DL] 모델 로드 완료: {model_id} ({model_type})")
    return meta, model


def predict(model, meta: dict, feature_matrix: list[list[float]]) -> tuple[float, float]:
    """
    모델 추론 실행.

    Args:
        model: 로드된 모델 객체
        meta: 모델 메타데이터 (model_type, lookback_period 포함)
        feature_matrix: [[f1, f2, ...], ...] 형태의 피처 행렬 (시간 순)

    Returns:
        (buy_prob, sell_prob) - BUY/SELL 클래스 확률 (0~1)
    """
    model_type = meta.get("model_type", "keras")
    lookback = int(meta.get("lookback_period", 1))

    if len(feature_matrix) < lookback:
        raise ValueError(f"데이터 부족: {len(feature_matrix)} < lookback {lookback}")

    # 최근 lookback 개 행만 사용
    window = feature_matrix[-lookback:]
    X = np.array(window, dtype=np.float32)

    if model_type == "keras":
        # LSTM: (1, lookback, features) / Dense: (1, features)
        if len(X.shape) == 1 or lookback == 1:
            X = X.reshape(1, -1)
        else:
            X = X.reshape(1, lookback, X.shape[1])

        probs = model.predict(X, verbose=0)[0]  # shape: (num_classes,)

        # 클래스 순서: [HOLD, BUY, SELL] 또는 [BUY, SELL]
        if len(probs) == 3:
            buy_prob = float(probs[1])
            sell_prob = float(probs[2])
        elif len(probs) == 2:
            buy_prob = float(probs[0])
            sell_prob = float(probs[1])
        else:
            buy_prob = float(probs[0])
            sell_prob = 1.0 - buy_prob

    elif model_type == "xgboost":
        X_flat = X.flatten().reshape(1, -1)
        proba = model.predict_proba(X_flat)[0]
        if len(proba) == 3:
            buy_prob = float(proba[1])
            sell_prob = float(proba[2])
        else:
            buy_prob = float(proba[1]) if len(proba) > 1 else float(proba[0])
            sell_prob = 1.0 - buy_prob

    else:
        raise RuntimeError(f"지원하지 않는 모델 타입: {model_type}")

    return buy_prob, sell_prob
