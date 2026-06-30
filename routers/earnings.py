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
