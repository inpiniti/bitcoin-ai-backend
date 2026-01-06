import yahooFinance from 'yahoo-finance2';

export const config = {
    name: "fetch-stock-data",
    type: "event",
    subscribes: ["fetch-stock-data"],
};

export const handler = async (input: any) => {
    const { symbol } = input;
    const ticker = symbol || "BTC-USD"; // 기본값 비트코인

    console.log(`Fetching data for ${ticker}...`);

    try {
        // 최근 60일 데이터 가져오기 (TimesFM 입력용)
        const queryOptions = { period1: '60d', interval: '1h' }; // 1시간 간격, 60일
        // @ts-ignore
        const result = await yahooFinance.historical(ticker, queryOptions);

        if (!result || result.length === 0) {
            throw new Error(`No data found for ${ticker}`);
        }

        // 데이터 가공 (close price만 추출)
        const prices = result.map((candle: any) => candle.close);
        const lastDate = result[result.length - 1].date;

        console.log(`Fetched ${prices.length} data points.`);

        return {
            symbol: ticker,
            prices: prices,
            lastDate: lastDate,
            count: prices.length
        };
    } catch (error: any) {
        console.error("Yahoo Finance Error:", error);
        throw new Error(`Failed to fetch stock data: ${error.message}`);
    }
};
