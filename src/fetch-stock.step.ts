
export const config = {
    name: "fetch-stock-data",
    type: "event",
    subscribes: ["fetch-stock-data"],
    flows: ['bitcoin-forecast-flow'],
    emits: ["bitcoin-forecast"],
};

export const handler = async (input: any, { emit, logger }: any) => {
    const { symbol } = input;
    const ticker = symbol || "BTC-USD"; // 기본값 비트코인

    logger.info(`Fetching data for ${ticker} via direct API...`);

    // Yahoo Finance API 직접 호출 (라이브러리 문제 회피)
    // 최근 60일, 1시간 간격 데이터
    const interval = '1h';
    const range = '60d';
    const targetUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${ticker}?interval=${interval}&range=${range}`;

    try {
        const response = await fetch(targetUrl, {
            headers: {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Yahoo API Error (${response.status}): ${errorText}`);
        }

        const data: any = await response.json();

        if (!data.chart || !data.chart.result || data.chart.result.length === 0) {
            throw new Error(`No data found for ${ticker}`);
        }

        const result = data.chart.result[0];
        const timestamps = result.timestamp;
        const indicators = result.indicators.quote[0];
        const prices = indicators.close;

        if (!prices || prices.length === 0) {
            throw new Error(`Price data is empty for ${ticker}`);
        }

        // null 값 필터링 및 데이터 정렬 (Yahoo API는 가끔 종가에 null을 반환함)
        const validData = prices.map((price: number, index: number) => ({
            date: new Date(timestamps[index] * 1000),
            close: price
        })).filter((item: any) => item.close !== null);

        if (validData.length === 0) {
            throw new Error(`No valid price data found for ${ticker}`);
        }

        const filteredPrices = validData.map((d: any) => d.close);
        const lastDate = validData[validData.length - 1].date;

        logger.info(`Fetched ${filteredPrices.length} data points.`);

        const output = {
            symbol: ticker,
            data: filteredPrices,   // forecast_step.py가 기대하는 필드명
            prices: filteredPrices, // 기존 호환성용
            lastDate: lastDate,
            count: filteredPrices.length
        };

        // 다음 단계(Python AI Step)로 이벤트 발행
        await emit({
            topic: "bitcoin-forecast",
            data: output
        });

        return output;
    } catch (error: any) {
        logger.error(`Yahoo Fetch Error: ${error.message}`);
        throw new Error(`Failed to fetch stock data: ${error.message}`);
    }
};


