"""
#66 Gemini 뉴스 AI 분석 서비스

뉴스 제목/요약 → Gemini → 영향 종목/시장 분석 → Supabase 저장
주요 타겟: 미국 주식 (S&P500, 나스닥, 빅테크) + 국내 증시
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from services.gemini_key_manager import GeminiKeyManager, NoAvailableKeyError
from services import supabase_service

logger = logging.getLogger("news_analysis_service")

GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/gemini-1.5-flash:generateContent?key={api_key}"
)

RETRY_LIMIT = 3
RETRY_DELAY = 2.0


# ── 데이터클래스 ──────────────────────────────────────────────────────────────

@dataclass
class StockImpact:
    ticker: str
    name: str
    market: str       # US | KOSPI | KOSDAQ | CRYPTO
    direction: str    # bullish | bearish | neutral
    reason: str
    confidence: float

    def to_dict(self, news_id: str) -> dict:
        return {
            "news_id": news_id,
            "ticker": self.ticker,
            "name": self.name,
            "market": self.market,
            "direction": self.direction,
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class AnalysisResult:
    market_impact: str
    impact_level: str   # high | medium | low
    stocks: list[StockImpact] = field(default_factory=list)

    def to_news_update_dict(self) -> dict:
        return {
            "market_impact": self.market_impact,
            "impact_level": self.impact_level,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }


# ── 프롬프트 ─────────────────────────────────────────────────────────────────

def build_analysis_prompt(title: str, summary: Optional[str]) -> str:
    """
    뉴스 제목/요약 → Gemini 분석 프롬프트 생성.
    미국 주식(S&P500/나스닥/빅테크) 우선 분석 요청.
    """
    summary_text = summary or "요약 없음"
    return f"""다음 금융/증권 뉴스를 분석하여 주식 시장에 미치는 영향을 JSON으로 답해줘.

뉴스 제목: {title}
뉴스 요약: {summary_text}

분석 우선순위:
1. 미국 주식 시장 (S&P500, 나스닥, NASDAQ, 다우존스, 빅테크 - NVDA/AAPL/MSFT/GOOGL/AMZN/META/TSLA)
2. 한국 주식 시장 (KOSPI, KOSDAQ)
3. 암호화폐 (BTC, ETH)

반드시 아래 JSON 형식으로만 답해 (마크다운 코드블록 제외):
{{
  "market_impact": "시장 전체에 미치는 영향 설명 (2~3문장, 한국어)",
  "impact_level": "high 또는 medium 또는 low",
  "stocks": [
    {{
      "ticker": "종목코드 또는 ETF 심볼 (예: SPY, QQQ, NVDA, 005930)",
      "name": "종목명 (한국어)",
      "market": "US 또는 KOSPI 또는 KOSDAQ 또는 CRYPTO",
      "direction": "bullish 또는 bearish 또는 neutral",
      "reason": "영향 이유 (1~2문장, 한국어)",
      "confidence": 0.0~1.0
    }}
  ]
}}

영향받는 종목이 없으면 stocks는 빈 배열로 반환. JSON만 반환, 다른 텍스트 금지."""


# ── Gemini 호출 ───────────────────────────────────────────────────────────────

async def call_gemini(
    title: str,
    summary: Optional[str],
    api_key: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[AnalysisResult]:
    """Gemini API 호출 → AnalysisResult. 파싱 실패 시 None 반환."""
    prompt = build_analysis_prompt(title, summary)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024},
    }
    url = GEMINI_API_URL.format(api_key=api_key)

    async def _post(c: httpx.AsyncClient) -> httpx.Response:
        return await c.post(url, json=payload)

    if client is not None:
        resp = await _post(client)
    else:
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await _post(c)

    if resp.status_code == 429:
        raise httpx.HTTPStatusError("Rate Limited", request=resp.request, response=resp)
    if resp.status_code != 200:
        logger.warning(f"[Gemini] HTTP {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return parse_gemini_response(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.warning(f"[Gemini] 응답 파싱 실패: {e}")
        return None


def parse_gemini_response(text: str) -> Optional[AnalysisResult]:
    """
    Gemini 응답 텍스트 → AnalysisResult.
    마크다운 코드블록(```json ... ```) 자동 제거.
    파싱 실패 시 None 반환.
    """
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.debug(f"[Parse] JSON 파싱 실패: {text[:100]}")
        return None

    stocks = []
    for s in data.get("stocks", []):
        try:
            stocks.append(StockImpact(
                ticker=s.get("ticker", ""),
                name=s.get("name", ""),
                market=s.get("market", "US"),
                direction=s.get("direction", "neutral"),
                reason=s.get("reason", ""),
                confidence=float(s.get("confidence", 0.5)),
            ))
        except (ValueError, TypeError) as e:
            logger.debug(f"[Parse] 종목 파싱 오류 스킵: {e}")

    return AnalysisResult(
        market_impact=data.get("market_impact", ""),
        impact_level=data.get("impact_level", "medium"),
        stocks=stocks,
    )


# ── 분석 + 저장 ───────────────────────────────────────────────────────────────

async def save_news_analysis(news_id: str, result: AnalysisResult) -> None:
    await supabase_service.update_news_analysis(news_id, result.to_news_update_dict())
    if result.stocks:
        await supabase_service.insert_news_stock_impacts(
            [s.to_dict(news_id) for s in result.stocks]
        )


async def analyze_news_item(
    news_id: str,
    title: str,
    summary: Optional[str],
    key_manager: GeminiKeyManager,
) -> None:
    """
    단일 뉴스 항목 분석 + 저장.
    키 매니저에서 키를 가져와 Gemini 호출.
    429 시 해당 키 rate limit 처리 후 재시도.
    """
    for attempt in range(RETRY_LIMIT):
        try:
            api_key = key_manager.next_key()
        except NoAvailableKeyError:
            logger.warning(f"[Analysis] 모든 키 rate limited, 뉴스 스킵: {title[:30]}")
            return

        try:
            result = await call_gemini(title, summary, api_key)
            if result is None:
                logger.warning(f"[Analysis] Gemini 분석 실패 (attempt {attempt+1}): {title[:40]}")
                if attempt < RETRY_LIMIT - 1:
                    await asyncio.sleep(RETRY_DELAY)
                continue

            await save_news_analysis(news_id, result)
            logger.info(f"[Analysis] 완료: {title[:40]} → {result.impact_level}, {len(result.stocks)}종목")
            return

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                key_manager.mark_rate_limited(api_key, cooldown_seconds=60)
                logger.warning(f"[Analysis] 429 rate limit: {api_key[:8]}..., 재시도")
                continue
            logger.error(f"[Analysis] HTTP 오류: {e}")
            return
        except Exception as e:
            logger.exception(f"[Analysis] 예외 발생: {e}")
            return


_ANALYSIS_CONCURRENCY = 3  # 동시 Gemini 호출 수 (키 개수와 맞춤)


async def analyze_unanalyzed_news(key_manager: GeminiKeyManager) -> int:
    """
    미분석 뉴스를 조회하여 동시 분석.
    Semaphore로 동시 호출 수를 제한해 rate limit 회피.

    Returns:
        분석 완료 건수
    """
    items = await supabase_service.get_unanalyzed_news(limit=50)
    if not items:
        logger.info("[Analysis] 분석할 뉴스 없음")
        return 0

    logger.info(f"[Analysis] 미분석 뉴스 {len(items)}건 분석 시작")
    sem = asyncio.Semaphore(_ANALYSIS_CONCURRENCY)

    async def _bounded(item: dict) -> None:
        async with sem:
            await analyze_news_item(
                news_id=item["id"],
                title=item["title"],
                summary=item.get("summary"),
                key_manager=key_manager,
            )

    await asyncio.gather(*[_bounded(item) for item in items])
    logger.info(f"[Analysis] {len(items)}건 분석 완료")
    return len(items)
