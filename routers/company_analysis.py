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

class PriceRequest(BaseModel):
    tickers: list[str]

@router.post("/prices")
async def get_prices_by_tickers(req: PriceRequest, background_tasks: BackgroundTasks):
    """
    주어진 티커 목록에 대한 52주 최고가 및 현재가 캐시 정보를 반환합니다.
    캐싱되지 않았거나 만료(12시간 기준)된 데이터는 동기식으로 수집하여 반환합니다.
    """
    from services.data_collector import get_stock_price_map, update_stock_prices_cache
    import time
    try:
        tickers = [t.upper().strip() for t in req.tickers if t]
        prices_cache = get_stock_price_map()
        group_prices = {}
        missing_tickers = []
        
        current_time = time.time()
        CACHE_EXPIRY = 12 * 3600
        
        for t in tickers:
            if t in prices_cache:
                cache_data = prices_cache[t]
                updated_at = cache_data.get("updated_at", 0)
                if current_time - updated_at < CACHE_EXPIRY:
                    group_prices[t] = {
                        "high52": cache_data["high52"],
                        "current": cache_data["current"]
                    }
                    continue
            missing_tickers.append(t)
            
        if missing_tickers:
            await update_stock_prices_cache(missing_tickers)
            prices_cache = get_stock_price_map()
            for t in missing_tickers:
                if t in prices_cache:
                    cache_data = prices_cache[t]
                    group_prices[t] = {
                        "high52": cache_data["high52"],
                        "current": cache_data["current"]
                    }
            
        return {
            "status": "ok",
            "prices": group_prices
        }
    except Exception as e:
        logger.error(f"[RouterReport] Prices fetch failed: {e}")
        from services.error_log_service import log_error_to_db
        log_error_to_db("router_get_prices_by_tickers", e, {"tickers_count": len(req.tickers)})
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/tickers/{group_key}")
async def get_tickers_by_group(group_key: str, background_tasks: BackgroundTasks):
    """
    특정 지수 그룹(sp500, qqq, kospi200, kosdaq150, krx300)의 구성 종목 티커 리스트, 종목명 매핑 및 52주 최고가/현재가 정보를 반환합니다.
    캐싱되지 않았거나 만료(12시간 기준)된 데이터는 동기식으로 수집하여 반환합니다.
    """
    from services.data_collector import fetch_tickers_for_group, get_ticker_name_map, get_stock_price_map, update_stock_prices_cache
    import time
    try:
        tickers = await fetch_tickers_for_group(group_key)
        all_names = get_ticker_name_map()
        
        # 현재 수집된 티커들에 대한 매핑 정보만 추출하여 반환
        group_names = {t: all_names[t] for t in tickers if t in all_names}
        
        # 주가 캐시 조회
        prices_cache = get_stock_price_map()
        group_prices = {}
        missing_tickers = []
        
        current_time = time.time()
        CACHE_EXPIRY = 12 * 3600  # 12시간 기준 캐시 만료
        
        for t in tickers:
            if t in prices_cache:
                cache_data = prices_cache[t]
                updated_at = cache_data.get("updated_at", 0)
                # 캐시 만료 여부 확인
                if current_time - updated_at < CACHE_EXPIRY:
                    group_prices[t] = {
                        "high52": cache_data["high52"],
                        "current": cache_data["current"]
                    }
                    continue
            missing_tickers.append(t)
            
        # 캐시에 없는 종목이 있거나 만료된 경우 동기식으로 yfinance/TradingView 수집 처리
        if missing_tickers:
            await update_stock_prices_cache(missing_tickers)
            prices_cache = get_stock_price_map()
            for t in missing_tickers:
                if t in prices_cache:
                    cache_data = prices_cache[t]
                    group_prices[t] = {
                        "high52": cache_data["high52"],
                        "current": cache_data["current"]
                    }
            
        return {
            "status": "ok", 
            "group": group_key, 
            "tickers": tickers,
            "names": group_names,
            "prices": group_prices
        }
    except Exception as e:
        logger.error(f"[RouterReport] Tickers fetch failed for group {group_key}: {e}")
        from services.error_log_service import log_error_to_db
        log_error_to_db("router_get_tickers_by_group", e, {"group_key": group_key})
        raise HTTPException(status_code=500, detail=str(e))

