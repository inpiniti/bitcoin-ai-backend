"""
bitcoin-ai-backend - FastAPI 기반 백엔드
Motia(Node.js) 제거, 순수 Python + FastAPI + uvicorn 구성
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from routers import forecast, whale, xgb, market_cap, auto_trade

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("main")

scheduler = AsyncIOScheduler()


async def _scheduled_auto_trade():
    """스케줄러가 호출하는 자동매매 진입점"""
    from services.auto_trade_service import run_auto_trade_dl
    try:
        logger.info("[Scheduler] 자동매매 시작")
        result = await run_auto_trade_dl(is_test=False)
        logger.info(f"[Scheduler] 자동매매 완료: {result}")
    except Exception as e:
        logger.exception(f"[Scheduler] 자동매매 실패: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── TimesFM 모델 사전 로드 ──────────────────────
    logger.info("서버 시작 - TimesFM 모델 사전 로드 시도...")
    try:
        from services.forecast_service import get_model
        get_model()
        logger.info("TimesFM 모델 로드 완료")
    except Exception as e:
        logger.warning(f"TimesFM 사전 로드 실패 (첫 요청 시 로드): {e}")

    # ── 자동매매 스케줄러 시작 ──────────────────────
    # America/New_York 타임존 지정 → DST(EDT/EST) 자동 처리
    # 평일(월~금) 15:00 ET = 장 마감 1시간 전
    scheduler.add_job(
        _scheduled_auto_trade,
        CronTrigger(
            hour=15,
            minute=0,
            day_of_week="mon-fri",
            timezone="America/New_York",
        ),
        id="auto_trade_dl",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[Scheduler] 자동매매 스케줄러 시작 (평일 15:00 ET)")

    yield

    # ── 종료 ────────────────────────────────────────
    scheduler.shutdown()
    logger.info("서버 종료")


app = FastAPI(
    title="bitcoin-ai-backend",
    version="2.0.0",
    description="FastAPI 기반 AI 예측 백엔드 (Motia 제거)",
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


@app.get("/")
async def health():
    return {"status": "ok", "version": "2.0.0"}
