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
  GET  /api/earnings/api-logs          API 통신 이력 로그 조회
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
from starlette.concurrency import run_in_threadpool, iterate_in_threadpool

from services import earnings_repo, earnings_service
from services.earnings_logger import log_earnings_api
from services.supabase_service import SUPABASE_URL, _headers, _check_config

# 미사용 import 제거 (이미 위에서 정의됨)
# import httpx

logger = logging.getLogger("earnings_router")
router = APIRouter(prefix="/api", tags=["earnings"])


# ── 요청 모델 ────────────────────────────────────────────────

class HistoryCollectReq(BaseModel):
    universe: str = "SP500"
    tickers: Optional[list[str]] = None
    limit: int = 20            # universe 사용 시 적재할 종목 수 상한
    max_per_ticker: int = 8    # 종목당 과거 분기 수
    skip_existing: bool = True # 이미 적재된 종목 건너뛰기 (이어하기)


class TodayCollectReq(BaseModel):
    date: Optional[str] = None
    tickers: Optional[list[str]] = None


class PredictReq(BaseModel):
    scope: str = "missing_label"   # missing_label | all


class TrainReq(BaseModel):
    min_samples: int = 30


# ── 헬퍼 ─────────────────────────────────────────────────────

def _sp500_tickers_sync(limit: int) -> list[tuple[str, str]]:
    """(ticker, sector) 목록. 동기 함수(스레드풀에서 실행)."""
    import asyncio
    from services.sp500_list_service import fetch_sp500_list

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        stocks = loop.run_until_complete(fetch_sp500_list())
        return [(s.ticker, s.sector) for s in stocks[:limit]]
    finally:
        if loop:
            loop.close()


# ── 백그라운드 수집 작업 상태 (서버 프로세스 소유) ─────────────
#   브라우저를 껐다 켜도 이 상태를 /status 로 조회해 진행률을 이어서 표시.
#   서버 재시작 시에만 초기화됨 → skip_existing 으로 이어하기.
_collect_job: dict = {
    "status": "idle",          # idle | running | done | error
    "current": 0,
    "total": 0,
    "ticker": None,
    "collected": 0,
    "total_inserted": 0,
    "failed": [],
    "started_at": None,
    "finished_at": None,
    "message": None,
}
_collect_task = None   # 백그라운드 태스크 강한 참조 유지 (GC 방지)


async def _run_collect_job(tickers: list[str], max_per_ticker: int, sector_map: dict):
    """백그라운드에서 수집을 끝까지 수행하며 _collect_job 진행 상태를 갱신."""
    def _do():
        for ev in earnings_service.collect_history_iter(tickers, max_per_ticker, sector_map):
            if ev.get("done"):
                _collect_job.update({
                    "status": "done",
                    "collected": ev["collected"],
                    "total_inserted": ev["total_inserted"],
                    "failed": ev["failed"],
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "message": (
                        f"{ev['processed_tickers']}개 처리, "
                        f"{ev['total_inserted']}개 적재, 실패 {len(ev['failed'])}개"
                    ),
                })
            else:
                _collect_job.update({
                    "current": ev["current"],
                    "total": ev["total"],
                    "ticker": ev["ticker"],
                })

    try:
        await run_in_threadpool(_do)
    except Exception as e:
        logger.error(f"[earnings] 백그라운드 수집 실패: {e}", exc_info=True)
        _collect_job.update({
            "status": "error",
            "message": str(e),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
    finally:
        await log_earnings_api(
            api="/api/earnings/history/collect/start",
            inout="in",
            payload={"total": _collect_job.get("total")},
            response={k: _collect_job[k] for k in ("collected", "total_inserted", "failed", "status")},
            status="success" if _collect_job["status"] == "done" else "error",
            error_message=None if _collect_job["status"] == "done" else _collect_job.get("message"),
        )


# ── 초기 1회 ─────────────────────────────────────────────────

@router.post("/earnings/history/collect", summary="과거 실적·주가 배치 적재")
async def history_collect(req: HistoryCollectReq, request: Request):
    payload = req.dict()
    # 동기 작업을 스레드풀에서 실행 — 섹터 정보도 함께 확보
    sector_map: dict = {}
    if req.tickers:
        tickers = req.tickers
    else:
        pairs = await run_in_threadpool(_sp500_tickers_sync, req.limit)
        tickers = [t for t, _ in pairs]
        sector_map = {t: s for t, s in pairs}
    if not tickers:
        raise HTTPException(400, "적재할 종목이 없습니다 (tickers 또는 universe 확인)")

    try:
        # SEC + Yahoo Chart 수집도 스레드풀에서 실행
        result = await run_in_threadpool(
            earnings_service.collect_history,
            tickers,
            req.max_per_ticker,
            sector_map,
        )
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=result,
            status="success"
        )
        return {"status": "ok", **result}
    except Exception as e:
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=None,
            status="error",
            error_message=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/earnings/history/collect/stream", summary="과거 적재 (SSE 실시간 진행률)")
async def history_collect_stream(req: HistoryCollectReq, request: Request):
    """
    503개 전체를 한 번에 수집하되, 종목마다 진행 상황을 SSE로 푸시.
    타임아웃 없이 0 → N 진행률을 실시간 표시.

    SSE 이벤트(각 줄 `data: {json}\\n\\n`):
      진행: {"current":1,"total":503,"ticker":"MMM","saved":7,"status":"ok"}
      완료: {"done":true,"collected":3500,"total_inserted":3500,"failed":[...]}
    """
    payload = req.dict()
    sector_map: dict = {}
    if req.tickers:
        tickers = req.tickers
    else:
        pairs = await run_in_threadpool(_sp500_tickers_sync, req.limit)
        tickers = [t for t, _ in pairs]
        sector_map = {t: s for t, s in pairs}
    if not tickers:
        raise HTTPException(400, "적재할 종목이 없습니다 (tickers 또는 universe 확인)")

    async def event_stream():
        final = None
        try:
            # 동기 제너레이터를 스레드풀에서 비동기로 소비
            sync_gen = earnings_service.collect_history_iter(
                tickers, req.max_per_ticker, sector_map
            )
            async for ev in iterate_in_threadpool(sync_gen):
                if ev.get("done"):
                    final = ev
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:
            logger.error(f"[earnings] SSE 수집 실패: {e}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            # 최종 결과를 통신 로그에 기록 (스트림 종료 후)
            await log_earnings_api(
                api=str(request.url.path),
                inout="in",
                payload=payload,
                response=final,
                status="success" if final else "error",
                error_message=None if final else "스트림 중단",
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # 프록시 버퍼링 비활성화 (실시간 전달)
        },
    )


@router.post("/earnings/history/collect/start", summary="과거 적재 백그라운드 시작")
async def history_collect_start(req: HistoryCollectReq, request: Request):
    """
    수집을 서버 백그라운드에서 시작하고 즉시 응답.
    브라우저를 껐다 켜도 /status 로 진행률을 이어서 확인 가능.
    이미 실행 중이면 현재 상태를 반환(중복 시작 방지).
    skip_existing=True 면 이미 적재된 종목은 건너뜀(이어하기).
    """
    if _collect_job["status"] == "running":
        return {"status": "already_running", **_collect_job}

    # 대상 종목 + 섹터
    sector_map: dict = {}
    if req.tickers:
        tickers = req.tickers
    else:
        pairs = await run_in_threadpool(_sp500_tickers_sync, req.limit)
        tickers = [t for t, _ in pairs]
        sector_map = {t: s for t, s in pairs}
    if not tickers:
        raise HTTPException(400, "적재할 종목이 없습니다 (tickers 또는 universe 확인)")

    total_requested = len(tickers)
    skipped = 0
    if req.skip_existing:
        existing = await run_in_threadpool(earnings_repo.list_collected_tickers)
        before = len(tickers)
        tickers = [t for t in tickers if t not in existing]
        skipped = before - len(tickers)

    if not tickers:
        _collect_job.update({
            "status": "done",
            "current": 0, "total": 0, "ticker": None,
            "collected": 0, "total_inserted": 0, "failed": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "message": f"이미 모두 적재됨 (전체 {total_requested}개 스킵)",
        })
        return {"status": "done", **_collect_job}

    # 상태 초기화 + 백그라운드 시작
    _collect_job.update({
        "status": "running",
        "current": 0,
        "total": len(tickers),
        "ticker": None,
        "collected": 0,
        "total_inserted": 0,
        "failed": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "message": f"수집 시작 ({len(tickers)}개 대상, {skipped}개 스킵)",
    })
    global _collect_task
    _collect_task = asyncio.create_task(_run_collect_job(tickers, req.max_per_ticker, sector_map))
    return {
        "status": "started",
        "total": len(tickers),
        "skipped": skipped,
        "total_requested": total_requested,
    }


@router.get("/earnings/history/collect/status", summary="과거 적재 진행 상태 조회")
async def history_collect_status():
    """현재 백그라운드 수집 진행 상태. 브라우저 재접속 시 진행률 복원용."""
    return _collect_job


# ── 일일 루프 ────────────────────────────────────────────────

@router.get("/earnings/calendar", summary="오늘 발표 예정 종목 (DB 기준)")
async def calendar(
    request: Request,
    date: str = Query(default=None, description="YYYY-MM-DD (기본: 오늘 UTC)"),
    universe: str = "SP500",
):
    day = date or datetime.utcnow().strftime("%Y-%m-%d")
    payload = {"date": day, "universe": universe}
    try:
        rows = earnings_repo.list_events_for_date(day)
        tickers = sorted({r["ticker"] for r in rows})
        result = {
            "date": day,
            "count": len(tickers),
            "tickers": tickers,
            "status": "ok" if tickers else "no_earnings",
        }
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=result,
            status="success" if tickers else "empty"
        )
        return result
    except Exception as e:
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=None,
            status="error",
            error_message=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/earnings/today/collect", summary="오늘자 실적 수집·저장")
async def today_collect(req: TodayCollectReq, request: Request):
    payload = req.dict()
    day = req.date or datetime.utcnow().strftime("%Y-%m-%d")
    if not req.tickers:
        raise HTTPException(
            400,
            "tickers 가 필요합니다. 실시간 캘린더 피드는 MVP 미포함이므로 "
            "발표 종목 티커를 명시하세요 (예: [\"AAPL\",\"MSFT\"]).",
        )
    
    try:
        collected, failed = 0, []
        for t in req.tickers:
            try:
                # yfinance API를 서버에서 직접 호출함 (out)
                api_call_url = f"yfinance.Ticker({t})"
                await log_earnings_api(
                    api=api_call_url,
                    inout="out",
                    payload={"ticker": t},
                    response={"message": "Fetching from yfinance..."},
                    status="success"
                )
                
                if earnings_service.collect_event(t, earnings_date=day):
                    collected += 1
            except Exception as e:
                logger.warning(f"[earnings] today_collect {t} 실패: {e}")
                failed.append(t)
                await log_earnings_api(
                    api=f"yfinance.Ticker({t})",
                    inout="out",
                    payload={"ticker": t},
                    response=None,
                    status="error",
                    error_message=str(e)
                )
                
        result = {"status": "ok", "date": day, "collected": collected, "failed": failed}
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=result,
            status="success"
        )
        return result
    except Exception as e:
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=None,
            status="error",
            error_message=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/earnings/events", summary="이벤트 테이블 조회")
async def events(
    request: Request,
    ticker: str = Query(default=None),
    sector: str = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
):
    payload = {"ticker": ticker, "sector": sector, "limit": limit}
    try:
        items = earnings_repo.list_events(ticker=ticker, sector=sector, limit=limit)
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response={"count": len(items)},
            status="success" if items else "empty"
        )
        return {"items": items}
    except Exception as e:
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=None,
            status="error",
            error_message=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/predict", summary="라벨 미완성 행 예측값 채우기")
async def predict(req: PredictReq, request: Request):
    payload = req.dict()
    try:
        result = earnings_service.predict(scope=req.scope)
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=result,
            status="success"
        )
        return {"status": "ok", **result}
    except Exception as e:
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=None,
            status="error",
            error_message=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


# ── 학습 ─────────────────────────────────────────────────────

@router.post("/model/train", summary="섹터별 모델 학습")
async def model_train(req: TrainReq, request: Request):
    payload = req.dict()
    try:
        result = earnings_service.train(min_samples=req.min_samples)
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=result,
            status="success"
        )
        return {"status": "ok", **result}
    except Exception as e:
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=None,
            status="error",
            error_message=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/model/status", summary="학습 상태·모델 목록")
async def model_status(request: Request, limit: int = Query(default=50, ge=1, le=200)):
    payload = {"limit": limit}
    try:
        models = earnings_repo.list_earnings_models(limit)
        sectors = sorted({m.get("gics_sector") for m in models if m.get("gics_sector")})
        result = {"count": len(models), "sectors": sectors, "models": models}
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response={"count": len(models)},
            status="success" if models else "empty"
        )
        return result
    except Exception as e:
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=None,
            status="error",
            error_message=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


# ── 대시보드 ─────────────────────────────────────────────────

@router.get("/positions", summary="대시보드용 포지션 목록")
async def positions(request: Request, limit: int = Query(default=100, ge=1, le=500)):
    payload = {"limit": limit}
    try:
        items = earnings_service.get_positions(limit)
        
        # 실시간 현재가 변동 시뮬레이션 매핑 추가
        enriched_items = []
        for row in items:
            start_p = float(row.get("start_price") or 100.0)
            predict_p = float(row.get("predict_price") or (start_p * 1.15))
            
            # 실시간 현재가 시뮬레이션
            current_p = start_p * (1.0 + (datetime.now(timezone.utc).second % 10 - 3) * 0.01)
            
            # 가격 위치% = (현재가 - 시작가) / (예측가 - 시작가) * 100
            denom = (predict_p - start_p)
            price_pos_pct = ((current_p - start_p) / denom * 100.0) if denom != 0 else 0.0
            
            enriched_items.append({
                **row,
                "start_price": start_p,
                "current_price": round(current_p, 2),
                "predict_price": round(predict_p, 2),
                "price_position_pct": round(price_pos_pct, 1)
            })
            
        result = {"items": enriched_items}
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response={"count": len(enriched_items)},
            status="success" if enriched_items else "empty"
        )
        return result
    except Exception as e:
        await log_earnings_api(
            api=str(request.url.path),
            inout="in",
            payload=payload,
            response=None,
            status="error",
            error_message=str(e)
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/earnings/api-logs", summary="API 통신 로그 조회")
async def get_api_logs(limit: int = 50):
    try:
        _check_config()
        url = f"{SUPABASE_URL}/rest/v1/earnings_api_logs?select=*&order=created_at.desc&limit={limit}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers())
        if resp.status_code == 200:
            return resp.json()
        raise Exception(resp.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
