"""
소문/커뮤니티 데이터 수집 서비스

Reddit, StockTwits 등에서 종목 관련 소문을 수집합니다.
"""
import logging

logger = logging.getLogger("rumors_service")


async def _collect_reddit(ticker: str) -> list[dict]:
    """Reddit에서 종목 관련 글 수집"""
    try:
        import praw
        from datetime import datetime, timedelta

        # Reddit API 초기화 (환경변수에서 자격증명 읽기)
        reddit = praw.Reddit(
            client_id="placeholder",
            client_secret="placeholder",
            user_agent="bitcoin-ai-backend/1.0"
        )

        # r/stocks, r/investing, r/wallstreetbets에서 검색
        subreddits = ["stocks", "investing"]
        results = []

        for subreddit_name in subreddits:
            try:
                subreddit = reddit.subreddit(subreddit_name)
                # 최근 일주일 글 검색
                for post in subreddit.search(ticker, time_filter="week", limit=10):
                    results.append({
                        "source": "reddit",
                        "subreddit": subreddit_name,
                        "title": post.title,
                        "score": post.score,
                        "created_at": datetime.fromtimestamp(post.created_utc).isoformat(),
                        "url": post.url
                    })
            except Exception as e:
                logger.warning(f"[Rumors:Reddit] {subreddit_name} 수집 실패: {e}")
                continue

        logger.info(f"[Rumors:Reddit] {ticker}: {len(results)}개 글 수집")
        return results
    except ImportError:
        logger.debug("[Rumors:Reddit] PRAW 라이브러리 없음, 스킵")
        return []
    except Exception as e:
        logger.warning(f"[Rumors:Reddit] 수집 실패: {e}")
        return []


async def _collect_stocktwits(ticker: str) -> list[dict]:
    """StockTwits에서 종목 관련 메시지 수집"""
    try:
        import httpx

        # StockTwits API (공개 API, 인증 불필요)
        async with httpx.AsyncClient() as client:
            # 최근 30개 메시지 수집
            url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
            response = await client.get(url, timeout=10.0)

            if response.status_code != 200:
                logger.warning(f"[Rumors:StockTwits] API 응답 실패: {response.status_code}")
                return []

            data = response.json()
            results = []

            for msg in data.get("messages", [])[:30]:
                results.append({
                    "source": "stocktwits",
                    "author": msg.get("user", {}).get("username", "unknown"),
                    "text": msg.get("body", ""),
                    "created_at": msg.get("created_at", ""),
                    "likes": msg.get("likes", {}).get("total", 0),
                    "sentiment": msg.get("entities", {}).get("sentiment", None)
                })

            logger.info(f"[Rumors:StockTwits] {ticker}: {len(results)}개 메시지 수집")
            return results
    except Exception as e:
        logger.warning(f"[Rumors:StockTwits] 수집 실패: {e}")
        return []


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

    # 병렬 수집
    reddit_data = await _collect_reddit(ticker)
    stocktwits_data = await _collect_stocktwits(ticker)

    # Twitter는 API 제약이 있어서 일단 공백 (유료 API 필요)
    twitter_data = []

    result = {
        "reddit": reddit_data,
        "stocktwits": stocktwits_data,
        "twitter": twitter_data,
    }

    total = sum(len(v) for v in result.values())
    logger.info(f"[Rumors] {ticker} 소문 수집 완료: {total}개")
    return result
