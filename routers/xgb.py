"""
POST /v1/xgb/train   - XGBoost 학습
POST /v1/xgb/predict - XGBoost 예측
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import xgb_service

router = APIRouter()
logger = logging.getLogger("router.xgb")


class TrainRequest(BaseModel):
    datasetId: str
    modelName: str | None = None


class PredictRequest(BaseModel):
    modelId: str
    features: list | None = None
    datasetId: str | None = None


@router.post("/v1/xgb/train")
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


@router.post("/v1/xgb/predict")
async def predict(body: PredictRequest):
    if not body.modelId or (not body.features and not body.datasetId):
        raise HTTPException(
            status_code=400,
            detail="modelId and (features or datasetId) are required",
        )

    try:
        result = await xgb_service.predict(body.modelId, body.features, body.datasetId)
        return result
    except Exception as e:
        logger.exception(f"[/v1/xgb/predict] 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
