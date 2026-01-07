/**
 * Step 4: 결과 포맷팅 및 State 저장
 * Event Step - 'format-result' 이벤트 구독
 */
export const config = {
    name: "format-result",
    type: "event",
    subscribes: ['format-result'],
    emits: [],
    flows: ['bitcoin-forecast-flow']
};

interface ForecastItem {
    ds: string;
    y: number;
    timesfm?: number;
}

export const handler = async (input: any, { state, logger }: any) => {
    const { jobId, symbol, lastDate, forecast, model, dataPoints } = input;

    try {
        logger.info(`[Step4:Format] Formatting result for job ${jobId}`);

        // 결과 포맷팅
        const report = {
            title: `${symbol} 가격 예측 보고서`,
            symbol,
            generatedAt: new Date().toISOString(),
            model,
            dataPoints,
            predictionCount: forecast?.length || 0,
            predictions: (forecast || []).slice(0, 24).map((item: ForecastItem, index: number) => ({
                step: index + 1,
                date: item.ds,
                price: Math.round(item.timesfm || item.y || 0),
                priceFormatted: new Intl.NumberFormat('en-US', {
                    style: 'currency',
                    currency: 'USD'
                }).format(item.timesfm || item.y || 0)
            }))
        };

        // State에 완료된 결과 저장
        const job = await state.get('forecasts', jobId);
        if (job) {
            job.status = 'completed';
            job.completedAt = new Date().toISOString();
            job.result = report;
            await state.set('forecasts', jobId, job);
        }

        logger.info(`[Step4:Format] Job ${jobId} completed successfully`);

    } catch (error: any) {
        logger.error(`[Step4:Format] Error for job ${jobId}: ${error.message}`);

        // 에러 상태로 업데이트
        const job = await state.get('forecasts', jobId);
        if (job) {
            job.status = 'error';
            job.error = error.message;
            await state.set('forecasts', jobId, job);
        }
    }
};
