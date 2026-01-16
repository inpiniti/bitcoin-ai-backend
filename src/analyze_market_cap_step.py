
# === DEBUG MODE: CLEAN REWRITE v3 ===
print("[ANALYZE_MARKET_CAP_STEP] === CLEAN REWRITE LOADED ===")

import logging
import asyncio

# Step Configuration
config = {
    "name": "analyze-market-cap",
    "type": "event",
    "subscribes": ["analyze-market-cap"],
    "emits": ["format-market-cap"],
    "flows": ["market-cap-inference-flow"]
}

async def handler(event, context):
    logger = logging.getLogger("market_cap_debug")
    logging.basicConfig(level=logging.INFO)
    
    # 확실한 로그 출력
    print(f"[DEBUG] Handler invoked! Keys: {list(event.keys())}")
    logger.info(f"[DEBUG] Handler invoked via Logger")

    job_id = event.get('jobId', 'unknown')
    
    # 1초 대기
    await asyncio.sleep(1)
    
    result = {
        "symbol": event.get('ticker', 'DEBUG'),
        "actual_market_cap": 0,
        "inferred_market_cap": 0,
        "diff_value": 0,
        "diff_percent": 0,
        "model_loss": 0,
        "cached": False,
        "cache_source": "clean_rewrite"
    }
    
    await context.emit({
        "topic": "format-market-cap",
        "data": {
            "jobId": job_id,
            "result": result
        }
    })
    
    logger.info(f"[DEBUG] Emitted result for {job_id}")
