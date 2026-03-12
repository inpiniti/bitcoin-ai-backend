"""
Yahoo Finance HTTP 요청 서비스
fetch_for_forecast : 종가 배열 + 마지막 날짜 반환
fetch_for_whale    : OHLCV 리스트 반환
"""
import logging
import httpx

logger = logging.getLogger("yahoo_service")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


async def fetch_for_forecast(symbol: str, interval: str) -> tuple[list[float], str]:
    """종가 배열과 마지막 날짜를 반환합니다."""
    if interval == "day":
        yahoo_interval, yahoo_range = "1d", "2y"
        include_pre_post = False
    elif interval == "minute":
        yahoo_interval, yahoo_range = "1m", "7d"
        include_pre_post = True
    else:  # hour
        yahoo_interval, yahoo_range = "1h", "60d"
        include_pre_post = True

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={yahoo_interval}&range={yahoo_range}&includePrePost={str(include_pre_post).lower()}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=HEADERS)

    if resp.status_code != 200:
        raise ValueError(f"Yahoo API Error: {resp.status_code}")

    data = resp.json()
    chart_result = data.get("chart", {}).get("result", [None])[0]
    if not chart_result:
        raise ValueError(f"No data found for {symbol}")

    timestamps = chart_result["timestamp"]
    closes = chart_result["indicators"]["quote"][0]["close"]

    valid_prices = [p for p in closes if p is not None]
    last_ts = timestamps[-1]

    from datetime import datetime, timezone
    last_date = datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()

    logger.info(f"[Yahoo] {symbol} {interval}: {len(valid_prices)} points")
    return valid_prices, last_date


async def fetch_for_whale(symbol: str, interval: str) -> list[dict]:
    """OHLCV 딕셔너리 리스트를 반환합니다."""
    if interval == "day":
        yahoo_interval, yahoo_range = "1d", "1y"
    else:
        yahoo_interval, yahoo_range = "1h", "60d"

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval={yahoo_interval}&range={yahoo_range}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=HEADERS)

    if resp.status_code != 200:
        raise ValueError(f"Yahoo API Error: {resp.status_code}")

    data = resp.json()
    chart_result = data.get("chart", {}).get("result", [None])[0]
    if not chart_result:
        raise ValueError(f"No data found for {symbol}")

    timestamps = chart_result["timestamp"]
    q = chart_result["indicators"]["quote"][0]
    closes, highs, lows, volumes = q["close"], q["high"], q["low"], q["volume"]

    market_data = []
    for i, ts in enumerate(timestamps):
        if closes[i] is None or volumes[i] is None or highs[i] is None or lows[i] is None:
            continue
        from datetime import datetime, timezone
        market_data.append({
            "timestamp": ts,
            "date": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
            "close": closes[i],
            "high": highs[i],
            "low": lows[i],
            "volume": volumes[i],
        })

    logger.info(f"[Yahoo/Whale] {symbol} {interval}: {len(market_data)} points")
    return market_data
