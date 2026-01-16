
"""
Step 3: Market Cap Analysis (AMC Debug)
"""
import logging
import asyncio

# 전역 변수 (Lazy Load용)
pd = None
tf = None
np = None
joblib = None

def get_dependencies():
    global pd, tf, np, joblib
    if pd is None:
        import pandas as _pd
        import tensorflow as _tf
        import numpy as _np
        import joblib as _joblib
        pd = _pd
        tf = _tf
        np = _np
        joblib = _joblib
    return pd, tf, np, joblib

# Step Configuration
config = {
    "name": "analyze-market-cap",
    "type": "event",
    "subscribes": ["analyze-market-cap"],
    "emits": ["format-market-cap"],
    "flows": ["market-cap-inference-flow"]
}

async def handler(event, context):
    logger = logging.getLogger("amc_step")
    logging.basicConfig(level=logging.INFO)
    
    logger.info(f"[AMC_STEP] Handler invoked! Keys: {list(event.keys())}")
    print(f"[AMC_STEP] Handler invoked via Print")

    job_id = event.get('jobId', 'unknown')
    
    # 1초 대기 (가짜 처리)
    await asyncio.sleep(1)
    
    result = {
        "symbol": event.get('ticker', 'AMC'),
        "actual_market_cap": 0,
        "inferred_market_cap": 0,
        "diff_value": 0,
        "diff_percent": 0,
        "model_loss": 0,
        "cached": False,
        "cache_source": "amc_debug"
    }
    
    await context.emit({
        "topic": "format-market-cap",
        "data": {
            "jobId": job_id,
            "result": result
        }
    })
    
    logger.info(f"[AMC_STEP] Emitted result for {job_id}")
