/**
 * Step 1: XGBoost 학습 요청 API
 * POST /v1/xgb/train
 */
import { v4 as uuidv4 } from 'uuid';

export const config = {
    name: "xgb-train-api",
    type: "api",
    path: "/v1/xgb/train",
    method: "POST",
    emits: ['xgb-train'],
    flows: ['xgb-flow']
};

const POLL_INTERVAL_MS = 500;
const MAX_WAIT_MS = 60000; // 최대 60초 (학습은 빠름)

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const handler = async (req: any, { emit, state, logger }: any) => {
    try {
        const body = req.body || {};
        const { features, labels } = body;
        const jobId = uuidv4();

        if (!features || !labels || features.length === 0) {
            return { status: 400, body: { error: 'features and labels are required' } };
        }

        logger.info(`[XGB:Train] Job ${jobId} started with ${features.length} samples`);

        await state.set('xgb-jobs', jobId, {
            jobId,
            status: 'pending',
            features, // 대용량 데이터는 state에 보관
            labels,
            createdAt: new Date().toISOString()
        });

        await emit({
            topic: 'xgb-train',
            data: { jobId } // 데이터 본문은 보내지 않음 (E2BIG 방지)
        });

        // Polling
        const startTime = Date.now();
        while (Date.now() - startTime < MAX_WAIT_MS) {
            await sleep(POLL_INTERVAL_MS);
            const job = await state.get('xgb-jobs', jobId);

            if (!job) break;

            if (job.status === 'completed') {
                const result = job.result;
                await state.delete('xgb-jobs', jobId);
                return { status: 200, body: result };
            }

            if (job.status === 'error') {
                await state.delete('xgb-jobs', jobId);
                return { status: 500, body: { error: job.error } };
            }
        }

        return { status: 202, body: { jobId, status: 'processing' } };

    } catch (error: any) {
        logger.error(`[XGB:Train] Error: ${error.message}`);
        return { status: 500, body: { error: error.message } };
    }
};
