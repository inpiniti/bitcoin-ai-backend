
import sys
import json
import os
import logging
from datetime import datetime, timedelta


# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("run_market_cap")

# [DEBUG] 환경 정보 출력
logger.info(f"Python Executable: {sys.executable}")
logger.info(f"Python Path: {sys.path}")
logger.info(f"Current Working Directory: {os.getcwd()}")
try:
    import site
    logger.info(f"Site Packages: {site.getsitepackages()}")
except:
    pass

# 전역 변수 (Lazy Load)
np = None
pd = None
tf = None
joblib = None
StandardScaler = None

def get_dependencies():
    global np, pd, tf, joblib, StandardScaler
    if np is None:
        logger.info("Loading dependencies...")
        import numpy as _np
        import pandas as _pd
        import tensorflow as _tf
        import joblib as _joblib
        from sklearn.preprocessing import StandardScaler as _StandardScaler
        
        np = _np
        pd = _pd
        tf = _tf
        joblib = _joblib
        StandardScaler = _StandardScaler
        logger.info("Dependencies loaded.")
    return np, pd, tf, joblib, StandardScaler

# 파일 캐시 경로
MODEL_DIR = "models/market_cap"
MODEL_PATH = os.path.join(MODEL_DIR, "market_cap_model.keras")
SCALER_PATH = os.path.join(MODEL_DIR, "scaler.joblib")
FEATURES_PATH = os.path.join(MODEL_DIR, "features.json")
FILE_TTL_HOURS = 24



# 헬퍼 함수: 안전한 float 변환
def to_float(x):
    try:
        return float(x)
    except:
        return 0.0

def preprocess_df(df, target_col):
    # 1. 숫자 변환 및 로그 변환 (금융 데이터의 편차를 줄임)
    exclude_cols = ['name', 'description', 'logoid', 'error', target_col, 'sector']
    numeric_candidates = [c for c in df.columns if c not in exclude_cols]
    
    for col in numeric_candidates:
        if col in df.columns:
            # 0 이하의 값은 0으로 처리 후 로그 변환 (안전한 log1p)
            vals = df[col].apply(to_float)
            # 음수값이 있을 수 있는 성장률 등은 log 대신 원본 유지 고려 가능하나,
            # 일단 자산/매출 등 큰 값들은 log가 유리함
            if "growth" not in col and "margin" not in col and "ratio" not in col:
                df[col] = np.log1p(vals.clip(lower=0))
            else:
                df[col] = vals.fillna(0)
            
    # 2. Sector One-Hot Encoding
    if 'sector' in df.columns:
        dummies = pd.get_dummies(df['sector'], prefix='sect', dummy_na=True)
        df = pd.concat([df, dummies], axis=1)
        
    return df

def train_and_predict(job_id, ticker_or_list, raw_list):
    np, pd, tf, joblib, StandardScaler = get_dependencies()
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    logger.info(f"Creating DataFrame from {len(raw_list)} items...")
    df = pd.DataFrame(raw_list)
    target_col = 'market_cap_basic'
    df[target_col] = df[target_col].apply(to_float)
    
    # [데이터 클렌징] 시총 100만 달러 미만 제외 및 필수 지표 결측치 보정
    df = df[df[target_col] > 1e6].copy()
    
    # [섹터별 편향 보정] 섹터별 중앙값 대비 상대 가치 지표 생성
    if 'sector' in df.columns:
        df['revenue_clean'] = df['total_revenue'].apply(to_float).clip(lower=1e6)
        df['psr_val'] = df[target_col] / df['revenue_clean']
        sector_median_psr = df.groupby('sector')['psr_val'].transform('median')
        df['sector_rel_psr'] = df['psr_val'] / sector_median_psr.replace(0, np.nan)
        df['sector_rel_psr'] = df['sector_rel_psr'].fillna(1.0).clip(upper=50) # 극단적 이상치 방지
    
    # [부채 영향력 강화] 부채 비율 및 현금 대비 부채 지표 생성
    df['assets_clean'] = df['total_assets_fq'].apply(to_float).clip(lower=1e6)
    df['debt_ratio'] = df['total_debt_fq'].apply(to_float) / df['assets_clean']
    df['cash_val'] = df['cash_n_short_term_invest_fq'].apply(to_float)
    df['debt_val'] = df['total_debt_fq'].apply(to_float).clip(lower=1e6)
    df['cash_to_debt'] = df['cash_val'] / df['debt_val']
    
    df['debt_ratio'] = df['debt_ratio'].fillna(0).clip(upper=10)
    df['cash_to_debt'] = df['cash_to_debt'].fillna(10).clip(upper=100)

    # 2. 공통 전처리
    df = preprocess_df(df, target_col)
    
    # --- Feature Selection ---
    valuation_metrics = [
        'price_earnings_ttm', 'price_revenue_ttm', 'price_book_ratio', 
        'price_free_cash_flow_ttm', 'enterprise_value_ebitda_ttm', 'enterprise_value_fq',
        'price_earnings_growth_ttm', 'sector_rel_psr', 'debt_ratio', 'cash_to_debt', 'debt_to_assets'
    ]
    
    # 타겟 로그 변환
    df['log_target'] = np.log1p(df[target_col].values)
    
    exclude_cols = ['name', 'description', 'logoid', 'error', target_col, 'sector', 'log_target', 'psr_val', 'revenue_clean', 'assets_clean', 'cash_val', 'debt_val']
    feature_cols = [c for c in df.columns if c not in exclude_cols]

    # 상관관계 및 중요 지표 필터링
    corrs = df[feature_cols].corrwith(df['log_target']).abs().fillna(0)
    keep_cols = corrs[corrs > 0.05].index.tolist()
    feature_cols = list(set(keep_cols + [c for c in valuation_metrics if c in df.columns]))
    
    logger.info(f"Selected {len(feature_cols)} features for Batched Model")

    # --- Training ---
    # 모든 피처를 수치형으로 강제 변환 (NaN 처리 포함)
    for c in feature_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
        
    X = df[feature_cols].values.astype(float)
    y = df['log_target'].values.astype(float)
    
    # NaN 행 제거
    mask = ~np.any(np.isnan(X), axis=1)
    X_train, y_train = X[mask], y[mask]
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_train)
    
    from sklearn.ensemble import HistGradientBoostingRegressor
    model = HistGradientBoostingRegressor(
        max_iter=600, max_depth=8, learning_rate=0.04, 
        l2_regularization=2.0, random_state=42, early_stopping=True
    )
    model.fit(X_scaled, y_train)
    logger.info(f"Training Complete. R2: {model.score(X_scaled, y_train):.4f}")

    # --- Batch Inference ---
    target_tickers = ticker_or_list if isinstance(ticker_or_list, list) else [ticker_or_list]
    results = []
    
    # 유효한 행만 미리 인덱싱
    full_feature_df = df.reindex(columns=feature_cols, fill_value=0)
    
    for tk in target_tickers:
        row_mask = (df['name'] == tk) | (df['name'].str.endswith(':' + tk))
        if not row_mask.any():
            continue
            
        idx = df[row_mask].index[0]
        actual_mc = float(df.loc[idx, target_col])
        
        X_target = full_feature_df.loc[[idx]].values
        X_target_scaled = scaler.transform(X_target)
        
        pred_log = model.predict(X_target_scaled)[0]
        pred_log = np.clip(pred_log, 15, 36)
        inferred_mc = float(np.expm1(pred_log))
        
        results.append({
            "symbol": tk,
            "actual_market_cap": actual_mc,
            "inferred_market_cap": inferred_mc,
            "diff_percent": ((inferred_mc - actual_mc) / actual_mc) * 100
        })
        
    return results

def main():
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"error": "No input file provided"}))
            sys.exit(1)
            
        input_file = sys.argv[1]
        logger.info(f"Starting Process with input: {input_file}")
        
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        job_id = data.get('jobId')
        ticker = data.get('ticker')
        raw_list = data.get('rawData', [])
        
        result = train_and_predict(job_id, ticker, raw_list)
        
        # 마지막 줄에 JSON 출력
        print(json.dumps(result))
        
    except Exception as e:
        logger.error(f"Execution failed: {str(e)}")
        # import traceback
        # logger.error(traceback.format_exc())
        error_result = {"error": str(e)}
        print(json.dumps(error_result))
        sys.exit(1)

if __name__ == "__main__":
    main()
