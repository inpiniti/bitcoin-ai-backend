"""
Issue #38: indicator_service MACD 지표 누락
- MACD 계산이 올바른지 검증
- add_derived_data에 macd/macd_signal/macd_hist 필드가 추가되는지 검증
"""
import sys
sys.path.insert(0, 'c:/Users/USER/git/bitcoin-ai-backend')
from services.indicator_service import _macd, add_derived_data


def make_candles(prices):
    return [{"date": f"2024-01-{i+1:02d}", "open": p, "high": p, "low": p, "close": p, "volume": 1000}
            for i, p in enumerate(prices)]


def test_macd_line_computed():
    """MACD 라인이 계산된다"""
    prices = [float(i) for i in range(1, 60)]
    macd_line, _, _ = _macd(prices)
    non_none = [v for v in macd_line if v is not None]
    assert len(non_none) > 0


def test_macd_fast_minus_slow():
    """MACD = EMA(12) - EMA(26): 상승 추세에서 양수"""
    prices = [float(i * 2) for i in range(1, 60)]  # 꾸준히 상승
    macd_line, _, _ = _macd(prices)
    last_macd = next((v for v in reversed(macd_line) if v is not None), None)
    assert last_macd is not None
    assert last_macd > 0  # 상승 추세 → EMA(12) > EMA(26)


def test_signal_line_lags_macd():
    """signal은 MACD보다 늦게 시작한다 (EMA of MACD)"""
    prices = [float(i) for i in range(1, 70)]
    macd_line, signal_line, _ = _macd(prices)
    macd_start = next((i for i, v in enumerate(macd_line) if v is not None), None)
    sig_start = next((i for i, v in enumerate(signal_line) if v is not None), None)
    assert sig_start is not None
    assert sig_start > macd_start  # signal은 MACD 이후 시작


def test_histogram_is_macd_minus_signal():
    """histogram = MACD - signal"""
    prices = [float(i + (i % 5)) for i in range(1, 70)]
    macd_line, signal_line, histogram = _macd(prices)
    for i, (m, s, h) in enumerate(zip(macd_line, signal_line, histogram)):
        if m is not None and s is not None and h is not None:
            assert abs(h - (m - s)) < 1e-9, f"index {i}: histogram mismatch"


def test_add_derived_data_includes_macd():
    """add_derived_data 결과에 macd/macd_signal/macd_hist 필드가 포함된다"""
    candles = make_candles([float(i + (i % 3)) for i in range(1, 60)])
    result = add_derived_data(candles)
    last = result[-1]
    assert "macd" in last
    assert "macd_signal" in last
    assert "macd_hist" in last


def test_short_data_returns_none_macd():
    """데이터가 26개 미만이면 MACD가 None"""
    prices = [float(i) for i in range(1, 20)]
    macd_line, _, _ = _macd(prices)
    assert all(v is None for v in macd_line)
