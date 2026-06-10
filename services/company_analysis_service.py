"""
안트롭틱(Anthropic) 스타일의 금융 에이전트를 모방한 Gemini 기반 기업 분석 서비스
"""
import logging
import json
import httpx
import asyncio
from datetime import datetime, timezone
from services.gemini_key_manager import get_key_manager
from services.company_analysis_data import fetch_company_profile_and_financials, fetch_company_news

logger = logging.getLogger("company_analysis_service")

# Vercel Proxy URL 설정 (있을 경우 사용)
import os
VERCEL_PROXY_URL = os.environ.get("VERCEL_PROXY_URL", "").strip()

if VERCEL_PROXY_URL:
    GEMINI_API_URL = VERCEL_PROXY_URL
else:
    GEMINI_API_URL = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        "/gemini-3.1-flash-lite:generateContent?key={api_key}"
    )

def build_market_research_prompt(symbol: str, profile: dict, news: list[dict]) -> str:
    """
    Market Researcher Agent 스타일의 시스템 프롬프트 조립
    """
    profile_data = profile.get("assetProfile", {})
    fin_data = profile.get("financialData", {})
    stats = profile.get("defaultKeyStatistics", {})
    detail = profile.get("summaryDetail", {})
    
    company_info = f"""
- Name: {profile_data.get('companyOfficers', [{}])[0].get('name', 'N/A') if profile_data.get('companyOfficers') else 'N/A'} (CEO/Key officers available)
- Sector: {profile_data.get('sector', 'N/A')}
- Industry: {profile_data.get('industry', 'N/A')}
- Business Summary: {profile_data.get('longBusinessSummary', 'N/A')[:500]}...
    """
    
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
        f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
        for n in news
    ])
    
    today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")
    
    return f"""You are a senior investment analyst and market researcher specializing in technology and growth stocks.
Analyze the following company data and news to provide a professional-grade market research report.

[ANALYSIS DATE / REPORT DATE]
{today_str}

[COMPANY PROFILE: {symbol}]
{company_info}

[FINANCIAL METRICS]
{financials}

[RECENT NEWS]
{news_context}

Please generate an in-depth analysis report structured as follows (Write the report in Korean):
*CRITICAL REQUIREMENT*: You must write "{today_str}" as the report date (보고서 작성일) at the beginning of the report.
1. **Executive Summary**: Core thesis on why this stock is a buy, hold, or sell.
2. **Business Strategy & Market Positioning**: Critique their product strategy, industry moat, and competitors.
3. **Financial Health & Efficiency Analysis**: Evaluate revenue growth, margins, cash flow strength, and debt levels.
4. **Recent News Sentiment & Catalysts**: Discuss major news, public sentiment, and short-term catalysts.
5. **Investment Recommendation**: Clear rating (Buy / Hold / Sell) with specific target/range advice. Ensure this recommendation is mathematically consistent with the provided 'Current Price' and any estimated target price ranges.

Make it clean, structured, insightful, and professional. Use markdown formatting.
"""

def build_earnings_review_prompt(symbol: str, profile: dict, news: list[dict]) -> str:
    """
    Earnings Reviewer Agent 스타일의 실적 보고서 분석 프롬프트 조립
    """
    earnings_data = profile.get("earnings", {})
    fin_data = profile.get("financialData", {})
    stats = profile.get("defaultKeyStatistics", {})
    
    financials = f"""
- Current Price: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')}
- Revenue Growth (YoY): {fin_data.get('revenueGrowth', {}).get('fmt', 'N/A')}
- Gross Profits: {fin_data.get('grossProfits', {}).get('fmt', 'N/A')}
- EBITDA: {fin_data.get('ebitda', {}).get('fmt', 'N/A')}
- Diluted EPS: {stats.get('trailingEps', {}).get('fmt', 'N/A')}
    """
    
    news_context = "\n".join([
        f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
        for n in news if "earnings" in n['title'].lower() or "revenue" in n['title'].lower() or "result" in n['title'].lower()
    ])
    if not news_context:
        # Earnings 관련 뉴스가 없을 경우 전체 뉴스를 전달
        news_context = "\n".join([
            f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
            for n in news[:10]
        ])

    today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")

    return f"""You are an expert financial auditor and earnings analyst.
Review the following financial performance metrics and news regarding {symbol}'s recent earnings release.

[ANALYSIS DATE / REPORT DATE]
{today_str}

[FINANCIAL PERFORMANCE: {symbol}]
{financials}

[RECENT EARNINGS NEWS & REACTIONS]
{news_context}

Please generate an Earnings Review Report in Korean. Structure the report as follows:
*CRITICAL REQUIREMENT*: You must write "{today_str}" as the report date (보고서 작성일) at the beginning of the report.
1. **Earnings Summary**: Highlight actual numbers vs. street consensus if available, plus revenue and EPS growth rates.
2. **Margin & Profitability Analysis**: Dive into gross margins, operating margin expansions/contractions, and cash generation.
3. **Management Guidance & Outlook**: What is the management outlook/future forecasts discussed in the news/filings?
4. **Key Risks & Red Flags**: List any negative factors, margin pressures, or macro risks.
5. **Earnings Score**: Assign a final rating (A+, A, B, C, D) with a 2-sentence rationale. Compare the performance context against the provided 'Current Price' to reflect whether the current price matches the earnings trajectory.

Make the output extremely analytical, data-driven, and written in clean Korean markdown.
"""

def build_valuation_prompt(symbol: str, profile: dict, news: list[dict]) -> str:
    """
    Valuation & Comps 분석용 프롬프트 조립
    """
    fin_data = profile.get("financialData", {})
    stats = profile.get("defaultKeyStatistics", {})
    detail = profile.get("summaryDetail", {})
    
    financials = f"""
- Current Price: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')}
- 52-Week Range: {detail.get('fiftyTwoWeekLow', {}).get('fmt', 'N/A')} - {detail.get('fiftyTwoWeekHigh', {}).get('fmt', 'N/A')}
- Market Cap: {detail.get('marketCap', {}).get('fmt', 'N/A')}
- Revenue: {fin_data.get('totalRevenue', {}).get('fmt', 'N/A')}
- Free Cash Flow: {fin_data.get('freeCashflow', {}).get('fmt', 'N/A')}
- Operating Margins: {fin_data.get('operatingMargins', {}).get('fmt', 'N/A')}
- Trailing PE: {detail.get('trailingPE', {}).get('fmt', 'N/A')}
- Forward PE: {stats.get('forwardPE', {}).get('fmt', 'N/A')}
- EV to Revenue: {stats.get('enterpriseToRevenue', {}).get('fmt', 'N/A')}
- EV to EBITDA: {stats.get('enterpriseToEbitda', {}).get('fmt', 'N/A')}
- PEG Ratio: {stats.get('pegRatio', {}).get('fmt', 'N/A')}
    """
    
    news_context = "\n".join([
        f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
        for n in news[:10]
    ])

    today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")

    return f"""You are a Valuation Expert and Equity Research Analyst.
Assess the intrinsic and relative value of {symbol} using the financial indicators and recent market news below.

[ANALYSIS DATE / REPORT DATE]
{today_str}

[FINANCIAL METRICS: {symbol}]
{financials}

[RECENT NEWS CONTEXT]
{news_context}

Please generate a professional Valuation & Comps Analysis Report in Korean. Structure the report as follows:
*CRITICAL REQUIREMENT*: You must write "{today_str}" as the report date (보고서 작성일) at the beginning of the report.
1. **Executive Summary**: Overview of the company's valuation state (Undervalued, Fairly valued, Overvalued).
2. **Intrinsic Valuation (DCF-based Analysis)**: Critique their Free Cash Flow (FCF) generation strength, project growth rates, and estimate cash flow stability.
3. **Relative Valuation (Comps Multiples)**: Compare current P/E, EV/EBITDA, and PEG ratios with industry averages and key competitors (recommend peer multi-comparison).
4. **Valuation Sensitivity & Catalysts**: Detail key risks to the valuation (e.g., interest rates, growth slowdowns) and catalysts that could unlock value.
5. **Target Price Range & Investment Verdict**: Provide a realistic target price range (e.g., $X ~ $Y) and a clear Buy/Hold/Sell recommendation with mathematical justification.
   *CRITICAL REQUIREMENT*: You must explicitly compare your Target Price Range with the provided 'Current Price'. If the Target Price Range is lower than the Current Price, it is a Downside (not Upside) and the rating must NOT be BUY (it should be HOLD or SELL). If the Target Price Range is higher than the Current Price, it is an Upside, and you can recommend BUY. The calculated Upside/Downside percentage and the rating MUST be mathematically consistent.

Make it clean, quantitative, and written in professional Korean markdown.
"""

def build_preview_prompt(symbol: str, profile: dict, news: list[dict]) -> str:
    """
    Earnings Preview & Catalyst 분석용 프롬프트 조립
    """
    fin_data = profile.get("financialData", {})
    stats = profile.get("defaultKeyStatistics", {})
    detail = profile.get("summaryDetail", {})
    
    financials = f"""
- Current Price: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')}
- Revenue Growth (YoY): {fin_data.get('revenueGrowth', {}).get('fmt', 'N/A')}
- Operating Margins: {fin_data.get('operatingMargins', {}).get('fmt', 'N/A')}
- Profit Margins: {fin_data.get('profitMargins', {}).get('fmt', 'N/A')}
- Trailing EPS: {stats.get('trailingEps', {}).get('fmt', 'N/A')}
    """
    
    news_context = "\n".join([
        f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
        for n in news if "preview" in n['title'].lower() or "earnings" in n['title'].lower() or "expect" in n['title'].lower()
    ])
    if not news_context:
        news_context = "\n".join([
            f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
            for n in news[:10]
        ])

    today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")

    return f"""You are an Equity Research Analyst preparing an Earnings Preview.
Analyze upcoming expectations and sentiment for {symbol}'s near-term earnings release.

[ANALYSIS DATE / REPORT DATE]
{today_str}

[CURRENT RECENT METRICS: {symbol}]
{financials}

[UPCOMING EXPECTATIONS & CATALYST NEWS]
{news_context}

Please generate an Earnings Preview Report in Korean. Structure the report as follows:
*CRITICAL REQUIREMENT*: You must write "{today_str}" as the report date (보고서 작성일) at the beginning of the report.
1. **Earnings Expectations**: Summarize the street consensus for revenue and EPS.
2. **Key Metrics to Watch**: Identify 2-3 segment revenues or operational metrics (e.g., Cloud growth, hardware delivery volume) that will decide the stock's post-earnings direction.
3. **Sentiment & Position Check**: Detail whether the market sentiment going into earnings is hot, cold, or neutral, and evaluate options/price movement implications.
4. **Bull vs. Bear Scenarios**: Draft a clear table/bullet comparison of the Bull case (what happens if they beat & guide up) vs. Bear case (what happens if they miss or guide down).
5. **Earnings Strategy & Rating**: Give an overall tactical recommendation (e.g., Neutral, Buy into Strength, Caution) for short-term traders. Make sure to relate this strategy directly to the current price level ('Current Price').

Make the output forward-looking, catalyst-centric, and written in clean Korean markdown.
"""

def build_moat_prompt(symbol: str, profile: dict, news: list[dict]) -> str:
    """
    Moat & AI Readiness 분석용 프롬프트 조립
    """
    profile_data = profile.get("assetProfile", {})
    fin_data = profile.get("financialData", {})
    stats = profile.get("defaultKeyStatistics", {})
    
    company_info = f"""
- Sector: {profile_data.get('sector', 'N/A')}
- Industry: {profile_data.get('industry', 'N/A')}
- Business Summary: {profile_data.get('longBusinessSummary', 'N/A')[:500]}...
    """
    
    financials = f"""
- Current Price: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')}
- Operating Margins: {fin_data.get('operatingMargins', {}).get('fmt', 'N/A')}
- Return on Equity (ROE): {fin_data.get('returnOnEquity', {}).get('fmt', 'N/A')}
- Gross Margins: {fin_data.get('grossMargins', {}).get('fmt', 'N/A')}
    """
    
    news_context = "\n".join([
        f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
        for n in news if "ai" in n['title'].lower() or "competitor" in n['title'].lower() or "tech" in n['title'].lower()
    ])
    if not news_context:
        news_context = "\n".join([
            f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
            for n in news[:10]
        ])

    today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")

    return f"""You are a Strategic Management Consultant and Private Equity Dilegence Analyst.
Evaluate {symbol}'s economic moat and AI technology readiness based on the business description, margins, and news below.

[ANALYSIS DATE / REPORT DATE]
{today_str}

[BUSINESS PROFILE: {symbol}]
{company_info}

[PROFITABILITY METRICS]
{financials}

[TECHNOLOGY & COMPETITION NEWS]
{news_context}

Please generate an Economic Moat & AI Readiness Report in Korean. Structure the report as follows:
*CRITICAL REQUIREMENT*: You must write "{today_str}" as the report date (보고서 작성일) at the beginning of the report.
1. **Moat Assessment**: Define the type of moat they possess (Network Effects, Switching Costs, Cost Advantage, or Intangible Assets) and rate it (Wide, Narrow, None).
2. **Competitor & Market Position Analysis**: Assess their market share and threat from key rivals.
3. **AI Readiness & Tech Stack Integration**: Analyze their AI initiatives, software capabilities, and whether they are an AI leader, fast follower, or laggard.
4. **Margin & Capital Efficiency Audit**: Link their high/low margins and ROE back to their structural business advantages, evaluating how well the current stock price ('Current Price') values these attributes.
5. **Strategic SWOT Summary**: Provide a strategic advice summary on their technological survival over the next 5-10 years.

Write in a highly strategic, professional tone using clean Korean markdown.
"""

def build_risk_prompt(symbol: str, profile: dict, news: list[dict]) -> str:
    """
    Risk & Warning Signals 분석용 프롬프트 조립
    """
    fin_data = profile.get("financialData", {})
    stats = profile.get("defaultKeyStatistics", {})
    
    financials = f"""
- Current Price: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')}
- Debt to Equity: {fin_data.get('debtToEquity', {}).get('fmt', 'N/A')}
- Current Ratio: {fin_data.get('currentRatio', {}).get('fmt', 'N/A')}
- Quick Ratio: {fin_data.get('quickRatio', {}).get('fmt', 'N/A')}
- Beta (5Y Monthly): {stats.get('beta', {}).get('fmt', 'N/A')}
    """
    
    news_context = "\n".join([
        f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
        for n in news if "risk" in n['title'].lower() or "worry" in n['title'].lower() or "drop" in n['title'].lower() or "lawsuit" in n['title'].lower() or "investigation" in n['title'].lower()
    ])
    if not news_context:
        news_context = "\n".join([
            f"- [{n['published_at']}] {n['title']}: {n['summary'][:150]}..."
            for n in news[:10]
        ])

    today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")

    return f"""You are a Financial Risk Manager and Auditor.
Analyze potential risk factors and red flags for {symbol} based on their leverage metrics and recent negative news flow.

[ANALYSIS DATE / REPORT DATE]
{today_str}

[LEVERAGE & LIQUIDITY METRICS: {symbol}]
{financials}

[NEGATIVE & RISK NEWS CONTEXT]
{news_context}

Please generate a Risk & Warning Signals Report in Korean. Structure the report as follows:
*CRITICAL REQUIREMENT*: You must write "{today_str}" as the report date (보고서 작성일) at the beginning of the report.
1. **Key Risk Matrix**: Classify risks into Financial, Operational, Regulatory, and Macroeconomic categories.
2. **Financial Risk & Liquidity Audit**: Evaluate their debt levels, interest coverage, current ratio, and overall liquidity posture.
3. **Operational & Regulatory Red Flags**: Call out supply chain disruptions, litigation issues, government probes, or management churn from recent news.
4. **Macroeconomic Sensitivity**: How sensitive is the business to interest rate changes, inflation, currency swings, and general economic slowdowns (Beta/Volatility analysis).
5. **Warning Score & Mitigation Verdict**: Assign a Risk Warning level (High, Medium, Low) with actionable hedging or mitigation advice for investors. Contextualize the threat severity in terms of the 'Current Price' level.

Make it analytical, caution-oriented, objective, and written in clean Korean markdown.
"""

def build_portfolio_manager_prompt(symbol: str, profile: dict, sub_reports: dict) -> str:
    """
    Portfolio Manager Agent 스타일의 종합 분석 보고서 프롬프트 조립
    """
    profile_data = profile.get("assetProfile", {})
    fin_data = profile.get("financialData", {})
    detail = profile.get("summaryDetail", {})
    
    today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")
    
    # 5대 서브 리포트 취합
    market_rep = sub_reports.get("market", "N/A")
    earnings_rep = sub_reports.get("earnings", "N/A")
    valuation_rep = sub_reports.get("valuation", "N/A")
    moat_rep = sub_reports.get("moat", "N/A")
    risk_rep = sub_reports.get("risk", "N/A")
    
    return f"""You are a Chief Investment Officer (CIO) and Senior Portfolio Manager.
Your job is to synthesize five specialized analysis reports for {symbol} and write a final, comprehensive Investment Memorandum (종합 투자 분석 보고서) in Korean.

[ANALYSIS DATE / REPORT DATE]
{today_str}

[COMPANY INFO]
- Ticker: {symbol}
- Current Price: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')}
- Market Cap: {detail.get('marketCap', {}).get('fmt', 'N/A')}

==================================================
[1. MARKET RESEARCH REPORT]
{market_rep}

[2. EARNINGS REVIEW REPORT]
{earnings_rep}

[3. VALUATION REPORT]
{valuation_rep}

[4. ECONOMIC MOAT & AI REPORT]
{moat_rep}

[5. RISK & WARNING REPORT]
{risk_rep}
==================================================

Based on the five specialized reports above, write a high-conviction, professional Investment Memorandum in Korean. 
Your report must be structured as follows:

*CRITICAL REQUIREMENT*: You must write "{today_str}" as the report date (보고서 작성일) at the beginning of the report.

1. **종합 평점 및 요약 (Executive Summary & Final Rating)**
   - 최종 투자 투자의견 (강력 매수 / 매수 / 보유 / 매도)을 제시하십시오.
   - 1~100점 사이의 종합 투자 매력도 점수(종합 평점)를 부여하고, 그 이유를 3줄 요약으로 제공하십시오.
   - 현재가 대비 최종 판단을 내린 요약을 작성하십시오.
2. **에이전트별 분석 요약 및 조율 (Synthesis of Domain Analyses)**
   - 5개 에이전트의 핵심 발견 사항을 각각 요약하십시오. (시장 기회, 실적 성과, 가치 평가 수준, 해자 및 AI 경쟁력, 주요 리스크 신호)
   - 의견이 상충하는 부분(예: 훌륭한 해자 vs 비싼 밸류에이션, 혹은 좋은 실적 vs 높은 리스크)이 있다면 포트폴리오 매니저의 관점에서 어떻게 조율하여 최종 의견을 정했는지 명확히 설명하십시오.
3. **핵심 투자 촉매제 (Key Catalysts)**
   - 향후 6~12개월 내 주가 상승을 이끌 수 있는 핵심 이벤트 2~3가지를 서술하십시오.
4. **핵심 리스크 및 대응 요령 (Risks & Mitigation)**
   - 투자 판단을 뒤흔들 수 있는 가장 치명적인 위험 요소 1~2가지와 투자자 입장에서의 리스크 헤지/대응 전략을 제시하십시오.
5. **최종 가치 평가 및 목표 주가 (Final Valuation & Verdict)**
   - 가치평가 및 리스크 리포트를 기반으로 산출한 합리적인 적정 주가 범위(Target Price Range)를 명시하고, 현재가와의 괴리율(상승/하락 여력, Upside/Downside %)을 수학적으로 명확하게 계산하여 적으십시오.
   - 최종 권고안(예: 비중 확대, 분할 매수, 관망 등)과 함께 투자 전략적 조언을 덧붙이십시오.

Make it clean, authoritative, data-driven, and written in highly professional Korean investment terminology. Use clean markdown formatting.
"""

async def call_gemini(prompt: str, api_key: str) -> str:
    """
    Gemini API를 호출하는 비동기 헬퍼 함수 (429 Rate Limit 시 기하급수적 백오프 재시도 적용)
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }
    url = GEMINI_API_URL.format(api_key=api_key)
    
    max_retries = 5
    base_delay = 2.0
    
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=payload)
                
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            elif resp.status_code == 429:
                retry_after = base_delay * (2 ** attempt)
                logger.warning(f"[Gemini] 429 Rate Limit 감지. {retry_after}초 후 재시도합니다 (시도 {attempt+1}/{max_retries}).")
                await asyncio.sleep(retry_after)
                continue
            else:
                raise Exception(f"Gemini API 에러: {resp.status_code} - {resp.text}")
        except httpx.HTTPError as he:
            if attempt == max_retries - 1:
                raise he
            retry_after = base_delay * (2 ** attempt)
            logger.warning(f"[Gemini] HTTP 통신 오류 발생. {retry_after}초 후 재시도합니다: {he}")
            await asyncio.sleep(retry_after)
            
    raise Exception("Gemini API 호출 재시도 횟수를 초과했습니다 (429 Rate Limit).")

async def run_company_analysis(symbol: str, analysis_type: str = "market") -> dict:
    """
    주어진 티커와 분석 타입(market: 기업분석, earnings: 실적리뷰, valuation: 가치평가, preview: 실적프리뷰, moat: 해자분석, risk: 리스크감지, comprehensive: 종합멀티에이전트분석)에 따라 분석을 수행합니다.
    """
    symbol = symbol.upper().strip()
    logger.info(f"[CompanyAnalysis] Starting {analysis_type} analysis for {symbol}")
    
    # 1. 데이터 수집 (국내 주식 티커의 경우 .KS, .KQ 순차적 자동 변환 탐색 적용)
    target_symbols = [symbol]
    if symbol.isdigit() and len(symbol) == 6:
        target_symbols = [f"{symbol}.KS", f"{symbol}.KQ"]
        
    profile = {}
    resolved_symbol = symbol
    for ts in target_symbols:
        logger.info(f"[CompanyAnalysis] Fetching profile for {ts}...")
        profile = await fetch_company_profile_and_financials(ts)
        if profile:
            resolved_symbol = ts
            break
            
    if not profile:
        return {"status": "error", "message": f"기업 기본 정보를 수집할 수 없습니다: {symbol}"}
        
    news = await fetch_company_news(resolved_symbol)
    
    # 2. API 키 획득
    try:
        key_manager = get_key_manager()
        api_key = key_manager.next_key()
    except Exception as e:
        logger.error(f"[CompanyAnalysis] API 키 매니저 에러: {e}")
        return {"status": "error", "message": "Gemini API 키가 설정되지 않았습니다."}

    # 3. 종합 분석(comprehensive)과 단일 분석 분기 처리
    if analysis_type == "comprehensive":
        # 5대 에이전트 프롬프트 빌드
        prompts = {
            "market": build_market_research_prompt(resolved_symbol, profile, news),
            "earnings": build_earnings_review_prompt(resolved_symbol, profile, news),
            "valuation": build_valuation_prompt(resolved_symbol, profile, news),
            "moat": build_moat_prompt(resolved_symbol, profile, news),
            "risk": build_risk_prompt(resolved_symbol, profile, news),
        }
        
        # 5대 에이전트 비동기 호출 태스크 생성 (429 완화를 위해 1초씩 미세한 시차 주입)
        tasks = []
        keys = list(prompts.keys())
        
        async def delayed_call(name: str, pr: str, key: str, delay: float):
            if delay > 0:
                await asyncio.sleep(delay)
            logger.info(f"[CompanyAnalysis] Starting sub-agent: {name} after {delay}s delay")
            return await call_gemini(pr, key)
            
        for i, k in enumerate(keys):
            tasks.append(delayed_call(k, prompts[k], api_key, i * 1.0))
            
        try:
            results = await asyncio.gather(*tasks)
            sub_reports = dict(zip(keys, results))
        except Exception as e:
            logger.exception(f"[CompanyAnalysis] 서브 에이전트 병렬 호출 중 오류 발생: {e}")
            return {"status": "error", "message": f"서브 에이전트 분석 실패: {str(e)}"}
            
        # 포트폴리오 매니저 프롬프트 빌드 및 최종 호출
        pm_prompt = build_portfolio_manager_prompt(resolved_symbol, profile, sub_reports)
        try:
            final_report = await call_gemini(pm_prompt, api_key)
        except Exception as e:
            logger.exception(f"[CompanyAnalysis] 포트폴리오 매니저 취합 중 오류 발생: {e}")
            return {"status": "error", "message": f"포트폴리오 매니저 취합 실패: {str(e)}"}
            
        analysis_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return {
            "status": "ok",
            "ticker": symbol,
            "analysis_type": analysis_type,
            "analysis_date": analysis_date,
            "report": final_report
        }

    else:
        # 단일 분석 프롬프트 작성
        if analysis_type == "earnings":
            prompt = build_earnings_review_prompt(resolved_symbol, profile, news)
        elif analysis_type == "valuation":
            prompt = build_valuation_prompt(resolved_symbol, profile, news)
        elif analysis_type == "preview":
            prompt = build_preview_prompt(resolved_symbol, profile, news)
        elif analysis_type == "moat":
            prompt = build_moat_prompt(resolved_symbol, profile, news)
        elif analysis_type == "risk":
            prompt = build_risk_prompt(resolved_symbol, profile, news)
        else:
            prompt = build_market_research_prompt(resolved_symbol, profile, news)
            
        try:
            result_text = await call_gemini(prompt, api_key)
            analysis_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return {
                "status": "ok",
                "ticker": symbol,
                "analysis_type": analysis_type,
                "analysis_date": analysis_date,
                "report": result_text
            }
        except Exception as e:
            logger.exception(f"[CompanyAnalysis] Gemini 호출 중 오류 발생: {e}")
            return {"status": "error", "message": f"Gemini 호출 오류: {str(e)}"}
