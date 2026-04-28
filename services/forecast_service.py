"""
TimesFM 예측 서비스
모델은 프로세스 생애주기 동안 메모리에 유지됩니다 (싱글톤).
"""
import logging
from datetime import datetime, timedelta, timezone
import numpy as np

logger = logging.getLogger("forecast_service")

# ── 싱글톤 ──────────────────────────────────────────────
_model = None


def get_model():
    global _model
    if _model is None:
        try:
            from timesfm import TimesFM_2p5_200M_torch  # type: ignore
            logger.info("TimesFM 2.5 모델 로드 중...")
            _model = TimesFM_2p5_200M_torch.from_pretrained(
                "google/timesfm-2.5-200m-pytorch",
                force_download=False,
            )
            logger.info("TimesFM 2.5 모델 로드 완료")
        except Exception as exc:
            logger.exception(f"TimesFM 모델 로드 실패: {exc}")
            _model = None
    return _model


# ── 예측 실행 ────────────────────────────────────────────

def run_forecast(symbol: str, interval: str, prices: list[float], last_date: str) -> dict:
    """가격 배열을 받아 TimesFM 예측 결과(report dict)를 반환합니다."""
    if not prices:
        raise ValueError("No price data provided")

    logger.info(f"[Forecast] {symbol} {interval}: {len(prices)} 입력 포인트")

    horizon = 30 if interval == "day" else 24
    input_data = np.array(prices, dtype=np.float32)

    model = get_model()
    if model is None:
        raise RuntimeError("TimesFM 모델을 로드할 수 없습니다")

    # forecast_naive(horizon): 1-step ahead 예측
    # 반환값 outputs[0] shape: (output_patch_len=128, horizon=?)
    outputs = model.model.forecast_naive(horizon=horizon, inputs=[input_data])
    forecast_values = [float(outputs[0][i, 0]) for i in range(min(horizon, outputs[0].shape[0]))]

    # 날짜 계산
    try:
        base_date = datetime.fromisoformat(last_date.replace("Z", "+00:00"))
    except ValueError:
        base_date = datetime.now(tz=timezone.utc)

    time_unit = timedelta(days=1) if interval == "day" else timedelta(hours=1)

    predictions = []
    max_steps = 30 if interval == "day" else 24

    for i, val in enumerate(forecast_values[:max_steps]):
        forecast_date = base_date + time_unit * (i + 1)
        predictions.append({
            "step": i + 1,
            "date": forecast_date.isoformat(),
            "price": round(val),
            "priceFormatted": f"${val:,.2f}",
        })

    interval_label = "일봉" if interval == "day" else "시봉"
    report = {
        "title": f"{symbol} {interval_label} 가격 예측 보고서",
        "symbol": symbol,
        "interval": interval,
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "model": "TimesFM-2.5-200m",
        "dataPoints": len(prices),
        "predictionCount": len(predictions),
        "predictions": predictions,
    }

    logger.info(f"[Forecast] {symbol}: {len(predictions)}개 예측 완료")
    return report
