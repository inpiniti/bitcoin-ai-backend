"""
실시간 매매 라우터
- /realtime/start-detection : 감지 시작 (이미 실행 중이면 noop)
- /realtime/stop-detection  : 감지 중지
- /realtime/detection-status : 상태 조회
서버 시작 시 main.py의 lifespan에서 start_detection_internal()을 호출해 자동 시작.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/realtime", tags=["realtime"])


# ─────────────────────────────────────────────
# 전역 상태 (단일 인스턴스 가정)
# ─────────────────────────────────────────────
_detection_state = {
    "task": None,       # asyncio.Task
    "manager": None,    # KISWebSocketManager
    "started_at": None,
}


def is_detection_running() -> bool:
    task = _detection_state.get("task")
    return task is not None and not task.done()


# ─────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────
def _get_supabase_env() -> tuple[str | None, str | None]:
    # services/supabase_service.py와 동일한 우선순위
    url = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = (
        os.environ.get("VITE_SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    return url, key


async def _fetch_latest_approval_key() -> str | None:
    """Supabase websocket_keys에서 최신 키 1건 조회"""
    from supabase import create_client

    url, key = _get_supabase_env()
    if not url or not key:
        return None
    sb = create_client(url, key)
    try:
        res = sb.table("websocket_keys") \
            .select("approval_key, expires_at, issued_at") \
            .order("issued_at", desc=True) \
            .limit(1) \
            .execute()
        rows = res.data or []
        if not rows:
            return None
        return rows[0].get("approval_key")
    except Exception as e:
        logger.error(f"[Realtime] approval_key 조회 실패: {e}")
        return None


async def _detection_loop(approval_key: str):
    """KIS WebSocket 연결 + 메시지 수신 루프 (task로 실행)"""
    from supabase import create_client
    from services.websocket_service import KISWebSocketManager, handle_price_detection
    from services.auto_trade_service import execute_realtime_order

    url, key = _get_supabase_env()
    if not url or not key:
        logger.error("[Realtime] Supabase 환경변수 없음")
        return

    supabase = create_client(url, key)

    # 활성 trade 조회
    try:
        res = supabase.table("realtime_trading").select("*").eq("is_active", True).execute()
        active_trades = res.data or []
    except Exception as e:
        logger.error(f"[Realtime] 활성 trade 조회 실패: {e}")
        return

    if not active_trades:
        logger.info("[Realtime] 활성 종목 없음 - 감지 시작 안 함")
        return

    # 매칭 키: BRK.B / BRK-B / BRK/B / BRKB 모두 동일하게 비교되도록 정규화
    def _norm(t):
        return str(t or "").upper().replace(".", "").replace("-", "").replace("/", "")

    active_trades_dict = {_norm(t["ticker"]): t for t in active_trades}
    logger.info(f"[Realtime] 감지 시작: {len(active_trades)}개 종목")

    manager = KISWebSocketManager(
        approval_key=approval_key,
        user_id="system",
        supabase_url=url,
        supabase_key=key,
    )
    _detection_state["manager"] = manager

    try:
        await manager.connect()

        for trade in active_trades:
            try:
                await manager.subscribe_to_stock(
                    ticker=trade["ticker"],
                    market=trade.get("market", "NAS"),
                )
            except Exception as e:
                logger.error(f"[Realtime] {trade['ticker']} 구독 실패: {e}")

        async def on_price_update(data):
            try:
                symb = (data.get("SYMB") or "").upper()
                rate = float(data.get("RATE") or 0)
                mtyp = data.get("MTYP") or "1"
                current_price = float(data.get("LAST") or 0)

                trade = active_trades_dict.get(_norm(symb))
                if not trade:
                    return
                ticker = trade["ticker"]  # DB 원본 (BRK-B 등) 사용

                async def _on_execute(order_data):
                    await execute_realtime_order(
                        trade_id=trade["id"],
                        order_data=order_data,
                        supabase_client=supabase,
                    )
                    # 매매/업데이트 후 캐시 최신화
                    try:
                        latest = supabase.table("realtime_trading") \
                            .select("*") \
                            .eq("id", trade["id"]) \
                            .single() \
                            .execute()
                        if getattr(latest, "data", None):
                            active_trades_dict[_norm(symb)] = latest.data
                    except Exception as e:
                        logger.error(f"[Realtime] {ticker} 캐시 갱신 실패: {e}")

                await handle_price_detection(
                    ticker=ticker,
                    market=trade.get("market", "NAS"),
                    current_price=current_price,
                    base_price=float(trade["base_price"]),
                    gap=float(trade.get("gap", 1)),
                    quantity=int(trade.get("quantity", 0)),
                    rate=rate,
                    mtyp=mtyp,
                    supabase_client=supabase,
                    on_order_execute=_on_execute,
                )
            except Exception as e:
                logger.error(f"[Realtime] 가격 처리 오류: {e}")

        await manager.listen(on_price_update)

    except asyncio.CancelledError:
        logger.info("[Realtime] 감지 task 취소됨")
        raise
    except Exception as e:
        logger.error(f"[Realtime] WebSocket 감지 오류: {e}")
    finally:
        try:
            await manager.disconnect()
        except Exception:
            pass
        _detection_state["manager"] = None


# ─────────────────────────────────────────────
# 시작 / 중지 (lifespan + 엔드포인트 공용)
# ─────────────────────────────────────────────
async def start_detection_internal(approval_key: str | None = None) -> dict:
    if is_detection_running():
        return {"status": "already_running"}

    if not approval_key:
        approval_key = await _fetch_latest_approval_key()
    if not approval_key:
        return {"status": "no_key", "message": "websocket_keys 테이블에 키가 없습니다"}

    task = asyncio.create_task(_detection_loop(approval_key))
    _detection_state["task"] = task
    _detection_state["started_at"] = datetime.now(timezone.utc).isoformat()
    return {"status": "started", "started_at": _detection_state["started_at"]}


async def stop_detection_internal() -> dict:
    if not is_detection_running():
        # task가 있어도 끝났으면 정리
        _detection_state["task"] = None
        _detection_state["manager"] = None
        return {"status": "not_running"}

    manager = _detection_state.get("manager")
    if manager:
        try:
            await manager.disconnect()
        except Exception as e:
            logger.warning(f"[Realtime] manager.disconnect 오류: {e}")

    task = _detection_state.get("task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    _detection_state["task"] = None
    _detection_state["manager"] = None
    _detection_state["started_at"] = None
    return {"status": "stopped"}


# ─────────────────────────────────────────────
# 엔드포인트
# ─────────────────────────────────────────────
@router.post(
    "/start-detection",
    summary="실시간 감지 시작",
    description="approval_key 미지정 시 websocket_keys 테이블에서 최신 키를 자동 조회합니다. 이미 실행 중이면 noop.",
)
async def start_detection(approval_key: str | None = None):
    return await start_detection_internal(approval_key)


@router.post(
    "/stop-detection",
    summary="실시간 감지 중지",
)
async def stop_detection():
    return await stop_detection_internal()


@router.get(
    "/detection-status",
    summary="실시간 감지 상태 조회",
)
async def detection_status():
    return {
        "running": is_detection_running(),
        "started_at": _detection_state.get("started_at"),
    }
