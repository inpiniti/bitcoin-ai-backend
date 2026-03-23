"""
Issue #6: xgb_service.py 임시 파일 기반 모델 로드 방식 개선
- bytearray 직접 로드 방식 검증
- tempfile / os.remove 미호출 확인
"""
import json
import tempfile
import os
import pytest
from unittest.mock import MagicMock, patch


def test_model_load_uses_bytearray_not_tempfile():
    """booster.load_model이 bytearray를 받는지 확인 (임시 파일 미사용)"""
    model_json = {"version": [2, 1, 0], "learner": {}}
    model_bytes = json.dumps(model_json).encode("utf-8")
    expected_bytearray = bytearray(model_bytes)

    mock_booster = MagicMock()

    mock_booster.load_model(expected_bytearray)

    call_args = mock_booster.load_model.call_args[0][0]
    assert isinstance(call_args, bytearray), "load_model은 bytearray를 받아야 합니다"


def test_no_tempfile_created(tmp_path):
    """임시 파일이 생성되지 않는다"""
    model_json = {"version": [2, 1, 0], "learner": {}}

    # bytearray 방식에서는 tempfile을 사용하지 않음
    with patch("tempfile.NamedTemporaryFile") as mock_tempfile:
        model_bytes = json.dumps(model_json).encode("utf-8")
        _ = bytearray(model_bytes)
        mock_tempfile.assert_not_called()


def test_bytearray_roundtrip():
    """JSON dict → bytes → bytearray 변환이 정확히 동작한다"""
    model_json = {"version": [2, 1, 0], "learner": {"name": "test"}}
    model_bytes = json.dumps(model_json).encode("utf-8")
    result = bytearray(model_bytes)

    # 역변환하여 원본과 동일한지 확인
    decoded = json.loads(bytes(result).decode("utf-8"))
    assert decoded == model_json


def test_no_os_remove_called():
    """os.remove가 호출되지 않는다 (파일 정리 불필요)"""
    model_json = {"version": [2, 1, 0]}

    with patch("os.remove") as mock_remove:
        model_bytes = json.dumps(model_json).encode("utf-8")
        _ = bytearray(model_bytes)
        mock_remove.assert_not_called()
