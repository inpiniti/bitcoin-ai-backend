import logging
import numpy as np
import pandas as pd
from timesfm import TimesFm

# 전역 변수로 모델 캐싱 (서버 시작 시 또는 첫 요청 시 로드)
tfm_model = None

def get_model():
    global tfm_model
    if tfm_model is None:
        logging.info("Loading TimesFM model from Hugging Face Hub...")
        # TimesFM 모델 초기화 및 체크포인트 로드
        # google/timesfm-1.0-200m 기준 설정
        tfm_model = TimesFm(
            context_len=512,
            horizon_len=128,
            input_patch_len=32,
            output_patch_len=128,
            num_layers=20,
            model_dims=1280,
            backend="cpu",  # CPU 환경 기준 (Hugging Face Spaces 무료 티어 등)
        )
        tfm_model.load_from_checkpoint(repo_id="google/timesfm-1.0-200m")
        logging.info("TimesFM model loaded successfully.")
    return tfm_model

# Motia Python Step Configuration
config = {
    "name": "bitcoin-forecast",
    "type": "event",
    "subscribes": ["bitcoin-forecast"],
    "flows": ["bitcoin-forecast-flow"],
    "emits": ["format-forecast-result"]
}

def handler(event, context):
    """
    Bitcoin 가격 예측을 수행하는 Python Step.
    TimesFM 모델을 사용하여 시계열 데이터를 분석합니다.
    """
    try:
        data = event.get("data", [])
        symbol = event.get("symbol", "BTC-USD")
        
        if not data:
            return {"status": "error", "message": "No data provided"}

        logging.info(f"Received data for forecasting {symbol}: {len(data)} points")
        
        # 1. 데이터 전처리
        input_data = np.array(data, dtype=np.float32)
        
        # 모델 로드
        tfm = get_model()
        
        # 2. 추론 수행
        # 데이터프레임 구성
        df = pd.DataFrame({
            "unique_id": [symbol] * len(input_data),
            "ds": pd.date_range(start="2024-01-01", periods=len(input_data), freq="H"),
            "y": input_data
        })
        
        logging.info("Starting TimesFM inference...")
        forecast_df = tfm.forecast_on_df(
            inputs=df,
            freq="H",
            value_name="y",
        )
        
        # 3. 결과 후처리
        result_list = forecast_df.to_dict(orient="records")
        for item in result_list:
            if isinstance(item.get("ds"), pd.Timestamp):
                item["ds"] = item["ds"].isoformat()
        
        output = {
            "status": "success",
            "model": "TimesFM-1.0-200m",
            "symbol": symbol,
            "forecast": result_list,
            "message": f"Successfully forecasted {len(result_list)} points"
        }

        # 다음 단계(Formatting Step)로 이벤트 발행
        context.emit("format-forecast-result", output)
        
        return output

    except Exception as e:
        logging.error(f"Error during forecasting: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }

