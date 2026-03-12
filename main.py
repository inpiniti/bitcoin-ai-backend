"""
bitcoin-ai-backend - FastAPI 기반 백엔드
Motia(Node.js) 제거, 순수 Python + FastAPI + uvicorn 구성
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import forecast, whale, xgb, market_cap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("서버 시작 - TimesFM 모델 사전 로드 시도...")
    try:
        from services.forecast_service import get_model
        get_model()
        logger.info("TimesFM 모델 로드 완료")
    except Exception as e:
        logger.warning(f"TimesFM 사전 로드 실패 (첫 요청 시 로드): {e}")
    yield
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


@app.get("/")
async def health():
    return {"status": "ok", "version": "2.0.0"}
