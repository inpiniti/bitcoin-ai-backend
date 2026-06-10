import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.company_analysis_data import fetch_macro_indicators

async def main():
    print("Fetching macro indicators...")
    macro_data = await fetch_macro_indicators()
    print(f"Fetch completed. Total items: {len(macro_data)}")
    
    for symbol, info in macro_data.items():
        print(f"[{symbol}] {info['name']}: Price={info['price']}, Change={info['change']}({info['changePercent']:.2f}%)")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
