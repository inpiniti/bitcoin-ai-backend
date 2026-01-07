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
        # Motia Python API Step의 request 구조 파싱
        # req가 dict일 수도 있고, object일 수도 있음
        logging.info(f"[Forecast API] Received req type: {type(req)}")
        
        # 다양한 구조 처리
        if isinstance(req, dict):
            body = req.get("body", req)
        elif hasattr(req, 'body'):
            body = req.body
        else:
            body = req
        
        # body가 또 다른 레벨일 수 있음
        if isinstance(body, dict) and "body" in body:
            body = body["body"]
            
        logging.info(f"[Forecast API] Parsed body type: {type(body)}, keys: {body.keys() if isinstance(body, dict) else 'N/A'}")
        
        data = body.get("data", []) if isinstance(body, dict) else []
        symbol = body.get("symbol", "BTC-USD") if isinstance(body, dict) else "BTC-USD"
        last_date = body.get("lastDate") if isinstance(body, dict) else None
        
        if not data:
            logging.error(f"[Forecast API] No data in body. Body content: {str(body)[:500]}")
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
