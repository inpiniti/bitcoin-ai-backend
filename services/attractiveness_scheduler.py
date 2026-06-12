import asyncio
import logging
import re
from datetime import datetime
import pytz
from supabase import create_client
from services import auth_service
from services.company_analysis_service import run_company_analysis

logger = logging.getLogger("attractiveness_scheduler")

def extract_attractiveness(report_text: str) -> int | None:
    """
    종합 분석 보고서 텍스트에서 종합 투자 매력도 점수를 파싱합니다.
    """
    if not report_text:
        return None

    # 마크다운 볼드체 및 방해 문자 제거 (별표 제거)
    cleaned = report_text.replace("*", "")
    
    # 1단계: 표준 정규식 매칭 시도
    patterns = [
        r"종합\s*투자\s*매력도\s*[:：\s]*(\d+)",
        r"종합\s*투자\s*매력도\s*점수\s*[:：\s]*(\d+)",
        r"투자\s*매력도\s*[:：\s]*(\d+)",
        r"종합\s*평점\s*[:：\s]*(\d+)",
        r"최종\s*투자\s*매력도\s*[:：\s]*(\d+)",
        r"투자\s*매력도[^\d]*(\d+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            try:
                score = int(match.group(1))
                if 0 <= score <= 100:
                    return score
            except ValueError:
                continue
                
    # 2단계: 휴리스틱 파싱 (키워드 반경 근처에서 숫자 찾기)
    for kw in ["투자 매력도", "투자매력도", "종합 평점", "종합평점"]:
        idx = cleaned.find(kw)
        if idx != -1:
            chunk = cleaned[idx:idx + 60]
            numbers = re.findall(r"\d+", chunk)
            for num_str in numbers:
                try:
                    val = int(num_str)
                    if 0 <= val <= 100:
                        # 100인 분모 제거용 예외 처리 (e.g. 85 / 100 에서 100 무시)
                        if val == 100 and len(numbers) > 1:
                            continue
                        return val
                except ValueError:
                    continue
                    
    return None

def extract_reason(report_text: str) -> str | None:
    """
    종합 분석 보고서 텍스트에서 종합 투자 매력도 점수의 핵심 이유(3줄 요약 및 평점 요약)를 추출합니다.
    """
    if not report_text:
        return None
        
    cleaned = report_text.replace("*", "")
    
    import re
    
    # 1단계: '1. 종합 평점 및 요약' 섹션의 전체 본문을 '2. 에이전트별 분석 요약' 전까지 추출
    # 별표가 제거된 텍스트이므로 보통 '1. 종합 평점 및 요약 (Executive Summary & Final Rating)' 형태로 매칭됨
    match = re.search(r"종합\s*평점\s*및\s*요약[^\n]*\n([\s\S]+?)(?=\n2\.\s*(?:에이전트|Synthesis)|\n\n\n|\Z)", cleaned, re.IGNORECASE)
    if match:
        reason_str = match.group(1).strip()
        if reason_str:
            # 혹시 맨 앞부분에 '(Executive Summary & Final Rating)' 등의 괄호 잔재가 단독으로 오거나 불필요한 줄바꿈이 있으면 정리
            return reason_str

    # 2단계: 특정 핵심 이유/요약/3줄 요약 키워드 기준 추출 (룩어헤드로 2번 섹션 시작 전까지만 제한)
    patterns = [
        r"(?:3줄\s*요약|핵심\s*이유|이유)[\s:：]*\n*([\s\S]{1,400}?)(?=\n2\.\s*(?:에이전트|Synthesis)|\n\n\n|\Z)",
        r"(?:종합\s*평점\s*및\s*요약)[\s:：]*\n*([\s\S]{1,400}?)(?=\n2\.\s*(?:에이전트|Synthesis)|\n\n\n|\Z)"
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            reason_str = match.group(1).strip()
            if reason_str:
                return reason_str
                
    # 폴백: 서두 150자 반환
    return cleaned[:150].strip() + "..."

def extract_macro_ratio(report_text: str) -> tuple[int, int]:
    """
    거시경제 보고서 텍스트에서 추천 주식/현금 비중을 추출합니다. (기본값 50%, 50%)
    """
    if not report_text:
        return 50, 50
        
    cleaned = report_text.replace("*", "")
    import re
    
    patterns = [
        r"주식\s*(?:비중)?\s*[:：\s]*(\d+)\s*%\s*(?:대|vs|및|,)?\s*현금\s*(?:비중)?\s*[:：\s]*(\d+)\s*%",
        r"주식\s*(\d+)\s*%\s*,\s*현금\s*(\d+)\s*%",
        r"주식\s*:\s*(\d+)\s*,\s*현금\s*:\s*(\d+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            try:
                stock = int(match.group(1))
                cash = int(match.group(2))
                if 0 <= stock <= 100 and 0 <= cash <= 100:
                    return stock, cash
            except ValueError:
                continue
                
    # 개별 파싱 폴백
    stock_match = re.search(r"주식\s*(?:비중)?\s*[:：\s]*(\d+)\s*%", cleaned)
    cash_match = re.search(r"현금\s*(?:비중)?\s*[:：\s]*(\d+)\s*%", cleaned)
    if stock_match and cash_match:
        try:
            stock = int(stock_match.group(1))
            cash = int(cash_match.group(2))
            return stock, cash
        except ValueError:
            pass
            
    return 50, 50

def extract_macro_reason(report_text: str) -> str | None:
    """
    거시경제 보고서에서 자산 배분 비중 권고 근거(2줄 요약)를 파싱합니다.
    """
    if not report_text:
        return None
        
    cleaned = report_text.replace("*", "")
    import re
    
    patterns = [
        r"(?:자산\s*배분의?\s*핵심\s*거시적\s*근거|핵심\s*거시적\s*근거|배분의?\s*핵심\s*근거)[\s:：]*\n*([\s\S]{1,250}?)(?=\n\d+\.|\n[A-Za-z]|\n[가-힣]+|\n\n|\Z)",
        r"(?:자산\s*배분\s*가이드라인\s*제안)[\s:：]*\n*([\s\S]{1,400}?)(?=\n\n|\Z)"
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            reason_str = match.group(1).strip()
            if reason_str:
                return reason_str
                
    return cleaned[:150].strip() + "..."

async def run_hourly_macro_analysis(supabase, year: int, month: int, day: int, hour: int):
    """
    글로벌 거시경제 분석을 실행하여 현금/주식 비율 및 리포트 원문을 macro_analysis 테이블에 저장합니다.
    """
    logger.info(f"[Scheduler] 글로벌 거시경제 분석 시작 ({year}-{month:02d}-{day:02d} {hour:02d}시)")
    try:
        from services.company_analysis_service import run_macro_analysis
        result = await run_macro_analysis()
        if result.get("status") != "ok":
            raise Exception(result.get("message") or "거시경제 분석 실행 실패")
            
        report = result.get("report", "")
        stock, cash = extract_macro_ratio(report)
        reason = extract_macro_reason(report)
        
        payload = {
            "year": year,
            "month": month,
            "day": day,
            "hour": hour,
            "stock_ratio": stock,
            "cash_ratio": cash,
            "reason": reason,
            "report": report
        }
        
        supabase.table("macro_analysis").upsert(
            payload,
            on_conflict="year,month,day,hour"
        ).execute()
        
        logger.info(f"[Scheduler] 글로벌 거시경제 분석 적재 완료 (주식: {stock}%, 현금: {cash}%)")
    except Exception as e:
        logger.error(f"[Scheduler] 글로벌 거시경제 분석 스케줄링 적재 실패: {e}")

async def run_hourly_attractiveness_analysis():
    """
    매 시각 호출되는 관심종목 투자 매력도 분석/저장 메인 태스크
    """
    logger.info("[Scheduler] 시간별 투자 매력도 분석 스케줄러 시작")
    
    # 1. Supabase 인증 정보 획득
    url, key = auth_service.get_supabase_env()
    if not url or not key:
        logger.error("[Scheduler] Supabase 설정이 누락되어 스케줄러를 실행할 수 없습니다.")
        return
        
    supabase = create_client(url, key)
    
    # 한국 시간(KST) 기준 년/월/일/시 획득
    kst = pytz.timezone("Asia/Seoul")
    now_kst = datetime.now(kst)
    year = now_kst.year
    month = now_kst.month
    day = now_kst.day
    hour = now_kst.hour
    
    # A. 글로벌 거시경제 분석 우선 실행 및 적재
    await run_hourly_macro_analysis(supabase, year, month, day, hour)
    
    # 2. 실시간 매매 대상 관심종목 (is_active = true) 목록 조회
    try:
        res = supabase.table("realtime_trading").select("ticker").eq("is_active", True).execute()
        rows = res.data or []
    except Exception as e:
        logger.error(f"[Scheduler] 활성 관심종목 조회 실패: {e}")
        return
        
    tickers = sorted(list(set(row.get("ticker").upper().strip() for row in rows if row.get("ticker"))))
    if not tickers:
        logger.info("[Scheduler] 활성 관심종목이 없어 스케줄 작업을 종료합니다.")
        return
        
    logger.info(f"[Scheduler] 분석 대상 관심종목 ({len(tickers)}개): {tickers}")
    
    # 3. 1분 간격으로 종목마다 순차 분석 실행
    for idx, ticker in enumerate(tickers):
        if idx > 0:
            logger.info(f"[Scheduler] 다음 분석 전 60초 대기 중... ({idx}/{len(tickers)})")
            await asyncio.sleep(60)
            
        logger.info(f"[Scheduler] [{ticker}] AI 종합 분석 시작 ({year}-{month:02d}-{day:02d} {hour:02d}시)")
        
        success = False
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # comprehensive AI 기업분석 리포트 실행
                result = await run_company_analysis(ticker, "comprehensive")
                if result.get("status") != "ok":
                    raise Exception(result.get("message") or "분석 실패")
                    
                report = result.get("report", "")
                score = extract_attractiveness(report)
                if score is None:
                    raise Exception("보고서 본문에서 매력도 점수를 추출하지 못했습니다.")
                    
                # 3줄 요약 핵심 근거 추출
                reason = extract_reason(report)
                    
                # DB 저장 (upsert)
                payload = {
                    "year": year,
                    "month": month,
                    "day": day,
                    "hour": hour,
                    "ticker": ticker,
                    "attractiveness": score,
                    "reason": reason
                }
                
                # Supabase upsert 호출 (unique 제약 조건에 의해 충돌 시 업데이트됨)
                supabase.table("ticker_attractiveness").upsert(
                    payload,
                    on_conflict="year,month,day,hour,ticker"
                ).execute()
                
                logger.info(f"[Scheduler] [{ticker}] 분석 및 저장 완료: {score}점")
                success = True
                break
                
            except Exception as e:
                logger.warning(f"[Scheduler] [{ticker}] 시도 {attempt + 1}/{max_retries} 실패: {e}")
                if attempt < max_retries - 1:
                    # 429 완화 및 잠시 대기 후 재시도
                    await asyncio.sleep(5)
                    
        if not success:
            logger.error(f"[Scheduler] [{ticker}] {max_retries}회 재시도 모두 실패")
            
    logger.info("[Scheduler] 시간별 투자 매력도 분석 스케줄러 완료")
