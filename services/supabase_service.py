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
    """ml_models 테이블에서 model_json, stage 로드
    Returns: {"model_json": dict, "stage": int}
    """
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/ml_models?id=eq.{model_id}&select=model_json,stage"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=_headers())

    if resp.status_code < 200 or resp.status_code >= 300:
        raise Exception(f"Supabase 에러 ({resp.status_code}): {resp.text}")

    result = resp.json()
    if not result:
        raise Exception(f"Model {model_id} 를 찾을 수 없습니다")

    row = result[0]
    return {
        "model_json": row["model_json"],
        "stage": row.get("stage", 6),  # 기존 모델 기본값 6
    }


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

async def load_all_automation_settings_active() -> list[dict]:
    """
    is_active=true 인 automation_settings 전체 목록을 반환합니다.
    설정이 여러 개일 때 각각 독립 실행할 수 있도록 모두 반환합니다.
    """
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/automation_settings?is_active=eq.true&select=*"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"automation_settings 로드 실패 ({resp.status_code}): {resp.text}")
    return resp.json()


async def load_automation_settings_active() -> dict | None:
    """
    하위 호환용: is_active=true 설정 중 첫 번째 1개를 반환합니다.
    새 코드에서는 load_all_automation_settings_active() 를 사용하세요.
    """
    rows = await load_all_automation_settings_active()
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
# 상위 종목 로그 (top_tickers_log)
# ─────────────────────────────────────────────

async def save_top_tickers_log(data: dict) -> None:
    """top_tickers_log 테이블에 매수 후보 TOP10 저장"""
    _check_config()
    from datetime import datetime, timezone
    data.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    url = f"{SUPABASE_URL}/rest/v1/top_tickers_log"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=data, headers=_headers())
    if resp.status_code >= 400:
        logger.warning(f"top_tickers_log 저장 실패 ({resp.status_code}): {resp.text}")


async def get_top_tickers_log(
    setting_name: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """top_tickers_log 최근 목록 조회"""
    _check_config()
    filters = f"order=trade_date.desc&limit={limit}"
    if setting_name:
        filters = f"setting_name=eq.{setting_name}&{filters}"
    url = f"{SUPABASE_URL}/rest/v1/top_tickers_log?select=*&{filters}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"top_tickers_log 조회 실패 ({resp.status_code}): {resp.text}")
    return resp.json()


async def update_top_tickers_timesfm(record_id: str, tickers: list[dict]) -> None:
    """top_tickers_log의 tickers 필드 업데이트 (TimesFM 신호 보정용)"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/top_tickers_log?id=eq.{record_id}"
    headers = {**_headers(), "Prefer": "return=representation"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(url, json={"tickers": tickers}, headers=headers)
    if resp.status_code >= 400:
        logger.warning(f"top_tickers_log 업데이트 실패 ({resp.status_code}): {resp.text}")


async def get_top_tickers_by_date(
    trade_date: str,
    setting_name: str | None = None,
) -> list[dict]:
    """특정 날짜의 top_tickers_log 조회"""
    _check_config()
    filters = f"trade_date=eq.{trade_date}&order=created_at.desc"
    if setting_name:
        filters = f"setting_name=eq.{setting_name}&{filters}"
    url = f"{SUPABASE_URL}/rest/v1/top_tickers_log?select=*&{filters}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"top_tickers_log 날짜 조회 실패 ({resp.status_code}): {resp.text}")
    return resp.json()



async def get_dl_logs_by_date(
    trade_date: str,
    setting_name: str | None = None,
) -> list[dict]:
    """특정 날짜의 auto_trade_dl_logs 조회"""
    _check_config()
    filters = f"date=eq.{trade_date}&order=created_at.desc"
    if setting_name:
        filters = f"setting_name=eq.{setting_name}&{filters}"
    url = f"{SUPABASE_URL}/rest/v1/auto_trade_dl_logs?select=*&{filters}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"auto_trade_dl_logs 날짜 조회 실패 ({resp.status_code}): {resp.text}")
    return resp.json()


# ─────────────────────────────────────────────
# S&P 500 일별 영향도 분석 (sp500_daily_impact / sp500_daily_analysis_meta)
# ─────────────────────────────────────────────

async def upsert_sp500_daily_impact(rows: list[dict]) -> int:
    """
    sp500_daily_impact 테이블에 종목 영향도 upsert.
    (analysis_date, ticker) unique 제약으로 중복 시 업데이트.

    Returns:
        처리된 건수
    """
    if not rows:
        return 0
    _check_config()
    # PostgREST upsert 시 on_conflict 파라미터가 명시적이어야 충돌 방지가 잘 됨
    url = f"{SUPABASE_URL}/rest/v1/sp500_daily_impact?on_conflict=analysis_date,ticker"
    headers = {
        **_headers(),
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    # Supabase REST는 대량 insert 시 배치 처리
    BATCH_SIZE = 100
    total = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            resp = await client.post(url, json=batch, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    f"sp500_daily_impact upsert 실패 "
                    f"(batch {i//BATCH_SIZE+1}, {resp.status_code}): {resp.text[:200]}"
                )
            else:
                result = resp.json()
                total += len(result) if isinstance(result, list) else 0
    return total


async def upsert_sp500_analysis_meta(data: dict) -> None:
    """
    sp500_daily_analysis_meta 테이블에 분석 메타 upsert.
    analysis_date unique 제약으로 중복 시 업데이트.
    """
    _check_config()
    import json as _json
    # news_sources가 list인 경우 JSON 직렬화
    if "news_sources" in data and isinstance(data["news_sources"], list):
        data["news_sources"] = _json.dumps(data["news_sources"])
    url = f"{SUPABASE_URL}/rest/v1/sp500_daily_analysis_meta?on_conflict=analysis_date"
    headers = {**_headers(), "Prefer": "resolution=merge-duplicates"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=data, headers=headers)
    if resp.status_code >= 400:
        logger.warning(f"sp500_daily_analysis_meta upsert 실패 ({resp.status_code}): {resp.text[:200]}")


async def get_sp500_daily_impact(
    analysis_date: str,
    sector: str | None = None,
    direction: str | None = None,
    limit: int = 600,
) -> list[dict]:
    """
    특정 날짜의 S&P 500 영향도 조회.

    Args:
        analysis_date: 조회 날짜 (YYYY-MM-DD)
        sector: 섹터 필터 (optional)
        direction: 방향 필터 - bullish/bearish/neutral (optional)
        limit: 최대 건수
    """
    _check_config()
    filters = f"analysis_date=eq.{analysis_date}&order=confidence.desc&limit={limit}"
    if sector:
        filters += f"&sector=eq.{sector}"
    if direction:
        filters += f"&direction=eq.{direction}"
    url = f"{SUPABASE_URL}/rest/v1/sp500_daily_impact?select=*&{filters}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"sp500_daily_impact 조회 실패 ({resp.status_code}): {resp.text}")
    return resp.json()


async def get_sp500_analysis_meta(analysis_date: str) -> dict | None:
    """특정 날짜의 분석 메타 조회"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/sp500_daily_analysis_meta?analysis_date=eq.{analysis_date}&select=*"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"sp500_analysis_meta 조회 실패 ({resp.status_code}): {resp.text}")
    rows = resp.json()
    return rows[0] if rows else None


# ─────────────────────────────────────────────
# S&P 500 시간대별 영향도 분석 (sp500_impact_hourly / sp500_hourly_analysis_meta)
# ─────────────────────────────────────────────

async def upsert_sp500_impact_hourly(rows: list[dict]) -> int:
    """
    sp500_impact_hourly 테이블에 종목 영향도 upsert.
    (analysis_datetime, ticker) unique 제약으로 중복 시 업데이트.

    Returns:
        처리된 건수
    """
    if not rows:
        return 0
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/sp500_impact_hourly?on_conflict=analysis_datetime,ticker"
    headers = {
        **_headers(),
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    BATCH_SIZE = 100
    total = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            resp = await client.post(url, json=batch, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    f"sp500_impact_hourly upsert 실패 "
                    f"(batch {i//BATCH_SIZE+1}, {resp.status_code}): {resp.text[:200]}"
                )
            else:
                result = resp.json()
                total += len(result) if isinstance(result, list) else 0
    return total


async def upsert_sp500_hourly_analysis_meta(data: dict) -> None:
    """
    sp500_hourly_analysis_meta 테이블에 분석 메타 upsert.
    analysis_datetime unique 제약으로 중복 시 업데이트.
    """
    _check_config()
    import json as _json
    if "news_sources" in data and isinstance(data["news_sources"], list):
        data["news_sources"] = _json.dumps(data["news_sources"])
    url = f"{SUPABASE_URL}/rest/v1/sp500_hourly_analysis_meta?on_conflict=analysis_datetime"
    headers = {**_headers(), "Prefer": "resolution=merge-duplicates"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=data, headers=headers)
    if resp.status_code >= 400:
        logger.warning(f"sp500_hourly_analysis_meta upsert 실패 ({resp.status_code}): {resp.text[:200]}")


async def get_sp500_impact_hourly_by_date(
    analysis_date: str,
    sector: str | None = None,
    direction: str | None = None,
    limit: int = 600,
) -> dict:
    """
    특정 날짜의 S&P 500 시간대별 영향도 조회.
    같은 날짜의 모든 시간대 데이터를 시간별로 그룹화.

    Returns:
        {
            "date": "2026-04-30",
            "times": ["09:00", "10:00", "11:00", ...],
            "by_time": {
                "09:00": [stock1, stock2, ...],
                "10:00": [stock1, stock2, ...],
                ...
            }
        }
    """
    _check_config()
    from datetime import datetime

    # 해당 날짜의 모든 시간대 데이터 조회 (ISO 형식: 2026-04-30T00:00:00.000000+00:00)
    filters = f"analysis_datetime=gte.{analysis_date}T00:00:00Z&analysis_datetime=lt.{analysis_date}T23:59:59.999999Z&order=analysis_datetime.asc,confidence.desc&limit={limit}"
    if sector:
        filters += f"&sector=eq.{sector}"
    if direction:
        filters += f"&direction=eq.{direction}"

    url = f"{SUPABASE_URL}/rest/v1/sp500_impact_hourly?select=*&{filters}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())

    if resp.status_code >= 400:
        raise Exception(f"sp500_impact_hourly 조회 실패 ({resp.status_code}): {resp.text}")

    rows = resp.json()

    # 시간별로 그룹화
    times_set = set()
    by_time = {}
    for row in rows:
        # analysis_datetime에서 시간 추출 (예: "2026-04-30T09:00:00.000000+00:00" → "09:00")
        dt_str = row.get("analysis_datetime", "")
        if dt_str:
            try:
                # ISO 형식 파싱
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                time_str = dt.strftime("%H:%M")
                times_set.add(time_str)
                if time_str not in by_time:
                    by_time[time_str] = []
                by_time[time_str].append(row)
            except Exception as e:
                logger.warning(f"시간 추출 실패: {dt_str} - {e}")

    times = sorted(list(times_set))

    return {
        "date": analysis_date,
        "times": times,
        "by_time": by_time,
    }


async def get_sp500_hourly_analysis_meta_by_date(analysis_date: str) -> list[dict]:
    """특정 날짜의 모든 시간대 분석 메타 조회 (시간 오름차순)"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/sp500_hourly_analysis_meta?analysis_datetime=gte.{analysis_date}T00:00:00Z&analysis_datetime=lt.{analysis_date}T23:59:59.999999Z&order=analysis_datetime.asc&select=*"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=_headers())
    if resp.status_code >= 400:
        raise Exception(f"sp500_hourly_analysis_meta 조회 실패 ({resp.status_code}): {resp.text}")
    return resp.json()


# ─────────── 포트폴리오 조회 함수 ───────────


async def get_portfolio_data(
    person_id: int | None = None,
    stock: str | None = None,
    limit: int = 500,
) -> dict:
    """
    포트폴리오 전체 데이터 조회
    Returns: {"based_on_person": [...], "based_on_stock": [...]}
    """
    _check_config()
    try:
        # portfolio_investors 테이블 조회
        query = f"{SUPABASE_URL}/rest/v1/portfolio_investors?select=*&limit={limit}"
        if person_id:
            query += f"&id=eq.{person_id}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(query, headers=_headers())

        if resp.status_code >= 400:
            logger.warning(f"포트폴리오 조회 실패: {resp.status_code}, 기본값 반환")
            return {"based_on_person": [], "based_on_stock": []}

        based_on_person = resp.json()

        # portfolio_holdings 테이블 조회
        query = f"{SUPABASE_URL}/rest/v1/portfolio_holdings?select=*&limit={limit}"
        if stock:
            query += f"&ticker=ilike.{stock}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(query, headers=_headers())

        if resp.status_code >= 400:
            logger.warning(f"보유 종목 조회 실패: {resp.status_code}")
            based_on_stock = []
        else:
            based_on_stock = resp.json()

        return {
            "based_on_person": based_on_person,
            "based_on_stock": based_on_stock,
        }
    except Exception as e:
        logger.error(f"포트폴리오 데이터 조회 실패: {e}")
        return {"based_on_person": [], "based_on_stock": []}


async def get_portfolio_by_person(
    person_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """투자자별 포트폴리오 조회"""
    _check_config()
    try:
        query = f"{SUPABASE_URL}/rest/v1/portfolio_investors?select=*&limit={limit}"
        if person_id:
            query += f"&id=eq.{person_id}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(query, headers=_headers())

        if resp.status_code >= 400:
            logger.warning(f"투자자 조회 실패: {resp.status_code}")
            return []

        return resp.json()
    except Exception as e:
        logger.error(f"투자자 포트폴리오 조회 실패: {e}")
        return []


async def get_portfolio_by_stock(
    stock: str,
    limit: int = 100,
) -> list[dict]:
    """종목별 보유 투자자 조회"""
    _check_config()
    try:
        query = f"{SUPABASE_URL}/rest/v1/portfolio_holdings?select=*&ticker=ilike.{stock}&limit={limit}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(query, headers=_headers())

        if resp.status_code >= 400:
            logger.warning(f"종목 조회 실패: {resp.status_code}")
            return []

        return resp.json()
    except Exception as e:
        logger.error(f"종목 포트폴리오 조회 실패: {e}")
        return []


async def save_kis_debug_log(krw_data: dict, usd_data: dict, notes: str | None = None) -> None:
    """kis_debug_logs 테이블에 디버그/에러 로그 저장"""
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/kis_debug_logs"
    payload = {
        "krw_data": krw_data,
        "usd_data": usd_data,
        "notes": notes
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=_headers())
        if resp.status_code >= 400:
            logger.warning(f"kis_debug_logs 저장 실패 ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"kis_debug_logs 저장 실패 익셉션: {e}")

