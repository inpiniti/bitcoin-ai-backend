import logging
import traceback
from typing import Any, Dict, Optional
from services.auth_service import get_admin_supabase

logger = logging.getLogger("error_log_service")

def log_error_to_db(phase: str, error: Exception, payload: Optional[Dict[str, Any]] = None):
    """
    Supabase 'error_logs' 테이블에 서버 에러 기록을 저장합니다.
    """
    error_message = str(error)
    error_stack = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    
    try:
        supabase = get_admin_supabase()
        db_payload = {
            "error_message": error_message,
            "error_stack": error_stack,
            "phase": phase,
            "payload": payload
        }
        supabase.table("error_logs").insert(db_payload).execute()
        logger.info(f"[ErrorLog] Successfully logged error to DB. Phase: {phase}")
    except Exception as db_err:
        logger.error(f"[ErrorLog] Failed to write error to DB. Phase: {phase}, DB Error: {db_err}")
        logger.error(f"[Original Error] Message: {error_message}\\nStack: {error_stack}")
