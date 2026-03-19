"""
자동매매 딥러닝 라우터

설정은 클라이언트(AutomationSettingsPanel)에서 Supabase automation_settings 테이블에 직접 저장합니다.
백엔드는 해당 테이블을 읽기만 합니다.

Endpoints:
    POST /auto-trade/run        스케줄러가 호출하는 매매 실행 엔드포인트
    POST /auto-trade/run-test   테스트 모드 실행 (실제 주문 없음)
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

@router.post(
    "/run",
    summary="자동매매 실행 (스케줄러 트리거)",
    description="""
**APScheduler**(평일 15:00 ET)가 자동 호출하거나 외부에서 수동으로 트리거하는 실제 자동매매 엔드포인트입니다.

### 인증
- 헤더 `X-Cron-Secret` 값이 서버 환경변수 `CRON_SECRET`와 일치해야 합니다.
- `CRON_SECRET`가 설정되지 않은 경우 인증을 건너뜁니다.

### 처리 흐름 (백그라운드 실행)
1. Supabase `automation_settings`에서 활성 설정 로드 (KIS 키, 모델 ID, 매매 조건)
2. KIS API 토큰 발급
3. 딥러닝 모델 로드 (Supabase Storage `dl-models` 버킷)
4. KIS 잔고 조회 → 보유 종목 파악
5. 대상 그룹(S&P500 / QQQ / 슈퍼인베스터 / 보유종목) 티커 로드
6. 티커별 주가 데이터 수집 + 기술적 지표 계산
7. 딥러닝 모델로 **매수 신호** 스캔 (buy_probability ≥ buy_threshold)
8. 딥러닝 모델로 **매도 신호** 스캔 (sell_probability ≥ sell_threshold, 보유 종목 대상)
9. 매도 주문 선행 실행
10. 가용 현금을 매수 후보 수로 **균등 분배** 후 매수 실행
11. 실행 로그를 Supabase `auto_trade_dl_logs` 테이블에 저장

> **trade_enabled = false** 이면 실제 KIS 주문 없이 로그만 기록합니다 (모의매매 모드).

### 응답
요청 즉시 `triggered` 반환 — 매매 로직은 **백그라운드**에서 실행됩니다.
""",
)
async def run_auto_trade(
    x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret"),
):
    _verify_cron(x_cron_secret)

    import asyncio
    asyncio.ensure_future(auto_trade_service.run_auto_trade_dl(is_test=False))
    return {"status": "triggered", "message": "자동매매 플로우가 백그라운드에서 시작되었습니다."}


@router.post(
    "/run-test",
    summary="자동매매 테스트 실행",
    description="""
자동매매 로직을 **동기 실행**하여 결과를 즉시 반환합니다.

- 실제 KIS 매수·매도 주문은 발생하지 않습니다 (`is_test=True`).
- `trade_enabled` 설정과 무관하게 항상 모의매매로 동작합니다.
- 응답에 전체 실행 로그와 매수·매도 후보 목록이 포함되어 디버깅에 활용할 수 있습니다.

> 실행 시간이 수십 초~수 분 소요될 수 있습니다 (분석 대상 종목 수에 따라 다름).
""",
)
async def run_auto_trade_test(
    x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret"),
):
    _verify_cron(x_cron_secret)
    try:
        result = await auto_trade_service.run_auto_trade_dl(is_test=True)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# 조회
# ─────────────────────────────────────────────

@router.get(
    "/settings",
    summary="현재 활성 자동매매 설정 조회",
    description="""
Supabase `automation_settings` 테이블에서 `is_active = true`인 설정을 조회합니다.

### 포함 정보
- KIS 앱키 / 계좌번호 (시크릿은 마지막 4자리만 표시)
- 사용할 딥러닝 모델 ID
- 분석 대상 티커 그룹 (sp500 / qqq / superinvestor / myholdings)
- 매수 조건 확률 임계값, 매도 조건 확률 임계값
- 실제매매 활성화 여부 (`trade_enabled`)

> 설정이 없으면 `active: false`를 반환합니다.
""",
)
async def get_active_settings():
    cfg = await load_automation_settings_active()
    if not cfg:
        return {"active": False, "message": "활성화된 설정이 없습니다."}
    if cfg.get("kis_secret"):
        cfg["kis_secret"] = "****" + cfg["kis_secret"][-4:]
    return {"active": True, "settings": cfg}


@router.post(
    "/reschedule",
    summary="자동매매 스케줄 재설정",
    description="""
Supabase `automation_settings`의 활성 설정에서 `execution_time`을 읽어 스케줄을 재등록합니다.

클라이언트에서 설정을 변경한 직후 호출하면 서버 재시작 없이 즉시 반영됩니다.
""",
)
async def reschedule_auto_trade(
    x_cron_secret: Optional[str] = Header(None, alias="X-Cron-Secret"),
):
    _verify_cron(x_cron_secret)
    try:
        from main import reschedule_from_settings
        result = await reschedule_from_settings()
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/logs",
    summary="자동매매 실행 로그 조회",
    description="""
Supabase `auto_trade_dl_logs` 테이블에서 최근 자동매매 실행 기록을 조회합니다.

### 응답 항목 (1건 기준)
- `date`: 실행 날짜
- `model_id`: 사용된 딥러닝 모델 ID
- `target_group`: 분석 대상 그룹
- `holdings_count`: 보유 종목 수
- `buy_signals` / `sell_signals`: 신호 발생 종목 수
- `buy_orders` / `sell_orders`: 실제(또는 모의) 주문 건수
- `logs`: 실행 중 발생한 상세 로그 배열
- `error`: 오류 발생 시 오류 메시지

### 쿼리 파라미터
- `limit`: 조회할 최대 건수 (기본값: 30)
""",
)
async def get_logs(limit: int = 30):
    return await get_auto_trade_logs(limit=limit)
