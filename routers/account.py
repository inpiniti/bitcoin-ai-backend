"""
계좌 조회 라우터

GET /account/domestic-balance
  국내주식 잔고 조회 (KIS TTTC8434R)

GET /account/overseas-balance
  해외주식 잔고 조회 (KIS CTRP6504R)
"""
import logging
from fastapi import APIRouter, HTTPException, Header
from typing import Optional

from services import auth_service, kis_service

logger = logging.getLogger("account_router")
router = APIRouter(prefix="/account", tags=["account"])


async def _get_user_kis_creds(user_id: str) -> dict:
    """Supabase에서 사용자의 KIS 자격증명 조회"""
    sb = auth_service.get_admin_supabase()
    try:
        res = (
            sb.table("kis_credentials")
            .select("*")
            .eq("user_id", user_id)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if rows:
            return rows[0]
    except Exception as e:
        logger.error(f"[account] kis_credentials 조회 실패 user={user_id}: {e}")
    return None


@router.get(
    "/domestic-balance",
    summary="국내주식 잔고 조회",
    description="로그인한 사용자의 국내주식 잔고를 반환합니다 (KIS TTTC8434R)",
)
async def get_domestic_balance(authorization: Optional[str] = Header(None)):
    """
    국내주식 잔고 조회

    Response:
    {
      "success": bool,
      "error": str (오류 시만 존재),
      "holdings": [
        {
          "pdno": "005930",
          "prdt_name": "삼성전자",
          "hldg_qty": "10",
          "pchs_avg_pric": "50000",
          "prpr": "55000",
          "evlu_amt": "550000",
          "evlu_pfls_amt": "50000",
          "evlu_pfls_rt": "9.09",
          ...
        }
      ],
      "summary": {
        "dnca_tot_amt": "100000",
        "tot_evlu_amt": "650000",
        "nass_amt": "750000",
        ...
      }
    }
    """
    token = authorization.replace("Bearer ", "") if authorization else ""
    try:
        user_id = auth_service.verify_supabase_jwt(token)
    except Exception:
        raise HTTPException(status_code=401, detail="인증 토큰이 유효하지 않습니다")

    creds = await _get_user_kis_creds(user_id)
    if not creds:
        raise HTTPException(status_code=400, detail="KIS 자격증명이 없습니다. 먼저 KIS 로그인을 하세요")

    account_no, account_code = kis_service.parse_account(creds["account_no"])
    try:
        result = await kis_service.get_domestic_balance(
            creds["appkey"],
            creds["appsecret"],
            account_no,
            account_code,
        )
        return result
    except Exception as e:
        logger.error(f"[account] 국내 잔고 조회 실패 user={user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"잔고 조회 중 오류: {str(e)}")


@router.get(
    "/overseas-balance",
    summary="해외주식 잔고 조회",
    description="로그인한 사용자의 해외주식 잔고를 반환합니다 (KIS CTRP6504R)",
)
async def get_overseas_balance(authorization: Optional[str] = Header(None)):
    """
    해외주식 잔고 조회

    Response:
    {
      "success": bool,
      "error": str (오류 시만 존재),
      "holdings": [...],
      "summary": {...},
      "usd_available": 123.45
    }
    """
    token = authorization.replace("Bearer ", "") if authorization else ""
    try:
        user_id = auth_service.verify_supabase_jwt(token)
    except Exception:
        raise HTTPException(status_code=401, detail="인증 토큰이 유효하지 않습니다")

    creds = await _get_user_kis_creds(user_id)
    if not creds:
        raise HTTPException(status_code=400, detail="KIS 자격증명이 없습니다. 먼저 KIS 로그인을 하세요")

    account_no, account_code = kis_service.parse_account(creds["account_no"])
    try:
        result = await kis_service.get_overseas_balance(
            creds["appkey"],
            creds["appsecret"],
            account_no,
            account_code,
        )
        return result
    except Exception as e:
        logger.error(f"[account] 해외 잔고 조회 실패 user={user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"잔고 조회 중 오류: {str(e)}")
