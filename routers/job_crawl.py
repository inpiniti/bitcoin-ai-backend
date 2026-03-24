"""
#46 #48 채용공고 크롤러 라우터

- POST /job-crawl/run   : 수동 즉시 실행
- GET  /job-crawl/listings : 최근 공고 조회
"""
import logging
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("job_crawl_router")

router = APIRouter(prefix="/job-crawl", tags=["job-crawl"])


@router.post(
    "/run",
    summary="채용공고 크롤링 수동 실행",
    description="사람인 + 원티드 크롤링 즉시 실행 후 카카오로 미발송 공고 전송.",
)
async def run_job_crawl():
    """#48 수동 실행 엔드포인트 - 테스트 및 즉시 발송용"""
    from main import _scheduled_job_crawl
    try:
        await _scheduled_job_crawl()
        return {"status": "ok", "message": "크롤링 및 카카오 발송 완료"}
    except Exception as e:
        logger.exception(f"[JobCrawl Router] 수동 실행 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/listings",
    summary="최근 채용공고 조회",
    description="저장된 채용공고 목록 조회 (최신순).",
)
async def get_listings(limit: int = Query(default=50, ge=1, le=200)):
    """#48 크롤링 결과 조회 엔드포인트"""
    from services.supabase_service import get_job_listings
    try:
        jobs = await get_job_listings(limit=limit)
        return {"count": len(jobs), "jobs": jobs}
    except Exception as e:
        logger.exception(f"[JobCrawl Router] 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
