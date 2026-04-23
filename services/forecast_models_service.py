"""
Amazon Chronos-2 및 Salesforce Moirai 시계열 예측 서비스

두 모델 모두 싱글턴으로 캐시하며, 최초 호출 시 Hugging Face에서 다운로드합니다.
메모리 효율을 위해 소형 variant를 사용합니다.

사용:
    from services import forecast_models_service
    result = await forecast_models_service.predict_direction_chronos(closes)   # "up" | "down" | None
    result = await forecast_models_service.predict_direction_moirai(closes)    # "up" | "down" | None
"""
import asyncio
import logging
import threading

import numpy as np

logger = logging.getLogger("forecast_models_service")

# ── Chronos-2 싱글턴 ──────────────────────────────────────────────────────────

_chronos_pipeline = None
_chronos_attempted = False
_chronos_lock = threading.Lock()


def _load_chronos():
    """Amazon Chronos-2 파이프라인 로드 (최초 1회)."""
    global _chronos_pipeline, _chronos_attempted
    if _chronos_attempted and _chronos_pipeline is not None:
        return _chronos_pipeline
    with _chronos_lock:
        if _chronos_attempted:
            return _chronos_pipeline
        _chronos_attempted = True
        try:
            # chronos-forecasting 패키지 사용
            from chronos import BaseChronosPipeline  # type: ignore
            import torch
            logger.info("[Chronos] 모델 로드 중 (amazon/chronos-t5-small → mini 버전)...")
            # chronos-2 소형 변형: amazon/chronos-t5-small (76M)
            _chronos_pipeline = BaseChronosPipeline.from_pretrained(
                "amazon/chronos-t5-small",
                device_map="cpu",
                torch_dtype=torch.float32,
            )
            logger.info("[Chronos] 모델 로드 완료")
        except Exception as exc:
            logger.exception(f"[Chronos] 모델 로드 실패 (예측 비활성화): {exc}")
            _chronos_pipeline = None
    return _chronos_pipeline


# ── Moirai 싱글턴 ─────────────────────────────────────────────────────────────

_moirai_model = None
_moirai_attempted = False
_moirai_lock = threading.Lock()


def _load_moirai():
    """Salesforce Moirai 모델 로드 (최초 1회)."""
    global _moirai_model, _moirai_attempted
    if _moirai_attempted and _moirai_model is not None:
        return _moirai_model
    with _moirai_lock:
        if _moirai_attempted:
            return _moirai_model
        _moirai_attempted = True
        try:
            from uni2ts.model.moirai import MoiraiForecast, MoiraiModule  # type: ignore
            logger.info("[Moirai] 모델 로드 중 (Salesforce/moirai-moe-1.0-R-small)...")
            # 소형 variant 사용 (메모리 절약)
            _moirai_model = MoiraiForecast(
                module=MoiraiModule.from_pretrained("Salesforce/moirai-moe-1.0-R-small"),
                prediction_length=1,
                context_length=64,
                patch_size="auto",
                num_samples=20,
                target_dim=1,
                feat_dynamic_real_dim=0,
                past_feat_dynamic_real_dim=0,
            )
            logger.info("[Moirai] 모델 로드 완료")
        except Exception as exc:
            logger.exception(f"[Moirai] 모델 로드 실패 (예측 비활성화): {exc}")
            _moirai_model = None
    return _moirai_model


# ── 예측 함수 ─────────────────────────────────────────────────────────────────

def _predict_chronos_sync(closes: list[float]) -> str | None:
    """Chronos-2 동기 예측 (executor에서 실행)."""
    if len(closes) < 32:
        logger.debug(f"[Chronos] 데이터 부족: {len(closes)}개 (최소 32개 필요)")
        return None
    pipeline = _load_chronos()
    if pipeline is None:
        logger.warning(f"[Chronos] 모델 로드 실패 또는 미시도 (attempted={_chronos_attempted})")
        return None
    try:
        import torch
        context = torch.tensor(closes[-64:], dtype=torch.float32).unsqueeze(0)
        # predict_quantiles 반환값: 구버전은 tuple, 신버전은 tensor
        quantile_forecasts = pipeline.predict_quantiles(
            inputs=context,
            prediction_length=1,
            quantile_levels=[0.5],
        )
        # tuple인 경우 첫 번째 원소 추출
        if isinstance(quantile_forecasts, tuple):
            quantile_forecasts = quantile_forecasts[0]

        # shape: (1, 1, 1) → 스칼라 추출
        forecast_price = float(quantile_forecasts[0, 0, 0])
        current_price = float(closes[-1])
        if current_price <= 0:
            logger.warning(f"[Chronos] 현재가 유효하지 않음: {current_price}")
            return None
        return "up" if forecast_price > current_price else "down"
    except Exception as exc:
        logger.exception(f"[Chronos] 예측 오류 (데이터: {len(closes)}개, shape={torch.tensor(closes[-64:]).shape if 'torch' in globals() else 'N/A'}): {exc}")


def _predict_moirai_sync(closes: list[float]) -> str | None:
    """Moirai 동기 예측 (executor에서 실행)."""
    if len(closes) < 32:
        logger.debug(f"[Moirai] 데이터 부족: {len(closes)}개 (최소 32개 필요)")
        return None
    model = _load_moirai()
    if model is None:
        logger.warning(f"[Moirai] 모델 로드 실패 또는 미시도 (attempted={_moirai_attempted})")
        return None
    try:
        import torch
        current_price = float(closes[-1])
        if current_price <= 0:
            logger.warning(f"[Moirai] 현재가 유효하지 않음: {current_price}")
            return None

        # Moirai context_len: 모델은 64 고정이지만, 입력 데이터 길이가 불일치하면 실패
        # → 정확히 64개 또는 그보다 적으면 padding, 많으면 최근 64개만 사용
        context_len = min(64, len(closes))
        past_values = torch.tensor(closes[-context_len:], dtype=torch.float32)

        # Moirai가 기대하는 input shape 확인
        # 일반적으로: (batch, time, features) = (1, 64, 1)
        # 하지만 context_len < 64이면 (1, context_len, 1)
        # → 모델이 strict하면 padding 필요
        pad_len = 0
        if context_len < 64:
            pad_len = 64 - context_len
            padding = torch.full((pad_len,), closes[0], dtype=torch.float32)
            past_values = torch.cat([padding, past_values])

        past_values = past_values.unsqueeze(0).unsqueeze(-1)  # (1, 64, 1)
        # past_observed는 (batch, time) shape = (1, 64)
        past_observed = torch.ones((past_values.shape[0], past_values.shape[1]), dtype=torch.bool)
        # 패딩된 부분은 is_pad=True로 표시
        past_is_pad = torch.zeros((past_values.shape[0], past_values.shape[1]), dtype=torch.bool)
        if pad_len > 0:
            past_is_pad[0, :pad_len] = True

        future_values, _, _ = model(
            past_target=past_values,
            past_observed_target=past_observed,
            past_is_pad=past_is_pad,
        )
        forecast_price = float(future_values.median())
        return "up" if forecast_price > current_price else "down"
    except Exception as exc:
        logger.exception(f"[Moirai] 예측 오류 (데이터: {len(closes)}개): {exc}")
        return None


async def predict_direction_chronos(closes: list[float]) -> str | None:
    """
    Amazon Chronos-2로 다음날 주가 방향 예측 (비동기).
    
    Args:
        closes: 날짜 오름차순 종가 리스트 (최소 32개 필요)

    Returns:
        "up" | "down" | None
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _predict_chronos_sync, closes)


async def predict_direction_moirai(closes: list[float]) -> str | None:
    """
    Salesforce Moirai로 다음날 주가 방향 예측 (비동기).
    
    Args:
        closes: 날짜 오름차순 종가 리스트 (최소 32개 필요)

    Returns:
        "up" | "down" | None
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _predict_moirai_sync, closes)
