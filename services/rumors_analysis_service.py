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
            "reddit": [...],
            "stocktwits": [...],
            "twitter": [...]
        }

    Returns:
        {
            "sentiment": "positive" | "negative" | "neutral",
            "confidence": 0.0~1.0,
            "reddit_sentiment": "...",
            "stocktwits_sentiment": "...",
            "twitter_sentiment": "..."
        }
    """
    logger.info("[Sentiment] 감정 분석 시작...")

    # 각 플랫폼별 감정 분석
    def _analyze_platform(platform_data: list) -> tuple[str, float]:
        if not platform_data:
            return "neutral", 0.5

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
            return "neutral", 0.5

        # 다수결로 최종 감정 결정
        pos_count = sentiments.count("positive")
        neg_count = sentiments.count("negative")
        avg_confidence = sum(confidences) / len(confidences)

        if pos_count > neg_count:
            return "positive", avg_confidence
        elif neg_count > pos_count:
            return "negative", avg_confidence
        else:
            return "neutral", avg_confidence

    reddit_sentiment, reddit_conf = _analyze_platform(rumors_data.get("reddit", []))
    stocktwits_sentiment, stocktwits_conf = _analyze_platform(rumors_data.get("stocktwits", []))
    twitter_sentiment, twitter_conf = _analyze_platform(rumors_data.get("twitter", []))

    # 전체 감정 (가중평균)
    all_sentiments = []
    all_confidences = []

    for sent, conf in [(reddit_sentiment, reddit_conf), (stocktwits_sentiment, stocktwits_conf), (twitter_sentiment, twitter_conf)]:
        if sent != "neutral":
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

    result = {
        "sentiment": overall_sentiment,
        "confidence": round(overall_confidence, 3),
        "reddit_sentiment": reddit_sentiment,
        "stocktwits_sentiment": stocktwits_sentiment,
        "twitter_sentiment": twitter_sentiment,
    }

    logger.info(f"[Sentiment] 감정 분석 완료: {overall_sentiment} (신뢰도 {overall_confidence:.2f})")
    return result
