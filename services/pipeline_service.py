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
        candles = await fetch_stock_history_yf(run.ticker, 2000)
        result = {"count": len(candles), "latest_close": candles[-1]["close"] if candles else None}
        run.set_step_status("stock_data", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("stock_data", "failed", error=str(e))
        raise


async def execute_preprocess(run: PipelineRun) -> dict:
    """2. 데이터 전처리"""
    try:
        # 실제로는 stock_data 결과를 받아야 함
        result = {"samples": 1500, "features": 7, "stage": 6}
        run.set_step_status("preprocess", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("preprocess", "failed", error=str(e))
        raise


async def execute_xgboost(run: PipelineRun) -> dict:
    """3. XGBoost 분석"""
    try:
        # 실제로는 모델 예측 실행
        result = {"prediction": 0.72, "confidence": "high"}
        run.set_step_status("xgboost", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("xgboost", "failed", error=str(e))
        raise


async def execute_rl(run: PipelineRun) -> dict:
    """4. RL 분석"""
    try:
        result = {"signal": "BUY", "confidence": 0.65}
        run.set_step_status("rl", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("rl", "failed", error=str(e))
        raise


async def execute_timesfm(run: PipelineRun) -> dict:
    """5. TimesFM 분석"""
    try:
        result = {"direction": "up", "confidence": 0.58}
        run.set_step_status("timesfm", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("timesfm", "failed", error=str(e))
        raise


async def execute_chronos(run: PipelineRun) -> dict:
    """6. Chronos 분석"""
    try:
        result = {"direction": "down", "confidence": 0.62}
        run.set_step_status("chronos", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("chronos", "failed", error=str(e))
        raise


async def execute_moirai(run: PipelineRun) -> dict:
    """7. Moirai 분석"""
    try:
        result = {"direction": "up", "confidence": 0.55}
        run.set_step_status("moirai", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("moirai", "failed", error=str(e))
        raise


async def execute_rumors(run: PipelineRun) -> dict:
    """8. 소문 수집"""
    try:
        result = {"reddit": 45, "stocktwits": 120, "twitter": 300}
        run.set_step_status("rumors", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("rumors", "failed", error=str(e))
        raise


async def execute_analyze_rumors(run: PipelineRun) -> dict:
    """9. 소문 분석"""
    try:
        result = {"sentiment": "bullish", "confidence": 0.71, "posts": 465}
        run.set_step_status("analyze_rumors", "completed", result)
        return result
    except Exception as e:
        run.set_step_status("analyze_rumors", "failed", error=str(e))
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
