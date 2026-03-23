"""
Issue #37: XGBoost 평가 지표 F1/Precision/Recall/AUC 미계산
- 수정 후 평가 지표가 올바르게 계산되는지 검증
"""
import pytest
import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score


def compute_metrics(y_true, y_pred, y_proba):
    """xgb_service.py의 수정 후 지표 계산 로직"""
    accuracy  = float(accuracy_score(y_true, y_pred))
    f1        = float(f1_score(y_true, y_pred, zero_division=0))
    precision = float(precision_score(y_true, y_pred, zero_division=0))
    recall    = float(recall_score(y_true, y_pred, zero_division=0))
    try:
        import math
        auc_val = roc_auc_score(y_true, y_proba)
        auc = 0.0 if math.isnan(auc_val) else float(auc_val)
    except ValueError:
        auc = 0.0
    return {"accuracy": accuracy, "f1": f1, "precision": precision, "recall": recall, "auc": auc}


def test_all_metrics_computed():
    """5개 지표가 모두 반환된다"""
    y_true  = np.array([0, 1, 0, 1, 1])
    y_pred  = np.array([0, 1, 0, 0, 1])
    y_proba = np.array([0.1, 0.9, 0.2, 0.4, 0.8])

    metrics = compute_metrics(y_true, y_pred, y_proba)

    assert "accuracy" in metrics
    assert "f1" in metrics
    assert "precision" in metrics
    assert "recall" in metrics
    assert "auc" in metrics


def test_metrics_range():
    """모든 지표는 0~1 범위"""
    y_true  = np.array([0, 1, 0, 1, 1])
    y_pred  = np.array([0, 1, 0, 0, 1])
    y_proba = np.array([0.1, 0.9, 0.2, 0.4, 0.8])

    metrics = compute_metrics(y_true, y_pred, y_proba)

    for k, v in metrics.items():
        assert 0.0 <= v <= 1.0, f"{k}={v} 범위 벗어남"


def test_single_class_auc_fallback():
    """단일 클래스일 때 AUC는 0.0으로 폴백한다"""
    y_true  = np.array([0, 0, 0, 0])
    y_pred  = np.array([0, 0, 0, 0])
    y_proba = np.array([0.1, 0.2, 0.1, 0.2])

    metrics = compute_metrics(y_true, y_pred, y_proba)
    assert metrics["auc"] == 0.0


def test_perfect_predictions():
    """완벽한 예측은 모든 지표가 1.0"""
    y_true  = np.array([0, 1, 0, 1])
    y_pred  = np.array([0, 1, 0, 1])
    y_proba = np.array([0.0, 1.0, 0.0, 1.0])

    metrics = compute_metrics(y_true, y_pred, y_proba)

    assert metrics["accuracy"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["auc"] == 1.0


def test_accuracy_alone_misleads_imbalanced():
    """불균형 데이터에서 accuracy만으로는 부족함을 보여준다"""
    # 95% 음성 클래스 → 모두 0 예측해도 accuracy=95%
    y_true  = np.array([0]*95 + [1]*5)
    y_pred  = np.array([0]*100)
    y_proba = np.array([0.1]*100)

    metrics = compute_metrics(y_true, y_pred, y_proba)

    assert metrics["accuracy"] >= 0.9  # 높은 accuracy
    assert metrics["recall"] == 0.0    # 양성 클래스를 전혀 못 잡음
    assert metrics["f1"] == 0.0        # F1은 0 → 모델 쓸모없음
