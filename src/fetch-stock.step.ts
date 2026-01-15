/**
 * Step 2: Yahoo Finance 데이터 수집
 * Event Step - 'fetch-stock' 이벤트 구독
 */
export const config = {
    name: "fetch-stock",
    type: "event",
    subscribes: ['fetch-stock'],
    emits: ['run-forecast'],
    flows: ['bitcoin-forecast-flow']
};

export const handler = async (input: any, { emit, state, logger }: any) => {
    const { jobId, symbol, interval = "hour" } = input;

    try {
        logger.info(`[Step2:Fetch] Fetching ${interval} data for ${symbol} (Job: ${jobId})`);

        // interval에 따른 Yahoo Finance API 설정
        let yahooInterval = "1h";
        let yahooRange = "60d";
        let includePrePost = false;

        if (interval === "day") {
            yahooInterval = "1d";
            yahooRange = "2y";
            includePrePost = false;
        } else if (interval === "minute") {
            yahooInterval = "1m";
            yahooRange = "7d"; // 1분봉 최대 7일
            includePrePost = true;
        } else {
            // default to hour
            yahooInterval = "1h";
            yahooRange = "60d";
            includePrePost = true;
        }

        const yahooUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=${yahooInterval}&range=${yahooRange}&includePrePost=${includePrePost}`;

        const response = await fetch(yahooUrl, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        });

        if (!response.ok) {
            throw new Error(`Yahoo API Error: ${response.status}`);
        }

        const data: any = await response.json();
        if (!data.chart?.result?.[0]) {
            throw new Error(`No data found for ${symbol}`);
        }

        const chartResult = data.chart.result[0];
        const timestamps = chartResult.timestamp;
        const closes = chartResult.indicators.quote[0].close;

        // null 값 필터링
        const validPrices = closes.filter((p: any) => p !== null);
        const lastDate = new Date(timestamps[timestamps.length - 1] * 1000);

        logger.info(`[Step2:Fetch] Retrieved ${validPrices.length} ${interval} data points for ${symbol}`);

        // State 업데이트
        const job = await state.get('forecasts', jobId);
        if (job) {
            job.status = 'fetched';
            job.dataPoints = validPrices.length;
            await state.set('forecasts', jobId, job);
        }

        // Step 3으로 이벤트 발행
        await emit({
            topic: 'run-forecast',
            data: {
                jobId,
                symbol,
                interval,
                prices: validPrices,
                lastDate: lastDate.toISOString(),
                count: validPrices.length
            }
        });


        logger.info(`[Step2:Fetch] Data sent to forecast step for job ${jobId}`);

    } catch (error: any) {
        logger.error(`[Step2:Fetch] Error for job ${jobId}: ${error.message}`);

        // 에러 상태로 업데이트
        const job = await state.get('forecasts', jobId);
        if (job) {
            job.status = 'error';
            job.error = error.message;
            await state.set('forecasts', jobId, job);
        }
    }
};
