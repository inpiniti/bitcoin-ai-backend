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


@router.post(
    "/v1/market-cap",
    summary="AI 시가총액 유추",
    description="""
동종 업계 비교·재무 데이터를 기반으로 **AI가 적정 시가총액을 추정**합니다.

### 요청 파라미터
- `ticker`: 분석할 종목 티커 (예: `AAPL`, `TSLA`, `NVDA`)

### 처리 흐름
1. Yahoo Finance에서 재무 데이터(매출, 순이익, PER 등) 수집
2. 동종 업계 평균 지표와 비교
3. AI 추정 적정 시가총액 및 현재 시가총액과의 괴리율 반환

### 응답 예시
```json
{
  "ticker": "AAPL",
  "current_market_cap": 2800000000000,
  "estimated_market_cap": 3100000000000,
  "gap_ratio": 0.107,
  "assessment": "저평가"
}
```
""",
    tags=["시총분석"],
)
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
