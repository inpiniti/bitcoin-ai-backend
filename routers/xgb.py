"""
POST /v1/xgb/train          - XGBoost 학습
POST /v1/xgb/predict        - XGBoost 예측
GET  /v1/xgb/group-tickers  - 그룹 티커 목록 반환
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import xgb_service
from services.data_collector import fetch_tickers_for_group

router = APIRouter()
logger = logging.getLogger("router.xgb")


class TrainRequest(BaseModel):
    datasetId: str
    modelName: str | None = None


class PredictRequest(BaseModel):
    modelId: str
    features: list | None = None
    datasetId: str | None = None
    ticker: str | None = None
    days: int = 2000


@router.post(
    "/v1/xgb/train",
    summary="XGBoost 모델 학습",
    description="""
Supabase `stock_dataset`에 저장된 과거 주가 데이터를 불러와 **XGBoost 분류 모델**을 학습합니다.

### 요청 파라미터
- `datasetId`: Supabase `stock_dataset` 테이블의 ID (필수)
- `modelName`: 저장할 모델 이름 (선택, 미입력 시 자동 생성)

### 처리 흐름
1. Supabase에서 캔들 데이터 로드
2. 기술적 지표(SMA, RSI, 볼린저밴드 등) 계산
3. 매수/매도 라벨 자동 생성 후 XGBoost 학습
4. 학습된 모델을 Supabase Storage(`xgb-models` 버킷)에 저장

### 응답 예시
```json
{
  "success": true,
  "model_id": "xgb_AAPL_20240601",
  "accuracy": 0.72,
  "features": ["sma_20", "rsi_14", "bb_upper"]
}
```
""",
    tags=["XGBoost"],
)
async def train(body: TrainRequest):
    if not body.datasetId:
        raise HTTPException(status_code=400, detail="datasetId is required")

    model_name = body.modelName or f"XGB_Model_{body.datasetId[:8]}"

    try:
        result = await xgb_service.train(body.datasetId, model_name)
        return result
    except Exception as e:
        logger.exception(f"[/v1/xgb/train] 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/v1/xgb/predict",
    summary="XGBoost 매수/매도 예측",
    description="""
학습된 XGBoost 모델로 **현재 시점의 매수·매도 확률**을 예측합니다.

### 요청 파라미터
- `modelId`: 사용할 모델 ID (필수)
- `features`: 직접 전달할 피처 벡터 (선택)
- `datasetId`: 피처를 자동 계산할 데이터셋 ID (선택, `features` 미입력 시 필수)

> `features`와 `datasetId` 중 하나는 반드시 입력해야 합니다.

### 응답 예시
```json
{
  "model_id": "xgb_AAPL_20240601",
  "buy_probability": 0.68,
  "sell_probability": 0.21,
  "signal": "BUY"
}
```
""",
    tags=["XGBoost"],
)
async def predict(body: PredictRequest):
    if not body.modelId or (not body.features and not body.datasetId and not body.ticker):
        raise HTTPException(
            status_code=400,
            detail="modelId and one of (ticker, features, datasetId) are required",
        )

    try:
        result = await xgb_service.predict(body.modelId, body.features, body.datasetId, body.ticker, body.days)
        return result
    except Exception as e:
        logger.exception(f"[/v1/xgb/predict] 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/v1/xgb/group-tickers",
    summary="그룹 티커 목록 조회",
    tags=["XGBoost"],
)
async def group_tickers(group: str = Query(..., description="그룹 키 (sp500, qqq, usall, kospi200, kosdaq150)")):
    """학습에 사용되는 그룹의 티커 목록을 반환합니다."""
    try:
        tickers = await fetch_tickers_for_group(group)
        return {"group": group, "tickers": tickers, "count": len(tickers)}
    except Exception as e:
        logger.exception(f"[/v1/xgb/group-tickers] 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
