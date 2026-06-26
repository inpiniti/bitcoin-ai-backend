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
    """ret_hold 가 채워진(학습 가능) 행."""
    q = _sb().table("earnings_events").select("*").not_.is_("ret_hold", "null")
    if sector:
        q = q.eq("gics_sector", sector)
    return q.execute().data or []


def list_unlabeled_events() -> list[dict]:
    """ret_hold 가 비어있는(예측 대상) 행."""
    return (
        _sb()
        .table("earnings_events")
        .select("*")
        .is_("ret_hold", "null")
        .execute()
        .data
        or []
    )


# ─────────────────────────────────────────────
# earnings_predictions
# ─────────────────────────────────────────────

def upsert_prediction(row: dict) -> dict:
    """(event_id, model_version) 기준 upsert."""
    res = (
        _sb()
        .table("earnings_predictions")
        .upsert(row, on_conflict="event_id,model_version")
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

def save_earnings_model(sector: str, model_json: dict, meta: dict) -> str:
    """실적 모델을 ml_models 에 저장(domain='earnings', gics_sector=섹터). id 반환."""
    payload = {
        "name": f"earnings_{sector}",
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


def latest_earnings_model(sector: str) -> Optional[dict]:
    """섹터별 최신 실적 모델 1건 (model_version 내림차순)."""
    res = (
        _sb()
        .table("ml_models")
        .select("*")
        .eq("domain", "earnings")
        .eq("gics_sector", sector)
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
