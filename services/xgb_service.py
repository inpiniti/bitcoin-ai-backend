"""
XGBoost 학습 / 예측 서비스
(기존 train_step.py + predict_step.py 통합)
"""
import json
import logging
import os
import tempfile

logger = logging.getLogger("xgb_service")

_xgb = None
_np = None


def _get_deps():
    global _xgb, _np
    if _xgb is None:
        import xgboost as x
        import numpy as n
        _xgb, _np = x, n
    return _xgb, _np


# ── 학습 ─────────────────────────────────────────────────

async def train(dataset_id: str, model_name: str) -> dict:
    """Supabase에서 데이터를 로드해 XGBoost 학습 후 저장합니다."""
    from services import supabase_service
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score

    xgb, np = _get_deps()

    logger.info(f"[XGB:Train] 데이터셋 로드: {dataset_id}")
    features, labels = await supabase_service.load_dataset(dataset_id)

    X = np.array(features)
    y = np.array(labels)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = xgb.XGBClassifier(
        n_estimators=100,
        learning_rate=0.1,
        max_depth=6,
        objective="binary:logistic",
        eval_metric="logloss",
        use_label_encoder=False,
    )
    model.fit(X_train, y_train)

    preds    = model.predict(X_test)
    accuracy = float(accuracy_score(y_test, preds))

    model_json_str = model.get_booster().save_raw("json").decode("utf-8")
    model_json     = json.loads(model_json_str)

    model_data = {
        "name":          model_name,
        "accuracy":      accuracy,
        "feature_count": int(X.shape[1]),
        "sample_count":  int(X.shape[0]),
        "model_json":    model_json,
    }

    logger.info(f"[XGB:Train] Supabase 저장 중... accuracy={accuracy:.4f}")
    model_id = await supabase_service.save_model(model_data)
    logger.info(f"[XGB:Train] 저장 완료 modelId={model_id}")

    return {
        "modelId":      model_id,
        "accuracy":     accuracy,
        "featureCount": int(X.shape[1]),
        "sampleCount":  int(X.shape[0]),
    }


# ── 예측 ─────────────────────────────────────────────────

async def predict(model_id: str, features: list | None, dataset_id: str | None) -> dict:
    """Supabase에서 모델을 로드해 예측합니다."""
    from services import supabase_service

    xgb, np = _get_deps()

    logger.info(f"[XGB:Predict] 모델 로드: {model_id}")
    model_json = await supabase_service.load_model(model_id)

    # 임시 파일로 모델 로드 (xgb.Booster는 파일 경로 필요)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(model_json, f)
        temp_path = f.name

    try:
        booster = xgb.Booster()
        booster.load_model(temp_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # features 확보
    if dataset_id and not features:
        logger.info(f"[XGB:Predict] 데이터셋 로드: {dataset_id}")
        features = await supabase_service.load_features(dataset_id)

    if not features:
        raise ValueError("features 또는 datasetId 가 필요합니다")

    input_data = np.array(features, dtype=np.float32)
    if len(input_data.shape) == 1:
        input_data = input_data.reshape(1, -1)

    dmatrix = xgb.DMatrix(input_data)
    probs   = booster.predict(dmatrix)

    result_list = []
    for p in (probs if hasattr(probs, "__iter__") else [probs]):
        result_list.append({
            "probability": float(p),
            "prediction":  1 if float(p) > 0.5 else 0,
        })

    logger.info(f"[XGB:Predict] {len(result_list)}건 예측 완료")
    return {"predictions": result_list}
