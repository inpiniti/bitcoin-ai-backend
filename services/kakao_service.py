"""
카카오톡 나에게 보내기 서비스

access_token 만료 시 refresh_token으로 자동 갱신 후 Supabase 업데이트.
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger("kakao_service")

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_MESSAGE_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def _rest_api_key() -> str:
    return os.environ.get("KAKAO_REST_API_KEY", "")


async def _refresh_token(refresh_token: str) -> dict | None:
    """refresh_token으로 access_token 갱신. 성공 시 새 토큰 dict 반환."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(KAKAO_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": _rest_api_key(),
            "refresh_token": refresh_token,
        })
    if resp.status_code != 200:
        logger.warning(f"[Kakao] 토큰 갱신 실패: {resp.text}")
        return None
    data = resp.json()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 21600))
    ).isoformat()
    return {
        "kakao_access_token": data["access_token"],
        "kakao_refresh_token": data.get("refresh_token", refresh_token),  # 갱신 안되면 기존 유지
        "kakao_token_expires_at": expires_at,
    }


async def _update_tokens_in_supabase(config_id, tokens: dict):
    """갱신된 토큰을 automation_settings 에 업데이트."""
    from services.supabase_service import _headers, _check_config, SUPABASE_URL
    _check_config()
    url = f"{SUPABASE_URL}/rest/v1/automation_settings?id=eq.{config_id}"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.patch(url, json=tokens, headers=_headers())


async def _send_message(access_token: str, text: str) -> bool:
    """나에게 보내기 API 호출."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            KAKAO_MESSAGE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": json.dumps({
                "object_type": "text",
                "text": text[:2000],  # 카카오 최대 2000자
                "link": {"web_url": "https://kakao.com"},
            })},
        )
    if resp.status_code != 200:
        logger.warning(f"[Kakao] 메시지 전송 실패 ({resp.status_code}): {resp.text}")
        return False
    logger.info("[Kakao] 메시지 전송 완료")
    return True


async def send_trade_report(cfg: dict, report_text: str) -> bool:
    """
    자동매매 리포트를 카카오톡으로 전송.
    토큰 만료 임박 시 자동 갱신 후 Supabase 업데이트.

    Args:
        cfg: automation_settings 행 (kakao_access_token, kakao_refresh_token, kakao_token_expires_at 포함)
        report_text: 전송할 메시지 내용
    """
    access_token = (cfg.get("kakao_access_token") or "").strip()
    refresh_token = (cfg.get("kakao_refresh_token") or "").strip()

    if not access_token:
        logger.info("[Kakao] access_token 없음, 카카오 전송 스킵")
        return False

    # 만료 5분 전이면 갱신
    expires_at_str = cfg.get("kakao_token_expires_at")
    if expires_at_str:
        try:
            exp_dt = datetime.fromisoformat(expires_at_str)
            if datetime.now(timezone.utc) >= exp_dt - timedelta(minutes=5):
                logger.info("[Kakao] access_token 만료 임박, 갱신 시도")
                new_tokens = await _refresh_token(refresh_token)
                if new_tokens:
                    access_token = new_tokens["kakao_access_token"]
                    await _update_tokens_in_supabase(cfg.get("id"), new_tokens)
                else:
                    logger.warning("[Kakao] 토큰 갱신 실패, 기존 토큰으로 시도")
        except Exception as e:
            logger.warning(f"[Kakao] 만료 확인 오류: {e}")

    return await _send_message(access_token, report_text)


def build_trade_report(summary: dict, mode: str = "") -> str:
    """자동매매 결과를 카카오 메시지 포맷으로 변환."""
    now_kst = datetime.now(timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Seoul")
    )
    date_str = now_kst.strftime("%Y.%m.%d %H:%M KST")
    label = "🔵 모의매매" if summary.get("is_test") else "🟠 실매매"

    buy_signals = summary.get("buy_signals", 0)
    sell_signals = summary.get("sell_signals", 0)
    buy_orders = summary.get("buy_orders", 0)
    sell_orders = summary.get("sell_orders", 0)
    holdings = summary.get("holdings_count", 0)
    group = summary.get("target_group", "-")
    model = summary.get("model_id", "-")
    error = summary.get("error")

    if error:
        return (
            f"❌ 자동매매 오류 ({date_str})\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"오류: {error}"
        )

    lines = [
        f"📊 자동매매 리포트 {mode}",
        f"🕐 {date_str}",
        f"━━━━━━━━━━━━━━━━",
        f"{label} | 그룹: {group}",
        f"",
        f"📈 매수신호: {buy_signals}종목 → 주문: {buy_orders}건",
        f"📉 매도신호: {sell_signals}종목 → 주문: {sell_orders}건",
        f"💼 보유종목: {holdings}개",
        f"",
        f"🤖 모델: {model}",
    ]
    return "\n".join(lines)
