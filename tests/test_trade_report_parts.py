"""
Issue #58: 카카오 리포트에 매도/매수 티커별 확률 상세 추가
"""
import pytest
from services.kakao_service import build_trade_report_parts

LIMIT = 1950


def make_summary(sell_details=None, buy_details=None, **kwargs):
    base = {
        "is_test": True,
        "target_group": "sp500",
        "model_id": "model-uuid",
        "buy_signals": 0,
        "sell_signals": 0,
        "buy_orders": 0,
        "sell_orders": 0,
        "holdings_count": 0,
        "sell_threshold": 0.2,
        "sell_profit_threshold": 20.0,
        "buy_threshold": 0.6,
        "sell_details": sell_details or [],
        "buy_details": buy_details or [],
    }
    base.update(kwargs)
    return base


# ── 기본 구조 ─────────────────────────────────────────


def test_returns_list():
    parts = build_trade_report_parts(make_summary())
    assert isinstance(parts, list)
    assert len(parts) >= 1


def test_part1_contains_summary_fields():
    s = make_summary(buy_signals=3, sell_signals=1, holdings_count=5)
    parts = build_trade_report_parts(s)
    part1 = parts[0]
    assert "매수신호: 3종목" in part1
    assert "매도신호: 1종목" in part1
    assert "보유종목: 5개" in part1


def test_error_summary_returns_single_error_part():
    parts = build_trade_report_parts({"error": "KIS 연결 실패", "is_test": True})
    assert len(parts) == 1
    assert "오류" in parts[0]
    assert "KIS 연결 실패" in parts[0]


# ── 매도 분석 (2부) ───────────────────────────────────


def test_sell_details_appear_in_part2():
    details = [
        {"ticker": "AAPL", "buy_prob": 0.103, "profit_rate": -10.0, "triggered": False, "skip_reason": None},
        {"ticker": "TSLA", "buy_prob": 0.151, "profit_rate": -22.3, "triggered": True, "skip_reason": None},
    ]
    parts = build_trade_report_parts(make_summary(sell_details=details, sell_signals=1))
    combined = "\n".join(parts)
    assert "AAPL" in combined
    assert "TSLA" in combined
    assert "⚠매도신호" in combined  # triggered=True


def test_sell_skip_reason_손실방지():
    details = [
        {"ticker": "META", "buy_prob": 0.05, "profit_rate": -5.0, "triggered": False, "skip_reason": "손실매도방지"},
    ]
    parts = build_trade_report_parts(make_summary(sell_details=details))
    combined = "\n".join(parts)
    assert "🛡손실방지" in combined


def test_sell_detail_no_prob():
    """주가 데이터 없어서 확률 계산 못한 경우"""
    details = [
        {"ticker": "XYZ", "buy_prob": None, "profit_rate": 0.0, "triggered": False, "skip_reason": "데이터없음"},
    ]
    parts = build_trade_report_parts(make_summary(sell_details=details))
    combined = "\n".join(parts)
    assert "XYZ" in combined


# ── 매수 후보 (3부) ───────────────────────────────────


def test_buy_details_top10_appear():
    buy_details = [
        {"ticker": f"T{i:02}", "buy_prob": 0.9 - i * 0.05, "name": ""}
        for i in range(15)  # 15개 → top 10만 표시
    ]
    parts = build_trade_report_parts(make_summary(buy_details=buy_details, buy_signals=15))
    combined = "\n".join(parts)
    assert "TOP10" in combined
    assert "T00" in combined  # 1위
    assert "T09" in combined  # 10위
    assert "T10" not in combined  # 11위는 제외


def test_no_buy_candidates_shows_message():
    parts = build_trade_report_parts(make_summary(buy_signals=0, buy_details=[]))
    combined = "\n".join(parts)
    assert "매수 후보 없음" in combined


# ── 2000자 제한 ──────────────────────────────────────


def test_all_parts_within_2000_chars():
    """각 파트가 2000자를 넘지 않아야 한다."""
    # 보유종목 50개로 긴 리포트 시뮬레이션
    sell_details = [
        {"ticker": f"S{i:03}", "buy_prob": 0.1 + i * 0.01, "profit_rate": -5.0 + i, "triggered": i % 5 == 0, "skip_reason": None}
        for i in range(50)
    ]
    buy_details = [
        {"ticker": f"B{i:03}", "buy_prob": 0.95 - i * 0.01, "name": ""}
        for i in range(10)
    ]
    parts = build_trade_report_parts(make_summary(
        sell_details=sell_details, buy_details=buy_details,
        sell_signals=10, buy_signals=10, holdings_count=50,
    ))
    for i, part in enumerate(parts):
        assert len(part) <= 2000, f"파트 {i+1}이 2000자 초과: {len(part)}자"
