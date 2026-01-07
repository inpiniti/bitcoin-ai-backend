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
        # TimesFM v2.0 (PyTorch/JAX) API 대응
        # 하이퍼파라미터를 TimesFmHparams 객체로 래핑하여 전달
        hparams = TimesFmHparams(
            context_len=512,
            horizon_len=128,
            input_patch_len=32,
            output_patch_len=128,
            num_layers=20,
            model_dims=1280,
            backend="cpu", # CPU 환경
        )
        
        tfm_model = TimesFm(
            hparams=hparams,
            checkpoint="google/timesfm-1.0-200m", # 혹은 로컬 체크포인트 경로
        )
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

async def handler(event, context):
    """
    Bitcoin 가격 예측을 수행하는 Python Step.
    TimesFM 모델을 사용하여 시계열 데이터를 분석합니다.
    """
    try:
        data = event.get("data", [])
        symbol = event.get("symbol", "BTC-USD")
        last_date = event.get("lastDate")
        
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
        # forecast_on_df는 v2.0에서도 지원됨
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
            "lastDate": last_date,
            "forecast": result_list,
            "message": f"Successfully forecasted {len(result_list)} points"
        }

        # 다음 단계(Formatting Step)를 호출하여 최종 보고서를 받아옵니다.
        logging.info("[Forecast] Emitting 'format-forecast-result'...")
        if hasattr(context, "emit"):
            # Python SDK에서 emit 결과물을 동기적으로 받기 위해 await 사용
            final_report = await context.emit("format-forecast-result", output)
            return final_report
        
        return output

    except Exception as e:
        logging.error(f"Error during forecasting: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }


