"""
자동매매 딥러닝 라우터

설정은 클라이언트(AutomationSettingsPanel)에서 Supabase automation_settings 테이블에 직접 저장합니다.
백엔드는 해당 테이블을 읽기만 합니다.

Endpoints:
    POST /auto-trade/run        Vercel Cron 이 호출하는 매매 실행 엔드포인트
    POST /auto-trade/run-test   테스트 모드 실행 (주문 수량 0)
    GET  /auto-trade/logs       실행 로그 조회
    GET  /auto-trade/settings   현재 활성 설정 확인 (읽기 전용)
"""
import logging
import os
from fastapi import APIRouter, HTTPException, Header
from typing import Optional

from services import auto_trade_service
from services.supabase_service import load_automation_settings_active, get_auto_trade_logs

logger = logging.getLogger("auto_trade_router")
router = APIRouter(prefix="/auto-trade", tags=["auto-trade"])

CRON_SECRET = os.environ.get("CRON_SECRET", "")


def _verify_cron(x_cron_secret: Optional[str]):
    """CRON_SECRET 설정된 경우에만 검증. 미설정이면 검증 스킵."""
    if CRON_SECRET and x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized: CRON_SECRET 불일치")


# ─────────────────────────────────────────────
# 매매 실행
# ─────────────────────────────────────────────

@router.post("/run")
async def run_auto_trade(
    x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret"),
):
    """
    Vercel Cron 이 호출하는 엔드포인트.
    실제 매매 로직은 BackgroundTask 로 실행하여 즉시 응답 반환.
    """
    _verify_cron(x_cron_secret)

    # BackgroundTasks 없이 직접 실행 후 즉시 응답
    # (FastAPI BackgroundTasks 는 응답 전송 후 실행되므로, Cron 입장에선 트리거 성공으로 처리됨)
    import asyncio
    asyncio.ensure_future(auto_trade_service.run_auto_trade_dl(is_test=False))
    return {"status": "triggered", "message": "자동매매 플로우가 백그라운드에서 시작되었습니다."}


@router.post("/run-test")
async def run_auto_trade_test(
    x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret"),
):
    """테스트 모드 실행 (실제 주문 없음, 동기 실행으로 결과 즉시 반환)"""
    _verify_cron(x_cron_secret)
    try:
        result = await auto_trade_service.run_auto_trade_dl(is_test=True)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# 조회
# ─────────────────────────────────────────────

@router.get("/settings")
async def get_active_settings():
    """현재 활성화된 automation_settings 확인 (KIS 시크릿은 마스킹)"""
    cfg = await load_automation_settings_active()
    if not cfg:
        return {"active": False, "message": "활성화된 설정이 없습니다."}
    # 시크릿 마스킹
    if cfg.get("kis_secret"):
        cfg["kis_secret"] = "****" + cfg["kis_secret"][-4:]
    return {"active": True, "settings": cfg}


@router.get("/logs")
async def get_logs(limit: int = 30):
    """자동매매 실행 로그 조회"""
    return await get_auto_trade_logs(limit=limit)
