
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
    # 1. 숫자 변환
    exclude_cols = ['name', 'description', 'logoid', 'error', target_col, 'sector']
    numeric_candidates = [c for c in df.columns if c not in exclude_cols]
    
    for col in numeric_candidates:
        if col in df.columns:
            df[col] = df[col].apply(to_float).fillna(0)
            
    # 2. Sector One-Hot Encoding
    if 'sector' in df.columns:
        # One-Hot Encoding
        dummies = pd.get_dummies(df['sector'], prefix='sect', dummy_na=True)
        # 기존 df와 병합 (axis=1)
        df = pd.concat([df, dummies], axis=1)
        
    return df

def train_and_predict(job_id, ticker, raw_list):
    np, pd, tf, joblib, StandardScaler = get_dependencies()
    
    # 디렉토리 생성
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    logger.info(f"Creating DataFrame from {len(raw_list)} items...")
    df = pd.DataFrame(raw_list)
    target_col = 'market_cap_basic'
    
    # 전처리 1: Target 및 기본 정제
    df[target_col] = df[target_col].apply(to_float)
    df = df[df[target_col] > 0].copy()
    
    # 전처리 2: 공통 전처리 (숫자 변환 및 One-Hot)
    # 학습이든 추론이든 무조건 수행
    df = preprocess_df(df, target_col)
    
    # Target Row 찾기
    target_row = df[df['name'] == ticker]
    if target_row.empty:
         target_row = df[df['name'].str.contains(rf'\b{ticker}\b', case=False, regex=True)]
    if target_row.empty:
        target_row = df[df['name'].str.endswith(':' + ticker)]

    if target_row.empty:
        raise ValueError(f"Ticker {ticker} not found in dataset")
        
    target_idx = target_row.index[0]
    actual_market_cap = float(df.loc[target_idx, target_col])
    target_name = df.loc[target_idx, 'name']
    
    logger.info(f"Target identified: {target_name} ({actual_market_cap})")

    # --- Cache Check ---
    should_train = True
    model = None
    scaler = None
    feature_cols = None
    
    if os.path.exists(MODEL_PATH) and os.path.exists(FEATURES_PATH):
        file_time = os.path.getmtime(MODEL_PATH)
        age = datetime.now() - datetime.fromtimestamp(file_time)
        if age < timedelta(hours=FILE_TTL_HOURS):
            try:
                logger.info(f"Loading cached model (Age: {age})...")
                model = tf.keras.models.load_model(MODEL_PATH)
                scaler = joblib.load(SCALER_PATH)
                with open(FEATURES_PATH, 'r') as f:
                    feature_cols = json.load(f)
                should_train = False
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")
                should_train = True

    # --- Training ---
    exclude_cols_base = ['name', 'description', 'logoid', 'error', target_col, 'sector']
    
    if should_train:
        logger.info("Training new model...")
        
        # Feature Selection
        feature_cols = [c for c in df.columns if c not in exclude_cols_base]
        
        # Save features used
        with open(FEATURES_PATH, 'w') as f:
             json.dump(feature_cols, f)
        
        X = df[feature_cols].values
        y = np.log1p(df[target_col].values)
        
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        input_dim = X_scaled.shape[1]
        
        model = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(input_dim,)), # Input Layer 명시 (Warning 해결)
            tf.keras.layers.Dense(64, activation='relu'),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(32, activation='relu'),
            tf.keras.layers.Dense(1)
        ])
        model.compile(optimizer='adam', loss='mse')
        
        logger.info(f"Fitting model on {len(X)} samples... (Features: {len(feature_cols)})")
        history = model.fit(X_scaled, y, epochs=10, batch_size=32, verbose=0, validation_split=0.1)
        final_loss = history.history['loss'][-1]
        
        try:
            model.save(MODEL_PATH)
            joblib.dump(scaler, SCALER_PATH)
            logger.info("Model cached to disk.")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    else:
        final_loss = 0.0

    # --- Inference ---
    # DataFrame을 feature_cols 순서와 구성에 맞게 재정렬 (부족하면 0, 넘치면 제거)
    # reindex가 핵심!
    X_final = df.reindex(columns=feature_cols, fill_value=0)
    
    # Target Row 추출 (reindex 된 상태에서)
    X_target_vals = X_final.loc[[target_idx]].values
    
    # Scaler Transform
    X_target_scaled = scaler.transform(X_target_vals)
    
    pred_log = model.predict(X_target_scaled, verbose=0)[0][0]
    inferred_market_cap = float(np.expm1(pred_log))
    
    logger.info(f"Inference complete: {inferred_market_cap}")
    
    result = {
        "symbol": ticker,
        "actual_market_cap": actual_market_cap,
        "inferred_market_cap": inferred_market_cap,
        "diff_value": inferred_market_cap - actual_market_cap,
        "diff_percent": ((inferred_market_cap - actual_market_cap) / actual_market_cap) * 100,
        "model_loss": float(final_loss),
        "cached": not should_train,
        "cache_source": "file" if not should_train else "trained"
    }
    return result

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
