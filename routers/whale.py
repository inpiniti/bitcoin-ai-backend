"""
POST /v1/whale  - 고래 수급 분석
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import yahoo_service, whale_service

router = APIRouter()
logger = logging.getLogger("router.whale")


class WhaleRequest(BaseModel):
    symbol: str = "BTC-USD"
    interval: str = "day"  # 수급 분석은 일봉 권장


@router.post("/v1/whale")
async def whale(body: WhaleRequest):
    try:
        market_data = await yahoo_service.fetch_for_whale(body.symbol, body.interval)
        result = whale_service.analyze_and_format(body.symbol, market_data)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"[/v1/whale] 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
