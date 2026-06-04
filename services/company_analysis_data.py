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

async def get_yahoo_cookie_and_crumb() -> tuple[dict, str]:
    """
    Yahoo Finance의 Cookie와 Crumb를 동적으로 획득합니다.
    """
    cookie_url = "https://fc.yahoo.com"
    crumb_url = "https://query2.finance.yahoo.com/v1/test/getcrumb"
    
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, verify=False) as client:
            # 1. fc.yahoo.com에서 쿠키(A3 등) 획득
            await client.get(cookie_url)
            # 2. getcrumb에서 crumb 획득
            crumb_resp = await client.get(crumb_url)
            if crumb_resp.status_code == 200:
                crumb = crumb_resp.text.strip()
                # httpx의 Cookies 객체를 딕셔너리로 변환하여 반환
                return dict(client.cookies), crumb
    except Exception as e:
        logger.error(f"[Yahoo] Cookie 및 Crumb 획득 실패: {e}")
    
    return {}, ""

async def fetch_company_profile_and_financials(symbol: str) -> dict:
    """
    Yahoo Finance quoteSummary API를 활용하여 기업 프로필 및 재무 데이터를 가져옵니다.
    """
    cookies, crumb = await get_yahoo_cookie_and_crumb()
    if not crumb:
        logger.warning(f"[Yahoo] {symbol} Crumb 획득 실패로 인해 조회 불가")
        return {}
        
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=assetProfile,financialData,defaultKeyStatistics,summaryDetail,earnings&crumb={crumb}"
    
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, cookies=cookies, verify=False) as client:
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
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, verify=False) as client:
            resp = await client.get(url)
            
        if resp.status_code != 200:
            logger.warning(f"[GoogleNews] {symbol} RSS HTTP {resp.status_code}")
            return []
            
        root = ET.fromstring(resp.text)
        for item_el in list(root.iter("item"))[:15]:  # 최근 15개 기사만
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

