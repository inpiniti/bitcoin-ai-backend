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


@router.post(
    "/v1/whale",
    summary="고래 수급 분석",
    description="""
**대규모 자금(고래) 유입·유출 신호**를 분석하여 수급 강도를 판단합니다.

### 요청 파라미터
- `symbol`: Yahoo Finance 티커 (예: `BTC-USD`, `AAPL`)
- `interval`: 분석 단위 (`day` 권장 — 일봉 기준 분석)

### 분석 항목
- 거래량 급증 감지 (평균 대비 N배 이상)
- 가격·거래량 방향성 일치 여부 (매집 vs 분산)
- 수급 강도 점수 및 신호 (BUY / SELL / NEUTRAL)

### 응답 예시
```json
{
  "symbol": "AAPL",
  "signal": "BUY",
  "score": 72.5,
  "reason": "거래량 2.8배 급증 + 가격 상승 동반"
}
```
""",
    tags=["수급분석"],
)
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
