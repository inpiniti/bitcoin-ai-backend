"""
서버 사이드 데이터 수집 + 피처 엔지니어링
mlProcessor.js의 processStockDataForML 로직을 Python으로 포팅
"""
import asyncio
import logging
from typing import Callable

import httpx

logger = logging.getLogger("data_collector")

# ── 피처 Stage 상수 ────────────────────────────────────────────
# 2의 거듭제곱 거래일 lookback (거래일 기준)
STAGE_LOOKBACKS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]


def get_stage_lookbacks(stage: int) -> list[int]:
    """stage(1~11)에 해당하는 lookback 목록 반환"""
    stage = max(1, min(stage, len(STAGE_LOOKBACKS)))
    return STAGE_LOOKBACKS[:stage]


def get_required_calendar_days(stage: int, min_rows: int = 200) -> int:
    """학습에 필요한 최소 캘린더 일수 (여유 포함)"""
    stage = max(1, min(stage, len(STAGE_LOOKBACKS)))
    max_lookback = STAGE_LOOKBACKS[stage - 1]
    total_trading = max_lookback + min_rows
    return int(total_trading * (365 / 250)) + 30


def get_max_achievable_stage(available_candles: int, min_rows: int = 50) -> int:
    """보유 캔들 수 기준으로 학습 가능한 최대 stage 반환"""
    for stage in range(len(STAGE_LOOKBACKS), 0, -1):
        if available_candles > STAGE_LOOKBACKS[stage - 1] + min_rows:
            return stage
    return 1


def _days_to_yf_period(days: int) -> str:
    """캘린더 일수를 yfinance period 문자열로 변환"""
    if days <= 30:   return "1mo"
    if days <= 90:   return "3mo"
    if days <= 180:  return "6mo"
    if days <= 365:  return "1y"
    if days <= 730:  return "2y"
    if days <= 1825: return "5y"
    return "max"


def _calc_consecutive_days(candles: list[dict], i: int) -> int:
    """i번째 캔들의 연속 상승/하락 일수 계산"""
    today = candles[i]
    if not today.get("close") or not candles[i - 1].get("close"):
        return 0
    consecutive = 0
    if today["close"] > candles[i - 1]["close"]:
        temp = 1
        while (i - temp > 0
               and candles[i - temp].get("close")
               and candles[i - temp - 1].get("close")
               and candles[i - temp]["close"] > candles[i - temp - 1]["close"]):
            consecutive += 1
            temp += 1
        return consecutive if consecutive > 0 else 1
    elif today["close"] < candles[i - 1]["close"]:
        temp = 1
        while (i - temp > 0
               and candles[i - temp].get("close")
               and candles[i - temp - 1].get("close")
               and candles[i - temp]["close"] < candles[i - temp - 1]["close"]):
            consecutive -= 1
            temp += 1
        return consecutive if consecutive < 0 else -1
    return 0


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
    elif group_key == "krx300":
        return await _fetch_krx300()
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
    """Nasdaq + NYSE 전체 (nasdaqtrader.com FTP)
    주의: 정상적인 주식만 필터링 (warrants, preferred stocks, delisted 제외)
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    async with httpx.AsyncClient(timeout=60) as client:
        nasdaq_res, other_res = await asyncio.gather(
            client.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", headers=headers),
            client.get("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", headers=headers),
        )

    tickers = []

    def _is_valid_ticker(ticker: str) -> bool:
        """유효한 주식 티커만 필터링"""
        if not ticker:
            return False
        # $ 포함 (preferred stock, warrant) 제외
        if "$" in ticker:
            return False
        # . 포함하면 대부분 특수 주식 (warrants, units 등), 한국주식(-로 표시된 것) 제외
        if "." in ticker:
            return False
        # 길이 체크: 1-5자 (nasdaq.com 규칙)
        if len(ticker) > 5 or len(ticker) < 1:
            return False
        # 알파벳 + 숫자만 (특수문자 제외)
        if not all(c.isalnum() for c in ticker):
            return False
        return True

    if nasdaq_res.status_code == 200:
        lines = nasdaq_res.text.split("\n")[1:]  # 헤더 제거
        for line in lines:
            cols = line.split("|")
            if len(cols) < 7:
                continue
            ticker = cols[0].strip()
            test_issue = cols[3].strip()
            etf = cols[6].strip()
            # 테스트 이슈, ETF 제외
            if test_issue != "Y" and etf != "Y" and "File Creation" not in ticker and _is_valid_ticker(ticker):
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
            # 테스트 이슈, ETF 제외
            if test_issue != "Y" and etf != "Y" and "File Creation" not in ticker and _is_valid_ticker(ticker):
                tickers.append(ticker)
        logger.info(f"[USALL] NYSE/AMEX: {len(tickers) - nasdaq_count}개, 총 {len(tickers)}개")

    return tickers


async def _fetch_naver_index_stocks(index_code: str) -> list[str]:
    """
    네이버 증권 모바일 API를 사용하여 특정 지수의 구성 종목 목록을 페이지네이션 순회하여 긁어옵니다.
    index_code 예시: KPI200 (코스피 200), KOSDAQ150 (코스닥 150), KRX300 (KRX 300)
    """
    tickers = []
    page = 1
    async with httpx.AsyncClient(timeout=15) as client:
        while page <= 25:
            url = f"https://m.stock.naver.com/api/index/{index_code}/enrollStocks?page={page}"
            try:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                if resp.status_code != 200:
                    break
                data = resp.json()
                stocks = data.get("stocks") or []
                if not stocks:
                    break
                for s in stocks:
                    item_code = s.get("itemCode")
                    if item_code and item_code.isdigit() and len(item_code) == 6:
                        tickers.append(item_code)
                page += 1
            except Exception as e:
                logger.warning(f"[NaverIndex] {index_code} page {page} 수집 중 오류: {e}")
                break
    return tickers


async def _fetch_kospi200() -> list[str]:
    # 1. 네이버 API 우선 시도
    tickers = await _fetch_naver_index_stocks("KPI200")
    if tickers:
        logger.info(f"[KOSPI200] 네이버 API를 통해 {len(tickers)}개 종목 수집 성공")
        return tickers

    # 2. 실패 시 위키백과 폴백
    logger.info("[KOSPI200] 네이버 API 실패로 위키백과 폴백 작동")
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
    logger.info(f"[KOSPI200] 위키백과 폴백 결과 {len(tickers)}개 종목 로드")
    return tickers


async def _fetch_kosdaq150() -> list[str]:
    """KOSDAQ 150 지수 구성 종목"""
    # 1. 네이버 API 우선 시도
    tickers = await _fetch_naver_index_stocks("KOSDAQ150")
    if tickers:
        logger.info(f"[KOSDAQ150] 네이버 API를 통해 {len(tickers)}개 종목 수집 성공")
        return tickers

    # 2. 실패 시 위키백과 폴백
    logger.info("[KOSDAQ150] 네이버 API 실패로 위키백과 폴백 작동")
    url = "https://ko.wikipedia.org/wiki/%EC%BD%94%EC%8A%A4%EB%8B%A5_150"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        import re
        soup = BeautifulSoup(resp.text, "html.parser")
        tickers = []
        for table in soup.find_all("table", class_="wikitable"):
            if "종목명" in table.text or "코드" in table.text:
                tbody = table.find("tbody")
                if tbody:
                    for row in tbody.find_all("tr"):
                        cols = row.find_all("td")
                        if len(cols) >= 2:
                            ticker = cols[0].text.strip() if cols else ""
                            if not ticker:
                                ticker = cols[1].text.strip() if len(cols) > 1 else ""
                            if re.match(r"^\d{6}$", ticker):
                                tickers.append(ticker)
        logger.info(f"[KOSDAQ150] 위키백과 폴백 결과 {len(tickers)}개 종목 로드")
        return tickers
    except Exception as e:
        logger.warning(f"[KOSDAQ150] 자동 수집 실패: {e} - 빈 목록 반환")
        return []


async def _fetch_krx300() -> list[str]:
    """KRX 300 지수 구성 종목 (네이버 API 전용)"""
    tickers = await _fetch_naver_index_stocks("KRX300")
    logger.info(f"[KRX300] 네이버 API를 통해 {len(tickers)}개 종목 수집 완료")
    return tickers


# ── Yahoo Finance 히스토리 수집 ───────────────────────────────

async def fetch_stock_history_yf(ticker: str, days: int) -> list[dict]:
    """yfinance로 주가 히스토리 수집, 캔들 리스트 반환"""
    try:
        import yfinance as yf
        import pandas as pd

        period = _days_to_yf_period(days)

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


# ── 피처 엔지니어링 ───────────────────────────────────────────

def process_stock_data_for_ml(candles: list[dict], stage: int = 6) -> tuple[list, list]:
    """
    TO-BE: 2의 거듭제곱 lookback 기반 피처 엔지니어링
    - 피처: [consecutiveDays, change1d%, change2d%, change4d%, ...] (stage에 따라 개수 증가)
    - 레이블: 다음날 상승(>0%) → 1, 아니면 → 0
    """
    lookbacks = get_stage_lookbacks(stage)
    max_lookback = max(lookbacks)
    features = []
    labels = []

    if not candles or len(candles) <= max_lookback + 1:
        return features, labels

    for i in range(max_lookback, len(candles) - 1):
        today = candles[i]
        tomorrow = candles[i + 1]

        if not today.get("close") or not tomorrow.get("close"):
            continue

        consecutive = _calc_consecutive_days(candles, i)

        changes = []
        valid = True
        for lb in lookbacks:
            past = candles[i - lb]
            if not past or not past.get("close") or past["close"] == 0:
                valid = False
                break
            pct = ((today["close"] - past["close"]) / past["close"]) * 100
            changes.append(round(pct, 2) if pct == pct else 0.0)

        if not valid:
            continue

        features.append([consecutive] + changes)

        next_day_change = ((tomorrow["close"] - today["close"]) / today["close"]) * 100
        labels.append(1 if next_day_change > 0 else 0)

    return features, labels


# ── 예측용 피처 추출 (레이블 없음, 마지막 날까지 포함) ──────────────

def process_stock_data_for_prediction(candles: list[dict], stage: int = 6) -> tuple[list, list, list, list]:
    """
    TO-BE: stage 기반 피처 추출 (레이블 없음, 마지막 날까지 포함)
    Returns: (features, dates, raw_features, actuals)
    raw_features 딕셔너리 키: consecutiveDays, change1d, change2d, change4d, ... (stage에 따라 동적)
    """
    lookbacks = get_stage_lookbacks(stage)
    max_lookback = max(lookbacks)
    features = []
    dates = []
    raw_features = []
    actuals = []

    if not candles or len(candles) <= max_lookback:
        return features, dates, raw_features, actuals

    for i in range(max_lookback, len(candles)):
        today = candles[i]
        if not today.get("close"):
            continue

        consecutive = _calc_consecutive_days(candles, i)

        changes = []
        raw = {"consecutiveDays": consecutive}
        valid = True
        for lb in lookbacks:
            past = candles[i - lb]
            if not past or not past.get("close") or past["close"] == 0:
                valid = False
                break
            pct = ((today["close"] - past["close"]) / past["close"]) * 100
            val = round(pct, 2) if pct == pct else 0.0
            changes.append(val)
            raw[f"change{lb}d"] = val

        if not valid:
            continue

        features.append([consecutive] + changes)
        dates.append(today.get("date", ""))
        raw_features.append(raw)

        if (i + 1 < len(candles)
                and candles[i + 1].get("close")
                and today["close"] != 0):
            next_close = candles[i + 1]["close"]
            actual_change = ((next_close - today["close"]) / today["close"]) * 100
            actuals.append(round(actual_change, 2))
        else:
            actuals.append(None)

    return features, dates, raw_features, actuals


# ── 통합 수집 파이프라인 ──────────────────────────────────────

async def collect_and_train_data(
    group_key: str,
    period_days: int,
    single_ticker: str | None,
    progress_callback: Callable,
    stage: int = 6,
) -> tuple[list, list, int]:
    """
    티커 그룹(또는 단일 티커)에 대해 데이터를 수집하고 피처를 추출합니다.
    - period_days: 사용자가 선택한 학습 기간(캘린더 일수)
    - stage: 피처 단계 (1~11). 데이터 부족 시 자동 하향 조정됨.
    Returns: (features, labels, actual_stage)
    """
    # stage에 필요한 최소 캘린더 일수보다 크게 설정
    required_days = get_required_calendar_days(stage)
    effective_days = max(period_days, required_days)
    yf_period = _days_to_yf_period(effective_days)

    if single_ticker:
        await progress_callback(0)
        candles = await fetch_stock_history_yf(single_ticker, effective_days)

        # 데이터 부족 시 stage 자동 하향
        actual_stage = min(stage, get_max_achievable_stage(len(candles)))
        if actual_stage < stage:
            logger.warning(
                f"[Collector] {single_ticker}: 캔들 {len(candles)}개 — "
                f"stage {stage}→{actual_stage} 자동 조정"
            )

        features, labels = process_stock_data_for_ml(candles, actual_stage)
        await progress_callback(100)
        logger.info(f"[Collector] {single_ticker}: {len(features)}개 샘플 (stage={actual_stage})")
        return features, labels, actual_stage

    # 그룹 수집
    tickers = await fetch_tickers_for_group(group_key)
    if not tickers:
        raise ValueError(f"그룹 '{group_key}'에서 종목을 찾을 수 없습니다")

    logger.info(f"[Collector] {group_key}: {len(tickers)}개 종목 수집 시작 (stage={stage}, period={yf_period})")

    all_features: list = []
    all_labels: list = []
    total = len(tickers)
    BATCH_SIZE = 25
    BATCH_DELAY = 2.0

    loop = asyncio.get_event_loop()

    for i in range(0, total, BATCH_SIZE):
        batch = tickers[i: i + BATCH_SIZE]

        def _batch_download(batch_tickers, period):
            import yfinance as yf
            import pandas as pd
            try:
                df = yf.download(
                    batch_tickers,
                    period=period,
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
                if df is None or df.empty:
                    return {}
                if isinstance(df.columns, pd.MultiIndex):
                    result = {}
                    for tkr in batch_tickers:
                        try:
                            tkr_df = df.xs(tkr, axis=1, level=1).dropna(how="all")
                            if tkr_df.empty:
                                continue
                            candles = []
                            for ts, row in tkr_df.iterrows():
                                close = row.get("Close")
                                if pd.isna(close):
                                    continue
                                candles.append({
                                    "date": ts.strftime("%Y-%m-%d"),
                                    "open":   float(row.get("Open",  close)),
                                    "high":   float(row.get("High",  close)),
                                    "low":    float(row.get("Low",   close)),
                                    "close":  float(close),
                                    "volume": float(row.get("Volume", 0) or 0),
                                })
                            result[tkr] = candles
                        except Exception:
                            continue
                    return result
                else:
                    tkr = batch_tickers[0]
                    candles = []
                    for ts, row in df.iterrows():
                        close = row.get("Close")
                        if pd.isna(close):
                            continue
                        candles.append({
                            "date": ts.strftime("%Y-%m-%d"),
                            "open":   float(row.get("Open",  close)),
                            "high":   float(row.get("High",  close)),
                            "low":    float(row.get("Low",   close)),
                            "close":  float(close),
                            "volume": float(row.get("Volume", 0) or 0),
                        })
                    return {tkr: candles}
            except Exception as e:
                logger.warning(f"[Collector] 배치 다운로드 실패 {batch_tickers[:3]}...: {e}")
                return {}

        ticker_candles = await loop.run_in_executor(None, _batch_download, batch, yf_period)

        for candles in ticker_candles.values():
            # 종목별 달성 가능한 stage로 학습 (신생 종목 자동 조정)
            ticker_stage = min(stage, get_max_achievable_stage(len(candles)))
            feats, labs = process_stock_data_for_ml(candles, ticker_stage)
            # 피처 개수가 요청된 stage와 일치하는 데이터만 수집 (모든 샘플이 같은 feature count 필요)
            if feats and len(feats[0]) == stage + 1:
                all_features.extend(feats)
                all_labels.extend(labs)

        progress = min(round(((i + BATCH_SIZE) / total) * 100), 99)
        await progress_callback(progress)

        await asyncio.sleep(BATCH_DELAY)

    logger.info(f"[Collector] 수집 완료: 총 {len(all_features)}개 샘플 (stage={stage})")
    return all_features, all_labels, stage
