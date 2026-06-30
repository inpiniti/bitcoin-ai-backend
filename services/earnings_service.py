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


def _fetch_earnings_qs_sync(ticker: str, max_quarters: int = 8) -> list[dict]:
    """
    Yahoo quoteSummary earningsHistory — Crumb 없이 직접 요청 시도.
    반환: [{"fiscal_end": date, "eps_est": float|None, "eps_act": float|None}, ...]
    fiscal_end = 분기말 날짜 (실발표일이 아님, _find_announcement_date로 추정)
    """
    import httpx
    from datetime import timezone as _tz

    url = (
        f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
        f"?modules=earningsHistory"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with httpx.Client(timeout=15, verify=False, headers=headers) as client:
            r = client.get(url)
        logger.info(f"[YahooQS] {ticker} earningsHistory HTTP {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            qs = data.get("quoteSummary", {})
            if qs.get("error"):
                logger.warning(f"[YahooQS] {ticker} 오류: {qs['error']}")
                return []
            hist = (
                (qs.get("result") or [{}])[0]
                .get("earningsHistory", {})
                .get("history", [])
            )
            out = []
            for h in hist[-max_quarters:]:
                ts = (h.get("quarter") or {}).get("raw")
                if not ts:
                    continue
                fiscal_end = datetime.fromtimestamp(int(ts), tz=_tz.utc).date()
                out.append({
                    "fiscal_end": fiscal_end,
                    "eps_est": (h.get("epsEstimate") or {}).get("raw"),
                    "eps_act": (h.get("epsActual") or {}).get("raw"),
                })
            logger.info(f"[YahooQS] {ticker} {len(out)}개 분기 수신")
            return out
        logger.warning(f"[YahooQS] {ticker} HTTP {r.status_code} — Crumb 필요 가능성")
    except Exception as e:
        logger.warning(f"[YahooQS] {ticker} 실패: {e}")
    return []


def _find_announcement_date(date_close: dict, fiscal_end) -> object:
    """
    분기말 이후 5~60일 내 가장 큰 일일 등락이 있는 날을 발표일로 추정.
    실적 발표는 통상 분기 마감 후 2~8주 사이에 발생.
    """
    window_start = fiscal_end + timedelta(days=5)
    window_end = fiscal_end + timedelta(days=60)

    max_move = 0.0
    best_date = fiscal_end + timedelta(days=30)  # 기본값

    # 탐색 직전 마지막 종가
    prev_close = None
    for d in sorted(date_close.keys()):
        if d < window_start:
            prev_close = date_close[d]
        else:
            break

    for d in sorted(d for d in date_close if window_start <= d <= window_end):
        c = date_close.get(d)
        if prev_close and prev_close > 0 and c:
            move = abs(c / prev_close - 1)
            if move > max_move:
                max_move = move
                best_date = d
        prev_close = c

    return best_date


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


def collect_history(tickers: list[str], max_per_ticker: int = 8) -> dict:
    """
    과거 실적 이벤트 적재.

    전략:
      1) 가격 데이터: Yahoo Chart v8 API (Crumb 불필요 — HuggingFace 확인됨)
      2) EPS 데이터:  Yahoo quoteSummary earningsHistory (Crumb 없이 시도)
      3) 발표일 추정: 분기말 이후 5~60일 내 최대 일일 등락 날짜
    """
    import time
    from datetime import timezone as _tz

    logger.info(f"[earnings] collect_history 시작: {len(tickers)}개 종목")

    total = 0
    failed = []

    for i, ticker in enumerate(tickers):
        if i > 0:
            time.sleep(1)

        logger.info(f"[earnings] {ticker} 수집 ({i+1}/{len(tickers)})")
        try:
            # ── 1. 가격 데이터 (Chart API) ──────────────────────────
            chart_data = _fetch_yahoo_chart_sync(ticker, "2y")
            result_list = chart_data.get("chart", {}).get("result")
            if not result_list:
                logger.warning(f"[earnings] {ticker} 차트 데이터 없음")
                failed.append(ticker)
                continue

            res = result_list[0]
            timestamps = res.get("timestamp", [])
            closes_raw = (res.get("indicators", {})
                          .get("quote", [{}])[0]
                          .get("close", []))
            date_close: dict = {}
            for ts, c in zip(timestamps, closes_raw):
                if ts is not None and c is not None:
                    date_close[datetime.fromtimestamp(int(ts), tz=_tz.utc).date()] = float(c)

            if not date_close:
                logger.warning(f"[earnings] {ticker} 가격 데이터 없음")
                failed.append(ticker)
                continue

            # ── 2. EPS 데이터 (quoteSummary — Crumb 없이 시도) ──────
            qs_events = _fetch_earnings_qs_sync(ticker, max_per_ticker)
            if not qs_events:
                logger.warning(f"[earnings] {ticker} EPS 데이터 없음 (quoteSummary 차단 또는 빈값)")
                failed.append(ticker)
                continue

            # ── 3. 이벤트별 저장 ──────────────────────────────────
            saved_count = 0
            for idx, ev in enumerate(qs_events):
                try:
                    fiscal_end = ev["fiscal_end"]  # date

                    # 발표일 추정: 분기말 이후 가장 큰 일일 등락
                    ann_date = _find_announcement_date(date_close, fiscal_end)
                    ed_str = ann_date.strftime("%Y-%m-%d")

                    # 다음 분기 발표일 추정
                    next_ed_str = None
                    if idx + 1 < len(qs_events):
                        next_ann = _find_announcement_date(
                            date_close, qs_events[idx + 1]["fiscal_end"]
                        )
                        next_ed_str = next_ann.strftime("%Y-%m-%d")

                    px_pre = _close_nearby(date_close, ann_date, direction=-1)
                    px_post = _close_nearby(date_close, ann_date, direction=+1)
                    px_next_pre = None
                    if next_ed_str:
                        next_ann_date = datetime.strptime(next_ed_str, "%Y-%m-%d").date()
                        px_next_pre = _close_nearby(date_close, next_ann_date, direction=-1)

                    eps_est = _f(ev.get("eps_est"))
                    eps_act = _f(ev.get("eps_act"))
                    eps_surprise = None
                    if eps_est and eps_act and eps_est != 0:
                        eps_surprise = (eps_act - eps_est) / abs(eps_est)

                    ret_event = _ratio(
                        (px_post - px_pre) if px_pre and px_post else None, px_pre
                    )
                    ret_hold = _ratio(
                        (px_next_pre - px_post) if px_post and px_next_pre else None, px_post
                    )

                    row = {k: v for k, v in {
                        "ticker": ticker,
                        "earnings_date": ed_str,
                        "next_earnings_date": next_ed_str,
                        "px_pre": px_pre,
                        "px_post": px_post,
                        "px_next_pre": px_next_pre,
                        "eps_est": eps_est,
                        "eps_act": eps_act,
                        "eps_surprise_pct": eps_surprise,
                        "ret_event": ret_event,
                        "ret_hold": ret_hold,
                    }.items() if v is not None}
                    row["ticker"] = ticker

                    earnings_repo.upsert_event(row)
                    saved_count += 1
                    total += 1

                except Exception as e:
                    logger.warning(f"[earnings] {ticker} 이벤트 처리 실패: {e}")

            logger.info(f"[earnings] ✅ {ticker}: {saved_count}/{len(qs_events)}개 저장")

        except Exception as e:
            logger.error(f"[earnings] ❌ {ticker} 실패: {type(e).__name__} {e}", exc_info=True)
            failed.append(ticker)

    logger.info(f"[earnings] ✅ collect_history 완료: {total}개 수집, {len(failed)}개 실패")
    return {
        "collected": total,
        "processed_tickers": len(tickers),
        "total_inserted": total,
        "failed": failed,
    }


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
