"""
실적발표 자동매매 — API 통신 로그 서비스

Supabase earnings_api_logs 테이블에 기록한다.
  시간 | api | payload | response | inout | status | error_message

  inout: 'in' (클라이언트→서버) | 'out' (서버→외부 API 호출)
  status: 'success' | 'error' | 'empty' (빈 배열 리턴)
"""
import json
import logging
from typing import Optional, Any

from services.auth_service import get_admin_supabase

logger = logging.getLogger("earnings_logger")


async def log_earnings_api(
    api: str,
    inout: str,
    payload: Optional[Any] = None,
    response: Optional[Any] = None,
    status: str = "success",
    error_message: Optional[str] = None,
) -> None:
    """
    API 통신 로그를 Supabase에 기록한다.

    Args:
        api: 엔드포인트 또는 외부 API 이름 (예: "/api/earnings/history/collect", "yfinance.Ticker(AAPL)")
        inout: 'in' (클라이언트 요청) | 'out' (서버 내부 호출)
        payload: 요청 페이로드
        response: 응답 (에러 시 None)
        status: 'success' | 'error' | 'empty'
        error_message: 에러 메시지 (status='error'일 때 기록)
    """
    try:
        sb = get_admin_supabase()

        # JSONB는 직렬화 가능해야 함
        def make_serializable(obj: Any) -> Any:
            if obj is None:
                return None
            if isinstance(obj, (dict, list)):
                return obj
            try:
                json.dumps(obj)
                return obj
            except (TypeError, ValueError):
                return str(obj)

        row = {
            "api": api,
            "inout": inout,
            "payload": make_serializable(payload),
            "response": make_serializable(response),
            "status": status,
            "error_message": error_message,
        }

        sb.table("earnings_api_logs").insert(row).execute()
        logger.debug(f"[Log] {inout.upper()} {api} {status}")
    except Exception as e:
        logger.error(f"[Log] 기록 실패 {api}: {e}")
        # 로그 기록 실패는 API 실행을 막지 않음
        pass
