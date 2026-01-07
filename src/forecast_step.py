import logging
import torch
import numpy as np
import pandas as pd
import timesfm

# 전역 변수로 모델 캐싱
tfm_model = None

# GPU 사용 설정
torch.set_float32_matmul_precision("high")

def get_model():
    global tfm_model
    if tfm_model is None:
        logging.info("Loading TimesFM 2.5 model...")
        try:
            # TimesFM 2.5 PyTorch 버전
            tfm_model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
                "google/timesfm-2.5-200m-pytorch"
            )
            tfm_model.compile(
                timesfm.ForecastConfig(
                    max_context=1024,
                    max_horizon=128,
                    normalize_inputs=True,
                )
            )
            logging.info("TimesFM 2.5 model loaded successfully.")
        except Exception as e:
            logging.error(f"Failed to load TimesFM: {str(e)}")
            raise
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
        
        logging.info("[Forecast API] Running TimesFM 2.5 inference...")
        
        # TimesFM 2.5 API: model.forecast(horizon, inputs=[...])
        horizon = 24  # 24시간 예측
        point_forecast, quantile_forecast = tfm.forecast(
            horizon=horizon,
            inputs=[input_data],  # 리스트로 감싸서 전달
        )
        
        # 결과 변환: numpy array -> list of dicts
        forecast_values = point_forecast[0].tolist()  # 첫 번째 입력에 대한 예측
        
        # 마지막 날짜 기준으로 예측 날짜 생성
        from datetime import datetime, timedelta
        base_date = datetime.fromisoformat(last_date.replace('Z', '+00:00')) if last_date else datetime.now()
        
        result_list = []
        for i, val in enumerate(forecast_values):
            forecast_date = base_date + timedelta(hours=i+1)
            result_list.append({
                "ds": forecast_date.isoformat(),
                "y": float(val),
                "timesfm": float(val)
            })
        
        logging.info(f"[Forecast API] Completed. Generated {len(result_list)} predictions.")
        
        return {
            "status": 200,
            "body": {
                "status": "success",
                "model": "TimesFM-2.5-200m",
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
