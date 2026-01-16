
import os
import sys
import time
import json
import logging

# Logger setup (가장 먼저!)
logging.basicConfig(level=logging.INFO, format='[%(name)s] %(message)s')
logger = logging.getLogger("market_cap_step")
logger.info("=== Module loading started ===")

# 가벼운 import
logger.info("Loading standard libraries...")
from datetime import datetime, timedelta

# 무거운 라이브러리는 로깅과 함께 import
try:
    logger.info("Loading numpy...")
    import numpy as np
    logger.info("Loading pandas...")
    import pandas as pd
    logger.info("Loading sklearn...")
    from sklearn.preprocessing import StandardScaler
    logger.info("Loading joblib...")
    import joblib
    logger.info("All imports successful!")
except Exception as e:
    logger.error(f"Import failed: {e}")
    raise

# TensorFlow는 lazy import (handler에서 필요할 때 로드)
tf = None

def get_tensorflow():
    """TensorFlow를 lazy load합니다. 첫 호출 시에만 import."""
    global tf
    if tf is None:
        logger.info("Loading TensorFlow (first time)...")
        import tensorflow as _tf
        tf = _tf
        logger.info(f"TensorFlow {tf.__version__} loaded successfully!")
    return tf

# Step Configuration
config = {
    "name": "analyze-market-cap",
    "type": "event",
    "subscribes": ["analyze-market-cap"],
    "emits": ["format-market-cap"],
    "flows": ["market-cap-inference-flow"]
}

logger.info("=== Module loading completed ===")

# ============================================
# Memory Cache (프로세스 수명 동안 유지)
# ============================================
_memory_cache = {
    "model": None,
    "scaler": None,
    "feature_cols": None,
    "cached_at": None
}

MEMORY_TTL_HOURS = 6  # 메모리 캐시는 6시간 유지

def get_memory_cache():
    """메모리에서 캐시된 모델을 반환. TTL 체크 포함."""
    global _memory_cache
    
    if _memory_cache["model"] is None:
        return None
    
    # TTL 체크
    if _memory_cache["cached_at"]:
        age = datetime.now() - _memory_cache["cached_at"]
        if age > timedelta(hours=MEMORY_TTL_HOURS):
            logger.info(f"[Cache] Memory cache expired (Age: {age})")
            clear_memory_cache()
            return None
    
    return _memory_cache

def set_memory_cache(model, scaler, feature_cols):
    """모델을 메모리에 캐싱."""
    global _memory_cache
    _memory_cache["model"] = model
    _memory_cache["scaler"] = scaler
    _memory_cache["feature_cols"] = feature_cols
    _memory_cache["cached_at"] = datetime.now()
    logger.info("[Cache] Model cached in memory")

def clear_memory_cache():
    """메모리 캐시 클리어."""
    global _memory_cache
    _memory_cache = {
        "model": None,
        "scaler": None,
        "feature_cols": None,
        "cached_at": None
    }

# ============================================
# File Cache (영구 저장, 선택적)
# ============================================
MODEL_DIR = "models/market_cap"
os.makedirs(MODEL_DIR, exist_ok=True)
MODEL_PATH = os.path.join(MODEL_DIR, "market_cap_model.keras")
SCALER_PATH = os.path.join(MODEL_DIR, "scaler.joblib")
FEATURES_PATH = os.path.join(MODEL_DIR, "features.json")
FILE_TTL_HOURS = 24

def get_file_cache_age():
    if not os.path.exists(MODEL_PATH):
        return None
    file_time = os.path.getmtime(MODEL_PATH)
    return datetime.now() - datetime.fromtimestamp(file_time)

def handler(event, context):
    # TensorFlow lazy load (handler 호출 시에만 로드)
    global tf
    tf = get_tensorflow()
    
    try:
        # --- 0. Input Validation ---
        logger.info(f"[Step3:AI] Handler started. Event keys: {list(event.keys())}")
        
        job_id = event.get('jobId')
        ticker = event.get('ticker')
        raw_list = event.get('rawData')
        
        if not job_id:
            raise ValueError("Missing jobId in event")
        if not ticker:
            raise ValueError("Missing ticker in event")
        if not raw_list:
            raise ValueError("Missing rawData in event")
        
        logger.info(f"[Step3:AI] Job {job_id}: Starting analysis for {ticker}. Data points: {len(raw_list)}")

        # --- 1. Data Processing ---
        logger.info(f"[Step3:AI] Job {job_id}: Creating DataFrame...")
        df = pd.DataFrame(raw_list)
        
        def to_float(x):
            try:
                return float(x)
            except:
                return 0.0

        target_col = 'market_cap_basic'
        df[target_col] = df[target_col].apply(to_float)
        df = df[df[target_col] > 0].copy()
        
        # Locate Target Ticker
        target_mask = df['name'].str.contains(rf'\b{ticker}\b', case=False, regex=True)
        if not target_mask.any():
            target_mask = df['name'] == ticker
        
        if not target_mask.any():
            raise ValueError(f"Ticker {ticker} not found in fetch results.")
            
        target_idx = df.index[target_mask][0]
        actual_market_cap = float(df.loc[target_idx, target_col])
        target_name = df.loc[target_idx, 'name']

        logger.info(f"Target found: {target_name}, MarketCap: {actual_market_cap}")

        # --- 2. Cache Logic (메모리 우선 → 파일 → 새 학습) ---
        should_train = True
        model = None
        scaler = None
        feature_cols = None
        
        # Step 2-1: 메모리 캐시 확인 (가장 빠름)
        mem_cache = get_memory_cache()
        if mem_cache:
            logger.info("[Cache] Using memory-cached model ⚡")
            model = mem_cache["model"]
            scaler = mem_cache["scaler"]
            feature_cols = mem_cache["feature_cols"]
            should_train = False
        
        # Step 2-2: 파일 캐시 확인 (메모리 캐시 없을 때)
        if should_train:
            file_age = get_file_cache_age()
            if file_age and file_age < timedelta(hours=FILE_TTL_HOURS):
                try:
                    logger.info(f"[Cache] Loading file cache (Age: {file_age})...")
                    model = tf.keras.models.load_model(MODEL_PATH)
                    scaler = joblib.load(SCALER_PATH)
                    with open(FEATURES_PATH, 'r') as f:
                        feature_cols = json.load(f)
                    should_train = False
                    # 파일에서 로드한 모델을 메모리에도 캐싱
                    set_memory_cache(model, scaler, feature_cols)
                except Exception as e:
                    logger.warning(f"[Cache] Failed to load file cache: {e}")

        # Step 2-3: 새로 학습 (캐시 없을 때)
        if should_train:
            logger.info("[Train] Training new model... (첫 호출 또는 캐시 만료)")
            # Drop metadata/text columns for X
            exclude_cols = ['name', 'description', 'logoid', 'error', target_col]
            
            # Identify numeric columns
            numeric_candidates = [c for c in df.columns if c not in exclude_cols and c != 'sector']
            for col in numeric_candidates:
                df[col] = df[col].apply(to_float).fillna(0)
                
            # One-Hot Encode Sector
            if 'sector' in df.columns:
                df = pd.get_dummies(df, columns=['sector'], prefix='sect', dummy_na=True)
            
            feature_cols = [c for c in df.columns if c not in exclude_cols]
            
            X = df[feature_cols].values
            y = np.log1p(df[target_col].values) 
            
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            
            input_dim = X_scaled.shape[1]
            
            model = tf.keras.Sequential([
                tf.keras.layers.Dense(64, activation='relu', input_shape=(input_dim,)),
                tf.keras.layers.Dropout(0.2),
                tf.keras.layers.Dense(32, activation='relu'),
                tf.keras.layers.Dense(1)
            ])
            model.compile(optimizer='adam', loss='mse')
            
            logger.info(f"[Train] Training on {len(X)} samples...")
            history = model.fit(X_scaled, y, epochs=20, batch_size=32, verbose=0, validation_split=0.1)
            final_loss = history.history['loss'][-1]
            
            # 메모리 캐시에 저장 (가장 중요!)
            set_memory_cache(model, scaler, feature_cols)
            
            # 파일 캐시에도 저장 (선택적, 실패해도 OK)
            try:
                model.save(MODEL_PATH)
                joblib.dump(scaler, SCALER_PATH)
                with open(FEATURES_PATH, 'w') as f:
                    json.dump(feature_cols, f)
                logger.info("[Cache] Model saved to file (backup)")
            except Exception as e:
                logger.warning(f"[Cache] File save failed (OK): {e}")
        else:
            final_loss = 0  # 캐시 사용 시 loss 계산 안함

        # --- 3. Inference ---
        # Prepare target row with same features as the model expects
        # 1. Start with numeric/dummy processing on full df to ensure target row has all columns
        # (Though we already have df from step 1)
        
        # Need to re-process df columns to match feature_cols for cached model
        exclude_cols_base = ['name', 'description', 'logoid', 'error', target_col]
        
        # Ensure all required features are present
        for col in feature_cols:
            if col not in df.columns:
                # If it's a dummy sector column, it might be missing in this batch
                df[col] = 0.0
            elif col not in exclude_cols_base:
                df[col] = df[col].apply(to_float).fillna(0)

        # Re-run dummy encoding if needed to capture sectors present in current data 
        # but the model's feature_cols is the source of truth.
        if 'sector' in df.columns:
             current_dummies = pd.get_dummies(df[['sector']], prefix='sect', dummy_na=True)
             for col in current_dummies.columns:
                 if col in feature_cols:
                     df[col] = current_dummies[col]

        X_target = df[feature_cols].values[[df.index.get_loc(target_idx)]]
        X_target_scaled = scaler.transform(X_target)
        
        pred_log = model.predict(X_target_scaled, verbose=0)[0][0]
        inferred_market_cap = float(np.expm1(pred_log))
        
        logger.info(f"Inferred: {inferred_market_cap}")

        result = {
            "symbol": ticker,
            "actual_market_cap": actual_market_cap,
            "inferred_market_cap": inferred_market_cap,
            "diff_value": inferred_market_cap - actual_market_cap,
            "diff_percent": ((inferred_market_cap - actual_market_cap) / actual_market_cap) * 100,
            "model_loss": float(final_loss),
            "cached": not should_train,
            "cache_source": "memory" if (mem_cache and not should_train) else ("file" if not should_train else "trained")
        }
        
        context['emit']({
            "topic": "format-market-cap",
            "data": {
                "jobId": job_id,
                "result": result
            }
        })

    except Exception as e:
        import traceback
        error_msg = str(e)
        error_trace = traceback.format_exc()
        logger.error(f"[Step3:AI] Analysis failed for job {event.get('jobId', 'unknown')}")
        logger.error(f"[Step3:AI] Error: {error_msg}")
        logger.error(f"[Step3:AI] Traceback:\n{error_trace}")
        
        # State를 error로 업데이트하여 API가 에러 응답을 받을 수 있도록 함
        try:
            job_id = event.get('jobId')
            if job_id and 'state' in context:
                context['state'].set('market_cap_jobs', job_id, {
                    'jobId': job_id,
                    'status': 'error',
                    'error': error_msg,
                    'errorTrace': error_trace[:500]  # 처음 500자만
                })
                logger.info(f"[Step3:AI] State updated to error for job {job_id}")
        except Exception as state_error:
            logger.error(f"[Step3:AI] Failed to update state: {state_error}")
        
        raise e

