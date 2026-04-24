"""
소문/커뮤니티 데이터 수집 서비스

Reddit, StockTwits 등에서 종목 관련 소문을 수집합니다.
"""
import logging

logger = logging.getLogger("rumors_service")


async def _collect_reddit(ticker: str) -> tuple[list[dict], str | None]:
    """
    Reddit에서 종목 관련 글 수집 (오늘 글만).
    Returns: (data, error_message)
    """
    try:
        import os
        import praw
        from datetime import datetime, timezone

        # 환경변수에서 Reddit API 자격증명 읽기
        client_id = os.getenv("REDDIT_CLIENT_ID")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET")

        if not client_id or not client_secret:
            error_msg = "환경변수 필요: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET"
            logger.info(f"[Rumors:Reddit] {error_msg}")
            return [], error_msg

        # Reddit API 초기화
        try:
            reddit = praw.Reddit(
                client_id=client_id,
                client_secret=client_secret,
                user_agent="bitcoin-ai-backend/1.0"
            )
        except Exception as e:
            error_msg = f"Reddit API 인증 실패: {str(e)}"
            logger.warning(f"[Rumors:Reddit] {error_msg}")
            return [], error_msg

        # 오늘 시작 시간
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        # r/stocks, r/investing에서 검색
        subreddits = ["stocks", "investing"]
        results = []

        for subreddit_name in subreddits:
            try:
                subreddit = reddit.subreddit(subreddit_name)
                # 오늘의 글 검색
                for post in subreddit.search(ticker, sort="new", time_filter="day", limit=15):
                    if post.created_utc >= today_start:
                        results.append({
                            "source": "reddit",
                            "subreddit": subreddit_name,
                            "title": post.title,
                            "score": post.score,
                            "created_at": datetime.fromtimestamp(post.created_utc, tz=timezone.utc).isoformat(),
                            "url": post.url,
                            "comments": post.num_comments
                        })
            except Exception as e:
                logger.warning(f"[Rumors:Reddit] r/{subreddit_name} 수집 실패: {e}")
                continue

        logger.info(f"[Rumors:Reddit] {ticker}: 오늘 글 {len(results)}개 수집")
        return results, None
    except ImportError:
        error_msg = "라이브러리 필요: pip install praw"
        logger.debug(f"[Rumors:Reddit] {error_msg}")
        return [], error_msg
    except Exception as e:
        error_msg = f"예상치 못한 오류: {str(e)}"
        logger.warning(f"[Rumors:Reddit] {error_msg}")
        return [], error_msg


async def _collect_stocktwits(ticker: str) -> tuple[list[dict], str | None]:
    """
    StockTwits에서 종목 관련 메시지 수집 (오늘 메시지만).
    Returns: (data, error_message)
    """
    try:
        import httpx
        from datetime import datetime, timezone, timedelta

        # 오늘 시작 시간 (UTC)
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        async with httpx.AsyncClient() as client:
            # StockTwits API: 최근 메시지부터 수집, 여러 페이지 요청
            results = []
            max_messages = 200  # 최대 수집 메시지 수
            cursor = None

            for page in range(3):  # 최대 3페이지 (각 페이지 최대 30개)
                try:
                    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
                    params = {}
                    if cursor:
                        params["since"] = cursor

                    response = await client.get(url, params=params, timeout=10.0)

                    if response.status_code != 200:
                        logger.warning(f"[Rumors:StockTwits] API 응답 실패: {response.status_code}")
                        break

                    data = response.json()
                    messages = data.get("messages", [])

                    if not messages:
                        break

                    for msg in messages:
                        # 타임스탐프 파싱
                        try:
                            created_str = msg.get("created_at", "")
                            # ISO format: "2026-04-24T07:14:00Z"
                            msg_time = datetime.fromisoformat(created_str.replace("Z", "+00:00"))

                            # 오늘 메시지만 수집
                            if msg_time >= today_start:
                                results.append({
                                    "source": "stocktwits",
                                    "author": msg.get("user", {}).get("username", "unknown"),
                                    "text": msg.get("body", ""),
                                    "created_at": created_str,
                                    "likes": msg.get("likes", {}).get("total", 0),
                                    "sentiment": msg.get("entities", {}).get("sentiment", None)
                                })
                            else:
                                # 오늘 이전 메시지 도달하면 중단
                                logger.info(f"[Rumors:StockTwits] 어제 메시지 도달, 수집 종료")
                                page = 999  # 외부 루프 탈출
                                break
                        except Exception as e:
                            logger.warning(f"[Rumors:StockTwits] 메시지 파싱 실패: {e}")
                            continue

                        if len(results) >= max_messages:
                            logger.info(f"[Rumors:StockTwits] 최대 수집 개수 도달")
                            page = 999
                            break

                    # 다음 페이지를 위한 cursor 설정
                    if messages:
                        last_msg_id = messages[-1].get("id")
                        if last_msg_id:
                            cursor = last_msg_id

                except Exception as e:
                    logger.warning(f"[Rumors:StockTwits] 페이지 {page} 수집 실패: {e}")
                    continue

            logger.info(f"[Rumors:StockTwits] {ticker}: 오늘 메시지 {len(results)}개 수집")
            return results, None
    except Exception as e:
        error_msg = f"StockTwits API 오류: {str(e)}"
        logger.warning(f"[Rumors:StockTwits] {error_msg}")
        return [], error_msg


async def collect_rumors(ticker: str) -> dict:
    """
    종목에 대한 소문/커뮤니티 데이터 수집 (오늘 메시지만).

    Args:
        ticker: 종목 코드 (예: "AAPL")

    Returns:
        {
            "reddit": {
                "data": [...],
                "error": "에러메시지 또는 null"
            },
            "stocktwits": {
                "data": [...],
                "error": "에러메시지 또는 null"
            },
            "twitter": {
                "data": [],
                "error": "에러메시지"
            }
        }

    설정 방법:
    - Reddit: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET 환경변수 설정 필요
    - StockTwits: 자격증명 불필요 (공개 API)
    - Twitter: 유료 API 필요 (현재 미구현)
    """
    logger.info(f"[Rumors] {ticker} 오늘 소문 수집 시작...")

    # 병렬 수집
    reddit_data, reddit_error = await _collect_reddit(ticker)
    stocktwits_data, stocktwits_error = await _collect_stocktwits(ticker)

    # Twitter는 유료 API (현재 미구현)
    twitter_error = "Twitter API v2는 유료 계정 필수 (Enterprise plan $100+/월)"

    result = {
        "reddit": {
            "data": reddit_data,
            "error": reddit_error,
            "count": len(reddit_data)
        },
        "stocktwits": {
            "data": stocktwits_data,
            "error": stocktwits_error,
            "count": len(stocktwits_data)
        },
        "twitter": {
            "data": [],
            "error": twitter_error,
            "count": 0
        },
    }

    total = sum(r["count"] for r in result.values())
    logger.info(f"[Rumors] {ticker} 오늘 소문 수집 완료: {total}개 (Reddit: {result['reddit']['error'] or '성공'}, StockTwits: {result['stocktwits']['error'] or '성공'}, Twitter: {twitter_error})")
    return result
