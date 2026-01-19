/**
 * 결과 조회 API
 * GET /v1/result/:jobId
 * 
 * 예측 결과를 조회하고, 완료된 결과는 조회 후 삭제합니다.
 * (10분 이상 된 데이터도 자동 정리)
 */
export const config = {
    name: "result-api",
    type: "api",
    path: "/v1/result/:jobId",
    method: "GET",
    emits: [],
    flows: ['bitcoin-forecast-flow']
};

const TTL_MINUTES = 10; // 10분 후 자동 만료

export const handler = async (req: any, { state, logger }: any) => {
    try {
        const jobId = req.params?.jobId || req.pathParams?.jobId;

        if (!jobId) {
            return {
                status: 400,
                body: { error: 'jobId is required' }
            };
        }

        logger.info(`[Result] Fetching result for job ${jobId}`);

        const job = await state.get('forecasts', jobId);

        if (!job) {
            return {
                status: 404,
                body: { error: 'Job not found or expired', jobId }
            };
        }

        // TTL 체크 (10분 이상 된 데이터 삭제)
        const createdAt = new Date(job.createdAt);
        const now = new Date();
        const ageMinutes = (now.getTime() - createdAt.getTime()) / (1000 * 60);

        if (ageMinutes > TTL_MINUTES) {
            await state.delete('forecasts', jobId);
            logger.info(`[Result] Job ${jobId} expired and deleted`);
            return {
                status: 410, // Gone
                body: { error: 'Job result expired', jobId }
            };
        }

        // 상태에 따른 응답
        if (job.status === 'completed') {
            // 완료된 결과는 조회 후 삭제
            await state.delete('forecasts', jobId);
            logger.info(`[Result] Job ${jobId} result delivered and deleted`);

            return {
                status: 200,
                body: job.result
            };
        }

        if (job.status === 'error') {
            // 에러 상태도 조회 후 삭제
            await state.delete('forecasts', jobId);

            return {
                status: 500,
                body: {
                    status: 'error',
                    error: job.error,
                    jobId
                }
            };
        }

        // 아직 처리 중
        return {
            status: 202, // Accepted (still processing)
            body: {
                jobId,
                status: job.status,
                symbol: job.symbol,
                message: '작업이 진행 중입니다. 잠시 후 다시 조회해 주세요.',
                progress: getProgressPercent(job.status)
            }
        };

    } catch (error: any) {
        logger.error(`[Result] Error: ${error.message}`);
        return {
            status: 500,
            body: { error: error.message }
        };
    }
};

function getProgressPercent(status: string): number {
    switch (status) {
        case 'pending': return 10;
        case 'fetched': return 40;
        case 'forecasted': return 80;
        case 'completed': return 100;
        default: return 0;
    }
}
