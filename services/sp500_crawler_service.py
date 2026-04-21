"""
S&P 500 영향도 분석용 뉴스 크롤러

뉴스 소스:
  - Yahoo Finance RSS (미국 시장 뉴스)
  - Google News RSS (stock market, S&P 500 키워드)

24시간 내 뉴스만 수집 → 하나의 컨텍스트 텍스트로 합침.
"""
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

logger = logging.getLogger("sp500_crawler_service")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}


@dataclass
class RssNewsItem:
    title: str
    summary: str
    source: str
    published_at: Optional[datetime]

    def to_context_line(self, index: int) -> str:
        """컨텍스트 한 줄로 변환"""
        date_str = self.published_at.strftime("%Y-%m-%d %H:%M") if self.published_at else "N/A"
        summary = self.summary[:200] if self.summary else ""
        return f"[{index}] ({date_str}) {self.title}. {summary}"


# ── RSS 피드 URL 목록 ────────────────────────────────────────────────────────

YAHOO_RSS_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://finance.yahoo.com/rss/topstories",
]

GOOGLE_NEWS_RSS_QUERIES = [
    "S&P 500 stock market",
    "US stock market earnings",
    "Federal Reserve interest rate",
    "NVIDIA Apple Microsoft Tesla earnings",
    "Wall Street market today",
]


def _build_google_news_url(query: str) -> str:
    """Google News RSS URL 생성"""
    encoded = query.replace(" ", "+")
    return f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"


# ── HTML 태그 제거 유틸 ───────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """HTML 태그 제거"""
    return _TAG_RE.sub("", text).strip()


# ── RSS 파싱 ──────────────────────────────────────────────────────────────────

def _parse_rss_xml(xml_text: str, source: str, cutoff: datetime) -> list[RssNewsItem]:
    """RSS XML → RssNewsItem 리스트 (cutoff 이후 뉴스만)"""
    items: list[RssNewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"[RSS] XML 파싱 실패 ({source}): {e}")
        return items

    for item_el in root.iter("item"):
        try:
            title = item_el.findtext("title", "").strip()
            if not title:
                continue

            desc = _strip_html(item_el.findtext("description", ""))

            pub_date_str = item_el.findtext("pubDate", "")
            published_at: Optional[datetime] = None
            if pub_date_str:
                try:
                    published_at = parsedate_to_datetime(pub_date_str)
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=timezone.utc)
                    # cutoff 이전 뉴스 스킵
                    if published_at < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            items.append(RssNewsItem(
                title=title,
                summary=desc,
                source=source,
                published_at=published_at,
            ))
        except Exception as e:
            logger.debug(f"[RSS] 항목 파싱 스킵: {e}")

    return items


# ── 크롤링 함수 ───────────────────────────────────────────────────────────────

async def _fetch_rss(
    client: httpx.AsyncClient,
    url: str,
    source: str,
    cutoff: datetime,
) -> list[RssNewsItem]:
    """단일 RSS 피드 크롤링"""
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"[RSS] HTTP {resp.status_code}: {url}")
            return []
        items = _parse_rss_xml(resp.text, source, cutoff)
        logger.info(f"[RSS] {source}: {len(items)}건 ({url[:60]}...)")
        return items
    except httpx.RequestError as e:
        logger.error(f"[RSS] 요청 오류 ({source}): {e}")
        return []


async def crawl_market_news(hours: int = 24) -> list[RssNewsItem]:
    """
    Yahoo Finance + Google News RSS에서 최근 N시간 금융 뉴스 크롤링.

    Returns:
        중복 제거된 RssNewsItem 리스트
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    all_items: list[RssNewsItem] = []

    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        tasks = []

        # Yahoo Finance RSS
        for url in YAHOO_RSS_FEEDS:
            tasks.append(_fetch_rss(client, url, "yahoo_finance", cutoff))

        # Google News RSS
        for query in GOOGLE_NEWS_RSS_QUERIES:
            url = _build_google_news_url(query)
            tasks.append(_fetch_rss(client, url, "google_news", cutoff))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_items.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"[RSS] 크롤링 예외: {result}")

    # 제목 기준 중복 제거
    seen_titles: set[str] = set()
    unique: list[RssNewsItem] = []
    for item in all_items:
        title_key = item.title.lower().strip()
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique.append(item)

    # 시간순 정렬 (최신 먼저)
    unique.sort(key=lambda x: x.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    logger.info(f"[CrawlMarket] 총 {len(all_items)}건 → 중복 제거 후 {len(unique)}건")
    return unique


def build_news_context(items: list[RssNewsItem], max_items: int = 300) -> str:
    """
    뉴스 리스트 → 하나의 컨텍스트 텍스트로 합침.
    최대 max_items건까지만 포함.

    Returns:
        줄바꿈으로 구분된 뉴스 컨텍스트 문자열
    """
    limited = items[:max_items]
    lines = [item.to_context_line(i + 1) for i, item in enumerate(limited)]
    context = "\n".join(lines)
    logger.info(f"[Context] {len(limited)}건 → {len(context)}자 컨텍스트 생성")
    return context
