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
_load_error: str | None = None  # 마지막 로드 실패 메시지
_model_lock = threading.Lock()  # 병렬 스레드에서 중복 로드 방지


def reset_model():
    """모델 싱글턴을 초기화해 다음 호출 시 재시도하도록 합니다."""
    global _model, _load_attempted, _load_error
    with _model_lock:
        _model = None
        _load_attempted = False
        _load_error = None
    logger.info("[TimesFM] 모델 상태 초기화 완료 (다음 predict 시 재로드)")


def get_load_status() -> dict:
    """모델 로드 상태를 반환합니다."""
    return {
        "loaded": _model is not None,
        "attempted": _load_attempted,
        "error": _load_error,
    }


def _load_model():
    """TimesFM 모델 로드 (최초 1회만 수행, 이후 캐시된 인스턴스 반환)."""
    global _model, _load_attempted, _load_error
    # 락 없이 먼저 체크 (로드 완료 후 빠른 경로)
    if _load_attempted and _model is not None:
        return _model
    with _model_lock:
        # 락 획득 후 재확인 (다른 스레드가 이미 로드했을 수 있음)
        if _load_attempted:
            return _model
        _load_attempted = True

        try:
            import timesfm as tfm_module  # type: ignore
            # 버전별 클래스명 대응: TimesFM_2p5_200M_torch 또는 TimesFm
            ModelClass = getattr(tfm_module, 'TimesFM_2p5_200M_torch', None) or getattr(tfm_module, 'TimesFm', None)
            if ModelClass is None:
                raise ImportError(f"timesfm 패키지에서 사용 가능한 모델 클래스를 찾을 수 없습니다. 사용 가능한 속성: {dir(tfm_module)}")

            logger.info(f"[TimesFM] 모델 로드 중: {ModelClass.__name__} (첫 실행 시 HuggingFace 다운로드 발생)...")

            # from_pretrained의 proxies 인자 오류를 피하기 위해 저수준 로드
            if hasattr(ModelClass, 'from_pretrained'):
                try:
                    # cache_dir을 명시해서 proxies 전달 우회 시도
                    from huggingface_hub import snapshot_download
                    import os
                    cache_dir = os.path.expanduser("~/.cache/huggingface/hub")
                    model_dir = snapshot_download(
                        "google/timesfm-2.5-200m-pytorch",
                        cache_dir=cache_dir,
                        local_files_only=False,
                    )
                    logger.info(f"[TimesFM] 모델 파일 다운로드 완료: {model_dir}")
                    # 다운로드한 경로에서 직접 로드 (proxies 우회)
                    _model = ModelClass.from_pretrained(model_dir)
                except Exception as e:
                    logger.warning(f"[TimesFM] snapshot_download 로드 실패, 기본 로드 시도: {e}")
                    try:
                        # 마지막 시도: 기존 방식
                        _model = ModelClass.from_pretrained(
                            "google/timesfm-2.5-200m-pytorch",
                        )
                    except TypeError as te:
                        if 'proxies' in str(te):
                            logger.warning(f"[TimesFM] proxies 인자 오류 감지, TimesFM 비활성화")
                            raise ImportError("TimesFM_2p5_200M_torch이 proxies 인자를 지원하지 않습니다")
                        else:
                            raise
            else:
                # 구버전 API (TimesFm 클래스)
                _model = ModelClass(
                    hparams=tfm_module.TimesFmHparams(
                        backend="pytorch",
                        per_core_batch_size=32,
                        horizon_len=1,
                        num_layers=20,
                        model_dims=1280,
                        context_len=512,
                    ),
                    checkpoint=tfm_module.TimesFmCheckpoint(
                        huggingface_repo_id="google/timesfm-2.5-200m-pytorch",
                    ),
                )

            logger.info("[TimesFM] 모델 로드 완료")
            _load_error = None
        except Exception as exc:
            logger.exception(f"[TimesFM] 모델 로드 실패 (TimesFM 예측 비활성화): {exc}")
            _model = None
            _load_error = str(exc)

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
        current_price = float(closes[-1])

        # 신버전 API (TimesFM_2p5_200M_torch.from_pretrained 방식)
        # forecast 결과는 [forecast_tensor, full_output_tensor] 형태로 반환됨
        forecast_results = model.forecast(inputs=[context], freq=[0])
        if isinstance(forecast_results, (list, tuple)):
            forecast_values = forecast_results[0]
            forecast_price = float(forecast_values[0, 0])
        else:
            # 구버전 API: (point_forecast, quantile_forecast) 반환
            forecast_price = float(forecast_results[0][0])

        if current_price <= 0:
            return None

        return "up" if forecast_price > current_price else "down"

    except Exception as exc:
        logger.exception(f"[TimesFM] 예측 중 오류: {exc}")
        return None
