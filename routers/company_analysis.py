"""
기업 분석 및 실적 리뷰 API 라우터
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from services.company_analysis_service import run_company_analysis

logger = logging.getLogger("company_analysis_router")
router = APIRouter(prefix="/api/analysis", tags=["company-analysis"])

class CompanyAnalysisRequest(BaseModel):
    ticker: str
    analysis_type: str = "market"  # market (기업분석) or earnings (실적리뷰)

class CompanyAnalysisResponse(BaseModel):
    status: str
    ticker: str
    analysis_type: str
    analysis_date: str
    report: str

@router.post("/company", response_model=CompanyAnalysisResponse)
async def analyze_company(req: CompanyAnalysisRequest):
    """
    특정 종목(Ticker)에 대한 AI 기업분석 또는 실적 리뷰 리포트를 생성합니다.
    - **ticker**: 예) TSLA, AAPL, NVDA
    - **analysis_type**: market (기본 기업분석) / earnings (실적리뷰)
    """
    result = await run_company_analysis(req.ticker, req.analysis_type)
    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))
        
    return CompanyAnalysisResponse(
        status=result["status"],
        ticker=result["ticker"],
        analysis_type=result["analysis_type"],
        analysis_date=result["analysis_date"],
        report=result["report"]
    )
