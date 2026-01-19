/**
 * Step 2: Yahoo Finance 데이터 수집 (수급 분석용)
 * Event Step - 'fetch-whale-data' 이벤트 구독
 */
export const config = {
    name: "fetch-whale-data",
    type: "event",
    subscribes: ['fetch-whale-data'],
    emits: ['analyze-whale'],
    flows: ['whale-tracking-flow']
};

export const handler = async (input: any, { emit, state, logger }: any) => {
    const { jobId, symbol, interval = "day" } = input;

    try {
        logger.info(`[Step2:FetchWhale] Fetching volume data for ${symbol} (Job: ${jobId})`);

        // 수급 분석은 중장기 추세가 중요하므로 기간을 넉넉하게 설정
        // 일봉(day): 1년치 데이터 (1y)
        // 시봉(hour): 60일치 데이터 (60d) - Yahoo API 최대치 제한 고려
        const yahooInterval = interval === "day" ? "1d" : "1h";
        const yahooRange = interval === "day" ? "1y" : "60d";

        const yahooUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?interval=${yahooInterval}&range=${yahooRange}`;

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
        const indicators = chartResult.indicators.quote[0];

        const closes = indicators.close;
        const volumes = indicators.volume;
        const highs = indicators.high;
        const lows = indicators.low;

        // 데이터 정제 (null 필터링 및 OHLCV 구조화)
        const marketData = [];
        for (let i = 0; i < timestamps.length; i++) {
            if (closes[i] !== null && volumes[i] !== null && highs[i] !== null && lows[i] !== null) {
                marketData.push({
                    timestamp: timestamps[i],
                    date: new Date(timestamps[i] * 1000).toISOString(),
                    close: closes[i],
                    high: highs[i],
                    low: lows[i],
                    volume: volumes[i]
                });
            }
        }

        logger.info(`[Step2:FetchWhale] Retrieved ${marketData.length} data points for ${symbol}`);

        // State 업데이트
        const job = await state.get('whale_jobs', jobId);
        if (job) {
            job.status = 'fetched';
            job.dataPoints = marketData.length;
            await state.set('whale_jobs', jobId, job);
        }

        // Step 3 (Python Analysis)로 데이터 전달
        await emit({
            topic: 'analyze-whale',
            data: {
                jobId,
                symbol,
                interval,
                marketData // OHLCV 전체 데이터 전달
            }
        });

    } catch (error: any) {
        logger.error(`[Step2:FetchWhale] Error for job ${jobId}: ${error.message}`);

        const job = await state.get('whale_jobs', jobId);
        if (job) {
            job.status = 'error';
            job.error = error.message;
            await state.set('whale_jobs', jobId, job);
        }
    }
};
