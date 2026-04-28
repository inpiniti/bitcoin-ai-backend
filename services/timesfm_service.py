"""
Google TimesFM 주가 방향 예측 서비스

TimesFM 2.5 200M PyTorch 모델을 사용해 다음날 주가 상승/하락 여부를 예측합니다.
모델은 최초 호출 시 Hugging Face에서 다운로드하여 싱글턴으로 캐시합니다.

사용:
    from services import timesfm_service
    signal = timesfm_service.predict_direction(closes)  # "up" | "down" | None
"""
import logging
import numpy as np

logger = logging.getLogger("timesfm_service")

_model = None
_load_attempted = False


def _load_model():
    """TimesFM 모델 로드 (최초 1회만 수행, 이후 캐시된 인스턴스 반환)."""
    global _model, _load_attempted
    if _load_attempted:
        return _model
    _load_attempted = True

    try:
        from timesfm import TimesFM_2p5_200M_torch  # type: ignore
        logger.info("[TimesFM] 모델 로드 중 (첫 실행 시 HuggingFace 다운로드 발생)...")
        _model = TimesFM_2p5_200M_torch.from_pretrained(
            "google/timesfm-2.5-200m-pytorch"
        )
        logger.info("[TimesFM] 모델 로드 완료")
    except Exception as exc:
        logger.exception(f"[TimesFM] 모델 로드 실패: {exc}")
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
        logger.debug(f"[TimesFM] 데이터 부족: {len(closes)}개 (최소 32개 필요)")
        return None

    model = _load_model()
    if model is None:
        logger.warning("[TimesFM] 모델 미로드")
        return None

    try:
        # 최근 512개 종가 사용 (모델 컨텍스트 상한 고려)
        context = np.array(closes[-512:], dtype=np.float32)
        current_price = float(closes[-1])

        logger.info(
            f"[TimesFM:Input] 데이터={len(context)}개, "
            f"min={np.min(context):.2f}, max={np.max(context):.2f}, "
            f"mean={np.mean(context):.2f}, std={np.std(context):.2f}, "
            f"current={current_price:.4f}"
        )

        # 입력 데이터 검증
        if np.any(np.isnan(context)) or np.any(np.isinf(context)):
            logger.warning(f"[TimesFM:Input] NaN/Inf 감지: NaN={np.sum(np.isnan(context))}, Inf={np.sum(np.isinf(context))}")
            return None

        if np.std(context) == 0:
            logger.warning(f"[TimesFM:Input] 표준편차=0 (모든 값이 동일)")
            return None

        if current_price <= 0:
            logger.warning(f"[TimesFM] 현재가 유효하지 않음: {current_price}")
            return None

        # forecast_naive(horizon=1): 1일 앞 예측
        # 반환값 outputs[0] shape: (output_patch_len=128, horizon=1)
        # outputs[0][0, 0] = 1-step ahead point forecast
        logger.info("[TimesFM] 모델 호출 시작")
        outputs = model.model.forecast_naive(horizon=1, inputs=[context])

        forecast_price = float(outputs[0][0, 0])

        logger.info(
            f"[TimesFM:Output] forecast={forecast_price:.4f}, "
            f"current={current_price:.4f}, "
            f"NaN={np.isnan(forecast_price)}, Inf={np.isinf(forecast_price)}"
        )

        # NaN/Inf 체크
        if np.isnan(forecast_price) or np.isinf(forecast_price):
            logger.error(f"[TimesFM] NaN/Inf 예측값: {forecast_price}")
            return None

        direction = "up" if forecast_price > current_price else "down"
        logger.info(f"[TimesFM] ✓ forecast={forecast_price:.4f} vs current={current_price:.4f} → {direction}")
        return direction

    except Exception as exc:
        logger.exception(f"[TimesFM] 예측 중 오류: {exc}")
        return None
