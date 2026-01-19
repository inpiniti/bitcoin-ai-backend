/**
 * Step 1: Market Cap API Endpoint
 * POST /v1/market-cap
 * 
 * Input: { "ticker": "AAPL" }
 * Output: { "actual": ..., "inferred": ... }
 */
import { v4 as uuidv4 } from 'uuid';

export const config = {
    name: "market-cap-api",
    type: "api",
    path: "/v1/market-cap",
    method: "POST",
    emits: ['fetch-market-data'],
    flows: ['market-cap-inference-flow']
};

const POLL_INTERVAL_MS = 1000;
const MAX_WAIT_MS = 120000; // Allow 2 mins (Fetch: 5s, Train: 30s+)

const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

export const handler = async (req: any, { emit, state, logger }: any) => {
    try {
        const body = req.body || {};
        const ticker = (body.ticker || "").toUpperCase();

        if (!ticker) {
            return {
                status: 400,
                body: { error: 'Ticker is required' }
            };
        }

        const jobId = uuidv4();
        logger.info(`[API] Starting market cap inference for ${ticker} (Job: ${jobId})`);

        // Initialize State
        await state.set('market_cap_jobs', jobId, {
            jobId,
            ticker,
            status: 'pending',
            createdAt: new Date().toISOString(),
            result: null
        });

        // Start Flow
        await emit({
            topic: 'fetch-market-data',
            data: { jobId, ticker }
        });

        // Polling
        const startTime = Date.now();
        while (Date.now() - startTime < MAX_WAIT_MS) {
            await sleep(POLL_INTERVAL_MS);

            const job = await state.get('market_cap_jobs', jobId);

            if (!job) {
                logger.error(`[API] Job ${jobId} vanished.`);
                return { status: 500, body: { error: "Job lost" } };
            }

            if (job.status === 'completed') {
                // Clean up
                await state.delete('market_cap_jobs', jobId);

                return {
                    status: 200,
                    body: job.result
                };
            }

            if (job.status === 'error') {
                await state.delete('market_cap_jobs', jobId);
                return {
                    status: 500,
                    body: { error: job.error || "Unknown error" }
                };
            }
        }

        // Timeout
        return {
            status: 202,
            body: {
                jobId,
                status: 'processing',
                message: 'Analysis is taking longer than expected.'
            }
        };

    } catch (error: any) {
        logger.error(`[API] Error: ${error.message}`);
        return {
            status: 500,
            body: { error: error.message }
        };
    }
};
