"""
Google TimesFM 주가 방향 예측 서비스

TimesFM 2.5 200M PyTorch 모델을 사용해 다음날 주가 상승/하락 여부를 예측합니다.
모델은 최초 호출 시 Hugging Face에서 다운로드하여 싱글턴으로 캐시합니다.

사용:
    from services import timesfm_service
    signal = timesfm_service.predict_direction(closes)  # "up" | "down" | None
"""
import logging
import threading

import numpy as np

logger = logging.getLogger("timesfm_service")

_model = None           # 싱글턴 모델 인스턴스
_load_attempted = False  # 로드 시도 여부 (재시도 방지)
_model_lock = threading.Lock()  # 병렬 스레드에서 중복 로드 방지


def _load_model():
    """TimesFM 모델 로드 (최초 1회만 수행, 이후 캐시된 인스턴스 반환)."""
    global _model, _load_attempted
    # 락 없이 먼저 체크 (로드 완료 후 빠른 경로)
    if _load_attempted and _model is not None:
        return _model
    with _model_lock:
        # 락 획득 후 재확인 (다른 스레드가 이미 로드했을 수 있음)
        if _load_attempted:
            return _model
        _load_attempted = True

        try:
            from timesfm import TimesFM_2p5_200M_torch  # type: ignore
            logger.info("[TimesFM] 모델 로드 중 (첫 실행 시 HuggingFace 다운로드 발생)...")
            _model = TimesFM_2p5_200M_torch.from_pretrained(
                "google/timesfm-2.5-200m-pytorch",
                force_download=False,
            )
            logger.info("[TimesFM] 모델 로드 완료")
        except Exception as exc:
            logger.warning(f"[TimesFM] 모델 로드 실패 (TimesFM 예측 비활성화): {exc}")
            _model = None

    return _model


def predict_direction(closes: list[float]) -> str | None:
    """종가 리스트로 다음날 상승/하락 방향 예측.

    Args:
        closes: 날짜 오름차순 종가 리스트 (최소 32개 필요, 64개 이상 권장)

    Returns:
        "up"   - 다음날 상승 예상
        "down" - 다음날 하락 예상
        None   - 예측 불가 (데이터 부족 또는 모델 로드 실패)
    """
    if len(closes) < 32:
        return None

    model = _load_model()
    if model is None:
        return None

    try:
        # 최근 512개 종가 사용 (모델 컨텍스트 상한 고려)
        context = np.array(closes[-512:], dtype=np.float32)

        # forecast API 수정 (PyTorch 2.5 200M 규격)
        # inputs는 리스트여야 하며, freq도 명시적으로 전달 (0은 단위 없음/주식 데이터용)
        # forecast 결과는 [forecast_tensor, full_output_tensor] 형태로 반환됨
        forecast_results = model.forecast(inputs=[context], freq=[0])
        forecast_values = forecast_results[0] # (batch, horizon)
        
        forecast_price = float(forecast_values[0, 0])
        current_price = float(closes[-1])

        if current_price <= 0:
            return None

        return "up" if forecast_price > current_price else "down"

    except Exception as exc:
        logger.warning(f"[TimesFM] 예측 중 오류: {exc}")
        return None
