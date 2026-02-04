
"""
Step 4: XGBoost 예측 (Python)
"""
import logging
import json
import tempfile
import os
import xgboost as xgb
import numpy as np

config = {
    "name": "xgb-predict-worker",
    "type": "event",
    "subscribes": ["xgb-predict"],
    "emits": [],
    "flows": ["xgb-flow"]
}

async def handler(event, context):
    job_id = event.get("jobId")
    model_json = event.get("modelJson")
    features = event.get("features")

    try:
        context.logger.info(f"[XGB:Worker] Prediction job {job_id}")

        # Load Model via temp file (XGBoost load_model needs file path for JSON format)
        model_json_str = json.dumps(model_json)
        
        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(model_json_str)
            temp_path = f.name
        
        try:
            booster = xgb.Booster()
            booster.load_model(temp_path)
        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        # Prepare Data
        # features is list of lists [[f1, f2, ...]] or single list [f1, f2, ...]
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

