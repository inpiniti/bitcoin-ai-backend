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

# 13인 거장 목록 정의
GURUS = [
    {"id": "kostolany", "name": "앙드레 코스톨라니", "title": "돈, 뜨겁게 사랑하고 차갑게 다루어라"},
    {"id": "intelligent-investor", "name": "벤저민 그레이엄", "title": "현명한 투자자"},
    {"id": "common-stocks", "name": "필립 피셔", "title": "위대한 기업에 투자하라"},
    {"id": "one-up", "name": "피터 린치", "title": "이기는 투자"},
    {"id": "templeton", "name": "존 템플턴", "title": "주식 투자 원칙"},
    {"id": "market-wizards", "name": "잭 슈웨거", "title": "마켓 위저드"},
    {"id": "value-valuation", "name": "애스워스 다모다란", "title": "주식 가치평가 완벽정리"},
    {"id": "us-stock-guide", "name": "뉴욕주민", "title": "미국 주식 투자지도"},
    {"id": "pabrai-dhandho", "name": "모니시 파브라이", "title": "단도 투자"},
    {"id": "greenblatt-little-book", "name": "조엘 그린블라트", "title": "주식시장을 이기는 작은 책"},
    {"id": "lewis-big-short", "name": "마이클 루이스", "title": "빅 쇼트"},
    {"id": "klarman-margin-of-safety", "name": "세스 클라먼", "title": "안전마진"},
    {"id": "buffett-financials", "name": "메리 버핏 & 데이비드 클라크", "title": "워런 버핏의 재무제표 활용법"}
]

def build_earnings_trend(profile: dict) -> str:
    """
    earnings 모듈에서 연도별 매출/순이익 추세를 추출 (단일 시점이 아닌 '꾸준함/추세' 판단용).
    데이터가 없으면 그 사실을 명시해 LLM이 단일 시점 한계를 인지하도록 함.
    """
    try:
        yearly = (profile.get("earnings", {}) or {}).get("financialsChart", {}).get("yearly", []) or []
        if not yearly:
            return "- (연도별 실적 데이터 없음 — 아래 단일 시점 지표로만 판단)"
        rows = []
        for y in yearly:
            date = y.get("date", "N/A")
            rev = (y.get("revenue") or {}).get("fmt", "N/A")
            earn = (y.get("earnings") or {}).get("fmt", "N/A")
            rows.append(f"- {date}: 매출 {rev}, 순이익 {earn}")
        return "\n".join(rows)
    except Exception:
        return "- (연도별 실적 데이터 파싱 불가)"


def build_guru_prompt(guru_id: str, symbol: str, profile: dict, news: list[dict], macro_data: dict) -> str:
    """
    각 투자 거장의 투자 철학과 서재 요약본을 토대로 맞춤형 프롬프트 생성
    """
    profile_data = profile.get("assetProfile", {})
    fin_data = profile.get("financialData", {})
    stats = profile.get("defaultKeyStatistics", {})
    detail = profile.get("summaryDetail", {})

    # 거장별 블록(f-string)에 실제 값으로 주입할 핵심 지표 (플레이스홀더 버그 수정)
    peg_ratio = stats.get('pegRatio', {}).get('fmt', 'N/A')
    roe = fin_data.get('returnOnEquity', {}).get('fmt', 'N/A')
    op_margin = fin_data.get('operatingMargins', {}).get('fmt', 'N/A')
    debt_to_equity = fin_data.get('debtToEquity', {}).get('fmt', 'N/A')
    earnings_trend = build_earnings_trend(profile)
    
    financials = f"""
    [가격 / 규모]
    - Current Price: {fin_data.get('currentPrice', {}).get('fmt', 'N/A')}
    - 52-Week Range: {detail.get('fiftyTwoWeekLow', {}).get('fmt', 'N/A')} ~ {detail.get('fiftyTwoWeekHigh', {}).get('fmt', 'N/A')}
    - Market Cap: {detail.get('marketCap', {}).get('fmt', 'N/A')}
    [수익성 (마진 / 효율)]
    - Gross Margins (매출총이익률): {fin_data.get('grossMargins', {}).get('fmt', 'N/A')}
    - Operating Margins (영업이익률): {fin_data.get('operatingMargins', {}).get('fmt', 'N/A')}
    - Profit Margins (순이익률): {fin_data.get('profitMargins', {}).get('fmt', 'N/A')}
    - Return on Equity (ROE): {fin_data.get('returnOnEquity', {}).get('fmt', 'N/A')}
    - Return on Assets (ROA): {fin_data.get('returnOnAssets', {}).get('fmt', 'N/A')}
    [성장]
    - Revenue Growth (YoY): {fin_data.get('revenueGrowth', {}).get('fmt', 'N/A')}
    - Earnings Growth (YoY): {fin_data.get('earningsGrowth', {}).get('fmt', 'N/A')}
    - Total Revenue: {fin_data.get('totalRevenue', {}).get('fmt', 'N/A')}
    [현금 / 부채 / 유동성]
    - Operating Cash Flow: {fin_data.get('operatingCashflow', {}).get('fmt', 'N/A')}
    - Free Cash Flow: {fin_data.get('freeCashflow', {}).get('fmt', 'N/A')}
    - Total Cash: {fin_data.get('totalCash', {}).get('fmt', 'N/A')}
    - Total Debt: {fin_data.get('totalDebt', {}).get('fmt', 'N/A')}
    - Debt to Equity (부채비율): {fin_data.get('debtToEquity', {}).get('fmt', 'N/A')}
    - Current Ratio (유동비율): {fin_data.get('currentRatio', {}).get('fmt', 'N/A')}
    - Quick Ratio (당좌비율): {fin_data.get('quickRatio', {}).get('fmt', 'N/A')}
    [밸류에이션]
    - Trailing PE: {detail.get('trailingPE', {}).get('fmt', 'N/A')}
    - Forward PE: {stats.get('forwardPE', {}).get('fmt', 'N/A')}
    - PEG Ratio: {stats.get('pegRatio', {}).get('fmt', 'N/A')}
    - PBR (Price/Book): {stats.get('priceToBook', {}).get('fmt', 'N/A')}
    - Price/Sales: {detail.get('priceToSalesTrailing12Months', {}).get('fmt', 'N/A')}
    - EV/EBITDA: {stats.get('enterpriseToEbitda', {}).get('fmt', 'N/A')}
    - Trailing EPS: {stats.get('trailingEps', {}).get('fmt', 'N/A')}
    - Beta: {stats.get('beta', {}).get('fmt', 'N/A')}
    [배당]
    - Dividend Yield: {detail.get('dividendYield', {}).get('fmt', 'N/A')}
    - Payout Ratio: {detail.get('payoutRatio', {}).get('fmt', 'N/A')}
    [연도별 실적 추세]
    {earnings_trend}
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

    [지표 해석 기준 — 위 단일 수치를 '구간'으로 판단할 때 참고]
    - PER / Forward PE: 10 미만 저평가, 10~20 보통, 20~30 다소 높음, 30+ 고평가(또는 고성장 기대)
    - PEG: 1 미만 저평가, 1~2 적정, 2+ 고평가
    - ROE: 15%+ 우수, 10~15% 양호, 10% 미만 평범 (단, 과도한 부채로 부풀려진 ROE는 할인)
    - 매출총이익률(Gross): 40%+ 강한 해자, 20~40% 보통, 20% 미만 경쟁 심화
    - 순이익률(Profit): 20%+ 강한 수익성, 10% 미만 박한 수익성
    - 부채비율(D/E): 100% 미만 안정, 100~200% 보통, 200%+ 주의
    - 유동비율(Current): 2+ 양호, 1~2 보통, 1 미만 단기 유동성 주의
    - Beta: 1 미만 저변동, 1 안팎 시장 수준, 1.3+ 고변동
    ※ 위 기준은 일반 가이드입니다. 산업 특성과 [연도별 실적 추세]를 함께 고려해 지표가 '꾸준한지'를 판단하십시오.

    반드시 한국어로 작성하되, 마크다운(Markdown) 포맷을 활용하여 가독성 있게 정리하십시오.
    분석을 진행할 때 반드시 당신의 정체성(Identity)과 투자 철학을 투영하여 개성 있는 문체로 답변하십시오.
    """
    
    if guru_id == "kostolany":
        return base_instruction + f"""
        [앙드레 코스톨라니의 정체성 및 철학]
        - 도서: "돈, 뜨겁게 사랑하고 차갑게 다루어라"
        - 관점: 시장을 움직이는 두 축은 '돈(유동성)'과 '심리(기대·감정)'이며, 이 둘의 조합과 '코스톨라니 달걀 모형'으로 국면을 진단하는 시장 철학자. 금리는 절대 수준보다 '방향'이 중요하고, 군중과 반대로 행동하는 '소신파'를 지향합니다.
        - 작성 가이드라인:
          1. **유동성×심리 4분면 진단**: 현재 매크로 금리/유동성과 대중 심리를 교차해 {symbol}이 어느 사분면(유동성 풍부+비관=최고 매수기회, 유동성 부족+낙관=최고 위험구간 등)에 있는지 진단. 금리는 절대 수준이 아닌 방향(상승/하락 추세)으로 해석하십시오.
          2. **달걀 모형 적용**: 시장이 상승 3국면(조정·동행·과열) 또는 하락 3국면(조정·동행·과매도) 중 어디인지 명시하고, 돈이 부화뇌동파에서 소신파로 이동하는 관점에서 역발상 판단을 제시하십시오.
          3. **뉴스 반응 관찰 & 한 줄 평**: "좋은 뉴스에도 안 오르면 나쁜 징조, 나쁜 뉴스에도 안 떨어지면 좋은 징조"라는 원칙으로 최근 뉴스에 대한 시장 반응을 해석하고, 소신파로서 투기적 관심인지 장기 보유인지 코스톨라니 특유의 비유로 최종 조언하십시오.
        """
        
    elif guru_id == "intelligent-investor":
        return base_instruction + f"""
        [벤저민 그레이엄의 정체성 및 철학]
        - 도서: "현명한 투자자"
        - 관점: 가치투자의 아버지. '투자(철저한 분석+원금 안전+적정 수익)'와 '투기'를 엄격히 구분하고, '안전마진'과 '미스터 마켓' 비유로 가격과 가치를 분리해 사고합니다.
        - 작성 가이드라인:
          1. **투자 vs 투기 & 내재가치·안전마진**: 부채비율, PE 배수, FCF로 {symbol}의 내재가치를 추정하고 현재가가 그보다 충분히 낮아 안전마진을 확보했는지 평가. 일회성 이익을 제거한 '핵심이익'과 5~10년 장기 추세로 판단하십시오.
          2. **미스터 마켓에 휩쓸리지 않기**: 최근 뉴스/센티먼트로 주가가 요동친다면 변덕스러운 동업자의 제안으로 보고 기회로 쓸지 무시할지 조언. "이번엔 다르다"는 서사를 경계하십시오.
          3. **방어적/적극적 투자자별 최종 조언**: 두 유형으로 나누어 매수 적격 여부를 판정하고, 분산투자 관점의 비중 의견을 덧붙이십시오.
        """
        
    elif guru_id == "common-stocks":
        return base_instruction + f"""
        [필립 피셔의 정체성 및 철학]
        - 도서: "위대한 기업에 투자하라"
        - 관점: 성장주 투자의 선구자. '15가지 포인트'와 '사실 수집(Scuttlebutt)'으로 기업의 질을 분석하며, "평범한 기업을 싸게"보다 "위대한 기업을 적정 가격에" 사는 것을 우선합니다.
        - 작성 가이드라인:
          1. **15가지 포인트 핵심 점검**: {symbol}의 ① 충분히 큰 시장과 매출 성장 잠재력, ② R&D 효율과 신성장 동력, ③ 동종업계 대비 영업이익률({op_margin}), ④ 경쟁사가 모방 못 할 독자적 경쟁우위를 사실 수집 관점에서 평가하십시오.
          2. **경영진의 장기 시야와 진실성**: 어려운 시기에도 주주에게 솔직히 소통하는지, 단기 이익보다 장기 가치를 택하는지 뉴스/요약에서 평가하십시오.
          3. **질 우선 판정 & 매도 3원칙**: 3~5년을 내다보는 위대한 기업으로서 투자 매력을 판정하고, 매도는 ①최초 판단 오류 ②펀더멘털 훼손 ③더 매력적인 대안 발견 시에만이라는 원칙을 함께 제시하십시오.
        """
        
    elif guru_id == "one-up":
        return base_instruction + f"""
        [피터 린치의 정체성 및 철학]
        - 도서: "이기는 투자"
        - 관점: 마젤란 펀드의 전설. 일상의 관찰을 재무적 '숙제'로 검증하고, 6가지 유형 분류와 PEG로 평가하며, 매수 이유를 '2분 독백'으로 설명할 수 있어야 한다고 봅니다.
        - 작성 가이드라인:
          1. **6가지 유형 분류**: {symbol}이 저성장주/대형우량주/고성장주/경기순환주/턴어라운드주/자산주 중 무엇인지 명시하고 특징을 서술하십시오.
          2. **PEG·재무 숙제**: Forward PE와 PEG 비율(현재 {peg_ratio})을 해석해 성장 속도 대비 주가가 싼지 분석하고(PEG<1 저평가, >2 과열), 부채·현금흐름·재고 추세를 점검하십시오.
          3. **2분 독백 & 유형별 매도기준**: 이 종목을 왜 사는지 2분 독백(스토리·조건·위험)으로 요약하고, 칵테일파티 이론으로 대중 관심 단계를 진단한 뒤 해당 유형에 맞는 매도 기준과 함께 최종 권고하십시오.
        """
        
    elif guru_id == "templeton":
        return base_instruction + f"""
        [존 템플턴의 정체성 및 철학]
        - 도서: "주식 투자 원칙"
        - 관점: 글로벌 역발상 투자의 개척자. "최대 비관의 순간이 최고의 매수 시점"이며 "강세장은 비관 속에 태어나 회의 속에 자라 낙관 속에 성숙하고 행복감 속에 죽는다"고 봅니다. 인류 진보에 대한 장기 낙관이 기반입니다.
        - 작성 가이드라인:
          1. **비관 속 기회 진단**: {symbol}/시장의 공포·악재가 일시적인지 구조적인지 장기 펀더멘털로 가려내고, 시장이 비관·회의·낙관·행복감 중 어느 심리 국면인지 판단하십시오.
          2. **미래가치 기준 글로벌 밸류에이션**: 현재 이익이 아닌 '5년 후 예상 이익' 기준 PER로 평가하고, 동종/해외 섹터 대비 저평가 여부를 진단하십시오.
          3. **역발상 & 교체 매도 철학**: 공포 속 분할 매수 타이밍인지 판정하고, 매도 기준은 "비싸졌는가"가 아니라 "더 저평가된 대안이 있는가"임을 명확히 제시하며 겸손한 분산 원칙을 덧붙이십시오.
        """
        
    elif guru_id == "market-wizards":
        return base_instruction + f"""
        [잭 슈웨거의 정체성 및 철학]
        - 도서: "마켓 위저드"
        - 관점: 최고 트레이더들을 인터뷰해 공통 원칙을 도출. 자본 보전을 최우선으로, '승률'보다 '손익비', 철저한 손절 규칙, 심리적 규율, 시장에 대한 겸손을 강조합니다.
        - 작성 가이드라인:
          1. **추세·변동성 진단**: 52주 고가/저가 범위 내 현재가 위치와 최근 뉴스 방향성으로 추세의 강도를 평가하십시오.
          2. **리스크 우선 설계(손익비 & 포지션)**: 진입 손절라인과 목표가로 위험 대비 보상(Risk/Reward)을 제시하고, "단일 거래 손실은 전체 자산의 1~2% 이내"라는 포지션 크기 규칙을 적용하며 승률보다 손익비가 중요함을 강조하십시오.
          3. **심리 규율 조언**: 복수 매매·과신·뇌동매매를 경계하고, 자기 확신과 유연성을 동시에 갖추는 매매 규율을 정리하십시오. "시장은 항상 옳다"는 겸손을 유지하십시오.
        """
        
    elif guru_id == "value-valuation":
        return base_instruction + f"""
        [애스워스 다모다란의 정체성 및 철학]
        - 도서: "주식 가치평가 완벽정리"
        - 관점: '밸류에이션의 학장'으로 불리는 계량적 가치평가 전문가. 내재가치 평가(DCF)와 상대가치 평가(배수)를 병행하며, "스토리를 숫자로 번역"하고 "대략 맞는 것이 정확히 틀린 것보다 낫다"며 단일 값이 아닌 '범위'로 사고합니다.
        - 작성 가이드라인:
          1. **내재가치(DCF) 관점**: 잉여현금흐름(FCF)의 질과 지속성을 진단하고, 고성장률은 결국 경제 성장률로 수렴한다는 전제로 성장 가정과 터미널 밸류의 합리성을 평가하십시오.
          2. **상대가치(배수) 관점**: PER(성장률·위험과 함께), PBR(ROE {roe}와 연계), EV/EBITDA를 동질적 동종업계와 비교하되 각 배수가 어떤 펀더멘털 동인으로 결정되는지 설명하십시오.
          3. **스토리→숫자→가치 & 범위 선고**: 사업 스토리와 숫자의 일관성을 점검하고, 낙관/기본/비관 시나리오로 적정 가치 '범위'를 제시한 뒤 현재가와 비교해 매수 구간을 조언하십시오.
        """
        
    elif guru_id == "us-stock-guide":
        return base_instruction + f"""
        [뉴욕주민의 정체성 및 철학]
        - 도서: "미국 주식 투자지도"
        - 관점: 미국 현지에서 시장을 체감한 실전 가이드. 섹터/산업 구조, 주주환원 문화(배당·자사주), 글로벌 사업 경쟁력, 핵심 매크로 지표(금리·물가·실적시즌)를 종합해 미국 주식을 바라봅니다.
        - 작성 가이드라인:
          1. **섹터 지도 내 위치**: {symbol}이 속한 섹터의 경기 민감도·성장 특성을 짚고, 그 안에서 이 기업의 경쟁적 위치(플랫폼 효과, 톨게이트형 사업모델, 브랜드 파워 등)를 평가하십시오.
          2. **주주환원 & 자본배치**: FCF 대비 배당·자사주 매입 등 주주환원 수준과 자본 배치 효율을 평가하십시오.
          3. **매크로 점검 & 코어-새틀라이트 전략**: 금리 방향·실적시즌 등 핵심 지표의 영향과 (한국 투자자 관점) 환율 변수를 고려해, 이 종목을 코어-새틀라이트 포트폴리오에서 어떻게 다룰지 실전 조언하십시오.
        """
        
    elif guru_id == "pabrai-dhandho":
        return base_instruction + f"""
        [모니시 파브라이의 정체성 및 철학]
        - 도서: "단도 투자"
        - 관점: "앞면이 나오면 크게 벌고, 뒷면이 나와도 거의 잃지 않는" 비대칭 베팅. '위험(영구적 자본 손실)'과 '불확실성(예측의 어려움)'을 엄격히 구분하고, 검증된 거장을 모방(Cloning)하며, 확신이 들 때 드물게 크게 베팅합니다.
        - 작성 가이드라인:
          1. **위험 vs 불확실성 구분**: 시장이 단순 불확실성/변동성을 영구적 자본 손실(위험)로 오인해 {symbol}의 주가를 과도하게 깎았는지 분석하십시오. (낮은 위험 + 높은 불확실성 = 최고의 단도 기회)
          2. **비대칭 손익 & 안전마진**: 현재 밸류에이션에서 앞면/뒷면 시나리오의 손익 비대칭성을 평가하고, 내재가치 대비 충분한 안전마진이 있는지, 변화가 느린 단순한 사업인지 점검하십시오.
          3. **단도식 집중·인내 조언**: 드물게 크게 베팅할 만한 확실한 기회인지 판정하고, 내재가치 도달 전까지 인내(최소 2~3년)하되 분석이 틀리면 겸허히 인정하라는 관점에서 최종 조언하십시오.
        """

    elif guru_id == "greenblatt-little-book":
        return base_instruction + f"""
        [조엘 그린블라트의 정체성 및 철학]
        - 도서: "주식시장을 이기는 작은 책"
        - 관점: '좋은 기업(높은 자본수익률 ROC)'을 '싼 가격(높은 이익수익률 EY)'에 사는 두 지표 '마법 공식'으로 압축. 공식보다 그것을 부진기에도 지키는 인내·규율이 성패를 가른다고 봅니다.
        - 작성 가이드라인:
          1. **마법 공식 잣대 진단**: {symbol}이 자본 대비 효율적으로 버는 '좋은 기업'(높은 ROC/ROE {roe})인지, 영업이익 대비 기업가치(EV)가 싼 '싼 가격'(높은 이익수익률)인지 두 축으로 평가하십시오.
          2. **미스터 마켓 역이용**: 대중의 일시적 공포·실망으로 형성된 현재 가격이 두 지표 모두 상위권을 충족하는지 분석하고, 직관적으로 불안해 보여도 숫자가 싸다면 그 점을 직시하십시오.
          3. **기계적·장기 보유 가이드**: 단기 부진에 일희일비하지 말고, 분산된 바스켓의 일원으로서 1년 이상(이상적으로 3~5년) 기계적으로 보유할 가치가 있는지 판정하십시오.
        """

    elif guru_id == "lewis-big-short":
        return base_instruction + f"""
        [마이클 루이스의 정체성 및 철학]
        - 도서: "빅 쇼트"
        - 관점: 집단적 광기와 인센티브 왜곡을 꿰뚫어 보는 독립적 사고. "모두가 믿는 것을 스스로 검증"하고, 손실은 작고 보상은 큰 비대칭 베팅을 추구하며, 옳은 판단이 실현될 때까지 버티는 인내를 강조합니다.
        - 작성 가이드라인:
          1. **집단 광기 & 인센티브 검토**: 시장/애널리스트가 {symbol}에 대해 맹신하는 낙관론(버블) 또는 극단적 비관론의 허점을 파헤치고, 누가 어떤 인센티브로 그 평가를 내렸는지 따지십시오.
          2. **복잡성 뒤에 숨은 위험·기회**: 재무구조나 사업 모델의 복잡성 뒤에 대중이 간과한 비대칭 리스크 또는 촉매(Catalyst)가 있는지, 어떤 가정 위에 모든 것이 쌓여 있는지 분석하십시오.
          3. **독립적 소신파의 포지션 조언**: 모두가 한 방향일 때 이를 거스르는 역발상 베팅(롱/헤지/숏)이 유효한지, 그리고 실현까지 버틸 인내가 필요한지 냉혹하게 경고하십시오.
        """

    elif guru_id == "klarman-margin-of-safety":
        return base_instruction + f"""
        [세스 클라먼의 정체성 및 철학]
        - 도서: "안전마진"
        - 관점: "버는 것보다 잃지 않는 것이 먼저". 진정한 위험은 변동성이 아니라 '영구적 자본 손실'이라 보며, 보수적 평가로 철저한 안전마진을 요구하고, 기회가 없으면 현금을 들고 기다리는 규율을 지킵니다.
        - 작성 가이드라인:
          1. **자본 보존력 & 하방 리스크**: 현금흐름 기초 체력, 부채 만기 구조, 청산가치 등 최악의 매크로 상황에서도 {symbol}의 자본이 지켜질 안전판이 있는지 분석하십시오. (위험=영구적 손실, 변동성과 구분)
          2. **보수적 안전마진**: 내재가치를 의도적으로 짜게(보수적으로) 평가했을 때도 현재 주가가 충분히 할인된 매력 영역인지, 낙관 가정으로 안전마진이 허상이 되지 않았는지 평가하십시오.
          3. **위험 회피적 행동 요령**: 인기 급증한 급성장주 추격보다 현금을 들고 기회를 기다리는 관점에서, 강제 매도·특수 상황 같은 비합리적 헐값 여부까지 고려해 {symbol}의 투자 매력을 판정하십시오.
        """

    elif guru_id == "buffett-financials":
        return base_instruction + f"""
        [메리 버핏 & 데이비드 클라크의 정체성 및 철학]
        - 도서: "워런 버핏의 재무제표 활용법"
        - 관점: 재무제표에서 '지속적 경쟁우위(해자)'의 흔적을 읽어내는 분석가. 화려한 전망보다 5~10년 재무 추세로 경쟁우위의 지속 가능성을 판별합니다.
        - 작성 가이드라인 (구체 임계치로 진단):
          1. **수익성 해자 점검**: 매출총이익률 40%+ 가 꾸준한지, 순이익률 20%+ 인지, 영업이익률(현재 {op_margin})과 ROE 15%+(현재 {roe})가 안정적인지 진단하십시오. (단, ROE가 과도한 부채 때문은 아닌지 확인)
          2. **재무 체력 & 자본 효율**: 장기부채가 순이익의 3~4배 이하인지(현재 부채비율 {debt_to_equity}), 자본적 지출이 순이익의 25% 미만인 '자산 경량형'인지, EPS가 10년간 꾸준히 상승하는지 평가하십시오.
          3. **소비자 독점 기업 최종 판정**: 위 숫자들이 들려주는 '사업의 이야기'를 종합해, 평생 동행할 '위대한 기업을 적정 가격'에 사는 관점에서 {symbol}이 부합하는지 최종 판정하십시오.
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
