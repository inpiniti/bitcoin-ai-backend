"""
안트롭틱(Anthropic) 스타일의 금융 에이전트를 모방한 Gemini 기반 기업 분석 서비스
"""
import logging
import json
import httpx
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
    
    return f"""You are a senior investment analyst and market researcher specializing in technology and growth stocks.
Analyze the following company data and news to provide a professional-grade market research report.

[COMPANY PROFILE: {symbol}]
{company_info}

[FINANCIAL METRICS]
{financials}

[RECENT NEWS]
{news_context}

Please generate an in-depth analysis report structured as follows (Write the report in Korean):
1. **Executive Summary**: Core thesis on why this stock is a buy, hold, or sell.
2. **Business Strategy & Market Positioning**: Critique their product strategy, industry moat, and competitors.
3. **Financial Health & Efficiency Analysis**: Evaluate revenue growth, margins, cash flow strength, and debt levels.
4. **Recent News Sentiment & Catalysts**: Discuss major news, public sentiment, and short-term catalysts.
5. **Investment Recommendation**: Clear rating (Buy / Hold / Sell) with specific target/range advice.

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

    return f"""You are an expert financial auditor and earnings analyst.
Review the following financial performance metrics and news regarding {symbol}'s recent earnings release.

[FINANCIAL PERFORMANCE: {symbol}]
{financials}

[RECENT EARNINGS NEWS & REACTIONS]
{news_context}

Please generate an Earnings Review Report in Korean. Structure the report as follows:
1. **Earnings Summary**: Highlight actual numbers vs. street consensus if available, plus revenue and EPS growth rates.
2. **Margin & Profitability Analysis**: Dive into gross margins, operating margin expansions/contractions, and cash generation.
3. **Management Guidance & Outlook**: What is the management outlook/future forecasts discussed in the news/filings?
4. **Key Risks & Red Flags**: List any negative factors, margin pressures, or macro risks.
5. **Earnings Score**: Assign a final rating (A+, A, B, C, D) with a 2-sentence rationale.

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

    return f"""You are a Valuation Expert and Equity Research Analyst.
Assess the intrinsic and relative value of {symbol} using the financial indicators and recent market news below.

[FINANCIAL METRICS: {symbol}]
{financials}

[RECENT NEWS CONTEXT]
{news_context}

Please generate a professional Valuation & Comps Analysis Report in Korean. Structure the report as follows:
1. **Executive Summary**: Overview of the company's valuation state (Undervalued, Fairly valued, Overvalued).
2. **Intrinsic Valuation (DCF-based Analysis)**: Critique their Free Cash Flow (FCF) generation strength, project growth rates, and estimate cash flow stability.
3. **Relative Valuation (Comps Multiples)**: Compare current P/E, EV/EBITDA, and PEG ratios with industry averages and key competitors (recommend peer multi-comparison).
4. **Valuation Sensitivity & Catalysts**: Detail key risks to the valuation (e.g., interest rates, growth slowdowns) and catalysts that could unlock value.
5. **Target Price Range & Investment Verdict**: Provide a realistic target price range (e.g., $X ~ $Y) and a clear Buy/Hold/Sell recommendation with mathematical justification.

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

    return f"""You are an Equity Research Analyst preparing an Earnings Preview.
Analyze upcoming expectations and sentiment for {symbol}'s near-term earnings release.

[CURRENT RECENT METRICS: {symbol}]
{financials}

[UPCOMING EXPECTATIONS & CATALYST NEWS]
{news_context}

Please generate an Earnings Preview Report in Korean. Structure the report as follows:
1. **Earnings Expectations**: Summarize the street consensus for revenue and EPS.
2. **Key Metrics to Watch**: Identify 2-3 segment revenues or operational metrics (e.g., Cloud growth, hardware delivery volume) that will decide the stock's post-earnings direction.
3. **Sentiment & Position Check**: Detail whether the market sentiment going into earnings is hot, cold, or neutral, and evaluate options/price movement implications.
4. **Bull vs. Bear Scenarios**: Draft a clear table/bullet comparison of the Bull case (what happens if they beat & guide up) vs. Bear case (what happens if they miss or guide down).
5. **Earnings Strategy & Rating**: Give an overall tactical recommendation (e.g., Neutral, Buy into Strength, Caution) for short-term traders.

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

    return f"""You are a Strategic Management Consultant and Private Equity Dilegence Analyst.
Evaluate {symbol}'s economic moat and AI technology readiness based on the business description, margins, and news below.

[BUSINESS PROFILE: {symbol}]
{company_info}

[PROFITABILITY METRICS]
{financials}

[TECHNOLOGY & COMPETITION NEWS]
{news_context}

Please generate an Economic Moat & AI Readiness Report in Korean. Structure the report as follows:
1. **Moat Assessment**: Define the type of moat they possess (Network Effects, Switching Costs, Cost Advantage, or Intangible Assets) and rate it (Wide, Narrow, None).
2. **Competitor & Market Position Analysis**: Assess their market share and threat from key rivals.
3. **AI Readiness & Tech Stack Integration**: Analyze their AI initiatives, software capabilities, and whether they are an AI leader, fast follower, or laggard.
4. **Margin & Capital Efficiency Audit**: Link their high/low margins and ROE back to their structural business advantages.
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

    return f"""You are a Financial Risk Manager and Auditor.
Analyze potential risk factors and red flags for {symbol} based on their leverage metrics and recent negative news flow.

[LEVERAGE & LIQUIDITY METRICS: {symbol}]
{financials}

[NEGATIVE & RISK NEWS CONTEXT]
{news_context}

Please generate a Risk & Warning Signals Report in Korean. Structure the report as follows:
1. **Key Risk Matrix**: Classify risks into Financial, Operational, Regulatory, and Macroeconomic categories.
2. **Financial Risk & Liquidity Audit**: Evaluate their debt levels, interest coverage, current ratio, and overall liquidity posture.
3. **Operational & Regulatory Red Flags**: Call out supply chain disruptions, litigation issues, government probes, or management churn from recent news.
4. **Macroeconomic Sensitivity**: How sensitive is the business to interest rate changes, inflation, currency swings, and general economic slowdowns (Beta/Volatility analysis).
5. **Warning Score & Mitigation Verdict**: Assign a Risk Warning level (High, Medium, Low) with actionable hedging or mitigation advice for investors.

Make it analytical, caution-oriented, objective, and written in clean Korean markdown.
"""

async def run_company_analysis(symbol: str, analysis_type: str = "market") -> dict:
    """
    주어진 티커와 분석 타입(market: 기업분석, earnings: 실적리뷰, valuation: 가치평가, preview: 실적프리뷰, moat: 해자분석, risk: 리스크감지)에 따라 분석을 수행합니다.
    """
    symbol = symbol.upper().strip()
    logger.info(f"[CompanyAnalysis] Starting {analysis_type} analysis for {symbol}")
    
    # 1. 데이터 수집
    profile = await fetch_company_profile_and_financials(symbol)
    if not profile:
        return {"status": "error", "message": f"기업 기본 정보를 수집할 수 없습니다: {symbol}"}
        
    news = await fetch_company_news(symbol)
    
    # 2. 프롬프트 작성
    if analysis_type == "earnings":
        prompt = build_earnings_review_prompt(symbol, profile, news)
    elif analysis_type == "valuation":
        prompt = build_valuation_prompt(symbol, profile, news)
    elif analysis_type == "preview":
        prompt = build_preview_prompt(symbol, profile, news)
    elif analysis_type == "moat":
        prompt = build_moat_prompt(symbol, profile, news)
    elif analysis_type == "risk":
        prompt = build_risk_prompt(symbol, profile, news)
    else:
        prompt = build_market_research_prompt(symbol, profile, news)
        
    # 3. Gemini API 호출
    try:
        key_manager = get_key_manager()
        api_key = key_manager.next_key()
    except Exception as e:
        logger.error(f"[CompanyAnalysis] API 키 매니저 에러: {e}")
        return {"status": "error", "message": "Gemini API 키가 설정되지 않았습니다."}
        
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }
    
    url = GEMINI_API_URL.format(api_key=api_key)
    
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)
            
        if resp.status_code != 200:
            logger.error(f"[Gemini] HTTP {resp.status_code}: {resp.text}")
            return {"status": "error", "message": f"Gemini API 에러: {resp.status_code}"}
            
        data = resp.json()
        result_text = data["candidates"][0]["content"]["parts"][0]["text"]
        
        # 임시 Supabase 저장용 혹은 캐싱 메타데이터 구성
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
