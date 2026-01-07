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

    logger.info(`Step 1: Starting forecast workflow for ${symbol}...`);

    // Motia에서 emit은 설정에 따라 결과를 반환할 수 있는 동기적(RPC-like) 호출로 동작 가능합니다.
    // 전체 flow(fetch -> forecast -> format)가 완료될 때까지 기다려 결과를 가져옵니다.
    const result = await emit({
      topic: "fetch-stock-data",
      data: { symbol }
    });

    logger.info("Workflow completed successfully.");

    // 최종적으로 format-result.step.ts에서 가공된 데이터가 result에 담겨 반환됩니다.
    return {
      status: 200,
      body: result
    };
  } catch (error: any) {
    console.error("Workflow Error:", error);
    return {
      status: 500,
      body: {
        success: false,
        error: error.message || "Internal Server Error"
      }
    };
  }
};

