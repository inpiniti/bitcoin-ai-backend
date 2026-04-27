"""
S&P 500 뉴스 기반 영향도 분석 서비스

파이프라인:
  1. 뉴스 크롤링 (Yahoo/Google RSS)
  2. 뉴스 → 하나의 컨텍스트
  3. S&P 500 종목 리스트 (Wikipedia)
  4. GICS 섹터별로 Gemini에게 분석 요청
  4-1. Bullish 종목별 XGBoost 상승 확률
  4-2. Bullish 종목별 강화학습(RL) 신호
  4-3. Bullish 종목별 TimesFM 예측
  4-4. Bullish 종목별 Amazon Chronos-2 예측
  4-5. Bullish 종목별 Salesforce Moirai 예측
  5. 결과를 Supabase에 저장
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
from services.sp500_list_service import SP500Stock, fetch_sp500_list, group_by_sector
from services.sp500_crawler_service import crawl_market_news, build_news_context
from services import supabase_service

logger = logging.getLogger("sp500_analysis_service")

import os

# Hugging Face Secrets에 VERCEL_PROXY_URL (예: https://내버셀앱.vercel.app/api/simple/gemini-backend) 등록
# 값이 없으면 기존 구글 API 직접 호출로 폴백
VERCEL_PROXY_URL = os.environ.get("VERCEL_PROXY_URL", "").strip()

if VERCEL_PROXY_URL:
    GEMINI_API_URL = VERCEL_PROXY_URL
else:
    GEMINI_API_URL = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        "/gemini-2.0-flash:generateContent?key={api_key}"
    )

RETRY_LIMIT = 3
RETRY_DELAY = 5.0  # 초기 재시도 간격 (3초 → 5초)
SECTOR_DELAY = 2.0  # 섹터 간 요청 간격 (초)
CONCURRENCY = 1  # 동시 Gemini 호출 수 (429 에러 방지를 위해 1로 낮춤)


# ── 데이터클래스 ──────────────────────────────────────────────────────────────

@dataclass
class StockImpactResult:
    ticker: str
    name: str
    sector: str
    direction: str      # bullish | bearish | neutral
    confidence: float   # 0.0 ~ 1.0
    reason: str
    # 모델 예측 신호 (Step 4-1 ~ 4-5, bullish 종목만 채워짐)
    xgb_prob: Optional[float] = None        # XGBoost 상승 확률
    xgb_model_id: Optional[str] = None
    rl_signal: Optional[str] = None         # BUY / HOLD / SELL
    rl_model_id: Optional[str] = None
    timesfm_signal: Optional[str] = None    # up / down
    chronos_signal: Optional[str] = None    # up / down
    moirai_signal: Optional[str] = None     # up / down

    def to_dict(self, analysis_date: str, news_count: int) -> dict:
        d = {
            "analysis_date": analysis_date,
            "ticker": self.ticker,
            "name": self.name,
            "sector": self.sector,
            "direction": self.direction,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "news_count": news_count,
        }
        # 모델 신호 포함 (None이면 생략하지 않고 null로 저장)
        d["xgb_prob"] = self.xgb_prob
        d["xgb_model_id"] = self.xgb_model_id
        d["rl_signal"] = self.rl_signal
        d["rl_model_id"] = self.rl_model_id
        d["timesfm_signal"] = self.timesfm_signal
        d["chronos_signal"] = self.chronos_signal
        d["moirai_signal"] = self.moirai_signal
        return d


@dataclass
class SectorAnalysisResult:
    sector: str
    stocks: list[StockImpactResult] = field(default_factory=list)


# ── 프롬프트 ──────────────────────────────────────────────────────────────────

def build_sector_prompt(
    context: str,
    sector: str,
    stocks: list[SP500Stock],
) -> str:
    """섹터별 분석 프롬프트 생성"""
    ticker_list = "\n".join(
        f"- {s.ticker} ({s.name})" for s in stocks
    )
    return f"""You are a Wall Street equity analyst. Based on the news context below (last 24 hours), analyze the potential impact on each stock in the "{sector}" sector.

[NEWS CONTEXT - Last 24 Hours]
{context}

[STOCKS TO ANALYZE - {sector} Sector]
{ticker_list}

For EACH stock, determine:
- direction: "bullish" (positive impact), "bearish" (negative impact), or "neutral" (no significant impact)
- confidence: 0.0 to 1.0 (how confident you are)
- reason: Brief 1-sentence explanation in Korean

Respond with ONLY valid JSON (no markdown code blocks):
{{
  "sector": "{sector}",
  "results": [
    {{
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "direction": "bullish",
      "confidence": 0.75,
      "reason": "AI 투자 확대 수혜"
    }}
  ]
}}

IMPORTANT:
- Analyze EVERY stock in the list, do not skip any
- If a stock has no relevant news, set direction to "neutral" with low confidence
- JSON only, no other text"""


# ── Gemini 호출 ───────────────────────────────────────────────────────────────

async def _call_gemini_sector(
    context: str,
    sector: str,
    stocks: list[SP500Stock],
    api_key: str,
) -> Optional[SectorAnalysisResult]:
    """단일 섹터 Gemini 분석 호출"""
    prompt = build_sector_prompt(context, sector, stocks)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
    }
    url = GEMINI_API_URL.format(api_key=api_key)

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload)

    if resp.status_code == 429:
        raise httpx.HTTPStatusError("Rate Limited", request=resp.request, response=resp)
    if resp.status_code != 200:
        logger.warning(f"[Gemini] HTTP {resp.status_code} ({sector}): {resp.text[:200]}")
        return None

    try:
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_sector_response(text, sector)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.warning(f"[Gemini] 응답 파싱 실패 ({sector}): {e}")
        return None


def _parse_sector_response(text: str, sector: str) -> Optional[SectorAnalysisResult]:
    """Gemini 응답 → SectorAnalysisResult"""
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.debug(f"[Parse] JSON 파싱 실패 ({sector}): {text[:100]}")
        return None

    stocks = []
    for s in data.get("results", []):
        try:
            stocks.append(StockImpactResult(
                ticker=s.get("ticker", ""),
                name=s.get("name", ""),
                sector=sector,
                direction=s.get("direction", "neutral"),
                confidence=float(s.get("confidence", 0.5)),
                reason=s.get("reason", ""),
            ))
        except (ValueError, TypeError) as e:
            logger.debug(f"[Parse] 종목 파싱 스킵 ({sector}): {e}")

    return SectorAnalysisResult(sector=sector, stocks=stocks)


# ── 전체 파이프라인 ───────────────────────────────────────────────────────────

async def analyze_sector_with_retry(
    context: str,
    sector: str,
    stocks: list[SP500Stock],
    key_manager: GeminiKeyManager,
) -> Optional[SectorAnalysisResult]:
    """재시도 + 키 로테이션 포함 섹터 분석"""
    max_attempts = 30  # 충분한 재시도 횟수 부여
    for attempt in range(max_attempts):
        try:
            api_key = key_manager.next_key()
        except NoAvailableKeyError:
            # 모든 키가 쿨다운 중일 경우 더 길게 대기
            wait_seconds = 15 + (attempt // 3) * 5  # 15초부터 5초씩 증가
            logger.warning(f"[Analysis] 모든 키 소진. {wait_seconds}초 대기 후 재시도... ({sector})")
            await asyncio.sleep(wait_seconds)
            continue

        try:
            result = await _call_gemini_sector(context, sector, stocks, api_key)
            if result is not None:
                logger.info(f"[Analysis] {sector}: {len(result.stocks)}종목 분석 완료")
                return result
            # 결과가 None 인데 429 등의 예외는 아닌 경우 (파싱 실패 등)
            if attempt < max_attempts - 1:
                await asyncio.sleep(RETRY_DELAY)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                key_manager.mark_rate_limited(api_key, cooldown_seconds=60)
                # 429 시 exponential backoff: 5초 → 10초 → 15초...
                backoff_delay = RETRY_DELAY * (2 ** (attempt % 3))
                logger.warning(f"[Analysis] 429 ({sector}): {api_key[:8]}... {backoff_delay}초 후 재시도")
                await asyncio.sleep(backoff_delay)
                continue
            logger.error(f"[Analysis] HTTP 오류 ({sector}): {e}")
            if attempt < max_attempts - 1:
                await asyncio.sleep(RETRY_DELAY)
                continue
        except Exception as e:
            logger.exception(f"[Analysis] 예외 ({sector}): {e}")
            if attempt < max_attempts - 1:
                await asyncio.sleep(RETRY_DELAY)
                continue

    logger.error(f"[Analysis] {sector} 최대 재시도({max_attempts}) 넘음. 스킵.")
    return None


async def run_sp500_analysis(
    key_manager: GeminiKeyManager,
    hours: int = 24,
) -> dict:
    """
    전체 S&P 500 영향도 분석 파이프라인 실행.

    1. 뉴스 크롤링
    2. 컨텍스트 생성
    3. S&P 500 리스트 조회
    4. 섹터별 Gemini 분석 (병렬, Semaphore)
    4-1~4-5. Bullish 종목 모델 신호 수집 (XGB/RL/TimesFM/Chronos/Moirai)
    5. Supabase 저장

    Returns:
        실행 결과 요약 dict
    """
    from services import sp500_signal_service

    analysis_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info(f"[SP500] ═══ 분석 시작 (date={analysis_date}) ═══")

    # Step 1: 뉴스 크롤링
    logger.info("[SP500] Step 1: 뉴스 크롤링...")
    news_items = await crawl_market_news(hours=hours)
    if not news_items:
        logger.warning("[SP500] 수집된 뉴스 없음, 중단")
        return {"status": "no_news", "news_count": 0}

    # Step 2: 컨텍스트 생성
    logger.info("[SP500] Step 2: 컨텍스트 생성...")
    context = build_news_context(news_items, max_items=300)

    # Step 3: S&P 500 종목 리스트
    logger.info("[SP500] Step 3: S&P 500 종목 리스트 조회...")
    sp500_stocks = await fetch_sp500_list()
    sector_groups = group_by_sector(sp500_stocks)
    logger.info(f"[SP500] {len(sp500_stocks)}개 종목, {len(sector_groups)}개 섹터")

    # Step 4: 섹터별 Gemini 분석 (순차, 요청 간격 포함)
    logger.info("[SP500] Step 4: Gemini 섹터별 분석 시작...")
    sem = asyncio.Semaphore(CONCURRENCY)
    all_results: list[StockImpactResult] = []

    async def _bounded_analysis(sector: str, stocks: list[SP500Stock], delay: float):
        async with sem:
            await asyncio.sleep(delay)  # 섹터 간 요청 간격
            return await analyze_sector_with_retry(context, sector, stocks, key_manager)

    tasks = [
        _bounded_analysis(sector, stocks, idx * SECTOR_DELAY)
        for idx, (sector, stocks) in enumerate(sector_groups.items())
    ]
    sector_results = await asyncio.gather(*tasks)

    bullish_count = 0
    bearish_count = 0
    neutral_count = 0

    for result in sector_results:
        if result is not None:
            all_results.extend(result.stocks)
            for s in result.stocks:
                if s.direction == "bullish":
                    bullish_count += 1
                elif s.direction == "bearish":
                    bearish_count += 1
                else:
                    neutral_count += 1

    logger.info(
        f"[SP500] 분석 완료: {len(all_results)}종목 "
        f"(↑{bullish_count} ↓{bearish_count} →{neutral_count})"
    )

    # Step 4-1~4-5: Bullish/Bearish & Confidence >= 0.5 종목에 모델 신호 추가
    actionable_stocks = [
        s for s in all_results
        if s.direction in ["bullish", "bearish"] and s.confidence >= 0.5
    ]
    logger.info(
        f"[SP500] Step 4-1~4-5: 모델 신호 수집 시작 "
        f"({len(actionable_stocks)}개 종목: bullish/bearish & confidence >= 0.5)"
    )

    # 활성 XGBoost / RL 모델 ID 조회
    xgb_model_id, rl_model_id = await sp500_signal_service.load_active_model_ids()

    # 종목별 모델 신호 병렬 수집
    actionable_tickers = [s.ticker for s in actionable_stocks]
    signal_map = await sp500_signal_service.enrich_stocks_with_models(
        tickers=actionable_tickers,
        xgb_model_id=xgb_model_id,
        rl_model_id=rl_model_id,
    )

    # 결과 객체에 신호 병합
    for stock in all_results:
        if stock.ticker in signal_map:
            sig = signal_map[stock.ticker]
            stock.xgb_prob = sig.get("xgb_prob")
            stock.xgb_model_id = sig.get("xgb_model_id")
            stock.rl_signal = sig.get("rl_signal")
            stock.rl_model_id = sig.get("rl_model_id")
            stock.timesfm_signal = sig.get("timesfm_signal")
            stock.chronos_signal = sig.get("chronos_signal")
            stock.moirai_signal = sig.get("moirai_signal")

    logger.info("[SP500] Step 4-1~4-5: 모델 신호 수집 완료")

    # Step 5: Supabase 저장
    logger.info("[SP500] Step 5: Supabase 저장...")
    news_count = len(news_items)

    try:
        # 종목별 영향도 저장
        impact_rows = [r.to_dict(analysis_date, news_count) for r in all_results]
        await supabase_service.upsert_sp500_daily_impact(impact_rows)

        # 메타 데이터 저장
        sources = list(set(item.source for item in news_items))
        meta = {
            "analysis_date": analysis_date,
            "news_count": news_count,
            "news_sources": sources,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
        }
        await supabase_service.upsert_sp500_analysis_meta(meta)

        logger.info(f"[SP500] Supabase 저장 완료: {len(impact_rows)}건")
    except Exception as e:
        logger.exception(f"[SP500] Supabase 저장 실패: {e}")

    summary = {
        "status": "ok",
        "analysis_date": analysis_date,
        "news_count": news_count,
        "total_stocks": len(all_results),
        "bullish": bullish_count,
        "bearish": bearish_count,
        "neutral": neutral_count,
        "model_enriched": len(signal_map),
        "xgb_model_id": xgb_model_id,
        "rl_model_id": rl_model_id,
    }
    logger.info(f"[SP500] ═══ 분석 완료 ═══ {summary}")
    return summary
