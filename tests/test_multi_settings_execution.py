"""
Issue #57: auto-trade/run-test가 is_active=true 설정 중 첫 번째 1개만 실행됨

설계 의도: is_active=true 설정이 N개면 N번 독립 실행되어야 한다.
버그 원인: load_automation_settings_active()가 limit=1 로 단 1개만 반환.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── 헬퍼: 최소한의 유효한 설정 딕셔너리 생성 ──────────────────────────────


def make_cfg(name: str) -> dict:
    return {
        "id": name,
        "name": name,
        "is_active": True,
        "kis_appkey": "APPKEY",
        "kis_secret": "SECRET",
        "kis_account": "12345678-01",
        "ai_model_key": "model-uuid",
        "ticker_group_key": "sp500",
        "buy_condition": 60,
        "sell_condition": 20,
        "sell_profit_condition": 20,
        "prevent_loss_sell": False,
        "trade_enabled": False,  # 모의매매 – 실제 주문 없음
    }


def _make_balance_res():
    return {"success": True, "holdings": [], "usd_available": 0.0}


# ── Issue #57: 복수 설정 모두 실행 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_active_settings_are_executed():
    """
    is_active=true 설정이 2개일 때 각 설정에 대해 KIS 잔고 조회가 2회 이루어져야 한다.

    수정 전(버그): load_automation_settings_active()가 1개만 반환 → 1회 실행
    수정 후(기대): load_all_automation_settings_active()가 전체 반환 → 2회 실행
    """
    cfg_a = make_cfg("설정A")
    cfg_b = make_cfg("설정B")

    from services import auto_trade_service

    mock_model = MagicMock()
    mock_meta = {"feature_count": 5, "accuracy": 0.8}

    with patch(
        "services.auto_trade_service.load_all_automation_settings_active",
        new=AsyncMock(return_value=[cfg_a, cfg_b]),
    ), patch(
        "services.auto_trade_service.dl_model_service.get_model",
        new=AsyncMock(return_value=(mock_meta, mock_model)),
    ), patch(
        "services.auto_trade_service.kis_service.get_overseas_balance",
        new=AsyncMock(return_value=_make_balance_res()),
    ) as mock_balance, patch(
        "services.auto_trade_service.kis_service.parse_account",
        return_value=("12345678", "01"),
    ), patch(
        "services.auto_trade_service._load_target_tickers",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.auto_trade_service.save_auto_trade_log",
        new=AsyncMock(),
    ):
        results = await auto_trade_service.run_auto_trade_dl(is_test=True)

    # 설정 2개 → 잔고 조회 2회
    assert mock_balance.call_count == 2, (
        f"설정 2개에 대해 KIS 잔고 조회가 2회 이루어져야 하지만 {mock_balance.call_count}회 발생함. "
        "load_all_automation_settings_active()와 루프 처리를 확인하세요."
    )
    # 결과도 2개 반환
    assert len(results) == 2


@pytest.mark.asyncio
async def test_single_active_setting_still_works():
    """기존 동작 유지: 설정 1개일 때 1회 실행, 결과 1개 반환"""
    cfg = make_cfg("설정Single")

    from services import auto_trade_service

    mock_model = MagicMock()
    mock_meta = {"feature_count": 5, "accuracy": 0.8}

    with patch(
        "services.auto_trade_service.load_all_automation_settings_active",
        new=AsyncMock(return_value=[cfg]),
    ), patch(
        "services.auto_trade_service.dl_model_service.get_model",
        new=AsyncMock(return_value=(mock_meta, mock_model)),
    ), patch(
        "services.auto_trade_service.kis_service.get_overseas_balance",
        new=AsyncMock(return_value=_make_balance_res()),
    ) as mock_balance, patch(
        "services.auto_trade_service.kis_service.parse_account",
        return_value=("12345678", "01"),
    ), patch(
        "services.auto_trade_service._load_target_tickers",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.auto_trade_service.save_auto_trade_log",
        new=AsyncMock(),
    ):
        results = await auto_trade_service.run_auto_trade_dl(is_test=True)

    assert mock_balance.call_count == 1
    assert len(results) == 1


@pytest.mark.asyncio
async def test_no_active_settings_raises():
    """is_active=true 설정이 없으면 RuntimeError"""
    from services import auto_trade_service

    with patch(
        "services.auto_trade_service.load_all_automation_settings_active",
        new=AsyncMock(return_value=[]),
    ):
        with pytest.raises(RuntimeError, match="is_active=true"):
            await auto_trade_service.run_auto_trade_dl(is_test=True)


@pytest.mark.asyncio
async def test_one_cfg_failure_does_not_abort_others():
    """한 설정 실행이 실패해도 나머지 설정은 계속 실행된다."""
    cfg_a = make_cfg("설정A_실패")
    cfg_b = make_cfg("설정B_성공")

    from services import auto_trade_service

    mock_model = MagicMock()
    mock_meta = {"feature_count": 5, "accuracy": 0.8}

    call_count = 0

    async def balance_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("KIS 연결 오류 (설정A 시뮬레이션)")
        return _make_balance_res()

    with patch(
        "services.auto_trade_service.load_all_automation_settings_active",
        new=AsyncMock(return_value=[cfg_a, cfg_b]),
    ), patch(
        "services.auto_trade_service.dl_model_service.get_model",
        new=AsyncMock(return_value=(mock_meta, mock_model)),
    ), patch(
        "services.auto_trade_service.kis_service.get_overseas_balance",
        new=AsyncMock(side_effect=balance_side_effect),
    ), patch(
        "services.auto_trade_service.kis_service.parse_account",
        return_value=("12345678", "01"),
    ), patch(
        "services.auto_trade_service._load_target_tickers",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.auto_trade_service.save_auto_trade_log",
        new=AsyncMock(),
    ):
        results = await auto_trade_service.run_auto_trade_dl(is_test=True)

    # 2개 결과 반환 (하나는 error 포함, 하나는 정상)
    assert len(results) == 2
    assert "error" in results[0]   # 설정A 실패 결과
    assert "error" not in results[1]  # 설정B 성공 결과
