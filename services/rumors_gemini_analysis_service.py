"""
소문/커뮤니티 데이터 Gemini 분석 서비스

Reddit, StockTwits 등에서 수집한 소문을 Gemini에게 분석하여
각 종목별 영향도와 상세한 이유를 생성합니다.
"""
import json
import logging
import httpx
import os
from typing import Optional

logger = logging.getLogger("rumors_gemini_analysis")

VERCEL_PROXY_URL = os.environ.get("VERCEL_PROXY_URL", "").strip()

if VERCEL_PROXY_URL:
    GEMINI_API_URL = VERCEL_PROXY_URL
else:
    GEMINI_API_URL = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        "/gemini-1.5-flash:generateContent?key={api_key}"
    )


def build_rumors_context(rumors_data: dict, ticker: str) -> str:
    """
    수집한 소문 데이터를 분석용 텍스트 컨텍스트로 변환.

    Args:
        rumors_data: {
            "reddit": {"data": [...], "count": N},
            "stocktwits": {"data": [...], "count": N},
            ...
        }
        ticker: 분석 대상 종목

    Returns:
        Gemini 프롬프트용 포맷된 텍스트
    """
    context_parts = []

    # Reddit
    reddit_data = rumors_data.get("reddit", {}).get("data", [])
    if reddit_data:
        context_parts.append(f"## Reddit (r/stocks, r/investing) - {len(reddit_data)}개")
        for item in reddit_data[:10]:  # 상위 10개만
            title = item.get("title", "")
            score = item.get("score", 0)
            comments = item.get("comments", 0)
            context_parts.append(f"- [{title}] (점수: {score}, 댓글: {comments})")

    # StockTwits
    stocktwits_data = rumors_data.get("stocktwits", {}).get("data", [])
    if stocktwits_data:
        context_parts.append(f"\n## StockTwits - {len(stocktwits_data)}개")
        for item in stocktwits_data[:10]:  # 상위 10개만
            text = item.get("text", "")
            sentiment = item.get("sentiment", "")
            context_parts.append(f"- {text[:100]} [{sentiment}]" if sentiment else f"- {text[:100]}")

    # Twitter (현재는 미구현, 향후 추가 가능)
    twitter_data = rumors_data.get("twitter", {}).get("data", [])
    if twitter_data:
        context_parts.append(f"\n## Twitter/X - {len(twitter_data)}개")
        for item in twitter_data[:10]:  # 상위 10개만
            context_parts.append(f"- {item.get('text', '')[:100]}")

    if not context_parts:
        return ""

    return "\n".join(context_parts)


def build_rumors_prompt(context: str, ticker: str, company_name: str) -> str:
    """
    소문 분석용 Gemini 프롬프트 생성.

    Args:
        context: 소문 컨텍스트 (Reddit, StockTwits 등)
        ticker: 종목코드 (예: AAPL)
        company_name: 회사명 (예: Apple Inc.)

    Returns:
        Gemini에 전송할 프롬프트
    """
    return f"""You are a market analyst specializing in community sentiment analysis. Analyze the rumors and discussions about {ticker} ({company_name}) from multiple community sources (Reddit, StockTwits) during the last 24 hours.

[COMMUNITY DISCUSSIONS - Last 24 Hours]
{context}

Based on the above community discussions, provide:
- signal: "BUY" (bullish sentiment), "SELL" (bearish sentiment), or "HOLD" (neutral/mixed)
- confidence: 0.0 to 1.0 (how confident you are based on community consensus)
- reason: Brief 1-sentence explanation in Korean

Respond with ONLY valid JSON (no markdown code blocks):
{{
  "ticker": "{ticker}",
  "signal": "BUY",
  "confidence": 0.65,
  "reason": "커뮤니티에서 AI 관련 호재에 강한 긍정적 반응"
}}

IMPORTANT:
- JSON only, no other text
- Reason must be in Korean and one sentence only
- Base analysis on actual community discussions provided, not general knowledge"""


async def analyze_rumors_with_gemini(
    rumors_data: dict,
    ticker: str,
    company_name: str,
    api_key: str,
) -> Optional[dict]:
    """
    Gemini를 사용하여 소문 데이터 분석.

    Args:
        rumors_data: 수집한 소문 데이터
        ticker: 종목코드
        company_name: 회사명
        api_key: Gemini API 키

    Returns:
        {
            "ticker": "AAPL",
            "signal": "BUY",
            "confidence": 0.65,
            "reason": "상세한 설명..."
        }
        또는 None (분석 실패)
    """
    try:
        # 컨텍스트 생성
        context = build_rumors_context(rumors_data, ticker)
        if not context:
            logger.warning(f"[Gemini] {ticker}: 소문 데이터 없음")
            return None

        # 프롬프트 생성
        prompt = build_rumors_prompt(context, ticker, company_name)

        # Gemini API 호출
        url = GEMINI_API_URL.format(api_key=api_key) if "{api_key}" in GEMINI_API_URL else GEMINI_API_URL
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ]
        }

        logger.info(f"[Gemini] {ticker} 소문 분석 중...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code != 200:
            logger.warning(f"[Gemini] HTTP {resp.status_code} ({ticker}): {resp.text[:200]}")
            return None

        # 응답 파싱
        try:
            response_json = resp.json()
            content = response_json.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")

            # JSON 응답 추출
            content_clean = content.strip()
            if content_clean.startswith("```json"):
                content_clean = content_clean[7:-3]
            elif content_clean.startswith("```"):
                content_clean = content_clean[3:-3]

            result = json.loads(content_clean)
            logger.info(f"[Gemini] {ticker} 분석 완료: {result.get('signal')} ({result.get('confidence')})")
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"[Gemini] JSON 파싱 실패 ({ticker}): {e}")
            logger.debug(f"[Gemini] 응답: {content}")
            return None

    except Exception as e:
        logger.error(f"[Gemini] {ticker} 분석 실패: {e}")
        return None
