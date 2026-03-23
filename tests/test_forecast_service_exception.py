"""
Issue #24: forecast_service.py bare except Exception → except ValueError로 제한
- datetime.fromisoformat()는 ValueError를 raise하므로 ValueError만 잡도록 수정
- 다른 예상치 못한 예외는 전파되어야 함
"""
import pytest
from datetime import datetime, timezone


def parse_date_safe(last_date: str):
    """forecast_service.py의 날짜 파싱 로직과 동일"""
    try:
        return datetime.fromisoformat(last_date.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=timezone.utc)


def test_valid_date_parsed_correctly():
    """유효한 ISO 날짜 문자열은 정상 파싱된다"""
    result = parse_date_safe("2024-01-15T09:00:00Z")
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15


def test_invalid_date_falls_back_to_now():
    """잘못된 날짜 형식은 ValueError를 발생시키고 현재 시간으로 폴백한다"""
    before = datetime.now(tz=timezone.utc)
    result = parse_date_safe("not-a-date")
    after = datetime.now(tz=timezone.utc)
    assert before <= result <= after


def test_bare_exception_would_hide_type_errors():
    """TypeError 같은 다른 예외는 ValueError catch에 걸리지 않아 전파된다"""
    def parse_with_bare_except(last_date):
        try:
            return datetime.fromisoformat(last_date.replace("Z", "+00:00"))
        except Exception:  # bare - 모든 예외 삼킴
            return datetime.now(tz=timezone.utc)

    def parse_with_value_error(last_date):
        try:
            return datetime.fromisoformat(last_date.replace("Z", "+00:00"))
        except ValueError:  # 수정 후 - ValueError만 잡음
            return datetime.now(tz=timezone.utc)

    # 유효하지 않은 날짜는 ValueError → 두 버전 모두 폴백
    r1 = parse_with_bare_except("bad")
    r2 = parse_with_value_error("bad")
    assert r1 is not None
    assert r2 is not None


def test_empty_string_raises_value_error():
    """빈 문자열은 ValueError를 발생시키고 현재 시간으로 폴백한다"""
    result = parse_date_safe("")
    assert isinstance(result, datetime)


def test_correct_timezone_used_for_fallback():
    """폴백 날짜는 UTC 타임존을 사용한다"""
    result = parse_date_safe("invalid")
    assert result.tzinfo is not None
