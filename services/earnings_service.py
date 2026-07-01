"""
실적발표 자동매매 — 수집 / 피처 / 학습 / 예측 서비스

설계: trends/실적발표_자동매매_시퀀스.md (§8 데이터셋, §11 운영)
의존: tradingview(실적일), yfinance(주가·재무), xgboost(섹터별 회귀), earnings_repo(저장)

MVP 범위
  - 수집: TradeingView 실적 캘린더 + yfinance 가격/재무
  - 학습: 섹터별 XGBoost 회귀 (타깃 = ret_hold), ml_models 재사용
  - 예측: 라벨 미완성 행에 ret_hold_pred / target_price 주입

변경사항:
  - yfinance rate limit 문제 해결 (트레이딩뷰에서 실적일만 가져오기)
  - 가격 데이터는 여전히 yfinance 사용
"""
import json
import logging
import math
import tempfile
from datetime import datetime, timedelta
from typing import Optional

from services import earnings_repo

logger = logging.getLogger("earnings_service")

# 학습에 사용하는 수치형 피처 (이 순서를 train/predict 가 공유)
FEATURE_COLUMNS = [
    "eps_surprise_pct", "rev_surprise_pct",
    "gross_margin", "net_margin", "sga_to_gross", "roe", "roc", "earnings_yield",
    "capex_to_ni", "debt_to_ni", "inventory_vs_sales", "eps_yoy",
    "per", "pbr", "ev_ebitda", "peg", "dividend_yield",
    "fed_funds", "ust10y", "cpi_yoy",
]


# ─────────────────────────────────────────────
# 공통 유틸
# ─────────────────────────────────────────────

def _f(v) -> Optional[float]:
    """None/NaN/inf 안전 변환."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _ratio(a, b) -> Optional[float]:
    a, b = _f(a), _f(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _close_on_or_before(hist, date) -> Optional[float]:
    """date(포함) 이전의 마지막 종가."""
    if hist is None or hist.empty:
        return None
    sub = hist.loc[:date]
    if sub.empty:
        return None
    return _f(sub["Close"].iloc[-1])


def _close_on_or_after(hist, date) -> Optional[float]:
    if hist is None or hist.empty:
        return None
    sub = hist.loc[date:]
    if sub.empty:
        return None
    return _f(sub["Close"].iloc[0])


def _close_nearby(date_close: dict, target_date, direction: int) -> Optional[float]:
    """
    date_close(date→close 딕셔너리)에서 target_date 기준으로
    direction=-1이면 이전, +1이면 이후 가장 가까운 거래일 종가 반환 (최대 7일 탐색).
    """
    for delta in range(1, 8):
        d = target_date + timedelta(days=delta * direction)
        if d in date_close:
            return date_close[d]
    return None


def _series_asof(series: dict, target_date) -> Optional[float]:
    """series(date→값)에서 target_date 당일 포함 이전 가장 가까운 값 (최대 10일 소급)."""
    for delta in range(0, 11):
        d = target_date - timedelta(days=delta)
        if d in series:
            return series[d]
    return None


def _fetch_rate_series() -> dict:
    """
    ^TNX(미국 10년물 국채금리) 2년치 → {date: yield%}.
    Yahoo Chart v8 (Crumb 불필요). 전체 수집 시작 시 1회만 호출.
    """
    from datetime import timezone as _tz

    data = _fetch_yahoo_chart_sync("^TNX", "2y")
    result_list = data.get("chart", {}).get("result")
    if not result_list:
        logger.warning("[earnings] ^TNX 금리 시계열 조회 실패")
        return {}
    res = result_list[0]
    ts = res.get("timestamp", [])
    closes = res.get("indicators", {}).get("quote", [{}])[0].get("close", [])
    out: dict = {}
    for t, c in zip(ts, closes):
        if t is not None and c is not None:
            out[datetime.fromtimestamp(int(t), tz=_tz.utc).date()] = float(c)
    logger.info(f"[earnings] ^TNX 10년물 금리 {len(out)}일치 로드")
    return out


def _fetch_yahoo_chart_sync(ticker: str, range_str: str = "2y") -> dict:
    """Yahoo Chart v8 API — Crumb 불필요, HuggingFace 정상 동작."""
    import httpx
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?range={range_str}&interval=1d"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=15, verify=False, headers=headers) as client:
            r = client.get(url)
        if r.status_code == 200:
            return r.json()
        logger.warning(f"[YahooChart] {ticker} HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"[YahooChart] {ticker} 조회 실패: {e}")
    return {}


# ─────────────────────────────────────────────
# SEC EDGAR XBRL API (무인증 정부 공개 API)
# ─────────────────────────────────────────────

_sec_ticker_cik: dict = {}


def _get_sec_ticker_cik() -> dict:
    """SEC EDGAR 전체 ticker→CIK10 매핑 (1회 로드, 프로세스 캐시)."""
    global _sec_ticker_cik
    if _sec_ticker_cik:
        return _sec_ticker_cik
    import httpx
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        headers = {"User-Agent": "EarningsCollector contact@example.com"}
        with httpx.Client(timeout=30, verify=False, headers=headers) as client:
            r = client.get(url)
        if r.status_code == 200:
            data = r.json()
            _sec_ticker_cik = {
                v["ticker"].upper(): str(v["cik_str"]).zfill(10)
                for v in data.values()
            }
            logger.info(f"[SEC] ticker→CIK {len(_sec_ticker_cik)}개 로드 완료")
        else:
            logger.warning(f"[SEC] company_tickers.json HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"[SEC] CIK 로드 실패: {e}")
    return _sec_ticker_cik


def _fetch_sec_companyfacts(ticker: str) -> dict:
    """SEC companyfacts JSON에서 us-gaap 딕셔너리 반환 (1회 호출)."""
    import httpx

    cik = _get_sec_ticker_cik().get(ticker.upper())
    if not cik:
        logger.warning(f"[SEC] {ticker} CIK 없음")
        return {}

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    headers = {"User-Agent": "EarningsCollector contact@example.com"}
    try:
        with httpx.Client(timeout=60, verify=False, headers=headers) as client:
            r = client.get(url)
        if r.status_code != 200:
            logger.warning(f"[SEC] {ticker} companyfacts HTTP {r.status_code}")
            return {}
        return r.json().get("facts", {}).get("us-gaap", {})
    except Exception as e:
        logger.warning(f"[SEC] {ticker} companyfacts 실패: {e}")
        return {}


def _is_quarterly_period(fact: dict) -> bool:
    """start~end 기간이 약 1분기(80~100일)인지 — 누적(YTD)값 제외용."""
    start, end = fact.get("start"), fact.get("end")
    if not end:
        return False
    if not start:
        return True  # 시점값(balance sheet)은 통과
    try:
        days = (datetime.strptime(end, "%Y-%m-%d").date()
                - datetime.strptime(start, "%Y-%m-%d").date()).days
    except ValueError:
        return False
    return 80 <= days <= 100


def _flow_map(us_gaap: dict, *tags: str, unit: str = "USD") -> dict:
    """
    손익계산서 항목(매출·이익 등) → {end_str: val}, 분기값(80~100일)만.
    여러 태그를 순서대로 시도(회사별 태그 차이 대응).
    """
    out: dict = {}
    for tag in tags:
        facts = us_gaap.get(tag, {}).get("units", {}).get(unit, [])
        for f in sorted(facts, key=lambda x: x.get("filed", "")):
            if not _is_quarterly_period(f):
                continue
            end = f.get("end")
            if end:
                out[end] = _f(f.get("val"))   # 최신 filed가 덮어씀(수정본 반영)
        if out:
            break
    return out


def _stock_map(us_gaap: dict, *tags: str, unit: str = "USD") -> dict:
    """대차대조표 항목(자산·자본 등) → {end_str: val}, 시점값."""
    out: dict = {}
    for tag in tags:
        facts = us_gaap.get(tag, {}).get("units", {}).get(unit, [])
        for f in sorted(facts, key=lambda x: x.get("filed", "")):
            end = f.get("end")
            if end:
                out[end] = _f(f.get("val"))
        if out:
            break
    return out


def _extract_eps_quarters(us_gaap: dict, ticker: str, max_quarters: int = 8) -> list[dict]:
    """
    EPS 분기 리스트. 분기값(80~100일)만 골라 누적(6·9개월) EPS 혼입 방지.
    반환: [{"fiscal_end": date, "filed": date, "eps_act": float|None}, ...] (오래된→최신)
      filed = 10-Q 제출일 (≈ 실적 발표일)
    """
    from datetime import date as _d

    # EPS 태그 — 표준 우선, 없으면 '계속영업 EPS' 등 폴백
    #   (ABNB 등 일부 종목은 EarningsPerShareDiluted 대신 다른 태그로 보고)
    eps_facts = []
    for key in (
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
        "IncomeLossFromContinuingOperationsPerDilutedShare",
        "IncomeLossFromContinuingOperationsPerBasicShare",
        "EarningsPerShareBasicAndDiluted",
    ):
        eps_facts = us_gaap.get(key, {}).get("units", {}).get("USD/shares", [])
        if eps_facts:
            break
    if not eps_facts:
        logger.warning(f"[SEC] {ticker} EPS 데이터 없음")
        return []

    cutoff = _d.today() - timedelta(days=730)
    seen: dict = {}
    for f in sorted(eps_facts, key=lambda x: x.get("filed", "")):  # filed 오름차순
        if f.get("form") not in ("10-Q", "10-K"):
            continue
        filed = f.get("filed")
        end = f.get("end")
        if not filed or not end:
            continue
        if datetime.strptime(filed, "%Y-%m-%d").date() <= cutoff:
            continue
        if not _is_quarterly_period(f):   # 분기값만 (누적 제외)
            continue
        # 당분기 보고만 인정 — 같은 filing에 끼어든 '전년 동기 비교값' 제외
        # (당분기: filed가 분기말 직후 ~40일 / 비교값: filed가 분기말 +1년 이상)
        gap = (datetime.strptime(filed, "%Y-%m-%d").date()
               - datetime.strptime(end, "%Y-%m-%d").date()).days
        if gap < 0 or gap > 100:
            continue
        # filed = 최초 제출일(=실제 발표일) 고정 / eps_act = 최신값(restated 반영)
        # 수정 제출(amended)이 발표일을 미래로 덮어쓰는 버그 방지
        if end not in seen:
            seen[end] = {"eps_act": _f(f.get("val")), "filed": filed}
        else:
            seen[end]["eps_act"] = _f(f.get("val"))

    recent_ends = sorted(seen.keys())[-max_quarters:]
    return [
        {
            "fiscal_end": datetime.strptime(e, "%Y-%m-%d").date(),
            "filed": datetime.strptime(seen[e]["filed"], "%Y-%m-%d").date(),
            "eps_act": seen[e]["eps_act"],
        }
        for e in recent_ends
    ]


def _extract_financials(us_gaap: dict) -> dict:
    """
    분기말(end) → 재무 피처 딕셔너리.
    SEC companyfacts에서 거장 기반 재무비율 계산.
    """
    revenue = _flow_map(
        us_gaap,
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    )
    gross = _flow_map(us_gaap, "GrossProfit")
    net_income = _flow_map(us_gaap, "NetIncomeLoss")
    sga = _flow_map(
        us_gaap,
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    )
    equity = _stock_map(
        us_gaap,
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    )
    assets = _stock_map(us_gaap, "Assets")
    lt_debt = _stock_map(
        us_gaap,
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    )
    retained = _stock_map(us_gaap, "RetainedEarningsAccumulatedDeficit")
    cash = _stock_map(
        us_gaap,
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    )

    # 모든 분기말 키 합집합
    all_ends = set(revenue) | set(gross) | set(net_income) | set(equity)

    out: dict = {}
    for end in all_ends:
        rev = revenue.get(end)
        gp = gross.get(end)
        ni = net_income.get(end)
        feat = {
            "gross_margin": _ratio(gp, rev),
            "net_margin": _ratio(ni, rev),
            "roe": _ratio(ni, equity.get(end)),
            "roc": _ratio(ni, assets.get(end)),       # ROA 근사
            "sga_to_gross": _ratio(sga.get(end), gp),
            "debt_to_ni": _ratio(lt_debt.get(end), ni),
            "retained_earnings": retained.get(end),
            "cash_sti": cash.get(end),
        }
        # None 만 있는 분기는 스킵
        if any(v is not None for v in feat.values()):
            out[end] = {k: v for k, v in feat.items() if v is not None}
    return out


# ─────────────────────────────────────────────
# 수집 (yfinance → earnings_events)
# ─────────────────────────────────────────────

def _build_features_from_info(info: dict) -> dict:
    """yfinance .info 에서 재무 비율 피처 추출."""
    debt = _f(info.get("totalDebt"))
    ni = _f(info.get("netIncomeToCommon"))
    return {
        "gross_margin": _f(info.get("grossMargins")),
        "net_margin": _f(info.get("profitMargins")),
        "roe": _f(info.get("returnOnEquity")),
        "fcf": _f(info.get("freeCashflow")),
        "debt_to_ni": _ratio(debt, ni),
        "per": _f(info.get("trailingPE")),
        "pbr": _f(info.get("priceToBook")),
        "ev_ebitda": _f(info.get("enterpriseToEbitda")),
        "peg": _f(info.get("trailingPegRatio") or info.get("pegRatio")),
        "dividend_yield": _f(info.get("dividendYield")),
        # .info 로 직접 안 나오는 항목은 None (확장 시 재무제표 파싱으로 보강)
        "sga_to_gross": None,
        "roc": _f(info.get("returnOnAssets")),  # 근사치(ROA) — 보강 전 임시
        "earnings_yield": _ratio(info.get("ebitda"), info.get("enterpriseValue")),
        "capex_to_ni": None,
        "retained_earnings": None,
        "cash_sti": _f(info.get("totalCash")),
        "inventory_vs_sales": None,
    }


def collect_event(ticker: str, earnings_date: Optional[str] = None,
                  sector: Optional[str] = None, tv_event: Optional[object] = None) -> Optional[dict]:
    """
    단일 (ticker, earnings_date) 이벤트를 yfinance로 구성해 upsert.
    earnings_date 미지정 시 가장 최근 과거 실적일을 사용.
    """
    import yfinance as yf

    # earnings_date 확인
    if not earnings_date:
        logger.debug(f"[earnings] {ticker} 실적일 미지정")
        return None

    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception as e:
        logger.warning(f"[earnings] {ticker} info 조회 실패: {e}")
        info = {}

    # 실적일 + EPS 서프라이즈 --------------------------------------------------
    eps_est = eps_act = eps_surprise = None
    next_earnings_date = None
    try:
        edf = tk.get_earnings_dates(limit=12)
    except Exception:
        edf = None

    if edf is not None and not edf.empty:
        edf = edf.sort_index()
        idx_dates = [d.date() for d in edf.index]
        target = datetime.strptime(earnings_date, "%Y-%m-%d").date()

        # 다음 발표일 찾기
        future = [d for d in idx_dates if d > target]
        if future:
            next_earnings_date = future[0].strftime("%Y-%m-%d")

        # 현재 발표일의 EPS 데이터
        try:
            row = edf[[d.date() == target for d in edf.index]]
            if not row.empty:
                r = row.iloc[0]
                eps_est = _f(r.get("EPS Estimate"))
                eps_act = _f(r.get("Reported EPS"))
                sp = r.get("Surprise(%)")
                eps_surprise = _f(sp) if sp is not None else None
                if eps_surprise is None and eps_est not in (None, 0):
                    eps_surprise = (eps_act - eps_est) / abs(eps_est) if eps_est != 0 else None
        except Exception as e:
            logger.debug(f"[earnings] {ticker} 서프라이즈 파싱 스킵: {e}")

    if not earnings_date:
        logger.info(f"[earnings] {ticker} 실적일을 찾지 못함 → 스킵")
        return None

    # 발표 전후 가격 -------------------------------------------------------
    px_pre = px_post = px_next_pre = None
    try:
        ed = datetime.strptime(earnings_date, "%Y-%m-%d").date()
        start = (ed - timedelta(days=10)).strftime("%Y-%m-%d")
        end_d = next_earnings_date or (ed + timedelta(days=120)).strftime("%Y-%m-%d")
        hist = tk.history(start=start, end=(datetime.strptime(end_d, "%Y-%m-%d").date()
                                            + timedelta(days=3)).strftime("%Y-%m-%d"))
        if hist is not None and not hist.empty:
            hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index
            pre_day = (ed - timedelta(days=1)).strftime("%Y-%m-%d")
            post_day = (ed + timedelta(days=1)).strftime("%Y-%m-%d")
            px_pre = _close_on_or_before(hist, pre_day)
            px_post = _close_on_or_after(hist, post_day)
            if next_earnings_date:
                nxt_pre = (datetime.strptime(next_earnings_date, "%Y-%m-%d").date()
                           - timedelta(days=1)).strftime("%Y-%m-%d")
                px_next_pre = _close_on_or_before(hist, nxt_pre)
    except Exception as e:
        logger.warning(f"[earnings] {ticker} 가격 조회 실패: {e}")

    # 타깃 -----------------------------------------------------------------
    ret_event = _ratio((px_post - px_pre) if (px_pre and px_post) else None, px_pre)
    ret_hold = _ratio((px_next_pre - px_post) if (px_post and px_next_pre) else None, px_post)

    row = {
        "ticker": ticker,
        "gics_sector": sector or info.get("sector"),
        "earnings_date": earnings_date,
        "px_pre": px_pre,
        "px_post": px_post,
        "next_earnings_date": next_earnings_date,
        "px_next_pre": px_next_pre,
        "eps_est": eps_est,
        "eps_act": eps_act,
        "eps_surprise_pct": eps_surprise,
        "ret_event": ret_event,
        "ret_hold": ret_hold,
        **_build_features_from_info(info),
    }
    # None 컬럼 제거(부분 업데이트 깔끔하게)
    row = {k: v for k, v in row.items() if v is not None}
    row["ticker"] = ticker
    row["earnings_date"] = earnings_date

    saved = earnings_repo.upsert_event(row)
    logger.info(f"[earnings] {ticker} {earnings_date} 적재 "
                f"(ret_hold={'있음' if ret_hold is not None else '없음'})")
    return saved


def _collect_one_ticker(
    ticker: str,
    max_per_ticker: int,
    sector: Optional[str],
    rate_series: Optional[dict] = None,
) -> tuple[int, str]:
    """
    단일 종목 과거 실적 적재. (저장 개수, 상태) 반환.
      상태: ok | no_price | no_sec | no_eps
    데이터 소스 (모두 무인증):
      - SEC EDGAR companyfacts: EPS + 발표일(10-Q filed) + 재무제표
      - Yahoo Chart v8: px_pre/post/next_pre + 구간 고저(px_max/min)
      - ^TNX(rate_series): 발표일 금리(ust10y) + 구간 금리변동(ust10y_change)
    """
    from datetime import timezone as _tz

    rate_series = rate_series or {}

    # ── 1. 가격 데이터 (Chart API) — 종가 + 장중 고저 ────────
    chart_data = _fetch_yahoo_chart_sync(ticker, "2y")
    result_list = chart_data.get("chart", {}).get("result")
    if not result_list:
        logger.warning(f"[earnings] {ticker} 차트 데이터 없음")
        return 0, "no_price"

    res = result_list[0]
    timestamps = res.get("timestamp", [])
    quote = res.get("indicators", {}).get("quote", [{}])[0]
    closes_raw = quote.get("close", [])
    highs_raw = quote.get("high", [])
    lows_raw = quote.get("low", [])

    date_close: dict = {}
    date_high: dict = {}
    date_low: dict = {}
    for i, ts in enumerate(timestamps):
        if ts is None:
            continue
        d = datetime.fromtimestamp(int(ts), tz=_tz.utc).date()
        c = closes_raw[i] if i < len(closes_raw) else None
        h = highs_raw[i] if i < len(highs_raw) else None
        low = lows_raw[i] if i < len(lows_raw) else None
        if c is not None:
            date_close[d] = float(c)
        if h is not None:
            date_high[d] = float(h)
        if low is not None:
            date_low[d] = float(low)

    if not date_close:
        logger.warning(f"[earnings] {ticker} 가격 데이터 없음")
        return 0, "no_price"

    # ── 2. SEC companyfacts (EPS + 발표일 + 재무제표) ────────
    us_gaap = _fetch_sec_companyfacts(ticker)
    if not us_gaap:
        logger.warning(f"[earnings] {ticker} SEC companyfacts 없음")
        return 0, "no_sec"

    sec_events = _extract_eps_quarters(us_gaap, ticker, max_per_ticker)
    if not sec_events:
        logger.warning(f"[earnings] {ticker} EPS 분기 데이터 없음")
        return 0, "no_eps"

    # 발표일(filed) 오름차순 정렬 + 동일 발표일 중복 제거
    #   → next_earnings_date를 미래로 정확히 연결
    sec_events = sorted(sec_events, key=lambda e: e["filed"])
    _dedup: dict = {}
    for _ev in sec_events:
        _dedup[_ev["filed"]] = _ev   # 같은 발표일은 최신 분기말만 유지
    sec_events = sorted(_dedup.values(), key=lambda e: e["filed"])

    fin_by_period = _extract_financials(us_gaap)   # {end_str: {재무피처}}

    # ── 3. 이벤트별 저장 ──────────────────────────────────
    saved_count = 0
    for idx, ev in enumerate(sec_events):
        try:
            ann_date = ev["filed"]   # 10-Q 제출일 ≈ 발표일
            ed_str = ann_date.strftime("%Y-%m-%d")
            fiscal_end_str = ev["fiscal_end"].strftime("%Y-%m-%d")

            next_ed_str = None
            if idx + 1 < len(sec_events):
                next_ed_str = sec_events[idx + 1]["filed"].strftime("%Y-%m-%d")

            px_pre = _close_nearby(date_close, ann_date, direction=-1)
            px_post = _close_nearby(date_close, ann_date, direction=+1)
            px_next_pre = None
            next_ann_date = None
            if next_ed_str:
                next_ann_date = datetime.strptime(next_ed_str, "%Y-%m-%d").date()
                px_next_pre = _close_nearby(date_close, next_ann_date, direction=-1)

            eps_act = _f(ev.get("eps_act"))
            ret_event = _ratio(
                (px_post - px_pre) if px_pre and px_post else None, px_pre
            )
            ret_hold = _ratio(
                (px_next_pre - px_post) if px_post and px_next_pre else None, px_post
            )

            # ── 보유 구간(발표일~다음발표전날) 장중 고저 + MFE/MAE ──
            #   ※ px_max/min·ret_max_*·ust10y_change 는 '구간 종료까지의 결과'라
            #     예측 입력(피처)이 아니라 타깃/사후분석용 (미래 정보 — 학습 누수 주의)
            px_max = px_min = ret_max_up = ret_max_down = None
            if next_ann_date is not None:
                highs = [v for d, v in date_high.items() if ann_date <= d <= next_ann_date]
                lows = [v for d, v in date_low.items() if ann_date <= d <= next_ann_date]
                if highs:
                    px_max = max(highs)
                if lows:
                    px_min = min(lows)
                if px_max is not None and px_post:
                    ret_max_up = (px_max - px_post) / px_post      # MFE 최대 상승
                if px_min is not None and px_post:
                    ret_max_down = (px_min - px_post) / px_post    # MAE 최대 하락

            # ── 금리(10년물): 발표일 수준 + 보유 구간 변동(%p) ──
            ust10y = _series_asof(rate_series, ann_date)
            ust10y_change = None
            if ust10y is not None and next_ann_date is not None:
                ust10y_next = _series_asof(rate_series, next_ann_date)
                if ust10y_next is not None:
                    ust10y_change = ust10y_next - ust10y

            fin = fin_by_period.get(fiscal_end_str, {})   # 분기말 기준 재무 피처

            row = {k: v for k, v in {
                "ticker": ticker,
                "gics_sector": sector,
                "earnings_date": ed_str,
                "next_earnings_date": next_ed_str,
                "px_pre": px_pre,
                "px_post": px_post,
                "px_next_pre": px_next_pre,
                "px_max": px_max,
                "px_min": px_min,
                "ret_max_up": ret_max_up,
                "ret_max_down": ret_max_down,
                "eps_act": eps_act,
                "ret_event": ret_event,
                "ret_hold": ret_hold,
                "ust10y": ust10y,
                "ust10y_change": ust10y_change,
                **fin,
            }.items() if v is not None}
            row["ticker"] = ticker

            earnings_repo.upsert_event(row)
            saved_count += 1

        except Exception as e:
            logger.warning(f"[earnings] {ticker} 이벤트 처리 실패: {e}")

    logger.info(
        f"[earnings] ✅ {ticker}: {saved_count}/{len(sec_events)}개 저장 "
        f"(재무 {len(fin_by_period)}분기, 섹터={sector or '없음'})"
    )
    return saved_count, "ok"


def collect_history_iter(
    tickers: list[str],
    max_per_ticker: int = 8,
    sector_map: Optional[dict] = None,
):
    """
    과거 실적 적재 — 진행 상황을 종목마다 yield 하는 제너레이터 (SSE용).

    yield 형식:
      진행: {"current": i, "total": n, "ticker": t, "saved": k, "status": "ok"}
      완료: {"done": True, "collected": N, "processed_tickers": n,
             "total_inserted": N, "failed": [...]}
    """
    import time

    sector_map = sector_map or {}
    n = len(tickers)
    logger.info(f"[earnings] collect_history 시작: {n}개 종목 (SEC EDGAR + Yahoo Chart)")

    _get_sec_ticker_cik()          # CIK 매핑 사전 로드
    rate_series = _fetch_rate_series()   # ^TNX 10년물 금리 2년치 (1회)

    total = 0
    failed = []

    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(1)
        logger.info(f"[earnings] {ticker} 수집 ({i+1}/{n})")
        try:
            saved, status = _collect_one_ticker(
                ticker, max_per_ticker, sector_map.get(ticker), rate_series
            )
        except Exception as e:
            logger.error(f"[earnings] ❌ {ticker} 실패: {type(e).__name__} {e}", exc_info=True)
            saved, status = 0, "error"

        if status == "ok":
            total += saved
        else:
            failed.append(ticker)

        yield {
            "current": i + 1,
            "total": n,
            "ticker": ticker,
            "saved": saved,
            "status": status,
        }

    logger.info(f"[earnings] ✅ collect_history 완료: {total}개 수집, {len(failed)}개 실패")
    yield {
        "done": True,
        "collected": total,
        "processed_tickers": n,
        "total_inserted": total,
        "failed": failed,
    }


def collect_history(
    tickers: list[str],
    max_per_ticker: int = 8,
    sector_map: Optional[dict] = None,
) -> dict:
    """과거 실적 적재 (일괄). 진행 제너레이터를 소비해 최종 집계만 반환."""
    result = {
        "collected": 0,
        "processed_tickers": len(tickers),
        "total_inserted": 0,
        "failed": [],
    }
    for ev in collect_history_iter(tickers, max_per_ticker, sector_map):
        if ev.get("done"):
            result = {k: v for k, v in ev.items() if k != "done"}
    return result


# ─────────────────────────────────────────────
# 학습 (섹터별 XGBoost)
# ─────────────────────────────────────────────

def _to_matrix(events: list[dict]):
    import numpy as np
    X, y = [], []
    for e in events:
        if e.get("ret_hold") is None:
            continue
        X.append([_f(e.get(c)) if _f(e.get(c)) is not None else np.nan
                  for c in FEATURE_COLUMNS])
        y.append(float(e["ret_hold"]))
    return np.array(X, dtype="float32"), np.array(y, dtype="float32")


def train(min_samples: int = 30) -> dict:
    """섹터별로 라벨 완성 행을 모아 XGBoost 회귀 학습 → ml_models 저장."""
    import numpy as np
    import xgboost as xgb

    labeled = earnings_repo.list_labeled_events()
    by_sector: dict[str, list[dict]] = {}
    for e in labeled:
        sec = e.get("gics_sector") or "Unknown"
        by_sector.setdefault(sec, []).append(e)

    results = []
    for sector, rows in by_sector.items():
        X, y = _to_matrix(rows)
        if len(y) < min_samples:
            results.append({"sector": sector, "status": "skipped",
                            "reason": f"표본 부족({len(y)}<{min_samples})"})
            continue

        model = xgb.XGBRegressor(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
        )
        model.fit(X, y)
        pred = model.predict(X)
        rmse = float(np.sqrt(np.mean((pred - y) ** 2)))

        model_json = json.loads(model.get_booster().save_raw("json").decode("utf-8"))
        version = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        model_id = earnings_repo.save_earnings_model(
            sector, model_json,
            meta={
                "feature_count": len(FEATURE_COLUMNS),
                "sample_count": int(len(y)),
                "stage": 1,
                "rmse": rmse,
                "model_version": version,
            },
        )
        results.append({"sector": sector, "status": "trained", "model_id": model_id,
                        "version": version, "samples": int(len(y)), "rmse": round(rmse, 5)})
        logger.info(f"[earnings] 학습 완료 sector={sector} n={len(y)} rmse={rmse:.5f}")

    return {"sectors": results}


# ─────────────────────────────────────────────
# 예측 (라벨 미완성 행)
# ─────────────────────────────────────────────

_booster_cache: dict[str, object] = {}


def _load_booster(model_row: dict):
    import xgboost as xgb
    mid = model_row["id"]
    if mid in _booster_cache:
        return _booster_cache[mid]
    booster = xgb.Booster()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(model_row["model_json"], f)
        path = f.name
    booster.load_model(path)
    _booster_cache[mid] = booster
    return booster


def predict(scope: str = "missing_label") -> dict:
    """라벨 미완성(ret_hold IS NULL) 이벤트에 예측 주입."""
    import numpy as np
    import xgboost as xgb

    events = earnings_repo.list_unlabeled_events() if scope == "missing_label" \
        else earnings_repo.list_events(limit=500)

    model_by_sector: dict[str, Optional[dict]] = {}
    predicted, skipped = 0, 0

    for e in events:
        sector = e.get("gics_sector") or "Unknown"
        if sector not in model_by_sector:
            model_by_sector[sector] = earnings_repo.latest_earnings_model(sector)
        mrow = model_by_sector[sector]
        if not mrow:
            skipped += 1
            continue

        booster = _load_booster(mrow)
        x = np.array([[_f(e.get(c)) if _f(e.get(c)) is not None else np.nan
                       for c in FEATURE_COLUMNS]], dtype="float32")
        ret_hold_pred = float(booster.predict(xgb.DMatrix(x))[0])

        base = _f(e.get("px_post")) or _f(e.get("px_pre"))
        target_price = round(base * (1 + ret_hold_pred), 2) if base else None

        earnings_repo.upsert_prediction({
            "event_id": e["id"],
            "ticker": e["ticker"],
            "target_price": target_price,
            "ret_hold_pred": round(ret_hold_pred, 4),
            "model_id": mrow["id"],
            "model_version": mrow.get("model_version") or mrow["id"][:8],
        })
        predicted += 1

    return {"predicted": predicted, "skipped_no_model": skipped,
            "total_candidates": len(events)}


# ─────────────────────────────────────────────
# 대시보드 (현재가 결합 + 위치%)
# ─────────────────────────────────────────────

def _current_price(ticker: str) -> Optional[float]:
    import yfinance as yf
    try:
        fi = yf.Ticker(ticker).fast_info
        return _f(getattr(fi, "last_price", None) or fi.get("lastPrice"))
    except Exception:
        return None


def get_positions(limit: int = 100) -> list[dict]:
    """
    earnings_dashboard(시작가/예측가/경과%) + 실시간 현재가 → 가격 위치% 계산.
    가격 위치% = (현재가 - 시작가) / (예측가 - 시작가) × 100
    """
    rows = earnings_repo.list_dashboard(limit)
    out = []
    for r in rows:
        start = _f(r.get("start_price"))
        predict = _f(r.get("predict_price"))
        cur = _current_price(r["ticker"]) if r.get("ticker") else None
        pos_pct = None
        if start is not None and predict is not None and cur is not None and (predict - start) != 0:
            pos_pct = round((cur - start) / (predict - start) * 100, 1)
        out.append({
            **r,
            "current_price": cur,
            "price_position_pct": pos_pct,  # 가격 위치%
        })
    return out
