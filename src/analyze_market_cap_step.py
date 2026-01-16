
# === DEBUG MODE: MINIMAL ===
print("[ANALYZE_MARKET_CAP_STEP] === MINIMAL DEBUG LOADED ===")

import logging

# Step Configuration
config = {
    "name": "analyze-market-cap",
    "type": "event",
    "subscribes": ["analyze-market-cap"],
    "emits": ["format-market-cap"],
    "flows": ["market-cap-inference-flow"]
}

async def handler(event, context):
    # Logger setup inside handler to ensure visibility
    logger = logging.getLogger("market_cap_debug")
    logging.basicConfig(level=logging.INFO)
    
    print(f"[DEBUG] Handler invoked for event: {event.keys()}")
    logger.info("[DEBUG] Handler invoked via Logger")
    
    job_id = event.get('jobId')
    
    # 1초 대기 (처리 흉내)
    import asyncio
    await asyncio.sleep(1)
    
    # 결과 전송 (더미 데이터)
    result = {
        "symbol": event.get('ticker', 'UNKNOWN'),
        "actual_market_cap": 0,
        "inferred_market_cap": 0,
        "diff_value": 0,
        "diff_percent": 0,
        "model_loss": 0,
        "cached": False,
        "cache_source": "debug_mode"
    }
    
    await context.emit({
        "topic": "format-market-cap",
        "data": {
            "jobId": job_id,
            "result": result
        }
    })
    
    logger.info(f"[DEBUG] Event emitted for job {job_id}")

