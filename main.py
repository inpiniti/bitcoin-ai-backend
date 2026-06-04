"""
bitcoin-ai-backend - FastAPI 기반 백엔드
Motia(Node.js) 제거, 순수 Python + FastAPI + uvicorn 구성
"""
# numpy 2.x pickle 호환성 shim (반드시 최상단 — 다른 import보다 먼저)
from services import numpy_compat  # noqa: F401

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from routers import forecast, whale, xgb, market_cap, auto_trade, train_ws, gemini, youtube, rl, sp500, portfolio, test, realtime, auth, account, company_analysis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("main")

scheduler = AsyncIOScheduler()

# ── 미장 기준 상대 시간 매핑 (ET 기준, DST 자동 처리) ──
# 미국 정규장: 09:30 ~ 16:00 ET
MARKET_TIME_MAP: dict[str, tuple[int, int]] = {
    "market_open":      (9,  30),  # 장 시작
    "market_open_30m":  (10,  0),  # 장 시작 30분 후
    "market_open_1h":   (10, 30),  # 장 시작 1시간 후
    "market_close_2h":  (14,  0),  # 장 마감 2시간 전
    "market_close_1h":  (15,  0),  # 장 마감 1시간 전 (기본값)
    "market_close_30m": (15, 30),  # 장 마감 30분 전
    "market_close":     (16,  0),  # 장 마감
}


def _parse_market_time(time_key: str) -> tuple[int, int]:
    """미장 기준 상대 시간 키 → (hour, minute) ET 변환. 알 수 없는 키는 기본값 15:00 반환."""
    return MARKET_TIME_MAP.get(time_key, (15, 0))


async def _scheduled_auto_trade():
    """스케줄러가 호출하는 자동매매 진입점"""
    from services.auto_trade_service import run_auto_trade_dl
    try:
        logger.info("[Scheduler] 자동매매 시작")
        result = await run_auto_trade_dl(is_test=False)
        logger.info(f"[Scheduler] 자동매매 완료: {result}")
    except Exception as e:
        logger.exception(f"[Scheduler] 자동매매 실패: {e}")


async def _scheduled_sp500_analysis():
    """매시간 정각 실행 - S&P 500 뉴스 영향도 분석

    최근 1시간(hours=1)의 뉴스만 수집하여 분석하므로
    매 시간 새로운 분석 결과가 생성되고 Supabase에 저장됩니다.
    """
    from services.sp500_analysis_service import run_sp500_analysis
    from services.gemini_key_manager import get_key_manager
    try:
        logger.info("[SP500] 스케줄 분석 시작 (매시간)")
        key_mgr = get_key_manager()
        result = await run_sp500_analysis(key_mgr, hours=1)
        logger.info(f"[SP500] 스케줄 분석 완료: {result}")
    except Exception as e:
        logger.exception(f"[SP500] 스케줄 분석 실패: {e}")


async def reschedule_from_settings() -> dict:
    """
    Supabase automation_settings의 활성 설정에서 execution_time을 읽어
    APScheduler 잡을 재등록합니다. 라우터에서도 호출 가능합니다.
    """
    from services.supabase_service import load_automation_settings_active

    time_key = "market_close_1h"
    try:
        cfg = await load_automation_settings_active()
        if cfg and cfg.get("execution_time"):
            time_key = cfg["execution_time"]
    except Exception as e:
        logger.warning(f"[Scheduler] 설정 로드 실패, 기본값 사용: {e}")

    hour, minute = _parse_market_time(time_key)
    scheduler.add_job(
        _scheduled_auto_trade,
        CronTrigger(
            hour=hour,
            minute=minute,
            day_of_week="mon-fri",
            timezone="America/New_York",  # DST 자동 처리
        ),
        id="auto_trade",
        replace_existing=True,
    )
    msg = f"평일 {hour:02d}:{minute:02d} ET ({time_key})"
    logger.info(f"[Scheduler] 스케줄 등록 완료: {msg}")
    return {"time_key": time_key, "hour": hour, "minute": minute, "schedule": msg}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── TimesFM 모델 사전 로드 ──────────────────────
    logger.info("서버 시작 - TimesFM 모델 사전 로드 시도...")
    try:
        from services.forecast_service import get_model
        get_model()
        logger.info("TimesFM forecast 모델 로드 완료")
    except Exception as e:
        logger.warning(f"TimesFM forecast 사전 로드 실패 (첫 요청 시 로드): {e}")

    # ── 자동매매용 TimesFM 모델 사전 로드 ─────────────
    try:
        from services.timesfm_service import _load_model as _load_timesfm
        _load_timesfm()
        logger.info("TimesFM 자동매매 모델 사전 로드 완료")
    except Exception as e:
        logger.warning(f"TimesFM 자동매매 사전 로드 실패 (자동매매 실행 시 로드): {e}")

    # ── 자동매매 스케줄러 시작 ──────────────────────
    # Supabase 설정에서 실행 시간 읽기 → 동적 스케줄 등록
    # America/New_York 타임존으로 DST(EDT/EST) 자동 처리
    scheduler.start()
    result = await reschedule_from_settings()
    logger.info(f"[Scheduler] 자동매매 스케줄러 시작: {result['schedule']}")

    # ── S&P 500 영향도 분석 스케줄 등록 (매시간 정각) ──
    # UTC 기준 매 시간 정각 실행 (hour=* 는 모든 시간)
    scheduler.add_job(
        _scheduled_sp500_analysis,
        CronTrigger(minute=0, timezone="UTC"),  # 매시간 :00분
        id="sp500_analysis",
        replace_existing=True,
    )
    logger.info("[Scheduler] S&P 500 영향도 분석 등록: 매시간 정각 (UTC 기준)")

    # ── 실시간 매매 감지 자동 시작 (활성 사용자별) ────────
    try:
        from routers.realtime import start_all_detections
        result = await start_all_detections()
        logger.info(f"실시간 감지 자동 시작: {result}")
    except Exception as e:
        logger.warning(f"실시간 감지 자동 시작 실패: {e}")

    yield

    # ── 실시간 매매 감지 종료 (전체) ──────────────────
    try:
        from routers.realtime import stop_all_detections
        await stop_all_detections()
        logger.info("실시간 감지 종료 완료")
    except Exception as e:
        logger.warning(f"실시간 감지 종료 실패: {e}")

    # ── 종료 ────────────────────────────────────────
    scheduler.shutdown()
    logger.info("서버 종료")


app = FastAPI(
    title="Bitcoin AI Backend",
    version="2.0.0",
    description="""
## Bitcoin AI Backend API

**HuggingFace Spaces**에서 운영되는 AI 기반 주식·코인 분석 및 자동매매 백엔드입니다.

### 주요 기능

| 엔드포인트 | 설명 |
|---|---|
| `POST /v1/forecast` | Google TimesFM 딥러닝 모델로 가격 예측 |
| `POST /v1/whale` | 고래(대규모 자금) 수급 신호 분석 |
| `POST /v1/xgb/train` | XGBoost 매수/매도 분류 모델 학습 |
| `POST /v1/xgb/predict` | 학습된 XGBoost 모델로 매수/매도 확률 예측 |
| `POST /v1/market-cap` | AI 기반 적정 시가총액 추정 |
| `POST /auto-trade/run` | 자동매매 실행 (수동 실행) |
| `POST /auto-trade/run-test` | 자동매매 테스트 실행 (실제 주문 없음) |
| `GET /auto-trade/settings` | 현재 활성 자동매매 설정 확인 |
| `GET /auto-trade/logs` | 자동매매 실행 로그 조회 |
""",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(forecast.router)
app.include_router(whale.router)
app.include_router(xgb.router)
app.include_router(market_cap.router)
app.include_router(auto_trade.router)
app.include_router(train_ws.router)
app.include_router(gemini.router)
app.include_router(youtube.router)
app.include_router(rl.router)
app.include_router(sp500.router)
app.include_router(portfolio.router)
app.include_router(realtime.router)
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(company_analysis.router)
app.include_router(test.router)


@app.get(
    "/",
    summary="서버 상태 확인 (Health Check)",
    description="서버가 정상적으로 실행 중인지 확인합니다. HuggingFace Space 슬립 상태 감지 및 웨이크업 용도로도 사용됩니다.",
    tags=["health"],
)
async def health():
    return {"status": "ok", "version": "2.0.0"}
