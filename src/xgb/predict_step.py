
"""
Step 4: XGBoost 예측 (Python)
"""
import logging
import json
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

        # Load Model
        # JSON object -> String -> Bytes
        model_json_str = json.dumps(model_json)
        
        booster = xgb.Booster()
        booster.load_model(bytearray(model_json_str, 'utf-8'))
        
        # Prepare Data
        # features is list of lists [[f1, f2, ...]] or single list [f1, f2, ...]
        input_data = np.array(features)
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
