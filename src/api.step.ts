/**
 * Step 1: API Endpoint - 예측 작업 시작
 * POST /v1/forecast
 * 
 * 작업을 시작하고 jobId를 반환합니다.
 * 클라이언트는 GET /v1/result/:jobId로 결과를 조회합니다.
 */
import { v4 as uuidv4 } from 'uuid';

export const config = {
  name: "forecast-api",
  type: "api",
  path: "/v1/forecast",
  method: "POST",
  emits: ['fetch-stock'],
  flows: ['bitcoin-forecast-flow']
};

export const handler = async (req: any, { emit, state, logger }: any) => {
  try {
    const body = req.body || {};
    const symbol = body.symbol || "BTC-USD";
    const jobId = uuidv4();

    logger.info(`[Step1:API] Starting forecast job ${jobId} for ${symbol}`);

    // State에 작업 시작 기록
    await state.set('forecasts', jobId, {
      jobId,
      symbol,
      status: 'pending',
      createdAt: new Date().toISOString(),
      result: null
    });

    // Step 2로 이벤트 발행
    await emit({
      topic: 'fetch-stock',
      data: { jobId, symbol }
    });

    logger.info(`[Step1:API] Job ${jobId} queued for processing`);

    return {
      status: 202, // Accepted
      body: {
        jobId,
        symbol,
        status: 'pending',
        message: '예측 작업이 시작되었습니다. GET /v1/result/:jobId로 결과를 조회하세요.',
        resultUrl: `/v1/result/${jobId}`
      }
    };

  } catch (error: any) {
    logger.error(`[Step1:API] Error: ${error.message}`);
    return {
      status: 500,
      body: { success: false, error: error.message }
    };
  }
};
