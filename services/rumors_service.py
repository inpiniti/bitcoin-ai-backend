"""
소문/커뮤니티 데이터 수집 서비스

Reddit, StockTwits, Twitter 등에서 종목 관련 소문을 수집합니다.
현재는 최소한의 구현으로 빈 데이터 반환 (향후 API 통합 예정)
"""
import logging

logger = logging.getLogger("rumors_service")


async def collect_rumors(ticker: str) -> dict:
    """
    종목에 대한 소문/커뮤니티 데이터 수집.

    Args:
        ticker: 종목 코드 (예: "AAPL")

    Returns:
        {
            "reddit": [...],
            "stocktwits": [...],
            "twitter": [...]
        }
    """
    logger.info(f"[Rumors] {ticker} 소문 수집 시작...")

    # TODO: Reddit PRAW API, StockTwits API, Twitter API 통합
    # 현재는 빈 데이터 반환 (파이프라인이 진행되도록)

    result = {
        "reddit": [],
        "stocktwits": [],
        "twitter": [],
    }

    logger.info(f"[Rumors] {ticker} 소문 수집 완료: {sum(len(v) for v in result.values())}개")
    return result
