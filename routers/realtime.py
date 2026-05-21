"""
실시간 매매 라우터
- /realtime/start-detection : 감지 시작 (이미 실행 중이면 noop)
- /realtime/stop-detection  : 감지 중지
- /realtime/detection-status : 상태 조회
- /realtime/ws : 앱 클라이언트용 WebSocket (KIS 가격 중계)
서버 시작 시 main.py의 lifespan에서 start_detection_internal()을 호출해 자동 시작.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/realtime", tags=["realtime"])


# ─────────────────────────────────────────────
# 앱 클라이언트 WebSocket 브로드캐스터
# ─────────────────────────────────────────────
class _AppBroadcaster:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        logger.info(f"[AppWS] 앱 연결 (총 {len(self._connections)}개)")

    def disconnect(self, ws: WebSocket):
        try:
            self._connections.remove(ws)
        except ValueError:
            pass
        logger.info(f"[AppWS] 앱 연결 해제 (총 {len(self._connections)}개)")

    async def broadcast(self, data: dict):
        dead = []
        for ws in list(self._connections):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


_app_broadcaster = _AppBroadcaster()


# ─────────────────────────────────────────────
# 전역 상태 (단일 인스턴스 가정)
# ─────────────────────────────────────────────
_detection_state = {
    "task": None,         # asyncio.Task
    "manager": None,      # KISWebSocketManager
    "started_at": None,
    "reload_event": None, # asyncio.Event — set 시 즉시 동기화
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
    tickers_str = ", ".join([t["ticker"] for t in active_trades])
    logger.info(f"[Realtime] 감지 시작: {len(active_trades)}개 종목 ({tickers_str})")

    manager = KISWebSocketManager(
        approval_key=approval_key,
        user_id="system",
        supabase_url=url,
        supabase_key=key,
    )
    _detection_state["manager"] = manager
    reload_event = asyncio.Event()
    _detection_state["reload_event"] = reload_event

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
                ask_price = float(data.get("PASK") or 0)   # 매도호가 (매수 즉시체결용)
                bid_price = float(data.get("PBID") or 0)   # 매수호가 (매도 즉시체결용)
                khms = data.get("KHMS") or ""

                norm_symb = _norm(symb)
                trade = active_trades_dict.get(norm_symb)
                if not trade:
                    return
                # 추가 방어: is_active=false인 경우 매매 스킵 (동기화 직전 race condition 대비)
                if trade.get("is_active") is False:
                    return
                ticker = trade["ticker"]  # DB 원본 (BRK-B 등) 사용
                logger.info(f"[Realtime] 가격 수신 - {ticker}: {current_price} ({khms})")

                # 앱 클라이언트에 가격 데이터 중계
                await _app_broadcaster.broadcast({
                    "ticker": ticker,
                    "price": current_price,
                    "rate": rate,
                    "mtyp": mtyp,
                    "khms": khms,
                })

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
                            .execute()
                        rows = getattr(latest, "data", None) or []
                        if rows and rows[0].get("is_active"):
                            active_trades_dict[norm_symb] = rows[0]
                        else:
                            # row가 삭제됐거나 비활성화 → 캐시에서 제거 + 구독 해제
                            # (무한 매수 방지)
                            active_trades_dict.pop(norm_symb, None)
                            try:
                                await manager.unsubscribe_from_stock(
                                    ticker=ticker,
                                    market=trade.get("market", "NAS"),
                                )
                                logger.info(f"[Realtime] {ticker} 캐시/구독 정리 (행 없음 또는 비활성)")
                            except Exception as ue:
                                logger.warning(f"[Realtime] {ticker} 구독 해제 실패: {ue}")
                    except Exception as e:
                        logger.error(f"[Realtime] {ticker} 캐시 갱신 실패 → 안전상 캐시 제거: {e}")
                        active_trades_dict.pop(norm_symb, None)

                await handle_price_detection(
                    ticker=ticker,
                    market=trade.get("market", "NAS"),
                    current_price=current_price,
                    base_price=float(trade["base_price"]),
                    gap=float(trade.get("gap", 1)),
                    quantity=int(trade.get("quantity", 0)),
                    gap_qty=int(trade.get("gap_qty", 1) or 1),
                    rate=rate,
                    mtyp=mtyp,
                    supabase_client=supabase,
                    on_order_execute=_on_execute,
                    ask_price=ask_price,
                    bid_price=bid_price,
                )
            except Exception as e:
                logger.error(f"[Realtime] 가격 처리 오류: {e}")

        async def _reconcile_subscriptions():
            """주기적(or reload_event 트리거 시) DB 활성 trade를 재조회해 메모리/구독 동기화"""
            sync_interval = 15  # 초
            while True:
                try:
                    try:
                        await asyncio.wait_for(reload_event.wait(), timeout=sync_interval)
                        reload_event.clear()
                        logger.info("[Realtime] reload 트리거 → 즉시 동기화")
                    except asyncio.TimeoutError:
                        pass

                    try:
                        res = supabase.table("realtime_trading").select("*").eq("is_active", True).execute()
                    except Exception as e:
                        logger.error(f"[Realtime] 동기화 - DB 조회 실패: {e}")
                        continue

                    fresh = res.data or []
                    fresh_dict = {_norm(t["ticker"]): t for t in fresh}

                    # 신규 추가된 종목 → 구독
                    for norm_key, t in fresh_dict.items():
                        if norm_key not in active_trades_dict:
                            try:
                                await manager.subscribe_to_stock(
                                    ticker=t["ticker"],
                                    market=t.get("market", "NAS"),
                                )
                                logger.info(f"[Realtime] 신규 종목 구독: {t['ticker']}")
                            except Exception as e:
                                logger.error(f"[Realtime] {t['ticker']} 신규 구독 실패: {e}")

                    # 제거/비활성된 종목 → 구독 해제
                    for norm_key, t in list(active_trades_dict.items()):
                        if norm_key not in fresh_dict:
                            try:
                                await manager.unsubscribe_from_stock(
                                    ticker=t["ticker"],
                                    market=t.get("market", "NAS"),
                                )
                                logger.info(f"[Realtime] 제거 종목 구독 해제: {t['ticker']}")
                            except Exception as e:
                                logger.error(f"[Realtime] {t['ticker']} 구독 해제 실패: {e}")

                    # 캐시 교체 (gap_qty/gap/base_price 등 최신값 반영)
                    active_trades_dict.clear()
                    active_trades_dict.update(fresh_dict)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[Realtime] 동기화 루프 오류: {e}")

        listen_task = asyncio.create_task(manager.listen(on_price_update))
        reconcile_task = asyncio.create_task(_reconcile_subscriptions())
        try:
            done, pending = await asyncio.wait(
                [listen_task, reconcile_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
                try:
                    await p
                except (asyncio.CancelledError, Exception):
                    pass
            for d in done:
                exc = d.exception()
                if exc:
                    raise exc
        finally:
            for t in (listen_task, reconcile_task):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

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
        _detection_state["reload_event"] = None


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
    _detection_state["reload_event"] = None
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


@router.post(
    "/reload-config",
    summary="실시간 설정 즉시 재동기화",
    description=(
        "DB의 realtime_trading 변경사항(추가/삭제/비활성화/gap_qty 변경 등)을 "
        "메모리 캐시 및 KIS WebSocket 구독에 즉시 반영합니다. "
        "감지가 실행 중이 아니면 noop. 미호출 시에도 15초 주기로 자동 동기화됩니다."
    ),
)
async def reload_config():
    if not is_detection_running():
        return {"status": "not_running"}
    ev = _detection_state.get("reload_event")
    if ev is None:
        return {"status": "no_reload_event"}
    ev.set()
    return {"status": "triggered"}


@router.websocket("/ws")
async def app_websocket(websocket: WebSocket):
    """앱 클라이언트용 WebSocket — 백엔드가 KIS에서 수신한 가격 데이터를 중계"""
    await _app_broadcaster.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # 클라이언트 disconnect 감지
    except WebSocketDisconnect:
        _app_broadcaster.disconnect(websocket)
    except Exception:
        _app_broadcaster.disconnect(websocket)
