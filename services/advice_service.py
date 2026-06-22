"""
9인의 투자 거장들의 종목 심층 조언 생성 서비스
"""
import logging
import json
import asyncio
from datetime import datetime, timezone
from services.company_analysis_data import fetch_company_profile_and_financials, fetch_company_news, fetch_macro_indicators
from services.company_analysis_service import call_gemini

logger = logging.getLogger("advice_service")

# 9인 거장 목록 정의
GURUS = [
    {"id": "kostolany", "name": "앙드레 코스톨라니", "title": "돈, 뜨겁게 사랑하고 차갑게 다루어라"},
    {"id": "intelligent-investor", "name": "벤저민 그레이엄", "title": "현명한 투자자"},
    {"id": "common-stocks", "name": "필립 피셔", "title": "위대한 기업에 투자하라"},
    {"id": "one-up", "name": "피터 린치", "title": "이기는 투자"},
    {"id": "templeton", "name": "존 템플턴", "title": "주식 투자 원칙"},
    {"id": "market-wizards", "name": "잭 슈웨거", "title": "마켓 위저드"},
    {"id": "value-valuation", "name": "사뱌사치", "title": "주식 가치평가 완벽정리"},
    {"id": "us-stock-guide", "name": "뉴욕주민", "title": "미국 주식 투자지도"},
    {"id": "buffett-financials", "name": "메리 버핏 & 데이비드 클라크", "title": "워런 버핏의 재무제표 활용법"}
]

def build_guru_prompt(guru_id: str, symbol: str, profile: dict, news: list[dict], macro_data: dict) -> str:
    """
    각 투자 거장의 투자 철학과 서재 요약본을 토대로 맞춤형 프롬프트 생성
    """
    profile_data = profile.get("assetProfile", {})
    fin_data = profile.get("financialData", {})
    stats = profile.get("defaultKeyStatistics", {})
    detail = profile.get("summaryDetail", {})
    
    financials = f"""
    - Current Price: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')}
    - 52-Week Range: {detail.get('fiftyTwoWeekLow', {}).get('fmt', 'N/A')} - {detail.get('fiftyTwoWeekHigh', {}).get('fmt', 'N/A')}
    - Market Cap: {detail.get('marketCap', {}).get('fmt', 'N/A')}
    - Total Revenue: {fin_data.get('totalRevenue', {}).get('fmt', 'N/A')}
    - Free Cash Flow: {fin_data.get('freeCashflow', {}).get('fmt', 'N/A')}
    - Operating Margins: {fin_data.get('operatingMargins', {}).get('fmt', 'N/A')}
    - Return on Equity (ROE): {fin_data.get('returnOnEquity', {}).get('fmt', 'N/A')}
    - Debt to Equity: {fin_data.get('debtToEquity', {}).get('fmt', 'N/A')}
    - Forward PE: {stats.get('forwardPE', {}).get('fmt', 'N/A')}
    - PEG Ratio: {stats.get('pegRatio', {}).get('fmt', 'N/A')}
    """
    
    news_context = "\n".join([
        f"- [{n['published_at']}] {n['title']}: {n['summary'][:120]}..."
        for n in news[:8]
    ])
    
    macro_context_list = []
    for m_symbol, info in macro_data.items():
        macro_context_list.append(
            f"- {info['name']}: {info['price']} ({info['changePercent']:.2f}%)"
        )
    macro_context = "\n".join(macro_context_list)
    
    today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")
    
    base_instruction = f"""
    당신은 전설적인 투자 거장이며, 당신의 핵심 투자 고전을 바탕으로 {symbol} 종목에 대해 분석하고 독창적인 개인화된 투자 조언을 제공해야 합니다.
    아래 종목의 재무 지표, 관련 뉴스, 그리고 글로벌 매크로 지표를 신중히 검토하십시오.
    
    [보고서 작성일]
    {today_str}
    
    [분석 종목]
    {symbol} (현재가: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')})
    
    [기업 재무 정보]
    {financials}
    
    [최신 뉴스]
    {news_context}
    
    [글로벌 매크로 환경]
    {macro_context}
    
    반드시 한국어로 작성하되, 마크다운(Markdown) 포맷을 활용하여 가독성 있게 정리하십시오.
    분석을 진행할 때 반드시 당신의 정체성(Identity)과 투자 철학을 투영하여 개성 있는 문체로 답변하십시오.
    """
    
    if guru_id == "kostolany":
        return base_instruction + """
        [앙드레 코스톨라니의 정체성 및 철학]
        - 도서: "돈, 뜨겁게 사랑하고 차갑게 다루어라"
        - 관점: 시장의 유동성(금리)과 투자자 심리(대중의 군중심리)를 중시하며 '코스톨라니 달걀 모형'을 적용합니다.
        - 작성 가이드라인:
          1. **유동성과 심리로 본 {symbol} 진단**: 현재 글로벌 매크로 금리 환경과 대중의 최근 심리가 {symbol}의 주가 위치에 미치는 영향 분석.
          2. **코스톨라니 달걀 모형 적용**: 현재 이 종목 및 시장이 달걀 모형의 어느 국면(과매도 수정국면, 동행국면, 과열국면 등)에 가까운지 설명하고 역발상 판단 제시.
          3. **독자적인 권고 등급 & 한 줄 평**: 투기적 관심인지, 장기 보유가 타당한지 코스톨라니 특유의 철학적 비유로 최종 조언.
        """
        
    elif guru_id == "intelligent-investor":
        return base_instruction + """
        [벤저민 그레이엄의 정체성 및 철학]
        - 도서: "현명한 투자자"
        - 관점: 가치투자의 창시자로서 '안전마진(Margin of Safety)'과 '미스터 마켓(Mr. Market)'의 이성적/비이성적 변동성을 평가합니다.
        - 작성 가이드라인:
          1. **{symbol}의 내재가치와 안전마진 평가**: 부채비율, PE 배수, FCF 등을 대입해 순자산과 수익력 대비 현재 가격이 안전마진을 확보했는지 계산/평가.
          2. **미스터 마켓의 감정에 휩쓸리지 않는 법**: 최근 뉴스와 센티먼트로 인해 주가가 요동치고 있다면, 이를 기회로 볼지 회피할지 조언.
          3. **그레이엄의 보수적 최종 조언**: 방어적 투자자와 적극적 투자자 관점으로 나누어 매수 적격 여부 판정.
        """
        
    elif guru_id == "common-stocks":
        return base_instruction + """
        [필립 피셔의 정체성 및 철학]
        - 도서: "위대한 기업에 투자하라"
        - 관점: 성장주 투자의 선구자로, 질적 기업 분석인 '사실 수집(Scuttlebutt)'과 15가지 포인트, 강력한 독점력과 경영진의 유능성을 강조합니다.
        - 작성 가이드라인:
          1. **{symbol}의 성장 잠재력과 R&D 역량**: 이 기업이 향후 수년간 매출을 대폭 늘릴 수 있는 혁신 기술이나 독점적 제품 라인을 가졌는지 평가.
          2. **경영진의 역량과 진실성**: 뉴스 및 비즈니스 요약에서 나타나는 경영 전략 및 소통 방식을 평가.
          3. **피셔의 성장주 가치 판정**: 3년~5년을 내다보는 위대한 기업으로서 {symbol}에 투자할 매력이 충분한지 조언.
        """
        
    elif guru_id == "one-up":
        return base_instruction + """
        [피터 린치의 정체성 및 철학]
        - 도서: "이기는 투자"
        - 관점: 마젤란 펀드의 전설. 일상 속 주식 발견, 이익 성장에 기반한 PEG 배수 평가, 6가지 카테고리 분류를 중시합니다.
        - 작성 가이드라인:
          1. **{symbol}의 주식 카테고리 분류**: 이 종목이 고성장주, 대형우량주, 저성장주, 경기순환주, 자산주, 회생주 중 어디에 속하는지 명시하고 특징 서술.
          2. **이익과 밸류에이션(PEG Ratio) 검토**: Forward PE와 PEG 비율({pegRatio})을 해석하여 성장 속도 대비 현재 주가가 싼지 분석.
          3. **린치식 칵테일파티 이론 기반 판단**: 대중의 무관심 단계인지 과열 단계인지 진단하고 최종 매수 권고 여부 제시.
        """
        
    elif guru_id == "templeton":
        return base_instruction + """
        [존 템플턴의 정체성 및 철학]
        - 도서: "주식 투자 원칙"
        - 관점: 글로벌 역발상 투자의 개척자. '최대 비관의 시점(Point of Maximum Pessimism)'에 뛰어들어 최고의 밸류를 사들이는 철학입니다.
        - 작성 가이드라인:
          1. **비관 속에서 기회 찾기**: 현재 이 종목이나 시장 전체에 감도는 우려, 리스크, 주가 하락이 일시적 악재이자 헐값 매수의 기회인지 장기 펀더멘털을 근거로 진단.
          2. **글로벌 다각화 및 가치 진단**: 타 종목 및 해외 섹터 대비 {symbol}이 장기 성장 관점에서 보유할 만한 가치가 있는지 조언.
          3. **템플턴의 역발상 투자 조언**: 공포가 지배하는 시장에서 냉정을 유지하고 분할 매수로 접근할 타이밍인지 판정.
        """
        
    elif guru_id == "market-wizards":
        return base_instruction + """
        [잭 슈웨거의 정체성 및 철학]
        - 도서: "마켓 위저드"
        - 관점: 세계 최고 트레이더들과의 인터뷰를 바탕으로, 리스크 관리, 손절 규칙, 추세 매매 및 마인드 컨트롤을 제시합니다.
        - 작성 가이드라인:
          1. **{symbol}의 추세와 변동성 진단**: 52주 고가/저가 범위 내 현재가 위치 및 최근 뉴스의 방향성이 만드는 추세의 강도 평가.
          2. **트레이딩을 위한 위험 대비 보상 비율(Risk/Reward)**: 진입 시 손절라인 설정 기준과 자산 대비 비중 관리 규칙 제시.
          3. **마켓 위저드의 트레이딩 조언**: 뇌동매매를 방지하기 위해 이 종목을 다룰 때 지켜야 할 핵심 매매 규율 정리.
        """
        
    elif guru_id == "value-valuation":
        return base_instruction + """
        [사뱌사치의 정체성 및 철학]
        - 도서: "주식 가치평가 완벽정리"
        - 관점: 계량적 재무 분석가. 내재가치 평가(DCF, 배수 분석), 잉여현금흐름(FCF), 자본구조 및 효율성 지표를 정교하게 뜯어봅니다.
        - 작성 가이드라인:
          1. **재무제표 정밀 감사**: ROE, margins, Debt to Equity 지표를 통해 자본 효율성과 재무 안정성이 우수한지 진단.
          2. **수치로 계산한 저평가 여부**: PE, PEG, FCF 등의 멀티플을 동종업계 혹은 역사적 밴드와 비교하여 적정 주가 수준을 계량적으로 분석.
          3. **가치평가 전문가의 냉철한 선고**: 수치적 정당성 여부에 따른 매수 범위와 목표 밸류에이션 수준 조언.
        """
        
    elif guru_id == "us-stock-guide":
        return base_instruction + """
        [뉴욕주민의 정체성 및 철학]
        - 도서: "미국 주식 투자지도"
        - 관점: 월가 헤지펀드 트레이더 출신. 자본 배치 효율성(자사주 매입, 배당), 공매도 수급, 규제 프레임워크 및 실전 헤지 펀드의 접근법을 적용합니다.
        - 작성 가이드라인:
          1. **월가 기관 투자자의 자본배치 분석**: {symbol}의 FCF 대비 주주 환원(배당, 자사주 소각)이나 투자 효율성을 평가.
          2. **센티먼트와 공매도 및 수급 동향**: 최근 시장 흐름과 뉴스가 나타내는 수급 쏠림, 매크로 정책 변화에 따른 영향 분석.
          3. **프로의 눈으로 본 단기/장기 트레이딩 전략**: 실전 헤지펀드라면 포트폴리오에 이 종목을 어떻게 담거나 헷지할지 전문적 조언 제시.
        """
        
    elif guru_id == "buffett-financials":
        return base_instruction + """
        [메리 버핏 & 데이비드 클라크의 정체성 및 철학]
        - 도서: "워런 버핏의 재무제표 활용법"
        - 관점: 워런 버핏의 가치투자 동반자. '장기적인 경쟁우위(경제적 해자 - Moat)'를 가진 소비자 독점 기업의 재무적 신호를 판독합니다.
        - 작성 가이드라인:
          1. **경제적 해자(Moat)의 재무적 증거**: 영업이익률(margins), 높은 ROE({returnOnEquity}), 낮은 장기 부채 비율 등을 검토하여 장기 경쟁우위가 지속 가능한지 진단.
          2. **장기 수익성의 일관성**: 당기순이익이 매년 일관되게 성장하고 있는지 혹은 자본 지출(CapEx)이 과도하지 않은지 평가.
          3. **버핏의 소비자 독점 기업 판정**: 평생 동행할 만한 '위대한 기업을 매력적인 가격'에 사는 관점에서 {symbol}이 부합하는지 최종 판정.
        """
    
    return base_instruction

async def generate_advice_stream(ticker: str):
    """
    FastAPI StreamingResponse를 위해 SSE 프로토콜 형식으로 데이터를 양도(yield)하는 제너레이터 함수
    """
    clean_ticker = ticker.strip().upper()
    logger.info(f"[AdviceService] Starting advice streaming pipeline for ticker: {clean_ticker}")
    
    # ── 1단계: 글로벌 금리 및 매크로 지표 분석 ──
    logger.info(f"[AdviceService] [Step 1/3] Fetching macro indicators for {clean_ticker}")
    yield f"data: {json.dumps({'event': 'status', 'step': 'macro', 'status': 'processing', 'message': '글로벌 금리 및 매크로 지표 분석 중...'}, ensure_ascii=False)}\n\n"
    
    try:
        macro_data = await fetch_macro_indicators()
        logger.info(f"[AdviceService] [Step 1/3] Successfully fetched macro indicators. Keys: {list(macro_data.keys())}")
        yield f"data: {json.dumps({'event': 'status', 'step': 'macro', 'status': 'done', 'data': macro_data}, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.error(f"[AdviceService] [Step 1/3] Failed to fetch macro indicators: {e}")
        yield f"data: {json.dumps({'event': 'status', 'step': 'macro', 'status': 'error', 'message': f'매크로 지표 수집 중 오류: {str(e)}'}, ensure_ascii=False)}\n\n"
        return

    # ── 2단계: 기업 재무 및 관련 뉴스 분석 ──
    logger.info(f"[AdviceService] [Step 2/3] Fetching profile & news for {clean_ticker}")
    yield f"data: {json.dumps({'event': 'status', 'step': 'company', 'status': 'processing', 'message': '기업 재무 및 관련 뉴스 수집 중...'}, ensure_ascii=False)}\n\n"
    
    try:
        # 국내 주식 자동 변환 (.KS / .KQ 탐색)
        target_symbols = [clean_ticker]
        if clean_ticker.isdigit() and len(clean_ticker) == 6:
            target_symbols = [f"{clean_ticker}.KS", f"{clean_ticker}.KQ"]
            
        profile = {}
        resolved_symbol = clean_ticker
        for ts in target_symbols:
            logger.info(f"[AdviceService] Fetching financials for candidate: {ts}")
            profile = await fetch_company_profile_and_financials(ts)
            if profile:
                resolved_symbol = ts
                break
                
        if not profile:
            raise Exception(f"Yahoo Finance에서 '{clean_ticker}' 기업 재무제표를 로드하지 못했습니다.")
            
        news = await fetch_company_news(resolved_symbol)
        logger.info(f"[AdviceService] [Step 2/3] Successfully fetched financials & {len(news)} news articles for {resolved_symbol}")
        
        company_data = {"profile": profile, "news": news}
        yield f"data: {json.dumps({'event': 'status', 'step': 'company', 'status': 'done', 'data': company_data}, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.error(f"[AdviceService] [Step 2/3] Failed to fetch company profile & news: {e}")
        yield f"data: {json.dumps({'event': 'status', 'step': 'company', 'status': 'error', 'message': f'기업 정보 및 뉴스 수집 중 오류: {str(e)}'}, ensure_ascii=False)}\n\n"
        return

    # ── 3단계: 거장 9인의 심층 조언 생성 ──
    logger.info(f"[AdviceService] [Step 3/3] Starting AI analysis for 9 investment gurus")
    total = len(GURUS)
    
    for i, guru in enumerate(GURUS, 1):
        guru_id = guru["id"]
        guru_name = guru["name"]
        
        logger.info(f"[AdviceService] [Guru {i}/{total}] {guru_name} is generating advice for {resolved_symbol}...")
        # 1) 대가가 분석을 시작했음을 전송 (thinking)
        yield f"data: {json.dumps({'event': 'advice', 'guru': guru_id, 'status': 'thinking', 'step': i, 'total': total}, ensure_ascii=False)}\n\n"
        
        prompt = build_guru_prompt(guru_id, resolved_symbol, profile, news, macro_data)
        
        try:
            # 키 분산 및 과부하 방지를 위해 호출 전 짧은 딜레이 주입
            await asyncio.sleep(0.5)
            # Gemini 호출
            content = await call_gemini(prompt)
            
            logger.info(f"[AdviceService] [Guru {i}/{total}] {guru_name} successfully generated advice. Content len: {len(content)}")
            # 2) 대가의 분석 결과 전송 (done)
            yield f"data: {json.dumps({'event': 'advice', 'guru': guru_id, 'status': 'done', 'content': content, 'step': i, 'total': total}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"[AdviceService] [Guru {i}/{total}] {guru_name} failed to generate advice: {e}")
            # 3) 에러 시 전송
            yield f"data: {json.dumps({'event': 'advice', 'guru': guru_id, 'status': 'error', 'message': f'분석 실패: {str(e)}', 'step': i, 'total': total}, ensure_ascii=False)}\n\n"
            
    logger.info(f"[AdviceService] Completed all 9 gurus advice streaming for {clean_ticker}")
