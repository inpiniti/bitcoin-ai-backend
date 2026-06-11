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
    
    # 한국 시간(KST) 기준 년/월/일/시 획득
    kst = pytz.timezone("Asia/Seoul")
    now_kst = datetime.now(kst)
    year = now_kst.year
    month = now_kst.month
    day = now_kst.day
    hour = now_kst.hour
    
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
                    
                # DB 저장 (upsert)
                payload = {
                    "year": year,
                    "month": month,
                    "day": day,
                    "hour": hour,
                    "ticker": ticker,
                    "attractiveness": score
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
