
import os
import time
import json
import logging
import numpy as np
import pandas as pd
import tensorflow as tf
import joblib
from sklearn.preprocessing import StandardScaler
from datetime import datetime, timedelta

# Logger setup
logger = logging.getLogger("market_cap_step")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# Step Configuration
config = {
    "name": "analyze-market-cap",
    "type": "event",
    "subscribes": ["analyze-market-cap"],
    "emits": ["format-market-cap"],
    "flows": ["market-cap-inference-flow"]
}

# Cache Configuration
MODEL_DIR = "models/market_cap"
os.makedirs(MODEL_DIR, exist_ok=True)
MODEL_PATH = os.path.join(MODEL_DIR, "market_cap_model.keras")
SCALER_PATH = os.path.join(MODEL_DIR, "scaler.joblib")
FEATURES_PATH = os.path.join(MODEL_DIR, "features.json")
MODEL_TTL_HOURS = 24

def get_model_age():
    if not os.path.exists(MODEL_PATH):
        return None
    file_time = os.path.getmtime(MODEL_PATH)
    return datetime.now() - datetime.fromtimestamp(file_time)

def handler(event, context):
    try:
        job_id = event.get('jobId')
        ticker = event.get('ticker')
        raw_list = event.get('rawData')
        
        logger.info(f"[Step3:AI] Starting analysis for {ticker}. Data points: {len(raw_list)}")

        # --- 1. Data Processing ---
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

        # --- 2. Cache Logic ---
        age = get_model_age()
        should_train = True
        
        if age and age < timedelta(hours=MODEL_TTL_HOURS):
            try:
                logger.info(f"Loading cached model (Age: {age})...")
                model = tf.keras.models.load_model(MODEL_PATH)
                scaler = joblib.load(SCALER_PATH)
                with open(FEATURES_PATH, 'r') as f:
                    feature_cols = json.load(f)
                should_train = False
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}. Will retrain.")

        if should_train:
            logger.info("Training new model...")
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
            
            logger.info(f"Training on {len(X)} samples...")
            history = model.fit(X_scaled, y, epochs=20, batch_size=32, verbose=0, validation_split=0.1)
            final_loss = history.history['loss'][-1]
            
            # Save Cache
            model.save(MODEL_PATH)
            joblib.dump(scaler, SCALER_PATH)
            with open(FEATURES_PATH, 'w') as f:
                json.dump(feature_cols, f)
            logger.info("Model and scaler cached.")
        else:
            final_loss = 0 # Not calculated during inference-only

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
            "cached": not should_train
        }
        
        context['emit']({
            "topic": "format-market-cap",
            "data": {
                "jobId": job_id,
                "result": result
            }
        })

    except Exception as e:
        logger.error(f"Analysis failed: {str(e)}")
        raise e
