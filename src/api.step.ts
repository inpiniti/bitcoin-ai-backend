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

export const handler = async (req: any, { emit }: any) => {
  try {
    const body = req.body || {};
    const symbol = body.symbol || "BTC-USD";

    // 1. 야후 파이낸스 데이터 수집 (Event Emission)
    console.log("Step 1: Emitting 'fetch-stock-data'...");

    // emit은 비동기 이벤트이므로 결과를 직접 반환받지 못할 수 있습니다.
    // 현재 구조에서는 Event-driven으로 동작하므로, 
    // 결과를 받아오려면 RPC 패턴이나 별도 구현이 필요합니다.
    // 우선 flow가 동작하는지 확인하기 위해 emit을 사용합니다.
    const result = await emit({
      topic: "fetch-stock-data",
      data: { symbol }
    });

    // 만약 emit이 결과를 반환한다면 result를 사용할 수 있음.
    // 그렇지 않다면, 이 구조는 비동기 처리(202 Accepted)로 변경되어야 함.

    return {
      success: true,
      message: "Forecast requested. Check logs for progress.",
      // debug: result 
    };
  } catch (error: any) {
    console.error("Workflow Error:", error);
    // 프레임워크가 에러를 처리하도록 throw
    throw error;
  }
};
