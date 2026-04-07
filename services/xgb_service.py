"""
XGBoost 학습 / 예측 서비스
(기존 train_step.py + predict_step.py 통합)
"""
import json
import logging

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

    logger.info(f"[XGB:Train] 데이터셋 로드: {dataset_id}")
    features, labels = await supabase_service.load_dataset(dataset_id)
    return await train_from_data(features, labels, model_name)


async def train_from_data(features: list, labels: list, model_name: str) -> dict:
    """
    이미 수집된 features/labels로 XGBoost 학습 후 Supabase에 저장합니다.
    data_collector.py에서 서버 사이드 수집 완료 후 직접 호출하는 경로입니다.
    """
    from services import supabase_service
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

    xgb, np = _get_deps()

    X = np.array(features)
    y = np.array(labels)

    logger.info(f"[XGB:Train] 학습 시작: {X.shape[0]}개 샘플, {X.shape[1]}개 피처")

    # 시계열 순서 유지 — 랜덤 셔플 시 미래 데이터가 훈련셋에 유입됨(data leakage)
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

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
    probs    = model.predict_proba(X_test)[:, 1]
    accuracy  = float(accuracy_score(y_test, preds))
    f1        = float(f1_score(y_test, preds, zero_division=0))
    precision = float(precision_score(y_test, preds, zero_division=0))
    recall    = float(recall_score(y_test, preds, zero_division=0))
    try:
        auc_val = roc_auc_score(y_test, probs)
        import math
        auc = 0.0 if math.isnan(auc_val) else float(auc_val)
    except ValueError:
        auc = 0.0  # 단일 클래스만 존재하는 경우

    model_json_str = model.get_booster().save_raw("json").decode("utf-8")
    model_json     = json.loads(model_json_str)

    model_data = {
        "name":          model_name,
        "accuracy":      accuracy,
        "f1":            f1,
        "precision":     precision,
        "recall":        recall,
        "auc":           auc,
        "feature_count": int(X.shape[1]),
        "sample_count":  int(X.shape[0]),
        "model_json":    model_json,
    }

    logger.info(f"[XGB:Train] Supabase 저장 중... accuracy={accuracy:.4f} f1={f1:.4f} auc={auc:.4f}")
    model_id = await supabase_service.save_model(model_data)
    logger.info(f"[XGB:Train] 저장 완료 modelId={model_id}")

    return {
        "modelId":      model_id,
        "accuracy":     accuracy,
        "f1":           f1,
        "precision":    precision,
        "recall":       recall,
        "auc":          auc,
        "featureCount": int(X.shape[1]),
        "sampleCount":  int(X.shape[0]),
    }


# ── 예측 ─────────────────────────────────────────────────

async def predict(
    model_id: str,
    features: list | None,
    dataset_id: str | None,
    ticker: str | None = None,
    days: int = 2000,
) -> dict:
    """Supabase에서 모델을 로드해 예측합니다.
    ticker가 주어지면 서버에서 직접 데이터 수집 → 피처 추출 → 예측까지 처리합니다.
    """
    from services import supabase_service, data_collector

    xgb, np = _get_deps()

    logger.info(f"[XGB:Predict] 모델 로드: {model_id}")
    model_json = await supabase_service.load_model(model_id)

    # bytearray로 직접 로드 (임시 파일 불필요)
    model_bytes = json.dumps(model_json).encode("utf-8")
    booster = xgb.Booster()
    booster.load_model(bytearray(model_bytes))

    # features 확보 — ticker 우선, 그 다음 datasetId, 마지막으로 inline features
    dates: list = []
    raw_features: list = []

    if ticker and not features:
        logger.info(f"[XGB:Predict] ticker={ticker} 데이터 수집 시작 (days={days})")
        candles = await data_collector.fetch_stock_history_yf(ticker, days)
        if not candles:
            raise ValueError(f"ticker '{ticker}' 의 데이터를 가져올 수 없습니다")
        features, dates, raw_features = data_collector.process_stock_data_for_prediction(candles)
        logger.info(f"[XGB:Predict] 피처 추출 완료: {len(features)}개")
    elif dataset_id and not features:
        logger.info(f"[XGB:Predict] 데이터셋 로드: {dataset_id}")
        features = await supabase_service.load_features(dataset_id)

    if not features:
        raise ValueError("ticker, features, datasetId 중 하나는 필요합니다")

    input_data = np.array(features, dtype=np.float32)
    if len(input_data.shape) == 1:
        input_data = input_data.reshape(1, -1)

    dmatrix = xgb.DMatrix(input_data)
    probs   = booster.predict(dmatrix)

    result_list = []
    for idx, p in enumerate(probs if hasattr(probs, "__iter__") else [probs]):
        entry = {
            "probability": float(p),
            "prediction":  1 if float(p) > 0.5 else 0,
        }
        if idx < len(dates):
            entry["date"] = dates[idx]
        if idx < len(raw_features):
            entry.update(raw_features[idx])
        result_list.append(entry)

    logger.info(f"[XGB:Predict] {len(result_list)}건 예측 완료")
    return {"predictions": result_list}
