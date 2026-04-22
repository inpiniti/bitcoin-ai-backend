"""
S&P 500 영향도 분석 라우터

GET  /sp500/impact?date=YYYY-MM-DD   날짜별 영향도 조회
GET  /sp500/meta?date=YYYY-MM-DD     날짜별 분석 메타 조회
POST /sp500/run                       수동 분석 실행
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("sp500_router")
router = APIRouter(prefix="/sp500", tags=["sp500"])

KST = ZoneInfo("Asia/Seoul")


@router.get(
    "/impact",
    summary="S&P 500 일별 영향도 조회",
    description="뉴스 기반 S&P 500 종목 영향도를 날짜별로 조회합니다.",
)
async def get_impact(
    date: str = Query(default=None, description="조회 날짜 (YYYY-MM-DD). 기본값: 오늘(UTC)"),
    sector: str = Query(default=None, description="섹터 필터 (예: Technology)"),
    direction: str = Query(default=None, description="방향 필터: bullish/bearish/neutral"),
    limit: int = Query(default=600, ge=1, le=600),
):
    from services.supabase_service import get_sp500_daily_impact
    analysis_date = date or datetime.utcnow().strftime("%Y-%m-%d")
    try:
        items = await get_sp500_daily_impact(
            analysis_date=analysis_date,
            sector=sector,
            direction=direction,
            limit=limit,
        )
        return {"date": analysis_date, "count": len(items), "items": items}
    except Exception as e:
        logger.exception(f"[SP500] 영향도 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/meta",
    summary="S&P 500 분석 메타 조회",
    description="분석 날짜별 뉴스 수, 상승/하락/중립 종목 수 등 요약 정보 조회.",
)
async def get_meta(
    date: str = Query(default=None, description="조회 날짜 (YYYY-MM-DD). 기본값: 오늘(UTC)"),
):
    from services.supabase_service import get_sp500_analysis_meta
    analysis_date = date or datetime.utcnow().strftime("%Y-%m-%d")
    try:
        meta = await get_sp500_analysis_meta(analysis_date)
        if meta is None:
            return {"date": analysis_date, "status": "not_found", "meta": None}
        return {"date": analysis_date, "status": "ok", "meta": meta}
    except Exception as e:
        logger.exception(f"[SP500] 메타 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/run",
    summary="S&P 500 영향도 분석 수동 실행",
    description="뉴스 크롤링 → AI 분석 → Supabase 저장 파이프라인을 즉시 실행합니다.",
)
async def run_analysis(
    hours: int = Query(default=24, ge=1, le=72, description="크롤링할 뉴스 기간 (시간)"),
):
    from services.sp500_analysis_service import run_sp500_analysis
    from services.gemini_key_manager import get_key_manager
    try:
        key_mgr = get_key_manager()
        result = await run_sp500_analysis(key_mgr, hours=hours)
        return result
    except Exception as e:
        logger.exception(f"[SP500] 분석 실행 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/model-status",
    summary="시계열 모델 로드 상태 조회",
    description="TimesFM / Chronos / Moirai 모델의 로드 성공 여부와 오류 메시지를 반환합니다.",
)
async def get_model_status():
    from services import timesfm_service, forecast_models_service
    return {
        "timesfm": timesfm_service.get_load_status(),
        "chronos": {
            "loaded": forecast_models_service._chronos_pipeline is not None,
            "attempted": forecast_models_service._chronos_attempted,
        },
        "moirai": {
            "loaded": forecast_models_service._moirai_model is not None,
            "attempted": forecast_models_service._moirai_attempted,
        },
    }


@router.post(
    "/model-reset",
    summary="시계열 모델 싱글턴 초기화",
    description="로드 실패한 모델을 초기화해 다음 파이프라인 실행 시 재시도하도록 합니다.",
)
async def reset_models():
    from services import timesfm_service, forecast_models_service
    import threading
    timesfm_service.reset_model()
    with forecast_models_service._chronos_lock:
        forecast_models_service._chronos_pipeline = None
        forecast_models_service._chronos_attempted = False
    with forecast_models_service._moirai_lock:
        forecast_models_service._moirai_model = None
        forecast_models_service._moirai_attempted = False
    logger.info("[SP500] 시계열 모델 싱글턴 초기화 완료")
    return {"status": "ok", "message": "TimesFM / Chronos / Moirai 모델 초기화 완료. 다음 실행 시 재로드합니다."}
