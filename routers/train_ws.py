"""
WebSocket 기반 서버 사이드 학습 엔드포인트

연결 흐름:
  클라이언트 → WS /ws/train
  → 서버: 종목 그룹 수집 (progress 0~100)
  → 서버: XGBoost 학습 (training_progress 0~100)
  → 완료 메시지 전송

메시지 형식:
  {"type": "collection", "progress": 32}
  {"type": "training",   "progress": 55}
  {"type": "complete",   "result": {...}}
  {"type": "error",      "message": "..."}
"""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger("router.train_ws")


@router.websocket("/ws/train")
async def websocket_train(websocket: WebSocket):
    await websocket.accept()
    logger.info("[WS:Train] 클라이언트 연결됨")

    try:
        # 1. 학습 설정 수신
        data = await websocket.receive_json()
        group_key    = data.get("group", "sp500")
        period_days  = int(data.get("period", 365))
        model_name   = data.get("modelName", f"XGB_{group_key}")
        single_ticker = data.get("ticker")  # 단일 티커 모드

        logger.info(f"[WS:Train] 설정: group={group_key}, period={period_days}d, model={model_name}")

        from services.data_collector import collect_and_train_data
        from services.xgb_service import train_from_data
        import asyncio

        # 2. 수집 단계
        async def on_collection_progress(progress: int):
            try:
                await websocket.send_json({"type": "collection", "progress": progress})
            except Exception:
                pass

        await websocket.send_json({"type": "collection", "progress": 0})
        features, labels = await collect_and_train_data(
            group_key=group_key,
            period_days=period_days,
            single_ticker=single_ticker,
            progress_callback=on_collection_progress,
        )

        if not features:
            await websocket.send_json({
                "type": "error",
                "message": "데이터 수집 결과가 없습니다. 종목 그룹이나 기간을 확인하세요."
            })
            return

        await websocket.send_json({"type": "collection", "progress": 100})
        logger.info(f"[WS:Train] 수집 완료: {len(features)}개 샘플")

        # 3. 학습 단계 (XGBoost는 동기 blocking → executor 실행)
        await websocket.send_json({"type": "training", "progress": 0})

        loop = asyncio.get_event_loop()

        # 학습 진행률은 xgb 자체에서 얻기 어려워 시작/완료 두 단계만 전송
        await websocket.send_json({"type": "training", "progress": 10})

        result = await train_from_data(features, labels, model_name)

        await websocket.send_json({"type": "training", "progress": 100})

        # 4. 완료 메시지
        await websocket.send_json({"type": "complete", "result": result})
        logger.info(f"[WS:Train] 학습 완료: {result}")

    except WebSocketDisconnect:
        logger.info("[WS:Train] 클라이언트 연결 끊김")
    except Exception as e:
        logger.exception(f"[WS:Train] 오류: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
