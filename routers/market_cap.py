"""
POST /v1/market-cap  - AI 시총 유추
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import market_cap_service

router = APIRouter()
logger = logging.getLogger("router.market_cap")


class MarketCapRequest(BaseModel):
    ticker: str


@router.post("/v1/market-cap")
async def market_cap(body: MarketCapRequest):
    ticker = body.ticker.strip().upper()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")

    try:
        result = await market_cap_service.run_market_cap(ticker)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[/v1/market-cap] 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
