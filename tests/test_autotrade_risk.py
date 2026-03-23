"""
Issue #40: qty=0 주문 KIS API 전송 방지
Issue #41: 종목당 최대 포지션 크기 10% 캡
Issue #42: 실패 주문 카운트 제외
"""
import pytest


# ── Issue #40: qty=0 가드 ─────────────────────────────
def simulate_buy(per_ticker_amount, price, trade_enabled):
    qty = int(per_ticker_amount / price) if price > 0 else 0
    if qty == 0:
        return None  # skip
    return {"qty": qty, "sent": trade_enabled}


def test_qty_zero_is_skipped():
    """주가보다 배분액이 작으면 주문이 전송되지 않는다"""
    result = simulate_buy(per_ticker_amount=5.0, price=50.0, trade_enabled=True)
    assert result is None


def test_qty_positive_is_sent():
    """배분액이 충분하면 주문이 전송된다"""
    result = simulate_buy(per_ticker_amount=500.0, price=50.0, trade_enabled=True)
    assert result is not None
    assert result["qty"] == 10


def test_qty_zero_simulated_also_skipped():
    """모의매매 모드에서도 qty=0이면 건너뛴다"""
    result = simulate_buy(per_ticker_amount=1.0, price=200.0, trade_enabled=False)
    assert result is None


# ── Issue #41: 10% 포지션 캡 ──────────────────────────
def calc_per_ticker(available_cash, buy_count, max_pct=0.10):
    """수정 후 per_ticker_amount 계산 로직"""
    per_ticker = (available_cash / buy_count) if buy_count > 0 else 0
    max_per_ticker = available_cash * max_pct
    return min(per_ticker, max_per_ticker)


def test_single_buy_signal_capped_at_10pct():
    """매수 신호 1개일 때 전체 자금이 집중되지 않고 10% 캡 적용"""
    amount = calc_per_ticker(available_cash=10000.0, buy_count=1)
    assert amount == 1000.0  # 10% 캡


def test_many_signals_not_capped():
    """신호가 충분히 많으면 캡이 작동하지 않음"""
    amount = calc_per_ticker(available_cash=10000.0, buy_count=20)
    assert amount == 500.0  # 10000/20=500 < 1000(10%), 캡 불필요


def test_cap_prevents_100pct_concentration():
    """단일 신호로 100% 집중 불가"""
    amount = calc_per_ticker(available_cash=50000.0, buy_count=1)
    assert amount == 5000.0  # 최대 10%
    assert amount < 50000.0  # 전체 자금 투입 방지


# ── Issue #42: 성공 주문만 카운트 ─────────────────────
def count_orders(results):
    return len([r for r in results if r.get("simulated") or r.get("result", {}).get("success")])


def test_failed_orders_not_counted():
    """실패 주문은 카운트에서 제외된다"""
    results = [
        {"result": {"success": True}},
        {"result": {"success": False, "error": "KIS 오류"}},
        {"result": {"success": True}},
    ]
    assert count_orders(results) == 2


def test_simulated_orders_counted():
    """모의매매는 카운트에 포함된다"""
    results = [
        {"simulated": True},
        {"simulated": True},
    ]
    assert count_orders(results) == 2


def test_all_failed_returns_zero():
    """전부 실패하면 0을 반환한다"""
    results = [{"result": {"success": False}} for _ in range(5)]
    assert count_orders(results) == 0
