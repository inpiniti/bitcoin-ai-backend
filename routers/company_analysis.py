"""
기업 분석 및 실적 리뷰 API 라우터
"""
import logging
from fastapi import APIRouter, HTTPException, BackgroundTasks
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
    try:
        result = await run_company_analysis(req.ticker, req.analysis_type)
        if result.get("status") == "error":
            err = Exception(result.get("message") or "분석 실패")
            from services.error_log_service import log_error_to_db
            log_error_to_db("router_analyze_company_status_error", err, {"ticker": req.ticker, "analysis_type": req.analysis_type})
            raise HTTPException(status_code=500, detail=result.get("message"))
            
        return CompanyAnalysisResponse(
            status=result["status"],
            ticker=result["ticker"],
            analysis_type=result["analysis_type"],
            analysis_date=result["analysis_date"],
            report=result["report"],
            macro_data=result.get("macro_data")
        )
    except Exception as e:
        if not isinstance(e, HTTPException):
            from services.error_log_service import log_error_to_db
            log_error_to_db("router_analyze_company_exception", e, {"ticker": req.ticker, "analysis_type": req.analysis_type})
        raise e

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
        from services.error_log_service import log_error_to_db
        log_error_to_db("router_get_macro_data", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/macro", response_model=MacroAnalysisResponse)
async def analyze_macro():
    """
    글로벌 거시경제 분석 리포트 및 자산배분 비중 가이드를 생성합니다.
    """
    try:
        from services.company_analysis_service import run_macro_analysis
        result = await run_macro_analysis()
        if result.get("status") == "error":
            err = Exception(result.get("message") or "거시경제 분석 실패")
            from services.error_log_service import log_error_to_db
            log_error_to_db("router_analyze_macro_status_error", err)
            raise HTTPException(status_code=500, detail=result.get("message"))
            
        return MacroAnalysisResponse(
            status=result["status"],
            analysis_date=result["analysis_date"],
            report=result["report"],
            macro_data=result["macro_data"]
        )
    except Exception as e:
        if not isinstance(e, HTTPException):
            from services.error_log_service import log_error_to_db
            log_error_to_db("router_analyze_macro_exception", e)
        raise e

@router.post("/trigger-scheduled-attractiveness")
async def trigger_scheduled_attractiveness(background_tasks: BackgroundTasks):
    """
    일별 관심 종목 투자 매력도 분석 스케줄러를 즉시 백그라운드에서 실행합니다.
    """
    try:
        from services.attractiveness_scheduler import run_daily_attractiveness_analysis
        background_tasks.add_task(run_daily_attractiveness_analysis)
        return {"status": "triggered", "message": "일별 투자 매력도 분석 작업이 백그라운드에서 시작되었습니다."}
    except Exception as e:
        from services.error_log_service import log_error_to_db
        log_error_to_db("router_trigger_scheduled_attractiveness", e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/report-content/{file_id}")
async def get_report_content(file_id: str):
    """
    구글 드라이브 파일 ID를 사용하여 마크다운 리포트 본문 내용을 텍스트 형식으로 다운로드하여 반환합니다.
    """
    from services.attractiveness_scheduler import get_google_access_token
    import os
    import httpx
    
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN")
    
    if not all([client_id, client_secret, refresh_token]):
        err_msg = (
            f"Google Drive credentials are not configured in environment variables. "
            f"client_id: {'Set' if client_id else 'Missing'}, "
            f"client_secret: {'Set' if client_secret else 'Missing'}, "
            f"refresh_token: {'Set' if refresh_token else 'Missing'}"
        )
        from services.error_log_service import log_error_to_db
        log_error_to_db("router_get_report_content_init", ValueError(err_msg), {"file_id": file_id})
        raise HTTPException(status_code=500, detail=err_msg)
        
    try:
        access_token = await get_google_access_token(client_id, client_secret, refresh_token)
        headers = {
            "Authorization": f"Bearer {access_token}"
        }
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return {"status": "ok", "content": resp.text}
    except Exception as e:
        logger.error(f"[RouterReport] Google Drive download failed for file {file_id}: {e}")
        from services.error_log_service import log_error_to_db
        log_error_to_db("router_get_report_content", e, {"file_id": file_id})
        raise HTTPException(status_code=500, detail=f"Failed to fetch report content: {str(e)}")

@router.get("/tickers/{group_key}")
async def get_tickers_by_group(group_key: str):
    """
    특정 지수 그룹(sp500, qqq, kospi200, kosdaq150, krx300)의 구성 종목 티커 리스트를 반환합니다.
    """
    from services.data_collector import fetch_tickers_for_group
    try:
        tickers = await fetch_tickers_for_group(group_key)
        return {"status": "ok", "group": group_key, "tickers": tickers}
    except Exception as e:
        logger.error(f"[RouterReport] Tickers fetch failed for group {group_key}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

