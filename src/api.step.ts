/**
 * Bitcoin AI Backend API Step
 * React 앱과의 통신을 담당합니다.
 */
export const config = {
  name: "bitcoin-api",
  type: "api",
  path: "/v1/forecast",
  method: "POST",
  emits: ['fetch-stock-data'],
  flows: ['bitcoin-forecast-flow']
};

export const handler = async (req: any, { emit, logger }: any) => {
  try {
    const body = req.body || {};
    const symbol = body.symbol || "BTC-USD";

    logger.info(`[API] Starting forecast workflow for ${symbol}...`);

    // call 대신 await emit을 사용합니다.
    // fetch-stock-data 부터 시작되는 flow의 최종 결과값이 result에 담깁니다.
    const result = await emit({
      topic: "fetch-stock-data",
      data: { symbol }
    });

    logger.info("[API] Workflow completed successfully.");

    return {
      status: 200,
      body: result
    };
  } catch (error: any) {
    logger.error(`[API] Workflow Error: ${error.message}`);
    return {
      status: 500,
      body: { success: false, error: error.message }
    };
  }
};



