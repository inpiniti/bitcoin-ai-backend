"""
Step 3: TimesFM AI 예측
Event Step - 'run-forecast' 이벤트 구독
"""
import logging
import torch
import numpy as np
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


# Event Step Configuration
config = {
    "name": "run-forecast",
    "type": "event",
    "subscribes": ["run-forecast"],
    "emits": ["format-result"],
    "flows": ["bitcoin-forecast-flow"]
}


async def handler(event, context):
    """
    TimesFM AI 예측 수행.
    """
    job_id = event.get("jobId")
    symbol = event.get("symbol", "BTC-USD")
    prices = event.get("prices", [])
    last_date = event.get("lastDate")
    
    try:
        if not prices:
            raise ValueError("No price data provided")
        
        context.logger.info(f"[Step3:Forecast] Running AI prediction for {symbol} (Job: {job_id})")
        
        input_data = np.array(prices, dtype=np.float32)
        tfm = get_model()
        
        # TimesFM 2.5 예측
        horizon = 24  # 24시간 예측
        point_forecast, quantile_forecast = tfm.forecast(
            horizon=horizon,
            inputs=[input_data],
        )
        
        forecast_values = point_forecast[0].tolist()
        
        # 결과 변환
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
        
        context.logger.info(f"[Step3:Forecast] Generated {len(result_list)} predictions for job {job_id}")
        
        # State 업데이트
        job = await context.state.get("forecasts", job_id)
        if job:
            job["status"] = "forecasted"
            job["predictionCount"] = len(result_list)
            await context.state.set("forecasts", job_id, job)
        
        # Step 4로 이벤트 발행
        await context.emit("format-result", {
            "jobId": job_id,
            "symbol": symbol,
            "lastDate": last_date,
            "forecast": result_list,
            "model": "TimesFM-2.5-200m",
            "dataPoints": len(prices)
        })
        
    except Exception as e:
        context.logger.error(f"[Step3:Forecast] Error for job {job_id}: {str(e)}")
        
        # 에러 상태로 업데이트
        job = await context.state.get("forecasts", job_id)
        if job:
            job["status"] = "error"
            job["error"] = str(e)
            await context.state.set("forecasts", job_id, job)
