import logging
import numpy as np
import pandas as pd
from timesfm import TimesFm, TimesFmHparams

# 전역 변수로 모델 캐싱
tfm_model = None

def get_model():
    global tfm_model
    if tfm_model is None:
        logging.info("Loading TimesFM v2.0 model...")
        hparams = TimesFmHparams(
            context_len=512,
            horizon_len=128,
            input_patch_len=32,
            output_patch_len=128,
            num_layers=20,
            model_dims=1280,
            backend="cpu",
        )
        tfm_model = TimesFm(
            hparams=hparams,
            checkpoint="google/timesfm-1.0-200m",
        )
        logging.info("TimesFM model loaded successfully.")
    return tfm_model

# API 타입으로 변경 - 동기적 HTTP 호출 가능
config = {
    "name": "bitcoin-forecast-api",
    "type": "api",
    "path": "/internal/forecast",
    "method": "POST",
    "flows": ["bitcoin-forecast-flow"],
    "emits": []
}

async def handler(req, context):
    """
    Bitcoin 가격 예측 API (내부용).
    POST /internal/forecast 로 호출하면 동기적으로 예측 결과 반환.
    """

    try:
        body = req.body if hasattr(req, 'body') else req
        data = body.get("data", [])
        symbol = body.get("symbol", "BTC-USD")
        last_date = body.get("lastDate")
        
        if not data:
            return {"status": 400, "body": {"error": "No data provided"}}

        logging.info(f"[Forecast API] Received {len(data)} points for {symbol}")
        
        input_data = np.array(data, dtype=np.float32)
        tfm = get_model()
        
        df = pd.DataFrame({
            "unique_id": [symbol] * len(input_data),
            "ds": pd.date_range(start="2024-01-01", periods=len(input_data), freq="H"),
            "y": input_data
        })
        
        logging.info("[Forecast API] Running TimesFM inference...")
        forecast_df = tfm.forecast_on_df(
            inputs=df,
            freq="H",
            value_name="y",
        )
        
        result_list = forecast_df.to_dict(orient="records")
        for item in result_list:
            if isinstance(item.get("ds"), pd.Timestamp):
                item["ds"] = item["ds"].isoformat()
        
        logging.info(f"[Forecast API] Completed. Generated {len(result_list)} predictions.")
        
        return {
            "status": 200,
            "body": {
                "status": "success",
                "model": "TimesFM-1.0-200m",
                "symbol": symbol,
                "lastDate": last_date,
                "forecast": result_list,
                "predictionCount": len(result_list)
            }
        }

    except Exception as e:
        logging.error(f"[Forecast API] Error: {str(e)}")
        return {
            "status": 500,
            "body": {"error": str(e)}
        }
