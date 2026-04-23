"""
S&P 500 종목별 모델 신호 수집 서비스

Confidence >= 0.5 이고 bullish인 종목에 대해
XGBoost, RL, TimesFM, Chronos-2, Moirai 예측을 병렬로 실행합니다.

파이프라인 Step 4-1 ~ 4-5에 해당합니다.
"""
import asyncio
import logging
import traceback
from typing import Optional

logger = logging.getLogger("sp500_signal_service")

# 동시 처리 종목 수 (메모리 및 API 한계 고려)
SIGNAL_CONCURRENCY = 5


async def _fetch_closes(ticker: str, days: int = 200) -> list[float]:
    """종목 종가 리스트 조회 (시계열 예측용)."""
    from services.data_collector import fetch_stock_history_yf
    candles = await fetch_stock_history_yf(ticker, days)
    return [c["close"] for c in candles if c.get("close")]


async def _get_xgb_prediction(ticker: str, model_id: str | None, closes: list[float]) -> tuple[float | None, str | None]:
    """XGBoost 상승 확률 반환 (0.0~1.0)."""
    if not model_id:
        return None, None
    try:
        from services import xgb_service
        result = await xgb_service.predict(
            model_id=model_id,
            features=None,
            dataset_id=None,
            ticker=ticker,
        )
        preds = result.get("predictions", [])
        if preds:
            prob = float(preds[-1].get("probability", 0.5))
            return round(prob, 3), model_id
        return None, None
    except Exception as e:
        logger.error(f"[Signal] XGBoost 예측 실패 ({ticker}): {str(e)}\n{traceback.format_exc()}")
        return None, None


async def _get_rl_prediction(ticker: str, model_id: str | None) -> tuple[str | None, str | None]:
    """강화학습 신호 반환 (BUY / HOLD / SELL)."""
    if not model_id:
        return None, None
    try:
        from services import rl_service
        result = await rl_service.predict_rl(model_id=model_id, ticker=ticker, days=300)
        signal = result.get("latest_signal", "HOLD")
        return signal, model_id
    except Exception as e:
        logger.error(f"[Signal] RL 예측 실패 ({ticker}): {str(e)}\n{traceback.format_exc()}")
        return None, None


async def _get_timesfm_prediction(closes: list[float]) -> str | None:
    """TimesFM 방향 예측 (up / down)."""
    try:
        from services import timesfm_service
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, timesfm_service.predict_direction, closes)
    except Exception as e:
        logger.error(f"[Signal] TimesFM 예측 실패 (데이터: {len(closes)}개): {str(e)}\n{traceback.format_exc()}")
        return None


async def _get_chronos_prediction(closes: list[float]) -> str | None:
    """Chronos-2 방향 예측 (up / down)."""
    try:
        from services.forecast_models_service import predict_direction_chronos
        return await predict_direction_chronos(closes)
    except Exception as e:
        logger.error(f"[Signal] Chronos 예측 실패 (데이터: {len(closes)}개): {str(e)}\n{traceback.format_exc()}")
        return None


async def _get_moirai_prediction(closes: list[float]) -> str | None:
    """Moirai 방향 예측 (up / down)."""
    try:
        from services.forecast_models_service import predict_direction_moirai
        return await predict_direction_moirai(closes)
    except Exception as e:
        logger.error(f"[Signal] Moirai 예측 실패 (데이터: {len(closes)}개): {str(e)}\n{traceback.format_exc()}")
        return None


async def _enrich_single_stock(
    ticker: str,
    xgb_model_id: str | None,
    rl_model_id: str | None,
) -> dict:
    """단일 종목에 대해 모든 모델 예측을 병렬 실행."""
    # 종가 데이터는 한 번만 수집 (모든 시계열 모델 공유)
    closes = await _fetch_closes(ticker)

    if not closes:
        logger.warning(f"[Signal] {ticker}: 종가 데이터 없음 → 스킵")
        return {
            "ticker": ticker,
            "xgb_prob": None,
            "xgb_model_id": None,
            "rl_signal": None,
            "rl_model_id": None,
            "timesfm_signal": None,
            "chronos_signal": None,
            "moirai_signal": None,
        }

    # 모든 예측 병렬 실행
    (xgb_prob, xgb_mid), (rl_signal, rl_mid), timesfm_sig, chronos_sig, moirai_sig = await asyncio.gather(
        _get_xgb_prediction(ticker, xgb_model_id, closes),
        _get_rl_prediction(ticker, rl_model_id),
        _get_timesfm_prediction(closes),
        _get_chronos_prediction(closes),
        _get_moirai_prediction(closes),
        return_exceptions=False,
    )

    logger.info(
        f"[Signal] {ticker}: "
        f"XGB={xgb_prob}, RL={rl_signal}, "
        f"TimesFM={timesfm_sig}, Chronos={chronos_sig}, Moirai={moirai_sig}"
    )

    return {
        "ticker": ticker,
        "xgb_prob": xgb_prob,
        "xgb_model_id": xgb_mid,
        "rl_signal": rl_signal,
        "rl_model_id": rl_mid,
        "timesfm_signal": timesfm_sig,
        "chronos_signal": chronos_sig,
        "moirai_signal": moirai_sig,
    }


async def enrich_stocks_with_models(
    tickers: list[str],
    xgb_model_id: str | None,
    rl_model_id: str | None,
) -> dict[str, dict]:
    """
    대상 종목 리스트(bullish/bearish)에 모델 예측 신호를 추가합니다.

    Args:
        tickers: 분석 대상 티커 리스트
        xgb_model_id: 사용할 XGBoost 모델 ID (None이면 스킵)
        rl_model_id: 사용할 강화학습 모델 ID (None이면 스킵)

    Returns:
        {ticker: {xgb_prob, rl_signal, timesfm_signal, chronos_signal, moirai_signal}}
    """
    if not tickers:
        return {}

    logger.info(
        f"[Signal] ═══ 모델 신호 수집 시작: {len(tickers)}개 종목 "
        f"(XGB={xgb_model_id}, RL={rl_model_id}) ═══"
    )

    sem = asyncio.Semaphore(SIGNAL_CONCURRENCY)
    results: dict[str, dict] = {}

    async def _bounded(ticker: str):
        async with sem:
            result = await _enrich_single_stock(ticker, xgb_model_id, rl_model_id)
            results[ticker] = result

    await asyncio.gather(*[_bounded(t) for t in tickers])

    logger.info(f"[Signal] ═══ 모델 신호 수집 완료: {len(results)}개 ═══")
    return results


async def load_active_model_ids() -> tuple[str | None, str | None]:
    """
    automation_settings에서 'sp500_news' 전용 설정 또는
    활성화된 첫 번째 설정의 XGBoost 및 RL 모델 ID를 조회합니다.

    Returns:
        (xgb_model_id, rl_model_id)
    """
    try:
        from services import supabase_service
        settings_list = await supabase_service.load_all_automation_settings_active()
        
        xgb_model_id = None
        rl_model_id = None
        
        # 1. 'sp500_news' 전용 설정이 있는지 먼저 확인
        news_setting = next((s for s in settings_list if s.get("ticker_group_key") == "sp500_news"), None)
        
        if news_setting:
            xgb_model_id = str(news_setting["ai_model_key"]) if news_setting.get("ai_model_key") else None
            rl_model_id = str(news_setting["rl_model_key"]) if news_setting.get("rl_model_key") else None
            logger.info(f"[Signal] 전용 설정 발견 (sp500_news): XGB={xgb_model_id}, RL={rl_model_id}")
        else:
            # 2. 전용 설정 없으면 활성화된 첫 번째 설정의 모델 사용
            for settings in settings_list:
                if settings.get("ai_model_key") and not xgb_model_id:
                    xgb_model_id = str(settings["ai_model_key"])
                if settings.get("rl_model_key") and not rl_model_id:
                    rl_model_id = str(settings["rl_model_key"])
            logger.info(f"[Signal] 범용 활성 모델 사용: XGB={xgb_model_id}, RL={rl_model_id}")
        
        return xgb_model_id, rl_model_id
    except Exception as e:
        logger.warning(f"[Signal] 활성 모델 조회 실패: {e}")
        return None, None
