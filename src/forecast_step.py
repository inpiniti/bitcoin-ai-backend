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
    "subscribes": ["bitcoin.forecast.requested"],
    "emits": []
}

def handler(event, context):
    """
    Bitcoin 가격 예측을 수행하는 Python Step.
    TimesFM 모델을 사용하여 시계열 데이터를 분석합니다.
    """
    try:
        data = event.get("data", [])
        if not data:
            return {"status": "error", "message": "No data provided"}

        logging.info(f"Received data for forecasting: {len(data)} points")
        
        # 1. 데이터 전처리
        # 입력 데이터가 리스트 형태인 경우 numpy array로 변환
        # TimesFM은 [batch, context_len] 또는 데이터프레임 형식을 지원함
        # 여기서는 단순 시계열 리스트를 처리하는 예시
        input_data = np.array(data, dtype=np.float32)
        
        # 모델 로드
        tfm = get_model()
        
        # 2. 추론 수행
        # forecast_on_df를 사용하여 간편하게 예측 가능 (utilsforecast 형식 사용)
        # 또는 간단한 array 예측 사용
        
        # 데이터프레임 구성 (유니크 ID 'unique_id', 시간 'ds', 값 'y')
        df = pd.DataFrame({
            "unique_id": ["BTC"] * len(input_data),
            "ds": pd.date_range(start="2024-01-01", periods=len(input_data), freq="H"),
            "y": input_data
        })
        
        logging.info("Starting TimesFM inference...")
        forecast_df = tfm.forecast_on_df(
            inputs=df,
            freq="H",  # 데이터 주기에 맞게 설정 (예: 'H' for hourly)
            value_name="y",
        )
        
        # 3. 결과 후처리
        # 결과를 리스트 형태로 변환하여 반환
        result_list = forecast_df.to_dict(orient="records")
        # Timestamp 객체는 JSON 직렬화가 안되므로 문자열로 변환
        for item in result_list:
            if isinstance(item.get("ds"), pd.Timestamp):
                item["ds"] = item["ds"].isoformat()
        
        return {
            "status": "success",
            "model": "TimesFM-1.0-200m",
            "forecast": result_list,
            "message": f"Successfully forecasted {len(result_list)} points"
        }

    except Exception as e:
        logging.error(f"Error during forecasting: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }
