
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

def to_float(x):
    try:
        return float(x)
    except:
        return 0.0

def train_and_predict(job_id, ticker, raw_list):
    np, pd, tf, joblib, StandardScaler = get_dependencies()
    
    # 디렉토리 생성
    os.makedirs(MODEL_DIR, exist_ok=True)
    
    logger.info(f"Creating DataFrame from {len(raw_list)} items...")
    df = pd.DataFrame(raw_list)
    
    target_col = 'market_cap_basic'
    
    # 전처리: Target Column
    df[target_col] = df[target_col].apply(to_float)
    df = df[df[target_col] > 0].copy() # 시총 0인 것 제외
    
    # Target 찾기
    # 1. 완벽 일치  2. 포함 (Name)
    target_row = df[df['name'] == ticker]
    if target_row.empty:
         target_row = df[df['name'].str.contains(rf'\b{ticker}\b', case=False, regex=True)]
    
    if target_row.empty:
        # 마지막 시도로 :ticker 패턴 확인
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
    if should_train:
        logger.info("Training new model...")
        
        exclude_cols = ['name', 'description', 'logoid', 'error', target_col]
        # 숫자 컬럼 변환
        numeric_candidates = [c for c in df.columns if c not in exclude_cols and c != 'sector']
        for col in numeric_candidates:
            df[col] = df[col].apply(to_float).fillna(0)
            
        # Sector One-Hot Encoding
        if 'sector' in df.columns:
            df = pd.get_dummies(df, columns=['sector'], prefix='sect', dummy_na=True)
            
        feature_cols = [c for c in df.columns if c not in exclude_cols]
        
        X = df[feature_cols].values
        # Target Log 변환 (시총은 스케일이 매우 크고 편향되어 있음)
        y = np.log1p(df[target_col].values)
        
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        input_dim = X_scaled.shape[1]
        
        # 모델 구조 (간단하게)
        model = tf.keras.Sequential([
            tf.keras.layers.Dense(64, activation='relu', input_shape=(input_dim,)),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(32, activation='relu'),
            tf.keras.layers.Dense(1) # Log market cap
        ])
        model.compile(optimizer='adam', loss='mse')
        
        # 학습 (Epoch 5회로 제한하여 속도 확보)
        logger.info(f"Fitting model on {len(X)} samples...")
        history = model.fit(X_scaled, y, epochs=10, batch_size=32, verbose=0, validation_split=0.1)
        final_loss = history.history['loss'][-1]
        
        # 캐시 저장
        try:
            model.save(MODEL_PATH)
            joblib.dump(scaler, SCALER_PATH)
            with open(FEATURES_PATH, 'w') as f:
                json.dump(feature_cols, f)
            logger.info("Model cached to disk.")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    else:
        final_loss = 0.0

    # --- Inference ---
    # Cached 모델을 쓸 때도 현재 데이터프레임을 feature_cols에 맞춰야 함
    # 1. 숫자 변환 (Cached 안 했을 때 이미 변환됐을 수 있으나 안전하게 확인)
    exclude_cols_base = ['name', 'description', 'logoid', 'error', target_col]
    
    for col in feature_cols:
        if col not in df.columns:
            # 원-핫 인코딩 등 없는 컬럼은 0으로 채움
            df[col] = 0.0
        elif col not in exclude_cols_base:
             # 이미 변환되었는지 체크 어렵다면 다시 변환해도 됨 (apply to_float is idempotent-ish if numeric)
             # 여기서는 생략 (위 Training 블록이나 아래 로직에서 처리 필요)
             pass
    
    # 만약 Cached 모델을 사용한다면 df가 위 Training 블록을 안 타서 전처리가 안 되어 있을 수 있음.
    # 따라서 전처리 로직을 공통화하거나 여기서 다시 수행해야 함.
    if not should_train:
         numeric_candidates = [c for c in df.columns if c not in exclude_cols_base and c != 'sector']
         for col in numeric_candidates:
             try:
                 df[col] = df[col].apply(to_float).fillna(0)
             except: pass
             
         if 'sector' in df.columns:
             current_dummies = pd.get_dummies(df['sector'], prefix='sect', dummy_na=True)
             # 합치기
             df = pd.concat([df, current_dummies], axis=1)

    # DataFrame을 feature_cols 순서와 구성에 맞게 재정렬
    # 누락된 컬럼 0 채우기, 넘치는 컬럼 무시
    X_target_df = pd.DataFrame(index=df.index)
    for col in feature_cols:
        if col in df.columns:
            X_target_df[col] = df[col]
        else:
            X_target_df[col] = 0.0
            
    # Target Row만 추출
    X_target_vals = X_target_df.loc[[target_idx]].values
    X_target_scaled = scaler.transform(X_target_vals)
    
    pred_log = model.predict(X_target_scaled, verbose=0)[0][0]
    inferred_market_cap = float(np.expm1(pred_log)) # exp(log) - 1
    
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
