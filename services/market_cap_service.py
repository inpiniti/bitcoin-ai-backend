"""
시총 유추 서비스
TradingView 크롤링 (fetch-market-data.step.ts 포팅) +
ML 학습/추론 (scripts/run_market_cap.py 통합)
"""
import logging
import httpx

logger = logging.getLogger("market_cap_service")

# ── TradingView 크롤링 ────────────────────────────────────

_TV_COLUMNS = [
    "name", "description", "logoid", "market_cap_basic", "sector",
    "gross_margin_ttm", "operating_margin_ttm", "pre_tax_margin_ttm",
    "net_margin_ttm", "free_cash_flow_margin_ttm",
    "return_on_assets_fq", "return_on_equity_fq", "return_on_invested_capital_fq",
    "research_and_dev_ratio_ttm", "sell_gen_admin_exp_other_ratio_ttm",
    "total_revenue", "total_revenue_yoy_growth_ttm",
    "earnings_per_share_diluted_ttm", "earnings_per_share_diluted_yoy_growth_ttm",
    "total_assets_fq", "total_current_assets_fq", "cash_n_short_term_invest_fq",
    "total_liabilities_fq", "total_debt_fq", "net_debt_fq", "total_equity_fq",
    "current_ratio_fq", "quick_ratio_fq",
    "debt_to_equity_fq", "cash_n_short_term_invest_to_total_debt_fq",
    "cash_f_operating_activities_ttm", "cash_f_investing_activities_ttm",
    "cash_f_financing_activities_ttm", "free_cash_flow_ttm", "capital_expenditures_ttm",
    "price_earnings_ttm", "price_revenue_ttm", "price_book_ratio",
    "price_free_cash_flow_ttm", "enterprise_value_ebitda_ttm", "enterprise_value_fq",
    "dividend_yield_recent", "dividend_payout_ratio_ttm",
    "beta_1_year", "price_earnings_growth_ttm",
    "debt_to_assets", "book_value_per_share_fq", "cash_per_share_fq",
]


async def crawl_tradingview() -> list[dict]:
    """TradingView Scanner에서 미국주식 데이터를 가져옵니다."""
    payload = {
        "columns": _TV_COLUMNS,
        "ignore_unknown_fields": False,
        "options": {"lang": "en"},
        "range": [0, 9999],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "markets": ["america"],
        "filter": [
            {"left": "exchange",   "operation": "in_range", "right": ["NASDAQ", "NYSE"]},
            {"left": "type",       "operation": "equal",    "right": "stock"},
            {"left": "typespecs",  "operation": "has",      "right": ["common"]},
        ],
    }

    async with httpx.AsyncClient(timeout=60, verify=False) as client:
        resp = await client.post(
            "https://scanner.tradingview.com/america/scan",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    if resp.status_code != 200:
        raise ValueError(f"TradingView Scan 에러: {resp.status_code}")

    data = resp.json()
    result = []
    for item in data.get("data", []):
        obj = {}
        for i, col in enumerate(_TV_COLUMNS):
            obj[col] = item["d"][i]
        result.append(obj)

    logger.info(f"[MarketCap] TradingView: {len(result)}개 항목 수집")
    return result


# ── ML 학습 및 추론 ──────────────────────────────────────

def _to_float(x):
    try:
        return float(x)
    except Exception:
        return 0.0


def _preprocess_df(df, target_col, np, pd):
    exclude = {"name", "description", "logoid", "error", target_col, "sector"}
    for col in df.columns:
        if col in exclude:
            continue
        vals = df[col].apply(_to_float)
        if not any(kw in col for kw in ("growth", "margin", "ratio")):
            df[col] = np.log1p(vals.clip(lower=0))
        else:
            df[col] = vals.fillna(0)

    if "sector" in df.columns:
        dummies = pd.get_dummies(df["sector"], prefix="sect", dummy_na=True)
        df = pd.concat([df, dummies], axis=1)

    return df


def _train_and_infer(ticker: str, raw_list: list) -> dict:
    import numpy as np
    import pandas as pd
    from sklearn.preprocessing import StandardScaler
    from sklearn.ensemble import HistGradientBoostingRegressor

    target_col = "market_cap_basic"

    df = pd.DataFrame(raw_list)
    df[target_col] = df[target_col].apply(_to_float)
    df = df[df[target_col] > 1e6].copy()

    # 섹터별 PSR 상대 지표
    if "sector" in df.columns:
        df["revenue_clean"] = df["total_revenue"].apply(_to_float).clip(lower=1e6)
        df["psr_val"] = df[target_col] / df["revenue_clean"]
        sector_median = df.groupby("sector")["psr_val"].transform("median")
        df["sector_rel_psr"] = (df["psr_val"] / sector_median.replace(0, float("nan"))).fillna(1.0).clip(upper=50)

    # 부채 지표
    df["assets_clean"]   = df["total_assets_fq"].apply(_to_float).clip(lower=1e6)
    df["debt_ratio"]     = (df["total_debt_fq"].apply(_to_float) / df["assets_clean"]).fillna(0).clip(upper=10)
    df["cash_val"]       = df["cash_n_short_term_invest_fq"].apply(_to_float)
    df["debt_val"]       = df["total_debt_fq"].apply(_to_float).clip(lower=1e6)
    df["cash_to_debt"]   = (df["cash_val"] / df["debt_val"]).fillna(10).clip(upper=100)

    df = _preprocess_df(df, target_col, np, pd)

    valuation_extras = [
        "price_earnings_ttm", "price_revenue_ttm", "price_book_ratio",
        "price_free_cash_flow_ttm", "enterprise_value_ebitda_ttm", "enterprise_value_fq",
        "price_earnings_growth_ttm", "sector_rel_psr", "debt_ratio", "cash_to_debt", "debt_to_assets",
    ]

    df["log_target"] = np.log1p(df[target_col].values)
    exclude_cols = {"name", "description", "logoid", "error", target_col, "sector", "log_target",
                    "psr_val", "revenue_clean", "assets_clean", "cash_val", "debt_val"}
    feature_cols = [c for c in df.columns if c not in exclude_cols]

    corrs = df[feature_cols].corrwith(df["log_target"]).abs().fillna(0)
    keep  = corrs[corrs > 0.05].index.tolist()
    feature_cols = sorted(set(keep + [c for c in valuation_extras if c in df.columns]))

    for c in feature_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    X = df[feature_cols].values.astype(float)
    y = df["log_target"].values.astype(float)

    mask = ~np.any(np.isnan(X), axis=1)
    X_train, y_train = X[mask], y[mask]

    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(X_train)

    model = HistGradientBoostingRegressor(
        max_iter=600, max_depth=8, learning_rate=0.04,
        l2_regularization=2.0, random_state=42, early_stopping=True,
    )
    model.fit(X_scaled, y_train)

    # 타깃 추론
    full_df = df.reindex(columns=feature_cols, fill_value=0)
    row_mask = (df["name"] == ticker) | (df["name"].str.endswith(":" + ticker))
    if not row_mask.any():
        raise ValueError(f"Ticker {ticker} 를 데이터에서 찾을 수 없습니다")

    idx       = df[row_mask].index[0]
    actual_mc = float(df.loc[idx, target_col])

    X_t         = full_df.loc[[idx]].values
    X_t_scaled  = scaler.transform(X_t)
    pred_log    = float(np.clip(model.predict(X_t_scaled)[0], 15, 36))
    inferred_mc = float(np.expm1(pred_log))

    return {
        "symbol":              ticker,
        "actual_market_cap":   actual_mc,
        "inferred_market_cap": inferred_mc,
        "diff_percent":        ((inferred_mc - actual_mc) / actual_mc) * 100,
    }


async def run_market_cap(ticker: str) -> dict:
    """TradingView 크롤링 후 시총 유추 결과를 반환합니다."""
    raw_data = await crawl_tradingview()

    # 대상 종목 확인
    match = next(
        (r for r in raw_data if r.get("name") == ticker or str(r.get("name", "")).endswith(":" + ticker)),
        None,
    )
    if not match:
        raise ValueError(f"Ticker {ticker} not found in TradingView data")

    # 상위 3000개 + 대상 종목 보장
    optimized = raw_data[:3000]
    if match not in optimized:
        optimized.append(match)

    logger.info(f"[MarketCap] {ticker} 학습/추론 시작 ({len(optimized)}개 샘플)")
    result = _train_and_infer(ticker, optimized)
    logger.info(f"[MarketCap] {ticker}: actual={result['actual_market_cap']:.0f}, inferred={result['inferred_market_cap']:.0f}")
    return result
