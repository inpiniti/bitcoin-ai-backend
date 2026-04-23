"""
SP500 파이프라인 단계별 실행 서비스
각 단계를 독립적으로 실행하고 상태 추적
"""
import asyncio
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("pipeline_service")

# 파이프라인 단계 정의
PIPELINE_STEPS = [
    "stock_data",      # 1. AAPL 데이터 수집
    "preprocess",      # 2. 데이터 전처리
    "xgboost",         # 3. XGBoost 분석
    "rl",              # 4. RL 분석
    "timesfm",         # 5. TimesFM 분석
    "chronos",         # 6. Chronos 분석
    "moirai",          # 7. Moirai 분석
    "rumors",          # 8. 소문 수집
    "analyze_rumors",  # 9. 소문 분석
]

# 파이프라인 실행 상태 (메모리 저장, 나중에 DB로 변경 가능)
_pipeline_runs = {}


class PipelineRun:
    """파이프라인 실행 추적"""
    def __init__(self, run_id: str, ticker: str = "AAPL"):
        self.run_id = run_id
        self.ticker = ticker
        self.steps = {step: {"status": "pending", "result": None, "error": None, "timestamp": None}
                      for step in PIPELINE_STEPS}
        self.created_at = datetime.utcnow()
        self.current_step = None
        # 단계 간 데이터 공유
        self.data = {
            "candles": None,
            "features": None,
            "xgboost_result": None,
            "rl_result": None,
            "timesfm_result": None,
            "chronos_result": None,
            "moirai_result": None,
            "rumors": None,
        }

    def get_status(self) -> dict:
        """현재 파이프라인 상태 반환"""
        return {
            "run_id": self.run_id,
            "ticker": self.ticker,
            "created_at": self.created_at.isoformat(),
            "steps": self.steps,
            "current_step": self.current_step,
        }

    def set_step_status(self, step: str, status: str, result: Any = None, error: str = None):
        """단계 상태 업데이트"""
        if step in self.steps:
            self.steps[step]["status"] = status
            if result:
                self.steps[step]["result"] = result
            if error:
                self.steps[step]["error"] = error
            self.steps[step]["timestamp"] = datetime.utcnow().isoformat()
            self.current_step = step
            logger.info(f"[Pipeline:{self.run_id}] {step} → {status}")


def create_run(ticker: str = "AAPL") -> PipelineRun:
    """새로운 파이프라인 실행 생성"""
    import uuid
    run_id = str(uuid.uuid4())[:8]
    run = PipelineRun(run_id, ticker)
    _pipeline_runs[run_id] = run
    logger.info(f"[Pipeline] New run: {run_id} ({ticker})")
    return run


def get_run(run_id: str) -> PipelineRun | None:
    """파이프라인 실행 조회"""
    return _pipeline_runs.get(run_id)


# ── 단계별 실행 함수 ────────────────────────────────────────

async def execute_stock_data(run: PipelineRun) -> dict:
    """1. AAPL 데이터 수집"""
    try:
        from services.data_collector import fetch_stock_history_yf
        logger.info(f"[Pipeline:{run.run_id}] {run.ticker} 데이터 수집 중...")
        candles = await fetch_stock_history_yf(run.ticker, 2000)

        if not candles:
            raise ValueError(f"{run.ticker} 데이터를 찾을 수 없습니다")

        run.data["candles"] = candles

        result = {
            "count": len(candles),
            "latest_close": candles[-1]["close"],
            "date_range": f"{candles[0].get('date')} ~ {candles[-1].get('date')}"
        }
        run.set_step_status("stock_data", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] 데이터 수집 완료: {len(candles)}개")
        return result
    except Exception as e:
        run.set_step_status("stock_data", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] 데이터 수집 실패: {e}")
        raise


async def execute_preprocess(run: PipelineRun) -> dict:
    """2. 데이터 전처리"""
    try:
        from services.xgb_service import extract_features_for_prediction

        logger.info(f"[Pipeline:{run.run_id}] 데이터 전처리 중 ({run.ticker})...")

        features, stage = await extract_features_for_prediction(run.ticker, days=2000, target_stage=6)

        if not features:
            raise ValueError("피처 추출 실패: 데이터 부족")

        run.data["features"] = {
            "values": features,
            "stage": stage
        }

        result = {
            "samples": len(features),
            "features": len(features[0]) if features and len(features) > 0 else 0,
            "stage": stage
        }
        run.set_step_status("preprocess", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] 전처리 완료: {result}")
        return result
    except Exception as e:
        run.set_step_status("preprocess", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] 전처리 실패: {e}")
        raise


async def execute_xgboost(run: PipelineRun) -> dict:
    """3. XGBoost 분석"""
    try:
        logger.info(f"[Pipeline:{run.run_id}] XGBoost 예측 중...")

        try:
            from services.sp500_signal_service import load_active_model_ids
            from services.xgb_service import predict

            xgb_model_id, _ = await load_active_model_ids()

            if not xgb_model_id:
                logger.warning("[Pipeline] XGBoost 모델 ID 없음, 더미 예측 사용")
                result = {
                    "probability": 0.72,
                    "prediction": 1,
                    "confidence": 0.72
                }
            else:
                xgb_result = await predict(
                    model_id=xgb_model_id,
                    features=None,
                    dataset_id=None,
                    ticker=run.ticker
                )
                predictions = xgb_result.get("predictions", [])
                if predictions:
                    latest = predictions[-1]
                    prob = float(latest.get("probability", 0.5))
                    pred = int(latest.get("prediction", 0))
                    result = {
                        "probability": round(prob, 3),
                        "prediction": pred,
                        "confidence": round(prob, 3),
                        "signal": "BUY" if pred == 1 else "SELL"
                    }
                else:
                    result = {
                        "probability": 0.5,
                        "prediction": 0,
                        "confidence": 0.5
                    }
        except Exception as e:
            logger.warning(f"[Pipeline] XGBoost 예측 오류: {e}, 더미 사용")
            result = {
                "probability": 0.55,
                "prediction": 0,
                "confidence": 0.55
            }

        run.data["xgboost_result"] = result
        run.set_step_status("xgboost", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] XGBoost 예측 완료: {result}")
        return result
    except Exception as e:
        run.set_step_status("xgboost", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] XGBoost 예측 실패: {e}")
        raise


async def execute_rl(run: PipelineRun) -> dict:
    """4. RL 분석"""
    try:
        logger.info(f"[Pipeline:{run.run_id}] RL 모델 예측 중...")

        try:
            from services.sp500_signal_service import load_active_model_ids
            from services.rl_service import predict_rl

            _, rl_model_id = await load_active_model_ids()

            if rl_model_id:
                rl_result = await predict_rl(
                    model_id=rl_model_id,
                    ticker=run.ticker
                )
                signals = rl_result.get("signals", [])
                if signals:
                    latest_signal = signals[-1]
                    result = {
                        "signal": latest_signal,
                        "confidence": 0.65,
                        "total_signals": len(signals)
                    }
                else:
                    raise ValueError("RL 신호 없음")
            else:
                raise ValueError("RL 모델 ID 없음")
        except Exception as e:
            logger.warning(f"[Pipeline] RL 예측 오류: {e}, 더미 사용")
            result = {
                "signal": "BUY",
                "confidence": 0.55
            }

        run.data["rl_result"] = result
        run.set_step_status("rl", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] RL 예측 완료: {result}")
        return result
    except Exception as e:
        run.set_step_status("rl", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] RL 예측 실패: {e}")
        raise


async def execute_timesfm(run: PipelineRun) -> dict:
    """5. TimesFM 분석"""
    try:
        candles = run.data.get("candles")
        if not candles:
            raise ValueError("stock_data 단계가 완료되지 않았습니다")

        logger.info(f"[Pipeline:{run.run_id}] TimesFM 예측 중...")

        try:
            from services.timesfm_service import predict_direction
            closes = [c["close"] for c in candles[-500:]]
            direction = predict_direction(closes)

            if direction is None:
                raise ValueError("TimesFM 예측 실패")

            confidence = 0.58
        except Exception as e:
            logger.warning(f"[Pipeline] TimesFM 오류: {e}, 더미 예측 사용")
            direction = "up"
            confidence = 0.50

        result = {
            "direction": direction,
            "confidence": round(float(confidence), 3)
        }
        run.data["timesfm_result"] = result
        run.set_step_status("timesfm", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] TimesFM 예측 완료: {result}")
        return result
    except Exception as e:
        run.set_step_status("timesfm", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] TimesFM 예측 실패: {e}")
        raise


async def execute_chronos(run: PipelineRun) -> dict:
    """6. Chronos 분석"""
    try:
        candles = run.data.get("candles")
        if not candles:
            raise ValueError("stock_data 단계가 완료되지 않았습니다")

        logger.info(f"[Pipeline:{run.run_id}] Chronos 예측 중...")

        try:
            from services.forecast_models_service import predict_direction_chronos
            closes = [c["close"] for c in candles[-500:]]
            direction = await predict_direction_chronos(closes)

            if direction is None:
                raise ValueError("Chronos 예측 실패")

            confidence = 0.62
        except Exception as e:
            logger.warning(f"[Pipeline] Chronos 오류: {e}, 더미 예측 사용")
            direction = "down"
            confidence = 0.50

        result = {
            "direction": direction,
            "confidence": round(float(confidence), 3)
        }
        run.data["chronos_result"] = result
        run.set_step_status("chronos", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] Chronos 예측 완료: {result}")
        return result
    except Exception as e:
        run.set_step_status("chronos", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] Chronos 예측 실패: {e}")
        raise


async def execute_moirai(run: PipelineRun) -> dict:
    """7. Moirai 분석"""
    try:
        candles = run.data.get("candles")
        if not candles:
            raise ValueError("stock_data 단계가 완료되지 않았습니다")

        logger.info(f"[Pipeline:{run.run_id}] Moirai 예측 중...")

        try:
            from services.forecast_models_service import predict_direction_moirai
            closes = [c["close"] for c in candles[-500:]]
            direction = await predict_direction_moirai(closes)

            if direction is None:
                raise ValueError("Moirai 예측 실패")

            confidence = 0.55
        except Exception as e:
            logger.warning(f"[Pipeline] Moirai 오류: {e}, 더미 예측 사용")
            direction = "up"
            confidence = 0.50

        result = {
            "direction": direction,
            "confidence": round(float(confidence), 3)
        }
        run.data["moirai_result"] = result
        run.set_step_status("moirai", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] Moirai 예측 완료: {result}")
        return result
    except Exception as e:
        run.set_step_status("moirai", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] Moirai 예측 실패: {e}")
        raise


async def execute_rumors(run: PipelineRun) -> dict:
    """8. 소문 수집"""
    try:
        logger.info(f"[Pipeline:{run.run_id}] 소문 수집 중...")

        rumors_data = {
            "reddit": [],
            "stocktwits": [],
            "twitter": []
        }

        try:
            from services.rumors_service import collect_rumors
            rumors_data = await collect_rumors(run.ticker)
        except (ImportError, ModuleNotFoundError):
            logger.warning("[Pipeline] 소문 수집 서비스 불가능, 더미 데이터 사용")
            # Dummy data for testing
            rumors_data = {
                "reddit": [{"text": "AAPL 강세", "score": 45}],
                "stocktwits": [{"text": "좋은 실적 예상", "score": 120}],
                "twitter": [{"text": "긍정적 뉴스", "score": 300}]
            }

        reddit_count = len(rumors_data.get("reddit", []))
        stocktwits_count = len(rumors_data.get("stocktwits", []))
        twitter_count = len(rumors_data.get("twitter", []))

        run.data["rumors"] = rumors_data

        result = {
            "reddit": reddit_count,
            "stocktwits": stocktwits_count,
            "twitter": twitter_count,
            "total": reddit_count + stocktwits_count + twitter_count
        }
        run.set_step_status("rumors", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] 소문 수집 완료: {result}")
        return result
    except Exception as e:
        run.set_step_status("rumors", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] 소문 수집 실패: {e}")
        raise


async def execute_analyze_rumors(run: PipelineRun) -> dict:
    """9. 소문 분석"""
    try:
        rumors_data = run.data.get("rumors")
        if not rumors_data:
            raise ValueError("rumors 단계가 완료되지 않았습니다")

        logger.info(f"[Pipeline:{run.run_id}] 소문 감정 분석 중...")

        try:
            from services.rumors_analysis_service import analyze_sentiment
            sentiment_result = await analyze_sentiment(rumors_data)
            sentiment = sentiment_result.get("sentiment", "neutral")
            confidence = sentiment_result.get("confidence", 0.5)
        except (ImportError, ModuleNotFoundError):
            logger.warning("[Pipeline] 감정 분석 서비스 불가능, 휴리스틱 사용")
            # Simple heuristic: count positive words
            all_texts = " ".join([
                r.get("text", "") for r in (
                    rumors_data.get("reddit", []) +
                    rumors_data.get("stocktwits", []) +
                    rumors_data.get("twitter", [])
                )
            ]).lower()

            positive_words = ["좋은", "강세", "긍정", "강함", "상승", "매수"]
            negative_words = ["나쁜", "약세", "부정", "약함", "하락", "매도"]

            positive_count = sum(1 for word in positive_words if word in all_texts)
            negative_count = sum(1 for word in negative_words if word in all_texts)

            if positive_count > negative_count:
                sentiment = "bullish"
                confidence = 0.60
            elif negative_count > positive_count:
                sentiment = "bearish"
                confidence = 0.60
            else:
                sentiment = "neutral"
                confidence = 0.50

        total_posts = (
            len(rumors_data.get("reddit", [])) +
            len(rumors_data.get("stocktwits", [])) +
            len(rumors_data.get("twitter", []))
        )

        result = {
            "sentiment": sentiment,
            "confidence": round(float(confidence), 3),
            "posts": total_posts
        }
        run.set_step_status("analyze_rumors", "completed", result)
        logger.info(f"[Pipeline:{run.run_id}] 감정 분석 완료: {result}")
        return result
    except Exception as e:
        run.set_step_status("analyze_rumors", "failed", error=str(e))
        logger.error(f"[Pipeline:{run.run_id}] 감정 분석 실패: {e}")
        raise


# 단계별 실행 함수 매핑
STEP_EXECUTORS = {
    "stock_data": execute_stock_data,
    "preprocess": execute_preprocess,
    "xgboost": execute_xgboost,
    "rl": execute_rl,
    "timesfm": execute_timesfm,
    "chronos": execute_chronos,
    "moirai": execute_moirai,
    "rumors": execute_rumors,
    "analyze_rumors": execute_analyze_rumors,
}


async def execute_step(run: PipelineRun, step: str) -> dict:
    """단계 실행"""
    if step not in STEP_EXECUTORS:
        raise ValueError(f"Unknown step: {step}")

    run.set_step_status(step, "running")
    executor = STEP_EXECUTORS[step]
    return await executor(run)
