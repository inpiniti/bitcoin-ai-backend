/**
 * Bitcoin AI Backend API Step
 * React 앱과의 통신을 담당합니다.
 */
export const config = {
  name: "bitcoin-api",
  type: "api",
  path: "/v1/forecast",
  method: "POST",
  emits: []
};

export const handler = async (req: any, { step }: any) => {
  try {
    // API Step의 경우 첫 번째 인자로 req 객체가 직접 넘어옵니다.
    const body = req.body || {};
    const symbol = body.symbol || "BTC-USD";

    // 1. 야후 파이낸스 데이터 수집
    console.log("Step 1: Fetching data...");
    const stockData = await step.call("fetch-stock-data", { symbol });

    // 2. Python AI 모델 예측
    console.log("Step 2: Forecasting...");
    // Python Step은 'data' 키로 가격 배열을 받음
    const forecastResult = await step.call("bitcoin-forecast", {
      data: stockData.prices,
    });

    // Python 결과에서 예측 리스트 추출
    const predictions = forecastResult.forecast;

    // 3. 결과 포맷팅
    console.log("Step 3: Formatting...");
    const finalResult = await step.call("format-forecast-result", {
      forecast: predictions,
      symbol: stockData.symbol
    });

    return {
      success: true,
      data: finalResult,
    };
  } catch (error: any) {
    console.error("Workflow Error:", error);
    // 프레임워크가 에러를 처리하도록 throw
    throw error;
  }
};
