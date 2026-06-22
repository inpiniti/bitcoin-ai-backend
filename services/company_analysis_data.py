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
    
    for attempt in range(1):  # 1회만 시도
        headers = get_headers()
        try:
            async with httpx.AsyncClient(timeout=2, headers=headers, verify=False) as client:  # 타임아웃 2초
                await client.get(cookie_url)
                crumb_resp = await client.get(crumb_url)
                if crumb_resp.status_code == 200:
                    crumb = crumb_resp.text.strip()
                    if crumb:
                        return dict(client.cookies), crumb
        except Exception as e:
            logger.error(f"[Yahoo] Cookie 및 Crumb 획득 실패 (시도 {attempt+1}): {e}")
            
        await asyncio.sleep(0.5)
    
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
            "priceToBook": {"raw": clean_float(pbr_str), "fmt": f"{pbr_str}배"},
            "trailingEps": {"raw": clean_float(eps_str), "fmt": f"{eps_str}원"},
            "enterpriseToRevenue": {"raw": 0, "fmt": "N/A"},
            "enterpriseToEbitda": {"raw": 0, "fmt": "N/A"},
            "beta": {"raw": 0, "fmt": "N/A"}
        },
        "summaryDetail": {
            "fiftyTwoWeekLow": {"raw": low_52, "fmt": low_52_str},
            "fiftyTwoWeekHigh": {"raw": high_52, "fmt": high_52_str},
            "marketCap": {"raw": market_cap_num, "fmt": market_cap_str},
            "trailingPE": {"raw": clean_float(per_str), "fmt": f"{per_str}배"},
            "dividendYield": {"raw": clean_float(dividend_yield_str) / 100.0, "fmt": f"{dividend_yield_str}%"}
        },
        "earnings": {}
    }

    logger.info(f"[Naver] {symbol} ({stock_name}) 데이터 바인딩 완료")
    return profile

async def fetch_company_profile_and_financials_naver_us(symbol: str) -> dict:
    """
    네이버 금융 API를 사용하여 미국(해외) 주식의 상세 정보를 조회하고 기존 야후 파이낸스 스키마로 변환합니다.
    """
    symbol_upper = symbol.upper().strip()
    candidates = [symbol_upper] if "." in symbol_upper else [f"{symbol_upper}.O", f"{symbol_upper}.N", f"{symbol_upper}.K"]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://m.stock.naver.com/",
    }
    
    basic_data = None
    integration_data = None
    resolved_symbol = None
    
    async with httpx.AsyncClient(timeout=3, verify=False) as client:  # 타임아웃 3초로 단축
        for cand in candidates:
            basic_url = f"https://api.stock.naver.com/stock/{cand}/basic"
            try:
                resp = await client.get(basic_url, headers=headers)
                if resp.status_code == 200:
                    basic_data = resp.json()
                    resolved_symbol = cand
                    break
            except Exception as e:
                logger.warning(f"[NaverUS] Basic API failed for {cand}: {e}")
                
        if not basic_data or not resolved_symbol:
            logger.warning(f"[NaverUS] Could not resolve basic data for US symbol: {symbol}")
            return {}
            
        integration_url = f"https://api.stock.naver.com/stock/{resolved_symbol}/integration"
        try:
            resp_int = await client.get(integration_url, headers=headers)
            if resp_int.status_code == 200:
                integration_data = resp_int.json()
        except Exception as e:
            logger.warning(f"[NaverUS] Integration API failed for {resolved_symbol}: {e}")

    # 데이터 추출 및 기존 스키마 매핑
    stock_name = basic_data.get("stockName", symbol)
    stock_exchange = basic_data.get("stockExchangeName", "US")
    current_price_str = basic_data.get("closePrice", "0")
    current_price = clean_float(current_price_str)
    
    # stockItemTotalInfos 파싱
    total_infos = basic_data.get("stockItemTotalInfos", [])
    info_map = {item.get("code"): item.get("value") for item in total_infos if item.get("code")}
    
    low_52_str = info_map.get("lowPriceOf52Weeks", "N/A")
    high_52_str = info_map.get("highPriceOf52Weeks", "N/A")
    low_52 = clean_float(low_52_str)
    high_52 = clean_float(high_52_str)
    
    # 시가총액 파싱: "2조 4,060억 USD"
    market_cap_str = info_map.get("marketValue", "N/A")
    market_cap_num = 0.0
    try:
        clean_cap = market_cap_str.replace(" USD", "").strip()
        market_cap_num = parse_market_cap(clean_cap)
    except Exception:
        pass
        
    per_str = info_map.get("per", "N/A")
    eps_str = info_map.get("eps", "N/A")
    pbr_str = info_map.get("pbr", "N/A")
    bps_str = info_map.get("bps", "N/A")
    
    # integration 데이터에서 기업 개요 및 기타 재무 지표 추출
    business_summary = "정보가 제공되지 않습니다."
    if integration_data:
        business_summary = integration_data.get("corporateOverview", business_summary)
        
    profile = {
        "assetProfile": {
            "sector": basic_data.get("industryCodeType", {}).get("industryGroupKor", "US Stock"),
            "industry": stock_exchange,
            "longBusinessSummary": business_summary,
            "companyOfficers": [{"name": f"{stock_name} Management"}]
        },
        "financialData": {
            "currentPrice": {"raw": current_price, "fmt": f"${current_price_str}"},
            "totalRevenue": {"raw": 0, "fmt": "N/A"},
            "freeCashflow": {"raw": 0, "fmt": "N/A"},
            "operatingMargins": {"raw": 0, "fmt": "N/A"},
            "returnOnEquity": {"raw": 0, "fmt": "N/A"},
            "debtToEquity": {"raw": 0, "fmt": "N/A"},
            "revenueGrowth": {"raw": 0, "fmt": "N/A"},
            "grossProfits": {"raw": 0, "fmt": "N/A"},
            "ebitda": {"raw": 0, "fmt": "N/A"},
            "profitMargins": {"raw": 0, "fmt": "N/A"}
        },
        "defaultKeyStatistics": {
            "forwardPE": {"raw": clean_float(per_str), "fmt": f"{per_str}"},
            "pegRatio": {"raw": 0, "fmt": "N/A"},
            "priceToBook": {"raw": clean_float(pbr_str), "fmt": f"{pbr_str}"},
            "trailingEps": {"raw": clean_float(eps_str), "fmt": f"{eps_str}"},
            "enterpriseToRevenue": {"raw": 0, "fmt": "N/A"},
            "enterpriseToEbitda": {"raw": 0, "fmt": "N/A"},
            "beta": {"raw": 0, "fmt": "N/A"}
        },
        "summaryDetail": {
            "fiftyTwoWeekLow": {"raw": low_52, "fmt": low_52_str},
            "fiftyTwoWeekHigh": {"raw": high_52, "fmt": high_52_str},
            "marketCap": {"raw": market_cap_num, "fmt": market_cap_str},
            "trailingPE": {"raw": clean_float(per_str), "fmt": f"{per_str}"}
        },
        "earnings": {}
    }
    logger.info(f"[NaverUS] {symbol} ({stock_name}) 데이터 바인딩 완료")
    return profile

async def fetch_company_profile_and_financials(symbol: str) -> dict:
    """
    Yahoo Finance quoteSummary API를 활용하여 기업 프로필 및 재무 데이터를 가져옵니다 (쿠키/크럼 캐싱 적용).
    단, 한국 주식은 네이버 금융 연동을 통해 수집하여 차단 문제를 우회합니다.
    야후 파이낸스 조회가 실패하거나 429 등으로 차단될 경우, 네이버 해외 주식 API로 폴백합니다.
    """
    if is_korean_stock(symbol):
        return await fetch_company_profile_and_financials_naver(symbol)

    global _cached_cookies, _cached_crumb
    
    yahoo_success = False
    result_profile = {}
    
    for attempt in range(1):  # 1회만 시도
        if not _cached_crumb:
            _cached_cookies, _cached_crumb = await get_yahoo_cookie_and_crumb()
            
        if not _cached_crumb:
            logger.warning(f"[Yahoo] {symbol} Crumb 획득 실패로 인해 조회 불가")
            break
            
        url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=assetProfile,financialData,defaultKeyStatistics,summaryDetail,earnings&crumb={_cached_crumb}"
        headers = get_headers()
        
        try:
            async with httpx.AsyncClient(timeout=2, headers=headers, cookies=_cached_cookies, verify=False) as client:  # 타임아웃 2초
                resp = await client.get(url)
                
            if resp.status_code == 401:
                logger.warning(f"[Yahoo] {symbol} quoteSummary HTTP 401 (크럼 만료). 캐시 초기화 후 재시도...")
                _cached_cookies = None
                _cached_crumb = None
                continue
                
            if resp.status_code == 404:
                logger.warning(f"[Yahoo] {symbol} quoteSummary HTTP 404 (존재하지 않는 심볼)")
                break
                
            if resp.status_code != 200:
                logger.warning(f"[Yahoo] {symbol} quoteSummary HTTP {resp.status_code} (시도 {attempt+1})")
                await asyncio.sleep(0.5)
                continue
                
            data = resp.json()
            result = data.get("quoteSummary", {}).get("result")
            if not result:
                logger.warning(f"[Yahoo] {symbol} quoteSummary result empty (시도 {attempt+1})")
                await asyncio.sleep(0.5)
                continue
                
            result_profile = result[0]
            yahoo_success = True
            break
        except Exception as e:
            logger.error(f"[Yahoo] {symbol} quoteSummary 조회 중 에러 (시도 {attempt+1}): {e}")
            await asyncio.sleep(0.5)
            
    if yahoo_success and result_profile:
        return result_profile
        
    # 야후 파이낸스 조회 실패 시 네이버 해외 주식 API로 폴백
    logger.info(f"[Yahoo-Fallback] 야후 파이낸스 차단 또는 조회 실패로 네이버 해외 주식 API 폴백 실행: {symbol}")
    return await fetch_company_profile_and_financials_naver_us(symbol)

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
    글로벌 매크로 지표(주식 지수, 금리, 환율, 원자재, 공포지수 등)의 최근 수치 및 전일대비 변동률 데이터를 
    야후 파이낸스 Chart API(쿠키/크럼 미필요)를 통해 우선 수집하고, 실패 시 네이버 금융 및 디폴트 값으로 폴백합니다.
    """
    import asyncio
    import xml.etree.ElementTree as ET
    
    symbols = [
        "^GSPC",       # S&P 500
        "^IXIC",       # 나스닥 종합지수
        "^SOX",        # 필라델피아 반도체지수
        "^KS11",       # 코스피 지수
        "^TNX",        # 미국 10년물 국채 금리
        "USDKRW=X",    # 원·달러 환율
        "CL=F",        # WTI 원유 선물
        "GC=F",        # 국제 금 선물
        "^VIX",        # CBOE 변동성 지수
        "DX-Y.NYB"     # 달러 인덱스
    ]
    
    # 네이버 금융 폴백 매핑
    naver_map = {
        "^GSPC": ("SPI@SPX", "world_index"),
        "^IXIC": ("NAS@IXIC", "world_index"),
        "^SOX": ("NAS@SOX", "world_index"),
        "^KS11": ("KOSPI", "fchart"),
        "USDKRW=X": ("FX_USDKRW", "marketindex"),
        "CL=F": ("OIL_CL", "marketindex"),
        "GC=F": ("CMDT_GC", "marketindex"),
        "DX-Y.NYB": ("FX_USDX", "marketindex"),
    }
    
    # 최후의 수단 디폴트 값
    default_fallbacks = {
        "^TNX": 4.5,
        "^VIX": 15.0,
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    
    def clean_val(val_str):
        if isinstance(val_str, (int, float)):
            return float(val_str)
        return float(val_str.replace(",", "").replace("N/A", "0"))

    # 1) 네이버 해외지수 크롤러
    async def fetch_naver_world_index(client, symbol_naver: str) -> dict:
        tasks = []
        for page in range(1, 27):
            url = f"https://finance.naver.com/world/worldDayListJson.nhn?symbol={symbol_naver}&fdtc=0&page={page}"
            tasks.append(client.get(url, headers=headers))
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_records = []
        for r in responses:
            if isinstance(r, httpx.Response) and r.status_code == 200:
                try:
                    all_records.extend(r.json())
                except:
                    pass
        
        if not all_records:
            raise Exception(f"Naver world index {symbol_naver} returned no data")
            
        valid_records = [r for r in all_records if r.get("clos") is not None]
        if not valid_records:
            raise Exception(f"Naver world index {symbol_naver} has no valid close prices")
            
        price = float(valid_records[0]["clos"])
        closes = [float(r["clos"]) for r in reversed(valid_records)]
        highs = [float(r["high"]) if r.get("high") is not None else float(r["clos"]) for r in reversed(valid_records)]
        lows = [float(r["low"]) if r.get("low") is not None else float(r["clos"]) for r in reversed(valid_records)]
        
        change = float(valid_records[0].get("diff", 0.0))
        change_percent = float(valid_records[0].get("rate", 0.0))
        
        return {
            "price": price,
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "change": change,
            "changePercent": change_percent
        }

    # 2) 네이버 국내지수 XML 크롤러
    async def fetch_naver_fchart_index(client, symbol_naver: str) -> dict:
        url = f"https://fchart.stock.naver.com/sise.nhn?symbol={symbol_naver}&timeframe=day&count=260&requestType=0"
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"Naver fchart {symbol_naver} failed with status {resp.status_code}")
            
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")
        if not items:
            raise Exception(f"Naver fchart {symbol_naver} returned no items")
            
        closes = []
        highs = []
        lows = []
        for item in items:
            parts = item.attrib.get("data", "").split("|")
            if len(parts) >= 5:
                closes.append(float(parts[4]))
                highs.append(float(parts[2]))
                lows.append(float(parts[3]))
                
        if not closes:
            raise Exception(f"Naver fchart {symbol_naver} has no valid close prices")
            
        price = closes[-1]
        change = 0.0
        change_percent = 0.0
        if len(closes) >= 2:
            change = closes[-1] - closes[-2]
            change_percent = (change / closes[-2]) * 100.0
            
        return {
            "price": price,
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "change": change,
            "changePercent": change_percent
        }

    # 3) 네이버 시장지표 JSON 크롤러
    async def fetch_naver_marketindex_exchange(client, symbol_naver: str) -> dict:
        url = f"https://api.stock.naver.com/marketindex/exchange/{symbol_naver}/prices?pageSize=260&page=1"
        resp = await client.get(url, headers={**headers, "Accept": "application/json", "Referer": "https://m.stock.naver.com/"})
        if resp.status_code != 200:
            raise Exception(f"Naver marketindex {symbol_naver} failed with status {resp.status_code}")
            
        data = resp.json()
        if not data:
            raise Exception(f"Naver marketindex {symbol_naver} returned no data")
            
        valid_records = [r for r in data if r.get("closePrice") is not None]
        if not valid_records:
            raise Exception(f"Naver marketindex {symbol_naver} has no valid close prices")
            
        price = clean_val(valid_records[0]["closePrice"])
        closes = [clean_val(r["closePrice"]) for r in reversed(valid_records)]
        highs = list(closes)
        lows = list(closes)
        
        change = clean_val(valid_records[0].get("fluctuations", 0.0))
        flu_type = valid_records[0].get("fluctuationsType", {}).get("name", "")
        if "FALLING" in flu_type or "FALL" in flu_type:
            change = -abs(change)
            
        change_percent = clean_val(valid_records[0].get("fluctuationsRatio", 0.0))
        if "FALLING" in flu_type or "FALL" in flu_type:
            change_percent = -abs(change_percent)
            
        return {
            "price": price,
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "change": change,
            "changePercent": change_percent
        }

    async def fetch_single_indicator(client, symbol: str) -> dict:
        # 1. Primary: Yahoo Finance chart API
        url_yahoo = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d"
        try:
            r = await client.get(url_yahoo, headers=headers)
            if r.status_code == 200:
                data = r.json()
                result = data.get("chart", {}).get("result", [{}])[0]
                meta = result.get("meta", {})
                price = meta.get("regularMarketPrice")
                
                quote = result.get("indicators", {}).get("quote", [{}])[0]
                closes = [c for c in quote.get("close", []) if c is not None]
                highs = [h for h in quote.get("high", []) if h is not None]
                lows = [l for l in quote.get("low", []) if l is not None]
                
                if len(closes) >= 2 and price is not None:
                    change = price - closes[-2]
                    change_percent = (change / closes[-2]) * 100.0
                    return {
                        "price": price,
                        "closes": closes,
                        "highs": highs,
                        "lows": lows,
                        "change": change,
                        "changePercent": change_percent
                    }
        except Exception as e:
            logger.warning(f"[MacroData] Yahoo Chart API failed for {symbol}: {e}")
            
        # 2. Secondary: Naver Finance Fallback
        if symbol in naver_map:
            naver_sym, fetch_type = naver_map[symbol]
            try:
                logger.info(f"[MacroData] Trying Naver fallback for {symbol} ({naver_sym}, {fetch_type})...")
                if fetch_type == "world_index":
                    return await fetch_naver_world_index(client, naver_sym)
                elif fetch_type == "fchart":
                    return await fetch_naver_fchart_index(client, naver_sym)
                elif fetch_type == "marketindex":
                    return await fetch_naver_marketindex_exchange(client, naver_sym)
            except Exception as e:
                logger.error(f"[MacroData] Naver fallback failed for {symbol}: {e}")
                
        # 3. Last Resort: Sensible Default values to prevent application crashes
        if symbol in default_fallbacks:
            val = default_fallbacks[symbol]
            logger.warning(f"[MacroData] Using default fallback value for {symbol}: {val}")
            return {
                "price": val,
                "closes": [val] * 250,
                "highs": [val] * 250,
                "lows": [val] * 250,
                "change": 0.0,
                "changePercent": 0.0
            }
            
        logger.error(f"[MacroData] Failed to fetch macro indicator {symbol} and no default is mapped.")
        return {
            "price": 100.0,
            "closes": [100.0] * 250,
            "highs": [100.0] * 250,
            "lows": [100.0] * 250,
            "change": 0.0,
            "changePercent": 0.0
        }

    logger.info("[MacroData] Bulk fetching global macro indicators using cookie-free Yahoo Chart API...")
    
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        tasks = [fetch_single_indicator(client, sym) for sym in symbols]
        indicator_data = await asyncio.gather(*tasks)
        
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
    
    for symbol, data in zip(symbols, indicator_data):
        if not data:
            continue
            
        mapped_name = name_map.get(symbol, symbol)
        price = data["price"]
        closes = data["closes"]
        highs = data["highs"]
        lows = data["lows"]
        
        # 이동평균 계산
        fifty_day_avg = sum(closes[-50:]) / min(len(closes), 50) if closes else 0.0
        two_hundred_day_avg = sum(closes[-200:]) / min(len(closes), 200) if closes else 0.0
        
        # 52주 고점/저점 계산
        fifty_two_week_low = min(lows[-250:]) if lows else 0.0
        fifty_two_week_high = max(highs[-250:]) if highs else 0.0
        
        # 52주 백분위
        fifty_two_week_percentile = 0.0
        if fifty_two_week_high and fifty_two_week_low and (fifty_two_week_high - fifty_two_week_low) > 0:
            fifty_two_week_percentile = ((price - fifty_two_week_low) / (fifty_two_week_high - fifty_two_week_low)) * 100.0
            
        # MA 괴리율
        fifty_day_ma_diff = 0.0
        if fifty_day_avg > 0:
            fifty_day_ma_diff = ((price - fifty_day_avg) / fifty_day_avg) * 100.0
            
        two_hundred_day_ma_diff = 0.0
        if two_hundred_day_avg > 0:
            two_hundred_day_ma_diff = ((price - two_hundred_day_avg) / two_hundred_day_avg) * 100.0
            
        macro_dict[symbol] = {
            "name": mapped_name,
            "price": price,
            "change": data["change"],
            "changePercent": data["changePercent"],
            "fiftyDayAverage": fifty_day_avg,
            "twoHundredDayAverage": two_hundred_day_avg,
            "fiftyTwoWeekLow": fifty_two_week_low,
            "fiftyTwoWeekHigh": fifty_two_week_high,
            "fiftyTwoWeekPercentile": fifty_two_week_percentile,
            "fiftyDayMaDiff": fifty_day_ma_diff,
            "twoHundredDayMaDiff": two_hundred_day_ma_diff
        }
        
    return macro_dict



