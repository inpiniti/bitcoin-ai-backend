
"""
Step 2: XGBoost 학습 (Python)
"""
import logging
import json
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

async def handler(event, context):
    job_id = event.get("jobId")

    try:
        context.logger.info(f"[XGB:Worker] Training job {job_id} started")

        # Fetch data from state (to avoid E2BIG via event args)
        job_data = await context.state.get("xgb-jobs", job_id)
        if not job_data:
            raise Exception(f"Job data not found for {job_id}")
            
        features = job_data.get("features")
        labels = job_data.get("labels")
        
        if not features or not labels:
            raise Exception("Features or Labels missing in job data")

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
        
        # Serialize model to JSON
        # get_booster().save_raw('json') returns bytes, needs decode
        model_json_str = model.get_booster().save_raw('json').decode('utf-8')
        model_json = json.loads(model_json_str)

        context.logger.info(f"[XGB:Worker] Training complete. Accuracy: {accuracy}")

        # Result
        result = {
            "modelJson": model_json,
            "accuracy": accuracy,
            "featureCount": X.shape[1],
            "sampleCount": X.shape[0]
        }

        # Update State
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
