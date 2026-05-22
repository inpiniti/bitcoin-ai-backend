"""
KIS 로그인 기반 멀티유저 인증.

설계:
  - KIS appkey/appsecret으로 토큰 발급에 성공하면 "본인"으로 인증 (별도 회원가입 없음)
  - 계좌번호(CANO)로부터 결정적 user_id(uuid5)를 생성 → 같은 계좌면 항상 같은 ID
  - Supabase Legacy JWT Secret으로 RLS용 커스텀 JWT(HS256)를 서명해 앱에 발급
    → 앱이 그 JWT로 Supabase에 접속하면 RLS의 auth.uid()가 user_id로 작동

의존성 없이 표준 라이브러리로 HS256 서명만 수행한다 (검증은 Supabase가 담당).
"""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

# 계좌번호(CANO) → user_id 매핑용 고정 namespace.
# ⚠️ 절대 변경 금지 — 바뀌면 기존 모든 사용자의 user_id가 달라져 데이터와 연결이 끊긴다.
_USER_NAMESPACE = uuid.UUID("7b3e5c9a-1f2d-4a6b-8c0e-9d1a2b3c4d5e")

# 커스텀 JWT 유효기간 (재로그인 전까지). 소수 사용자 환경이라 길게 둔다.
_JWT_TTL_SECONDS = 30 * 24 * 3600  # 30일


def user_id_for_account(cano: str) -> str:
    """계좌번호(8자리 CANO)로부터 결정적 UUID(user_id) 생성."""
    return str(uuid.uuid5(_USER_NAMESPACE, str(cano).strip()))


def _b64url(raw: bytes) -> str:
    """JWT용 base64url 인코딩 (패딩 제거)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_supabase_jwt(user_id: str, ttl_seconds: int = _JWT_TTL_SECONDS) -> str:
    """Supabase Legacy JWT Secret으로 RLS용 HS256 JWT를 서명.

    클레임:
      - sub:  user_id  (RLS의 auth.uid()가 읽는 값)
      - role/aud: 'authenticated'  (Supabase가 인증된 사용자로 인식)
    """
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        raise RuntimeError("SUPABASE_JWT_SECRET 환경변수가 설정되어 있지 않습니다")

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "role": "authenticated",
        "aud": "authenticated",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    segments = [
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segments).encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    segments.append(_b64url(signature))
    return ".".join(segments)


def verify_supabase_jwt(token: str) -> str | None:
    """발급한 JWT의 HS256 서명·만료를 검증하고 sub(user_id)를 반환. 실패 시 None."""
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret or not token:
        return None
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        return None
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = _b64url(
        hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected_sig, sig_b64):
        return None
    try:
        pad = "=" * ((4 - len(payload_b64) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload.get("sub")


def get_supabase_env():
    """Supabase URL과 키를 반환 (service_role 우선·anon 폴백)."""
    url = os.environ.get("VITE_SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("VITE_SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    return url, key


def get_admin_supabase():
    """RLS를 우회하는 Supabase 클라이언트.

    SERVICE_ROLE_KEY가 있으면 그것을(RLS 우회), 없으면 ANON_KEY로 폴백한다.
    RLS를 켜기 전(전환 기간)에는 ANON_KEY로도 동작하고, RLS를 켠 뒤에는
    HF Secrets에 SUPABASE_SERVICE_ROLE_KEY를 넣어주면 자동으로 승격된다.
    """
    from supabase import create_client

    url, key = get_supabase_env()
    if not url or not key:
        raise RuntimeError("Supabase 환경변수(URL/KEY)가 설정되어 있지 않습니다")
    return create_client(url, key)
