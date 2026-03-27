"""
#62 뉴스 크롤러 서비스 테스트 (TDD)

주요 관심 시장: 미국 주식 (S&P500, 나스닥, 빅테크) + 국내 증시 병행
크롤링 소스:
  - 네이버금융 해외증시 뉴스
  - 한국경제 글로벌마켓
  - (확장) 연합인포맥스 해외경제

- NewsItem 데이터클래스
- HTML 파싱 (네이버금융 해외증시)
- 증권/시장 관련 필터링 (미국 중심 키워드 포함)
- 중복 제거
- to_dict 직렬화
"""
import pytest
from datetime import date, datetime, timezone

from services.news_crawler_service import (
    NewsItem,
    filter_finance_news,
    parse_naver_finance_page,
    deduplicate_news,
)


# ── NewsItem.to_dict ──────────────────────────────────────────────────────────

class TestNewsItemToDict:
    def _make(self, **kwargs):
        defaults = dict(
            title="엔비디아 실적 서프라이즈…나스닥 급등",
            summary="AI 수요 폭증으로 엔비디아 분기 실적이 시장 예상치를 크게 상회했다.",
            url="https://n.news.naver.com/mnews/article/001/0001",
            source="naver_finance",
            published_at=datetime(2026, 3, 27, 9, 0, tzinfo=timezone.utc),
            news_date=date(2026, 3, 27),
        )
        defaults.update(kwargs)
        return NewsItem(**defaults)

    def test_to_dict_contains_required_fields(self):
        item = self._make()
        d = item.to_dict()
        assert d["title"] == "엔비디아 실적 서프라이즈…나스닥 급등"
        assert d["url"] == "https://n.news.naver.com/mnews/article/001/0001"
        assert d["source"] == "naver_finance"
        assert d["news_date"] == "2026-03-27"

    def test_published_at_iso_format(self):
        item = self._make()
        d = item.to_dict()
        assert "2026-03-27" in d["published_at"]

    def test_no_published_at(self):
        item = self._make(published_at=None)
        d = item.to_dict()
        assert d["published_at"] is None

    def test_summary_can_be_none(self):
        item = self._make(summary=None)
        d = item.to_dict()
        assert d["summary"] is None


# ── 네이버금융 HTML 파싱 ──────────────────────────────────────────────────────

NAVER_FINANCE_HTML = """
<html><body>
<ul class="newsList">
  <li class="newsList_item">
    <a class="articleSubject" href="/mnews/article/001/0001234">
      S&amp;P500 사상 최고치…연준 금리 동결 호재
    </a>
    <span class="press">연합뉴스</span>
    <span class="when">2026.03.27 09:30</span>
    <p class="articleSummary">연준이 금리를 동결하면서 S&amp;P500이 사상 최고치를 경신했다.</p>
  </li>
  <li class="newsList_item">
    <a class="articleSubject" href="/mnews/article/002/0005678">
      엔비디아, 블랙웰 GPU 수요 급증…목표주가 상향
    </a>
    <span class="press">한국경제</span>
    <span class="when">2026.03.27 10:00</span>
    <p class="articleSummary">월가 애널리스트들이 엔비디아 목표주가를 일제히 상향 조정했다.</p>
  </li>
</ul>
</body></html>
"""

NAVER_FINANCE_HTML_EMPTY = "<html><body><ul class='newsList'></ul></body></html>"

NAVER_FINANCE_HTML_NO_HREF = """
<html><body>
<ul class="newsList">
  <li class="newsList_item">
    <a class="articleSubject">제목만 있고 href 없음</a>
  </li>
</ul>
</body></html>
"""


class TestParseNaverFinancePage:
    def test_parses_two_items(self):
        items = parse_naver_finance_page(NAVER_FINANCE_HTML)
        assert len(items) == 2

    def test_title_stripped(self):
        items = parse_naver_finance_page(NAVER_FINANCE_HTML)
        assert "S&P500" in items[0].title or "S&amp;P500" in items[0].title or "SP500" in items[0].title

    def test_url_absolute(self):
        items = parse_naver_finance_page(NAVER_FINANCE_HTML)
        assert items[0].url.startswith("https://finance.naver.com")

    def test_source_is_naver_finance(self):
        items = parse_naver_finance_page(NAVER_FINANCE_HTML)
        assert all(i.source == "naver_finance" for i in items)

    def test_news_date_set(self):
        items = parse_naver_finance_page(NAVER_FINANCE_HTML)
        assert items[0].news_date is not None

    def test_empty_html_returns_empty(self):
        items = parse_naver_finance_page(NAVER_FINANCE_HTML_EMPTY)
        assert items == []

    def test_no_href_skipped(self):
        items = parse_naver_finance_page(NAVER_FINANCE_HTML_NO_HREF)
        assert items == []

    def test_summary_extracted(self):
        items = parse_naver_finance_page(NAVER_FINANCE_HTML)
        assert items[0].summary is not None
        assert len(items[0].summary) > 0


# ── 증권 관련 뉴스 필터 (미국 주식 중심) ─────────────────────────────────────

def _make_item(title: str, summary: str = "") -> NewsItem:
    return NewsItem(
        title=title,
        summary=summary,
        url=f"https://example.com/{abs(hash(title))}",
        source="naver_finance",
        published_at=None,
        news_date=date(2026, 3, 27),
    )


class TestFilterFinanceNews:
    # ── 미국 시장 키워드 ──────────────────────────────────────────────────────
    def test_sp500_passes(self):
        assert len(filter_finance_news([_make_item("S&P500 사상 최고치 경신")])) == 1

    def test_nasdaq_passes(self):
        assert len(filter_finance_news([_make_item("나스닥 2% 급등")])) == 1

    def test_fed_passes(self):
        assert len(filter_finance_news([_make_item("연준 금리 동결 결정")])) == 1

    def test_nvidia_passes(self):
        assert len(filter_finance_news([_make_item("엔비디아 실적 서프라이즈")])) == 1

    def test_apple_passes(self):
        assert len(filter_finance_news([_make_item("애플 아이폰 판매 급증")])) == 1

    def test_us_bigtech_symbol_passes(self):
        assert len(filter_finance_news([_make_item("TSMC 주가 10% 상승")])) == 1

    def test_interest_rate_passes(self):
        assert len(filter_finance_news([_make_item("미국 기준금리 인상 우려")])) == 1

    def test_dow_passes(self):
        assert len(filter_finance_news([_make_item("다우존스 최고치 돌파")])) == 1

    # ── 국내 시장 키워드도 유지 ───────────────────────────────────────────────
    def test_kospi_passes(self):
        assert len(filter_finance_news([_make_item("코스피 2600선 회복")])) == 1

    def test_crypto_passes(self):
        assert len(filter_finance_news([_make_item("비트코인 1억 돌파")])) == 1

    def test_domestic_stock_passes(self):
        assert len(filter_finance_news([_make_item("삼성전자 주가 급등")])) == 1

    # ── 비관련 뉴스 차단 ─────────────────────────────────────────────────────
    def test_weather_blocked(self):
        assert len(filter_finance_news([_make_item("오늘의 날씨 맑음")])) == 0

    def test_celebrity_blocked(self):
        assert len(filter_finance_news([_make_item("연예인 결혼 소식")])) == 0

    def test_sports_blocked(self):
        assert len(filter_finance_news([_make_item("월드컵 예선 결과")])) == 0

    # ── 요약에 키워드 있어도 통과 ────────────────────────────────────────────
    def test_keyword_in_summary_passes(self):
        items = [_make_item("글로벌 시장 동향", summary="S&P500 지수가 상승세를 보이고 있다")]
        assert len(filter_finance_news(items)) == 1

    def test_empty_list(self):
        assert filter_finance_news([]) == []

    def test_mixed_list_filters_correctly(self):
        items = [
            _make_item("S&P500 급락"),        # 통과
            _make_item("연예인 근황"),          # 차단
            _make_item("연준 금리 인상"),       # 통과
            _make_item("오늘의 운세"),          # 차단
            _make_item("엔비디아 목표주가 상향"), # 통과
        ]
        result = filter_finance_news(items)
        assert len(result) == 3


# ── URL 기준 중복 제거 ────────────────────────────────────────────────────────

class TestDeduplicateNews:
    def test_removes_duplicate_urls(self):
        item = _make_item("제목A")
        result = deduplicate_news([item, item])
        assert len(result) == 1

    def test_keeps_different_urls(self):
        result = deduplicate_news([_make_item("제목A"), _make_item("제목B")])
        assert len(result) == 2

    def test_empty_list(self):
        assert deduplicate_news([]) == []

    def test_preserves_order(self):
        items = [_make_item(f"제목{i}") for i in range(5)]
        result = deduplicate_news(items)
        assert [i.title for i in result] == [f"제목{i}" for i in range(5)]
