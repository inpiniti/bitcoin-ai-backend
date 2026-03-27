"""
#64 뉴스 서비스 라우터

GET  /news?date=YYYY-MM-DD   날짜별 뉴스 목록 (영향 종목 포함)
POST /news/crawl             수동 크롤링 즉시 실행
POST /news/analyze           수동 AI 분석 즉시 실행
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("news_router")
router = APIRouter(prefix="/news", tags=["news"])

KST = ZoneInfo("Asia/Seoul")


@router.get(
    "",
    summary="날짜별 뉴스 목록 조회",
    description="날짜(KST 기준)로 뉴스 목록과 영향 종목을 조회합니다. date 미입력 시 오늘 날짜.",
)
async def get_news(
    date: str = Query(default=None, description="조회 날짜 (YYYY-MM-DD). 기본값: 오늘"),
    limit: int = Query(default=50, ge=1, le=200),
):
    from services.supabase_service import get_news_by_date
    news_date = date or datetime.now(KST).strftime("%Y-%m-%d")
    try:
        items = await get_news_by_date(news_date, limit=limit)
        return {"date": news_date, "count": len(items), "items": items}
    except Exception as e:
        logger.exception(f"[News] 뉴스 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/crawl",
    summary="뉴스 크롤링 수동 실행",
    description="즉시 크롤링 → 증권 필터 → Supabase 저장.",
)
async def run_news_crawl():
    from main import _scheduled_news_crawl
    try:
        await _scheduled_news_crawl()
        return {"status": "ok", "message": "크롤링 완료"}
    except Exception as e:
        logger.exception(f"[News] 크롤링 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/analyze",
    summary="뉴스 AI 분석 수동 실행",
    description="미분석 뉴스를 Gemini로 분석하여 영향 종목/시장 저장.",
)
async def run_news_analyze():
    from services.news_analysis_service import analyze_unanalyzed_news
    from services.gemini_key_manager import get_key_manager
    try:
        key_mgr = get_key_manager()
        count = await analyze_unanalyzed_news(key_mgr)
        return {"status": "ok", "analyzed": count}
    except Exception as e:
        logger.exception(f"[News] AI 분석 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
