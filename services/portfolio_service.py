"""
포트폴리오 데이터 생성 및 캐싱 서비스
potatoinvest dataroma_portfolio 패턴을 Python으로 구현
"""
import logging
from datetime import datetime
import yfinance as yf

logger = logging.getLogger("portfolio_service")

# 샘플 유명 투자자 데이터
SAMPLE_INVESTORS = [
    {"no": 1, "name": "Warren Buffett", "totalValue": "$1,234,567,890", "totalValueNum": 1234567890},
    {"no": 2, "name": "George Soros", "totalValue": "$987,654,321", "totalValueNum": 987654321},
    {"no": 3, "name": "Carl Icahn", "totalValue": "$876,543,210", "totalValueNum": 876543210},
    {"no": 4, "name": "Bill Ackman", "totalValue": "$765,432,100", "totalValueNum": 765432100},
    {"no": 5, "name": "Daniel Loeb", "totalValue": "$654,321,000", "totalValueNum": 654321000},
]

# 투자자별 샘플 포트폴리오
SAMPLE_PORTFOLIOS = {
    "Warren Buffett": [
        {"code": "AAPL", "ratio": "47.5"},
        {"code": "BAM", "ratio": "15.2"},
        {"code": "KO", "ratio": "10.8"},
        {"code": "AXP", "ratio": "9.3"},
        {"code": "JNJ", "ratio": "8.5"},
        {"code": "MA", "ratio": "5.2"},
        {"code": "V", "ratio": "3.5"},
    ],
    "George Soros": [
        {"code": "SPY", "ratio": "25.0"},
        {"code": "TLT", "ratio": "20.0"},
        {"code": "GLD", "ratio": "15.0"},
        {"code": "MSFT", "ratio": "12.0"},
        {"code": "NVDA", "ratio": "10.0"},
        {"code": "TSM", "ratio": "10.0"},
        {"code": "TSLA", "ratio": "8.0"},
    ],
    "Carl Icahn": [
        {"code": "TSLA", "ratio": "30.0"},
        {"code": "UVV", "ratio": "20.0"},
        {"code": "PHM", "ratio": "18.0"},
        {"code": "IEP", "ratio": "15.0"},
        {"code": "MGM", "ratio": "10.0"},
        {"code": "APA", "ratio": "7.0"},
    ],
    "Bill Ackman": [
        {"code": "UMG", "ratio": "35.0"},
        {"code": "PSP", "ratio": "25.0"},
        {"code": "GOOGL", "ratio": "20.0"},
        {"code": "AMZN", "ratio": "12.0"},
        {"code": "HLI", "ratio": "8.0"},
    ],
    "Daniel Loeb": [
        {"code": "NVDA", "ratio": "25.0"},
        {"code": "META", "ratio": "20.0"},
        {"code": "GOOG", "ratio": "18.0"},
        {"code": "ASML", "ratio": "15.0"},
        {"code": "AMD", "ratio": "12.0"},
        {"code": "PYPL", "ratio": "10.0"},
    ],
}


def build_stock_aggregation(investors_with_portfolio):
    """
    투자자별 포트폴리오 데이터를 집계하여 종목별 데이터로 변환
    potatoinvest의 buildStockAggregation과 동일 로직
    현재가와 거래소 정보 추가
    """
    stock_map = {}

    for investor in investors_with_portfolio:
        portfolio = investor.get("portfolio", [])
        for holding in portfolio:
            code = holding.get("code", "").upper()
            ratio_str = holding.get("ratio", "0")
            try:
                ratio = float(ratio_str)
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
                "ratio": ratio_str,
            })
            stock_map[code]["person_count"] += 1
            stock_map[code]["sum_ratio"] += ratio

    # 평균 비율 계산
    for stock_code, stock_data in stock_map.items():
        if stock_data["person_count"] > 0:
            stock_data["avg_ratio"] = stock_data["sum_ratio"] / stock_data["person_count"]

    # 현재가와 거래소 정보 추수집 (일괄 처리로 성능 최적화)
    try:
        logger.info("[Portfolio] Fetching stock prices from yfinance...")
        ticker_list = list(stock_map.keys())
        if ticker_list:
            # yfinance에서 모든 종목 정보를 한번에 가져오기
            tickers = yf.Tickers(" ".join(ticker_list))

            for code in stock_map:
                try:
                    ticker_obj = tickers.tickers.get(code)
                    if ticker_obj:
                        # 현재가 추출
                        history = ticker_obj.history(period="1d")
                        if not history.empty:
                            close_price = history['Close'].iloc[-1]
                            stock_map[code]["close"] = float(close_price)

                        # 거래소 정보 추출
                        info = ticker_obj.info
                        if info:
                            exchange = info.get("exchange", "UNKNOWN")
                            stock_map[code]["exchange"] = exchange
                            logger.debug(f"[Portfolio] {code}: ${stock_map[code]['close']:.2f} ({exchange})")
                except Exception as e:
                    logger.warning(f"[Portfolio] Failed to fetch {code} data: {e}")
    except Exception as e:
        logger.warning(f"[Portfolio] Failed to fetch stock prices: {e}")

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
    투자자별(based_on_person) 및 종목별(based_on_stock) 데이터 반환
    """
    try:
        # 1. 투자자별 포트폴리오 구성
        investors_data = []
        for investor in SAMPLE_INVESTORS:
            portfolio = SAMPLE_PORTFOLIOS.get(investor["name"], [])
            investor_with_portfolio = {
                **investor,
                "portfolio": portfolio,
            }
            investors_data.append(investor_with_portfolio)

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
            },
        }

        logger.info(f"[Portfolio] 생성 완료: 투자자 {len(investors_data)}명, 종목 {len(stocks)}개")
        return result

    except Exception as e:
        logger.error(f"[Portfolio] 생성 실패: {e}")
        return {
            "based_on_person": [],
            "based_on_stock": [],
            "meta": {"error": str(e)},
        }
