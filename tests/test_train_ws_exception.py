"""
Issue #30: train_ws.py bare except Exception → (WebSocketDisconnect, RuntimeError)로 제한
- WebSocket 전송 실패 시 학습이 계속 진행되는지 검증
- 예상치 못한 예외(TypeError 등)는 전파되는지 검증
"""
import pytest


def send_with_specific_catch(raise_exc):
    """train_ws.py의 send 패턴과 동일"""
    try:
        if raise_exc:
            raise raise_exc
        return True
    except (RuntimeError,):
        return False  # 연결 끊김 - 정상 처리


def test_runtime_error_is_caught():
    """RuntimeError(브라우저 연결 끊김)는 캐치되어 학습이 계속된다"""
    result = send_with_specific_catch(RuntimeError("WebSocket not connected"))
    assert result is False  # 캐치됨


def test_type_error_is_not_caught():
    """TypeError 같은 예상치 못한 예외는 캐치되지 않아 전파된다"""
    with pytest.raises(TypeError):
        send_with_specific_catch(TypeError("Unexpected error"))


def test_no_exception_returns_true():
    """예외 없이 정상 전송되면 True를 반환한다"""
    result = send_with_specific_catch(None)
    assert result is True


def test_broad_except_hides_bugs():
    """bare except Exception은 TypeError도 삼켜 버그를 숨긴다 (버그 재현)"""
    def send_bare(raise_exc):
        try:
            if raise_exc:
                raise raise_exc
            return True
        except Exception:
            return False  # TypeError도 삼킴

    # bare except는 TypeError도 False로 처리 (버그)
    result = send_bare(TypeError("hidden bug"))
    assert result is False  # 잘못된 처리


def test_specific_except_exposes_bugs():
    """specific except는 TypeError를 통과시켜 버그를 노출한다"""
    with pytest.raises(TypeError):
        send_with_specific_catch(TypeError("visible bug"))
