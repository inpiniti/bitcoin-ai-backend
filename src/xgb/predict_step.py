"""
Step 4: XGBoost 예측 (Python)
modelId를 사용하여 Supabase에서 모델을 로드하여 예측합니다.
"""
import logging
import json
import os
import http.client
import urllib.parse
import tempfile
import xgboost as xgb
import numpy as np

config = {
    "name": "xgb-predict-worker",
    "type": "event",
    "subscribes": ["xgb-predict"],
    "emits": [],
    "flows": ["xgb-flow"]
}

# Supabase 설정 (Hugging Face 환경 변수에서 로드)
SUPABASE_URL = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("VITE_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")

def load_model_from_supabase(model_id, logger):
    """Supabase REST API를 통해 모델 로드"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("Supabase configuration missing (URL or KEY)")

    # URL 파싱
    parsed_url = urllib.parse.urlparse(SUPABASE_URL)
    host = parsed_url.netloc
    path = f"/rest/v1/ml_models?id=eq.{model_id}&select=model_json"
    
    # HTTP 연결
    conn = http.client.HTTPSConnection(host)
    
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        conn.request("GET", path, headers=headers)
        
        response = conn.getresponse()
        resp_data = response.read().decode('utf-8')
        
        if response.status >= 200 and response.status < 300:
            result = json.loads(resp_data)
            if isinstance(result, list) and len(result) > 0:
                return result[0]['model_json']
            else:
                raise Exception(f"Model ID {model_id} not found in Supabase")
        else:
            logger.error(f"Supabase Error ({response.status}): {resp_data}")
            raise Exception(f"Supabase Load Failed: {resp_data}")
    finally:
        conn.close()

async def handler(event, context):
    job_id = event.get("jobId")
    model_id = event.get("modelId")
    features = event.get("features")

    try:
        context.logger.info(f"[XGB:Worker] Prediction job {job_id}, modelId: {model_id}")

        # Supabase에서 모델 데이터 가져오기
        model_json = load_model_from_supabase(model_id, context.logger)
        model_json_str = json.dumps(model_json)

        # 모델 로드를 위한 임시 파일 생성 (save_model/load_model은 파일 경로 필요)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(model_json_str)
            temp_path = f.name
        
        try:
            booster = xgb.Booster()
            booster.load_model(temp_path)
            context.logger.info(f"[XGB:Worker] Model {model_id} loaded successfully from Supabase")
        finally:
            # 임시 파일 삭제
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        # Prepare Data
        input_data = np.array(features, dtype=np.float32)
        if len(input_data.shape) == 1:
            input_data = input_data.reshape(1, -1)
            
        dmatrix = xgb.DMatrix(input_data)
        
        # Predict
        probs = booster.predict(dmatrix)
        
        result_list = []
        for p in probs:
            result_list.append({
                "probability": float(p),
                "prediction": 1 if p > 0.5 else 0
            })

        # Result
        result = {
            "predictions": result_list
        }

        # Update State
        job = await context.state.get("xgb-jobs", job_id)
        if job:
            job["status"] = "completed"
            job["result"] = result
            await context.state.set("xgb-jobs", job_id, job)

    except Exception as e:
        context.logger.error(f"[XGB:Worker] Predict Error job {job_id}: {str(e)}")
        job = await context.state.get("xgb-jobs", job_id)
        if job:
            job["status"] = "error"
            job["error"] = str(e)
            await context.state.set("xgb-jobs", job_id, job)


