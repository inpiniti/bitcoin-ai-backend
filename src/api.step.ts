/**
 * Step 1: API Endpoint - 예측 작업 시작 + Long Polling
 * POST /v1/forecast
 * 
 * Flow를 시작하고 State를 폴링하면서 완료를 대기합니다.
 * 클라이언트는 일반 API처럼 결과를 즉시 받을 수 있습니다.
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

// 폴링 설정
const POLL_INTERVAL_MS = 500;    // 0.5초마다 체크
const MAX_WAIT_MS = 60000;       // 최대 60초 대기

// sleep 함수
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const handler = async (req: any, { emit, state, logger }: any) => {
  try {
    const body = req.body || {};
    const symbol = body.symbol || "BTC-USD";
    const interval = body.interval || "hour"; // "day" 또는 "hour"
    const jobId = uuidv4();

    // interval 유효성 검사
    if (!["day", "hour"].includes(interval)) {
      return {
        status: 400,
        body: { error: 'interval must be "day" or "hour"' }
      };
    }

    logger.info(`[Step1:API] Starting forecast job ${jobId} for ${symbol} (${interval})`);

    // State에 작업 시작 기록
    await state.set('forecasts', jobId, {
      jobId,
      symbol,
      interval,
      status: 'pending',
      createdAt: new Date().toISOString(),
      result: null
    });

    // Step 2로 이벤트 발행 (비동기로 Flow 시작)
    await emit({
      topic: 'fetch-stock',
      data: { jobId, symbol, interval }
    });


    logger.info(`[Step1:API] Flow started, polling for completion...`);

    // Long Polling: State를 폴링하면서 완료 대기
    const startTime = Date.now();

    while (Date.now() - startTime < MAX_WAIT_MS) {
      await sleep(POLL_INTERVAL_MS);

      const job = await state.get('forecasts', jobId);

      if (!job) {
        logger.error(`[Step1:API] Job ${jobId} not found in state`);
        break;
      }

      // 완료 상태 체크
      if (job.status === 'completed') {
        logger.info(`[Step1:API] Job ${jobId} completed!`);

        // 결과 반환 후 State 삭제
        await state.delete('forecasts', jobId);

        return {
          status: 200,
          body: job.result
        };
      }

      // 에러 상태 체크
      if (job.status === 'error') {
        logger.error(`[Step1:API] Job ${jobId} failed: ${job.error}`);

        // 에러 상태도 삭제
        await state.delete('forecasts', jobId);

        return {
          status: 500,
          body: {
            success: false,
            error: job.error,
            jobId
          }
        };
      }

      // 진행 상황 로깅 (10초마다)
      const elapsed = Date.now() - startTime;
      if (elapsed % 10000 < POLL_INTERVAL_MS) {
        logger.info(`[Step1:API] Still waiting... status: ${job.status}, elapsed: ${elapsed}ms`);
      }
    }

    // 타임아웃
    logger.warn(`[Step1:API] Job ${jobId} timed out after ${MAX_WAIT_MS}ms`);

    // 타임아웃 시에도 결과 URL 제공
    return {
      status: 202,
      body: {
        jobId,
        symbol,
        status: 'processing',
        message: '작업이 아직 진행 중입니다. 잠시 후 결과 URL로 조회해 주세요.',
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
