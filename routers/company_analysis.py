"""
기업 분석 및 실적 리뷰 API 라우터
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from services.company_analysis_service import run_company_analysis

logger = logging.getLogger("company_analysis_router")
router = APIRouter(prefix="/api/analysis", tags=["company-analysis"])

class CompanyAnalysisRequest(BaseModel):
    ticker: str
    analysis_type: str = "market"  # market(기업분석), earnings(실적리뷰), valuation(가치평가), preview(실적프리뷰), moat(해자분석), risk(리스크감지)

class CompanyAnalysisResponse(BaseModel):
    status: str
    ticker: str
    analysis_type: str
    analysis_date: str
    report: str
    macro_data: Optional[Dict[str, Any]] = None

class MacroAnalysisResponse(BaseModel):
    status: str
    analysis_date: str
    report: str
    macro_data: Dict[str, Any]

@router.post("/company", response_model=CompanyAnalysisResponse)
async def analyze_company(req: CompanyAnalysisRequest):
    """
    특정 종목(Ticker)에 대한 AI 기업분석 리포트를 생성합니다.
    - **ticker**: 예) TSLA, AAPL, NVDA
    - **analysis_type**: 
      - `market`: 기본 기업분석
      - `earnings`: 실적 리뷰
      - `valuation`: 적정 가치 평가
      - `preview`: 실적 프리뷰
      - `moat`: 해자 분석 및 AI 준비도
      - `risk`: 리스크 및 경고 신호 감지
    """
    result = await run_company_analysis(req.ticker, req.analysis_type)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
        
    return CompanyAnalysisResponse(
        status=result["status"],
        ticker=result["ticker"],
        analysis_type=result["analysis_type"],
        analysis_date=result["analysis_date"],
        report=result["report"],
        macro_data=result.get("macro_data")
    )

@router.get("/macro-data")
async def get_macro_data():
    """
    실시간 글로벌 거시경제 지표 데이터를 수집하여 반환합니다 (Gemini AI 호출 없음).
    """
    from services.company_analysis_data import fetch_macro_indicators
    try:
        data = await fetch_macro_indicators()
        return {"status": "ok", "macro_data": data}
    except Exception as e:
        logger.error(f"[CompanyAnalysis] Macro data fetching error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/macro", response_model=MacroAnalysisResponse)
async def analyze_macro():
    """
    글로벌 거시경제 분석 리포트 및 자산배분 비중 가이드를 생성합니다.
    """
    from services.company_analysis_service import run_macro_analysis
    result = await run_macro_analysis()
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
        
    return MacroAnalysisResponse(
        status=result["status"],
        analysis_date=result["analysis_date"],
        report=result["report"],
        macro_data=result["macro_data"]
    )
