/**
 * Step 1: Whale Tracking API - 고래/수급 분석 요청 + Long Polling
 * POST /v1/whale
 */
import { v4 as uuidv4 } from 'uuid';

export const config = {
    name: "whale-api",
    type: "api",
    path: "/v1/whale",
    method: "POST",
    emits: ['fetch-whale-data'],
    flows: ['whale-tracking-flow']
};

// 폴링 설정
const POLL_INTERVAL_MS = 500;
const MAX_WAIT_MS = 60000;

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const handler = async (req: any, { emit, state, logger }: any) => {
    try {
        const body = req.body || {};
        const symbol = body.symbol || "BTC-USD";
        const interval = body.interval || "day"; // 수급 분석은 일봉 권장
        const jobId = uuidv4();

        logger.info(`[Step1:WhaleAPI] Starting whale analysis ${jobId} for ${symbol} (${interval})`);

        // State 초기화
        await state.set('whale_jobs', jobId, {
            jobId,
            symbol,
            interval,
            status: 'pending',
            createdAt: new Date().toISOString(),
            result: null
        });

        // Step 2로 이벤트 발행
        await emit({
            topic: 'fetch-whale-data',
            data: { jobId, symbol, interval }
        });

        logger.info(`[Step1:WhaleAPI] Flow started, polling for completion...`);

        // Long Polling
        const startTime = Date.now();

        while (Date.now() - startTime < MAX_WAIT_MS) {
            await sleep(POLL_INTERVAL_MS);

            const job = await state.get('whale_jobs', jobId);

            if (!job) {
                logger.error(`[Step1:WhaleAPI] Job ${jobId} not found`);
                break;
            }

            if (job.status === 'completed') {
                logger.info(`[Step1:WhaleAPI] Job ${jobId} completed!`);
                await state.delete('whale_jobs', jobId); // 조회 후 삭제
                return {
                    status: 200,
                    body: job.result
                };
            }

            if (job.status === 'error') {
                logger.error(`[Step1:WhaleAPI] Job ${jobId} failed: ${job.error}`);
                await state.delete('whale_jobs', jobId);
                return {
                    status: 500,
                    body: { success: false, error: job.error, jobId }
                };
            }
        }

        // Timeout
        return {
            status: 202,
            body: {
                jobId,
                status: 'processing',
                message: '분석이 진행 중입니다. 잠시 후 다시 시도해주세요.',
                resultUrl: `/v1/whale/result/${jobId}` // (별도 구현 필요 시)
            }
        };

    } catch (error: any) {
        logger.error(`[Step1:WhaleAPI] Error: ${error.message}`);
        return {
            status: 500,
            body: { success: false, error: error.message }
        };
    }
};
