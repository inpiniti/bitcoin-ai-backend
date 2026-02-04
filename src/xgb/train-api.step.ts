/**
 * Step 1: XGBoost 학습 요청 API
 * POST /v1/xgb/train
 * 학습 데이터가 크므로 클라이언트가 Supabase에 업로드한 datasetId를 받음
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
const MAX_WAIT_MS = 120000; // 최대 2분 (데이터 로드 시간 고려)

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const handler = async (req: any, { emit, state, logger }: any) => {
    try {
        const body = req.body || {};
        const { datasetId, modelName } = body;
        const jobId = uuidv4();

        if (!datasetId) {
            return { status: 400, body: { error: 'datasetId is required' } };
        }

        const finalModelName = modelName || `XGB_Model_${jobId.slice(0, 8)}`;

        logger.info(`[XGB:Train] Job ${jobId} started. Model: ${finalModelName}, Dataset: ${datasetId}`);

        await state.set('xgb-jobs', jobId, {
            jobId,
            status: 'pending',
            createdAt: new Date().toISOString()
        });

        await emit({
            topic: 'xgb-train',
            data: { jobId, datasetId, modelName: finalModelName }
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
