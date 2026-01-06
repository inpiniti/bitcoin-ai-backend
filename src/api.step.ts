/**
 * Bitcoin AI Backend API Step
 * React 앱과의 통신을 담당합니다.
 */
export const config = {
  name: "bitcoin-api",
  type: "api",
  route: "POST /v1/forecast",
};

export const handler = async ({ req, step }: any) => {
  try {
    const body = await req.json();

    // Python Step('bitcoin-forecast') 직접 호출하여 결과 대기
    const result = await step.call("bitcoin-forecast", {
      data: body.historicalData,
      symbol: body.symbol || "BTC/KRW",
    });

    return Response.json({
      success: true,
      data: result,
    });
  } catch (error: any) {
    return Response.json({
      success: false,
      error: error.message,
    }, { status: 500 });
  }
};
