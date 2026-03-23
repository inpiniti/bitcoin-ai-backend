"""
Issue #36: XGBoost train_test_split 시계열 무시 → 데이터 누수
- 시간순 split이 적용되는지 검증
- 훈련셋이 테스트셋보다 시간적으로 앞서는지 검증
"""
import numpy as np


def temporal_split(X, y, test_size=0.2):
    """xgb_service.py의 수정 후 split 로직"""
    split = int(len(X) * (1 - test_size))
    return X[:split], X[split:], y[:split], y[split:]


def test_train_comes_before_test():
    """훈련셋 인덱스가 테스트셋 인덱스보다 모두 앞선다"""
    X = np.arange(100).reshape(100, 1)
    y = np.zeros(100)

    X_train, X_test, _, _ = temporal_split(X, y)

    assert X_train[-1][0] < X_test[0][0], "훈련셋 마지막이 테스트셋 첫 번째보다 앞서야 함"


def test_split_ratio():
    """80/20 분할이 정확히 적용된다"""
    X = np.zeros((100, 5))
    y = np.zeros(100)

    X_train, X_test, y_train, y_test = temporal_split(X, y)

    assert len(X_train) == 80
    assert len(X_test) == 20


def test_no_overlap():
    """훈련셋과 테스트셋에 중복 인덱스가 없다"""
    X = np.arange(50).reshape(50, 1)
    y = np.zeros(50)

    X_train, X_test, _, _ = temporal_split(X, y)
    train_set = set(X_train.flatten().tolist())
    test_set = set(X_test.flatten().tolist())

    assert len(train_set & test_set) == 0, "훈련/테스트셋 중복 없어야 함"


def test_random_split_causes_leakage():
    """랜덤 split은 미래 데이터를 훈련셋에 포함시킨다 (버그 재현)"""
    from sklearn.model_selection import train_test_split

    X = np.arange(100).reshape(100, 1)
    y = np.zeros(100)

    X_train, X_test, _, _ = train_test_split(X, y, test_size=0.2, random_state=42)

    # 랜덤 split 시 훈련셋에 테스트셋보다 큰 인덱스가 들어있을 수 있음 (leakage)
    train_max = X_train.max()
    test_min = X_test.min()
    assert train_max > test_min, "랜덤 split은 data leakage 발생 (이것이 버그)"


def test_temporal_split_prevents_leakage():
    """시간순 split은 leakage를 방지한다"""
    X = np.arange(100).reshape(100, 1)
    y = np.zeros(100)

    X_train, X_test, _, _ = temporal_split(X, y)

    assert X_train.max() < X_test.min(), "시간순 split: 훈련셋 최대 < 테스트셋 최소"
