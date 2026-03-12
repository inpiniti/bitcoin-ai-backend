"""
POST /v1/forecast  - TimesFM 가격 예측
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import yahoo_service, forecast_service

router = APIRouter()
logger = logging.getLogger("router.forecast")


class ForecastRequest(BaseModel):
    symbol: str = "BTC-USD"
    interval: str = "hour"  # "day" | "hour" | "minute"


@router.post("/v1/forecast")
async def forecast(body: ForecastRequest):
    if body.interval not in ("day", "hour", "minute"):
        raise HTTPException(status_code=400, detail='interval must be "day", "hour", or "minute"')

    try:
        prices, last_date = await yahoo_service.fetch_for_forecast(body.symbol, body.interval)
        result = forecast_service.run_forecast(body.symbol, body.interval, prices, last_date)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"[/v1/forecast] 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
