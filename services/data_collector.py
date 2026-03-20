"""
서버 사이드 데이터 수집 + 피처 엔지니어링
mlProcessor.js의 processStockDataForML 로직을 Python으로 포팅
"""
import asyncio
import logging
from typing import Callable

import httpx

logger = logging.getLogger("data_collector")


# ── 티커 그룹 목록 수집 ──────────────────────────────────────

async def fetch_tickers_for_group(group_key: str) -> list[str]:
    """그룹 키에 해당하는 티커 목록 반환"""
    if group_key == "sp500":
        return await _fetch_sp500()
    elif group_key == "qqq":
        return await _fetch_qqq()
    elif group_key in ("usall", "nasdaq_nyse"):
        return await _fetch_usall()
    elif group_key == "kospi200":
        return await _fetch_kospi200()
    elif group_key == "kosdaq150":
        return await _fetch_kosdaq150()
    elif group_key == "indices":
        return ["^GSPC", "^NDX", "^IXIC", "^DJI", "^RUT", "^VIX"]
    else:
        # 단일 티커로 취급
        return [group_key]


async def _fetch_sp500() -> list[str]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    tickers = []
    table = soup.find("table", {"id": "constituents"})
    if table:
        for row in table.find("tbody").find_all("tr"):
            cols = row.find_all("td")
            if cols:
                ticker = cols[0].text.strip().replace(".", "-")
                if ticker:
                    tickers.append(ticker)
    logger.info(f"[S&P500] {len(tickers)}개 종목 로드")
    return tickers


async def _fetch_qqq() -> list[str]:
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    tickers = []
    table = soup.find("table", {"id": "constituents"})
    if table:
        for row in table.find("tbody").find_all("tr"):
            cols = row.find_all("td")
            if cols:
                ticker = cols[0].text.strip().replace(".", "-")
                if ticker:
                    tickers.append(ticker)
    logger.info(f"[QQQ] {len(tickers)}개 종목 로드")
    return tickers


async def _fetch_usall() -> list[str]:
    """Nasdaq + NYSE 전체 (nasdaqtrader.com FTP)"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(timeout=60) as client:
        nasdaq_res, other_res = await asyncio.gather(
            client.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", headers=headers),
            client.get("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", headers=headers),
        )

    tickers = []

    if nasdaq_res.status_code == 200:
        lines = nasdaq_res.text.split("\n")[1:]  # 헤더 제거
        for line in lines:
            cols = line.split("|")
            if len(cols) < 7:
                continue
            ticker = cols[0].strip()
            test_issue = cols[3].strip()
            etf = cols[6].strip()
            if ticker and test_issue != "Y" and etf != "Y" and "File Creation" not in ticker and len(ticker) <= 5:
                tickers.append(ticker)
        logger.info(f"[USALL] NASDAQ: {len(tickers)}개")

    nasdaq_count = len(tickers)
    if other_res.status_code == 200:
        lines = other_res.text.split("\n")[1:]
        for line in lines:
            cols = line.split("|")
            if len(cols) < 7:
                continue
            ticker = cols[0].strip()
            etf = cols[4].strip()
            test_issue = cols[6].strip()
            if ticker and test_issue != "Y" and etf != "Y" and "File Creation" not in ticker and len(ticker) <= 5:
                tickers.append(ticker)
        logger.info(f"[USALL] NYSE/AMEX: {len(tickers) - nasdaq_count}개, 총 {len(tickers)}개")

    return tickers


async def _fetch_kospi200() -> list[str]:
    url = "https://ko.wikipedia.org/wiki/%EC%BD%94%EC%8A%A4%ED%94%BC_200"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    from bs4 import BeautifulSoup
    import re
    soup = BeautifulSoup(resp.text, "html.parser")
    tickers = []
    for table in soup.find_all("table", class_="wikitable"):
        if "삼성전자" in table.text:
            for row in table.find("tbody").find_all("tr"):
                cols = row.find_all("td")
                if len(cols) >= 2:
                    ticker = cols[1].text.strip()
                    if re.match(r"^\d{6}$", ticker):
                        tickers.append(ticker)
            break
    logger.info(f"[KOSPI200] {len(tickers)}개 종목 로드")
    return tickers


async def _fetch_kosdaq150() -> list[str]:
    # KOSDAQ 150은 별도 소스가 없어 빈 목록 반환
    logger.warning("[KOSDAQ150] 자동 수집 미지원 - 빈 목록 반환")
    return []


# ── Yahoo Finance 히스토리 수집 ───────────────────────────────

async def fetch_stock_history_yf(ticker: str, days: int) -> list[dict]:
    """yfinance로 주가 히스토리 수집, 캔들 리스트 반환"""
    try:
        import yfinance as yf
        import pandas as pd

        period_map = {
            30: "1mo",
            60: "3mo",
            90: "3mo",
            180: "6mo",
            365: "1y",
            730: "2y",
            1825: "5y",
        }
        period = period_map.get(days, "1y")
        if days > 1825:
            period = "max"

        # yfinance는 동기 라이브러리이므로 executor에서 실행
        loop = asyncio.get_event_loop()

        def _download():
            tkr = yf.Ticker(ticker)
            # max 미지원 종목(워런트 등)은 5y → 2y → 1y 순으로 폴백
            fallback_periods = [period] if period != "max" else ["max", "5y", "2y", "1y"]
            for p in fallback_periods:
                try:
                    hist = tkr.history(period=p)
                    if hist is not None and not hist.empty:
                        return hist
                except Exception:
                    continue
            return None

        hist = await loop.run_in_executor(None, _download)

        if hist is None or hist.empty:
            return []

        candles = []
        for ts, row in hist.iterrows():
            candles.append({
                "date": ts.strftime("%Y-%m-%d"),
                "open": float(row["Open"]) if not pd.isna(row["Open"]) else None,
                "high": float(row["High"]) if not pd.isna(row["High"]) else None,
                "low": float(row["Low"]) if not pd.isna(row["Low"]) else None,
                "close": float(row["Close"]) if not pd.isna(row["Close"]) else None,
                "volume": float(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
            })

        return candles
    except Exception as e:
        logger.warning(f"[YF] {ticker} 수집 실패: {e}")
        return []


# ── 피처 엔지니어링 (mlProcessor.js 포팅) ────────────────────

def process_stock_data_for_ml(candles: list[dict]) -> tuple[list, list]:
    """
    mlProcessor.js processStockDataForML 와 동일한 로직
    - 4개 피처: [consecutiveDays, change1d%, change7d%, change30d%]
    - 레이블: 다음날 2% 이상 상승 → 1, 아니면 → 0
    """
    features = []
    labels = []

    if not candles or len(candles) <= 30:
        return features, labels

    for i in range(30, len(candles) - 1):
        today = candles[i]
        tomorrow = candles[i + 1]

        if not today.get("close") or not tomorrow.get("close"):
            continue
        if not candles[i - 1].get("close"):
            continue

        # 1. 연속 상승/하락 일수
        consecutive_days = 0
        if today["close"] > candles[i - 1]["close"]:
            temp = 1
            while i - temp > 0 and candles[i - temp].get("close") and candles[i - temp - 1].get("close") and candles[i - temp]["close"] > candles[i - temp - 1]["close"]:
                consecutive_days += 1
                temp += 1
            if consecutive_days == 0:
                consecutive_days = 1
        elif today["close"] < candles[i - 1]["close"]:
            temp = 1
            while i - temp > 0 and candles[i - temp].get("close") and candles[i - temp - 1].get("close") and candles[i - temp]["close"] < candles[i - temp - 1]["close"]:
                consecutive_days -= 1
                temp += 1
            if consecutive_days == 0:
                consecutive_days = -1

        # 2. 변화율 (1일, 7일, 30일)
        def get_change_pct(days: int) -> float:
            past = candles[i - days]
            if not past or not past.get("close") or past["close"] == 0:
                return 0.0
            pct = ((today["close"] - past["close"]) / past["close"]) * 100
            if pct != pct:  # NaN check
                return 0.0
            return round(pct, 2)

        change1d = get_change_pct(1)
        change7d = get_change_pct(7)
        change30d = get_change_pct(30)

        features.append([consecutive_days, change1d, change7d, change30d])

        next_day_change = ((tomorrow["close"] - today["close"]) / today["close"]) * 100
        labels.append(1 if next_day_change >= 2.0 else 0)

    return features, labels


# ── 통합 수집 파이프라인 ──────────────────────────────────────

async def collect_and_train_data(
    group_key: str,
    period_days: int,
    single_ticker: str | None,
    progress_callback: Callable,
) -> tuple[list, list]:
    """
    티커 그룹(또는 단일 티커)에 대해 데이터를 수집하고 피처를 추출합니다.
    progress_callback(progress: int) 은 0~100 수집 진행률로 호출됩니다.
    """
    if single_ticker:
        await progress_callback(0)
        candles = await fetch_stock_history_yf(single_ticker, period_days)
        features, labels = process_stock_data_for_ml(candles)
        await progress_callback(100)
        logger.info(f"[Collector] {single_ticker}: {len(features)}개 샘플")
        return features, labels

    # 그룹 수집
    tickers = await fetch_tickers_for_group(group_key)
    if not tickers:
        raise ValueError(f"그룹 '{group_key}'에서 종목을 찾을 수 없습니다")

    logger.info(f"[Collector] {group_key}: {len(tickers)}개 종목 수집 시작")

    all_features: list = []
    all_labels: list = []
    total = len(tickers)
    BATCH_SIZE = 5

    for i in range(0, total, BATCH_SIZE):
        batch = tickers[i: i + BATCH_SIZE]

        results = await asyncio.gather(
            *[fetch_stock_history_yf(ticker, period_days) for ticker in batch],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, Exception) or not result:
                continue
            feats, labs = process_stock_data_for_ml(result)
            all_features.extend(feats)
            all_labels.extend(labs)

        progress = min(round(((i + BATCH_SIZE) / total) * 100), 99)
        await progress_callback(progress)

        # 약간의 딜레이로 Rate Limit 방지
        await asyncio.sleep(0.1)

    logger.info(f"[Collector] 수집 완료: 총 {len(all_features)}개 샘플")
    return all_features, all_labels
