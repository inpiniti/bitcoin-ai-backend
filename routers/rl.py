"""
강화학습(PPO) 라우터

WS  /ws/rl/train         - 학습 시작 + 진행률 스트리밍
GET /v1/rl/train-status  - 학습 진행 상태 폴링 (브라우저 재접속 복원용)
POST /v1/rl/predict      - RL 모델로 종목 예측
"""
import logging
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from services import rl_service

router = APIRouter()
logger = logging.getLogger("router.rl")

# ── 서버 전역 학습 상태 (단일 HF Space 컨테이너 기준) ─────────
_job: dict = {
    "status":           "idle",   # idle | collecting | training | complete | error
    "collect_progress": 0,
    "train_progress":   0,
    "model_name":       "",
    "group":            "",
    "result":           None,
    "error":            None,
}


def _set_job(**kwargs):
    _job.update(kwargs)


# ── 상태 폴링 ────────────────────────────────────────────────

@router.get("/v1/rl/train-status", tags=["RL"])
async def rl_train_status():
    """현재 RL 학습 진행 상태를 반환합니다. 재접속 후 진행 복원에 사용됩니다."""
    return _job


# ── 예측 ────────────────────────────────────────────────────

class RLPredictRequest(BaseModel):
    modelId: str
    ticker:  str
    days:    int = 500
    stage:   int = 6


@router.post("/v1/rl/predict", tags=["RL"])
async def rl_predict(body: RLPredictRequest):
    """
    학습된 RL(PPO) 모델로 종목의 날짜별 매매 시그널을 반환합니다.

    응답 예시:
    ```json
    {
      "ticker": "AAPL",
      "latest_signal": "BUY",
      "latest_action": 1,
      "latest_price": 178.5,
      "holding": true,
      "holding_return": 2.3,
      "predictions": [{"date": "2024-01-02", "signal": "BUY", ...}]
    }
    ```
    """
    if not body.modelId or not body.ticker:
        raise HTTPException(status_code=400, detail="modelId, ticker 필수")

    try:
        result = await rl_service.predict_rl(
            body.modelId, body.ticker, body.days, body.stage
        )
        return result
    except Exception as e:
        logger.exception(f"[/v1/rl/predict] 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── WebSocket 학습 ───────────────────────────────────────────

@router.websocket("/ws/rl/train")
async def websocket_rl_train(websocket: WebSocket):
    """
    RL(PPO) 학습 WebSocket.
    클라이언트가 연결을 끊어도 서버에서 학습을 완료하고 Supabase에 저장합니다.

    요청 JSON:
    {
      "group":          "sp500",
      "period":         365,
      "stage":          6,
      "totalTimesteps": 300000,
      "modelName":      "RL_PPO_sp500_s6"
    }

    서버 → 클라이언트 메시지 타입:
      {"type": "collection", "progress": 0~100}
      {"type": "training",   "progress": 0~100, "message": "..."}
      {"type": "complete",   "result": {...}}
      {"type": "error",      "message": "..."}
    """
    await websocket.accept()
    logger.info("[WS:RLTrain] 클라이언트 연결됨")

    async def _send(payload: dict):
        try:
            await websocket.send_json(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass

    try:
        data = await websocket.receive_json()

        group_key       = data.get("group", "sp500")
        period_days     = int(data.get("period", 365))
        stage           = int(data.get("stage", 6))
        total_timesteps = int(data.get("totalTimesteps", 300_000))
        model_name      = data.get("modelName", f"RL_PPO_{group_key}_s{stage}")
        single_ticker   = data.get("ticker")

        logger.info(
            f"[WS:RLTrain] 설정: group={group_key}, period={period_days}d, "
            f"stage={stage}, timesteps={total_timesteps}, model={model_name}"
        )

        _set_job(
            status="collecting", collect_progress=0, train_progress=0,
            model_name=model_name, group=group_key, result=None, error=None,
        )

        # ── 1단계: 데이터 수집 ──────────────────────────────
        await _send({"type": "collection", "progress": 0})

        async def on_progress(pct: int):
            _set_job(collect_progress=pct)
            await _send({"type": "collection", "progress": pct})

        episodes = await rl_service.collect_rl_episodes(
            group_key=group_key,
            period_days=period_days,
            stage=stage,
            single_ticker=single_ticker,
            progress_callback=on_progress,
        )

        if not episodes:
            _set_job(status="error", error="에피소드 데이터가 없습니다. 그룹/기간을 확인해 주세요.")
            await _send({"type": "error", "message": _job["error"]})
            return

        _set_job(status="training", collect_progress=100, train_progress=0)
        await _send({"type": "collection", "progress": 100})
        await _send({
            "type":    "training",
            "progress": 0,
            "message": f"{len(episodes)}개 종목 데이터로 PPO 학습 시작 (timesteps={total_timesteps:,})",
        })

        # ── 2단계: PPO 학습 (blocking → executor) ───────────
        # 브라우저가 닫혀도 서버에서 계속 실행됩니다.
        # 10,000 스텝마다 진행률을 클라이언트에 전송합니다.
        async def on_train_progress(pct: int):
            _set_job(train_progress=pct)
            await _send({"type": "training", "progress": pct,
                         "message": f"PPO 학습 중... {pct}%"})

        result = await rl_service.train_rl(
            episodes, model_name, total_timesteps,
            stage=stage,
            train_progress_callback=on_train_progress,
        )

        _set_job(status="complete", train_progress=100, result=result)
        await _send({"type": "training", "progress": 100})
        await _send({"type": "complete", "result": result})

        logger.info(f"[WS:RLTrain] 학습 완료: {result}")

    except WebSocketDisconnect:
        logger.info("[WS:RLTrain] 클라이언트 연결 끊김 (서버 학습 계속 진행)")
    except Exception as e:
        logger.exception(f"[WS:RLTrain] 오류: {e}")
        _set_job(status="error", error=str(e))
        await _send({"type": "error", "message": str(e)})
