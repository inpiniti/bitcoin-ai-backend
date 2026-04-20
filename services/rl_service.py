"""
강화학습(PPO) 학습 / 예측 서비스

학습 흐름:
    1. 그룹 티커별 캔들 수집 (yfinance)
    2. 피처 추출 (data_collector.process_stock_data_for_prediction)
    3. StockTradingEnv 생성 → PPO 학습
    4. 모델 직렬화 (zip → base64) → Supabase ml_models 저장

예측 흐름:
    1. Supabase에서 RL 모델 로드 → base64 복원
    2. 종목 캔들 수집 → 피처 추출
    3. 날짜별 순서대로 PPO.predict() → BUY/HOLD/SELL 시퀀스 반환
"""
import asyncio
import base64
import logging
import os
import tempfile

import numpy as np

logger = logging.getLogger("rl_service")

_ppo_cls = None


def _get_ppo():
    global _ppo_cls
    if _ppo_cls is None:
        from stable_baselines3 import PPO
        _ppo_cls = PPO
    return _ppo_cls


# ─────────────────────────────────────────────────────────
# 데이터 수집
# ─────────────────────────────────────────────────────────

async def collect_rl_episodes(
    group_key: str,
    period_days: int,
    stage: int = 6,
    single_ticker: str | None = None,
    progress_callback=None,
) -> list[dict]:
    """
    각 종목별 (features, prices) 에피소드 수집.
    features: np.array (T, F)  — StockTradingEnv 관측값
    prices  : np.array (T,)    — 리워드 계산용 종가
    """
    from services.data_collector import (
        fetch_tickers_for_group,
        fetch_stock_history_yf,
        process_stock_data_for_prediction,
        get_required_calendar_days,
        get_stage_lookbacks,
    )

    tickers = [single_ticker] if single_ticker else await fetch_tickers_for_group(group_key)

    # 예측에 필요한 최소 캘린더 일수 (여유분 포함)
    calendar_days = max(period_days, get_required_calendar_days(stage, min_rows=10))

    lookbacks = get_stage_lookbacks(stage)
    max_lookback = max(lookbacks)

    episodes: list[dict] = []
    failed = 0

    for i, ticker in enumerate(tickers):
        try:
            candles = await fetch_stock_history_yf(ticker, calendar_days)
            if not candles or len(candles) < max_lookback + 50:
                failed += 1
                continue

            features, _, _, _ = process_stock_data_for_prediction(candles, stage)
            if not features or len(features) < 50:
                failed += 1
                continue

            # features[j] 는 candles[max_lookback + j] 에 대응
            prices = [
                candles[max_lookback + j]["close"]
                for j in range(len(features))
                if candles[max_lookback + j].get("close")
            ]

            if len(prices) != len(features):
                failed += 1
                continue

            episodes.append({
                "ticker": ticker,
                "features": np.array(features, dtype=np.float32),
                "prices":   np.array(prices,   dtype=np.float32),
            })

        except Exception as e:
            failed += 1
            logger.warning(f"[RL:Collect] {ticker} 수집 실패: {e}")

        if progress_callback and (i + 1) % 10 == 0:
            pct = int((i + 1) / len(tickers) * 100)
            await progress_callback(pct)

    logger.info(f"[RL:Collect] 완료: {len(episodes)}개 에피소드 / {failed}개 실패")
    return episodes


# ─────────────────────────────────────────────────────────
# PPO 학습 (동기 → executor에서 호출)
# ─────────────────────────────────────────────────────────

def _train_ppo_sync(episodes: list[dict], total_timesteps: int) -> object:
    """stable-baselines3 PPO 학습 (blocking). asyncio executor에서 실행됩니다."""
    from services.rl_environment import StockTradingEnv

    PPO = _get_ppo()
    env = StockTradingEnv(episodes)

    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        gamma=0.99,
        ent_coef=0.01,
        verbose=0,
    )

    logger.info(f"[RL:Train] PPO 학습 시작: timesteps={total_timesteps}, episodes={len(episodes)}")
    model.learn(total_timesteps=total_timesteps)
    logger.info("[RL:Train] PPO 학습 완료")
    return model


def _serialize_model(model) -> str:
    """PPO 모델 → base64 문자열"""
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        tmp_path = f.name

    try:
        # SB3 save()는 확장자 없이 넘기면 .zip을 자동으로 붙임
        save_path = tmp_path.removesuffix(".zip")
        model.save(save_path)
        actual = save_path + ".zip" if os.path.exists(save_path + ".zip") else tmp_path
        with open(actual, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    finally:
        for p in [tmp_path, tmp_path.removesuffix(".zip") + ".zip"]:
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


def _deserialize_model(model_b64: str):
    """base64 문자열 → PPO 모델"""
    PPO = _get_ppo()
    model_bytes = base64.b64decode(model_b64)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(model_bytes)
        tmp_path = f.name

    try:
        return PPO.load(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


# ─────────────────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────────────────

async def train_rl(
    episodes: list[dict],
    model_name: str,
    total_timesteps: int = 300_000,
) -> dict:
    """PPO 학습 후 Supabase ml_models 테이블에 저장합니다."""
    from services import supabase_service

    loop = asyncio.get_event_loop()
    model = await loop.run_in_executor(
        None, _train_ppo_sync, episodes, total_timesteps
    )

    model_b64 = await loop.run_in_executor(None, _serialize_model, model)

    n_features = int(episodes[0]["features"].shape[1]) + 3  # +3 포트폴리오 상태
    total_steps = sum(len(ep["features"]) for ep in episodes)

    model_data = {
        "name":          model_name,
        "accuracy":      0.0,
        "f1":            0.0,
        "precision":     0.0,
        "recall":        0.0,
        "auc":           0.0,
        "feature_count": n_features,
        "sample_count":  total_steps,
        "stage":         0,
        "model_json": {
            "type":             "rl",
            "algorithm":        "PPO",
            "n_episodes":       len(episodes),
            "total_timesteps":  total_timesteps,
            "model_b64":        model_b64,
        },
    }

    model_id = await supabase_service.save_model(model_data)
    logger.info(f"[RL:Train] Supabase 저장 완료: {model_id}")

    return {
        "modelId":        model_id,
        "algorithm":      "PPO",
        "episodeCount":   len(episodes),
        "totalTimesteps": total_timesteps,
        "featureCount":   n_features,
        "sampleCount":    total_steps,
    }


async def predict_rl(
    model_id: str,
    ticker: str,
    days: int = 500,
    stage: int = 6,
) -> dict:
    """RL 모델로 종목의 날짜별 BUY/HOLD/SELL 시퀀스를 반환합니다."""
    from services import supabase_service, data_collector

    # 모델 로드
    model_record = await supabase_service.load_model(model_id)
    model_json = model_record["model_json"]

    if not isinstance(model_json, dict) or model_json.get("type") != "rl":
        raise ValueError(f"모델 {model_id}는 RL 모델이 아닙니다 (type={model_json.get('type')})")

    loop = asyncio.get_event_loop()
    model = await loop.run_in_executor(
        None, _deserialize_model, model_json["model_b64"]
    )

    # 종목 데이터 수집
    from services.data_collector import get_stage_lookbacks, get_required_calendar_days
    calendar_days = max(days, get_required_calendar_days(stage, min_rows=10))
    candles = await data_collector.fetch_stock_history_yf(ticker, calendar_days)

    if not candles:
        raise ValueError(f"ticker '{ticker}'의 데이터를 가져올 수 없습니다")

    features, dates, raw_features, actuals = data_collector.process_stock_data_for_prediction(candles, stage)
    if not features:
        raise ValueError(f"ticker '{ticker}'의 피처 추출 실패")

    lookbacks = get_stage_lookbacks(stage)
    max_lookback = max(lookbacks)
    prices = [candles[max_lookback + j]["close"] for j in range(len(features))]

    features_arr = np.array(features, dtype=np.float32)
    prices_arr   = np.array(prices,   dtype=np.float32)

    # 순차 시뮬레이션
    SIGNAL = {0: "HOLD", 1: "BUY", 2: "SELL"}
    results = []
    holding      = False
    buy_price    = 0.0
    holding_days = 0

    for i in range(len(features_arr)):
        feat = features_arr[i]
        holding_flag   = 1.0 if holding else 0.0
        holding_return = (float(prices_arr[i]) - buy_price) / buy_price if holding and buy_price > 0 else 0.0
        obs = np.append(feat, [holding_flag, holding_return, float(holding_days)]).astype(np.float32)

        action, _ = model.predict(obs, deterministic=True)
        action = int(action)

        if action == 1 and not holding:
            holding = True
            buy_price = float(prices_arr[i])
            holding_days = 0
        elif action == 2 and holding:
            holding = False
            buy_price = 0.0
            holding_days = 0

        if holding:
            holding_days += 1

        entry: dict = {
            "date":           dates[i] if i < len(dates) else "",
            "action":         action,
            "signal":         SIGNAL[action],
            "price":          float(prices_arr[i]),
            "holding":        holding,
            "holding_return": round(holding_return * 100, 2),
        }
        if i < len(raw_features):
            entry.update(raw_features[i])
        if i < len(actuals) and actuals[i] is not None:
            entry["actual"] = actuals[i]

        results.append(entry)

    latest = results[-1] if results else {}

    return {
        "ticker":         ticker,
        "model_id":       model_id,
        "latest_signal":  latest.get("signal", "HOLD"),
        "latest_action":  latest.get("action", 0),
        "latest_price":   latest.get("price", 0.0),
        "holding":        latest.get("holding", False),
        "holding_return": latest.get("holding_return", 0.0),
        "predictions":    results[-60:],   # 최근 60일
    }
