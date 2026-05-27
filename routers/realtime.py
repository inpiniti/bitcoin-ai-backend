"""
실시간 매매 라우터 (멀티유저)

사용자(user_id)별로 독립된 KIS WebSocket 감지 세션을 운영한다.
요청 주인은 Authorization: Bearer <JWT>(앱 발급 토큰)로 식별한다.

- POST /realtime/start-detection : 본인 감지 시작
- POST /realtime/stop-detection  : 본인 감지 중지
- GET  /realtime/detection-status: 본인 감지 상태
- POST /realtime/reload-config   : 본인 설정 즉시 재동기화
- WS   /realtime/ws?token=<JWT>  : 앱용 — 본인 종목 가격만 중계

서버 시작 시 main.py lifespan에서 start_all_detections()로 활성 사용자별 세션을 자동 시작.
"""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, WebSocket, WebSocketDisconnect

from services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/realtime", tags=["realtime"])


# ─────────────────────────────────────────────
# 앱 클라이언트 WebSocket 브로드캐스터 (사용자별)
# ─────────────────────────────────────────────
class _AppBroadcaster:
    def __init__(self):
        self._by_user: dict[str, list[WebSocket]] = {}

    async def connect(self, ws: WebSocket, user_id: str):
        await ws.accept()
        self._by_user.setdefault(user_id, []).append(ws)
        total = sum(len(v) for v in self._by_user.values())
        logger.info(f"[AppWS] 앱 연결 user={user_id} (총 {total}개)")

    def disconnect(self, ws: WebSocket, user_id: str):
        conns = self._by_user.get(user_id)
        if conns:
            try:
                conns.remove(ws)
            except ValueError:
                pass
            if not conns:
                self._by_user.pop(user_id, None)
        logger.info(f"[AppWS] 앱 연결 해제 user={user_id}")

    async def broadcast_to_user(self, user_id: str, data: dict):
        dead = []
        for ws in list(self._by_user.get(user_id, [])):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, user_id)


_app_broadcaster = _AppBroadcaster()


# ─────────────────────────────────────────────
# 사용자별 감지 세션 상태
#   user_id -> { task, manager, started_at, reload_event }
# ─────────────────────────────────────────────
_sessions: dict[str, dict] = {}


def is_detection_running(user_id: str) -> bool:
    sess = _sessions.get(user_id)
    if not sess:
        return False
    task = sess.get("task")
    return task is not None and not task.done()


# 매칭 키 정규화: BRK.B / BRK-B / BRK/B / BRKB 모두 동일하게 비교
def _norm(t):
    return str(t or "").upper().replace(".", "").replace("-", "").replace("/", "")


async def _fetch_user_approval_key(supabase, user_id: str) -> str | None:
    """websocket_keys에서 해당 사용자의 최신 approval_key 조회"""
    try:
        res = (
            supabase.table("websocket_keys")
            .select("approval_key, issued_at")
            .eq("user_id", user_id)
            .order("issued_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        return rows[0].get("approval_key") if rows else None
    except Exception as e:
        logger.error(f"[Realtime] approval_key 조회 실패 user={user_id}: {e}")
        return None


async def _detection_loop(user_id: str, approval_key: str):
    """특정 사용자의 KIS WebSocket 연결 + 메시지 수신 루프 (task로 실행)"""
    from supabase import create_client
    from services.websocket_service import KISWebSocketManager, handle_price_detection
    from services.auto_trade_service import execute_realtime_order

    url, key = auth_service.get_supabase_env()
    if not url or not key:
        logger.error("[Realtime] Supabase 환경변수 없음")
        return

    supabase = create_client(url, key)

    # 해당 사용자의 활성 trade만 조회
    try:
        res = (
            supabase.table("realtime_trading")
            .select("*")
            .eq("is_active", True)
            .eq("user_id", user_id)
            .execute()
        )
        active_trades = res.data or []
    except Exception as e:
        logger.error(f"[Realtime] 활성 trade 조회 실패 user={user_id}: {e}")
        return

    if not active_trades:
        logger.info(f"[Realtime] 활성 종목 없음 user={user_id} - 감지 시작 안 함")
        return

    active_trades_dict = {_norm(t["ticker"]): t for t in active_trades}
    tickers_str = ", ".join([t["ticker"] for t in active_trades])
    logger.info(f"[Realtime] 감지 시작 user={user_id}: {len(active_trades)}개 ({tickers_str})")

    manager = KISWebSocketManager(
        approval_key=approval_key,
        user_id=user_id,
        supabase_url=url,
        supabase_key=key,
    )
    reload_event = asyncio.Event()
    _sessions.setdefault(user_id, {})
    _sessions[user_id]["manager"] = manager
    _sessions[user_id]["reload_event"] = reload_event

    try:
        await manager.connect()

        for trade in active_trades:
            try:
                await manager.subscribe_to_stock(
                    ticker=trade["ticker"], market=trade.get("market", "NAS")
                )
            except Exception as e:
                logger.error(f"[Realtime] {trade['ticker']} 구독 실패 user={user_id}: {e}")

        async def on_price_update(data):
            try:
                symb = (data.get("SYMB") or "").upper()
                market_type = data.get("MARKET_TYPE", "overseas")

                if market_type == "domestic":
                    # 국내주식 (H0STCNT0): STCK_PRPR, PRDY_CTRT
                    current_price = float(data.get("STCK_PRPR") or 0)
                    rate = float(data.get("PRDY_CTRT") or 0)
                    mtyp = "1"
                    khms = data.get("STCK_CNTG_HOUR") or ""
                    ask_price = float(data.get("ASKP1") or 0)
                    bid_price = float(data.get("BIDP1") or 0)
                else:
                    # 해외주식 (HDFSCNT0): LAST, RATE
                    current_price = float(data.get("LAST") or 0)
                    rate = float(data.get("RATE") or 0)
                    mtyp = data.get("MTYP") or "1"
                    khms = data.get("KHMS") or ""
                    ask_price = float(data.get("PASK") or 0)
                    bid_price = float(data.get("PBID") or 0)

                norm_symb = _norm(symb)
                trade = active_trades_dict.get(norm_symb)
                if not trade:
                    return
                if trade.get("is_active") is False:
                    return
                ticker = trade["ticker"]
                logger.info(f"[Realtime] 가격 수신 user={user_id} {ticker}: {current_price} ({khms})")

                # 본인 앱 클라이언트에만 가격 중계
                await _app_broadcaster.broadcast_to_user(user_id, {
                    "ticker": ticker,
                    "price": current_price,
                    "rate": rate,
                    "base_price": float(trade["base_price"]),
                    "mtyp": mtyp,
                    "khms": khms,
                })

                async def _on_execute(order_data):
                    await execute_realtime_order(
                        trade_id=trade["id"],
                        order_data=order_data,
                        supabase_client=supabase,
                        user_id=user_id,
                    )
                    # 매매/업데이트 후 캐시 최신화
                    try:
                        latest = supabase.table("realtime_trading").select("*").eq("id", trade["id"]).execute()
                        rows = getattr(latest, "data", None) or []
                        if rows and rows[0].get("is_active"):
                            active_trades_dict[norm_symb] = rows[0]
                        else:
                            active_trades_dict.pop(norm_symb, None)
                            try:
                                await manager.unsubscribe_from_stock(ticker=ticker, market=trade.get("market", "NAS"))
                                logger.info(f"[Realtime] {ticker} 캐시/구독 정리 user={user_id} (행 없음 또는 비활성)")
                            except Exception as ue:
                                logger.warning(f"[Realtime] {ticker} 구독 해제 실패: {ue}")
                    except Exception as e:
                        logger.error(f"[Realtime] {ticker} 캐시 갱신 실패 → 안전상 제거: {e}")
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
                    grid_step=int(trade.get("grid_step", 0)),
                )
            except Exception as e:
                logger.error(f"[Realtime] 가격 처리 오류 user={user_id}: {e}")

        async def _reconcile_subscriptions():
            """주기적(or reload_event 트리거 시) 해당 사용자 활성 trade 재조회·동기화"""
            sync_interval = 15
            while True:
                try:
                    try:
                        await asyncio.wait_for(reload_event.wait(), timeout=sync_interval)
                        reload_event.clear()
                        logger.info(f"[Realtime] reload 트리거 user={user_id} → 즉시 동기화")
                    except asyncio.TimeoutError:
                        pass

                    try:
                        res = (
                            supabase.table("realtime_trading")
                            .select("*")
                            .eq("is_active", True)
                            .eq("user_id", user_id)
                            .execute()
                        )
                    except Exception as e:
                        logger.error(f"[Realtime] 동기화 DB 조회 실패 user={user_id}: {e}")
                        continue

                    fresh = res.data or []
                    fresh_dict = {_norm(t["ticker"]): t for t in fresh}

                    # 신규 추가 → 구독
                    for norm_key, t in fresh_dict.items():
                        if norm_key not in active_trades_dict:
                            try:
                                await manager.subscribe_to_stock(ticker=t["ticker"], market=t.get("market", "NAS"))
                                logger.info(f"[Realtime] 신규 구독 user={user_id}: {t['ticker']}")
                            except Exception as e:
                                logger.error(f"[Realtime] {t['ticker']} 신규 구독 실패: {e}")

                    # 제거/비활성 → 구독 해제
                    for norm_key, t in list(active_trades_dict.items()):
                        if norm_key not in fresh_dict:
                            try:
                                await manager.unsubscribe_from_stock(ticker=t["ticker"], market=t.get("market", "NAS"))
                                logger.info(f"[Realtime] 구독 해제 user={user_id}: {t['ticker']}")
                            except Exception as e:
                                logger.error(f"[Realtime] {t['ticker']} 구독 해제 실패: {e}")

                    active_trades_dict.clear()
                    active_trades_dict.update(fresh_dict)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"[Realtime] 동기화 루프 오류 user={user_id}: {e}")

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
        logger.info(f"[Realtime] 감지 task 취소 user={user_id}")
        raise
    except Exception as e:
        logger.error(f"[Realtime] WebSocket 감지 오류 user={user_id}: {e}")
    finally:
        try:
            await manager.disconnect()
        except Exception:
            pass
        sess = _sessions.get(user_id)
        if sess:
            sess["manager"] = None
            sess["reload_event"] = None


# ─────────────────────────────────────────────
# 시작 / 중지 (lifespan + 엔드포인트 공용)
# ─────────────────────────────────────────────
async def start_detection_internal(user_id: str, approval_key: str | None = None) -> dict:
    if not user_id:
        return {"status": "no_user", "message": "user_id가 필요합니다"}
    if is_detection_running(user_id):
        return {"status": "already_running"}

    if not approval_key:
        from supabase import create_client
        url, key = auth_service.get_supabase_env()
        if not url or not key:
            return {"status": "no_config"}
        sb = create_client(url, key)
        approval_key = await _fetch_user_approval_key(sb, user_id)
    if not approval_key:
        return {"status": "no_key", "message": "해당 사용자의 websocket_keys가 없습니다"}

    task = asyncio.create_task(_detection_loop(user_id, approval_key))
    _sessions.setdefault(user_id, {})
    _sessions[user_id]["task"] = task
    _sessions[user_id]["started_at"] = datetime.now(timezone.utc).isoformat()
    return {"status": "started", "started_at": _sessions[user_id]["started_at"]}


async def stop_detection_internal(user_id: str) -> dict:
    sess = _sessions.get(user_id)
    if not sess or not is_detection_running(user_id):
        if sess:
            sess["task"] = None
            sess["manager"] = None
        return {"status": "not_running"}

    manager = sess.get("manager")
    if manager:
        try:
            await manager.disconnect()
        except Exception as e:
            logger.warning(f"[Realtime] manager.disconnect 오류 user={user_id}: {e}")

    task = sess.get("task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    _sessions.pop(user_id, None)
    return {"status": "stopped"}


async def start_all_detections() -> dict:
    """서버 부팅 시: 활성 종목이 있는 사용자별로 감지 세션을 시작."""
    from supabase import create_client
    url, key = auth_service.get_supabase_env()
    if not url or not key:
        logger.error("[Realtime] Supabase 환경변수 없음 - 자동 시작 불가")
        return {"status": "no_config"}
    sb = create_client(url, key)
    try:
        res = sb.table("realtime_trading").select("user_id").eq("is_active", True).execute()
        rows = res.data or []
    except Exception as e:
        logger.error(f"[Realtime] 활성 사용자 조회 실패: {e}")
        return {"status": "error", "error": str(e)}

    user_ids = {r.get("user_id") for r in rows if r.get("user_id")}
    started = []
    for uid in user_ids:
        approval_key = await _fetch_user_approval_key(sb, uid)
        if not approval_key:
            logger.warning(f"[Realtime] user={uid} approval_key 없음 - 자동 시작 건너뜀")
            continue
        result = await start_detection_internal(uid, approval_key)
        started.append({"user_id": uid, "result": result})
    logger.info(f"[Realtime] 자동 시작 완료: {len(started)}명")
    return {"status": "started", "users": started}


async def stop_all_detections() -> dict:
    for uid in list(_sessions.keys()):
        await stop_detection_internal(uid)
    return {"status": "stopped"}


# ─────────────────────────────────────────────
# 엔드포인트
# ─────────────────────────────────────────────
def _user_from_header(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return auth_service.verify_supabase_jwt(authorization[7:])


@router.post("/start-detection", summary="실시간 감지 시작 (본인)")
async def start_detection(authorization: str | None = Header(default=None)):
    user_id = _user_from_header(authorization)
    if not user_id:
        return {"status": "unauthorized", "message": "유효한 인증 토큰이 필요합니다"}
    return await start_detection_internal(user_id)


@router.post("/stop-detection", summary="실시간 감지 중지 (본인)")
async def stop_detection(authorization: str | None = Header(default=None)):
    user_id = _user_from_header(authorization)
    if not user_id:
        return {"status": "unauthorized"}
    return await stop_detection_internal(user_id)


@router.get("/detection-status", summary="실시간 감지 상태 (본인)")
async def detection_status(authorization: str | None = Header(default=None)):
    user_id = _user_from_header(authorization)
    if not user_id:
        return {"running": False, "unauthorized": True}
    sess = _sessions.get(user_id) or {}
    return {"running": is_detection_running(user_id), "started_at": sess.get("started_at")}


@router.post("/reload-config", summary="실시간 설정 즉시 재동기화 (본인)")
async def reload_config(authorization: str | None = Header(default=None)):
    user_id = _user_from_header(authorization)
    if not user_id:
        return {"status": "unauthorized"}
    if not is_detection_running(user_id):
        return {"status": "not_running"}
    ev = (_sessions.get(user_id) or {}).get("reload_event")
    if ev is None:
        return {"status": "no_reload_event"}
    ev.set()
    return {"status": "triggered"}


@router.websocket("/ws")
async def app_websocket(websocket: WebSocket):
    """앱 클라이언트용 WebSocket — 본인 종목 가격만 중계. ?token=<JWT>로 식별."""
    token = websocket.query_params.get("token")
    user_id = auth_service.verify_supabase_jwt(token) if token else None
    if not user_id:
        await websocket.close(code=4401)
        return
    await _app_broadcaster.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()  # 클라이언트 disconnect 감지
    except WebSocketDisconnect:
        _app_broadcaster.disconnect(websocket, user_id)
    except Exception:
        _app_broadcaster.disconnect(websocket, user_id)
