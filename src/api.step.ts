/**
 * Bitcoin AI Backend - Main API Step
 * 
 * 이 Step은 외부 클라이언트의 예측 요청을 처리하는 유일한 진입점입니다.
 * - POST /v1/forecast
 * - Body: { symbol?: string } (기본값: "BTC-USD")
 * 
 * 내부적으로 모듈화된 함수들을 호출하여 처리합니다:
 * 1. fetchStockData - Yahoo Finance 데이터 수집
 * 2. Python AI API - TimesFM 예측
 * 3. formatForecastReport - 결과 포맷팅
 */
import { fetchStockData } from './lib/fetch-stock';
import { formatForecastReport } from './lib/format-result';

export const config = {
  name: "bitcoin-api",
  type: "api",
  path: "/v1/forecast",
  method: "POST",
  emits: [],
  flows: ['bitcoin-forecast-flow']
};

export const handler = async (req: any, { logger }: any) => {
  try {
    const body = req.body || {};
    const symbol = body.symbol || "BTC-USD";

    logger.info(`[API] Starting forecast for ${symbol}...`);

    // ========== 1. Yahoo Finance 데이터 수집 ==========
    const stockData = await fetchStockData(symbol, logger);

    // ========== 2. Python AI API 호출 ==========
    logger.info(`[API] Calling Python AI Step...`);

    const forecastResponse = await fetch('http://localhost:7860/internal/forecast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: stockData.symbol,
        data: stockData.prices,
        lastDate: stockData.lastDate.toISOString()
      })
    });

    if (!forecastResponse.ok) {
      const errText = await forecastResponse.text();
      throw new Error(`Forecast API Error: ${forecastResponse.status} - ${errText}`);
    }

    const forecastData: any = await forecastResponse.json();
    logger.info(`[API] AI prediction completed: ${forecastData.predictionCount} predictions`);

    // ========== 3. 결과 포맷팅 ==========
    const report = formatForecastReport(
      stockData.symbol,
      forecastData.model,
      stockData.count,
      forecastData.forecast || []
    );

    logger.info(`[API] Report generated successfully for ${symbol}.`);

    return {
      status: 200,
      body: report
    };

  } catch (error: any) {
    logger.error(`[API] Error: ${error.message}`);
    return {
      status: 500,
      body: { success: false, error: error.message }
    };
  }
};
