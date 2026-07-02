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
#   ust10y_change: 보유 구간 금리변동 — 예측 시 시나리오(가정값)로 주입하는 '조건' 피처
# 전분기 대비 증감(QoQ Δ) — 재무 수익성 지표의 개선/악화 '모멘텀' 피처.
#   level(현재 수준)만으론 부족하므로 직전 분기 대비 변화량을 함께 학습한다.
#   (비율형 지표라 Δ는 %p 변화 = scale-free, 분모 0 폭발 없음)
QOQ_BASE = [
    "gross_margin", "net_margin", "roe", "roc",   # 수익성 비율 증감
    "sga_to_gross", "debt_to_ni",                 # 판관비·부채 비율 증감
    "retained_earnings", "cash_sti", "eps_act",   # 이익잉여·현금·EPS 증감
]  # ※ 매출·EBIT·FCF·capex·투하자본 증감은 원본 미저장 → 재수집 필요(별도)
QOQ_COLUMNS = [f"{b}_qoq" for b in QOQ_BASE]

FEATURE_COLUMNS = [
    "eps_surprise_pct", "rev_surprise_pct",
    "gross_margin", "net_margin", "sga_to_gross", "roe", "roc", "earnings_yield",
    "capex_to_ni", "debt_to_ni", "inventory_vs_sales", "eps_yoy",
    "per", "pbr", "ev_ebitda", "peg", "dividend_yield",
    "fed_funds", "ust10y", "ust10y_change", "cpi_yoy",
] + QOQ_COLUMNS   # ★ QoQ 증감은 뒤에 append (기존 인덱스 보존)

# 예측 타깃 (다중 출력) — 발표 직후(px_post) 대비 보유 구간 수익률
#   ret_hold     : 다음 발표 전날까지 종가 수익률
#   ret_max_up   : 구간 최대 상승폭 (MFE)
#   ret_max_down : 구간 최대 하락폭 (MAE, 음수)
TARGET_COLUMNS = ["ret_hold", "ret_max_up", "ret_max_down"]

# 예측 시 금리변동 시나리오 라벨 → ust10y_change 주입값(%p)
#   현실 범위(3개월 구간): 대략 ±0.1~0.6%p
RATE_SCENARIOS = {"up": 0.5, "down": -0.5, "flat": 0.0}


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


# DB DECIMAL 컬럼별 절대값 상한 (precision-scale 기반)
#   초과 시 저장하면 'numeric field overflow' → 이벤트 통째로 유실.
#   그런 극단 비율은 분모(순이익 등)가 0에 가까운 노이즈이므로 결측 처리한다.
_COL_MAX_ABS = {
    # DECIMAL(8,4) → |x| < 10^4
    "gross_margin": 9999.9999, "net_margin": 9999.9999, "sga_to_gross": 9999.9999,
    "roe": 9999.9999, "roc": 9999.9999, "earnings_yield": 9999.9999,
    "capex_to_ni": 9999.9999, "debt_to_ni": 9999.9999, "inventory_vs_sales": 9999.9999,
    "ust10y_change": 9999.9999,
    # DECIMAL(6,4) → |x| < 10^2
    "dividend_yield": 99.9999,
    # DECIMAL(10,4) → |x| < 10^6
    "eps_yoy": 999999.9999, "eps_surprise_pct": 999999.9999, "rev_surprise_pct": 999999.9999,
    "ret_event": 999999.9999, "ret_hold": 999999.9999,
    "ret_max_up": 999999.9999, "ret_max_down": 999999.9999,
    # DECIMAL(8,2) → |x| < 10^6
    "peg": 999999.99,
    # DECIMAL(10,2) → |x| < 10^8
    "per": 99999999.99, "pbr": 99999999.99, "ev_ebitda": 99999999.99,
}


def _sanitize_row(row: dict) -> dict:
    """DECIMAL 상한을 넘는 극단값은 결측 처리(제거) — overflow로 인한 이벤트 유실 방지."""
    for col, limit in _COL_MAX_ABS.items():
        v = row.get(col)
        if v is not None and abs(v) > limit:
            logger.debug(f"[earnings] {row.get('ticker')} {col}={v} 상한 초과 → 결측 처리")
            row.pop(col, None)
    return row


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

    data = _fetch_yahoo_chart_sync("^TNX", "10y")
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


def _fetch_yahoo_chart_sync(ticker: str, range_str: str = "2y", retries: int = 2) -> dict:
    """
    Yahoo Chart v8 API — Crumb 불필요, HuggingFace 정상 동작.
    일시적 타임아웃/오류 대비 재시도(기본 2회, 1.5초 간격).
    """
    import httpx
    import time as _t
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?range={range_str}&interval=1d"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=20, verify=False, headers=headers) as client:
                r = client.get(url)
            if r.status_code == 200:
                return r.json()
            logger.warning(f"[YahooChart] {ticker} HTTP {r.status_code} (시도 {attempt+1}/{retries+1})")
        except Exception as e:
            logger.warning(f"[YahooChart] {ticker} 조회 실패 (시도 {attempt+1}/{retries+1}): {e}")
        if attempt < retries:
            _t.sleep(1.5)
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


def _extract_eps_quarters(us_gaap: dict, ticker: str, max_quarters: int = 40) -> list[dict]:
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

    cutoff = _d.today() - timedelta(days=3800)   # 약 10년+여유 (주가 range="10y"와 정합)
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


def collect_event_sec(ticker: str, earnings_date: str, sector: Optional[str] = None) -> tuple[Optional[dict], str]:
    """
    특정 종목의 특정 발표일 데이터 수집 (SEC EDGAR + Yahoo Chart).
    과거 수집(`collect_history`)과 동일한 데이터 품질.

    반환: (저장된 이벤트 dict, 상태 메시지) — 실패 시 (None, "error reason")
    """
    from datetime import timezone as _tz

    try:
        target_date = datetime.strptime(earnings_date, "%Y-%m-%d").date()
    except ValueError:
        msg = f"잘못된 날짜: {earnings_date}"
        logger.warning(f"[earnings] {ticker} {msg}")
        return None, msg

    # ── 1. 가격 데이터 (Chart API) — 종가 + 장중 고저 ────────
    chart_data = _fetch_yahoo_chart_sync(ticker, "10y")
    result_list = chart_data.get("chart", {}).get("result")
    if not result_list:
        msg = "Yahoo 차트 데이터 없음 (종목이나 날짜 확인)"
        logger.warning(f"[earnings] {ticker} {msg}")
        return None, msg

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
        msg = "Yahoo 가격 데이터 없음 (종목 확인)"
        logger.warning(f"[earnings] {ticker} {msg}")
        return None, msg

    # ── 2. SEC companyfacts (EPS + 발표일 + 재무제표) ────────
    us_gaap = _fetch_sec_companyfacts(ticker)
    if not us_gaap:
        msg = "SEC EDGAR 데이터 없음 (발표 후 기다려주세요)"
        logger.warning(f"[earnings] {ticker} {msg}")
        return None, msg

    sec_events = _extract_eps_quarters(us_gaap, ticker, max_quarters=100)
    if not sec_events:
        msg = "SEC EPS 데이터 없음"
        logger.warning(f"[earnings] {ticker} {msg}")
        return None, msg

    # 정렬 + 중복 제거 (과거 수집과 동일)
    sec_events = sorted(sec_events, key=lambda e: e["filed"])
    _dedup: dict = {}
    for _ev in sec_events:
        _dedup[_ev["filed"]] = _ev
    sec_events = sorted(_dedup.values(), key=lambda e: e["filed"])

    # 목표 날짜의 이벤트 찾기
    target_event = None
    target_idx = None
    for idx, ev in enumerate(sec_events):
        if ev["filed"] == target_date:
            target_event = ev
            target_idx = idx
            break

    if target_event is None:
        msg = f"{earnings_date} SEC 데이터 없음 (발표 후 기다려주세요)"
        logger.warning(f"[earnings] {ticker} {msg}")
        return None, msg

    fin_by_period = _extract_financials(us_gaap)
    rate_series = _fetch_rate_series()

    # ── 3. 목표 이벤트만 처리 & 저장 ──────────────────────────
    try:
        ann_date = target_event["filed"]
        ed_str = ann_date.strftime("%Y-%m-%d")
        fiscal_end_str = target_event["fiscal_end"].strftime("%Y-%m-%d")

        next_ed_str = None
        if target_idx + 1 < len(sec_events):
            next_ed_str = sec_events[target_idx + 1]["filed"].strftime("%Y-%m-%d")

        px_pre = _close_nearby(date_close, ann_date, direction=-1)
        px_post = _close_nearby(date_close, ann_date, direction=+1)
        px_next_pre = None
        next_ann_date = None
        if next_ed_str:
            next_ann_date = datetime.strptime(next_ed_str, "%Y-%m-%d").date()
            px_next_pre = _close_nearby(date_close, next_ann_date, direction=-1)

        eps_act = _f(target_event.get("eps_act"))
        ret_event = _ratio(
            (px_post - px_pre) if px_pre and px_post else None, px_pre
        )
        ret_hold = _ratio(
            (px_next_pre - px_post) if px_post and px_next_pre else None, px_post
        )

        # 구간 고저 + MFE/MAE
        px_max = px_min = ret_max_up = ret_max_down = None
        if next_ann_date is not None:
            highs = [v for d, v in date_high.items() if ann_date <= d <= next_ann_date]
            lows = [v for d, v in date_low.items() if ann_date <= d <= next_ann_date]
            if highs:
                px_max = max(highs)
            if lows:
                px_min = min(lows)
            if px_max is not None and px_post:
                ret_max_up = (px_max - px_post) / px_post
            if px_min is not None and px_post:
                ret_max_down = (px_min - px_post) / px_post

        # 금리
        ust10y = _series_asof(rate_series, ann_date)
        ust10y_change = None
        if ust10y is not None and next_ann_date is not None:
            ust10y_next = _series_asof(rate_series, next_ann_date)
            if ust10y_next is not None:
                ust10y_change = ust10y_next - ust10y

        fin = fin_by_period.get(fiscal_end_str, {})

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
        row = _sanitize_row(row)

        result = earnings_repo.upsert_event(row)
        logger.info(f"[earnings] ✅ {ticker} {earnings_date} 수집 완료")
        return result, "ok"

    except Exception as e:
        msg = f"처리 중 오류: {e}"
        logger.warning(f"[earnings] {ticker} {earnings_date} {msg}")
        return None, msg


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
    chart_data = _fetch_yahoo_chart_sync(ticker, "10y")
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
            row = _sanitize_row(row)   # DECIMAL 상한 초과값 결측 처리 (overflow 방지)

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
    max_per_ticker: int = 40,
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
    max_per_ticker: int = 40,
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

def _attach_qoq(events: list[dict]) -> None:
    """
    ticker별 발표일 오름차순 정렬 후, 재무 수익성 지표의 '전분기 대비 증감(Δ)'을
    각 이벤트에 e['{base}_qoq'] 로 붙인다 (in-place). 첫 분기는 이전 없음 → None.
    학습·예측 모두 호출해 피처를 일관되게 구성한다. (events 리스트 순서는 보존)
    """
    from collections import defaultdict
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_ticker[e.get("ticker") or ""].append(e)
    for evs in by_ticker.values():
        evs.sort(key=lambda e: e.get("earnings_date") or "")
        prev = None
        for e in evs:
            for base in QOQ_BASE:
                cur = _f(e.get(base))
                pv = _f(prev.get(base)) if prev is not None else None
                e[f"{base}_qoq"] = (cur - pv) if (cur is not None and pv is not None) else None
            prev = e


def _feature_row(e: dict, cols: Optional[list] = None):
    """이벤트 → 피처 벡터 (cols 순서, 결측=nan). cols 미지정 시 전체 FEATURE_COLUMNS."""
    import numpy as np
    cols = cols or FEATURE_COLUMNS
    return [_f(e.get(c)) if _f(e.get(c)) is not None else np.nan
            for c in cols]


def _to_matrix(events: list[dict], target: str = "ret_hold", cols: Optional[list] = None):
    """해당 target 이 채워진 행만 (X, y, dates)로. dates 는 시계열 검증 분할용 발표일."""
    import numpy as np
    cols = cols or FEATURE_COLUMNS
    X, y, dates = [], [], []
    for e in events:
        v = _f(e.get(target))
        if v is None:
            continue
        X.append(_feature_row(e, cols))
        y.append(v)
        dates.append(e.get("earnings_date") or "")
    return np.array(X, dtype="float32"), np.array(y, dtype="float32"), dates


def select_features(labeled: list[dict], target: str = "ret_hold", max_features: int = 12) -> dict:
    """
    전진 선택법(forward greedy) 피처 선택.
    전 섹터 통합(pooled) 데이터로 target 을 시계열 검증(앞80%/뒤20%)하며,
    '추가했을 때 검증 RMSE 가 가장 줄어드는' 피처를 하나씩 채택. 개선 없으면 중단.
    → 노이즈 피처를 빼 과적합을 줄인 '공통 피처셋' 1개를 반환.

    반환: {"selected":[...], "curve":[{n,added,val_rmse}], "baseline_rmse":.., "best_rmse":..}
    """
    import numpy as np
    import xgboost as xgb

    rows = [e for e in labeled if _f(e.get(target)) is not None]
    if len(rows) < 200:
        return {"selected": list(FEATURE_COLUMNS), "curve": [], "baseline_rmse": None,
                "best_rmse": None, "note": "표본 부족 → 전체 피처 사용"}

    def _val_rmse(cols: list) -> float:
        X, y, dates = _to_matrix(rows, target, cols)
        order = np.argsort(np.array(dates))
        Xs, ys = X[order], y[order]
        split = int(len(y) * 0.8)
        # 선택 단계는 상대 비교용이라 트리 수를 낮춰 속도 확보(60)
        m = xgb.XGBRegressor(n_estimators=60, max_depth=4, learning_rate=0.05,
                             subsample=0.8, colsample_bytree=0.8, random_state=42)
        m.fit(Xs[:split], ys[:split])
        pred = m.predict(Xs[split:])
        return float(np.sqrt(np.mean((pred - ys[split:]) ** 2)))

    # 기준선: 평균 찍기(train 평균)의 검증 RMSE
    X0, y0, d0 = _to_matrix(rows, target, [FEATURE_COLUMNS[0]])
    order0 = np.argsort(np.array(d0))
    ys0 = y0[order0]
    split0 = int(len(ys0) * 0.8)
    base_rmse = float(np.sqrt(np.mean((np.full(len(ys0) - split0, float(ys0[:split0].mean())) - ys0[split0:]) ** 2)))

    selected: list = []
    candidates = list(FEATURE_COLUMNS)
    curve = []
    best_rmse = base_rmse
    while candidates and len(selected) < max_features:
        step_best, step_rmse = None, best_rmse
        for c in candidates:
            r = _val_rmse(selected + [c])
            if r < step_rmse:
                step_rmse, step_best = r, c
        if step_best is None:      # 더 이상 개선 없음 → 중단
            break
        selected.append(step_best)
        candidates.remove(step_best)
        curve.append({"n": len(selected), "added": step_best, "val_rmse": round(step_rmse, 5)})
        best_rmse = step_rmse

    if not selected:               # 어떤 피처도 평균보다 못하면 전체 사용(폴백)
        selected = list(FEATURE_COLUMNS)
    logger.info(f"[featsel] 선택 {len(selected)}개: {selected} (base={base_rmse:.5f} → best={best_rmse:.5f})")
    return {"selected": selected, "curve": curve,
            "baseline_rmse": round(base_rmse, 5), "best_rmse": round(best_rmse, 5)}


def train(min_samples: int = 30) -> dict:
    """
    섹터 × 타깃별로 라벨 완성 행을 모아 XGBoost 회귀 학습 → ml_models 저장.

    시계열 검증(발표일 앞 80% 학습 → 뒤 20% 검증)으로 '평균 찍기(no-skill)' 대비
    우위를 측정해 좋다/나쁘다(verdict)를 함께 반환한다. 저장 모델은 전체로 재학습.
    저장되는 rmse 는 훈련오차가 아니라 '검증 RMSE'(정직한 값).
    타깃: ret_hold(종가) / ret_max_up(최대상승) / ret_max_down(최대하락)
    """
    import numpy as np
    import xgboost as xgb

    def _rmse(pred, actual) -> float:
        return float(np.sqrt(np.mean((np.asarray(pred, dtype="float64") - np.asarray(actual, dtype="float64")) ** 2)))

    def _mk():
        return xgb.XGBRegressor(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
        )

    labeled = earnings_repo.list_labeled_events()
    _attach_qoq(labeled)                      # 전분기 대비 증감(QoQ) 피처 주입

    # ── 피처 선택(전 섹터 통합 ret_hold, 전진 선택) → 공통 피처셋 1개 ──
    fsel = select_features(labeled, target="ret_hold")
    cols = fsel["selected"]
    by_sector: dict[str, list[dict]] = {}
    for e in labeled:
        sec = e.get("gics_sector") or "Unknown"
        by_sector.setdefault(sec, []).append(e)

    results = []
    for sector, rows in by_sector.items():
        for target in TARGET_COLUMNS:
            X, y, dates = _to_matrix(rows, target, cols)
            n = len(y)
            if n < min_samples:
                results.append({"sector": sector, "target": target, "status": "skipped",
                                "reason": f"표본 부족({n}<{min_samples})"})
                continue

            # ── 시계열 분할: 발표일 오름차순 → 앞 80% 학습 / 뒤 20% 검증 ──
            order = np.argsort(np.array(dates))
            Xs, ys = X[order], y[order]
            val_n = int(round(n * 0.2))
            can_val = val_n >= 10 and (n - val_n) >= min_samples

            val_rmse = base_rmse = edge_pct = None
            verdict = "no_val"
            if can_val:
                split = n - val_n
                vm = _mk()
                vm.fit(Xs[:split], ys[:split])
                val_rmse = _rmse(vm.predict(Xs[split:]), ys[split:])
                # 평균 찍기(train 평균으로 전부 예측)의 검증 RMSE = no-skill 기준선
                base_rmse = _rmse(np.full(val_n, float(ys[:split].mean())), ys[split:])
                edge_pct = ((base_rmse - val_rmse) / base_rmse * 100.0) if base_rmse > 0 else 0.0
                if val_rmse < base_rmse * 0.98:
                    verdict = "signal"       # 평균보다 2%+ 나음 → 신호 있음
                elif val_rmse <= base_rmse * 1.02:
                    verdict = "random"       # 평균과 사실상 동일 → 주사위
                else:
                    verdict = "worse"        # 평균보다 나쁨 → 과적합

            # ── 저장 모델: 전체 데이터로 재학습(예측 품질 극대화) ──
            model = _mk()
            model.fit(X, y)
            train_rmse = _rmse(model.predict(X), y)
            report_rmse = val_rmse if val_rmse is not None else train_rmse

            model_json = json.loads(model.get_booster().save_raw("json").decode("utf-8"))
            version = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            model_id = earnings_repo.save_earnings_model(
                sector, model_json,
                meta={
                    "feature_count": len(cols),
                    "sample_count": int(n),
                    "stage": 1,
                    "rmse": round(report_rmse, 5),   # ★ 검증 RMSE (정직한 값)
                    "model_version": version,
                    "feature_list": cols,            # ★ 이 모델이 쓴 피처(예측 시 동일 순서 사용)
                },
                target=target,
            )
            results.append({
                "sector": sector, "target": target, "status": "trained",
                "model_id": model_id, "version": version, "samples": int(n),
                "val_rmse": round(val_rmse, 5) if val_rmse is not None else None,
                "base_rmse": round(base_rmse, 5) if base_rmse is not None else None,
                "edge_pct": round(edge_pct, 1) if edge_pct is not None else None,
                "verdict": verdict,
            })
            logger.info(f"[earnings] 학습 sector={sector} target={target} n={n} "
                        f"val_rmse={val_rmse} base={base_rmse} verdict={verdict}")

    # ── 전체 요약(좋다/나쁘다) ──
    trained = [r for r in results if r.get("status") == "trained"]
    validated = [r for r in trained if r.get("edge_pct") is not None]
    n_signal = sum(1 for r in validated if r["verdict"] == "signal")
    n_random = sum(1 for r in validated if r["verdict"] == "random")
    n_worse = sum(1 for r in validated if r["verdict"] == "worse")
    avg_edge = round(sum(r["edge_pct"] for r in validated) / len(validated), 1) if validated else None

    if not validated:
        overall = "검증 표본 부족 — 판정 불가"
    elif n_signal > (n_random + n_worse):
        overall = "쓸만함 — 다수 모델이 평균 찍기보다 나음"
    elif n_signal == 0:
        overall = "주사위 수준 — 재무 피처만으론 신호 거의 없음"
    else:
        overall = "미약 — 일부만 신호, 대부분 평균 수준"

    return {
        "sectors": results,
        "summary": {
            "trained": len(trained),
            "validated": len(validated),
            "signal": n_signal, "random": n_random, "worse": n_worse,
            "avg_edge_pct": avg_edge,
            "overall": overall,
        },
        "feature_selection": {
            "selected": cols,
            "count": len(cols),
            "curve": fsel.get("curve", []),
            "baseline_rmse": fsel.get("baseline_rmse"),
            "best_rmse": fsel.get("best_rmse"),
        },
    }


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


def predict(scope: str = "missing_label", rate_scenario: Optional[str] = None) -> dict:
    """
    라벨 미완성(ret_hold IS NULL) & 아직 예측값이 없는 이벤트에만 다중 타깃 예측 주입.
    (scope="missing_label" 시 해당 rate_scenario 로 이미 예측된 event 는 건너뜀)

    타깃별(ret_hold/ret_max_up/ret_max_down) 섹터 모델로 예측하고
    px_post 기준 예측가(종가/최대/최저)를 함께 저장.

    Args:
        rate_scenario: 금리변동 시나리오 ("up"|"down"|"flat" 또는 %p 숫자 문자열).
            주면 ust10y_change 를 그 값으로 덮어써 조건부 예측(what-if) 수행.
            None 이면 이벤트의 기존 ust10y_change 사용(최신 이벤트는 결측→nan).
    """
    import numpy as np
    import xgboost as xgb

    # 금리 시나리오 주입값 결정
    scenario_val: Optional[float] = None
    if rate_scenario is not None:
        if rate_scenario in RATE_SCENARIOS:
            scenario_val = RATE_SCENARIOS[rate_scenario]
        else:
            scenario_val = _f(rate_scenario)

    if scope == "missing_label":
        events = earnings_repo.list_unlabeled_events()
        # QoQ 피처: 라벨+미라벨 전체로 ticker별 시계열 컨텍스트를 만들어 증감 주입
        #   (미라벨 이벤트의 '직전 분기'는 대개 과거 라벨 이벤트라 컨텍스트가 필요)
        _attach_qoq(earnings_repo.list_labeled_events() + events)
        # 라벨 없음(예측 후보) + 같은 시나리오로 아직 예측되지 않은 행만 남긴다
        scenario_key = rate_scenario or "actual"
        done = earnings_repo.predicted_event_ids(scenario_key)
        if done:
            events = [e for e in events if e.get("id") not in done]
    else:
        events = earnings_repo.list_events(limit=500)
        _attach_qoq(events)

    # (sector, target) → model row 캐시
    model_cache: dict[tuple, Optional[dict]] = {}

    def _model(sector: str, target: str):
        key = (sector, target)
        if key not in model_cache:
            model_cache[key] = earnings_repo.latest_earnings_model(sector, target=target)
        return model_cache[key]

    predicted, skipped = 0, 0

    for e in events:
        sector = e.get("gics_sector") or "Unknown"

        # 예측가 기준선 = 시작가(px_pre, 발표 직전가). 화면 '시작가'와 일치시켜
        #   가격 위치% 계산의 기준선 불일치를 근본 해소한다. (px_pre 없으면 px_post 폴백)
        base = _f(e.get("px_pre")) or _f(e.get("px_post"))
        preds: dict = {}
        used_model = None
        for target in TARGET_COLUMNS:
            mrow = _model(sector, target)
            if not mrow:
                continue
            used_model = mrow
            # 모델이 학습에 쓴 피처(feature_list)로 동일 순서로 벡터 구성 (+ 금리 시나리오)
            cols = mrow.get("feature_list") or FEATURE_COLUMNS
            feat = _feature_row(e, cols)
            if scenario_val is not None and "ust10y_change" in cols:
                feat[cols.index("ust10y_change")] = scenario_val
            x = np.array([feat], dtype="float32")
            preds[target] = float(_load_booster(mrow).predict(xgb.DMatrix(x))[0])

        if not preds:
            skipped += 1
            continue

        ret_hold_pred = preds.get("ret_hold")
        ret_up_pred = preds.get("ret_max_up")
        ret_down_pred = preds.get("ret_max_down")

        def _px(r):
            return round(base * (1 + r), 2) if (base and r is not None) else None

        earnings_repo.upsert_prediction({
            "event_id": e["id"],
            "ticker": e["ticker"],
            "target_price": _px(ret_hold_pred),          # 종가 예측
            "price_max_pred": _px(ret_up_pred),          # 최대가 예측
            "price_min_pred": _px(ret_down_pred),        # 최저가 예측
            "ret_hold_pred": round(ret_hold_pred, 4) if ret_hold_pred is not None else None,
            "ret_max_up_pred": round(ret_up_pred, 4) if ret_up_pred is not None else None,
            "ret_max_down_pred": round(ret_down_pred, 4) if ret_down_pred is not None else None,
            "rate_scenario": rate_scenario or "actual",
            "model_id": used_model["id"],
            "model_version": used_model.get("model_version") or used_model["id"][:8],
        })
        predicted += 1

    return {"predicted": predicted, "skipped_no_model": skipped,
            "total_candidates": len(events), "rate_scenario": rate_scenario}


# ─────────────────────────────────────────────
# 대시보드 (현재가 결합 + 위치%)
# ─────────────────────────────────────────────

_price_cache: dict = {"data": {}, "ts": 0.0}


def fetch_current_prices(ttl: int = 60) -> dict:
    """
    TradingView 스캐너로 미국 주식 현재가를 '한 번에' 배치 조회 → {TICKER: close}.

    yfinance(종목별 호출 + crumb 차단) 대신 사용.
    시총 상위 4000종목(=S&P500 전부 포함)을 1회 요청으로 받고 TTL 캐시(기본 60초).
    실패 시 직전 캐시라도 반환.
    """
    import httpx
    import time

    global _price_cache
    now = time.time()
    if _price_cache["data"] and (now - _price_cache["ts"] < ttl):
        return _price_cache["data"]

    body = {
        "columns": ["name", "close"],
        "filter": [{"left": "is_primary", "operation": "equal", "right": True}],
        "range": [0, 4000],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "markets": ["america"],
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://www.tradingview.com",
        "Content-Type": "text/plain;charset=UTF-8",
    }
    try:
        with httpx.Client(timeout=20, verify=False, headers=headers) as client:
            r = client.post(
                "https://scanner.tradingview.com/america/scan",
                content=json.dumps(body),
            )
        if r.status_code != 200:
            logger.warning(f"[TradingView] 현재가 조회 HTTP {r.status_code}")
            return _price_cache["data"]
        out: dict = {}
        for row in r.json().get("data", []):
            d = row.get("d", [])
            if len(d) >= 2 and d[0] and d[1] is not None:
                out[str(d[0]).upper()] = float(d[1])
        _price_cache = {"data": out, "ts": now}
        logger.info(f"[TradingView] 현재가 {len(out)}종목 로드")
        return out
    except Exception as e:
        logger.warning(f"[TradingView] 현재가 조회 실패: {e}")
        return _price_cache["data"]


def get_positions(limit: int = 100) -> list[dict]:
    """
    earnings_dashboard(시작가/예측가/경과%) 반환.
    현재가·가격위치%는 라우터에서 TradingView 현재가와 결합한다.
    """
    return earnings_repo.list_dashboard(limit)
