
"""
Step 2: XGBoost 학습 (Python)
학습된 모델을 Supabase에 저장하고 modelId를 반환합니다.
"""
import logging
import json
import os
import http.client
import urllib.parse
import xgboost as xgb
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
import numpy as np

config = {
    "name": "xgb-train-worker",
    "type": "event",
    "subscribes": ["xgb-train"],
    "emits": [],
    "flows": ["xgb-flow"]
}

# Supabase 설정 (Hugging Face 환경 변수에서 로드)
SUPABASE_URL = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("VITE_SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")

def save_model_to_supabase(model_data, logger):
    """Supabase REST API를 통해 모델 저장"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("Supabase configuration missing (URL or KEY)")

    # URL 파싱
    parsed_url = urllib.parse.urlparse(SUPABASE_URL)
    host = parsed_url.netloc
    path = "/rest/v1/ml_models"
    
    # HTTP 연결
    conn = http.client.HTTPSConnection(host)
    
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation" # 저장된 row를 반환받기 위함
    }
    
    try:
        payload = json.dumps(model_data)
        conn.request("POST", path, body=payload, headers=headers)
        
        response = conn.getresponse()
        resp_data = response.read().decode('utf-8')
        
        if response.status >= 200 and response.status < 300:
            result = json.loads(resp_data)
            return result[0]['id'] if isinstance(result, list) and len(result) > 0 else None
        else:
            logger.error(f"Supabase Error ({response.status}): {resp_data}")
            raise Exception(f"Supabase Save Failed: {resp_data}")
    finally:
        conn.close()

async def handler(event, context):
    job_id = event.get("jobId")
    features = event.get("features")
    labels = event.get("labels")

    try:
        context.logger.info(f"[XGB:Worker] Training job {job_id} started")

        X = np.array(features)
        y = np.array(labels)

        # Train/Test Split
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

        # XGBoost Model
        model = xgb.XGBClassifier(
            n_estimators=100,
            learning_rate=0.1,
            max_depth=6,
            objective='binary:logistic',
            eval_metric='logloss',
            use_label_encoder=False
        )

        model.fit(X_train, y_train)

        # Evaluate
        preds = model.predict(X_test)
        accuracy = float(accuracy_score(y_test, preds))
        
        # 모델 직렬화 (JSON 형식)
        model_json_str = model.get_booster().save_raw('json').decode('utf-8')
        model_json = json.loads(model_json_str)
        
        # Supabase에 저장할 데이터 준비
        model_data = {
            "name": f"XGB_Model_{job_id[:8]}",
            "accuracy": accuracy,
            "feature_count": X.shape[1],
            "sample_count": X.shape[0],
            "model_json": model_json
        }
        
        # Supabase 저장 실행
        model_id = save_model_to_supabase(model_data, context.logger)
        
        context.logger.info(f"[XGB:Worker] Model saved to Supabase: {model_id}, Accuracy: {accuracy}")

        # Result - 저장된 DB의 UUID 반환
        result = {
            "modelId": model_id,
            "accuracy": accuracy,
            "featureCount": X.shape[1],
            "sampleCount": X.shape[0]
        }

        # Update State (Motia job status)
        job = await context.state.get("xgb-jobs", job_id)
        if job:
            job["status"] = "completed"
            job["result"] = result
            await context.state.set("xgb-jobs", job_id, job)

    except Exception as e:
        context.logger.error(f"[XGB:Worker] Error job {job_id}: {str(e)}")
        job = await context.state.get("xgb-jobs", job_id)
        if job:
            job["status"] = "error"
            job["error"] = str(e)
            await context.state.set("xgb-jobs", job_id, job)

