"""
투자 거장 9인의 실시간 종목 조언 API 라우터
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from services.advice_service import generate_advice_stream

logger = logging.getLogger("advice_router")
router = APIRouter(prefix="/api/analysis", tags=["advice"])

@router.get("/advice/stream")
async def get_advice_stream(ticker: str = Query(..., description="분석할 종목의 티커 코드 (예: AAPL, TSLA)")):
    """
    특정 종목에 대해 글로벌 매크로, 재무제표 및 관련 뉴스를 9인의 전설적 투자 거장들의 시각으로
    분석하고 조언을 실시간 Server-Sent Events (SSE) 형식으로 스트리밍합니다.
    """
    if not ticker:
        raise HTTPException(status_code=400, detail="티커(ticker) 파라미터가 누락되었습니다.")
        
    clean_ticker = ticker.strip().upper()
    logger.info(f"[AdviceRouter] GET /advice/stream?ticker={clean_ticker} requested")
    
    try:
        # text/event-stream 형식으로 비동기 스트리밍 반환
        return StreamingResponse(
            generate_advice_stream(clean_ticker),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"  # Nginx 버퍼링 차단 (실시간 전송 보장)
            }
        )
    except Exception as e:
        logger.error(f"[AdviceRouter] Failed to initialize advice stream: {e}")
        from services.error_log_service import log_error_to_db
        log_error_to_db("router_get_advice_stream_init", e, {"ticker": clean_ticker})
        raise HTTPException(status_code=500, detail=f"스트리밍 분석 초기화 실패: {str(e)}")
