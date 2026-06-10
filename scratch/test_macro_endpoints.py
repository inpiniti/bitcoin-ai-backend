import sys
import os
import asyncio
from unittest.mock import patch, AsyncMock

# Mock Gemini API Key 환경변수 설정
os.environ["GEMINI_API_KEY"] = "mock_key_for_testing"

# 모듈 탐색 경로 설정
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

@patch("services.company_analysis_service.call_gemini", new_callable=AsyncMock)
def test_endpoints(mock_call_gemini):
    # Mock Gemini 응답 정의
    mock_call_gemini.return_value = (
        "# [투자 분석 보고서] Mock Stock\n"
        "추천 자산 배분 비중: 주식 60% vs 현금 40%\n"
        "이것은 테스트용 모의 분석 보고서 내용입니다."
    )

    print("=== [Test 1] GET /api/analysis/macro-data 테스트 시작 ===")
    resp = client.get("/api/analysis/macro-data")
    print(f"Status Code: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print("Response keys:", data.keys())
        print("Macro indicators count:", len(data.get("macro_data", {})))
        for k, v in list(data.get("macro_data", {}).items())[:3]:
            print(f"  - {k}: {v}")
    else:
        print("Error details:", resp.text)
        
    print("\n=== [Test 2] POST /api/analysis/company (comprehensive) 테스트 시작 (macro_data 누락 방지 검증) ===")
    resp = client.post("/api/analysis/company", json={
        "ticker": "AAPL",
        "analysis_type": "comprehensive"
    })
    print(f"Status Code: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print("Response keys:", data.keys())
        print("Contains 'macro_data'?", "macro_data" in data)
        if "macro_data" in data and data["macro_data"]:
            print("Successfully verified: 'macro_data' exists and is not stripped!")
        else:
            print("Warning: 'macro_data' is null or missing")
    else:
        print("Error details:", resp.text)

    print("\n=== [Test 3] POST /api/analysis/macro 테스트 시작 ===")
    resp = client.post("/api/analysis/macro")
    print(f"Status Code: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        print("Response keys:", data.keys())
        print("Contains report?", "report" in data)
        print("Report preview (150 chars):")
        print(data.get("report", "")[:150], "...")
        print("Macro data indicators count in response:", len(data.get("macro_data", {})))
    else:
        print("Error details:", resp.text)

if __name__ == "__main__":
    test_endpoints()
