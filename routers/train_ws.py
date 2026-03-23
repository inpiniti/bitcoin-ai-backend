"""
WebSocket 기반 서버 사이드 학습 엔드포인트

- 브라우저가 닫혀도 서버에서 수집/학습을 완료하고 Supabase에 저장합니다.
- GET /v1/xgb/train-status 로 현재 진행 상태를 폴링할 수 있습니다.
  (재접속 후 진행 상태 복원용)
"""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger("router.train_ws")

# ── 서버 전역 진행 상태 ──────────────────────────────────────
# 단일 학습 작업만 지원 (HF Space는 1개 컨테이너)
_job: dict = {
    "status": "idle",          # idle | collecting | training | complete | error
    "collect_progress": 0,
    "train_progress": 0,
    "model_name": "",
    "group": "",
    "result": None,
    "error": None,
}


def _set_job(**kwargs):
    _job.update(kwargs)


# ── 상태 조회 엔드포인트 (폴링용) ────────────────────────────
@router.get("/v1/xgb/train-status", tags=["XGBoost"])
async def train_status():
    """현재 진행 중인 학습 작업 상태를 반환합니다. 재접속 후 진행 상태 복원에 사용합니다."""
    return _job


# ── WebSocket 학습 엔드포인트 ────────────────────────────────
@router.websocket("/ws/train")
async def websocket_train(websocket: WebSocket):
    await websocket.accept()
    logger.info("[WS:Train] 클라이언트 연결됨")

    try:
        # 1. 학습 설정 수신
        data = await websocket.receive_json()
        group_key     = data.get("group", "sp500")
        period_days   = int(data.get("period", 365))
        model_name    = data.get("modelName", f"XGB_{group_key}")
        single_ticker = data.get("ticker")

        logger.info(f"[WS:Train] 설정: group={group_key}, period={period_days}d, model={model_name}")
        _set_job(status="collecting", collect_progress=0, train_progress=0,
                 model_name=model_name, group=group_key, result=None, error=None)

        from services.data_collector import collect_and_train_data
        from services.xgb_service import train_from_data

        # 2. 수집 단계
        async def on_collection_progress(progress: int):
            _set_job(collect_progress=progress)
            try:
                await websocket.send_json({"type": "collection", "progress": progress})
            except (WebSocketDisconnect, RuntimeError):
                pass  # 브라우저가 닫혀도 서버는 계속 수집

        await websocket.send_json({"type": "collection", "progress": 0})

        features, labels = await collect_and_train_data(
            group_key=group_key,
            period_days=period_days,
            single_ticker=single_ticker,
            progress_callback=on_collection_progress,
        )

        if not features:
            _set_job(status="error", error="데이터 수집 결과가 없습니다.")
            try:
                await websocket.send_json({"type": "error", "message": _job["error"]})
            except (WebSocketDisconnect, RuntimeError):
                pass
            return

        _set_job(status="training", collect_progress=100, train_progress=0)
        logger.info(f"[WS:Train] 수집 완료: {len(features)}개 샘플")

        try:
            await websocket.send_json({"type": "collection", "progress": 100})
            await websocket.send_json({"type": "training", "progress": 0})
            await websocket.send_json({"type": "training", "progress": 10})
        except (WebSocketDisconnect, RuntimeError):
            pass

        # 3. 학습 (브라우저 닫혀도 여기까지 실행됨)
        result = await train_from_data(features, labels, model_name)

        _set_job(status="complete", train_progress=100, result=result)
        logger.info(f"[WS:Train] 학습 완료: {result}")

        try:
            await websocket.send_json({"type": "training", "progress": 100})
            await websocket.send_json({"type": "complete", "result": result})
        except (WebSocketDisconnect, RuntimeError):
            pass

    except WebSocketDisconnect:
        logger.info("[WS:Train] 클라이언트 연결 끊김 (서버는 계속 실행 중)")
    except Exception as e:
        logger.exception(f"[WS:Train] 오류: {e}")
        _set_job(status="error", error=str(e))
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except (WebSocketDisconnect, RuntimeError):
            pass
