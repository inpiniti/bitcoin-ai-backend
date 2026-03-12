"""
TimesFM 예측 서비스
모델은 프로세스 생애주기 동안 메모리에 유지됩니다 (싱글톤).
"""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("forecast_service")

# ── 싱글톤 ──────────────────────────────────────────────
_model = None
_torch = None
_np = None
_timesfm = None


def _load_deps():
    global _torch, _np, _timesfm
    if _torch is None:
        import torch as t
        import numpy as n
        import timesfm as tf
        t.set_float32_matmul_precision("high")
        _torch, _np, _timesfm = t, n, tf
    return _torch, _np, _timesfm


def get_model():
    global _model
    _, _, timesfm = _load_deps()

    if _model is None:
        logger.info("TimesFM 2.5 모델 로드 중...")
        _model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            "google/timesfm-2.5-200m-pytorch"
        )
        _model.compile(
            timesfm.ForecastConfig(
                max_context=1024,
                max_horizon=128,
                normalize_inputs=True,
            )
        )
        logger.info("TimesFM 2.5 모델 로드 완료")
    return _model


# ── 예측 실행 ────────────────────────────────────────────

def run_forecast(symbol: str, interval: str, prices: list[float], last_date: str) -> dict:
    """가격 배열을 받아 TimesFM 예측 결과(report dict)를 반환합니다."""
    _, np, _ = _load_deps()

    if not prices:
        raise ValueError("No price data provided")

    logger.info(f"[Forecast] {symbol} {interval}: {len(prices)} 입력 포인트")

    horizon = 30 if interval == "day" else 24
    input_data = np.array(prices, dtype=np.float32)

    model = get_model()
    point_forecast, _ = model.forecast(horizon=horizon, inputs=[input_data])
    forecast_values = point_forecast[0].tolist()

    # 날짜 계산
    try:
        base_date = datetime.fromisoformat(last_date.replace("Z", "+00:00"))
    except Exception:
        base_date = datetime.now(tz=timezone.utc)

    time_unit = timedelta(days=1) if interval == "day" else timedelta(hours=1)

    predictions = []
    max_steps = 30 if interval == "day" else 24
    interval_label = "일봉" if interval == "day" else "시봉"

    for i, val in enumerate(forecast_values[:max_steps]):
        forecast_date = base_date + time_unit * (i + 1)
        predictions.append({
            "step": i + 1,
            "date": forecast_date.isoformat(),
            "price": round(val),
            "priceFormatted": f"${val:,.2f}",
        })

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
