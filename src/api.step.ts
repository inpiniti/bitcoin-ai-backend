/**
 * Bitcoin AI Backend API Step
 * 모든 로직을 동기적으로 직접 처리합니다.
 */
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
    const interval = '1h';
    const range = '60d';
    const yahooUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=${interval}&range=${range}`;

    logger.info(`[API] Fetching Yahoo Finance data...`);
    const yahooResponse = await fetch(yahooUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
      }
    });

    if (!yahooResponse.ok) {
      throw new Error(`Yahoo API Error: ${yahooResponse.status}`);
    }

    const yahooData: any = await yahooResponse.json();
    if (!yahooData.chart?.result?.[0]) {
      throw new Error(`No data found for ${symbol}`);
    }

    const chartResult = yahooData.chart.result[0];
    const timestamps = chartResult.timestamp;
    const closes = chartResult.indicators.quote[0].close;

    // null 값 필터링
    const validPrices = closes.filter((p: any) => p !== null);
    const lastDate = new Date(timestamps[timestamps.length - 1] * 1000);

    logger.info(`[API] Fetched ${validPrices.length} data points.`);

    // ========== 2. Python AI API 호출 ==========
    logger.info(`[API] Calling Python AI API...`);

    // 내부 API 호출 (같은 서버의 /internal/forecast)
    const forecastResponse = await fetch('http://localhost:7860/internal/forecast', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: symbol,
        data: validPrices,
        lastDate: lastDate.toISOString()
      })
    });

    if (!forecastResponse.ok) {
      const errText = await forecastResponse.text();
      throw new Error(`Forecast API Error: ${forecastResponse.status} - ${errText}`);
    }

    const forecastData: any = await forecastResponse.json();
    logger.info(`[API] AI prediction completed: ${forecastData.predictionCount} predictions`);

    // ========== 3. 결과 포맷팅 ==========
    const report = {
      title: `${symbol} 가격 예측 보고서`,
      generatedAt: new Date().toISOString(),
      model: forecastData.model,
      dataPoints: validPrices.length,
      predictionCount: forecastData.predictionCount,
      predictions: forecastData.forecast?.slice(0, 24).map((item: any, index: number) => ({
        step: index + 1,
        date: item.ds,
        price: Math.round(item.timesfm || item.y || 0),
        priceFormatted: new Intl.NumberFormat('en-US', {
          style: 'currency',
          currency: 'USD'
        }).format(item.timesfm || item.y || 0)
      })) || []
    };

    logger.info(`[API] Report generated successfully.`);

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
