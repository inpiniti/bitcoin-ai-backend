"""
포트폴리오 데이터 라우터
potatoinvest의 dataroma/base 패턴을 적용

GET /portfolio?withDetails=true - 포트폴리오 데이터 조회 (SP500 분석과 통합 옵션)
GET /portfolio?refresh=1 - 강제 재생성
"""
import logging
import httpx
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

logger = logging.getLogger("portfolio_router")
router = APIRouter(prefix="/portfolio", tags=["portfolio"])


async def get_supabase_portfolio(use_cache: bool = True, force_refresh: bool = False):
    """
    Supabase에서 포트폴리오 데이터 조회 또는 생성
    potatoinvest의 dataroma/base 패턴 따름
    """
    from services.supabase_service import SUPABASE_URL, SUPABASE_KEY
    from services.portfolio_service import generate_portfolio_base

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.warning("Supabase 설정 없음, 생성된 데이터만 반환")
        return await generate_portfolio_base()

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

    # 1. DB에서 캐시된 portfolio 데이터 조회 (id=1)
    portfolio_data = None
    if use_cache and not force_refresh:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{SUPABASE_URL}/rest/v1/base?id=eq.1&select=json,updated_at",
                    headers=headers,
                )
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    portfolio_data = data[0].get("json")
                    logger.info(f"[Portfolio] DB 캐시 사용 (updated_at: {data[0].get('updated_at')})")
        except Exception as e:
            logger.warning(f"[Portfolio] DB 조회 실패, 재생성: {e}")

    # 2. 캐시 없거나 force_refresh인 경우 새로 생성
    if not portfolio_data:
        logger.info("[Portfolio] 포트폴리오 데이터 생성 중...")
        portfolio_data = await generate_portfolio_base()

        # 3. Supabase에 저장 (선택사항 - 캐시용)
        if use_cache:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    # 기존 데이터 삭제
                    await client.delete(
                        f"{SUPABASE_URL}/rest/v1/base?id=eq.1",
                        headers=headers,
                    )
                    # 새 데이터 삽입
                    await client.post(
                        f"{SUPABASE_URL}/rest/v1/base",
                        headers=headers,
                        json={
                            "id": 1,
                            "json": portfolio_data,
                            "updated_at": None,  # DB 자동 타임스탐프
                        },
                    )
                    logger.info("[Portfolio] DB에 캐시 저장 완료")
            except Exception as e:
                logger.warning(f"[Portfolio] DB 캐시 저장 실패: {e}")

    return portfolio_data


@router.get(
    "",
    summary="포트폴리오 데이터 조회",
    description="투자자별(based_on_person), 종목별(based_on_stock) 포트폴리오를 반환합니다.",
)
async def get_portfolio(
    withDetails: Optional[str] = Query(None, description="SP500 분석과 통합 (true/1)"),
    refresh: Optional[str] = Query(None, description="강제 재생성 (true/1)"),
):
    """
    포트폴리오 데이터 반환

    Response:
    {
      "based_on_person": [
        {
          "no": int,
          "name": str,
          "totalValue": str,
          "totalValueNum": int,
          "portfolio": [{"code": str, "ratio": str}]
        }
      ],
      "based_on_stock": [
        {
          "stock": str,
          "person": [{"no": int, "name": str, "ratio": str}],
          "person_count": int,
          "sum_ratio": float,
          "avg_ratio": float,
          "dcf_vs_market_cap_pct": float (optional, withDetails=true일 때)
        }
      ],
      "meta": {...}
    }
    """
    try:
        force_refresh = refresh in ["1", "true", "True"]
        portfolio = await get_supabase_portfolio(use_cache=True, force_refresh=force_refresh)

        # withDetails=true인 경우 SP500 분석 데이터로 enriching (향후 구현)
        should_enrich = withDetails in ["1", "true", "True"]
        if should_enrich and portfolio.get("based_on_stock"):
            logger.info("[Portfolio] SP500 분석 데이터 enrichment (향후 구현)")

        return portfolio

    except Exception as e:
        logger.exception(f"[Portfolio] 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
