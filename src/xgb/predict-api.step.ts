/**
 * Step 3: XGBoost 예측 요청 API
 * POST /v1/xgb/predict
 */
import { v4 as uuidv4 } from 'uuid';

export const config = {
    name: "xgb-predict-api",
    type: "api",
    path: "/v1/xgb/predict",
    method: "POST",
    emits: ['xgb-predict'],
    flows: ['xgb-flow']
};

const POLL_INTERVAL_MS = 200;
const MAX_WAIT_MS = 10000;

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const handler = async (req: any, { emit, state, logger }: any) => {
    try {
        const body = req.body || {};
        const { modelJson, features } = body;
        const jobId = uuidv4();

        if (!modelJson || !features) {
            return { status: 400, body: { error: 'modelJson and features are required' } };
        }

        logger.info(`[XGB:Predict] Job ${jobId} started`);

        await state.set('xgb-jobs', jobId, {
            jobId,
            status: 'pending',
            modelJson,
            features,
            createdAt: new Date().toISOString()
        });

        await emit({
            topic: 'xgb-predict',
            data: { jobId } // E2BIG 방지
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
        logger.error(`[XGB:Predict] Error: ${error.message}`);
        return { status: 500, body: { error: error.message } };
    }
};
