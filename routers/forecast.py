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


@router.post(
    "/v1/forecast",
    summary="가격 예측 (TimesFM)",
    description="""
**Google TimesFM 딥러닝 모델**을 사용하여 주식·코인의 미래 가격을 예측합니다.

### 요청 파라미터
- `symbol`: Yahoo Finance 티커 (예: `BTC-USD`, `AAPL`, `TSLA`)
- `interval`: 예측 단위
  - `day` — 일봉 기준 예측 (주식 권장)
  - `hour` — 시간봉 기준 예측
  - `minute` — 분봉 기준 예측

### 처리 흐름
1. Yahoo Finance에서 과거 가격 데이터 수집
2. TimesFM 모델로 미래 가격 예측
3. 예측값·신뢰구간 반환

### 응답 예시
```json
{
  "symbol": "AAPL",
  "interval": "day",
  "last_date": "2024-06-01",
  "forecast": [182.3, 184.1, 186.0],
  "lower": [178.0, 179.5, 181.2],
  "upper": [186.5, 188.7, 190.8]
}
```
""",
    tags=["예측"],
)
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
