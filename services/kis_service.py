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
    """KIS API용 ticker 변환 (웹 bitcoin-simulation/kisWebSocket.js와 동일):
       - 점(.) → 슬래시(/) : BRK.B → BRK/B
       - 하이픈(-) → 제거   : BRK-B → BRKB
    """
    return str(ticker or "").strip().upper().replace(".", "/").replace("-", "")


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
    headers = _make_headers(token, appkey, appsecret, tr_id)
    url = f"{KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                json=body,
                headers=headers,
            )
        data = resp.json()
    except Exception as e:
        from services import supabase_service
        masked_headers = {k: ("***" if k.lower() in ["appsecret", "authorization"] else v) for k, v in headers.items()}
        err_msg = f"해외 주문 HTTP 통신 예외 발생: {str(e)}"
        await supabase_service.save_kis_debug_log(
            krw_data={},
            usd_data={
                "url": url,
                "headers": masked_headers,
                "body": body,
                "error": err_msg
            },
            notes=err_msg
        )
        return {"success": False, "error": err_msg}

    if data.get("rt_cd") == "0":
        return {"success": True, "order_no": data.get("output", {}).get("ODNO"), "message": data.get("msg1")}
    err = data.get("msg1", "주문 실패")
    code = data.get("msg_cd", "")
    err_full = f"[{code}] {err}" if code else err

    from services import supabase_service
    masked_headers = {k: ("***" if k.lower() in ["appsecret", "authorization"] else v) for k, v in headers.items()}
    await supabase_service.save_kis_debug_log(
        krw_data={},
        usd_data={
            "url": url,
            "headers": masked_headers,
            "body": body,
            "response": data
        },
        notes=f"해외 {order_type.upper()} 주문 KIS 에러: {err_full}"
    )
    return {"success": False, "error": err_full}


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


async def get_overseas_unfilled_orders(
    appkey: str, appsecret: str, account_no: str, account_code: str,
    excg_cd: str = "NASD",
) -> dict:
    """해외주식 미체결내역 조회 (TTTS3018R, 실전 전용).

    excg_cd: NASD 입력 시 미국 전체(나스닥+뉴욕+아멕스) 미체결 조회.
    반환 orders 항목 주요 필드: odno(주문번호), pdno(종목), sll_buy_dvsn_cd(01매도/02매수),
    ft_ord_qty(주문수량), ft_ccld_qty(체결수량), nccs_qty(미체결수량),
    ord_dt(주문일자 YYYYMMDD), ord_tmd(주문시각 HHMMSS), ovrs_excg_cd(거래소).
    """
    token = await get_access_token(appkey, appsecret)
    params = {
        "CANO": account_no.strip(),
        "ACNT_PRDT_CD": account_code.strip(),
        "OVRS_EXCG_CD": excg_cd,
        "SORT_SQN": "",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-nccs",
            params=params,
            headers=_make_headers(token, appkey, appsecret, "TTTS3018R"),
        )
    data = resp.json()
    if data.get("rt_cd") == "0":
        return {"success": True, "orders": data.get("output", []) or []}
    return {"success": False, "error": data.get("msg1", "미체결내역 조회 실패")}


async def cancel_overseas_order(
    appkey: str, appsecret: str, account_no: str, account_code: str,
    ticker: str, org_order_no: str, qty: int, excg_cd: str = "NASD",
) -> dict:
    """해외주식 주문 취소 (TTTT1004U, 미국 정정취소).

    org_order_no: 원주문번호(ODNO) — 미체결내역/주문 API output의 ODNO.
    excg_cd: NASD/NYSE/AMEX 등 원주문 거래소코드. 취소 시 단가는 "0".
    """
    token = await get_access_token(appkey, appsecret)
    body = {
        "CANO": account_no.strip(),
        "ACNT_PRDT_CD": account_code.strip(),
        "OVRS_EXCG_CD": excg_cd,
        "PDNO": to_kis_ticker(ticker),
        "ORGN_ODNO": str(org_order_no),
        "RVSE_CNCL_DVSN_CD": "02",  # 02: 취소
        "ORD_QTY": str(qty),
        "OVRS_ORD_UNPR": "0",       # 취소 시 0
        "ORD_SVR_DVSN_CD": "0",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order-rvsecncl",
            json=body,
            headers=_make_headers(token, appkey, appsecret, "TTTT1004U"),
        )
    data = resp.json()
    if data.get("rt_cd") == "0":
        logger.info(f"[KIS] 주문취소 성공: {ticker} ODNO={org_order_no}")
        return {"success": True, "order_no": data.get("output", {}).get("ODNO"), "message": data.get("msg1")}
    err = data.get("msg1", "취소 실패")
    code = data.get("msg_cd", "")
    return {"success": False, "error": f"[{code}] {err}" if code else err}


# ============================================================
# 국내주식 API (1단계 이후)
# ============================================================

async def get_domestic_unfilled_orders(
    appkey: str, appsecret: str, account_no: str, account_code: str
) -> dict:
    """국내주식 미체결내역 조회 (TTTC8011R, 실전 전용).
    
    반환 orders 항목 주요 필드: odno(주문번호), pdno(종목), sll_buy_dvsn_cd(01매도/02매수),
    ord_qty(주문수량), cntg_qty(체결수량), rmnd_qty(미체결수량),
    ord_dt(주문일자 YYYYMMDD), ord_tmd(주문시각 HHMMSS).
    """
    token = await get_access_token(appkey, appsecret)
    params = {
        "CANO": account_no.strip(),
        "ACNT_PRDT_CD": account_code.strip(),
        "INQR_DVSN_1": "0",  # 조회구분1 (0: 전체, 1: 단가순, 2: 주문일시순)
        "INQR_DVSN_2": "0",  # 조회구분2 (0: 전체, 1: 매도, 2: 매수)
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-nccs",
            params=params,
            headers=_make_headers(token, appkey, appsecret, "TTTC8011R"),
        )
    data = resp.json()
    if data.get("rt_cd") == "0":
        return {"success": True, "orders": data.get("output", []) or []}
    return {"success": False, "error": data.get("msg1", "국내 미체결내역 조회 실패")}


async def get_domestic_balance(appkey: str, appsecret: str, account_no: str, account_code: str) -> dict:
    """국내주식 잔고 조회 (TTTC8434R 실전 / VTTC8434R 모의)"""
    token = await get_access_token(appkey, appsecret)
    params = {
        "CANO": account_no.strip(),
        "ACNT_PRDT_CD": account_code.strip(),
        "AFHR_FLPR_YN": "N",
        "INQR_DVSN": "01",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance",
            params=params,
            headers=_make_headers(token, appkey, appsecret, "TTTC8434R"),
        )
    data = resp.json()
    if data.get("rt_cd") == "0":
        return {
            "success": True,
            "holdings": data.get("output1", []),
            "summary": data.get("output2", [{}])[0] if data.get("output2") else {},
        }
    return {"success": False, "error": data.get("msg1", "잔고 조회 실패")}


async def buy_domestic_stock(
    appkey: str, appsecret: str, account_no: str, account_code: str,
    ticker: str, qty: int, price: float = 0,
) -> dict:
    """국내주식 매수 (TTTC0012U 실전 / VTTC0012U 모의)

    price=0 시 시장가로 주문 (ORD_DVSN=01).
    price>0 시 지정가로 주문 (ORD_DVSN=00).
    """
    token = await get_access_token(appkey, appsecret)
    ord_dvsn = "01" if price <= 0 else "00"
    ord_unpr = "0" if price <= 0 else str(int(price))

    body = {
        "CANO": account_no.strip(),
        "ACNT_PRDT_CD": account_code.strip(),
        "PDNO": ticker.strip(),
        "ORD_DVSN": ord_dvsn,
        "ORD_QTY": str(qty),
        "ORD_UNPR": ord_unpr,
    }
    headers = _make_headers(token, appkey, appsecret, "TTTC0012U")
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    logger.info(f"[KIS] 국내 매수 요청 - 헤더: {headers}, 바디: {body}")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                json=body,
                headers=headers,
            )
        data = resp.json()
    except Exception as e:
        from services import supabase_service
        masked_headers = {k: ("***" if k.lower() in ["appsecret", "authorization"] else v) for k, v in headers.items()}
        err_msg = f"국내 매수 HTTP 통신 예외 발생: {str(e)}"
        await supabase_service.save_kis_debug_log(
            krw_data={
                "url": url,
                "headers": masked_headers,
                "body": body,
                "error": err_msg
            },
            usd_data={},
            notes=err_msg
        )
        return {"success": False, "error": err_msg}

    logger.info(f"[KIS] 국내 매수 응답: {data}")
    if data.get("rt_cd") == "0":
        order_info = data.get("output", {})
        logger.info(f"[KIS] 국내 매수: {ticker} {qty}주 @ {ord_unpr} (ODNO={order_info.get('ODNO')})")
        return {"success": True, "order_no": order_info.get("ODNO"), "message": data.get("msg1")}
    err = data.get("msg1", "매수 실패")
    code = data.get("msg_cd", "")
    err_full = f"[{code}] {err}" if code else err

    from services import supabase_service
    masked_headers = {k: ("***" if k.lower() in ["appsecret", "authorization"] else v) for k, v in headers.items()}
    await supabase_service.save_kis_debug_log(
        krw_data={
            "url": url,
            "headers": masked_headers,
            "body": body,
            "response": data
        },
        usd_data={},
        notes=f"국내 매수 KIS 에러: {err_full}"
    )
    return {"success": False, "error": err_full}


async def sell_domestic_stock(
    appkey: str, appsecret: str, account_no: str, account_code: str,
    ticker: str, qty: int, price: float = 0,
) -> dict:
    """국내주식 매도 (TTTC0011U 실전 / VTTC0011U 모의)

    price=0 시 시장가로 주문 (ORD_DVSN=01).
    price>0 시 지정가로 주문 (ORD_DVSN=00).
    """
    token = await get_access_token(appkey, appsecret)
    ord_dvsn = "01" if price <= 0 else "00"
    ord_unpr = "0" if price <= 0 else str(int(price))

    body = {
        "CANO": account_no.strip(),
        "ACNT_PRDT_CD": account_code.strip(),
        "PDNO": ticker.strip(),
        "ORD_DVSN": ord_dvsn,
        "ORD_QTY": str(qty),
        "ORD_UNPR": ord_unpr,
    }
    headers = _make_headers(token, appkey, appsecret, "TTTC0011U")
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    logger.info(f"[KIS] 국내 매도 요청 - 헤더: {headers}, 바디: {body}")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                json=body,
                headers=headers,
            )
        data = resp.json()
    except Exception as e:
        from services import supabase_service
        masked_headers = {k: ("***" if k.lower() in ["appsecret", "authorization"] else v) for k, v in headers.items()}
        err_msg = f"국내 매도 HTTP 통신 예외 발생: {str(e)}"
        await supabase_service.save_kis_debug_log(
            krw_data={
                "url": url,
                "headers": masked_headers,
                "body": body,
                "error": err_msg
            },
            usd_data={},
            notes=err_msg
        )
        return {"success": False, "error": err_msg}

    logger.info(f"[KIS] 국내 매도 응답: {data}")
    if data.get("rt_cd") == "0":
        order_info = data.get("output", {})
        logger.info(f"[KIS] 국내 매도: {ticker} {qty}주 @ {ord_unpr} (ODNO={order_info.get('ODNO')})")
        return {"success": True, "order_no": order_info.get("ODNO"), "message": data.get("msg1")}
    err = data.get("msg1", "매도 실패")
    code = data.get("msg_cd", "")
    err_full = f"[{code}] {err}" if code else err

    from services import supabase_service
    masked_headers = {k: ("***" if k.lower() in ["appsecret", "authorization"] else v) for k, v in headers.items()}
    await supabase_service.save_kis_debug_log(
        krw_data={
            "url": url,
            "headers": masked_headers,
            "body": body,
            "response": data
        },
        usd_data={},
        notes=f"국내 매도 KIS 에러: {err_full}"
    )
    return {"success": False, "error": err_full}
