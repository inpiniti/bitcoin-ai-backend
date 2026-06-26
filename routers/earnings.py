"""
실적발표 자동매매 라우터

설계: trends/실적발표_자동매매_시퀀스.md §12.1
Postman: trends/실적발표_자동매매_API.postman_collection.json

엔드포인트 (prefix=/api)
  POST /api/earnings/history/collect   과거 적재 (초기 1회)
  GET  /api/earnings/calendar          오늘 발표 종목 (DB 기준)
  POST /api/earnings/today/collect     오늘자 수집
  GET  /api/earnings/events            이벤트 조회
  POST /api/predict                    라벨 미완성 행 예측
  POST /api/model/train                섹터별 학습
  GET  /api/model/status               모델 상태
  GET  /api/positions                  대시보드(시작가/현재가/예측가/위치%/경과%)

토큰·잔고·주문은 기존 /auth, /account, /auto-trade 재사용 (여기서 다루지 않음).
무거운 작업(수집/학습/예측/현재가)은 동기 def 로 두어 FastAPI 스레드풀에서 실행.
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import earnings_repo, earnings_service

logger = logging.getLogger("earnings_router")
router = APIRouter(prefix="/api", tags=["earnings"])


# ── 요청 모델 ────────────────────────────────────────────────

class HistoryCollectReq(BaseModel):
    universe: str = "SP500"
    tickers: Optional[list[str]] = None
    limit: int = 20            # universe 사용 시 적재할 종목 수 상한
    max_per_ticker: int = 8    # 종목당 과거 분기 수


class TodayCollectReq(BaseModel):
    date: Optional[str] = None
    tickers: Optional[list[str]] = None


class PredictReq(BaseModel):
    scope: str = "missing_label"   # missing_label | all


class TrainReq(BaseModel):
    min_samples: int = 30


# ── 헬퍼 ─────────────────────────────────────────────────────

def _sp500_tickers(limit: int) -> list[tuple[str, str]]:
    """(ticker, sector) 목록. fetch_sp500_list 는 async 라 동기 래핑."""
    import asyncio
    from services.sp500_list_service import fetch_sp500_list
    stocks = asyncio.run(fetch_sp500_list())
    return [(s.ticker, s.sector) for s in stocks[:limit]]


# ── 초기 1회 ─────────────────────────────────────────────────

@router.post("/earnings/history/collect", summary="과거 실적·주가 배치 적재")
def history_collect(req: HistoryCollectReq):
    tickers = req.tickers or [t for t, _ in _sp500_tickers(req.limit)]
    if not tickers:
        raise HTTPException(400, "적재할 종목이 없습니다 (tickers 또는 universe 확인)")
    result = earnings_service.collect_history(tickers, max_per_ticker=req.max_per_ticker)
    return {"status": "ok", **result}


# ── 일일 루프 ────────────────────────────────────────────────

@router.get("/earnings/calendar", summary="오늘 발표 예정 종목 (DB 기준)")
def calendar(
    date: str = Query(default=None, description="YYYY-MM-DD (기본: 오늘 UTC)"),
    universe: str = "SP500",
):
    day = date or datetime.utcnow().strftime("%Y-%m-%d")
    rows = earnings_repo.list_events_for_date(day)
    tickers = sorted({r["ticker"] for r in rows})
    return {
        "date": day,
        "count": len(tickers),
        "tickers": tickers,
        "status": "ok" if tickers else "no_earnings",  # 빈 목록 → 조기 종료
    }


@router.post("/earnings/today/collect", summary="오늘자 실적 수집·저장")
def today_collect(req: TodayCollectReq):
    day = req.date or datetime.utcnow().strftime("%Y-%m-%d")
    if not req.tickers:
        raise HTTPException(
            400,
            "tickers 가 필요합니다. 실시간 캘린더 피드는 MVP 미포함이므로 "
            "발표 종목 티커를 명시하세요 (예: [\"AAPL\",\"MSFT\"]).",
        )
    collected, failed = 0, []
    for t in req.tickers:
        try:
            if earnings_service.collect_event(t, earnings_date=day):
                collected += 1
        except Exception as e:
            logger.warning(f"[earnings] today_collect {t} 실패: {e}")
            failed.append(t)
    return {"status": "ok", "date": day, "collected": collected, "failed": failed}


@router.get("/earnings/events", summary="이벤트 테이블 조회")
def events(
    ticker: str = Query(default=None),
    sector: str = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    return {"items": earnings_repo.list_events(ticker=ticker, sector=sector, limit=limit)}


@router.post("/predict", summary="라벨 미완성 행 예측값 채우기")
def predict(req: PredictReq):
    return {"status": "ok", **earnings_service.predict(scope=req.scope)}


# ── 학습 ─────────────────────────────────────────────────────

@router.post("/model/train", summary="섹터별 모델 학습")
def model_train(req: TrainReq):
    return {"status": "ok", **earnings_service.train(min_samples=req.min_samples)}


@router.get("/model/status", summary="학습 상태·모델 목록")
def model_status(limit: int = Query(default=50, ge=1, le=200)):
    models = earnings_repo.list_earnings_models(limit)
    sectors = sorted({m.get("gics_sector") for m in models if m.get("gics_sector")})
    return {"count": len(models), "sectors": sectors, "models": models}


# ── 대시보드 ─────────────────────────────────────────────────

@router.get("/positions", summary="대시보드용 포지션 목록")
def positions(limit: int = Query(default=100, ge=1, le=500)):
    return {"items": earnings_service.get_positions(limit)}
