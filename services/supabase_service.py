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


# ─────────────────────────────────────────────
# automation_settings (클라이언트 설정 테이블)
# ─────────────────────────────────────────────

async def load_automation_settings_active() -> dict | None:
    """
    클라이언트(AutomationSettingsPanel)에서 저장한 is_active=true 설정 1개를 로드합니다.
    KIS 인증 정보(kis_appkey, kis_secret, kis_account)와
    매매 조건(buy_condition, sell_condition, ai_model_key, ticker_group_key 등)을 포함합니다.
    """
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/automation_settings?is_active=eq.true&select=*&limit=1"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"automation_settings 로드 실패 ({resp.status_code}): {resp.text}")
    rows = resp.json()
    return rows[0] if rows else None


# ─────────────────────────────────────────────
# 자동매매 딥러닝 관련
# ─────────────────────────────────────────────

async def load_auto_trade_settings() -> dict | None:
    """auto_trade_dl_settings 테이블에서 설정 로드 (단일 행)"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/auto_trade_dl_settings?select=*&limit=1"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"설정 로드 실패 ({resp.status_code}): {resp.text}")
    rows = resp.json()
    return rows[0] if rows else None


async def save_auto_trade_settings(data: dict) -> None:
    """auto_trade_dl_settings upsert (id=1 고정 행)"""
    _check_config()
    data["id"] = 1  # 단일 설정 행
    url = f"{SUPABASE_URL}/rest/v1/auto_trade_dl_settings"
    headers = {**_headers(), "Prefer": "resolution=merge-duplicates"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=data, headers=headers)
    if resp.status_code >= 400:
        raise Exception(f"설정 저장 실패 ({resp.status_code}): {resp.text}")


async def get_last_run_date() -> str | None:
    """마지막 실행일 조회 (YYYY-MM-DD)"""
    settings = await load_auto_trade_settings()
    return settings.get("last_run_date") if settings else None


async def update_last_run_date(date_str: str) -> None:
    """마지막 실행일 업데이트"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/auto_trade_dl_settings?id=eq.1"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(url, json={"last_run_date": date_str}, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"last_run_date 업데이트 실패 ({resp.status_code}): {resp.text}")


async def save_auto_trade_log(data: dict) -> None:
    """auto_trade_dl_logs 테이블에 실행 로그 저장"""
    _check_config()
    from datetime import datetime, timezone
    data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    url = f"{SUPABASE_URL}/rest/v1/auto_trade_dl_logs"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=data, headers=_headers())
    if resp.status_code >= 400:
        logger.warning(f"로그 저장 실패 ({resp.status_code}): {resp.text}")


async def get_auto_trade_logs(limit: int = 30) -> list:
    """auto_trade_dl_logs 최근 로그 조회"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/auto_trade_dl_logs?select=*&order=created_at.desc&limit={limit}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"로그 조회 실패 ({resp.status_code}): {resp.text}")
    return resp.json()


# ─────────────────────────────────────────────
# #44 job_listings (채용공고 저장/중복방지)
# ─────────────────────────────────────────────

async def upsert_job_listings(jobs: list[dict]) -> int:
    """
    job_listings 테이블에 공고 upsert.
    url unique 제약으로 중복 공고는 무시됨.

    Returns:
        신규 삽입된 건수
    """
    if not jobs:
        return 0
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/job_listings"
    headers = {
        **_headers(),
        "Prefer": "resolution=ignore-duplicates,return=representation",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=jobs, headers=headers)
    if resp.status_code >= 400:
        raise Exception(f"job_listings upsert 실패 ({resp.status_code}): {resp.text}")
    inserted = resp.json()
    return len(inserted) if isinstance(inserted, list) else 0


async def get_unnotified_jobs() -> list[dict]:
    """notified_at이 null인 미발송 공고 조회"""
    _check_config()
    url = (
        f"{SUPABASE_URL}/rest/v1/job_listings"
        "?notified_at=is.null"
        "&order=created_at.asc"
        "&select=*"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"미발송 공고 조회 실패 ({resp.status_code}): {resp.text}")
    return resp.json()


async def mark_jobs_notified(job_ids: list[str]) -> None:
    """발송 완료된 공고의 notified_at 업데이트"""
    if not job_ids:
        return
    from datetime import datetime, timezone
    _check_config()
    now = datetime.now(timezone.utc).isoformat()
    # Supabase REST: in 필터
    ids_csv = ",".join(job_ids)
    url = f"{SUPABASE_URL}/rest/v1/job_listings?id=in.({ids_csv})"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(url, json={"notified_at": now}, headers=_headers())
    if resp.status_code >= 400:
        logger.warning(f"notified_at 업데이트 실패 ({resp.status_code}): {resp.text}")


async def get_job_listings(limit: int = 50) -> list[dict]:
    """최근 채용공고 조회 (라우터 엔드포인트용)"""
    _check_config()
    url = (
        f"{SUPABASE_URL}/rest/v1/job_listings"
        f"?select=*&order=created_at.desc&limit={limit}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"공고 조회 실패 ({resp.status_code}): {resp.text}")
    return resp.json()
