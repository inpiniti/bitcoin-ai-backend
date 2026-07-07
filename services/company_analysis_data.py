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

def _fnguide_latest_row_value(html: str, item_name: str) -> float | None:
    """
    FnGuide SVD_Finance HTML에서 특정 계정과목 행의 '가장 최근 기간' 값(억원 단위)을 추출.
    구조: <div class="th_b">{항목}</div> ... </th> <td class="r" title="42,781.91">42,782</td> ...
    각 td는 좌→우 시간순(가장 오른쪽이 최신)이므로 마지막 유효 숫자를 취한다.
    반환값 단위: 원(억원 × 1e8). 실패 시 None.
    """
    import re
    # 항목명 위치 이후 </tr> 전까지의 구간을 잡는다
    idx = html.find(item_name)
    if idx == -1:
        return None
    tail = html[idx: idx + 2000]
    end = tail.find("</tr>")
    if end != -1:
        tail = tail[:end]
    # td의 title 속성(전체 정밀도)을 우선 사용
    titles = re.findall(r'<td[^>]*title="([-\d,\.]+)"', tail)
    vals: list[float] = []
    for t in titles:
        try:
            vals.append(float(t.replace(",", "")))
        except ValueError:
            continue
    if not vals:
        return None
    # 가장 최근(마지막) 값 → 억원 단위이므로 원으로 환산
    return vals[-1] * 100_000_000


async def fetch_kr_financials_from_fnguide(code: str) -> dict:
    """
    FnGuide(comp.fnguide.com) 종합 재무제표 페이지에서 한국 주식의 현금흐름 데이터를 조회합니다.
    가입/인증 불필요. 영업·투자·재무 현금흐름과 현금및현금성자산을 추출해 원(KRW) 단위로 반환합니다.
    잉여현금흐름(FCF)은 (영업활동 + 투자활동) 근사치로 계산합니다.
    """
    gicode = f"A{code}"
    url = (
        f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp"
        f"?pGB=1&gicode={gicode}&cID=&MenuYn=Y&ReportGB=&NewMenuID=103&stkGb=701"
    )
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        async with httpx.AsyncClient(timeout=8, verify=False, headers=headers) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"[FnGuide] 재무 조회 실패 (code={code}): HTTP {resp.status_code}")
            return {}

        html = resp.text  # FnGuide 페이지는 UTF-8
        operating = _fnguide_latest_row_value(html, "영업활동으로인한현금흐름")
        investing = _fnguide_latest_row_value(html, "투자활동으로인한현금흐름")
        cash = _fnguide_latest_row_value(html, "현금및현금성자산")

        result: dict = {}
        if operating is not None:
            result["operatingCashflow"] = operating
            if investing is not None:
                # FCF 근사: 영업활동 + 투자활동(통상 음수) → 자본지출 차감 효과
                result["freeCashflow"] = operating + investing
        if cash is not None:
            result["totalCash"] = cash

        if result:
            logger.info(f"[FnGuide] {code} 현금흐름 수집 성공: keys={list(result.keys())}")
        else:
            logger.warning(f"[FnGuide] {code} 현금흐름 항목을 찾지 못함")
        return result
    except Exception as e:
        logger.error(f"[FnGuide] 현금흐름 수집 중 오류 (code={code}): {e}")
        return {}


async def fetch_naver_kr_annual_metrics(code: str) -> dict:
    """
    네이버 모바일 연간 재무 API에서 무인증으로 당좌비율·EPS·PER·PBR 등을 보충 수집합니다.
    (integration API가 손실 연도 등으로 N/A를 주는 경우를 메꾸기 위함)
    가장 최근 '확정 실적(컨센서스 아님)' 컬럼 값을 사용합니다.
    """
    url = f"https://m.stock.naver.com/api/stock/{code}/finance/annual"
    headers = {"User-Agent": USER_AGENT, "Referer": "https://m.stock.naver.com/"}
    try:
        async with httpx.AsyncClient(timeout=6, verify=False, headers=headers) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        fin = data.get("financeInfo", {})
        # 확정 실적(isConsensus == 'N') 중 가장 최근 key 선택
        actual_keys = [c.get("key") for c in fin.get("trTitleList", []) if c.get("isConsensus") == "N" and c.get("key")]
        if not actual_keys:
            return {}
        latest_key = sorted(actual_keys)[-1]

        row_by_title = {r.get("title"): r for r in fin.get("rowList", [])}

        def val(title: str) -> float:
            r = row_by_title.get(title)
            if not r:
                return 0.0
            return clean_float(r.get("columns", {}).get(latest_key, {}).get("value", "0"))

        return {
            "quickRatio": val("당좌비율"),
            "trailingEps": val("EPS"),
            "trailingPE": val("PER"),
            "priceToBook": val("PBR"),
        }
    except Exception as e:
        logger.warning(f"[NaverKR] 연간 재무 보충 수집 실패 (code={code}): {e}")
        return {}


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
            # 네이버 3종 + FnGuide 현금흐름 + 네이버 연간 보충지표를 모두 병렬로 수집
            basic_data, integration_data, pc_html, fnguide_cf, annual_metrics = await asyncio.gather(
                fetch_json(client, basic_url),
                fetch_json(client, integration_url),
                fetch_html(client, pc_url),
                fetch_kr_financials_from_fnguide(code),
                fetch_naver_kr_annual_metrics(code),
            )
    except Exception as e:
        logger.error(f"[Naver] 병렬 데이터 수집 중 치명적 오류: {e}")
        return {}
    fnguide_cf = fnguide_cf or {}
    annual_metrics = annual_metrics or {}

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

    # ── 무인증 보충 데이터(FnGuide 현금흐름 + 네이버 연간 지표) 병합 ──────────
    def _fmt_won(raw):
        if not raw:
            return None
        if abs(raw) >= 1e12:
            return f"{raw / 1e12:,.2f}조"
        return f"{raw / 1e8:,.0f}억"

    def _cell_won(raw):
        f = _fmt_won(raw)
        return {"raw": raw, "fmt": f} if f else {"raw": 0, "fmt": "N/A"}

    # EPS/PER/PBR: integration(loss 연도엔 N/A) → 네이버 연간 실적으로 보충
    def _pick(primary_str, unit, annual_val):
        v = clean_float(primary_str)
        if v != 0:
            return {"raw": v, "fmt": f"{primary_str}{unit}"}
        if annual_val:
            return {"raw": annual_val, "fmt": f"{annual_val}{unit}"}
        return {"raw": 0, "fmt": "N/A"}

    op_cf_cell = _cell_won(fnguide_cf.get("operatingCashflow"))
    fcf_cell = _cell_won(fnguide_cf.get("freeCashflow"))
    cash_cell = _cell_won(fnguide_cf.get("totalCash"))
    eps_cell = _pick(eps_str, "원", annual_metrics.get("trailingEps"))
    per_cell = _pick(per_str, "배", annual_metrics.get("trailingPE"))
    pbr_cell = _pick(pbr_str, "배", annual_metrics.get("priceToBook"))
    _quick = annual_metrics.get("quickRatio")
    quick_cell = {"raw": _quick, "fmt": f"{_quick}%"} if _quick else {"raw": 0, "fmt": "N/A"}

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
            "operatingCashflow": op_cf_cell,
            "freeCashflow": fcf_cell,
            "totalCash": cash_cell,
            "quickRatio": quick_cell,
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
            "priceToBook": pbr_cell,
            "trailingEps": eps_cell,
            "enterpriseToRevenue": {"raw": 0, "fmt": "N/A"},
            "enterpriseToEbitda": {"raw": 0, "fmt": "N/A"},
            "beta": {"raw": 0, "fmt": "N/A"}
        },
        "summaryDetail": {
            "fiftyTwoWeekLow": {"raw": low_52, "fmt": low_52_str},
            "fiftyTwoWeekHigh": {"raw": high_52, "fmt": high_52_str},
            "marketCap": {"raw": market_cap_num, "fmt": market_cap_str},
            "trailingPE": per_cell,
            "dividendYield": {"raw": clean_float(dividend_yield_str) / 100.0, "fmt": f"{dividend_yield_str}%"}
        },
        "earnings": {}
    }

    logger.info(f"[Naver] {symbol} ({stock_name}) 데이터 바인딩 완료")
    return profile

async def resolve_naver_reuters_code(symbol: str) -> str | None:
    """
    네이버 자동완성 API로 티커의 정확한 reutersCode를 해석합니다.
    예: 'AAPL' → 'AAPL.O', 'BRK.B' → 'BRKb' (BRK.B 같은 특수문자 티커의 핵심 해결책).
    무인증. 실패 시 None.
    """
    symbol_upper = symbol.upper().strip()
    try:
        async with httpx.AsyncClient(timeout=5, verify=False) as client:
            resp = await client.get(
                "https://ac.stock.naver.com/ac",
                params={"q": symbol, "target": "stock,index"},
                headers={"User-Agent": USER_AGENT},
            )
        if resp.status_code != 200:
            return None
        items = resp.json().get("items", [])
        # items는 [[{...}], ...] 또는 [{...}] 형태일 수 있어 평탄화
        flat = []
        for it in items:
            if isinstance(it, list):
                flat.extend(it)
            elif isinstance(it, dict):
                flat.append(it)
        # code(대문자)가 입력 티커와 정확히 일치하는 항목의 reutersCode 우선
        for it in flat:
            if str(it.get("code", "")).upper() == symbol_upper and it.get("reutersCode"):
                return it["reutersCode"]
        # 못 찾으면 첫 미국 주식 항목의 reutersCode
        for it in flat:
            if it.get("reutersCode") and it.get("nationCode") == "USA":
                return it["reutersCode"]
    except Exception as e:
        logger.warning(f"[NaverAC] reutersCode 해석 실패 ({symbol}): {e}")
    return None


async def fetch_company_profile_and_financials_naver_us(symbol: str) -> dict:
    """
    네이버 금융 API를 사용하여 미국(해외) 주식의 상세 정보를 조회하고 기존 야후 파이낸스 스키마로 변환합니다.
    BRK.B 같은 특수문자 티커는 자동완성 API로 정확한 reutersCode(→ BRKb)를 먼저 해석해 사용합니다.
    """
    symbol_upper = symbol.upper().strip()

    # 1순위: 자동완성으로 정확한 reutersCode 해석 (BRK.B → BRKb 등)
    candidates = []
    resolved_code = await resolve_naver_reuters_code(symbol_upper)
    if resolved_code:
        candidates.append(resolved_code)

    # 2순위(폴백): 접미사 추정 형식
    if "." in symbol_upper or "-" in symbol_upper:
        base = symbol_upper.replace(".", "").replace("-", "")
        candidates += [symbol_upper, base + ".O", base + ".N", base + ".K"]
    else:
        candidates += [f"{symbol_upper}.O", f"{symbol_upper}.N", f"{symbol_upper}.K"]

    # 중복 제거(순서 유지)
    candidates = list(dict.fromkeys(candidates))
    
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

async def fetch_company_via_yahoo_chart(symbol: str) -> dict:
    """
    Yahoo Chart v8 API로 기본 가격·지표 조회 (Crumb 불필요).
    HuggingFace에서 fc.yahoo.com 차단 시 최종 폴백으로 사용.
    매크로 지표 조회와 동일한 엔드포인트 사용 → HuggingFace에서 정상 작동 확인.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1y&interval=1d&includePrePost=false"
    try:
        async with httpx.AsyncClient(timeout=10, verify=False, headers=get_headers()) as client:
            r = await client.get(url)
            if r.status_code != 200:
                logger.warning(f"[YahooChart] {symbol} HTTP {r.status_code}")
                return {}

            data = r.json()
            result_list = data.get("chart", {}).get("result")
            if not result_list:
                logger.warning(f"[YahooChart] {symbol} result 없음")
                return {}

            result = result_list[0]
            meta = result.get("meta", {})

            price = meta.get("regularMarketPrice") or meta.get("regularMarketPreviousClose", 0) or 0
            prev_close = meta.get("regularMarketPreviousClose") or meta.get("chartPreviousClose") or price
            low_52 = meta.get("fiftyTwoWeekLow", 0) or 0
            high_52 = meta.get("fiftyTwoWeekHigh", 0) or 0

            quote = result.get("indicators", {}).get("quote", [{}])[0]
            closes = [c for c in quote.get("close", []) if c is not None]
            ma50 = sum(closes[-50:]) / min(len(closes), 50) if closes else price
            ma200 = sum(closes[-200:]) / min(len(closes), 200) if closes else price

            profile = {
                "assetProfile": {
                    "sector": meta.get("exchangeName", "US Stock"),
                    "industry": meta.get("exchangeName", ""),
                    "longBusinessSummary": f"{symbol} ({meta.get('exchangeName','')}) — 재무 상세 데이터 제한적 (Yahoo Chart 기본값)",
                    "companyOfficers": [{"name": f"{symbol} Management"}]
                },
                "financialData": {
                    "currentPrice": {"raw": price, "fmt": f"${price:.2f}"},
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
                    "forwardPE": {"raw": 0, "fmt": "N/A"},
                    "pegRatio": {"raw": 0, "fmt": "N/A"},
                    "priceToBook": {"raw": 0, "fmt": "N/A"},
                    "trailingEps": {"raw": 0, "fmt": "N/A"},
                    "enterpriseToRevenue": {"raw": 0, "fmt": "N/A"},
                    "enterpriseToEbitda": {"raw": 0, "fmt": "N/A"},
                    "beta": {"raw": meta.get("beta", 0) or 0, "fmt": "N/A"}
                },
                "summaryDetail": {
                    "fiftyTwoWeekLow": {"raw": low_52, "fmt": f"${low_52:.2f}"},
                    "fiftyTwoWeekHigh": {"raw": high_52, "fmt": f"${high_52:.2f}"},
                    "marketCap": {"raw": 0, "fmt": "N/A"},
                    "trailingPE": {"raw": 0, "fmt": "N/A"},
                    "dividendYield": {"raw": 0, "fmt": "N/A"},
                    "fiftyDayAverage": {"raw": ma50, "fmt": f"${ma50:.2f}"},
                    "twoHundredDayAverage": {"raw": ma200, "fmt": f"${ma200:.2f}"},
                },
                "earnings": {}
            }
            logger.info(f"[YahooChart] {symbol} 폴백 성공 (price={price:.2f}, 52w {low_52:.2f}~{high_52:.2f})")
            return profile

    except Exception as e:
        logger.error(f"[YahooChart] {symbol} 조회 실패: {e}")
        return {}


async def fetch_company_profile_and_financials(symbol: str) -> dict:
    """
    기업 프로필 및 재무 데이터 조회. 3단계 폴백:
      1. Yahoo quoteSummary (Crumb 없이 먼저 시도 → 있으면 사용)
      2. 네이버 해외주식 API (한국 주식은 네이버 KR)
      3. Yahoo Chart v8 (Crumb 불필요 — HuggingFace에서도 동작 확인)
    """
    if is_korean_stock(symbol):
        return await fetch_company_profile_and_financials_naver(symbol)

    global _cached_cookies, _cached_crumb

    # Yahoo는 클래스주에 하이픈을 사용 (BRK.B → BRK-B, BF.B → BF-B)
    yahoo_symbol = symbol.replace(".", "-")

    # ── 1단계: Yahoo quoteSummary (Crumb 없이 먼저 시도) ──────────────────
    async def _try_quote_summary(crumb: str | None, cookies: dict | None) -> dict:
        crumb_param = f"&crumb={crumb}" if crumb else ""
        url = (
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{yahoo_symbol}"
            f"?modules=assetProfile,financialData,defaultKeyStatistics,summaryDetail,earnings"
            f"{crumb_param}"
        )
        try:
            async with httpx.AsyncClient(
                timeout=5, headers=get_headers(),
                cookies=cookies or {}, verify=False
            ) as client:
                resp = await client.get(url)

            if resp.status_code == 401:
                return {"__need_crumb_refresh": True}
            if resp.status_code != 200:
                return {}

            data = resp.json()
            result = data.get("quoteSummary", {}).get("result")
            if result:
                logger.info(f"[Yahoo] {symbol} quoteSummary 성공")
                return result[0]
        except Exception as e:
            logger.warning(f"[Yahoo] {symbol} quoteSummary 에러: {e}")
        return {}

    # Crumb 없이 먼저 시도
    profile = await _try_quote_summary(None, None)
    if profile and "__need_crumb_refresh" not in profile:
        return profile

    # Crumb 획득 후 재시도
    if not _cached_crumb:
        _cached_cookies, _cached_crumb = await get_yahoo_cookie_and_crumb()

    if _cached_crumb:
        profile = await _try_quote_summary(_cached_crumb, _cached_cookies)
        if profile and "__need_crumb_refresh" not in profile:
            return profile
        # 크럼 만료 시 캐시 초기화
        _cached_cookies = None
        _cached_crumb = None
    else:
        logger.warning(f"[Yahoo] {symbol} Crumb 획득 실패 (HuggingFace IP 차단 가능성)")

    # ── 2단계: 네이버 해외주식 폴백 ──────────────────────────────────────
    logger.info(f"[Fallback-2] {symbol} 네이버 해외주식 API 시도")
    naver_result = await fetch_company_profile_and_financials_naver_us(symbol)
    if naver_result:
        return naver_result

    # ── 3단계: Yahoo Chart v8 (Crumb 불필요 — 최종 폴백) ────────────────
    logger.info(f"[Fallback-3] {symbol} Yahoo Chart v8 API 시도 (Crumb 불필요)")
    return await fetch_company_via_yahoo_chart(yahoo_symbol)

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



