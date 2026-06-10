import asyncio
import sys
import os

# 프로젝트 루트 경로를 Python PATH에 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.company_analysis_service import run_company_analysis

async def test_comprehensive_analysis(symbol: str):
    print(f"\n==================================================")
    print(f"Testing COMPREHENSIVE Analysis for: {symbol}")
    print(f"==================================================")
    
    start_time = asyncio.get_event_loop().time()
    
    result = await run_company_analysis(symbol, analysis_type="comprehensive")
    
    end_time = asyncio.get_event_loop().time()
    elapsed = end_time - start_time
    
    print(f"Status: {result.get('status')}")
    print(f"Elapsed Time: {elapsed:.2f} seconds")
    
    if result.get("status") == "ok":
        report = result.get("report", "")
        print(f"Report Length: {len(report)} characters")
        print("\n--- REPORT PREVIEW (First 1000 chars) ---")
        print(report[:1000])
        print("\n--- REPORT PREVIEW (Last 500 chars) ---")
        print(report[-500:] if len(report) > 1000 else "")
        print("-----------------------------------------")
    else:
        print(f"Error Message: {result.get('message')}")

async def main():
    # 미국 주식(AAPL) 및 한국 주식(삼성전자) 테스트
    await test_comprehensive_analysis("AAPL")
    await test_comprehensive_analysis("005930")

if __name__ == "__main__":
    # Windows 환경에서 asyncio.run 이벤트 루프 충돌 방지용 설정
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
