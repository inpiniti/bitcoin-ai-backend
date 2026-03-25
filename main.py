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

from routers import forecast, whale, xgb, market_cap, auto_trade, train_ws, job_crawl, gemini

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


async def _scheduled_job_crawl():
    """#46 스케줄러가 호출하는 채용공고 크롤링 진입점 (매일 09:00 KST)"""
    from services.job_crawler_service import crawl_all_jobs
    from services.supabase_service import upsert_job_listings, get_unnotified_jobs, mark_jobs_notified
    from services.kakao_service import send_trade_report, build_job_report, load_kakao_config
    try:
        logger.info("[JobCrawl] 채용공고 크롤링 시작")
        jobs = await crawl_all_jobs()

        if not jobs:
            logger.info("[JobCrawl] 수집된 공고 없음")
            return

        # Supabase 저장 (중복 무시)
        inserted = await upsert_job_listings([j.to_dict() for j in jobs])
        logger.info(f"[JobCrawl] {len(jobs)}건 수집 → 신규 {inserted}건 저장")

        # 미발송 공고 조회 → 카카오 발송
        unnotified = await get_unnotified_jobs()
        if not unnotified:
            logger.info("[JobCrawl] 발송할 신규 공고 없음")
            return

        cfg = await load_kakao_config()
        if not cfg:
            logger.warning("[JobCrawl] 카카오 설정 없음, 발송 스킵")
            return

        report, web_url = build_job_report(unnotified)
        if not report:
            return

        sent = await send_trade_report(cfg, report, web_url=web_url)
        if sent:
            job_ids = [j["id"] for j in unnotified]
            await mark_jobs_notified(job_ids)
            logger.info(f"[JobCrawl] {len(unnotified)}건 카카오 발송 완료")
        else:
            logger.warning("[JobCrawl] 카카오 발송 실패")

    except Exception as e:
        logger.exception(f"[JobCrawl] 크롤링 실패: {e}")


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
        id="auto_trade_dl",
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
        logger.info("TimesFM 모델 로드 완료")
    except Exception as e:
        logger.warning(f"TimesFM 사전 로드 실패 (첫 요청 시 로드): {e}")

    # ── 자동매매 스케줄러 시작 ──────────────────────
    # Supabase 설정에서 실행 시간 읽기 → 동적 스케줄 등록
    # America/New_York 타임존으로 DST(EDT/EST) 자동 처리
    scheduler.start()
    result = await reschedule_from_settings()
    logger.info(f"[Scheduler] 자동매매 스케줄러 시작: {result['schedule']}")

    # ── 채용공고 크롤러 스케줄 등록 (매일 09:00 KST) ──
    scheduler.add_job(
        _scheduled_job_crawl,
        CronTrigger(hour=9, minute=0, timezone="Asia/Seoul"),
        id="job_crawl",
        replace_existing=True,
    )
    logger.info("[Scheduler] 채용공고 크롤러 등록: 매일 09:00 KST")

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
- **실행 시각**: Supabase `automation_settings.execution_time` 기반 동적 설정 (기본: 평일 15:00 ET)
- **DST 처리**: `America/New_York` 타임존으로 썸머타임 자동 처리
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
app.include_router(train_ws.router)
app.include_router(job_crawl.router)
app.include_router(gemini.router)


@app.get(
    "/",
    summary="서버 상태 확인 (Health Check)",
    description="서버가 정상적으로 실행 중인지 확인합니다. HuggingFace Space 슬립 상태 감지 및 웨이크업 용도로도 사용됩니다.",
    tags=["health"],
)
async def health():
    return {"status": "ok", "version": "2.0.0"}
