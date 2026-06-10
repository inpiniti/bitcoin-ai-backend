"""
Yahoo Finance data fetching functions for Company Analysis
"""
import logging
import httpx
import asyncio
import random

logger = logging.getLogger("company_analysis_data")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Global cache for Yahoo cookie and crumb to prevent frequent fc.yahoo.com calls
_cached_cookies = None
_cached_crumb = None

def get_headers() -> dict:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ko-KR;q=0.8,ko;q=0.7",
        "Connection": "keep-alive"
    }

async def get_yahoo_cookie_and_crumb() -> tuple[dict, str]:
    """
    Yahoo Finance의 Cookie와 Crumb를 동적으로 획득합니다 (재시도 로직 포함).
    """
    cookie_url = "https://fc.yahoo.com"
    crumb_url = "https://query2.finance.yahoo.com/v1/test/getcrumb"
    
    for attempt in range(3):
        headers = get_headers()
        try:
            async with httpx.AsyncClient(timeout=15, headers=headers, verify=False) as client:
                await client.get(cookie_url)
                crumb_resp = await client.get(crumb_url)
                if crumb_resp.status_code == 200:
                    crumb = crumb_resp.text.strip()
                    if crumb:
                        return dict(client.cookies), crumb
        except Exception as e:
            logger.error(f"[Yahoo] Cookie 및 Crumb 획득 실패 (시도 {attempt+1}): {e}")
            
        await asyncio.sleep(1)
    
    return {}, ""

def clean_float(val_str: str) -> float:
    if not val_str or val_str == "N/A" or val_str == "-":
        return 0.0
    val_str = val_str.replace(",", "").strip()
    val_str = val_str.replace("%", "")
    try:
        return float(val_str)
    except ValueError:
        return 0.0

def parse_market_cap(val_str: str) -> float:
    if not val_str:
        return 0.0
    val_str = val_str.replace(",", "").strip()
    total = 0.0
    try:
        if "조" in val_str:
            parts = val_str.split("조")
            cho = float(parts[0].strip())
            total += cho * 1_000_000_000_000
            if len(parts) > 1 and "억" in parts[1]:
                ok = float(parts[1].replace("억", "").strip())
                total += ok * 100_000_000
        elif "억" in val_str:
            ok = float(val_str.replace("억", "").strip())
            total += ok * 100_000_000
    except Exception:
        pass
    return total

def is_korean_stock(symbol: str) -> bool:
    symbol = symbol.upper().strip()
    if symbol.isdigit() and len(symbol) == 6:
        return True
    if symbol.endswith((".KS", ".KQ")) and symbol[:-3].isdigit() and len(symbol[:-3]) == 6:
        return True
    return False

async def fetch_company_profile_and_financials_naver(symbol: str) -> dict:
    """
    네이버 금융 모바일 API 및 웹스크래핑을 병렬로 처리하여 한국 주식의 상세 정보를 조회합니다.
    """
    code = symbol.upper().replace(".KS", "").replace(".KQ", "").strip()
    if not (code.isdigit() and len(code) == 6):
        logger.warning(f"[Naver] 잘못된 한국 주식 코드: {symbol}")
        return {}
        
    basic_url = f"https://m.stock.naver.com/api/stock/{code}/basic"
    integration_url = f"https://m.stock.naver.com/api/stock/{code}/integration"
    pc_url = f"https://finance.naver.com/item/main.naver?code={code}"
    
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    
    async def fetch_json(client, url):
        try:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"[Naver] JSON 조회 실패 ({url}): {e}")
        return None

    async def fetch_html(client, url):
        try:
            resp = await client.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.text
        except Exception as e:
            logger.error(f"[Naver] HTML 조회 실패 ({url}): {e}")
        return None

    try:
        async with httpx.AsyncClient(headers=headers, verify=False) as client:
            basic_data, integration_data, pc_html = await asyncio.gather(
                fetch_json(client, basic_url),
                fetch_json(client, integration_url),
                fetch_html(client, pc_url)
            )
    except Exception as e:
        logger.error(f"[Naver] 병렬 데이터 수집 중 치명적 오류: {e}")
        return {}

    if not basic_data or not integration_data:
        logger.warning(f"[Naver] 필수 API 데이터 수집 실패: {symbol}")
        return {}

    stock_name = basic_data.get("stockName", symbol)
    stock_exchange = basic_data.get("stockExchangeName", "KOSPI")
    sosok = basic_data.get("sosok", "0") # 0: 코스피, 1: 코스닥
    current_price_str = basic_data.get("closePrice", "0")
    current_price = clean_float(current_price_str)
    
    total_infos = integration_data.get("totalInfos", [])
    info_map = {item.get("code"): item.get("value") for item in total_infos if item.get("code")}
    
    low_52_str = info_map.get("lowPriceOf52Weeks", "N/A")
    high_52_str = info_map.get("highPriceOf52Weeks", "N/A")
    low_52 = clean_float(low_52_str)
    high_52 = clean_float(high_52_str)
    
    market_cap_str = info_map.get("marketValue", "N/A")
    market_cap_num = parse_market_cap(market_cap_str)
    
    per_str = info_map.get("per", "N/A")
    eps_str = info_map.get("eps", "N/A")
    forward_pe_str = info_map.get("cnsPer", "N/A")
    forward_eps_str = info_map.get("cnsEps", "N/A")
    pbr_str = info_map.get("pbr", "N/A")
    bps_str = info_map.get("bps", "N/A")
    dividend_yield_str = info_map.get("dividendYieldRatio", "N/A")
    dividend_str = info_map.get("dividend", "N/A")
    
    business_summary = "정보가 제공되지 않습니다."
    revenue_growth = 0.0
    total_revenue = 0.0
    operating_margin = 0.0
    net_margin = 0.0
    roe = 0.0
    debt_to_equity = 0.0
    op_income = 0.0
    
    last_actual_idx = 2
    rev_str = "0"
    op_inc_str = "0"
    
    if pc_html:
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(pc_html, "lxml")
            summary_div = soup.find("div", class_="summary_info")
            if summary_div:
                business_summary = summary_div.text.strip().replace("\n", " ").replace("\t", " ")
                
            tbl = soup.find("table", class_="tb_type1_ifrs")
            if tbl:
                thead_trs = tbl.find("thead").find_all("tr")
                if len(thead_trs) > 1:
                    ths = [th.text.strip().replace("\n", "").replace("\t", "") for th in thead_trs[1].find_all("th")]
                    years = ths[:4]
                    
                    last_actual_idx = 0
                    for idx, year in enumerate(years):
                        if "(E)" not in year:
                            last_actual_idx = idx
                    prev_actual_idx = max(0, last_actual_idx - 1)
                    
                    rows = {}
                    for tr in tbl.find_all("tr"):
                        th_el = tr.find("th")
                        td_els = tr.find_all("td")
                        if th_el and td_els:
                            row_name = th_el.text.strip()
                            rows[row_name] = [td.text.strip() for td in td_els]
                            
                    def get_row_val(row_name, idx):
                        if row_name in rows and len(rows[row_name]) > idx:
                            return rows[row_name][idx]
                        return "0"
                        
                    rev_str = get_row_val("매출액", last_actual_idx)
                    total_revenue = clean_float(rev_str) * 100_000_000
                    
                    rev_prev = clean_float(get_row_val("매출액", prev_actual_idx))
                    if rev_prev > 0:
                        revenue_growth = (clean_float(rev_str) - rev_prev) / rev_prev
                        
                    op_inc_str = get_row_val("영업이익", last_actual_idx)
                    op_income = clean_float(op_inc_str) * 100_000_000
                    
                    operating_margin = clean_float(get_row_val("영업이익률", last_actual_idx))
                    net_margin = clean_float(get_row_val("순이익률", last_actual_idx))
                    roe = clean_float(get_row_val("ROE(지배주주)", last_actual_idx)) or clean_float(get_row_val("ROE", last_actual_idx))
                    debt_to_equity = clean_float(get_row_val("부채비율", last_actual_idx))
                    
        except Exception as e:
            logger.error(f"[Naver] HTML 파싱 실패: {e}")

    profile = {
        "assetProfile": {
            "sector": "KOSPI" if sosok == "0" else "KOSDAQ",
            "industry": stock_exchange,
            "longBusinessSummary": business_summary,
            "companyOfficers": [{"name": f"{stock_name} Management"}]
        },
        "financialData": {
            "currentPrice": {"raw": current_price, "fmt": current_price_str},
            "totalRevenue": {"raw": total_revenue, "fmt": f"{rev_str}억"},
            "freeCashflow": {"raw": 0, "fmt": "N/A"},
            "operatingMargins": {"raw": operating_margin / 100.0, "fmt": f"{operating_margin}%"},
            "returnOnEquity": {"raw": roe / 100.0, "fmt": f"{roe}%"},
            "debtToEquity": {"raw": debt_to_equity, "fmt": f"{debt_to_equity}%"},
            "revenueGrowth": {"raw": revenue_growth, "fmt": f"{revenue_growth * 100:.2f}%"},
            "grossProfits": {"raw": op_income, "fmt": f"{op_inc_str}억"},
            "ebitda": {"raw": op_income, "fmt": f"{op_inc_str}억"},
            "profitMargins": {"raw": net_margin / 100.0, "fmt": f"{net_margin}%"}
        },
        "defaultKeyStatistics": {
            "forwardPE": {"raw": clean_float(forward_pe_str), "fmt": f"{forward_pe_str}배"},
            "pegRatio": {"raw": 0, "fmt": "N/A"},
            "trailingEps": {"raw": clean_float(eps_str), "fmt": f"{eps_str}원"},
            "enterpriseToRevenue": {"raw": 0, "fmt": "N/A"},
            "enterpriseToEbitda": {"raw": 0, "fmt": "N/A"},
            "beta": {"raw": 0, "fmt": "N/A"}
        },
        "summaryDetail": {
            "fiftyTwoWeekLow": {"raw": low_52, "fmt": low_52_str},
            "fiftyTwoWeekHigh": {"raw": high_52, "fmt": high_52_str},
            "marketCap": {"raw": market_cap_num, "fmt": market_cap_str},
            "trailingPE": {"raw": clean_float(per_str), "fmt": f"{per_str}배"}
        },
        "earnings": {}
    }

    logger.info(f"[Naver] {symbol} ({stock_name}) 데이터 바인딩 완료")
    return profile

async def fetch_company_profile_and_financials(symbol: str) -> dict:
    """
    Yahoo Finance quoteSummary API를 활용하여 기업 프로필 및 재무 데이터를 가져옵니다 (쿠키/크럼 캐싱 적용).
    단, 한국 주식은 네이버 금융 연동을 통해 수집하여 차단 문제를 우회합니다.
    """
    if is_korean_stock(symbol):
        return await fetch_company_profile_and_financials_naver(symbol)

    global _cached_cookies, _cached_crumb
    
    for attempt in range(2):
        if not _cached_crumb:
            _cached_cookies, _cached_crumb = await get_yahoo_cookie_and_crumb()
            
        if not _cached_crumb:
            logger.warning(f"[Yahoo] {symbol} Crumb 획득 실패로 인해 조회 불가")
            return {}
            
        url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=assetProfile,financialData,defaultKeyStatistics,summaryDetail,earnings&crumb={_cached_crumb}"
        headers = get_headers()
        
        try:
            async with httpx.AsyncClient(timeout=15, headers=headers, cookies=_cached_cookies, verify=False) as client:
                resp = await client.get(url)
                
            if resp.status_code == 401:
                logger.warning(f"[Yahoo] {symbol} quoteSummary HTTP 401 (크럼 만료). 캐시 초기화 후 재시도...")
                _cached_cookies = None
                _cached_crumb = None
                continue
                
            if resp.status_code == 404:
                logger.warning(f"[Yahoo] {symbol} quoteSummary HTTP 404 (존재하지 않는 심볼)")
                return {}
                
            if resp.status_code != 200:
                logger.warning(f"[Yahoo] {symbol} quoteSummary HTTP {resp.status_code} (시도 {attempt+1})")
                await asyncio.sleep(1)
                continue
                
            data = resp.json()
            result = data.get("quoteSummary", {}).get("result")
            if not result:
                logger.warning(f"[Yahoo] {symbol} quoteSummary result empty (시도 {attempt+1})")
                await asyncio.sleep(1)
                continue
                
            return result[0]
        except Exception as e:
            logger.error(f"[Yahoo] {symbol} quoteSummary 조회 중 에러 (시도 {attempt+1}): {e}")
            await asyncio.sleep(1)
            
    return {}

async def fetch_company_news(symbol: str) -> list[dict]:
    """
    Google News RSS를 통해 특정 티커와 관련된 뉴스 데이터를 조회합니다.
    """
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    
    # 국내 주식의 경우 뒤의 .KS나 .KQ를 제거하여 검색 성능을 향상시킵니다 (예: 005930.KS -> 005930)
    search_keyword = symbol
    if symbol.endswith((".KS", ".KQ")):
        search_keyword = symbol.split(".")[0]
        
    url = f"https://news.google.com/rss/search?q={search_keyword}+stock&hl=en-US&gl=US&ceid=US:en"
    news_items = []
    
    try:
        headers = {"User-Agent": USER_AGENT}
        async with httpx.AsyncClient(timeout=15, headers=headers, verify=False) as client:
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

async def fetch_macro_indicators() -> dict:
    """
    글로벌 매크로 지표(주식 지수, 금리, 환율, 원자재, 공포지수 등)의 최근 수치 및 전일대비 변동률 데이터를 야후 파이낸스 API를 통해 일괄 수집합니다.
    """
    global _cached_cookies, _cached_crumb
    
    symbols = [
        "^GSPC",       # S&P 500
        "^IXIC",       # 나스닥 종합지수
        "^SOX",        # 필라델피아 반도체지수
        "^KS11",       # 코스피 지수
        "^TNX",        # 미국 10년물 국채 금리 (수치가 10배 표기됨. 예: 4.15% -> 41.5)
        "USDKRW=X",    # 원·달러 환율
        "CL=F",        # WTI 원유 선물
        "GC=F",        # 국제 금 선물
        "^VIX",        # CBOE 변동성 지수 (공포지수)
        "DX-Y.NYB"     # 달러 인덱스
    ]
    symbols_str = ",".join(symbols)
    headers = get_headers()
    
    logger.info("[MacroData] Fetching global macro indicators with cookies and crumb...")
    
    for attempt in range(2):
        if not _cached_crumb:
            _cached_cookies, _cached_crumb = await get_yahoo_cookie_and_crumb()
            
        if not _cached_crumb:
            logger.warning("[MacroData] Crumb 획득 실패로 인해 조회 불가")
            return {}
            
        url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={symbols_str}&crumb={_cached_crumb}"
        
        try:
            async with httpx.AsyncClient(timeout=15, headers=headers, cookies=_cached_cookies, verify=False) as client:
                resp = await client.get(url)
                
            if resp.status_code == 401:
                logger.warning("[MacroData] API HTTP 401 (크럼 만료). 캐시 초기화 후 재시도...")
                _cached_cookies = None
                _cached_crumb = None
                continue
                
            if resp.status_code != 200:
                logger.warning(f"[MacroData] API HTTP {resp.status_code} (시도 {attempt+1})")
                await asyncio.sleep(1)
                continue
                
            data = resp.json()
            results = data.get("quoteResponse", {}).get("result", [])
            
            macro_dict = {}
            name_map = {
                "^GSPC": "S&P 500",
                "^IXIC": "NASDAQ",
                "^SOX": "SOX (필라델피아 반도체)",
                "^KS11": "KOSPI",
                "^TNX": "US 10Y Yield (미국 10년 국채금리)",
                "USDKRW=X": "USD/KRW (원·달러 환율)",
                "CL=F": "Crude Oil WTI (국제유가)",
                "GC=F": "Gold (국제 금값)",
                "^VIX": "CBOE VIX (공포지수)",
                "DX-Y.NYB": "US Dollar Index (달러인덱스)"
            }
            
            for item in results:
                symbol = item.get("symbol")
                mapped_name = name_map.get(symbol, symbol)
                
                price = item.get("regularMarketPrice", 0.0)
                change_percent = item.get("regularMarketChangePercent", 0.0)
                change = item.get("regularMarketChange", 0.0)
                
                # 미국 10년물 국채 금리의 경우 야후 파이낸스에서는 4.15%가 41.5로 반환되므로 변환
                if symbol == "^TNX" and price > 0:
                    price = price / 10.0
                    
                macro_dict[symbol] = {
                    "name": mapped_name,
                    "price": price,
                    "change": change,
                    "changePercent": change_percent
                }
                
            return macro_dict
            
        except Exception as e:
            logger.error(f"[MacroData] 매크로 지표 조회 에러 (시도 {attempt+1}): {e}")
            await asyncio.sleep(1)
            
    return {}



