"""
인증 라우터 — KIS 로그인 기반 멀티유저.

POST /auth/kis-login
  계좌번호 + appkey + appsecret 을 받아서
  1) KIS 토큰 발급으로 본인 인증
  2) user_id = uuid5(계좌번호) 생성
  3) kis_credentials / websocket_keys 를 사용자별 1건으로 저장 (서버 자동매매·감지용)
  4) Supabase RLS용 커스텀 JWT 발급 → 앱이 이 토큰으로 Supabase 접속
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import auth_service, kis_service
from services.websocket_service import issue_websocket_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class KisLoginRequest(BaseModel):
    account_no: str
    appkey: str
    appsecret: str


@router.post(
    "/kis-login",
    summary="KIS 자격증명으로 로그인 → Supabase RLS용 JWT 발급",
    description=(
        "appkey/appsecret으로 KIS 토큰 발급에 성공하면 본인으로 인증하고, "
        "계좌번호 기반 user_id에 대한 Supabase 커스텀 JWT를 발급한다. "
        "발급된 access_token으로 앱이 Supabase에 접속하면 RLS가 사용자별로 적용된다."
    ),
)
async def kis_login(body: KisLoginRequest):
    account_no = (body.account_no or "").strip()
    appkey = (body.appkey or "").strip()
    appsecret = (body.appsecret or "").strip()
    if not all([account_no, appkey, appsecret]):
        raise HTTPException(status_code=400, detail="account_no, appkey, appsecret를 모두 입력하세요")

    cano, _prdt = kis_service.parse_account(account_no)

    # 1. KIS 인증 검증 — 토큰 발급 성공 = appsecret을 아는 본인
    try:
        await kis_service.get_access_token(appkey, appsecret)
    except Exception as e:
        logger.warning(f"[auth] KIS 인증 실패 (account=...{cano[-2:]}): {e}")
        raise HTTPException(status_code=401, detail="KIS 인증 실패 — 계좌번호/AppKey/시크릿키를 확인하세요")

    user_id = auth_service.user_id_for_account(cano)
    now_iso = datetime.now(timezone.utc).isoformat()

    sb = auth_service.get_admin_supabase()

    # 2. KIS 자격증명 저장 (사용자별 1건 유지)
    try:
        sb.table("kis_credentials").delete().eq("user_id", user_id).execute()
        sb.table("kis_credentials").insert({
            "user_id": user_id,
            "account_no": account_no,
            "appkey": appkey,
            "appsecret": appsecret,
            "created_at": now_iso,
            "updated_at": now_iso,
        }).execute()
    except Exception as e:
        logger.error(f"[auth] kis_credentials 저장 실패: {e}")
        raise HTTPException(status_code=500, detail="자격증명 저장에 실패했습니다")

    # 3. 웹소켓 approval_key 발급 + 저장 (실패해도 로그인은 계속 — 실시간 매매 사용 시에만 필요)
    try:
        approval_key = await issue_websocket_key(appkey, appsecret)
        if approval_key:
            expires_iso = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
            sb.table("websocket_keys").delete().eq("user_id", user_id).execute()
            sb.table("websocket_keys").insert({
                "user_id": user_id,
                "approval_key": approval_key,
                "issued_at": now_iso,
                "expires_at": expires_iso,
            }).execute()
        else:
            logger.warning("[auth] approval_key 발급 결과가 비어 있음 (실시간 매매 시 재발급 필요)")
    except Exception as e:
        logger.warning(f"[auth] websocket_keys 저장 실패(무시): {e}")

    # 4. Supabase RLS용 커스텀 JWT 발급
    try:
        access_token = auth_service.sign_supabase_jwt(user_id)
    except Exception as e:
        logger.error(f"[auth] JWT 서명 실패: {e}")
        raise HTTPException(status_code=500, detail="인증 토큰 발급에 실패했습니다 (서버 설정 확인 필요)")

    logger.info(f"[auth] 로그인 성공 user_id={user_id} (account=...{cano[-2:]})")
    return {
        "user_id": user_id,
        "access_token": access_token,
        "account_no": account_no,
    }
