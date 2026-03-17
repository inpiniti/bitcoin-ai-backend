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
| `POST /auto-trade/run` | 자동매매 실행 (APScheduler 평일 15:00 ET 자동 호출) |
| `POST /auto-trade/run-test` | 자동매매 테스트 실행 (실제 주문 없음) |
| `GET /auto-trade/settings` | 현재 활성 자동매매 설정 확인 |
| `GET /auto-trade/logs` | 자동매매 실행 로그 조회 |

### 자동매매 스케줄
- **실행 시각**: 평일(월~금) **15:00 ET** (EDT/EST DST 자동 처리)
- **설정 저장소**: Supabase `automation_settings` 테이블
- **로그 저장소**: Supabase `auto_trade_dl_logs` 테이블
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


@app.get(
    "/",
    summary="서버 상태 확인 (Health Check)",
    description="서버가 정상적으로 실행 중인지 확인합니다. HuggingFace Space 슬립 상태 감지 및 웨이크업 용도로도 사용됩니다.",
    tags=["health"],
)
async def health():
    return {"status": "ok", "version": "2.0.0"}
