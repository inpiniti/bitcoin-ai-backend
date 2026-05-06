"""
포트폴리오 데이터 라우터

GET /portfolio           전체 포트폴리오 데이터 조회
GET /portfolio/person    투자자별 포트폴리오 조회
GET /portfolio/stock     종목별 포트폴리오 조회
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

logger = logging.getLogger("portfolio_router")
router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get(
    "",
    summary="포트폴리오 전체 데이터 조회",
    description="투자자별, 종목별 포트폴리오 데이터를 조회합니다.",
)
async def get_portfolio(
    person_id: Optional[int] = Query(None, description="투자자 ID (필터)"),
    stock: Optional[str] = Query(None, description="종목 (필터)"),
    limit: int = Query(default=500, ge=1, le=500, description="최대 결과 수"),
):
    """
    포트폴리오 전체 데이터 반환
    based_on_person: 투자자별 정보
    based_on_stock: 종목별 정보
    """
    try:
        from services.supabase_service import get_portfolio_data

        raw_data = await get_portfolio_data(
            person_id=person_id,
            stock=stock,
            limit=limit,
        )

        # 데이터 형식 정규화
        based_on_stock = []
        for item in raw_data.get("based_on_stock", []):
            based_on_stock.append({
                "stock": item.get("stock") or item.get("ticker"),
                "person": item.get("person") or [],
                "person_count": item.get("person_count") or 0,
                "sum_ratio": item.get("sum_ratio") or 0,
                "dcf_vs_market_cap_pct": item.get("dcf_vs_market_cap_pct"),
            })

        return {
            "based_on_person": raw_data.get("based_on_person", []),
            "based_on_stock": based_on_stock,
        }
    except Exception as e:
        logger.exception(f"[Portfolio] 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/person",
    summary="투자자별 포트폴리오 조회",
    description="유명 투자자들의 보유 현황을 조회합니다.",
)
async def get_portfolio_by_person(
    person_id: Optional[int] = Query(None, description="특정 투자자 ID"),
    limit: int = Query(default=100, ge=1, le=100),
):
    """투자자별 포트폴리오 데이터 반환"""
    try:
        from services.supabase_service import get_portfolio_by_person

        data = await get_portfolio_by_person(person_id=person_id, limit=limit)
        return {"count": len(data), "data": data}
    except Exception as e:
        logger.exception(f"[Portfolio] 투자자 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/stock",
    summary="종목별 포트폴리오 조회",
    description="특정 종목을 보유한 투자자 정보를 조회합니다.",
)
async def get_portfolio_by_stock(
    stock: str = Query(..., description="종목 코드 (예: AAPL)"),
    limit: int = Query(default=100, ge=1, le=100),
):
    """종목별 포트폴리오 데이터 반환"""
    try:
        from services.supabase_service import get_portfolio_by_stock

        data = await get_portfolio_by_stock(stock=stock, limit=limit)
        return {"stock": stock, "count": len(data), "data": data}
    except Exception as e:
        logger.exception(f"[Portfolio] 종목 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
