"""
소문/커뮤니티 감정 분석 서비스

수집된 소문 데이터의 감정(긍정/부정/중립)을 분석합니다.
TextBlob 또는 VADER를 사용한 감정 분류
"""
import logging

logger = logging.getLogger("rumors_analysis_service")


def _analyze_text_sentiment(text: str) -> tuple[str, float]:
    """
    텍스트의 감정 분석.

    Returns:
        (sentiment, confidence) - sentiment: "positive" | "negative" | "neutral"
    """
    try:
        from textblob import TextBlob
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity  # -1.0 ~ 1.0
        subjectivity = blob.sentiment.subjectivity  # 0.0 ~ 1.0

        if polarity > 0.1:
            sentiment = "positive"
            confidence = abs(polarity) * (1 - subjectivity * 0.5)
        elif polarity < -0.1:
            sentiment = "negative"
            confidence = abs(polarity) * (1 - subjectivity * 0.5)
        else:
            sentiment = "neutral"
            confidence = 1 - abs(polarity)

        return sentiment, min(1.0, confidence)
    except ImportError:
        # TextBlob 없으면 간단한 keyword 기반 분석
        text_lower = text.lower()
        positive_words = ["good", "great", "best", "buy", "profit", "moon", "bullish", "strong", "up", "rise"]
        negative_words = ["bad", "worst", "sell", "loss", "bearish", "weak", "down", "crash", "drop"]

        pos_count = sum(1 for w in positive_words if w in text_lower)
        neg_count = sum(1 for w in negative_words if w in text_lower)

        if pos_count > neg_count:
            return "positive", min(1.0, pos_count / 5)
        elif neg_count > pos_count:
            return "negative", min(1.0, neg_count / 5)
        else:
            return "neutral", 0.5
    except Exception as e:
        logger.warning(f"[Sentiment] 감정 분석 실패: {e}")
        return "neutral", 0.5


async def analyze_sentiment(rumors_data: dict) -> dict:
    """
    소문 데이터의 전체 감정을 분석합니다.

    Args:
        rumors_data: {
            "reddit": {"data": [...], "error": "..."},
            "stocktwits": {"data": [...], "error": "..."},
            "twitter": {"data": [...], "error": "..."}
        }

    Returns:
        {
            "sentiment": "positive" | "negative" | "neutral",
            "confidence": 0.0~1.0,
            "reddit_sentiment": "...",
            "reddit_error": "...",
            "stocktwits_sentiment": "...",
            "stocktwits_error": "...",
            "twitter_sentiment": "...",
            "twitter_error": "..."
        }
    """
    logger.info("[Sentiment] 감정 분석 시작...")

    # 각 플랫폼별 감정 분석
    def _analyze_platform(platform_info: dict) -> tuple[str, float, str | None]:
        """Returns: (sentiment, confidence, error)"""
        if isinstance(platform_info, dict):
            platform_data = platform_info.get("data", [])
            platform_error = platform_info.get("error")
        else:
            platform_data = platform_info if isinstance(platform_info, list) else []
            platform_error = None

        if not platform_data or platform_error:
            return "neutral", 0.5, platform_error

        sentiments = []
        confidences = []

        for item in platform_data:
            # 각 플랫폼의 텍스트 필드 추출
            if isinstance(item, dict):
                if "body" in item:  # Reddit
                    text = item["body"]
                elif "text" in item:  # StockTwits
                    text = item["text"]
                elif "title" in item:  # Reddit post
                    text = item["title"]
                else:
                    continue

                sentiment, confidence = _analyze_text_sentiment(text)
                sentiments.append(sentiment)
                confidences.append(confidence)

        if not sentiments:
            return "neutral", 0.5, platform_error

        # 다수결로 최종 감정 결정
        pos_count = sentiments.count("positive")
        neg_count = sentiments.count("negative")
        avg_confidence = sum(confidences) / len(confidences)

        sentiment = "positive" if pos_count > neg_count else "negative" if neg_count > pos_count else "neutral"
        return sentiment, avg_confidence, None

    reddit_sentiment, reddit_conf, reddit_error = _analyze_platform(rumors_data.get("reddit", {}))
    stocktwits_sentiment, stocktwits_conf, stocktwits_error = _analyze_platform(rumors_data.get("stocktwits", {}))
    twitter_sentiment, twitter_conf, twitter_error = _analyze_platform(rumors_data.get("twitter", {}))

    # 전체 감정 (데이터가 있는 플랫폼만 고려)
    all_sentiments = []
    all_confidences = []

    for sent, conf, err in [(reddit_sentiment, reddit_conf, reddit_error),
                             (stocktwits_sentiment, stocktwits_conf, stocktwits_error),
                             (twitter_sentiment, twitter_conf, twitter_error)]:
        # 에러가 없는 플랫폼의 데이터만 사용
        if err is None and sent != "neutral":
            all_sentiments.append(sent)
            all_confidences.append(conf)

    if all_sentiments:
        pos_count = all_sentiments.count("positive")
        neg_count = all_sentiments.count("negative")
        overall_sentiment = "positive" if pos_count > neg_count else "negative" if neg_count > pos_count else "neutral"
        overall_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.5
    else:
        overall_sentiment = "neutral"
        overall_confidence = 0.5

    # Signal 및 Reason 생성
    signal = "BUY" if overall_sentiment == "positive" else "SELL" if overall_sentiment == "negative" else "HOLD"
    reason = _build_reason(
        overall_sentiment,
        overall_confidence,
        reddit_sentiment,
        stocktwits_sentiment,
        twitter_sentiment,
    )

    result = {
        "sentiment": overall_sentiment,
        "signal": signal,
        "confidence": round(overall_confidence, 3),
        "reason": reason,
        "reddit_sentiment": reddit_sentiment,
        "reddit_error": reddit_error,
        "stocktwits_sentiment": stocktwits_sentiment,
        "stocktwits_error": stocktwits_error,
        "twitter_sentiment": twitter_sentiment,
        "twitter_error": twitter_error,
    }

    logger.info(f"[Sentiment] 감정 분석 완료: {overall_sentiment} (신뢰도 {overall_confidence:.2f}) → Signal: {signal}")
    return result


def _build_reason(
    overall_sentiment: str,
    confidence: float,
    reddit_sentiment: str,
    stocktwits_sentiment: str,
    twitter_sentiment: str,
) -> str:
    """
    소문 분석 결과를 기반으로 한글 이유 문구 생성.

    Args:
        overall_sentiment: positive | negative | neutral
        confidence: 신뢰도 (0.0~1.0)
        reddit_sentiment: Reddit 감정
        stocktwits_sentiment: StockTwits 감정
        twitter_sentiment: Twitter 감정

    Returns:
        한글 설명 문구
    """
    conf_pct = int(confidence * 100)

    # 플랫폼별 합의 수 계산
    sentiments = [reddit_sentiment, stocktwits_sentiment, twitter_sentiment]
    positive_count = sentiments.count("positive")
    negative_count = sentiments.count("negative")

    platforms = []
    if reddit_sentiment == overall_sentiment:
        platforms.append("Reddit")
    if stocktwits_sentiment == overall_sentiment:
        platforms.append("StockTwits")
    if twitter_sentiment == overall_sentiment:
        platforms.append("Twitter")

    platform_str = ", ".join(platforms) if platforms else "커뮤니티"

    if overall_sentiment == "positive":
        if conf_pct >= 70:
            return f"{platform_str}에서 강한 긍정적 감정 ({conf_pct}%). 주요 키워드: 상승, 매수, 호재"
        else:
            return f"{platform_str}에서 약한 긍정적 반응 ({conf_pct}%). 커뮤니티 관심 증가 중"

    elif overall_sentiment == "negative":
        if conf_pct >= 70:
            return f"{platform_str}에서 강한 부정적 감정 ({conf_pct}%). 주요 키워드: 하락, 매도, 악재"
        else:
            return f"{platform_str}에서 약한 부정적 반응 ({conf_pct}%). 약세 심화 우려"

    else:  # neutral
        if positive_count > negative_count:
            return f"{platform_str}에서 엇갈린 의견 (긍정 우세, {conf_pct}% 신뢰). 혼조 추세"
        elif negative_count > positive_count:
            return f"{platform_str}에서 엇갈린 의견 (부정 우세, {conf_pct}% 신뢰). 신중한 관망 필요"
        else:
            return f"{platform_str}에서 뚜렷한 의견 없음 ({conf_pct}% 신뢰). 추가 정보 대기 중"
