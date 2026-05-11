"""
실시간 매매 라우터
감지 시작 등
"""
import logging
import os
from fastapi import APIRouter, BackgroundTasks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/realtime", tags=["realtime"])


@router.post(
    "/start-detection",
    summary="실시간 감지 시작",
    tags=["realtime"]
)
async def start_detection(
    approval_key: str,
    background_tasks: BackgroundTasks
):
    """
    실시간 가격 감지를 시작합니다.
    이 엔드포인트는 서버 인스턴스가 하나인 경우 자동으로 호출됩니다.
    """
    try:
        # 백그라운드 작업으로 실시간 감지 시작
        background_tasks.add_task(
            _start_realtime_detection,
            approval_key
        )
        return {"status": "started"}
    except Exception as e:
        logger.error(f"Error starting detection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _start_realtime_detection(approval_key: str):
    """
    실시간 감지 백그라운드 작업
    이 함수는 서버 시작 시 또는 명시적으로 호출될 수 있습니다.
    """
    import asyncio
    from supabase import create_client
    from services.websocket_service import KISWebSocketManager, handle_price_detection
    from services.auto_trade_service import execute_realtime_order

    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_ANON_KEY')

    if not supabase_url or not supabase_key:
        logger.error("Supabase credentials not found")
        return

    supabase = create_client(supabase_url, supabase_key)

    # 1. Supabase에서 활성 실시간 매매 설정 조회
    try:
        result = supabase.table('realtime_trading').select('*').eq('is_active', True).execute()
        active_trades = result.data if result.data else []
    except Exception as e:
        logger.error(f"Error fetching active trades: {e}")
        return

    if not active_trades:
        logger.info("No active realtime trades to monitor")
        return

    logger.info(f"Starting realtime detection for {len(active_trades)} trades")

    # 2. WebSocket 매니저 초기화
    manager = KISWebSocketManager(
        approval_key=approval_key,
        user_id='system',
        supabase_url=supabase_url,
        supabase_key=supabase_key
    )

    try:
        await manager.connect()

        # 3. 각 종목 구독
        for trade in active_trades:
            try:
                await manager.subscribe_to_stock(
                    ticker=trade['ticker'],
                    market=trade.get('market', 'NAS')
                )
            except Exception as e:
                logger.error(f"Error subscribing to {trade['ticker']}: {e}")

        # 4. 메시지 수신 및 처리
        async def on_price_update(data):
            try:
                # 수신한 ticker 조회
                ticker = data.get('SYMB', '').upper()
                rate = float(data.get('RATE', 0))
                mtyp = data.get('MTYP', '1')
                current_price = float(data.get('LAST', 0))

                # 해당 종목의 설정 찾기
                trade = next(
                    (t for t in active_trades if t['ticker'].upper() == ticker),
                    None
                )

                if not trade:
                    return

                await handle_price_detection(
                    ticker=ticker,
                    market=trade.get('market', 'NAS'),
                    current_price=current_price,
                    base_price=float(trade['base_price']),
                    gap=float(trade.get('gap', 1)),
                    quantity=int(trade.get('quantity', 0)),
                    rate=rate,
                    mtyp=mtyp,
                    supabase_client=supabase,
                    on_order_execute=lambda order_data: execute_realtime_order(
                        trade_id=trade['id'],
                        order_data=order_data
                    )
                )
            except Exception as e:
                logger.error(f"Error handling price update: {e}")

        await manager.listen(on_price_update)

    except Exception as e:
        logger.error(f"WebSocket detection error: {e}")
    finally:
        await manager.disconnect()
