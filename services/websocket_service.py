"""
KIS WebSocket 실시간 매매 감지 서비스
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Callable
import websockets
from supabase import create_client

logger = logging.getLogger(__name__)

class KISWebSocketManager:
    """KIS WebSocket 실시간 가격 감지 매니저"""

    def __init__(self, approval_key: str, user_id: str, supabase_url: str, supabase_key: str):
        self.approval_key = approval_key
        self.user_id = user_id
        self.ws = None
        self.is_connected = False
        self.supabase = create_client(supabase_url, supabase_key)
        self.price_callbacks: Dict[str, List[Callable]] = {}

    async def connect(self):
        """WebSocket 연결"""
        try:
            self.ws = await websockets.connect(
                'ws://ops.koreainvestment.com:21000',
                subprotocols=['livedata']
            )
            self.is_connected = True
            logger.info(f"WebSocket connected for user {self.user_id}")
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self.is_connected = False
            raise

    async def disconnect(self):
        """WebSocket 연결 해제"""
        if self.ws:
            await self.ws.close()
            self.is_connected = False
            logger.info(f"WebSocket disconnected for user {self.user_id}")

    async def subscribe_to_stock(self, ticker: str, market: str = 'NAS'):
        """종목 실시간 가격 구독"""
        if not self.is_connected:
            raise RuntimeError("WebSocket is not connected")

        # tr_key 생성: D + market + ticker (BRK-B → BRK/B)
        # 예: DNASAAPL, DNASBRK/B
        kis_ticker = ticker.upper().replace("-", "/")
        tr_key = f"D{market}{kis_ticker}"

        header = {
            'approval_key': self.approval_key,
            'tr_type': '1',
            'custtype': 'P',
            'content-type': 'utf-8'
        }

        body = {
            'tr_id': 'HDFSCNT0',
            'tr_key': tr_key
        }

        message = json.dumps({
            'header': header,
            'body': body
        })

        await self.ws.send(message)
        logger.info(f"Subscribed to {ticker} ({market})")

    async def unsubscribe_from_stock(self, ticker: str, market: str = 'NAS'):
        """종목 실시간 가격 구독 해제"""
        if not self.is_connected:
            raise RuntimeError("WebSocket is not connected")

        kis_ticker = ticker.upper().replace("-", "/")
        tr_key = f"D{market}{kis_ticker}"

        header = {
            'approval_key': self.approval_key,
            'tr_type': '2',  # 해제
            'custtype': 'P',
            'content-type': 'utf-8'
        }

        body = {
            'tr_id': 'HDFSCNT0',
            'tr_key': tr_key
        }

        message = json.dumps({
            'header': header,
            'body': body
        })

        await self.ws.send(message)
        logger.info(f"Unsubscribed from {ticker} ({market})")

    async def listen(self, on_price_update: Callable):
        """실시간 메시지 수신 및 처리"""
        if not self.is_connected:
            raise RuntimeError("WebSocket is not connected")

        try:
            async for message in self.ws:
                await self._handle_message(message, on_price_update)
        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed")
            self.is_connected = False
        except Exception as e:
            logger.error(f"Error listening to WebSocket: {e}")
            self.is_connected = False
            raise

    async def _handle_message(self, message: str, on_price_update: Callable):
        """메시지 처리"""
        try:
            # 메시지는 '^'로 구분된 필드들의 문자열
            if isinstance(message, str):
                fields = message.split('^')
                if len(fields) >= 25:
                    data = {
                        'RSYM': fields[0],      # 실시간종목코드
                        'SYMB': fields[1],      # 종목코드
                        'ZDIV': fields[2],      # 수수점자리수
                        'TYMD': fields[3],      # 현지영업일자
                        'XYMD': fields[4],      # 현지일자
                        'XHMS': fields[5],      # 현지시간
                        'KYMD': fields[6],      # 한국일자
                        'KHMS': fields[7],      # 한국시간
                        'OPEN': fields[8],      # 시가
                        'HIGH': fields[9],      # 고가
                        'LOW': fields[10],      # 저가
                        'LAST': fields[11],     # 현재가
                        'SIGN': fields[12],     # 대비구분
                        'DIFF': fields[13],     # 전일대비
                        'RATE': fields[14],     # 등락율
                        'PBID': fields[15],     # 매수호가
                        'PASK': fields[16],     # 매도호가
                        'VBID': fields[17],     # 매수잔량
                        'VASK': fields[18],     # 매도잔량
                        'EVOL': fields[19],     # 체결량
                        'TVOL': fields[20],     # 거래량
                        'TAMT': fields[21],     # 거래대금
                        'BIVL': fields[22],     # 매도체결량
                        'ASVL': fields[23],     # 매수체결량
                        'STRN': fields[24],     # 체결강도
                        'MTYP': fields[25] if len(fields) > 25 else '1',  # 시장구분
                    }
                    await on_price_update(data)
        except Exception as e:
            logger.error(f"Error handling message: {e}")


async def issue_websocket_key(appkey: str, appsecret: str) -> Optional[str]:
    """KIS WebSocket 접속키 발급"""
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                'https://openapi.koreainvestment.com:9443/oauth2/Approval',
                json={
                    'grant_type': 'client_credentials',
                    'appkey': appkey,
                    'secretkey': appsecret
                },
                headers={'Content-Type': 'application/json; utf-8'}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('approval_key')
                else:
                    logger.error(f"Failed to issue WebSocket key: {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"Error issuing WebSocket key: {e}")
        return None


def is_market_hours(mtyp: str) -> bool:
    """장중 여부 확인 (MTYP: 1:장중, 2:장전, 3:장후)"""
    return mtyp == '1'


def parse_price(value: str) -> float:
    """가격 문자열을 float로 변환"""
    try:
        return float(value) if value else 0.0
    except (ValueError, TypeError):
        return 0.0


def parse_rate(value: str) -> float:
    """등락율 문자열을 float로 변환 (% 제거)"""
    try:
        val = float(value) if value else 0.0
        return val
    except (ValueError, TypeError):
        return 0.0


async def handle_price_detection(
    ticker: str,
    market: str,
    current_price: float,
    base_price: float,
    gap: float,
    quantity: int,
    rate: float,
    mtyp: str,
    supabase_client,
    on_order_execute: Callable
):
    """가격 변동 감지 및 자동 매매 실행"""

    # 1. 장중이 아니면 무시
    if not is_market_hours(mtyp):
        return

    # 2. 등락율 계산
    price_rate = ((current_price - base_price) / base_price * 100) if base_price > 0 else 0

    common = {
        'ticker': ticker,
        'market': market,
        'price': current_price,
        'base_price_before': base_price,
        'price_rate': price_rate,
        'current_quantity': quantity,
    }

    # 3. gap% 이상 올랐을 때 (수량 = floor(올린율 / gap))
    if price_rate >= gap:
        sell_quantity = int(price_rate / gap)
        if quantity > 0 and sell_quantity > 0:
            actual_sell_qty = min(sell_quantity, quantity)
            await on_order_execute({
                **common,
                'side': 'sell',
                'quantity': actual_sell_qty,
                'action': 'sell_and_update',
            })
        else:
            await on_order_execute({
                **common,
                'side': 'none',
                'quantity': 0,
                'action': 'update_base_price',
            })

    # 4. gap% 이상 내렸을 때 (수량 = floor(내린율 / gap))
    elif price_rate <= -gap:
        price_drop_rate = abs(price_rate)
        buy_quantity = int(price_drop_rate / gap)

        if buy_quantity > 0:
            await on_order_execute({
                **common,
                'side': 'buy',
                'quantity': buy_quantity,
                'action': 'buy_and_update',
            })
        else:
            await on_order_execute({
                **common,
                'side': 'none',
                'quantity': 0,
                'action': 'update_base_price',
            })
