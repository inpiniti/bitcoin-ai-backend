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


class InvalidApprovalError(Exception):
    """KIS WebSocket Approval Key가 유효하지 않거나 만료되었을 때 발생하는 예외"""
    pass


def _get_tr_id(market: str) -> str:
    """마켓에 따른 TR ID 결정"""
    if market in ('KRX', 'KOSDAQ'):
        return 'H0STCNT0'  # 국내주식
    else:
        return 'HDFSCNT0'  # 해외주식 (기본값)


class KISWebSocketManager:
    """KIS WebSocket 실시간 가격 감지 매니저"""

    def __init__(self, approval_key: str, user_id: str, supabase_url: str, supabase_key: str):
        self.approval_key = approval_key
        self.user_id = user_id
        self.ws = None
        self.is_connected = False
        self.supabase = create_client(supabase_url, supabase_key)
        self.price_callbacks: Dict[str, List[Callable]] = {}
        # 재연결 시 자동 재구독을 위해 활성 구독 추적: {(ticker, market)}
        self._active_subscriptions: set[tuple[str, str]] = set()

    async def connect(self):
        """WebSocket 연결"""
        try:
            self.ws = await websockets.connect(
                'ws://ops.koreainvestment.com:21000'
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

        tr_id = _get_tr_id(market)

        if market in ('KRX', 'KOSDAQ'):
            # 국내주식: 6자리 코드 그대로
            tr_key = ticker.strip().upper()
        else:
            # 해외주식: D + market + ticker
            # 점(.) → 슬래시(/), 하이픈(-) → 제거
            kis_ticker = ticker.upper().replace(".", "/").replace("-", "")
            tr_key = f"D{market}{kis_ticker}"

        header = {
            'approval_key': self.approval_key,
            'tr_type': '1',
            'custtype': 'P',
            'content-type': 'utf-8'
        }

        body = {
            'input': {
                'tr_id': tr_id,
                'tr_key': tr_key,
            }
        }

        message = json.dumps({
            'header': header,
            'body': body
        })

        await self.ws.send(message)
        self._active_subscriptions.add((ticker, market))
        logger.info(f"Subscribed to {ticker} ({market}) tr_id={tr_id} tr_key={tr_key}")

    async def unsubscribe_from_stock(self, ticker: str, market: str = 'NAS'):
        """종목 실시간 가격 구독 해제"""
        if not self.is_connected:
            raise RuntimeError("WebSocket is not connected")

        tr_id = _get_tr_id(market)

        if market in ('KRX', 'KOSDAQ'):
            tr_key = ticker.strip().upper()
        else:
            kis_ticker = ticker.upper().replace(".", "/").replace("-", "")
            tr_key = f"D{market}{kis_ticker}"

        header = {
            'approval_key': self.approval_key,
            'tr_type': '2',
            'custtype': 'P',
            'content-type': 'utf-8'
        }

        body = {
            'input': {
                'tr_id': tr_id,
                'tr_key': tr_key,
            }
        }

        message = json.dumps({
            'header': header,
            'body': body
        })

        await self.ws.send(message)
        self._active_subscriptions.discard((ticker, market))
        logger.info(f"Unsubscribed from {ticker} ({market})")

    async def _resubscribe_all(self):
        """재연결 후 활성 구독을 모두 다시 등록"""
        if not self._active_subscriptions:
            return
        logger.info(f"Resubscribing {len(self._active_subscriptions)} stocks after reconnect")
        for ticker, market in list(self._active_subscriptions):
            try:
                # subscribe_to_stock가 set에 이미 추가하므로 그대로 호출 가능
                await self.subscribe_to_stock(ticker, market)
            except Exception as e:
                logger.error(f"Resubscribe failed for {ticker} ({market}): {e}")

    async def listen(self, on_price_update: Callable, max_retries: int = 5):
        """실시간 메시지 수신 및 처리 (자동 재연결 기능)"""
        if not self.is_connected:
            raise RuntimeError("WebSocket is not connected")

        retry_count = 0
        while retry_count < max_retries:
            try:
                async for message in self.ws:
                    await self._handle_message(message, on_price_update)
            except websockets.exceptions.ConnectionClosed:
                logger.warning(f"WebSocket connection closed (retry {retry_count + 1}/{max_retries})")
                self.is_connected = False
                retry_count += 1

                if retry_count < max_retries:
                    wait_time = min(2 ** retry_count, 60)  # exponential backoff: 2s, 4s, 8s, 16s, 32s, 60s
                    logger.info(f"WebSocket will reconnect in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    try:
                        await self.connect()
                        logger.info("WebSocket reconnected successfully")
                        # KIS는 재연결 시 구독이 풀리므로 다시 등록
                        await self._resubscribe_all()
                        retry_count = 0  # 성공 시 재시도 카운터 리셋
                    except Exception as e:
                        logger.error(f"WebSocket reconnection failed: {e}")
                else:
                    logger.error(f"WebSocket max retries ({max_retries}) exceeded")
                    raise
            except Exception as e:
                logger.error(f"Error listening to WebSocket: {e}")
                self.is_connected = False
                raise

    async def _handle_message(self, message: str, on_price_update: Callable):
        """메시지 처리 (KIS WebSocket 형식: 0|HDFSCNT0|001|RSYM^SYMB^...)"""
        try:
            if isinstance(message, str):
                # JSON 응답(구독 등록 성공/실패, PINGPONG 등)은 데이터가 아님 — 내용만 로그로 남기고 종료
                if message.startswith('{'):
                    try:
                        resp = json.loads(message)
                        tr_id = (resp.get('header') or {}).get('tr_id', '')
                        body = resp.get('body') or {}
                        msg_cd = body.get('msg_cd', '')
                        msg1 = body.get('msg1', '')
                        # PINGPONG은 너무 많이 와서 debug로
                        if tr_id == 'PINGPONG':
                            logger.debug(f"[WebSocket] PINGPONG")
                        else:
                            logger.info(f"[WebSocket] KIS 응답 tr_id={tr_id} msg_cd={msg_cd} msg1={msg1}")
                            # 만료되거나 잘못된 approval_key인 경우 즉시 예외 발생
                            if msg_cd == 'OPSP0011' or 'invalid approval' in msg1.lower():
                                logger.error(f"[WebSocket] 유효하지 않은 KIS approval key 감지 (user={self.user_id}). 재연결을 중단합니다.")
                                raise InvalidApprovalError(f"유효하지 않은 KIS approval key: {msg1}")
                    except InvalidApprovalError:
                        raise
                    except Exception:
                        logger.info(f"[WebSocket] JSON 응답 파싱 실패: {message[:200]}")
                    return

                # 메시지를 |로 먼저 분리 (헤더와 데이터 분리)
                parts = message.split('|')
                if len(parts) < 4:
                    return

                tr_id = parts[1]

                if tr_id == 'H0STCNT0':
                    # 국내주식 실시간 체결가
                    data_str = parts[3]
                    fields = data_str.split('^')
                    if len(fields) < 6:
                        return

                    data = {
                        'SYMB': fields[0],              # 종목코드 (6자리)
                        'STCK_CNTG_HOUR': fields[1],    # 체결 시간
                        'STCK_PRPR': fields[2],         # 현재가
                        'PRDY_VRSS_SIGN': fields[3],    # 대비구분
                        'PRDY_VRSS': fields[4],         # 전일대비
                        'PRDY_CTRT': fields[5],         # 전일대비율
                        'ACML_VOL': fields[13] if len(fields) > 13 else '0',  # 누적거래량
                        'MARKET_TYPE': 'domestic',
                    }
                    logger.info(f"[WebSocket] 국내 데이터 수신 - {data['SYMB']}: {data['STCK_PRPR']} ({data['STCK_CNTG_HOUR']})")
                    await on_price_update(data)

                elif tr_id == 'HDFSCNT0':
                    # 해외주식 실시간 가격
                    data_str = parts[3]
                    fields = data_str.split('^')
                    if len(fields) < 25:
                        return

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
                        'MARKET_TYPE': 'overseas',
                    }
                    mtyp_label = {'1': '장중', '2': '장전', '3': '장후'}.get(data['MTYP'], f"MTYP={data['MTYP']}")
                    logger.info(f"[WebSocket] 해외 데이터 수신 - {data['SYMB']}: {data['LAST']} ({data['KHMS']}, {mtyp_label})")
                    await on_price_update(data)
        except InvalidApprovalError:
            raise
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
    """장전/장중 여부 확인 (MTYP: 1:장중, 2:장전, 3:장후)"""
    return mtyp in ('1', '2')


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
    gap_qty: int,
    rate: float,
    mtyp: str,
    supabase_client,
    on_order_execute: Callable,
    ask_price: float = 0.0,
    bid_price: float = 0.0,
    grid_step: int = 0,
):
    """가격 변동 감지 및 자동 매매 실행 (장중에만 매매)

    gap_qty: 갭 도달 시 매수/매도할 수량 (설정값)
    quantity: 현재 보유 수량 (매매 후 자동 갱신됨)
    ask_price/bid_price: 실시간 매도호가/매수호가 (즉시체결 유도용, 없으면 현재가 폴백)

    - 가격이 gap% 이상 오르면 gap_qty만큼 매도 (단, 보유수량 부족 시 보유수량만큼만)
    - 가격이 gap% 이상 내리면 gap_qty만큼 매수
    """

    # 1. 장전/장중이 아니면 매매 스킵 (수신/표시는 호출자에서 이미 처리됨)
    if not is_market_hours(mtyp):
        mtyp_label = {'3': '장후'}.get(mtyp, f"MTYP={mtyp}")
        logger.debug(f"[Realtime] {ticker} 매매 스킵 ({mtyp_label})")
        return

    # 2. 등락율 계산
    price_rate = ((current_price - base_price) / base_price * 100) if base_price > 0 else 0

    common = {
        'ticker': ticker,
        'market': market,
        'price': current_price,
        'ask': ask_price,
        'bid': bid_price,
        'base_price_before': base_price,
        'price_rate': price_rate,
        'current_quantity': quantity,
        'grid_step': grid_step,
    }

    gap_qty = max(int(gap_qty or 0), 0)

    # 2. 보유량이 0이면서 기준가 대비 현재가가 상승(+)한 경우, 기준가를 현재가(감지이래 최고가)로 즉시 업데이트 (요구사항 1)
    if quantity == 0 and price_rate > 0:
        await on_order_execute({
            **common,
            'side': 'none',
            'quantity': 0,
            'action': 'update_base_price',
        })
        return

    # 3. gap% 이상 올랐을 때 → 등비수열 수량만큼 매도 (보유 부족 시 보유량만큼만)
    if price_rate >= gap:
        # 등비수열 매도 수량 계산 (요구사항 3): base_qty * (1.1 ** (grid_step - 1))
        step = max(1, grid_step)
        target_sell_qty = max(1, round(gap_qty * (1.1 ** (step - 1))))
        actual_sell_qty = min(target_sell_qty, quantity)
        if actual_sell_qty > 0:
            await on_order_execute({
                **common,
                'side': 'sell',
                'quantity': actual_sell_qty,
                'action': 'sell_and_update',
            })
        # 매도할 보유량이 없으면 아무 것도 하지 않는다 (하락 시 기준가 슬라이딩 금지).
        # 기준가 슬라이딩은 오직 '신고점 + 보유 0'(section 2)에서만 일어난다.

    # 4. gap% 이상 내렸을 때 → 등비수열 수량만큼 매수
    elif price_rate <= -gap:
        if gap_qty > 0:
            # 등비수열 매수 수량 계산 (요구사항 3): base_qty * (1.1 ** grid_step)
            buy_qty = max(1, round(gap_qty * (1.1 ** grid_step)))
            await on_order_execute({
                **common,
                'side': 'buy',
                'quantity': buy_qty,
                'action': 'buy_and_update',
            })
        # gap_qty=0 등으로 매수 수량이 없어도 기준가를 끌어내리지 않는다 (하락 슬라이딩 금지).
