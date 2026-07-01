"""
실적발표 자동매매 — Supabase 데이터 액세스 계층

테이블: earnings_events / earnings_predictions / earnings_positions
뷰:     earnings_dashboard
모델:   ml_models (domain='earnings', gics_sector 로 라우팅)

마이그레이션:
  migrations/create_earnings_tables.sql
  migrations/add_earnings_model_columns.sql

supabase-py 클라이언트(동기)를 사용한다. 호출부(routers/earnings.py)는
동기 def 엔드포인트라 FastAPI 스레드풀에서 실행되므로 블로킹 호출이 안전하다.
"""
import logging
from typing import Optional

from services.auth_service import get_admin_supabase

logger = logging.getLogger("earnings_repo")


def _sb():
    return get_admin_supabase()


# ─────────────────────────────────────────────
# earnings_events
# ─────────────────────────────────────────────

def upsert_event(row: dict) -> dict:
    """(ticker, earnings_date) 기준 upsert. 저장된 행 반환."""
    res = (
        _sb()
        .table("earnings_events")
        .upsert(row, on_conflict="ticker,earnings_date")
        .execute()
    )
    return (res.data or [row])[0]


def get_event(ticker: str, earnings_date: str) -> Optional[dict]:
    res = (
        _sb()
        .table("earnings_events")
        .select("*")
        .eq("ticker", ticker)
        .eq("earnings_date", earnings_date)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def list_events(
    ticker: Optional[str] = None,
    sector: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    q = _sb().table("earnings_events").select("*")
    if ticker:
        q = q.eq("ticker", ticker)
    if sector:
        q = q.eq("gics_sector", sector)
    res = q.order("earnings_date", desc=True).limit(limit).execute()
    return res.data or []


def list_collected_tickers() -> set:
    """이미 적재된(수집 완료) 종목 집합. resume(이어하기)용 — 페이지네이션."""
    out: set = set()
    offset, page = 0, 1000
    while True:
        res = (
            _sb()
            .table("earnings_events")
            .select("ticker")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = res.data or []
        for r in rows:
            if r.get("ticker"):
                out.add(r["ticker"])
        if len(rows) < page:
            break
        offset += page
    return out


def list_events_for_date(date: str) -> list[dict]:
    res = (
        _sb()
        .table("earnings_events")
        .select("*")
        .eq("earnings_date", date)
        .execute()
    )
    return res.data or []


def list_labeled_events(sector: Optional[str] = None) -> list[dict]:
    """
    ret_hold 가 채워진(학습 가능) 행 전체.
    Supabase 기본 응답 상한(≈1000행)에 잘리지 않도록 range 페이지네이션으로 모두 조회한다.
    (id 정렬로 페이지 경계에서 누락/중복 방지)
    """
    out: list[dict] = []
    offset, page = 0, 1000
    while True:
        q = _sb().table("earnings_events").select("*").not_.is_("ret_hold", "null")
        if sector:
            q = q.eq("gics_sector", sector)
        rows = (
            q.order("id")
            .range(offset, offset + page - 1)
            .execute()
            .data
            or []
        )
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


def list_unlabeled_events() -> list[dict]:
    """
    ret_hold 가 비어있는(예측 대상 후보) 행 전체.
    Supabase 기본 응답 상한(≈1000행)에 잘리지 않도록 range 페이지네이션으로 모두 조회한다.
    """
    out: list[dict] = []
    offset, page = 0, 1000
    while True:
        rows = (
            _sb()
            .table("earnings_events")
            .select("*")
            .is_("ret_hold", "null")
            .order("id")
            .range(offset, offset + page - 1)
            .execute()
            .data
            or []
        )
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


# ─────────────────────────────────────────────
# earnings_predictions
# ─────────────────────────────────────────────

def upsert_prediction(row: dict) -> dict:
    """(event_id, rate_scenario) 기준 upsert — 금리 시나리오별 예측 공존."""
    res = (
        _sb()
        .table("earnings_predictions")
        .upsert(row, on_conflict="event_id,rate_scenario")
        .execute()
    )
    return (res.data or [row])[0]


def latest_prediction(event_id: str) -> Optional[dict]:
    res = (
        _sb()
        .table("earnings_predictions")
        .select("*")
        .eq("event_id", event_id)
        .order("predicted_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def predicted_event_ids(rate_scenario: str = "actual") -> set[str]:
    """
    해당 금리 시나리오로 이미 예측이 저장된 event_id 집합 (재예측 제외용).
    1000행 상한 없이 range 페이지네이션으로 모두 조회한다.
    """
    out: set[str] = set()
    offset, page = 0, 1000
    while True:
        rows = (
            _sb()
            .table("earnings_predictions")
            .select("event_id")
            .eq("rate_scenario", rate_scenario)
            .order("event_id")
            .range(offset, offset + page - 1)
            .execute()
            .data
            or []
        )
        for r in rows:
            if r.get("event_id"):
                out.add(r["event_id"])
        if len(rows) < page:
            break
        offset += page
    return out


# ─────────────────────────────────────────────
# earnings_positions / dashboard
# ─────────────────────────────────────────────

def list_positions(status: str = "open") -> list[dict]:
    q = _sb().table("earnings_positions").select("*")
    if status:
        q = q.eq("status", status)
    return q.execute().data or []


def list_dashboard(limit: int = 200) -> list[dict]:
    return (
        _sb()
        .table("earnings_dashboard")
        .select("*")
        .order("earnings_date", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


# ─────────────────────────────────────────────
# ml_models (실적 모델 재사용 + 섹터 라우팅)
# ─────────────────────────────────────────────

def save_earnings_model(sector: str, model_json: dict, meta: dict, target: str = "ret_hold") -> str:
    """
    실적 모델을 ml_models 에 저장. id 반환.
    섹터 × 타깃을 name(earnings_{sector}_{target})으로 구분 (스키마 변경 없이).
    """
    payload = {
        "name": f"earnings_{sector}_{target}",
        "domain": "earnings",
        "gics_sector": sector,
        "model_json": model_json,
        **meta,
    }
    res = _sb().table("ml_models").insert(payload).execute()
    rows = res.data or []
    if not rows:
        raise RuntimeError("ml_models 저장 후 id를 받지 못했습니다")
    return rows[0]["id"]


def latest_earnings_model(sector: str, target: str = "ret_hold") -> Optional[dict]:
    """섹터 × 타깃별 최신 실적 모델 1건 (model_version 내림차순)."""
    res = (
        _sb()
        .table("ml_models")
        .select("*")
        .eq("domain", "earnings")
        .eq("gics_sector", sector)
        .eq("name", f"earnings_{sector}_{target}")
        .order("model_version", desc=True)
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else None


def list_earnings_models(limit: int = 50) -> list[dict]:
    """실적 모델 목록(상태 조회용). model_json 은 제외해 가볍게."""
    return (
        _sb()
        .table("ml_models")
        .select("id,name,gics_sector,model_version,sample_count,rmse,stage")
        .eq("domain", "earnings")
        .order("model_version", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
