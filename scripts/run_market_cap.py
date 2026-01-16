
import sys
import json
import os
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("run_market_cap")

# 전역 변수 (Lazy Load)
np = None
pd = None
tf = None
joblib = None

def get_dependencies():
    global np, pd, tf, joblib
    if np is None:
        logger.info("Loading dependencies...")
        import numpy as _np
        import pandas as _pd
        import tensorflow as _tf
        import joblib as _joblib
        np = _np
        pd = _pd
        tf = _tf
        joblib = _joblib
        logger.info("Dependencies loaded.")
    return np, pd, tf, joblib

def main():
    try:
        if len(sys.argv) < 2:
            print(json.dumps({"error": "No input file provided"}))
            sys.exit(1)
            
        input_file = sys.argv[1]
        logger.info(f"Reading input from {input_file}")
        
        with open(input_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # 데이터 파싱
        job_id = data.get('jobId')
        ticker = data.get('ticker')
        raw_list = data.get('rawData', [])
        
        logger.info(f"Job: {job_id}, Ticker: {ticker}, Data Points: {len(raw_list)}")
        
        # --- 의존성 로드 및 로직 수행 (여기서는 디버그 모드) ---
        # get_dependencies() # 실제 로직 복원 시 주석 해제
        
        # 임시 디버그 로직
        result = {
            "symbol": ticker,
            "actual_market_cap": 0,
            "inferred_market_cap": 0,
            "diff_value": 0,
            "diff_percent": 0,
            "model_loss": 0,
            "cached": False,
            "cache_source": "script_debug"
        }
        
        # 결과 출력 (JSON)
        print(json.dumps(result))
        
    except Exception as e:
        logger.error(f"Execution failed: {e}")
        # 에러 발생 시 JSON 형태로 에러 출력
        error_result = {"error": str(e)}
        print(json.dumps(error_result))
        sys.exit(1)

if __name__ == "__main__":
    main()
