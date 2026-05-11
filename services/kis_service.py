"""
KIS (한국투자증권) API 서비스
kisApi.js 의 Python 포팅 버전

credentials 는 환경변수 대신 automation_settings(Supabase)에서 로드하여 주입합니다.
"""
import logging
from datetime import datetime, timezone, timedelta
import httpx

logger = logging.getLogger("kis_service")

KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"

# 토큰 인메모리 캐시 { appkey → {access_token, expires_at} }
_token_cache: dict[str, dict] = {}


def _normalize_order_price(price: float) -> float:
    """KIS 해외주식 주문 단가 형식에 맞게 소수점 2자리로 보정."""
    return round(float(price), 2)


def to_kis_ticker(ticker: str) -> str:
    """KIS API용 ticker 변환: BRK-B → BRK/B (해외주식 알파벳 클래스 종목)."""
    return str(ticker or "").strip().upper().replace("-", "/")


def parse_account(kis_account: str) -> tuple[str, str]:
    """
    automation_settings.kis_account 를 account_no(8자리) + account_code(2자리) 로 분리.
    형식: '12345678-01' 또는 '1234567801'
    """
    raw = kis_account.strip().replace("-", "")
    if len(raw) >= 10:
        return raw[:8], raw[8:10]
    return raw, "01"  # fallback


def _make_headers(access_token: str, appkey: str, appsecret: str, tr_id: str) -> dict:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": tr_id,
        "custtype": "P",
    }


async def get_access_token(appkey: str, appsecret: str) -> str:
    """KIS 액세스 토큰 발급 (appkey 단위로 캐시)"""
    now = datetime.now(timezone.utc)
    cached = _token_cache.get(appkey)
    if cached and cached["expires_at"] > now:
        return cached["access_token"]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            headers={"Content-Type": "application/json; charset=utf-8"},
            json={"grant_type": "client_credentials", "appkey": appkey, "appsecret": appsecret},
        )

    data = resp.json()
    if not data.get("access_token"):
        raise RuntimeError(f"KIS 토큰 발급 실패: {data.get('msg1', resp.text)}")

    expires_in = int(data.get("expires_in", 86400))
    _token_cache[appkey] = {
        "access_token": data["access_token"],
        "expires_at": now + timedelta(seconds=expires_in - 60),
    }
    logger.info(f"KIS 토큰 발급 완료 (appkey=...{appkey[-4:]})")
    return _token_cache[appkey]["access_token"]


async def get_overseas_balance(appkey: str, appsecret: str, account_no: str, account_code: str) -> dict:
    """해외주식 잔고 조회"""
    token = await get_access_token(appkey, appsecret)
    params = {
        "CANO": account_no.strip(),
        "ACNT_PRDT_CD": account_code.strip(),
        "WCRC_FRCR_DVSN_CD": "01",
        "NATN_CD": "840",
        "TR_MKET_CD": "00",
        "INQR_DVSN_CD": "00",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-present-balance",
            params=params,
            headers=_make_headers(token, appkey, appsecret, "CTRP6504R"),
        )
    data = resp.json()
    if data.get("rt_cd") == "0":
        # output2에서 USD 행 찾아 외화사용가능금액(frcr_dncl_amt_2) 추출
        output2 = data.get("output2", [])
        usd_row = next((r for r in output2 if r.get("crcy_cd") == "USD"), {})
        usd_available = float(usd_row.get("frcr_dncl_amt_2", 0) or 0)
        return {
            "success": True,
            "holdings": data.get("output1", []),
            "summary": data.get("output3", {}),
            "usd_available": usd_available,
        }
    return {"success": False, "error": data.get("msg1", "잔고 조회 실패")}


async def get_current_price(appkey: str, appsecret: str, exchange: str, symbol: str) -> dict:
    """해외주식 현재가 조회"""
    token = await get_access_token(appkey, appsecret)
    params = {"AUTH": "", "EXCD": exchange, "SYMB": to_kis_ticker(symbol)}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/overseas-price/v1/quotations/price-detail",
            params=params,
            headers=_make_headers(token, appkey, appsecret, "HHDFS76200200"),
        )
    data = resp.json()
    if data.get("rt_cd") == "0" and data.get("output"):
        last = data["output"].get("last", "") or ""
        if not last.strip():
            return {"success": False, "error": f"{symbol}: 시세 없음 (last 빈값)"}
        return {"success": True, "price": float(last), "exchange": exchange}
    return {"success": False, "error": data.get("msg1", "현재가 조회 실패")}


async def get_current_price_with_exchange_search(appkey: str, appsecret: str, ticker: str) -> dict:
    """NAS → NYS → AMS 순으로 현재가 조회"""
    for excd in ["NAS", "NYS", "AMS"]:
        result = await get_current_price(appkey, appsecret, excd, ticker)
        if result["success"]:
            return result
    return {"success": False, "error": f"{ticker}: 모든 거래소 조회 실패"}


async def _order_overseas_stock(
    appkey: str, appsecret: str, account_no: str, account_code: str,
    order_type: str, exchange: str, symbol: str, price: float, qty: int,
) -> dict:
    """매수/매도 공통 주문"""
    token = await get_access_token(appkey, appsecret)
    normalized_price = _normalize_order_price(price)
    exchange_map = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}
    tr_id = "TTTT1002U" if order_type == "buy" else "TTTT1006U"
    body = {
        "CANO": account_no,
        "ACNT_PRDT_CD": account_code,
        "OVRS_EXCG_CD": exchange_map.get(exchange, "NASD"),
        "PDNO": to_kis_ticker(symbol),
        "ORD_QTY": str(qty),
        "OVRS_ORD_UNPR": f"{normalized_price:.2f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order",
            json=body,
            headers=_make_headers(token, appkey, appsecret, tr_id),
        )
    data = resp.json()
    if data.get("rt_cd") == "0":
        return {"success": True, "order_no": data.get("output", {}).get("ODNO"), "message": data.get("msg1")}
    err = data.get("msg1", "주문 실패")
    code = data.get("msg_cd", "")
    return {"success": False, "error": f"[{code}] {err}" if code else err}


async def buy_overseas_stock(
    appkey: str, appsecret: str, account_no: str, account_code: str,
    ticker: str, qty: int, price: float, exchange: str = "NAS",
) -> dict:
    normalized_price = _normalize_order_price(price)
    logger.info(f"[KIS] 매수: {ticker} {qty}주 @ ${normalized_price:.2f} ({exchange})")
    return await _order_overseas_stock(appkey, appsecret, account_no, account_code, "buy", exchange, ticker, price, qty)


async def sell_overseas_stock(
    appkey: str, appsecret: str, account_no: str, account_code: str,
    ticker: str, qty: int, price: float, exchange: str = "NAS",
) -> dict:
    normalized_price = _normalize_order_price(price)
    logger.info(f"[KIS] 매도: {ticker} {qty}주 @ ${normalized_price:.2f} ({exchange})")
    return await _order_overseas_stock(appkey, appsecret, account_no, account_code, "sell", exchange, ticker, price, qty)
