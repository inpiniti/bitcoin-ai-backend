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
        try:
            from stable_baselines3 import PPO
            _ppo_cls = PPO
        except ImportError as e:
            if "numpy._core" in str(e):
                logger.error(f"[RL] Numpy compatibility error: {e}. RL analysis unavailable.")
                raise RuntimeError("RL 모듈을 로드할 수 없습니다. 시스템 관리자에게 문의하세요.") from e
            raise
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

def _train_ppo_sync(
    episodes: list[dict],
    total_timesteps: int,
    on_progress=None,   # callable(pct: int) — 스레드에서 호출 (동기)
) -> object:
    """stable-baselines3 PPO 학습 (blocking). asyncio executor에서 실행됩니다."""
    from stable_baselines3.common.callbacks import BaseCallback
    from services.rl_environment import StockTradingEnv

    PPO = _get_ppo()
    env = StockTradingEnv(episodes)

    class _ProgressCb(BaseCallback):
        """10,000 스텝마다 진행률을 on_progress 콜백으로 전달합니다."""
        def __init__(self, total, cb):
            super().__init__()
            self._total = total
            self._cb = cb
            self._last_pct = -1

        def _on_step(self) -> bool:
            if self._cb and self.num_timesteps % 10_000 == 0:
                pct = min(99, int(self.num_timesteps / self._total * 100))
                if pct != self._last_pct:
                    self._last_pct = pct
                    try:
                        self._cb(pct)
                    except Exception:
                        pass
            return True

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

    cb = _ProgressCb(total_timesteps, on_progress) if on_progress else None
    logger.info(f"[RL:Train] PPO 학습 시작: timesteps={total_timesteps}, episodes={len(episodes)}")
    model.learn(total_timesteps=total_timesteps, callback=cb)
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
# 백테스트 (동기 — executor에서 실행)
# ─────────────────────────────────────────────────────────

def _backtest_sync(model, episodes: list[dict], max_episodes: int = 100) -> dict:
    """
    학습된 모델로 에피소드를 시뮬레이션해 성능 지표를 계산합니다.

    Returns:
        win_rate       : 수익 거래 비율 (0~100%)  → accuracy 컬럼에 저장
        avg_trade_ret  : 거래당 평균 수익률 %     → f1 컬럼에 저장
        sharpe_ratio   : 샤프 비율               → auc 컬럼에 저장
        total_trades   : 총 거래 횟수
        total_return   : 전체 누적 수익률 %
    """
    sample = episodes[:max_episodes]
    trade_returns: list[float] = []
    episode_returns: list[float] = []

    for ep in sample:
        holding      = False
        buy_price    = 0.0
        holding_days = 0

        for i in range(len(ep["features"])):
            feat = ep["features"][i]
            holding_flag   = 1.0 if holding else 0.0
            holding_return = (float(ep["prices"][i]) - buy_price) / buy_price if holding and buy_price > 0 else 0.0
            obs = np.append(feat, [holding_flag, holding_return, float(holding_days)]).astype(np.float32)

            action, _ = model.predict(obs, deterministic=True)
            action = int(action)

            if action == 1 and not holding:
                holding      = True
                buy_price    = float(ep["prices"][i])
                holding_days = 0
            elif action == 2 and holding:
                ret = (float(ep["prices"][i]) - buy_price) / buy_price
                trade_returns.append(ret)
                holding      = False
                buy_price    = 0.0
                holding_days = 0

            if holding:
                holding_days += 1

        # 미청산 포지션 강제 청산
        if holding and buy_price > 0:
            ret = (float(ep["prices"][-1]) - buy_price) / buy_price
            trade_returns.append(ret)

        if len(ep["prices"]) > 1:
            ep_ret = (float(ep["prices"][-1]) - float(ep["prices"][0])) / float(ep["prices"][0])
            episode_returns.append(ep_ret)

    n = len(trade_returns)
    if n == 0:
        return {"win_rate": 0.0, "avg_trade_ret": 0.0, "sharpe_ratio": 0.0,
                "total_trades": 0, "total_return": 0.0}

    wins         = sum(1 for r in trade_returns if r > 0)
    win_rate     = round(wins / n * 100, 2)
    avg_ret      = float(np.mean(trade_returns)) * 100
    total_return = float(np.sum(episode_returns)) / len(episode_returns) * 100 if episode_returns else 0.0

    # 샤프 비율 (무위험 수익률 0 가정)
    ret_std = float(np.std(trade_returns))
    sharpe  = round(float(np.mean(trade_returns)) / ret_std, 3) if ret_std > 0 else 0.0

    logger.info(
        f"[RL:Backtest] 거래={n}, 승률={win_rate}%, 평균수익={avg_ret:.2f}%, 샤프={sharpe}"
    )
    return {
        "win_rate":      win_rate,
        "avg_trade_ret": round(avg_ret, 2),
        "sharpe_ratio":  sharpe,
        "total_trades":  n,
        "total_return":  round(total_return, 2),
    }


# ─────────────────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────────────────

async def load_rl_model(model_id: str) -> tuple[dict, object]:
    """RL 모델 로드 및 역직렬화. (model_record, ppo_model) 반환."""
    from services import supabase_service

    model_record = await supabase_service.load_model(model_id)
    model_json = model_record.get("model_json") or {}

    if not isinstance(model_json, dict) or model_json.get("type") != "rl":
        raise ValueError(f"모델 {model_id}는 RL 모델이 아닙니다 (type={model_json.get('type')})")

    loop = asyncio.get_event_loop()
    ppo_model = await loop.run_in_executor(None, _deserialize_model, model_json["model_b64"])
    return model_record, ppo_model


def get_latest_signal_sync(ppo_model, candles: list[dict], stage: int) -> str:
    """
    캔들 데이터로 RL 최신 시그널 반환 (BUY/SELL/HOLD).
    순차 시뮬레이션으로 실제 포지션 상태를 반영합니다.
    """
    from services.data_collector import process_stock_data_for_prediction, get_stage_lookbacks

    features, _, _, _ = process_stock_data_for_prediction(candles, stage)
    if not features:
        return "HOLD"

    lookbacks = get_stage_lookbacks(stage)
    max_lookback = max(lookbacks)

    if len(candles) < max_lookback + len(features):
        return "HOLD"

    prices = [
        candles[max_lookback + j]["close"]
        for j in range(len(features))
        if candles[max_lookback + j].get("close")
    ]

    if len(prices) != len(features):
        return "HOLD"

    features_arr = np.array(features, dtype=np.float32)
    prices_arr = np.array(prices, dtype=np.float32)

    SIGNAL = {0: "HOLD", 1: "BUY", 2: "SELL"}
    holding = False
    buy_price = 0.0
    holding_days = 0
    latest_signal = "HOLD"

    for i in range(len(features_arr)):
        feat = features_arr[i]
        holding_flag = 1.0 if holding else 0.0
        holding_return = (float(prices_arr[i]) - buy_price) / buy_price if holding and buy_price > 0 else 0.0
        obs = np.append(feat, [holding_flag, holding_return, float(holding_days)]).astype(np.float32)

        action, _ = ppo_model.predict(obs, deterministic=True)
        action = int(action)
        latest_signal = SIGNAL[action]

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

    return latest_signal


async def train_rl(
    episodes: list[dict],
    model_name: str,
    total_timesteps: int = 300_000,
    stage: int = 6,
    train_progress_callback=None,   # async callable(pct: int)
) -> dict:
    """PPO 학습 후 Supabase ml_models 테이블에 저장합니다."""
    from services import supabase_service

    loop = asyncio.get_event_loop()

    # 동기 콜백 → asyncio 이벤트 루프로 브리지
    def _sync_progress(pct: int):
        if train_progress_callback:
            asyncio.run_coroutine_threadsafe(train_progress_callback(pct), loop)

    model = await loop.run_in_executor(
        None, _train_ppo_sync, episodes, total_timesteps, _sync_progress
    )

    # 직렬화 + 백테스트 병렬 실행
    model_b64, bt = await asyncio.gather(
        loop.run_in_executor(None, _serialize_model, model),
        loop.run_in_executor(None, _backtest_sync, model, episodes),
    )

    n_features = int(episodes[0]["features"].shape[1]) + 3  # +3 포트폴리오 상태
    total_steps = sum(len(ep["features"]) for ep in episodes)

    model_data = {
        "name":          model_name,
        # XGBoost accuracy 자리에 승률, auc 자리에 샤프비율 저장
        # → 기존 모델 목록 UI에서 그대로 성능 지표로 활용 가능
        "accuracy":      bt["win_rate"] / 100,     # 0~1 범위로 저장 (UI가 % 표시)
        "f1":            max(0.0, bt["avg_trade_ret"] / 100),
        "precision":     0.0,
        "recall":        0.0,
        "auc":           max(0.0, bt["sharpe_ratio"]),
        "feature_count": n_features,
        "sample_count":  total_steps,
        "stage":         stage,   # 학습에 사용된 피처 stage (예측 시 동일 stage 필요)
        "model_json": {
            "type":             "rl",
            "algorithm":        "PPO",
            "stage":            stage,
            "n_episodes":       len(episodes),
            "total_timesteps":  total_timesteps,
            # 백테스트 지표 (model_json에도 원본 수치 보존)
            "win_rate":         bt["win_rate"],
            "avg_trade_ret":    bt["avg_trade_ret"],
            "sharpe_ratio":     bt["sharpe_ratio"],
            "total_trades":     bt["total_trades"],
            "total_return":     bt["total_return"],
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
        # 백테스트 성능 지표
        "winRate":        bt["win_rate"],
        "avgTradeReturn": bt["avg_trade_ret"],
        "sharpeRatio":    bt["sharpe_ratio"],
        "totalTrades":    bt["total_trades"],
        "totalReturn":    bt["total_return"],
    }


async def predict_rl(
    model_id: str,
    ticker: str,
    days: int = 500,
    stage: int | None = None,   # None이면 model_json에서 자동 로드
) -> dict:
    """RL 모델로 종목의 날짜별 BUY/HOLD/SELL 시퀀스를 반환합니다."""
    from services import supabase_service, data_collector

    # 모델 로드
    model_record = await supabase_service.load_model(model_id)
    model_json = model_record["model_json"]

    if not isinstance(model_json, dict) or model_json.get("type") != "rl":
        raise ValueError(f"모델 {model_id}는 RL 모델이 아닙니다 (type={model_json.get('type')})")

    # stage: model_json 우선 (학습 시 저장된 값), 없으면 파라미터, 최후 기본값 6
    stage = model_json.get("stage") or stage or model_record.get("stage") or 6

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

        # 알파고처럼 각 액션 확률 추출 (Policy Network softmax 출력)
        try:
            import torch
            obs_t = obs.reshape(1, -1)
            obs_tensor, _ = model.policy.obs_to_tensor(obs_t)
            with torch.no_grad():
                dist = model.policy.get_distribution(obs_tensor)
                probs = dist.distribution.probs.squeeze().cpu().numpy()
            prob_hold = round(float(probs[0]) * 100, 1)
            prob_buy  = round(float(probs[1]) * 100, 1)
            prob_sell = round(float(probs[2]) * 100, 1)
        except Exception:
            prob_hold = prob_buy = prob_sell = None

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
            # 알파고처럼 각 액션의 확률 (Policy Network 출력)
            "prob_hold":      prob_hold,
            "prob_buy":       prob_buy,
            "prob_sell":      prob_sell,
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
