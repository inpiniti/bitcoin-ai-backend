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

export const handler = async (req: any, { call, logger }: any) => {
  try {
    const body = req.body || {};
    const symbol = body.symbol || "BTC-USD";

    logger.info(`[API] Starting synchronous forecast for ${symbol}...`);

    // emit 대신 call을 사용하여 체인의 최종 결과(formatted report)를 직접 받습니다.
    const result = await call("fetch-stock-data", { symbol });

    logger.info("[API] Workflow completed. Returning report.");

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


