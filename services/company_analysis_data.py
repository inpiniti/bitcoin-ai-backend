"""
Yahoo Finance data fetching functions for Company Analysis
"""
import logging
import httpx

logger = logging.getLogger("company_analysis_data")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}

async def fetch_company_profile_and_financials(symbol: str) -> dict:
    """
    Yahoo Finance quoteSummary API를 활용하여 기업 프로필 및 재무 데이터를 가져옵니다.
    """
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=assetProfile,financialData,defaultKeyStatistics,summaryDetail,earnings"
    
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            resp = await client.get(url)
            
        if resp.status_code != 200:
            logger.warning(f"[Yahoo] {symbol} quoteSummary HTTP {resp.status_code}")
            return {}
            
        data = resp.json()
        result = data.get("quoteSummary", {}).get("result")
        if not result:
            return {}
            
        return result[0]
    except Exception as e:
        logger.error(f"[Yahoo] {symbol} quoteSummary 조회 중 에러: {e}")
        return {}

async def fetch_company_news(symbol: str) -> list[dict]:
    """
    Google News RSS를 통해 특정 티커와 관련된 뉴스 데이터를 조회합니다.
    """
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    
    url = f"https://news.google.com/rss/search?q={symbol}+stock&hl=en-US&gl=US&ceid=US:en"
    news_items = []
    
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            resp = await client.get(url)
            
        if resp.status_code != 200:
            logger.warning(f"[GoogleNews] {symbol} RSS HTTP {resp.status_code}")
            return []
            
        root = ET.fromstring(resp.text)
        for item_el in root.iter("item")[:15]:  # 최근 15개 기사만
            title = item_el.findtext("title", "").strip()
            desc = item_el.findtext("description", "").strip()
            pub_date_str = item_el.findtext("pubDate", "")
            
            published_at_str = ""
            if pub_date_str:
                try:
                    dt = parsedate_to_datetime(pub_date_str)
                    published_at_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    published_at_str = pub_date_str
                    
            news_items.append({
                "title": title,
                "summary": desc,
                "published_at": published_at_str
            })
            
        return news_items
    except Exception as e:
        logger.error(f"[GoogleNews] {symbol} RSS 조회 실패: {e}")
        return []
