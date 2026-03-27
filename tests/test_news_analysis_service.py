"""
#66 뉴스 AI 분석 서비스 테스트 (TDD)

Gemini로 뉴스의 영향 종목/시장 분석.
주요 타겟: 미국 주식 (S&P500, 나스닥, 빅테크) + 국내 증시

- Gemini JSON 응답 파싱
- 프롬프트 생성
- 분석 결과 저장 (Supabase mock)
- 키 매니저 연동 (다중 키 분배)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, datetime, timezone

from services.news_analysis_service import (
    build_analysis_prompt,
    parse_gemini_response,
    AnalysisResult,
    StockImpact,
)


# ── 프롬프트 생성 ─────────────────────────────────────────────────────────────

class TestBuildAnalysisPrompt:
    def test_prompt_contains_title(self):
        prompt = build_analysis_prompt("S&P500 급락", "연준 발언으로 증시 하락")
        assert "S&P500 급락" in prompt

    def test_prompt_contains_summary(self):
        prompt = build_analysis_prompt("제목", "연준이 금리를 인상했다")
        assert "연준이 금리를 인상했다" in prompt

    def test_prompt_requests_json_format(self):
        prompt = build_analysis_prompt("제목", "요약")
        assert "json" in prompt.lower() or "JSON" in prompt

    def test_prompt_mentions_us_market(self):
        """프롬프트가 미국 시장 분석을 요청하는지"""
        prompt = build_analysis_prompt("제목", "요약")
        assert any(kw in prompt for kw in ["미국", "US", "S&P", "나스닥", "NASDAQ"])

    def test_prompt_mentions_impact_level(self):
        """impact_level 필드 요청 포함"""
        prompt = build_analysis_prompt("제목", "요약")
        assert "impact_level" in prompt

    def test_prompt_handles_none_summary(self):
        """요약 없어도 정상 생성"""
        prompt = build_analysis_prompt("제목만 있음", None)
        assert "제목만 있음" in prompt
        assert isinstance(prompt, str)


# ── Gemini 응답 파싱 ─────────────────────────────────────────────────────────

VALID_GEMINI_RESPONSE = """
{
  "market_impact": "연준의 금리 동결로 S&P500 전체에 긍정적 영향이 예상됩니다.",
  "impact_level": "high",
  "stocks": [
    {
      "ticker": "SPY",
      "name": "S&P500 ETF",
      "market": "US",
      "direction": "bullish",
      "reason": "금리 동결로 밸류에이션 부담 완화",
      "confidence": 0.85
    },
    {
      "ticker": "NVDA",
      "name": "엔비디아",
      "market": "US",
      "direction": "bullish",
      "reason": "위험자산 선호 심리 회복",
      "confidence": 0.72
    }
  ]
}
"""

GEMINI_WITH_MARKDOWN = """
```json
{
  "market_impact": "금리 인상으로 성장주 하락 압력",
  "impact_level": "medium",
  "stocks": [
    {
      "ticker": "QQQ",
      "name": "나스닥100 ETF",
      "market": "US",
      "direction": "bearish",
      "reason": "고밸류에이션 성장주 조정 우려",
      "confidence": 0.78
    }
  ]
}
```
"""

INVALID_JSON_RESPONSE = "죄송합니다, 분석이 불가능합니다."

MISSING_STOCKS_RESPONSE = """
{
  "market_impact": "시장 영향 설명",
  "impact_level": "low"
}
"""


class TestParseGeminiResponse:
    def test_parses_valid_json(self):
        result = parse_gemini_response(VALID_GEMINI_RESPONSE)
        assert isinstance(result, AnalysisResult)
        assert result.market_impact == "연준의 금리 동결로 S&P500 전체에 긍정적 영향이 예상됩니다."
        assert result.impact_level == "high"

    def test_parses_stocks(self):
        result = parse_gemini_response(VALID_GEMINI_RESPONSE)
        assert len(result.stocks) == 2
        assert result.stocks[0].ticker == "SPY"
        assert result.stocks[0].market == "US"
        assert result.stocks[0].direction == "bullish"
        assert result.stocks[0].confidence == 0.85

    def test_strips_markdown_code_block(self):
        """```json ... ``` 래핑 제거 후 파싱"""
        result = parse_gemini_response(GEMINI_WITH_MARKDOWN)
        assert result is not None
        assert result.impact_level == "medium"
        assert result.stocks[0].ticker == "QQQ"

    def test_invalid_json_returns_none(self):
        result = parse_gemini_response(INVALID_JSON_RESPONSE)
        assert result is None

    def test_missing_stocks_returns_empty_list(self):
        result = parse_gemini_response(MISSING_STOCKS_RESPONSE)
        assert result is not None
        assert result.stocks == []

    def test_us_market_stock_parsed(self):
        result = parse_gemini_response(VALID_GEMINI_RESPONSE)
        markets = {s.market for s in result.stocks}
        assert "US" in markets


# ── AnalysisResult / StockImpact 데이터클래스 ────────────────────────────────

class TestAnalysisResult:
    def test_to_news_update_dict(self):
        result = AnalysisResult(
            market_impact="S&P500에 긍정적",
            impact_level="high",
            stocks=[],
        )
        d = result.to_news_update_dict()
        assert d["market_impact"] == "S&P500에 긍정적"
        assert d["impact_level"] == "high"
        assert "analyzed_at" in d

    def test_stock_impact_to_dict(self):
        stock = StockImpact(
            ticker="NVDA",
            name="엔비디아",
            market="US",
            direction="bullish",
            reason="AI 수요 폭증",
            confidence=0.9,
        )
        d = stock.to_dict(news_id="uuid-123")
        assert d["news_id"] == "uuid-123"
        assert d["ticker"] == "NVDA"
        assert d["market"] == "US"
        assert d["confidence"] == 0.9


# ── Supabase 저장 (mock) ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAnalyzeAndSave:
    async def test_analyze_news_calls_gemini_and_saves(self):
        """analyze_news_item이 Gemini 호출 후 Supabase에 저장하는지"""
        from services.news_analysis_service import analyze_news_item
        from services.gemini_key_manager import GeminiKeyManager

        mock_result = AnalysisResult(
            market_impact="연준 금리 동결로 증시 상승",
            impact_level="high",
            stocks=[
                StockImpact("SPY", "S&P500 ETF", "US", "bullish", "금리 부담 완화", 0.85)
            ],
        )

        with patch("services.news_analysis_service.call_gemini", new_callable=AsyncMock) as mock_gemini, \
             patch("services.news_analysis_service.save_news_analysis", new_callable=AsyncMock) as mock_save:
            mock_gemini.return_value = mock_result
            mock_save.return_value = None

            key_mgr = GeminiKeyManager("test_key")
            await analyze_news_item(
                news_id="uuid-001",
                title="연준 금리 동결",
                summary="FOMC 금리 동결 결정",
                key_manager=key_mgr,
            )

        mock_gemini.assert_called_once()
        mock_save.assert_called_once()

    async def test_analyze_skips_on_none_result(self):
        """Gemini가 None 반환 시 저장 스킵"""
        from services.news_analysis_service import analyze_news_item
        from services.gemini_key_manager import GeminiKeyManager

        with patch("services.news_analysis_service.call_gemini", new_callable=AsyncMock) as mock_gemini, \
             patch("services.news_analysis_service.save_news_analysis", new_callable=AsyncMock) as mock_save:
            mock_gemini.return_value = None

            key_mgr = GeminiKeyManager("test_key")
            await analyze_news_item(
                news_id="uuid-002",
                title="파싱 불가 뉴스",
                summary=None,
                key_manager=key_mgr,
            )

        mock_save.assert_not_called()
