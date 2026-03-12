"""
Supabase REST API 래퍼 (http.client → httpx 전환)
XGBoost 학습/예측 양쪽에서 공용 사용
"""
import os
import logging
import httpx

logger = logging.getLogger("supabase_service")

SUPABASE_URL = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("VITE_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY", "")


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _check_config():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("Supabase 설정 누락 (SUPABASE_URL 또는 SUPABASE_KEY)")


async def load_dataset(dataset_id: str) -> tuple[list, list]:
    """training_datasets 테이블에서 features, labels 로드"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/training_datasets?id=eq.{dataset_id}&select=features,labels"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers())

    if resp.status_code < 200 or resp.status_code >= 300:
        raise Exception(f"Supabase 에러 ({resp.status_code}): {resp.text}")

    result = resp.json()
    if not result:
        raise Exception(f"Dataset {dataset_id} 를 찾을 수 없습니다")

    return result[0]["features"], result[0]["labels"]


async def load_features(dataset_id: str) -> list:
    """training_datasets 테이블에서 features 만 로드 (예측용)"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/training_datasets?id=eq.{dataset_id}&select=features"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers())

    if resp.status_code < 200 or resp.status_code >= 300:
        raise Exception(f"Supabase 에러 ({resp.status_code}): {resp.text}")

    result = resp.json()
    if not result:
        raise Exception(f"Dataset {dataset_id} 를 찾을 수 없습니다")

    return result[0]["features"]


async def load_model(model_id: str) -> dict:
    """ml_models 테이블에서 model_json 로드"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/ml_models?id=eq.{model_id}&select=model_json"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers())

    if resp.status_code < 200 or resp.status_code >= 300:
        raise Exception(f"Supabase 에러 ({resp.status_code}): {resp.text}")

    result = resp.json()
    if not result:
        raise Exception(f"Model {model_id} 를 찾을 수 없습니다")

    return result[0]["model_json"]


async def save_model(model_data: dict) -> str:
    """ml_models 테이블에 모델 저장 후 생성된 id 반환"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/ml_models"
    headers = {**_headers(), "Prefer": "return=representation"}

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=model_data, headers=headers)

    if resp.status_code < 200 or resp.status_code >= 300:
        raise Exception(f"Supabase 저장 실패 ({resp.status_code}): {resp.text}")

    result = resp.json()
    if isinstance(result, list) and result:
        return result[0]["id"]
    raise Exception("모델 저장 후 id를 받지 못했습니다")
