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
from routers import forecast, whale, xgb, market_cap, auto_trade, train_ws, gemini, youtube, rl, sp500, portfolio, test, realtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("main")




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

    yield

    # ── 종료 ────────────────────────────────────────
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
app.include_router(test.router)


@app.get(
    "/",
    summary="서버 상태 확인 (Health Check)",
    description="서버가 정상적으로 실행 중인지 확인합니다. HuggingFace Space 슬립 상태 감지 및 웨이크업 용도로도 사용됩니다.",
    tags=["health"],
)
async def health():
    return {"status": "ok", "version": "2.0.0"}
