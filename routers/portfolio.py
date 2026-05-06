"""
포트폴리오 데이터 라우터
GET /portfolio - based_on_person, based_on_stock 포맷 반환
"""
import logging
from fastapi import APIRouter, HTTPException
from typing import Optional

logger = logging.getLogger("portfolio_router")
router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get(
    "",
    summary="포트폴리오 데이터 조회",
    description="투자자별(based_on_person), 종목별(based_on_stock) 포트폴리오를 반환합니다.",
)
async def get_portfolio():
    """
    포트폴리오 데이터 반환
    구조: { based_on_person: [...], based_on_stock: [...] }

    based_on_stock 각 항목:
    {
      stock: str,
      person: list,
      person_count: int,
      sum_ratio: float,
      dcf_vs_market_cap_pct: float | null
    }
    """
    try:
        # Supabase portfolio 테이블이 비어있는 경우 빈 배열 반환
        # 향후 Supabase에 데이터 적재 시 쿼리로 변경 가능
        return {
            "based_on_person": [],
            "based_on_stock": [],
        }
    except Exception as e:
        logger.exception(f"[Portfolio] 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
