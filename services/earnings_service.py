"""
실적발표 자동매매 — 수집 / 피처 / 학습 / 예측 서비스

설계: trends/실적발표_자동매매_시퀀스.md (§8 데이터셋, §11 운영)
의존: yfinance(주가·재무·실적), xgboost(섹터별 회귀), earnings_repo(저장)

MVP 범위
  - 수집: yfinance 의 실적일/EPS 서프라이즈 + 발표 전후 종가 + 재무비율(.info)
  - 학습: 섹터별 XGBoost 회귀 (타깃 = ret_hold), ml_models 재사용
  - 예측: 라벨 미완성 행에 ret_hold_pred / target_price 주입

주의: 프레스 릴리즈 실시간 LLM 파싱(§2)은 본 MVP에 미포함.
      today/collect 는 yfinance 기준으로 발표 전후 가격·피처를 적재한다.
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
                  sector: Optional[str] = None) -> Optional[dict]:
    """
    단일 (ticker, earnings_date) 이벤트를 yfinance 로 구성해 upsert.
    earnings_date 미지정 시 가장 최근 과거 실적일을 사용.
    """
    import time
    import yfinance as yf

    # rate limit 회피
    time.sleep(0.3)

    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception as e:
        logger.warning(f"[earnings] {ticker} info 조회 실패: {e}")
        info = {}

    # 실적일 + EPS 서프라이즈 ----------------------------------------------
    eps_est = eps_act = eps_surprise = None
    next_earnings_date = None
    try:
        edf = tk.get_earnings_dates(limit=12)
    except Exception:
        edf = None

    if edf is not None and not edf.empty:
        edf = edf.sort_index()
        idx_dates = [d.date() for d in edf.index]
        now = datetime.utcnow().date()

        if earnings_date:
            target = datetime.strptime(earnings_date, "%Y-%m-%d").date()
        else:
            past = [d for d in idx_dates if d <= now]
            target = past[-1] if past else idx_dates[-1]

        earnings_date = target.strftime("%Y-%m-%d")
        # 다음 발표일
        future = [d for d in idx_dates if d > target]
        if future:
            next_earnings_date = future[0].strftime("%Y-%m-%d")

        try:
            row = edf[[d.date() == target for d in edf.index]]
            if not row.empty:
                r = row.iloc[0]
                eps_est = _f(r.get("EPS Estimate"))
                eps_act = _f(r.get("Reported EPS"))
                sp = r.get("Surprise(%)")
                eps_surprise = _f(sp) if sp is not None else None
                if eps_surprise is None and eps_est not in (None, 0):
                    eps_surprise = (eps_act - eps_est) / abs(eps_est)
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
    """여러 종목의 과거 실적 이벤트를 적재(초기 1회). 종목별 최근 max_per_ticker 분기."""
    import time
    import yfinance as yf

    total = 0
    failed = []
    for i, t in enumerate(tickers):
        try:
            # yfinance rate limit 회피: 각 호출 사이 0.5초 대기
            if i > 0:
                time.sleep(0.5)

            tk = yf.Ticker(t)
            sector = (tk.info or {}).get("sector")
            edf = tk.get_earnings_dates(limit=max_per_ticker)
            dates = [d.date().strftime("%Y-%m-%d") for d in edf.index] if edf is not None and not edf.empty else [None]
            for d in dates:
                if collect_event(t, earnings_date=d, sector=sector):
                    total += 1
        except Exception as e:
            logger.warning(f"[earnings] history {t} 실패: {e}")
            failed.append(t)
    return {"collected": total, "tickers": len(tickers), "failed": failed}


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
