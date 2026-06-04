import pytest
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.company_analysis import router

# Create a minimal FastAPI instance for isolated router testing
app = FastAPI()
app.include_router(router)
client = TestClient(app)

@patch("routers.company_analysis.run_company_analysis", new_callable=AsyncMock)
def test_company_analysis_endpoint(mock_run):
    # Mock return value from the service
    mock_run.return_value = {
        "status": "ok",
        "ticker": "AAPL",
        "analysis_type": "market",
        "analysis_date": "2026-06-04",
        "report": "Apple Inc. (AAPL) is a buy based on Gemini analysis."
    }

    response = client.post(
        "/api/analysis/company",
        json={"ticker": "AAPL", "analysis_type": "market"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["ticker"] == "AAPL"
    assert "Apple Inc." in data["report"]
    mock_run.assert_called_once_with("AAPL", "market")
