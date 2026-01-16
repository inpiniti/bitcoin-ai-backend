
import os
import time
import json
import logging
import numpy as np
import pandas as pd
import tensorflow as tf
from sklearn.preprocessing import StandardScaler

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

def handler(event, context):
    try:
        # Motia passes the event data directly as 'event'
        job_id = event.get('jobId')
        ticker = event.get('ticker')
        raw_list = event.get('rawData')
        
        logger.info(f"[Step3:AI] Starting analysis for {ticker}. Data points: {len(raw_list)}")

        # --- 1. Data Processing ---
        df = pd.DataFrame(raw_list)
        
        # Helper to clean numeric values (some might be strings or null)
        def to_float(x):
            try:
                return float(x)
            except:
                return 0.0

        target_col = 'market_cap_basic'
        
        # Ensure target col is numeric
        df[target_col] = df[target_col].apply(to_float)
        
        # Filter valid market caps
        df = df[df[target_col] > 0].copy()
        
        # Locate Target Ticker
        # 'name' could be "AAPL" or "NASDAQ:AAPL". Ticker input implies "AAPL".
        target_mask = df['name'].str.contains(rf'\b{ticker}\b', case=False, regex=True)
        if not target_mask.any():
            # Fallback strict match
            target_mask = df['name'] == ticker
        
        if not target_mask.any():
            raise ValueError(f"Ticker {ticker} not found in fetch results.")
            
        target_idx = df.index[target_mask][0]
        actual_market_cap = float(df.loc[target_idx, target_col])
        target_name = df.loc[target_idx, 'name']

        logger.info(f"Target found: {target_name}, MarketCap: {actual_market_cap}")

        # Drop metadata/text columns for X
        exclude_cols = ['name', 'description', 'logoid', 'error', target_col]
        # 'sector' will be handled separately
        
        # Identify numeric columns dynamically
        numeric_candidates = [c for c in df.columns if c not in exclude_cols and c != 'sector']
        
        # Clean numeric cols
        for col in numeric_candidates:
            df[col] = df[col].apply(to_float).fillna(0)
            
        # One-Hot Encode Sector
        if 'sector' in df.columns:
            df = pd.get_dummies(df, columns=['sector'], prefix='sect', dummy_na=True)
        
        # Refine Feature Columns (Numeric + OneHot Sectors)
        feature_cols = [c for c in df.columns if c not in exclude_cols]
        
        X = df[feature_cols].values
        # Use Log scale for Market Cap target to stabilize training
        y = np.log1p(df[target_col].values) 
        
        # Scale Features
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        input_dim = X_scaled.shape[1]
        
        # --- 2. Model Definition ---
        # Simple MLP for Regression
        model = tf.keras.Sequential([
            tf.keras.layers.Dense(64, activation='relu', input_shape=(input_dim,)),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(32, activation='relu'),
            tf.keras.layers.Dense(1) # Linear output
        ])
        
        model.compile(optimizer='adam', loss='mse')
        
        # --- 3. Train ---
        # Train on full dataset
        logger.info(f"Training model on {len(X)} samples...")
        history = model.fit(X_scaled, y, epochs=20, batch_size=32, verbose=0, validation_split=0.1)
        final_loss = history.history['loss'][-1]
        logger.info(f"Training complete. Final Loss: {final_loss:.4f}")
        
        # --- 4. Inference ---
        target_X = X_scaled[[df.index.get_loc(target_idx)]] # Select as 2D array
        pred_log = model.predict(target_X, verbose=0)[0][0]
        
        # Inverse Log
        inferred_market_cap = float(np.expm1(pred_log))
        
        logger.info(f"Inferred: {inferred_market_cap}")

        # Result Object
        result = {
            "symbol": ticker,
            "actual_market_cap": actual_market_cap,
            "inferred_market_cap": inferred_market_cap,
            "diff_value": inferred_market_cap - actual_market_cap,
            "diff_percent": ((inferred_market_cap - actual_market_cap) / actual_market_cap) * 100,
            "model_loss": final_loss
        }
        
        # Emit to next step
        context['emit']({
            "topic": "format-market-cap",
            "data": {
                "jobId": job_id,
                "result": result
            }
        })

    except Exception as e:
        logger.error(f"Analysis failed: {str(e)}")
        # Propagate error state
        # Raising exception in Motia Python step usually logs it.
        # Ideally we update state, but we don't have direct state access here unless passed in context (Motia V2 might, but here assuming basic emit).
        raise e
