"""
포트폴리오 데이터 생성 및 캐싱 서비스
potatoinvest dataroma API에서 원본 데이터를 직접 조회
"""
import logging
from datetime import datetime
import httpx
import yfinance as yf

logger = logging.getLogger("portfolio_service")

POTATOINVEST_API = "https://potatoinvest.com/api/dataroma/base"


async def fetch_dataroma_portfolio():
    """
    dataroma.com에서 직접 투자자 데이터 크롤링
    """
    try:
        import re
        from bs4 import BeautifulSoup

        logger.info("[Portfolio] Fetching from dataroma.com direct crawl...")

        # 알려진 투자자 목록
        investor_map = {
            'BRK': {'no': 1, 'name': 'Warren Buffett'},
            'SOROS': {'no': 2, 'name': 'George Soros'},
            'ICAHN': {'no': 3, 'name': 'Carl Icahn'},
            'ACKMAN': {'no': 4, 'name': 'Bill Ackman'},
            'LOEB': {'no': 5, 'name': 'Daniel Loeb'},
            'DALIO': {'no': 6, 'name': 'Ray Dalio'},
            'LYNCH': {'no': 7, 'name': 'Peter Lynch'},
            'DRUCKENMILLER': {'no': 8, 'name': 'Stan Druckenmiller'},
            'EINHORN': {'no': 9, 'name': 'David Einhorn'},
            'SLOAN': {'no': 10, 'name': 'Allan Sloan'},
        }

        # 각 investor에서 포트폴리오 데이터 추출
        investors_data = []
        tickers = list(investor_map.keys())
        logger.info(f"[Portfolio] Fetching portfolio data for {len(tickers)} known investors from dataroma")

        for idx, ticker in enumerate(tickers):
            try:
                async with httpx.AsyncClient(timeout=20, verify=False) as client:
                    resp = await client.get(
                        f"https://www.dataroma.com/m/holdings.php?m={ticker}",
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                    )

                if resp.status_code != 200:
                    continue

                # HTML에서 포트폴리오 추출 (table 형식)
                soup = BeautifulSoup(resp.text, 'html.parser')

                # investor 이름 찾기
                investor_name = investor_map.get(ticker, {}).get('name', ticker)

                # 포트폴리오 데이터 추출 (테이블에서)
                portfolio = []
                table = soup.find('table')  # 클래스가 없을 수 있으므로 첫 테이블 사용
                if table:
                    rows = table.find_all('tr')[1:]  # 헤더 제외
                    for row in rows[:100]:  # 최대 100개 종목
                        tds = row.find_all('td')
                        if len(tds) >= 3:
                            # 첫 번째 td는 아이콘, 두 번째가 티커-이름, 세 번째가 비율
                            ticker_name = tds[1].get_text(strip=True) if len(tds) > 1 else ''
                            ratio_text = tds[2].get_text(strip=True) if len(tds) > 2 else ''

                            # ticker_name은 "AAPL - Apple Inc." 형식일 수 있음
                            ticker_text = ticker_name.split('-')[0].strip() if ticker_name else ''

                            if ticker_text and ratio_text:
                                try:
                                    ratio_val = float(ratio_text.replace('%', '').strip())
                                    portfolio.append({
                                        'code': ticker_text,
                                        'ratio': str(ratio_val)
                                    })
                                except:
                                    pass

                if portfolio:
                    investor = {
                        'no': investor_map.get(ticker, {}).get('no', idx + 1),
                        'name': investor_name,
                        'totalValue': '$N/A',
                        'totalValueNum': 0,
                        'portfolio': portfolio
                    }
                    investors_data.append(investor)
                    logger.info(f"[Portfolio] {investor_name}: {len(portfolio)} holdings")

            except Exception as e:
                logger.debug(f"[Portfolio] Error fetching {ticker}: {e}")
                continue

        if investors_data:
            logger.info(f"[Portfolio] Successfully crawled {len(investors_data)} investors from dataroma")
            return {
                'based_on_person': investors_data,
                'based_on_stock': []  # build_stock_aggregation에서 계산됨
            }

        return None

    except Exception as e:
        logger.warning(f"[Portfolio] Direct dataroma crawl failed: {e}")
        return None


def build_stock_aggregation(investors_with_portfolio):
    """
    투자자별 포트폴리오 데이터를 집계하여 종목별 데이터로 변환
    """
    stock_map = {}

    for investor in investors_with_portfolio:
        portfolio = investor.get("portfolio", [])
        for holding in portfolio:
            code = holding.get("code", "").upper()
            ratio_str = holding.get("ratio", "0")
            try:
                # ratio가 문자열 "12.5" 또는 "12.5%" 형태일 수 있음
                ratio_val = str(ratio_str).replace("%", "")
                ratio = float(ratio_val) if ratio_val else 0.0
            except (ValueError, TypeError):
                ratio = 0.0

            if code not in stock_map:
                stock_map[code] = {
                    "stock": code,
                    "person": [],
                    "person_count": 0,
                    "sum_ratio": 0.0,
                    "avg_ratio": None,
                    "close": None,
                    "exchange": None,
                }

            # 투자자 정보 추가
            stock_map[code]["person"].append({
                "no": investor.get("no", 0),
                "name": investor.get("name", "Unknown"),
                "ratio": str(ratio),
            })
            stock_map[code]["person_count"] += 1
            stock_map[code]["sum_ratio"] += ratio

    # 평균 비율 계산
    for stock_code, stock_data in stock_map.items():
        if stock_data["person_count"] > 0:
            stock_data["avg_ratio"] = stock_data["sum_ratio"] / stock_data["person_count"]

    # 현재가와 거래소 정보 수집 (각 종목별 개별 호출로 안정성 강화)
    logger.info(f"[Portfolio] Fetching stock prices for {len(stock_map)} tickers from yfinance...")
    for code in stock_map:
        try:
            ticker = yf.Ticker(code)

            # 현재가 추출
            try:
                history = ticker.history(period="1d")
                if not history.empty and 'Close' in history.columns:
                    close_price = history['Close'].iloc[-1]
                    if close_price and close_price > 0:
                        stock_map[code]["close"] = float(close_price)
            except Exception as e:
                logger.debug(f"[Portfolio] Could not fetch price for {code}: {e}")

            # 거래소 정보 추출
            try:
                info = ticker.info
                if info and isinstance(info, dict):
                    exchange = info.get("exchange", "UNKNOWN")
                    if exchange:
                        stock_map[code]["exchange"] = exchange
                        close_val = stock_map[code].get("close")
                        if close_val and isinstance(close_val, (int, float)):
                            logger.debug(f"[Portfolio] {code}: ${close_val:.2f} ({exchange})")
            except Exception as e:
                logger.debug(f"[Portfolio] Could not fetch exchange for {code}: {e}")

        except Exception as e:
            logger.warning(f"[Portfolio] Error fetching data for {code}: {e}")

    # 인원 수 기준으로 정렬
    stocks = sorted(
        stock_map.values(),
        key=lambda x: (x["person_count"], x["sum_ratio"]),
        reverse=True,
    )

    return stocks


async def generate_portfolio_base():
    """
    포트폴리오 기본 데이터 생성
    1차: potatoinvest dataroma API에서 원본 데이터 조회
    2차: 조회 실패 시 샘플 데이터 사용
    """
    try:
        # 1. potatoinvest에서 원본 데이터 조회
        dataroma_data = await fetch_dataroma_portfolio()

        if dataroma_data and dataroma_data.get("based_on_person"):
            investors_data = dataroma_data.get("based_on_person", [])

            # 2. 종목별 집계
            stocks = build_stock_aggregation(investors_data)

            # 3. 메타데이터
            result = {
                "based_on_person": investors_data,
                "based_on_stock": stocks,
                "meta": {
                    "investors_count": len(investors_data),
                    "stocks_count": len(stocks),
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "source": "dataroma",
                },
            }

            logger.info(f"[Portfolio] 생성 완료: 투자자 {len(investors_data)}명, 종목 {len(stocks)}개 (dataroma)")
            return result

        else:
            logger.warning("[Portfolio] dataroma API failed, using fallback sample data")
            # 폴백: 샘플 데이터 사용
            SAMPLE_INVESTORS = [
                {"no": 1, "name": "Warren Buffett", "totalValue": "$1B+", "totalValueNum": 1000000000},
                {"no": 2, "name": "George Soros", "totalValue": "$1B+", "totalValueNum": 1000000000},
                {"no": 3, "name": "Carl Icahn", "totalValue": "$1B+", "totalValueNum": 1000000000},
            ]

            SAMPLE_PORTFOLIOS = {
                "Warren Buffett": [
                    {"code": "AAPL", "ratio": "25.0"},
                    {"code": "BAM", "ratio": "15.0"},
                    {"code": "KO", "ratio": "10.0"},
                ],
                "George Soros": [
                    {"code": "MSFT", "ratio": "20.0"},
                    {"code": "NVDA", "ratio": "15.0"},
                    {"code": "SPY", "ratio": "25.0"},
                ],
                "Carl Icahn": [
                    {"code": "TSLA", "ratio": "30.0"},
                    {"code": "UVV", "ratio": "20.0"},
                ],
            }

            investors_data = []
            for investor in SAMPLE_INVESTORS:
                portfolio = SAMPLE_PORTFOLIOS.get(investor["name"], [])
                investor_with_portfolio = {
                    **investor,
                    "portfolio": portfolio,
                }
                investors_data.append(investor_with_portfolio)

            stocks = build_stock_aggregation(investors_data)

            result = {
                "based_on_person": investors_data,
                "based_on_stock": stocks,
                "meta": {
                    "investors_count": len(investors_data),
                    "stocks_count": len(stocks),
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "source": "sample",
                },
            }

            logger.info(f"[Portfolio] 생성 완료: 투자자 {len(investors_data)}명, 종목 {len(stocks)}개 (sample fallback)")
            return result

    except Exception as e:
        logger.error(f"[Portfolio] 생성 실패: {e}")
        return {
            "based_on_person": [],
            "based_on_stock": [],
            "meta": {"error": str(e), "source": "error"},
        }
