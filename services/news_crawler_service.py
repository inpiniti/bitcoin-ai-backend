"""
#62 뉴스 크롤러 서비스

주요 관심 시장: 미국 주식 (S&P500, 나스닥, 빅테크) + 국내 증시
크롤링 소스:
  - 네이버금융 해외증시 뉴스 (naver_finance)
  - 한국경제 글로벌마켓 (hankyung_global)
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("news_crawler_service")

KST = ZoneInfo("Asia/Seoul")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}

REQUEST_DELAY = 1.0


# ── 데이터클래스 ──────────────────────────────────────────────────────────────

@dataclass
class NewsItem:
    title: str
    summary: Optional[str]
    url: str
    source: str
    published_at: Optional[datetime]
    news_date: date

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "news_date": self.news_date.isoformat(),
        }


# ── 증권 관련 필터 키워드 (미국 중심 + 국내) ─────────────────────────────────

_ALLOW_KEYWORDS = [
    # 미국 지수/시장
    "s&p", "sp500", "s&p500", "나스닥", "nasdaq", "다우", "dow",
    # 연준/통화정책
    "연준", "fed", "fomc", "금리", "기준금리", "파월",
    # 미국 빅테크/주요 종목
    "엔비디아", "nvidia", "애플", "apple", "마이크로소프트", "microsoft",
    "구글", "google", "알파벳", "alphabet", "아마존", "amazon",
    "메타", "meta", "테슬라", "tesla", "tsmc", "반도체",
    "빅테크", "ai주", "인공지능 주",
    # 경제 지표
    "cpi", "pce", "고용지표", "실업률", "gdp", "인플레이션",
    # 국내 시장
    "코스피", "kospi", "코스닥", "kosdaq", "삼성전자", "sk하이닉스",
    # 공통
    "주식", "주가", "증시", "펀드", "etf", "채권", "환율",
    "비트코인", "이더리움", "코인", "crypto",
    "실적", "어닝", "earnings", "배당", "ipo",
    "월가", "뉴욕증시", "미국증시", "해외증시",
]


def filter_finance_news(items: list[NewsItem]) -> list[NewsItem]:
    """
    제목 또는 요약에 증권/시장 관련 키워드가 포함된 뉴스만 통과.
    미국 주식(S&P500, 나스닥, 빅테크) 우선 + 국내 증시 병행.
    """
    result = []
    for item in items:
        text = (item.title + " " + (item.summary or "")).lower()
        if any(kw in text for kw in _ALLOW_KEYWORDS):
            result.append(item)
        else:
            logger.debug(f"[Filter] 비관련 뉴스 스킵: {item.title}")
    return result


def deduplicate_news(items: list[NewsItem]) -> list[NewsItem]:
    """URL 기준 중복 제거 (순서 유지)"""
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        if item.url not in seen:
            seen.add(item.url)
            result.append(item)
    return result


# ── 날짜 파싱 유틸 ────────────────────────────────────────────────────────────

def _parse_korean_date(text: str) -> tuple[Optional[datetime], date]:
    """
    '2026.03.27 09:30' 형식 → (datetime(KST), date)
    파싱 실패 시 (None, 오늘 날짜)
    """
    today = datetime.now(KST).date()
    text = text.strip()
    for fmt in ("%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=KST)
            return dt, dt.date()
        except ValueError:
            continue
    return None, today


# ── 네이버금융 해외증시 뉴스 크롤러 ─────────────────────────────────────────

NAVER_FINANCE_BASE = "https://finance.naver.com"

# 해외증시 뉴스 (미국 포함 글로벌 뉴스)
NAVER_WORLD_NEWS_URL = NAVER_FINANCE_BASE + "/news/worldmarketnews.naver"

# 메인 종합뉴스 (국내+해외)
NAVER_MAIN_NEWS_URL = NAVER_FINANCE_BASE + "/news/mainnews.naver"


def parse_naver_finance_page(html: str) -> list[NewsItem]:
    """
    네이버금융 뉴스 페이지 HTML 파싱.
    .newsList > .newsList_item 구조 처리.
    """
    soup = BeautifulSoup(html, "lxml")
    items: list[NewsItem] = []
    today = datetime.now(KST).date()

    for li in soup.select(".newsList_item, .realtimeNewsList_item, li.block"):
        try:
            a_tag = li.select_one("a.articleSubject, a.tit, a[href*='/mnews/article']")
            if not a_tag:
                continue
            href = a_tag.get("href", "").strip()
            if not href:
                continue

            title = a_tag.get_text(strip=True)
            if not title:
                continue

            url = href if href.startswith("http") else NAVER_FINANCE_BASE + href
            summary_tag = li.select_one("p.articleSummary, .articleSummary, .summary")
            summary = summary_tag.get_text(strip=True) if summary_tag else None
            when_tag = li.select_one(".when, .date, span.time")
            published_at, news_date = (
                _parse_korean_date(when_tag.get_text()) if when_tag else (None, today)
            )

            items.append(NewsItem(
                title=title,
                summary=summary,
                url=url,
                source="naver_finance",
                published_at=published_at,
                news_date=news_date,
            ))
        except Exception as e:
            logger.debug(f"[NaverFinance] 파싱 오류 스킵: {e}")

    return items


async def crawl_naver_finance(max_pages: int = 3) -> list[NewsItem]:
    """
    네이버금융 해외증시 + 메인뉴스 크롤링.

    Returns:
        NewsItem 목록 (필터링 전)
    """
    urls = [
        NAVER_WORLD_NEWS_URL,                                               # 해외증시 page 1
        NAVER_MAIN_NEWS_URL,                                                # 메인 종합뉴스
        *[f"{NAVER_WORLD_NEWS_URL}?page={p}" for p in range(2, max_pages + 1)],  # 해외증시 page 2+
    ]

    items: list[NewsItem] = []

    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"[NaverFinance] HTTP {resp.status_code}: {url}")
                    continue
                page_items = parse_naver_finance_page(resp.text)
                logger.info(f"[NaverFinance] {len(page_items)}건: {url}")
                items.extend(page_items)

                if url != urls[-1]:
                    await asyncio.sleep(REQUEST_DELAY)
            except httpx.RequestError as e:
                logger.error(f"[NaverFinance] 요청 오류: {e}")

    return items


# ── 한국경제 글로벌마켓 크롤러 ───────────────────────────────────────────────

HANKYUNG_GLOBAL_URL = "https://www.hankyung.com/globalmarket"


def _parse_hankyung_page(html: str) -> list[NewsItem]:
    """한국경제 글로벌마켓 뉴스 파싱"""
    soup = BeautifulSoup(html, "lxml")
    items: list[NewsItem] = []
    today = datetime.now(KST).date()

    for article in soup.select("li.item, article.news-item, div.article-item"):
        try:
            a_tag = article.select_one("a")
            if not a_tag:
                continue
            href = a_tag.get("href", "").strip()
            if not href:
                continue
            url = href if href.startswith("http") else "https://www.hankyung.com" + href

            title_tag = article.select_one("h2, h3, .headline, .title")
            title = (title_tag or a_tag).get_text(strip=True)
            if not title:
                continue

            summary_tag = article.select_one("p, .summary, .lead")
            summary = summary_tag.get_text(strip=True) if summary_tag else None

            date_tag = article.select_one("time, .date, .datetime")
            published_at, news_date = (
                _parse_korean_date(date_tag.get("datetime", date_tag.get_text()))
                if date_tag else (None, today)
            )

            items.append(NewsItem(
                title=title,
                summary=summary,
                url=url,
                source="hankyung_global",
                published_at=published_at,
                news_date=news_date,
            ))
        except Exception as e:
            logger.debug(f"[Hankyung] 파싱 오류 스킵: {e}")

    return items


async def crawl_hankyung_global(max_pages: int = 2) -> list[NewsItem]:
    """한국경제 글로벌마켓 뉴스 크롤링"""
    items: list[NewsItem] = []

    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            url = f"{HANKYUNG_GLOBAL_URL}?page={page}"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"[Hankyung] HTTP {resp.status_code} page {page}")
                    break
                page_items = _parse_hankyung_page(resp.text)
                logger.info(f"[Hankyung] {len(page_items)}건 page {page}")
                items.extend(page_items)

                if page < max_pages:
                    await asyncio.sleep(REQUEST_DELAY)
            except httpx.RequestError as e:
                logger.error(f"[Hankyung] 요청 오류 page {page}: {e}")
                break

    return items


# ── 통합 크롤러 ───────────────────────────────────────────────────────────────

async def crawl_all_news(
    naver_pages: int = 3,
    hankyung_pages: int = 2,
) -> list[NewsItem]:
    """
    네이버금융 + 한국경제 동시 크롤링 → 증권 필터 → URL 중복 제거.

    주요 타겟: 미국 주식 (S&P500, 나스닥, 빅테크) + 국내 증시

    Returns:
        필터링 + 중복 제거된 NewsItem 목록
    """
    naver_task = crawl_naver_finance(max_pages=naver_pages)
    hankyung_task = crawl_hankyung_global(max_pages=hankyung_pages)

    naver_items, hankyung_items = await asyncio.gather(naver_task, hankyung_task)

    all_items = naver_items + hankyung_items
    filtered = filter_finance_news(all_items)
    unique = deduplicate_news(filtered)

    logger.info(
        f"[CrawlAll] 네이버 {len(naver_items)} + 한경 {len(hankyung_items)}"
        f" → 필터 후 {len(filtered)} → 중복 제거 후 {len(unique)}건"
    )
    return unique
